from __future__ import annotations

import os
import time
from typing import Any

import httpx
import pytest


pytestmark = pytest.mark.skipif(
    os.getenv("MIRA_RUN_REAL_AI_DEPLOY_TEST") != "1",
    reason="set MIRA_RUN_REAL_AI_DEPLOY_TEST=1 to call the deployed real AI runtime",
)


BASE_URL = os.getenv("MIRA_DEPLOY_BASE_URL", "").rstrip("/")
APP_ID = os.getenv("MIRA_REAL_AI_APP_ID")
VAGUE_BOOK_INPUT = os.getenv("MIRA_REAL_AI_BOOK_INPUT", "我不知道看啥，最近想找本书")
TIMEOUT_SECONDS = float(os.getenv("MIRA_REAL_AI_TIMEOUT", "120"))


def test_deployed_book_recommendation_real_ai_enters_ask_user_waiting() -> None:
    """真实部署环境回归测试：模糊书单输入应由真实 Agent 主动 ask_user。

    这个测试故意不使用 MockRuntime，也不依赖 ``[[ask_user:...]]`` 测试标记。
    它通过部署版 HTTP API 创建真实 run，并等待运行进入 ``waiting_for_user``。
    """

    with httpx.Client(base_url=BASE_URL, timeout=30) as client:
        token = _login(client)
        client.headers.update({"Authorization": f"Bearer {token}"})
        app_id = APP_ID or _find_book_recommendation_app_id(client)
        input_node_id = _first_user_input_node_id(client, app_id)
        run_id = _create_run(client, app_id, input_node_id, VAGUE_BOOK_INPUT)
        try:
            body = _wait_for_waiting(client, run_id)
            waiting_step = _waiting_step(body)
            assert waiting_step["input"]["ask_user"]["groups"], _debug_run(body)
        finally:
            client.post(f"/api/runs/{run_id}/cancel")


def _login(client: httpx.Client) -> str:
    username = os.getenv("MIRA_DEPLOY_USERNAME")
    password = os.getenv("MIRA_DEPLOY_PASSWORD")
    if not BASE_URL:
        pytest.skip("set MIRA_DEPLOY_BASE_URL to call the deployed real AI runtime")
    if not username or not password:
        pytest.skip("set MIRA_DEPLOY_USERNAME and MIRA_DEPLOY_PASSWORD")
    response = client.post("/api/auth/login", json={"username": username, "password": password})
    response.raise_for_status()
    return str(response.json()["token"])


def _first_user_input_node_id(client: httpx.Client, app_id: str) -> str:
    response = client.get(f"/api/apps/{app_id}")
    response.raise_for_status()
    graph = response.json()["graph"]
    for node in graph.get("nodes", []):
        if isinstance(node, dict) and node.get("type") == "user_input":
            return str(node["id"])
    raise AssertionError(f"app {app_id} has no user_input node")


def _find_book_recommendation_app_id(client: httpx.Client) -> str:
    candidates: list[dict[str, Any]] = []
    for endpoint in ("/api/apps", "/api/apps?market=true", "/api/apps?gallery=true"):
        response = client.get(endpoint)
        response.raise_for_status()
        payload = response.json()
        if isinstance(payload, list):
            candidates.extend(item for item in payload if isinstance(item, dict))

    for app in candidates:
        if app.get("name") in {"书单推荐（副本）", "书单推荐 Remix"}:
            return str(app["id"])
    for app in candidates:
        if "书单推荐" in str(app.get("name") or ""):
            return str(app["id"])
    raise AssertionError("no deployed book recommendation app found; set MIRA_REAL_AI_APP_ID")


def _create_run(client: httpx.Client, app_id: str, input_node_id: str, value: str) -> str:
    response = client.post(
        "/api/runs",
        json={"app_id": app_id, "inputs": {input_node_id: {"value": value}}},
    )
    response.raise_for_status()
    return str(response.json()["run_id"])


def _wait_for_waiting(client: httpx.Client, run_id: str) -> dict[str, Any]:
    deadline = time.time() + TIMEOUT_SECONDS
    last: dict[str, Any] | None = None
    terminal = {"success", "failed", "cancelled"}
    while time.time() < deadline:
        response = client.get(f"/api/runs/{run_id}")
        response.raise_for_status()
        body = response.json()
        last = body
        if body["status"] == "waiting_for_user":
            return body
        if body["status"] in terminal:
            raise AssertionError(f"run ended before ask_user waiting: {_debug_run(body)}")
        time.sleep(1)
    raise AssertionError(f"run did not enter waiting_for_user within {TIMEOUT_SECONDS}s: {_debug_run(last)}")


def _waiting_step(body: dict[str, Any]) -> dict[str, Any]:
    for step in body.get("steps", []):
        if step.get("status") == "waiting_for_user":
            return step
    raise AssertionError(f"waiting run has no waiting step: {_debug_run(body)}")


def _debug_run(body: dict[str, Any] | None) -> dict[str, Any] | None:
    if body is None:
        return None
    return {
        "id": body.get("id"),
        "status": body.get("status"),
        "steps": [
            {
                "node_id": step.get("node_id"),
                "status": step.get("status"),
                "input": step.get("input"),
                "output": step.get("output"),
                "error": step.get("error"),
            }
            for step in body.get("steps", [])
        ],
    }
