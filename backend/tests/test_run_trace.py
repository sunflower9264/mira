from __future__ import annotations

import asyncio
import json
import time
import uuid
from pathlib import Path

from app.runtime.base import AgentChunk, AgentExecutionResult, AgentRuntimeStatus
from app.runtime.factory import set_runtime_override
from app.services.runtime_paths import uploads_dir
from app.utils import now_utc
from tests.auth_helpers import create_regular_user
from tests.runtime_mock import MockRuntime


class TraceRuntime:
    async def detect_status(self) -> AgentRuntimeStatus:
        return AgentRuntimeStatus(
            installed=True,
            runnable=True,
            identity="trace-runtime",
            method="test",
            checked_at=now_utc(),
        )

    async def execute(
        self,
        *,
        prompt: str,
        session_id: str | None,
        model: str | None,
        reasoning_effort: str | None,
        cwd: Path,
        on_chunk,
        cancel_event: asyncio.Event,
        on_decision_request=None,
        runtime_tools=None,
        runtime_policy="execute",
        output_schema=None,
        session_scope=None,
        fork_session=False,
    ) -> AgentExecutionResult:
        if runtime_policy == "plan":
            text = '{"decision_summary":"无需额外提问。","reason":"测试 trace 场景不需要补充用户决策。"}'
            await on_chunk(AgentChunk(type="text", text=text))
            return AgentExecutionResult(
                session_id=session_id,
                total_text=text,
                finished_with="done",
            )
        if "你正在生成 Mira output 节点" in prompt:
            text = '{"html":"<section>OK</section>"}'
            await on_chunk(AgentChunk(type="text", text=text))
            return AgentExecutionResult(
                session_id=session_id or "trace_session",
                total_text=text,
                finished_with="done",
            )
        artifact = cwd / "report.txt"
        artifact.write_text("trace artifact", encoding="utf-8")
        upload_path = uploads_dir("user_admin") / "upl_trace" / "blob"
        await on_chunk(
            AgentChunk(
                type="tool_call",
                text="write report",
                raw={"tool": "write_file", "path": str(artifact), "upload_path": str(upload_path)},
            )
        )
        await on_chunk(
            AgentChunk(
                type="text",
                text=f"done: {artifact}",
                raw={"delta": str(artifact), "upload_path": str(upload_path)},
            )
        )
        return AgentExecutionResult(
            session_id=session_id or "trace_session",
            total_text=f"done: {artifact}",
            finished_with="done",
        )


def _build_app(auth_client, *, graph: dict) -> str:
    created = auth_client.post("/api/apps", json={"name": "TraceApp"}).json()
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


def _generate_graph() -> dict:
    return {
        "nodes": [
            {
                "id": "n_gen",
                "type": "generate",
                "position": {"x": 0, "y": 0},
                "title": "Generate Trace",
                "prompt": "hello trace",
                "model": "test-model",
                "reasoning_effort": "high",
            },
            {
                "id": "n_out",
                "type": "output",
                "position": {"x": 200, "y": 0},
                "title": "Output",
                "prompt": "render [[respond:<section>ok</section>]]",
            },
        ],
        "execution_edges": [{"id": "e_out", "source": "n_gen", "target": "n_out"}],
    }


def test_run_step_trace_returns_llm_debug_payload(auth_client, configure_codex):
    configure_codex()
    app_id = _build_app(auth_client, graph=_generate_graph())
    set_runtime_override(TraceRuntime())
    try:
        created = auth_client.post("/api/runs", json={"app_id": app_id, "inputs": {}})
        assert created.status_code == 200, created.text
        run_id = created.json()["run_id"]
        final = _wait_for_terminal(auth_client, run_id)
    finally:
        set_runtime_override(MockRuntime())

    assert final["status"] == "success"
    response = auth_client.get(f"/api/runs/{run_id}/steps/n_gen/trace")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["run_id"] == run_id
    assert body["node_id"] == "n_gen"
    assert body["node_type"] == "generate"
    assert body["node_title"] == "Generate Trace"
    assert body["status"] == "success"
    assert body["model"] == "test-model"
    assert body["reasoning_effort"] == "high"
    assert "agent_session_id" not in body
    assert body["started_at"].endswith("+08:00")
    assert body["logs"][0]["ts"].endswith("+08:00")
    assert "# 当前任务" in body["prompt"]
    assert "hello trace" in body["prompt"]
    assert "读取" not in body["raw_text"]
    assert body["chunks"][0]["type"] == "tool_call"
    assert body["chunks"][1]["type"] == "text"
    assert body["chunks"][0]["raw"]["path"] is None
    assert body["chunks"][0]["raw"]["upload_path"] == "[local path redacted]"
    assert body["chunks"][1]["raw"]["delta"] == "[local path redacted]"
    assert body["chunks"][1]["raw"]["upload_path"] == "[local path redacted]"
    assert "/runtime/workspaces/" not in body["chunks"][1]["text"]
    assert str(uploads_dir("user_admin")) not in json.dumps(body)
    assert "download_url" not in body["chunks"][1]["text"]
    assert "[local path redacted]" in body["chunks"][1]["text"]
    assert body["output"]
    assert body["artifacts"] == []


def test_run_step_trace_rejects_non_llm_node(auth_client, configure_codex):
    configure_codex()
    graph = {
        "nodes": [
            {
                "id": "n_input",
                "type": "user_input",
                "position": {"x": 0, "y": 0},
                "title": "Input",
                "input_schema": {"label": "input", "kind": "text"},
            },
            {
                "id": "n_out",
                "type": "output",
                "position": {"x": 200, "y": 0},
                "title": "Output",
                "prompt": "render [[respond:<section>ok</section>]]",
            },
        ],
        "execution_edges": [{"id": "e_out", "source": "n_input", "target": "n_out"}],
    }
    app_id = _build_app(auth_client, graph=graph)
    created = auth_client.post("/api/runs", json={"app_id": app_id, "inputs": {"n_input": "hello"}})
    assert created.status_code == 200, created.text
    run_id = created.json()["run_id"]
    _wait_for_terminal(auth_client, run_id)

    response = auth_client.get(f"/api/runs/{run_id}/steps/n_input/trace")
    assert response.status_code == 400
    assert response.json()["detail"] == "Trace 仅支持 LLM 节点"


def test_run_step_trace_404_for_other_user(auth_client, configure_codex):
    configure_codex()
    app_id = _build_app(auth_client, graph=_generate_graph())
    created = auth_client.post("/api/runs", json={"app_id": app_id, "inputs": {}})
    assert created.status_code == 200, created.text
    run_id = created.json()["run_id"]
    _wait_for_terminal(auth_client, run_id)

    user_token = create_regular_user(f"trace_{uuid.uuid4().hex[:8]}")["token"]
    response = auth_client.get(
        f"/api/runs/{run_id}/steps/n_gen/trace",
        headers={"Authorization": f"Bearer {user_token}"},
    )
    assert response.status_code == 404
    assert response.json()["detail"] == "运行记录不存在"
