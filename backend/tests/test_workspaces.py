from __future__ import annotations

import io
import shutil
import zipfile
import asyncio
from pathlib import Path

import pytest

from app.config import get_settings
from app.db import SessionLocal
from app.models import (
    WorkspaceEvent,
    WorkspaceSession,
    WorkspaceTurn,
    WorkspaceWorkflowProposal,
)
from app.services.workspaces import _replace_wiki_tree, update_workspace_runtime_state
from app.utils import dumps, now_utc
from tests.auth_helpers import create_regular_user


GRAPH = {
    "nodes": [
        {
            "id": "asset",
            "type": "asset",
            "position": {"x": 0, "y": 0},
            "title": "Input",
            "asset_kind": "text",
            "content": "hello",
        },
        {
            "id": "output",
            "type": "output",
            "position": {"x": 200, "y": 0},
            "title": "Output",
            "prompt": "请将上游内容渲染为完整 HTML 页面。",
        },
    ],
    "execution_edges": [{"id": "edge", "source": "asset", "target": "output"}],
}


@pytest.fixture(autouse=True)
def _workspace_runtime_ready(monkeypatch):
    async def start(db, workspace):
        await update_workspace_runtime_state(db, workspace_id=workspace.id, status="ready")

    monkeypatch.setattr("app.api.workspaces.start_workspace_runtime", start)
    monkeypatch.setattr("app.api.workspaces.schedule_workspace_turn", lambda _turn_id: None)

    async def stop(_workspace_id):
        return None

    monkeypatch.setattr("app.runtime.workspace_runtime.stop_workspace_runtime", stop)


def _create_workspace(client, name: str = "Workspace") -> dict:
    response = client.post(
        "/api/workspaces",
        json={"name": name, "description": "persistent project", "source": {"kind": "empty"}},
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_workspace_crud_sessions_files_and_owner_isolation(auth_client):
    workspace = _create_workspace(auth_client)
    workspace_id = workspace["id"]
    assert workspace["runtime_status"] == "ready"
    assert auth_client.get("/api/workspaces").json()[0]["id"] == workspace_id

    patched = auth_client.patch(
        f"/api/workspaces/{workspace_id}", json={"name": "Renamed"}
    )
    assert patched.status_code == 200
    assert patched.json()["name"] == "Renamed"

    session = auth_client.post(
        f"/api/workspaces/{workspace_id}/sessions", json={"title": "First"}
    )
    assert session.status_code == 200
    session_id = session.json()["id"]
    renamed = auth_client.patch(
        f"/api/workspace-sessions/{session_id}", json={"title": "Second"}
    )
    assert renamed.status_code == 200
    assert renamed.json()["title"] == "Second"

    second_session = auth_client.post(
        f"/api/workspaces/{workspace_id}/sessions", json={"title": "Searchable"}
    )
    assert second_session.status_code == 200
    second_session_id = second_session.json()["id"]
    turn = auth_client.post(
        f"/api/workspace-sessions/{second_session_id}/turns",
        json={"text": "Find this conversation"},
    )
    assert turn.status_code == 200, turn.text

    first_page = auth_client.get(
        f"/api/workspaces/{workspace_id}/sessions", params={"limit": 1}
    )
    assert first_page.status_code == 200
    assert first_page.json()["has_more"] is True
    assert len(first_page.json()["items"]) == 1
    assert first_page.json()["next_offset"] == 1

    title_search = auth_client.get(
        f"/api/workspaces/{workspace_id}/sessions", params={"q": "search"}
    )
    assert [item["id"] for item in title_search.json()["items"]] == [second_session_id]

    content_search = auth_client.get(
        f"/api/workspaces/{workspace_id}/sessions", params={"q": "conversation"}
    )
    content_item = content_search.json()["items"][0]
    assert content_item["id"] == second_session_id
    assert "conversation" in content_item["match_context"].lower()
    assert auth_client.get(
        f"/api/workspaces/{workspace_id}/sessions", params={"q": "role"}
    ).json()["items"] == []

    uploaded = auth_client.post(
        f"/api/workspaces/{workspace_id}/files",
        files=[("files", ("notes.md", b"# Notes\n", "text/markdown"))],
    )
    assert uploaded.status_code == 200, uploaded.text
    assert any(item["path"] == "notes.md" for item in uploaded.json()["files"])
    preview = auth_client.get(
        f"/api/workspaces/{workspace_id}/files/preview", params={"path": "notes.md"}
    )
    assert preview.status_code == 200
    assert preview.json()["content"] == "# Notes\n"
    assert auth_client.get(
        f"/api/workspaces/{workspace_id}/files/download", params={"path": "notes.md"}
    ).content == b"# Notes\n"
    assert auth_client.get(
        f"/api/workspaces/{workspace_id}/files/preview", params={"path": "../secret"}
    ).status_code == 400

    outsider = create_regular_user("workspace-outsider")
    auth_client.headers.update({"Authorization": f"Bearer {outsider['token']}"})
    assert auth_client.get(f"/api/workspaces/{workspace_id}").status_code == 404
    assert auth_client.get(f"/api/workspaces/{workspace_id}/sessions").status_code == 404
    assert auth_client.get(f"/api/workspace-sessions/{session_id}/events").status_code == 404


def test_workspace_zip_rejects_traversal_and_extracts_safe_files(auth_client):
    workspace_id = _create_workspace(auth_client, "ZIP")["id"]
    safe = io.BytesIO()
    with zipfile.ZipFile(safe, "w") as archive:
        archive.writestr("src/main.py", "print('ok')\n")
    response = auth_client.post(
        f"/api/workspaces/{workspace_id}/files",
        files=[("files", ("source.zip", safe.getvalue(), "application/zip"))],
    )
    assert response.status_code == 200, response.text
    assert any(item["path"] == "src/main.py" for item in response.json()["files"])

    unsafe = io.BytesIO()
    with zipfile.ZipFile(unsafe, "w") as archive:
        archive.writestr("../escape.txt", "bad")
    rejected = auth_client.post(
        f"/api/workspaces/{workspace_id}/files",
        files=[("files", ("unsafe.zip", unsafe.getvalue(), "application/zip"))],
    )
    assert rejected.status_code == 400


def test_workspace_files_hide_playwright_cli_runtime_directory(auth_client):
    workspace_id = _create_workspace(auth_client, "Browser artifacts")["id"]
    matches = list(
        (get_settings().runtime_dir / "persistent-workspaces").glob(
            f"*/{workspace_id}/project"
        )
    )
    assert len(matches) == 1
    internal = matches[0] / ".playwright-cli"
    internal.mkdir()
    (internal / "session.yaml").write_text("secret: browser-state\n", encoding="utf-8")

    listed = auth_client.get(f"/api/workspaces/{workspace_id}/files")
    assert listed.status_code == 200
    assert all(
        not item["path"].startswith(".playwright-cli")
        for item in listed.json()["files"]
    )
    assert auth_client.get(
        f"/api/workspaces/{workspace_id}/files/preview",
        params={"path": ".playwright-cli/session.yaml"},
    ).status_code == 400
    assert auth_client.get(
        f"/api/workspaces/{workspace_id}/files/download",
        params={"path": ".playwright-cli/session.yaml"},
    ).status_code == 400


def test_workspace_git_allowlist_defaults(auth_client):
    settings = auth_client.get("/api/settings")
    assert settings.status_code == 200
    assert settings.json()["workspace_git_allowed_hosts"] == [
        "github.com",
        "gitlab.com",
        "bitbucket.org",
        "gitee.com",
    ]


def test_workspace_git_allowlist_and_token_redaction(auth_client):
    workspace_id = _create_workspace(auth_client, "Git")["id"]
    configured = auth_client.put(
        "/api/settings/workspace-git-hosts", json={"hosts": ["git.example.com"]}
    )
    assert configured.status_code == 200
    assert configured.json()["workspace_git_allowed_hosts"] == ["git.example.com"]
    saved = auth_client.put(
        f"/api/workspaces/{workspace_id}/git-config",
        json={
            "repository_url": "https://git.example.com/acme/private.git",
            "default_branch": "main",
            "access_token": "very-secret-token",
        },
    )
    assert saved.status_code == 200, saved.text
    assert saved.json()["token_configured"] is True
    assert "secret" not in saved.text
    assert auth_client.put(
        f"/api/workspaces/{workspace_id}/git-config",
        json={"repository_url": "https://evil.example/repo.git"},
    ).status_code == 400


def test_workspace_wiki_sync_and_workflow_proposal_confirmation(auth_client):
    workspace_id = _create_workspace(auth_client, "Bridge")["id"]
    synced = auth_client.post(f"/api/workspaces/{workspace_id}/wiki/sync")
    assert synced.status_code == 200, synced.text
    assert synced.json()["status"] == "ready"
    assert auth_client.get(f"/api/workspaces/{workspace_id}/files").json()["files"] == []
    matches = list(
        (get_settings().runtime_dir / "persistent-workspaces").glob(
            f"*/{workspace_id}/.mira/wiki/working-tree"
        )
    )
    assert matches and (matches[0] / "wiki" / "index.md").is_file()
    assert (matches[0] / "purpose.md").is_file()
    assert (matches[0] / "schema.md").is_file()

    proposal = auth_client.post(
        f"/api/workspaces/{workspace_id}/workflow-proposals",
        json={
            "kind": "create",
            "name": "Workspace App",
            "description": "from proposal",
            "graph": GRAPH,
        },
    )
    assert proposal.status_code == 200, proposal.text
    assert proposal.json()["lint"]["ok"] is True
    confirmed = auth_client.post(
        f"/api/workspaces/{workspace_id}/workflow-proposals/{proposal.json()['id']}/confirm"
    )
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["status"] == "applied"
    assert confirmed.json()["app_id"]


def test_workspace_workflow_runs_are_read_only_call_history(auth_client):
    workspace_id = _create_workspace(auth_client, "Workflow history")["id"]
    response = auth_client.get(f"/api/workspaces/{workspace_id}/workflow-runs")
    assert response.status_code == 200
    assert response.json() == []
    assert auth_client.post(
        f"/api/workspaces/{workspace_id}/workflow-runs", json={"app_id": "app"}
    ).status_code == 405


def test_workspace_turn_and_decision_resume_validate_current_request(auth_client, monkeypatch):
    workspace_id = _create_workspace(auth_client, "Turn")["id"]
    session_id = auth_client.post(
        f"/api/workspaces/{workspace_id}/sessions", json={"title": "Chat"}
    ).json()["id"]
    created = auth_client.post(
        f"/api/workspace-sessions/{session_id}/turns", json={"text": "Need a choice"}
    )
    assert created.status_code == 200, created.text
    turn_id = created.json()["id"]
    events = auth_client.get(f"/api/workspace-sessions/{session_id}/events").json()
    assert events[-1]["event_type"] == "message_completed"
    assert events[-1]["payload"] == {
        "role": "user",
        "text": "Need a choice",
        "attachments": [],
    }

    async def waiting_event() -> None:
        async with SessionLocal() as db:
            turn = await db.get(WorkspaceTurn, turn_id)
            turn.status = "waiting"
            db.add(
                WorkspaceEvent(
                    workspace_id=workspace_id,
                    session_id=session_id,
                    turn_id=turn_id,
                    event_type="decision_request",
                    payload_json=dumps(
                        {
                            "request_id": "request-1",
                            "context": {"title": "Choose", "summary": "Choose one"},
                            "groups": [
                                {
                                    "id": "choice",
                                    "label": "Choice",
                                    "type": "single",
                                    "options": [
                                        {"label": "A", "description": "Option A", "recommended": True},
                                        {"label": "B", "description": "Option B", "recommended": False},
                                    ],
                                }
                            ],
                        }
                    ),
                    created_at=now_utc(),
                )
            )
            await db.commit()

    asyncio.run(waiting_event())
    accepted: dict[str, str] = {}

    async def resume(turn_id_value, request_id, result):
        accepted["turn_id"] = turn_id_value
        accepted["request_id"] = request_id
        assert result.answers[0].selected == ["A"]
        return True

    monkeypatch.setattr("app.api.workspaces.resume_workspace_turn", resume)
    stale = auth_client.post(
        f"/api/workspace-turns/{turn_id}/resume",
        json={"request_id": "old", "answers": [{"group_id": "choice", "selected": ["A"]}]},
    )
    assert stale.status_code == 409
    response = auth_client.post(
        f"/api/workspace-turns/{turn_id}/resume",
        json={"request_id": "request-1", "answers": [{"group_id": "choice", "selected": ["A"]}]},
    )
    assert response.status_code == 204, response.text
    assert accepted == {"turn_id": turn_id, "request_id": "request-1"}


def test_workspace_delete_removes_database_tree_and_runtime_directory(auth_client):
    workspace_id = _create_workspace(auth_client, "Delete")["id"]
    session = auth_client.post(
        f"/api/workspaces/{workspace_id}/sessions", json={"title": "Delete with workspace"}
    )
    assert session.status_code == 200, session.text
    session_id = session.json()["id"]
    turn = auth_client.post(
        f"/api/workspace-sessions/{session_id}/turns", json={"text": "Delete this turn"}
    )
    assert turn.status_code == 200, turn.text
    turn_id = turn.json()["id"]

    async def create_event() -> int:
        async with SessionLocal() as db:
            row = WorkspaceEvent(
                workspace_id=workspace_id,
                session_id=session_id,
                turn_id=turn_id,
                event_type="message",
                payload_json=dumps({"text": "Delete this event"}),
                created_at=now_utc(),
            )
            db.add(row)
            await db.commit()
            await db.refresh(row)
            return row.id

    event_id = asyncio.run(create_event())
    proposal = auth_client.post(
        f"/api/workspaces/{workspace_id}/workflow-proposals",
        json={
            "kind": "create",
            "name": "Delete with workspace",
            "description": "must not become orphaned",
            "graph": GRAPH,
        },
    )
    assert proposal.status_code == 200, proposal.text
    proposal_id = proposal.json()["id"]
    roots = list(
        (get_settings().runtime_dir / "persistent-workspaces").glob(f"*/{workspace_id}")
    )
    assert len(roots) == 1 and roots[0].is_dir()
    response = auth_client.delete(f"/api/workspaces/{workspace_id}")
    assert response.status_code == 204, response.text
    assert not roots[0].exists()
    assert auth_client.get(f"/api/workspaces/{workspace_id}").status_code == 404

    async def database_tree_was_deleted() -> bool:
        async with SessionLocal() as db:
            return all(
                item is None
                for item in (
                    await db.get(WorkspaceSession, session_id),
                    await db.get(WorkspaceTurn, turn_id),
                    await db.get(WorkspaceEvent, event_id),
                    await db.get(WorkspaceWorkflowProposal, proposal_id),
                )
            )

    assert asyncio.run(database_tree_was_deleted())


def test_replace_wiki_tree_can_replace_read_only_revision_copy(tmp_path: Path):
    source = tmp_path / "source"
    target = tmp_path / "target"
    (source / "wiki").mkdir(parents=True)
    (source / "wiki" / "index.md").write_text("new", encoding="utf-8")
    shutil.copytree(source, target)
    for path in target.rglob("*"):
        path.chmod(0o555 if path.is_dir() else 0o444)
    target.chmod(0o555)

    _replace_wiki_tree(source, target)

    assert (target / "wiki" / "index.md").read_text(encoding="utf-8") == "new"
    assert target.stat().st_mode & 0o700 == 0o700
