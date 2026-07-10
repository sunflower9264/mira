from __future__ import annotations

import asyncio
import time
from pathlib import Path

from app.db import SessionLocal
from app.models import App
from app.runtime.base import AgentChunk, AgentExecutionResult, AgentProviderStatus
from app.runtime.factory import set_runtime_override
from app.services.admin import ADMIN_USER_ID
from app.services.uploads import resolve_upload
from app.utils import dumps, now_utc
from tests.auth_helpers import create_regular_user
from tests.runtime_mock import MockRuntime

USER_INPUT_NODE = {
    "id": "n_input",
    "type": "user_input",
    "position": {"x": 0, "y": 0},
    "title": "Input",
    "input_schema": {"label": "input", "kind": "text"},
}
ASSET_NODE = {
    "id": "n_asset",
    "type": "asset",
    "position": {"x": 100, "y": 0},
    "title": "Asset",
    "asset_kind": "text",
    "content": "demo",
}
OUTPUT_FROM_INPUT_NODE = {
    "id": "n_out",
    "type": "output",
    "position": {"x": 200, "y": 0},
    "title": "Output",
    "prompt": "render [[respond:<section>ok</section>]]",
    "source_node_id": "n_input",
}


def _user_input_output_graph() -> dict:
    return {
        "agent": "claude",
        "nodes": [USER_INPUT_NODE, OUTPUT_FROM_INPUT_NODE],
        "edges": [{"id": "e_out", "source": "n_input", "target": "n_out"}],
    }


class PromptCaptureRuntime:
    def __init__(self) -> None:
        self.prompts: list[str] = []

    async def detect_status(self) -> AgentProviderStatus:
        return AgentProviderStatus(
            installed=True,
            runnable=True,
            identity="prompt-capture",
            method="test",
            checked_at=now_utc(),
        )

    async def execute(
        self,
        *,
        prompt: str,
        session_id: str | None,
        allowed_tools: list[str] | None,
        model: str | None,
        reasoning_effort: str | None,
        cwd: Path,
        on_chunk,
        cancel_event: asyncio.Event,
        on_ask_user=None,
        runtime_tools=None,
        runtime_policy="execute",
        output_schema=None,
    ) -> AgentExecutionResult:
        if runtime_policy == "ask_user_plan":
            text = '{"action":"complete","decision_summary":"无需额外提问。","reason":"测试场景不需要补充用户决策。"}'
        elif "你正在生成 Mira output 节点" in prompt:
            self.prompts.append(prompt)
            text = '{"html":"<section>OK</section>"}'
        else:
            self.prompts.append(prompt)
            text = "OK"
        await on_chunk(AgentChunk(type="text", text=text))
        return AgentExecutionResult(session_id=session_id or "prompt_capture", total_text=text, finished_with="done")


def _build_app(auth_client, *, graph: dict) -> str:
    created = auth_client.post("/api/apps", json={"name": "RunCrudApp"}).json()
    response = auth_client.patch(f"/api/apps/{created['id']}", json={"graph": graph})
    assert response.status_code == 200, response.text
    return created["id"]


def _wait_for_terminal(auth_client, run_id: str, *, timeout: float = 5.0) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        body = auth_client.get(f"/api/runs/{run_id}").json()
        if body["status"] in {"success", "failed", "cancelled"}:
            return body
        time.sleep(0.05)
    raise AssertionError(f"run {run_id} did not finish within {timeout}s")


def test_create_run_with_simple_input_asset_graph(auth_client, enable_claude_agent):
    enable_claude_agent()
    graph = {
        "agent": "claude",
        "nodes": [
            USER_INPUT_NODE,
            ASSET_NODE,
            OUTPUT_FROM_INPUT_NODE,
        ],
        "edges": [{"id": "e_out", "source": "n_input", "target": "n_out"}],
    }
    app_id = _build_app(auth_client, graph=graph)
    response = auth_client.post(
        "/api/runs",
        json={"app_id": app_id, "inputs": {"n_input": "hello"}},
    )
    assert response.status_code == 200, response.text
    run_id = response.json()["run_id"]
    assert run_id.startswith("run_")
    final = _wait_for_terminal(auth_client, run_id)
    assert final["status"] == "success"
    assert [step["node_id"] for step in final["steps"]] == ["n_asset", "n_input", "n_out"]
    assert all(step["status"] == "success" for step in final["steps"])


def test_create_run_rejects_unknown_input_key(auth_client, enable_claude_agent):
    enable_claude_agent()
    graph = _user_input_output_graph()
    app_id = _build_app(auth_client, graph=graph)
    response = auth_client.post(
        "/api/runs",
        json={"app_id": app_id, "inputs": {"n_unknown": "x"}},
    )
    assert response.status_code == 400
    assert "不存在" in response.json()["detail"]


def test_create_run_rejects_non_empty_graph_without_output(auth_client):
    graph = {
        "nodes": [USER_INPUT_NODE],
        "edges": [],
    }
    app_id = _build_app(auth_client, graph=graph)
    response = auth_client.post(
        "/api/runs",
        json={"app_id": app_id, "inputs": {"n_input": "hello"}},
    )
    assert response.status_code == 400
    assert response.json()["detail"] == "工作流必须包含 output 节点"


def test_create_run_revalidates_stored_asset_upload_owner(auth_client, enable_claude_agent):
    enable_claude_agent()
    created = auth_client.post("/api/apps", json={"name": "Stored Missing Asset Run"}).json()
    graph = {
        "agent": "claude",
        "nodes": [
            {
                "id": "n_asset",
                "type": "asset",
                "position": {"x": 0, "y": 0},
                "title": "Files",
                "asset_kind": "file",
                "uploads": [{"id": "upl_missing", "name": "missing.txt"}],
            },
            {
                "id": "n_out",
                "type": "output",
                "position": {"x": 220, "y": 0},
                "title": "Output",
                "prompt": "render",
                "source_node_id": "n_asset",
            },
        ],
        "edges": [{"id": "e_out", "source": "n_asset", "target": "n_out"}],
    }

    async def store_graph() -> None:
        async with SessionLocal() as db:
            app = await db.get(App, created["id"])
            assert app is not None
            app.graph_json = dumps(graph)
            await db.commit()

    asyncio.run(store_graph())
    response = auth_client.post("/api/runs", json={"app_id": created["id"], "inputs": {}})
    assert response.status_code == 400
    assert response.json()["detail"] == "素材上传文件不存在"


def test_patch_app_rejects_cyclic_graph(auth_client):
    graph = {
        "agent": "claude",
        "nodes": [
            {
                "id": "n_gen_a",
                "type": "generate",
                "position": {"x": 0, "y": 0},
                "title": "A",
                "prompt": "a",
            },
            {
                "id": "n_gen_b",
                "type": "generate",
                "position": {"x": 0, "y": 0},
                "title": "B",
                "prompt": "b",
            },
        ],
        "edges": [
            {"id": "e1", "source": "n_gen_a", "target": "n_gen_b"},
            {"id": "e2", "source": "n_gen_b", "target": "n_gen_a"},
        ],
    }
    created = auth_client.post("/api/apps", json={"name": "RunCrudApp"}).json()
    response = auth_client.patch(f"/api/apps/{created['id']}", json={"graph": graph})
    assert response.status_code == 400
    assert "环路" in response.json()["detail"]


def test_create_run_requires_enabled_agent_for_generate_graph(auth_client):
    graph = {
        "agent": "claude",
        "nodes": [
            {
                "id": "n_gen",
                "type": "generate",
                "position": {"x": 0, "y": 0},
                "title": "Gen",
                "prompt": "hi",
            },
            {
                "id": "n_out",
                "type": "output",
                "position": {"x": 200, "y": 0},
                "title": "Output",
                "prompt": "render",
                "source_node_id": "n_gen",
            },
        ],
        "edges": [{"id": "e_out", "source": "n_gen", "target": "n_out"}],
    }
    app_id = _build_app(auth_client, graph=graph)
    response = auth_client.post("/api/runs", json={"app_id": app_id, "inputs": {}})
    assert response.status_code == 400
    assert response.json()["detail"] == "无可用 Agent，请先在设置中启用 Agent"


def test_create_run_requires_graph_agent_for_generate_graph(auth_client, enable_claude_agent):
    enable_claude_agent()
    graph = {
        "nodes": [
            {
                "id": "n_gen",
                "type": "generate",
                "position": {"x": 0, "y": 0},
                "title": "Gen",
                "prompt": "hi",
            },
            {
                "id": "n_out",
                "type": "output",
                "position": {"x": 200, "y": 0},
                "title": "Output",
                "prompt": "render",
                "source_node_id": "n_gen",
            },
        ],
        "edges": [{"id": "e_out", "source": "n_gen", "target": "n_out"}],
    }
    app_id = _build_app(auth_client, graph=graph)
    response = auth_client.post("/api/runs", json={"app_id": app_id, "inputs": {}})
    assert response.status_code == 400
    assert response.json()["detail"] == "应用必须选择 Agent"


def test_create_run_rejects_disabled_graph_agent(auth_client, enable_claude_agent):
    enable_claude_agent()
    graph = {
        "agent": "codex",
        "nodes": [
            {
                "id": "n_gen",
                "type": "generate",
                "position": {"x": 0, "y": 0},
                "title": "Gen",
                "prompt": "hi",
            },
            {
                "id": "n_out",
                "type": "output",
                "position": {"x": 200, "y": 0},
                "title": "Output",
                "prompt": "render",
                "source_node_id": "n_gen",
            },
        ],
        "edges": [{"id": "e_out", "source": "n_gen", "target": "n_out"}],
    }
    app_id = _build_app(auth_client, graph=graph)
    response = auth_client.post("/api/runs", json={"app_id": app_id, "inputs": {}})
    assert response.status_code == 400
    assert "未启用" in response.json()["detail"]


def test_runtime_prompt_uses_staged_upload_path_for_input_attachment(auth_client, enable_claude_agent):
    enable_claude_agent()
    runtime = PromptCaptureRuntime()
    set_runtime_override(runtime)
    try:
        upload = auth_client.post(
            "/api/uploads",
            files={"file": ("brief.txt", b"upload-body", "text/plain")},
        )
        assert upload.status_code == 200, upload.text
        upload_id = upload.json()["id"]
        graph = {
            "agent": "claude",
            "nodes": [
                USER_INPUT_NODE,
                {
                    "id": "n_gen",
                    "type": "generate",
                    "position": {"x": 200, "y": 0},
                    "title": "Gen",
                    "prompt": "Read the attached file.",
                },
                {
                    "id": "n_out",
                    "type": "output",
                    "position": {"x": 400, "y": 0},
                    "title": "Output",
                    "prompt": "render",
                    "source_node_id": "n_gen",
                },
            ],
            "edges": [
                {"id": "e1", "source": "n_input", "target": "n_gen"},
                {"id": "e_out", "source": "n_gen", "target": "n_out"},
            ],
        }
        app_id = _build_app(auth_client, graph=graph)

        response = auth_client.post(
            "/api/runs",
            json={
                "app_id": app_id,
                "inputs": {
                    "n_input": {
                        "value": "hello",
                        "attachments": [{"id": upload_id, "name": "brief.txt"}],
                    }
                },
            },
        )
        assert response.status_code == 200, response.text
        final = _wait_for_terminal(auth_client, response.json()["run_id"])

        assert final["status"] == "success"
        assert runtime.prompts
        prompt = runtime.prompts[0]
        assert f"/uploads/{upload_id}/blob" not in prompt
        assert ".uploads/" in prompt
        assert f"/{upload_id}/blob" in prompt
    finally:
        set_runtime_override(MockRuntime())


def test_non_owner_can_run_run_only_app_with_file_asset(auth_client, enable_claude_agent):
    enable_claude_agent()
    runtime = PromptCaptureRuntime()
    set_runtime_override(runtime)
    try:
        upload = auth_client.post(
            "/api/uploads",
            files={"file": ("source.txt", b"owner asset", "text/plain")},
        )
        assert upload.status_code == 200, upload.text
        upload_id = upload.json()["id"]
        app = auth_client.post("/api/apps", json={"name": "Run Only File Asset"}).json()
        graph = {
            "agent": "claude",
            "nodes": [
                {
                    "id": "n_asset",
                    "type": "asset",
                    "position": {"x": 0, "y": 0},
                    "title": "Owner File",
                    "asset_kind": "file",
                    "uploads": [upload.json()],
                },
                {
                    "id": "n_gen",
                    "type": "generate",
                    "position": {"x": 200, "y": 0},
                    "title": "Gen",
                    "prompt": "Read the owner file.",
                },
                {
                    "id": "n_out",
                    "type": "output",
                    "position": {"x": 400, "y": 0},
                    "title": "Output",
                    "prompt": "render",
                    "source_node_id": "n_gen",
                },
            ],
            "edges": [
                {"id": "e1", "source": "n_asset", "target": "n_gen"},
                {"id": "e_out", "source": "n_gen", "target": "n_out"},
            ],
        }
        patched = auth_client.patch(f"/api/apps/{app['id']}", json={"graph": graph})
        assert patched.status_code == 200, patched.text
        published = auth_client.post(
            f"/api/apps/{app['id']}/publish",
            json={"visibility": "public", "market_access": "run_only"},
        )
        assert published.status_code == 200, published.text

        runner = create_regular_user("run-only-file-asset-runner")
        auth_client.headers.update({"Authorization": f"Bearer {runner['token']}"})
        run = auth_client.post("/api/runs", json={"app_id": app["id"], "inputs": {}})
        assert run.status_code == 200, run.text
        final = _wait_for_terminal(auth_client, run.json()["run_id"])

        assert final["status"] == "success"
        assert runtime.prompts
        prompt = runtime.prompts[0]
        assert ".uploads/" in prompt
        assert f"/{upload_id}/blob" in prompt
        assert f"/uploads/{upload_id}/blob" not in prompt
    finally:
        set_runtime_override(MockRuntime())


def test_non_owner_can_run_public_app_with_drawing_asset(auth_client, enable_claude_agent):
    enable_claude_agent()
    runtime = PromptCaptureRuntime()
    set_runtime_override(runtime)
    try:
        upload = auth_client.post(
            "/api/uploads",
            files={"file": ("drawing.png", b"owner drawing", "image/png")},
        )
        assert upload.status_code == 200, upload.text
        upload_id = upload.json()["id"]
        app = auth_client.post("/api/apps", json={"name": "Public Drawing Asset"}).json()
        graph = {
            "agent": "claude",
            "nodes": [
                {
                    "id": "n_asset",
                    "type": "asset",
                    "position": {"x": 0, "y": 0},
                    "title": "Owner Drawing",
                    "asset_kind": "drawing",
                    "upload": upload.json(),
                },
                {
                    "id": "n_gen",
                    "type": "generate",
                    "position": {"x": 200, "y": 0},
                    "title": "Gen",
                    "prompt": "Read the owner drawing.",
                },
                {
                    "id": "n_out",
                    "type": "output",
                    "position": {"x": 400, "y": 0},
                    "title": "Output",
                    "prompt": "render",
                    "source_node_id": "n_gen",
                },
            ],
            "edges": [
                {"id": "e1", "source": "n_asset", "target": "n_gen"},
                {"id": "e_out", "source": "n_gen", "target": "n_out"},
            ],
        }
        patched = auth_client.patch(f"/api/apps/{app['id']}", json={"graph": graph})
        assert patched.status_code == 200, patched.text
        published = auth_client.post(f"/api/apps/{app['id']}/publish", json={"visibility": "public"})
        assert published.status_code == 200, published.text

        runner = create_regular_user("public-drawing-asset-runner")
        auth_client.headers.update({"Authorization": f"Bearer {runner['token']}"})
        run = auth_client.post("/api/runs", json={"app_id": app["id"], "inputs": {}})
        assert run.status_code == 200, run.text
        final = _wait_for_terminal(auth_client, run.json()["run_id"])

        assert final["status"] == "success"
        assert runtime.prompts
        prompt = runtime.prompts[0]
        assert ".uploads/" in prompt
        assert f"/{upload_id}/blob" in prompt
        assert f"/uploads/{upload_id}/blob" not in prompt
    finally:
        set_runtime_override(MockRuntime())


def test_get_run_404_for_other_user(auth_client, enable_claude_agent):
    enable_claude_agent()
    graph = _user_input_output_graph()
    app_id = _build_app(auth_client, graph=graph)
    run = auth_client.post(
        "/api/runs", json={"app_id": app_id, "inputs": {"n_input": "x"}}
    ).json()
    # 创建一个独立用户拿 token，避免污染 auth_client 的默认 headers。
    user_token = create_regular_user()["token"]
    response = auth_client.get(
        f"/api/runs/{run['run_id']}",
        headers={"Authorization": f"Bearer {user_token}"},
    )
    assert response.status_code == 404
    assert response.json()["detail"] == "运行记录不存在"


def test_list_runs_for_app_returns_recent_first(auth_client, enable_claude_agent):
    enable_claude_agent()
    graph = _user_input_output_graph()
    app_id = _build_app(auth_client, graph=graph)
    run_ids = []
    for _ in range(3):
        run = auth_client.post(
            "/api/runs", json={"app_id": app_id, "inputs": {"n_input": "x"}}
        ).json()
        _wait_for_terminal(auth_client, run["run_id"])
        run_ids.append(run["run_id"])
    response = auth_client.get(f"/api/apps/{app_id}/runs")
    assert response.status_code == 200
    body = response.json()
    assert len(body) == 3
    assert {item["id"] for item in body} == set(run_ids)


def test_list_run_summaries_omits_heavy_run_detail(auth_client, enable_claude_agent):
    enable_claude_agent()
    graph = _user_input_output_graph()
    app_id = _build_app(auth_client, graph=graph)
    run = auth_client.post(
        "/api/runs", json={"app_id": app_id, "inputs": {"n_input": "history summary"}}
    ).json()
    _wait_for_terminal(auth_client, run["run_id"])

    response = auth_client.get(f"/api/apps/{app_id}/runs/summary")
    assert response.status_code == 200, response.text
    body = response.json()
    assert [item["id"] for item in body] == [run["run_id"]]
    summary = body[0]
    assert summary["inputs"] == {"n_input": {"value": "history summary", "attachments": []}}
    assert summary["status"] == "success"
    assert "graph" not in summary
    assert "steps" not in summary
    assert "recovery" not in summary


def test_public_app_run_history_is_isolated_per_runner(auth_client, enable_claude_agent):
    enable_claude_agent()
    admin_auth = auth_client.headers["Authorization"]
    graph = _user_input_output_graph()
    app_id = _build_app(auth_client, graph=graph)
    published = auth_client.post(f"/api/apps/{app_id}/publish", json={"visibility": "public"})
    assert published.status_code == 200, published.text

    owner_run = auth_client.post(
        "/api/runs", json={"app_id": app_id, "inputs": {"n_input": "owner input"}}
    )
    assert owner_run.status_code == 200, owner_run.text
    owner_run_id = owner_run.json()["run_id"]
    _wait_for_terminal(auth_client, owner_run_id)

    runner = create_regular_user("run-history-runner")
    auth_client.headers.update({"Authorization": f"Bearer {runner['token']}"})
    runner_run = auth_client.post(
        "/api/runs", json={"app_id": app_id, "inputs": {"n_input": "runner input"}}
    )
    assert runner_run.status_code == 200, runner_run.text
    runner_run_id = runner_run.json()["run_id"]
    _wait_for_terminal(auth_client, runner_run_id)

    runner_runs = auth_client.get(f"/api/apps/{app_id}/runs")
    assert runner_runs.status_code == 200, runner_runs.text
    assert [item["id"] for item in runner_runs.json()] == [runner_run_id]
    runner_summaries = auth_client.get(f"/api/apps/{app_id}/runs/summary")
    assert runner_summaries.status_code == 200, runner_summaries.text
    assert [item["id"] for item in runner_summaries.json()] == [runner_run_id]
    assert auth_client.get(f"/api/runs/{owner_run_id}").status_code == 404

    spectator = create_regular_user("run-history-spectator")
    auth_client.headers.update({"Authorization": f"Bearer {spectator['token']}"})
    spectator_runs = auth_client.get(f"/api/apps/{app_id}/runs")
    assert spectator_runs.status_code == 200, spectator_runs.text
    assert spectator_runs.json() == []
    spectator_summaries = auth_client.get(f"/api/apps/{app_id}/runs/summary")
    assert spectator_summaries.status_code == 200, spectator_summaries.text
    assert spectator_summaries.json() == []

    auth_client.headers.update({"Authorization": admin_auth})
    owner_runs = auth_client.get(f"/api/apps/{app_id}/runs")
    assert owner_runs.status_code == 200, owner_runs.text
    assert [item["id"] for item in owner_runs.json()] == [owner_run_id]
    owner_summaries = auth_client.get(f"/api/apps/{app_id}/runs/summary")
    assert owner_summaries.status_code == 200, owner_summaries.text
    assert [item["id"] for item in owner_summaries.json()] == [owner_run_id]
    assert auth_client.get(f"/api/runs/{runner_run_id}").status_code == 404


def test_delete_run_only_after_terminal(auth_client, enable_claude_agent):
    enable_claude_agent()
    graph = _user_input_output_graph()
    app_id = _build_app(auth_client, graph=graph)
    run = auth_client.post(
        "/api/runs", json={"app_id": app_id, "inputs": {"n_input": "x"}}
    ).json()
    final = _wait_for_terminal(auth_client, run["run_id"])
    assert final["status"] == "success"
    response = auth_client.delete(f"/api/runs/{run['run_id']}")
    assert response.status_code == 204
    response = auth_client.get(f"/api/runs/{run['run_id']}")
    assert response.status_code == 404


def test_delete_app_cleans_run_input_uploads(auth_client, enable_claude_agent):
    enable_claude_agent()
    graph = _user_input_output_graph()
    app_id = _build_app(auth_client, graph=graph)
    upload = auth_client.post(
        "/api/uploads",
        files={"file": ("reference.txt", b"hello", "text/plain")},
    )
    assert upload.status_code == 200, upload.text
    upload_id = upload.json()["id"]
    assert resolve_upload(ADMIN_USER_ID, upload_id) is not None

    run = auth_client.post(
        "/api/runs",
        json={
            "app_id": app_id,
            "inputs": {
                "n_input": {
                    "value": "x",
                    "attachments": [{"id": upload_id, "name": "reference.txt"}],
                }
            },
        },
    ).json()
    final = _wait_for_terminal(auth_client, run["run_id"])
    assert final["status"] == "success"

    response = auth_client.delete(f"/api/apps/{app_id}")
    assert response.status_code == 204
    assert auth_client.get(f"/api/apps/{app_id}").status_code == 404
    assert auth_client.get(f"/api/runs/{run['run_id']}").status_code == 404
    assert resolve_upload(ADMIN_USER_ID, upload_id) is None


def test_delete_public_app_with_other_user_runs_archives_and_keeps_history(auth_client, enable_claude_agent):
    enable_claude_agent()
    admin_auth = auth_client.headers["Authorization"]
    graph = _user_input_output_graph()
    app_id = _build_app(auth_client, graph=graph)
    published = auth_client.post(f"/api/apps/{app_id}/publish", json={"visibility": "public"})
    assert published.status_code == 200, published.text

    owner_run = auth_client.post(
        "/api/runs", json={"app_id": app_id, "inputs": {"n_input": "owner input"}}
    )
    assert owner_run.status_code == 200, owner_run.text
    owner_run_id = owner_run.json()["run_id"]
    _wait_for_terminal(auth_client, owner_run_id)

    runner = create_regular_user("archived-app-runner")
    auth_client.headers.update({"Authorization": f"Bearer {runner['token']}"})
    upload = auth_client.post(
        "/api/uploads",
        files={"file": ("runner-reference.txt", b"hello", "text/plain")},
    )
    assert upload.status_code == 200, upload.text
    upload_id = upload.json()["id"]
    runner_run = auth_client.post(
        "/api/runs",
        json={
            "app_id": app_id,
            "inputs": {
                "n_input": {
                    "value": "runner input",
                    "attachments": [{"id": upload_id, "name": "runner-reference.txt"}],
                }
            },
        },
    )
    assert runner_run.status_code == 200, runner_run.text
    runner_run_id = runner_run.json()["run_id"]
    _wait_for_terminal(auth_client, runner_run_id)

    auth_client.headers.update({"Authorization": admin_auth})
    deleted = auth_client.delete(f"/api/apps/{app_id}")
    assert deleted.status_code == 204, deleted.text

    my_apps = auth_client.get("/api/apps")
    assert my_apps.status_code == 200, my_apps.text
    assert all(app["id"] != app_id for app in my_apps.json())
    market = auth_client.get("/api/apps", params={"market": "true"})
    assert market.status_code == 200, market.text
    assert all(app["id"] != app_id for app in market.json())

    owner_app = auth_client.get(f"/api/apps/{app_id}")
    assert owner_app.status_code == 200, owner_app.text
    owner_body = owner_app.json()
    assert owner_body["archived_at"] is not None
    assert owner_body["can_edit"] is False
    assert owner_body["can_clone"] is False
    assert owner_body["can_run"] is False
    assert owner_body["can_view_source"] is False
    assert auth_client.get(f"/api/runs/{owner_run_id}").status_code == 200
    assert auth_client.post("/api/runs", json={"app_id": app_id, "inputs": {"n_input": "again"}}).status_code == 400
    assert auth_client.patch(f"/api/apps/{app_id}", json={"name": "Nope"}).status_code == 404
    assert auth_client.post(f"/api/apps/{app_id}/publish", json={"visibility": "public"}).status_code == 404
    assert auth_client.post(f"/api/apps/{app_id}/versions", json={"label": "Nope"}).status_code == 404

    auth_client.headers.update({"Authorization": f"Bearer {runner['token']}"})
    recent = auth_client.get("/api/apps/recent-runs")
    assert recent.status_code == 200, recent.text
    recent_app = next(app for app in recent.json() if app["id"] == app_id)
    assert recent_app["archived_at"] is not None
    assert recent_app["can_run"] is False
    assert recent_app["can_clone"] is False
    assert recent_app["can_view_source"] is False
    assert auth_client.get(f"/api/apps/{app_id}").status_code == 200
    assert auth_client.get(f"/api/runs/{runner_run_id}").status_code == 200
    runner_runs = auth_client.get(f"/api/apps/{app_id}/runs")
    assert runner_runs.status_code == 200, runner_runs.text
    assert [item["id"] for item in runner_runs.json()] == [runner_run_id]
    assert auth_client.post("/api/runs", json={"app_id": app_id, "inputs": {"n_input": "again"}}).status_code == 400
    assert auth_client.post(f"/api/apps/{app_id}/clone").status_code == 403
    assert resolve_upload(runner["id"], upload_id) is not None

    spectator = create_regular_user("archived-app-spectator")
    auth_client.headers.update({"Authorization": f"Bearer {spectator['token']}"})
    assert auth_client.get(f"/api/apps/{app_id}").status_code == 404
    assert auth_client.post("/api/runs", json={"app_id": app_id, "inputs": {"n_input": "x"}}).status_code == 404


def test_delete_run_rejected_when_not_terminal(auth_client, monkeypatch, enable_claude_agent):
    """通过把 orchestrator schedule 改成 no-op，让 run 永远停在 pending。"""
    enable_claude_agent()

    from app.api import runs as runs_api

    def _no_schedule(_run_id: str):
        return None

    monkeypatch.setattr(runs_api, "schedule_run", _no_schedule)
    graph = _user_input_output_graph()
    app_id = _build_app(auth_client, graph=graph)
    run = auth_client.post(
        "/api/runs", json={"app_id": app_id, "inputs": {"n_input": "x"}}
    ).json()
    response = auth_client.delete(f"/api/runs/{run['run_id']}")
    assert response.status_code == 400
    assert response.json()["detail"] == "只能删除已结束的运行"


def test_run_attachments_404_for_unknown_upload(auth_client, enable_claude_agent):
    enable_claude_agent()
    graph = _user_input_output_graph()
    app_id = _build_app(auth_client, graph=graph)
    response = auth_client.post(
        "/api/runs",
        json={
            "app_id": app_id,
            "inputs": {
                "n_input": {"value": "x", "attachments": [{"id": "upl_does_not_exist"}]}
            },
        },
    )
    assert response.status_code == 404
    assert response.json()["detail"] == "附件不存在"


def test_run_inputs_too_large(auth_client, monkeypatch, enable_claude_agent):
    from app.config import get_settings

    enable_claude_agent()
    monkeypatch.setattr(get_settings(), "max_input_size_bytes", 8)
    graph = _user_input_output_graph()
    app_id = _build_app(auth_client, graph=graph)
    response = auth_client.post(
        "/api/runs",
        json={"app_id": app_id, "inputs": {"n_input": "0123456789"}},
    )
    assert response.status_code == 400
    assert response.json()["detail"] == "输入内容过大"
