from __future__ import annotations

import asyncio
import json
from pathlib import Path

from app.runtime.base import AgentChunk, AgentExecutionResult, AgentRuntimeStatus
from app.runtime.factory import set_runtime_override
from app.utils import now_utc
from tests.runtime_mock import MockRuntime


class LayoutRuntime:
    def __init__(self, positions: list[dict] | None = None) -> None:
        self.positions = positions
        self.prompts: list[str] = []
        self.on_ask_user_values: list[object] = []

    async def detect_status(self) -> AgentRuntimeStatus:
        return AgentRuntimeStatus(
            installed=True,
            runnable=True,
            identity="layout-runtime",
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
        on_ask_user=None,
        runtime_tools=None,
        runtime_policy="execute",
        output_schema=None,
    ) -> AgentExecutionResult:
        self.prompts.append(prompt)
        self.on_ask_user_values.append(on_ask_user)
        text = json.dumps({"positions": self.positions or []}, ensure_ascii=False)
        await on_chunk(AgentChunk(type="text", text=text))
        return AgentExecutionResult(session_id=session_id, total_text=text, finished_with="done")


def _configure_codex(client) -> None:
    response = client.put(
        "/api/settings/codex/config",
        json={"content": "\n", "auth_content": "{}\n", "supported_models": ["test-model"]},
    )
    assert response.status_code == 200, response.text


def _create_app(client) -> str:
    response = client.post("/api/apps", json={"name": "Layout Demo"})
    assert response.status_code == 200, response.text
    return response.json()["id"]


def _graph() -> dict:
    return {
        "nodes": [
            {
                "id": "n_input",
                "type": "user_input",
                "position": {"x": 0, "y": 0},
                "title": "输入",
                "input_schema": {"label": "输入", "kind": "text", "required": True},
            },
            {
                "id": "n_a",
                "type": "generate",
                "position": {"x": 100, "y": 0},
                "title": "A",
                "prompt": "生成 A",
            },
            {
                "id": "n_b",
                "type": "generate",
                "position": {"x": 100, "y": 100},
                "title": "B",
                "prompt": "生成 B",
            },
        ],
        "execution_edges": [
            {"id": "e_input_a", "source": "n_input", "target": "n_a"},
            {"id": "e_input_b", "source": "n_input", "target": "n_b"},
        ],
    }


def test_graph_layout_beautify_updates_positions_only(auth_client):
    _configure_codex(auth_client)
    app_id = _create_app(auth_client)
    runtime = LayoutRuntime(
        [
            {"id": "n_input", "x": 80, "y": 200},
            {"id": "n_a", "x": 400, "y": 80},
            {"id": "n_b", "x": 400, "y": 320},
        ]
    )
    set_runtime_override(runtime)
    try:
        response = auth_client.post(
            "/api/graph-layout/beautify",
            json={
                "app_id": app_id,
                "graph": {**_graph(), "_runtime_tools": {"allowed_tool_ids": ["mcp:injected"]}},
                "node_sizes": {"n_a": {"width": 220, "height": 92}},
            },
        )
    finally:
        set_runtime_override(MockRuntime())

    assert response.status_code == 200, response.text
    graph = response.json()["graph"]
    assert [node["position"] for node in graph["nodes"]] == [
        {"x": 80.0, "y": 200.0},
        {"x": 400.0, "y": 80.0},
        {"x": 400.0, "y": 320.0},
    ]
    assert graph["execution_edges"] == _graph()["execution_edges"]
    assert "_runtime_tools" not in graph
    assert graph["nodes"][1]["prompt"] == "生成 A"
    assert runtime.on_ask_user_values == [None]
    assert "节点尺寸 JSON" in runtime.prompts[0]
    assert "_runtime_tools" not in runtime.prompts[0]
    assert '"n_a": {"width": 220.0, "height": 92.0}' in runtime.prompts[0]


def test_graph_layout_rejects_missing_node_position(auth_client):
    _configure_codex(auth_client)
    app_id = _create_app(auth_client)
    runtime = LayoutRuntime(
        [
            {"id": "n_input", "x": 80, "y": 200},
            {"id": "n_a", "x": 400, "y": 80},
        ]
    )
    set_runtime_override(runtime)
    try:
        response = auth_client.post(
            "/api/graph-layout/beautify",
            json={"app_id": app_id, "graph": _graph()},
        )
    finally:
        set_runtime_override(MockRuntime())

    assert response.status_code == 502, response.text
    assert "布局结果缺少节点" in response.json()["detail"]


def test_graph_layout_rejects_unknown_node_position(auth_client):
    _configure_codex(auth_client)
    app_id = _create_app(auth_client)
    runtime = LayoutRuntime(
        [
            {"id": "n_input", "x": 80, "y": 200},
            {"id": "n_a", "x": 400, "y": 80},
            {"id": "n_b", "x": 400, "y": 320},
            {"id": "n_extra", "x": 700, "y": 0},
        ]
    )
    set_runtime_override(runtime)
    try:
        response = auth_client.post(
            "/api/graph-layout/beautify",
            json={"app_id": app_id, "graph": _graph()},
        )
    finally:
        set_runtime_override(MockRuntime())

    assert response.status_code == 502, response.text
    assert "布局结果包含未知节点" in response.json()["detail"]
