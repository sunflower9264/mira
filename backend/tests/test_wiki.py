from __future__ import annotations

import time

from app.services.runtime_paths import wiki_run_snapshot_path
from tests.auth_helpers import create_regular_user


GRAPH = {
    "nodes": [
        {
            "id": "input",
            "type": "user_input",
            "position": {"x": 0, "y": 0},
            "title": "Input",
            "input_schema": {"label": "Input", "kind": "text"},
        },
        {
            "id": "output",
            "type": "output",
            "position": {"x": 200, "y": 0},
            "title": "Output",
            "prompt": "render [[respond:<p>ok</p>]]",
        },
    ],
    "execution_edges": [{"id": "edge", "source": "input", "target": "output"}],
}


def _wait_operations(client, *, timeout: float = 5.0) -> list[dict]:
    deadline = time.time() + timeout
    while time.time() < deadline:
        operations = client.get("/api/wiki/operations").json()
        if operations and all(item["status"] not in {"pending", "running"} for item in operations):
            return operations
        time.sleep(0.05)
    raise AssertionError("Wiki operation did not finish")


def _upload_unsupported(client, name: str = "archive.bin") -> dict:
    response = client.post(
        "/api/wiki/sources",
        data={"path": name},
        files={"file": (name, b"binary knowledge", "application/octet-stream")},
    )
    assert response.status_code == 200, response.text
    operations = _wait_operations(client)
    assert operations[0]["status"] == "success", operations
    return response.json()["source"]


def test_wiki_initializes_required_files_and_rejects_unsafe_paths(auth_client):
    info = auth_client.get("/api/wiki")
    assert info.status_code == 200
    assert info.json()["source_count"] == 0
    paths = {item["path"] for item in auth_client.get("/api/wiki/tree").json()}
    assert {"purpose.md", "schema.md", "wiki/index.md", "wiki/log.md", "wiki/overview.md"} <= paths

    response = auth_client.post(
        "/api/wiki/sources",
        data={"path": "../escape.md"},
        files={"file": ("escape.md", b"unsafe", "text/markdown")},
    )
    assert response.status_code == 400


def test_unsupported_source_is_preserved_and_revision_can_restore(auth_client):
    source = _upload_unsupported(auth_client)
    sources = auth_client.get("/api/wiki/sources").json()
    assert sources[0]["id"] == source["id"]
    assert sources[0]["status"] == "unsupported"

    files = auth_client.get("/api/wiki/tree").json()
    raw = next(item for item in files if item["path"] == "raw/archive.bin")
    download = auth_client.get(raw["download_url"])
    assert download.status_code == 200
    assert download.content == b"binary knowledge"

    revisions = auth_client.get("/api/wiki/revisions").json()
    assert len(revisions) >= 2
    initial = revisions[-1]
    restored = auth_client.post(f"/api/wiki/revisions/{initial['id']}/restore")
    assert restored.status_code == 200
    assert restored.json()["current"] is True
    restored_paths = {item["path"] for item in auth_client.get("/api/wiki/tree").json()}
    assert "raw/archive.bin" not in restored_paths


def test_third_party_app_requires_graph_bound_consent_and_can_skip(
    auth_client,
    configure_codex,
):
    configure_codex()
    app = auth_client.post("/api/apps", json={"name": "Shared Wiki App"}).json()
    patched = auth_client.patch(f"/api/apps/{app['id']}", json={"graph": GRAPH})
    assert patched.status_code == 200, patched.text
    published = auth_client.post(
        f"/api/apps/{app['id']}/publish",
        json={"visibility": "public", "market_access": "run_only"},
    )
    assert published.status_code == 200, published.text

    runner = create_regular_user("wiki-consent-runner")
    auth_client.headers.update({"Authorization": f"Bearer {runner['token']}"})
    _upload_unsupported(auth_client, "private-notes.bin")

    access = auth_client.get(f"/api/apps/{app['id']}/wiki-access")
    assert access.status_code == 200
    assert access.json()["requires_consent"] is True
    blocked = auth_client.post(
        "/api/runs",
        json={"app_id": app["id"], "inputs": {"input": "hello"}},
    )
    assert blocked.status_code == 409

    skipped = auth_client.post(
        "/api/runs",
        json={"app_id": app["id"], "inputs": {"input": "hello"}, "wiki_mode": "without"},
    )
    assert skipped.status_code == 200, skipped.text
    assert not wiki_run_snapshot_path(skipped.json()["run_id"]).exists()

    grant = auth_client.post(
        f"/api/apps/{app['id']}/wiki-access",
        json={"graph_sha256": access.json()["graph_sha256"]},
    )
    assert grant.status_code == 200, grant.text
    assert grant.json()["granted"] is True
    created = auth_client.post(
        "/api/runs",
        json={"app_id": app["id"], "inputs": {"input": "hello"}},
    )
    assert created.status_code == 200, created.text
    snapshot = wiki_run_snapshot_path(created.json()["run_id"])
    assert (snapshot / "tree" / "raw" / "private-notes.bin").read_bytes() == b"binary knowledge"


def test_owner_run_freezes_revision_without_writing_back(auth_client, configure_codex):
    configure_codex()
    _upload_unsupported(auth_client, "facts.bin")
    before = auth_client.get("/api/wiki").json()["current_revision_id"]
    app = auth_client.post("/api/apps", json={"name": "Owner Wiki App"}).json()
    assert auth_client.patch(f"/api/apps/{app['id']}", json={"graph": GRAPH}).status_code == 200
    created = auth_client.post(
        "/api/runs",
        json={"app_id": app["id"], "inputs": {"input": "hello"}},
    )
    assert created.status_code == 200, created.text
    assert wiki_run_snapshot_path(created.json()["run_id"]).is_dir()
    assert auth_client.get("/api/wiki").json()["current_revision_id"] == before
