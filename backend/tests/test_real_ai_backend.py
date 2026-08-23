from __future__ import annotations

import json
import os
import shutil
import socket
import sqlite3
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import httpx
import pytest


pytestmark = pytest.mark.skipif(
    os.getenv("MIRA_RUN_REAL_AI_BACKEND_TEST") != "1",
    reason="set MIRA_RUN_REAL_AI_BACKEND_TEST=1 to call the real Codex runtime",
)

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"
REAL_TIMEOUT = float(os.getenv("MIRA_REAL_AI_TIMEOUT", "180"))
REAL_EFFECT_REPEATS = max(1, int(os.getenv("MIRA_REAL_AI_EFFECT_REPEATS", "1")))
REAL_EFFECT_DELAY = max(0.0, float(os.getenv("MIRA_REAL_AI_EFFECT_DELAY", "0")))
REQUIRED_ENV = (
    "ADMIN_USERNAME",
    "ADMIN_PASSWORD",
    "JWT_SECRET",
    "CODEX_CONFIG_SECRET",
    "MIRA_REAL_AI_SOURCE_DB",
)


@pytest.fixture(scope="session")
def real_ai_service(tmp_path_factory: pytest.TempPathFactory):
    test_env = {key: os.getenv(key, "") for key in REQUIRED_ENV}
    missing = [key for key, value in test_env.items() if not value]
    if missing:
        pytest.skip(f"real AI backend test env is missing: {', '.join(missing)}")

    source_db = Path(test_env["MIRA_REAL_AI_SOURCE_DB"])
    if not source_db.exists():
        pytest.skip(f"MIRA_REAL_AI_SOURCE_DB does not exist: {source_db}")

    root = tmp_path_factory.mktemp("real-ai-backend")
    db_path = root / "mira-real-ai.sqlite"
    data_dir = root / "data"
    runtime_dir = root / "runtime"
    shutil.copy2(source_db, db_path)
    _strip_external_tools(db_path)

    port = _free_port()
    env = os.environ.copy()
    env.update(
        {
            "DATABASE_URL": f"sqlite+aiosqlite:///{db_path.as_posix()}",
            "DATA_DIR": str(data_dir),
            "RUNTIME_DIR": str(runtime_dir),
            "PYTHONPATH": str(BACKEND),
        }
    )

    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "app.main:app",
            "--host",
            "0.0.0.0",
            "--port",
            str(port),
        ],
        cwd=BACKEND,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    base_url = f"http://127.0.0.1:{port}"
    try:
        _wait_for_health(base_url, process)
        yield {
            "base_url": base_url,
            "username": test_env["ADMIN_USERNAME"],
            "password": test_env["ADMIN_PASSWORD"],
            "db_path": db_path,
            "data_dir": data_dir,
            "runtime_dir": runtime_dir,
            "env": env,
        }
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=10)


@pytest.fixture()
def real_ai_client(real_ai_service):
    with httpx.Client(base_url=real_ai_service["base_url"], timeout=REAL_TIMEOUT) as client:
        response = client.post(
            "/api/auth/login",
            json={"username": real_ai_service["username"], "password": real_ai_service["password"]},
        )
        response.raise_for_status()
        client.headers.update({"Authorization": f"Bearer {response.json()['token']}"})
        yield client


def test_real_ai_codex_status_smoke(real_ai_client: httpx.Client) -> None:
    response = real_ai_client.post("/api/settings/codex/status")
    response.raise_for_status()
    body = response.json()
    assert body["installed"] is True
    assert body["runnable"] is True, body.get("error")


def test_real_ai_run_user_input_to_output(real_ai_client: httpx.Client) -> None:
    graph = _simple_output_graph(marker="REAL_AI_CODEX_SMOKE")
    _assert_no_node_format_instructions(graph)
    app_id = _create_app(real_ai_client, graph, name="real-ai-codex-smoke")
    run_id = _create_run(real_ai_client, app_id, {"n_input": {"value": "hello"}})
    final = _wait_for_status(real_ai_client, run_id, {"success"})
    by_id = _steps_by_id(final)
    assert by_id["n_out"]["status"] == "success"
    assert "REAL_AI_CODEX_SMOKE" in by_id["n_out"]["output"]


def test_real_ai_run_generate_json_contract(real_ai_client: httpx.Client) -> None:
    graph = {
        "tools": {"disabled_tool_ids": []},
        "nodes": [
            _input_node(),
            {
                "id": "n_gen",
                "type": "generate",
                "position": {"x": 260, "y": 0},
                "title": "JSON",
                "prompt": (
                    "根据上游输入生成一个简短结果摘要，摘要内容必须精确包含 "
                    "REAL_AI_CODEX_JSON。信息已经完整，请直接完成。"
                ),
                "output_contract": {
                    "type": "json",
                    "json_schema": {
                        "type": "object",
                        "additionalProperties": False,
                        "properties": {"summary": {"type": "string"}},
                        "required": ["summary"],
                    },
                },
            },
            _output_node("请把上游结构化结果中的摘要内容渲染成 HTML。", source="n_gen"),
        ],
        "execution_edges": [
            {"id": "e1", "source": "n_input", "target": "n_gen"},
            {"id": "e2", "source": "n_gen", "target": "n_out"},
        ],
    }
    _assert_no_node_format_instructions(graph)
    app_id = _create_app(real_ai_client, graph, name="real-ai-codex-json")
    run_id = _create_run(real_ai_client, app_id, {"n_input": {"value": "json contract input"}})
    final = _wait_for_status(real_ai_client, run_id, {"success"})
    by_id = _steps_by_id(final)
    assert by_id["n_gen"]["output"]["summary"]
    assert "REAL_AI_CODEX_JSON" in by_id["n_gen"]["output"]["summary"]


def test_real_ai_run_artifact_contract_with_codex(real_ai_client: httpx.Client) -> None:
    graph = {
        "tools": {"disabled_tool_ids": []},
        "nodes": [
            _input_node(),
            {
                "id": "n_gen",
                "type": "generate",
                "position": {"x": 260, "y": 0},
                "title": "Artifact",
                "prompt": (
                    "在当前工作目录创建文件 real_ai_artifact.txt，内容为 REAL_AI_ARTIFACT_OK。"
                    "信息已经完整，请直接完成。"
                ),
                "output_contract": {"type": "artifact", "artifact_kind": "file"},
            },
            _output_node("展示上游文件产物名称。", source="n_gen"),
        ],
        "execution_edges": [
            {"id": "e1", "source": "n_input", "target": "n_gen"},
            {"id": "e2", "source": "n_gen", "target": "n_out"},
        ],
    }
    _assert_no_node_format_instructions(graph)
    app_id = _create_app(real_ai_client, graph, name="real-ai-codex-artifact")
    run_id = _create_run(real_ai_client, app_id, {"n_input": {"value": "artifact input"}})
    final = _wait_for_status(real_ai_client, run_id, {"success"})
    by_id = _steps_by_id(final)
    assert by_id["n_gen"]["output"][0]["name"]

    artifacts = real_ai_client.get(f"/api/runs/{run_id}/artifacts")
    artifacts.raise_for_status()
    body = artifacts.json()
    names = {item["name"] for item in body["artifacts"]}
    assert by_id["n_gen"]["output"][0]["name"] in names
    artifact = next(item for item in body["artifacts"] if item["name"] == by_id["n_gen"]["output"][0]["name"])
    downloaded = real_ai_client.get(artifact["download_url"])
    downloaded.raise_for_status()
    assert downloaded.text == "REAL_AI_ARTIFACT_OK"


def test_real_ai_run_plan_with_codex(real_ai_client: httpx.Client) -> None:
    graph = {
        "tools": {"disabled_tool_ids": []},
        "nodes": [
            _input_node(),
            {
                "id": "n_gen",
                "type": "generate",
                "position": {"x": 260, "y": 0},
                "title": "Ask",
                "prompt": (
                    "必须先询问用户更偏好简洁版还是详细版。"
                    "拿到回答后输出包含 REAL_AI_ASK_RESUMED 的一句话。"
                ),
            },
            _output_node("展示上游结果。", source="n_gen"),
        ],
        "execution_edges": [
            {"id": "e1", "source": "n_input", "target": "n_gen"},
            {"id": "e2", "source": "n_gen", "target": "n_out"},
        ],
    }
    app_id = _create_app(real_ai_client, graph, name="real-ai-codex-ask-user")
    run_id = _create_run(real_ai_client, app_id, {"n_input": {"value": "ask user input"}})
    waiting = _wait_for_status(real_ai_client, run_id, {"waiting_for_user"})
    ask = _steps_by_id(waiting)["n_gen"]["input"]["decision_request"]
    selected = ask["groups"][0]["options"][0]["label"]
    response = real_ai_client.post(
        f"/api/runs/{run_id}/resume",
        json={
            "node_id": "n_gen",
            "request_id": ask["request_id"],
            "answers": [{"group_id": ask["groups"][0]["id"], "selected": [selected]}],
        },
    )
    assert response.status_code == 204, response.text
    final = _wait_for_status(real_ai_client, run_id, {"success"})
    assert "REAL_AI_ASK_RESUMED" in _steps_by_id(final)["n_gen"]["output"]


def test_real_ai_prompt_assistant_with_codex(real_ai_client: httpx.Client) -> None:
    graph = _simple_output_graph(marker="REAL_AI_PROMPT_ASSISTANT")
    app_id = _create_app(real_ai_client, graph, name="real-ai-prompt-assistant")
    response = real_ai_client.post(
        "/api/prompt-assistant/generate",
        json={
            "app_id": app_id,
            "graph": graph,
            "node_id": "n_out",
            "user_request": "把当前 output 提示词改成简洁中文 HTML 展示，并保留 REAL_AI_PROMPT_ASSISTANT 标记。",
        },
    )
    response.raise_for_status()
    body = response.json()
    assert body["status"] == "completed"
    assert "REAL_AI_PROMPT_ASSISTANT" in body["prompt"]


def test_real_ai_graph_layout_with_codex(real_ai_client: httpx.Client) -> None:
    graph = _simple_output_graph(marker="REAL_AI_LAYOUT")
    app_id = _create_app(real_ai_client, graph, name="real-ai-layout")
    response = real_ai_client.post(
        "/api/graph-layout/beautify",
        json={"app_id": app_id, "graph": graph, "node_sizes": {}},
    )
    response.raise_for_status()
    next_graph = response.json()["graph"]
    assert {node["id"] for node in next_graph["nodes"]} == {"n_input", "n_out"}
    assert next_graph["execution_edges"] == graph["execution_edges"]
    assert all(isinstance(node.get("position", {}).get("x"), (int, float)) for node in next_graph["nodes"])


def test_real_ai_nlcompile_plan_and_apply_with_codex(real_ai_client: httpx.Client) -> None:
    for attempt in _effect_attempts():
        graph = _simple_output_graph(marker="REAL_AI_NLCOMPILE")
        app_id = _create_app(real_ai_client, graph, name=f"real-ai-nlcompile-{attempt}")
        response = real_ai_client.post(
            "/api/nlcompile",
            json={
                "app_id": app_id,
                "current_graph": graph,
                "instruction": "只把 output 节点标题改为「真实 AI 输出」，不要修改其它节点。",
            },
        )
        response.raise_for_status()
        planned = response.json()
        assert planned["status"] == "planned", planned
        applied = real_ai_client.post(f"/api/nlcompile/{planned['compile_id']}/apply")
        applied.raise_for_status()
        body = applied.json()
        assert body["status"] == "completed"
        assert body["applied_patches"]
        titles = {node["id"]: node.get("title") for node in body["new_graph"]["nodes"]}
        assert titles["n_out"] == "真实 AI 输出"
        assert {node["id"] for node in body["new_graph"]["nodes"]} == {"n_input", "n_out"}


def test_real_ai_nlcompile_builds_complete_graph_without_unneeded_question(
    real_ai_client: httpx.Client,
) -> None:
    for attempt in _effect_attempts():
        graph = {"tools": {"disabled_tool_ids": []}, "nodes": [], "execution_edges": []}
        app_id = _create_app(real_ai_client, graph, name=f"real-ai-nlcompile-empty-{attempt}")
        response = real_ai_client.post(
            "/api/nlcompile",
            json={
                "app_id": app_id,
                "current_graph": graph,
                "instruction": (
                    "从空画布创建一个中文文章摘要应用。运行时由用户输入一段中文文章；"
                    "一个生成节点提取恰好三条要点，每条不超过 40 字；唯一输出节点以标题「摘要结果」"
                    "和有序列表渲染 HTML。不要添加条件分支、素材节点或文件产物。以上业务决策已经完整。"
                ),
            },
        )
        response.raise_for_status()
        planned = response.json()
        assert planned["status"] == "planned", planned
        plan = planned["plan"]
        for key in ("implementation_steps", "graph_changes", "expected_inputs", "expected_outputs", "acceptance_criteria"):
            assert plan[key], (key, plan)

        applied = real_ai_client.post(f"/api/nlcompile/{planned['compile_id']}/apply")
        applied.raise_for_status()
        body = applied.json()
        assert body["status"] == "completed"
        assert body["applied_patches"]
        nodes = body["new_graph"]["nodes"]
        assert [node["type"] for node in nodes].count("user_input") == 1
        assert [node["type"] for node in nodes].count("generate") == 1
        assert [node["type"] for node in nodes].count("output") == 1
        assert all(_valid_position(node.get("position")) for node in nodes)


def test_real_ai_nlcompile_asks_for_unresolved_delivery_choice(real_ai_client: httpx.Client) -> None:
    for attempt in _effect_attempts():
        graph = {"tools": {"disabled_tool_ids": []}, "nodes": [], "execution_edges": []}
        app_id = _create_app(real_ai_client, graph, name=f"real-ai-nlcompile-ambiguous-{attempt}")
        response = real_ai_client.post(
            "/api/nlcompile",
            json={
                "app_id": app_id,
                "current_graph": graph,
                "instruction": (
                    "创建一个内容营销应用，但最终交付形式还没有决定：可能是单篇社媒文案、"
                    "完整营销方案或可下载内容日历。请根据我选择的交付形式搭建不同的工作流。"
                ),
            },
        )
        response.raise_for_status()
        waiting = response.json()
        assert waiting["status"] == "waiting_for_user", waiting
        groups = waiting["request"]["groups"]
        assert 1 <= len(groups) <= 3
        assert all(2 <= len(group["options"]) - 1 <= 3 for group in groups)
        answers = [
            {"group_id": group["id"], "selected": [group["options"][0]["label"]]}
            for group in groups
        ]
        selected = [answer["selected"][0] for answer in answers]
        resumed = real_ai_client.post(
            f"/api/nlcompile/{waiting['compile_id']}/resume",
            json={"answers": answers},
        )
        resumed.raise_for_status()
        planned = resumed.json()
        assert planned["status"] == "planned", planned
        plan_text = json.dumps(planned["plan"], ensure_ascii=False)
        assert any(label in plan_text for label in selected), (selected, planned["plan"])


def test_real_ai_prompt_assistant_preserves_long_prompt_with_codex(real_ai_client: httpx.Client) -> None:
    for attempt in _effect_attempts():
        long_prompt = _long_prompt_fixture()
        graph = {
            "tools": {"disabled_tool_ids": []},
            "nodes": [
                _input_node(),
                {
                    "id": "n_gen",
                    "type": "generate",
                    "position": {"x": 260, "y": 0},
                    "title": "Long Prompt",
                    "prompt": long_prompt,
                    "output_contract": {
                        "type": "json",
                        "json_schema": {
                            "type": "object",
                            "additionalProperties": False,
                            "properties": {"summary": {"type": "string"}},
                            "required": ["summary"],
                        },
                    },
                },
                _output_node("展示上游 summary。", source="n_gen"),
            ],
            "execution_edges": [
                {"id": "e1", "source": "n_input", "target": "n_gen"},
                {"id": "e2", "source": "n_gen", "target": "n_out"},
            ],
        }
        app_id = _create_app(real_ai_client, graph, name=f"real-ai-long-prompt-{attempt}")
        response = real_ai_client.post(
            "/api/prompt-assistant/generate",
            json={
                "app_id": app_id,
                "graph": graph,
                "node_id": "n_gen",
                "user_request": "只把第一句改得更直接；其它段落、标记、示例和输出约束逐字保留。",
            },
        )
        response.raise_for_status()
        body = response.json()
        assert body["status"] == "completed", body
        for marker in ("HEAD_CONSTRAINT_KEEP_EXACTLY", "MIDDLE_CONSTRAINT_KEEP_EXACTLY", "TAIL_CONSTRAINT_KEEP_EXACTLY"):
            assert marker in body["prompt"]
        assert body["output_contract"] in (None, graph["nodes"][1]["output_contract"])


def test_real_ai_condition_uses_branch_labels_and_default(real_ai_client: httpx.Client) -> None:
    for attempt in _effect_attempts():
        graph = {
            "tools": {"disabled_tool_ids": []},
            "nodes": [
                _input_node(),
                {
                    "id": "n_condition",
                    "type": "condition",
                    "position": {"x": 260, "y": 0},
                    "title": "预算判断",
                    "mode": "cases",
                    "prompt": (
                        "根据上游文本判断预算状态。明确低于 1000 元时选择「预算不足」；"
                        "明确达到或超过 1000 元时选择「预算充足」；没有预算信息时选择其它默认分支。"
                        "信息足够时直接判断，不要向用户提问。"
                    ),
                    "branches": [
                        {"key": "route_a", "label": "预算不足"},
                        {"key": "route_b", "label": "预算充足"},
                    ],
                },
                _output_node("展示上游选择结果。", source="n_condition"),
            ],
            "execution_edges": [
                {"id": "e_input", "source": "n_input", "target": "n_condition"},
                {"id": "e_low", "source": "n_condition", "target": "n_out", "branch_key": "route_a"},
                {"id": "e_high", "source": "n_condition", "target": "n_out", "branch_key": "route_b"},
                {"id": "e_default", "source": "n_condition", "target": "n_out", "branch_key": "__default__"},
            ],
        }
        app_id = _create_app(real_ai_client, graph, name=f"real-ai-condition-labels-{attempt}")
        for input_text, expected in (("本次预算为 300 元。", "route_a"), ("没有提供任何预算信息。", "__default__")):
            run_id = _create_run(real_ai_client, app_id, {"n_input": {"value": input_text}})
            final = _wait_for_status(real_ai_client, run_id, {"success"})
            assert _steps_by_id(final)["n_condition"]["output"] == expected, _debug_run(final)


def test_real_ai_output_contract_repair_preserves_facts(real_ai_service) -> None:
    expected = {
        "project": "Orion",
        "count": 17,
        "url": "https://example.com/orion",
        "items": ["alpha", "beta"],
        "owner": None,
    }
    original_output = "```json\n" + json.dumps(expected, ensure_ascii=False) + "\n```\n这是额外解释。"
    for _attempt in _effect_attempts():
        repaired = _run_real_repair_prompt(
            real_ai_service,
            task_context=(
                "把给定事实原样映射到 JSON。不得改变项目名、数量、URL、数组顺序或 owner；"
                "owner 未知时必须保持 null。"
            ),
            original_output=original_output,
        )
        assert repaired == expected


def _strip_external_tools(db_path: Path) -> None:
    with sqlite3.connect(db_path) as conn:
        conn.execute("UPDATE settings SET skills_json = '[]', mcp_servers_json = '[]'")
        conn.commit()


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _effect_attempts():
    for attempt in range(REAL_EFFECT_REPEATS):
        if attempt > 0 and REAL_EFFECT_DELAY:
            time.sleep(REAL_EFFECT_DELAY)
        yield attempt


def _wait_for_health(base_url: str, process: subprocess.Popen[str]) -> None:
    deadline = time.time() + 45
    last_error: Exception | None = None
    while time.time() < deadline:
        if process.poll() is not None:
            output = process.stdout.read() if process.stdout is not None else ""
            raise AssertionError(f"real AI backend exited during startup: {output[-4000:]}")
        try:
            response = httpx.get(f"{base_url}/api/health", timeout=2)
            if response.status_code == 200:
                return
        except Exception as exc:  # noqa: BLE001
            last_error = exc
        time.sleep(0.5)
    raise AssertionError(f"real AI backend did not become healthy: {last_error}")


def _create_app(client: httpx.Client, graph: dict[str, Any], *, name: str) -> str:
    created = client.post("/api/apps", json={"name": name})
    created.raise_for_status()
    app_id = str(created.json()["id"])
    patched = client.patch(f"/api/apps/{app_id}", json={"graph": graph})
    patched.raise_for_status()
    return app_id


def _create_run(client: httpx.Client, app_id: str, inputs: dict[str, Any]) -> str:
    response = client.post("/api/runs", json={"app_id": app_id, "inputs": inputs})
    response.raise_for_status()
    return str(response.json()["run_id"])


def _wait_for_status(client: httpx.Client, run_id: str, statuses: set[str]) -> dict[str, Any]:
    deadline = time.time() + REAL_TIMEOUT
    last: dict[str, Any] | None = None
    while time.time() < deadline:
        response = client.get(f"/api/runs/{run_id}")
        response.raise_for_status()
        last = response.json()
        if last["status"] in statuses:
            return last
        if last["status"] in {"success", "failed", "cancelled"} and last["status"] not in statuses:
            raise AssertionError(_debug_run(last))
        time.sleep(1)
    raise AssertionError(_debug_run(last))


def _steps_by_id(run: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {step["node_id"]: step for step in run.get("steps", [])}


def _input_node() -> dict[str, Any]:
    return {
        "id": "n_input",
        "type": "user_input",
        "position": {"x": 0, "y": 0},
        "title": "Input",
        "input_schema": {"label": "input", "kind": "text", "required": True},
    }


def _output_node(prompt: str, *, source: str = "n_input") -> dict[str, Any]:
    return {
        "id": "n_out",
        "type": "output",
        "position": {"x": 520, "y": 0},
        "title": "Output",
        "prompt": prompt,
    }


def _simple_output_graph(*, marker: str) -> dict[str, Any]:
    return {
        "tools": {"disabled_tool_ids": []},
        "nodes": [
            _input_node(),
            _output_node(f"把主输入渲染为一个极简 HTML 片段，页面中必须出现字符串 {marker}。"),
        ],
        "execution_edges": [{"id": "e1", "source": "n_input", "target": "n_out"}],
    }


def _long_prompt_fixture() -> str:
    filler = "请保留本段既有业务语义和措辞。" * 90
    return "\n\n".join(
        [
            "请根据上游输入生成摘要。HEAD_CONSTRAINT_KEEP_EXACTLY",
            filler,
            "示例字段必须保留：MIDDLE_CONSTRAINT_KEEP_EXACTLY",
            filler,
            "最终 summary 字段约束：TAIL_CONSTRAINT_KEEP_EXACTLY",
        ]
    )


def _valid_position(value: Any) -> bool:
    return (
        isinstance(value, dict)
        and isinstance(value.get("x"), (int, float))
        and not isinstance(value.get("x"), bool)
        and isinstance(value.get("y"), (int, float))
        and not isinstance(value.get("y"), bool)
    )


def _run_real_repair_prompt(
    service: dict[str, Any],
    *,
    task_context: str,
    original_output: str,
) -> dict[str, Any]:
    payload = {"username": service["username"], "task_context": task_context, "original_output": original_output}
    script = r'''
import asyncio
import json
import sys

from sqlalchemy import select

from app.db import SessionLocal
from app.models import User
from app.runtime.factory import get_runtime
from app.services import runtime_config
from app.services.prompts import get_prompt_content, render_prompt
from app.services.runtime_paths import prompt_assistant_workspace
from app.services.structured_output import parse_structured_json_object


SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "project": {"type": "string"},
        "count": {"type": "integer"},
        "url": {"type": "string"},
        "items": {"type": "array", "items": {"type": "string"}},
        "owner": {"type": ["string", "null"]},
    },
    "required": ["project", "count", "url", "items", "owner"],
}


async def main():
    payload = json.loads(sys.stdin.read())
    async with SessionLocal() as db:
        user = (await db.execute(select(User).where(User.username == payload["username"]))).scalar_one()
        await runtime_config.write_configs(db)
        template = await get_prompt_content(db, "output_contract_repair")
    prompt = render_prompt(
        template,
        {
            "task_context": payload["task_context"],
            "contract": json.dumps(SCHEMA, ensure_ascii=False),
            "validation_error": "模型输出包含 Markdown fence 和额外解释，不是单一 JSON 对象",
            "original_output": payload["original_output"],
        },
    )

    async def on_chunk(_chunk):
        return None

    result = await get_runtime().execute(
        prompt=prompt,
        session_id=None,
        model="gpt-5.5",
        reasoning_effort="low",
        cwd=prompt_assistant_workspace(user.id),
        on_chunk=on_chunk,
        cancel_event=asyncio.Event(),
        on_decision_request=None,
        runtime_policy="execute",
        output_schema=SCHEMA,
    )
    if result.finished_with != "done":
        raise RuntimeError(result.error or result.finished_with)
    repaired = parse_structured_json_object(result.total_text, label="repair eval")
    print("MIRA_REPAIR_RESULT=" + json.dumps(repaired, ensure_ascii=False, separators=(",", ":")))


asyncio.run(main())
'''
    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=BACKEND,
        env=service["env"],
        input=json.dumps(payload, ensure_ascii=False),
        capture_output=True,
        text=True,
        timeout=REAL_TIMEOUT,
        check=False,
    )
    if completed.returncode != 0:
        raise AssertionError(
            {
                "returncode": completed.returncode,
                "stdout": completed.stdout[-4000:],
                "stderr": completed.stderr[-4000:],
            }
        )
    prefix = "MIRA_REPAIR_RESULT="
    line = next((item for item in reversed(completed.stdout.splitlines()) if item.startswith(prefix)), None)
    if line is None:
        raise AssertionError({"stdout": completed.stdout[-4000:], "stderr": completed.stderr[-4000:]})
    repaired = json.loads(line[len(prefix) :])
    assert isinstance(repaired, dict)
    return repaired


def _assert_no_node_format_instructions(graph: dict[str, Any]) -> None:
    forbidden = (
        "输出 JSON",
        "结构如下",
        "输出要求",
        "只输出 JSON",
        "Markdown 代码块",
        "顶层字段",
        "必须严格输出 JSON",
        "最终只返回",
        "JSON 对象",
        "可被 JSON 解析",
    )
    hits: list[str] = []
    for node in graph.get("nodes", []):
        prompt = str(node.get("prompt") or "")
        hits.extend(f"{node.get('id')}:{phrase}" for phrase in forbidden if phrase in prompt)
    assert hits == []


def _debug_run(run: dict[str, Any] | None) -> dict[str, Any] | None:
    if run is None:
        return None
    return {
        "id": run.get("id"),
        "status": run.get("status"),
        "steps": [
            {
                "node_id": step.get("node_id"),
                "status": step.get("status"),
                "error": step.get("error"),
                "input": step.get("input"),
                "output": step.get("output"),
            }
            for step in run.get("steps", [])
        ],
    }
