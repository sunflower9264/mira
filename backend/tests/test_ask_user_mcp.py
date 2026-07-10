from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import urllib.request
from pathlib import Path
from typing import Any

from app.runtime.ask_user_bridge import AskUserBridge
from app.runtime.base import AskUserRequest, AskUserResult
from app.schemas.decision import DecisionAnswer


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "ask_user_mcp_server.py"


def _decision_options(labels: list[str]) -> list[dict[str, Any]]:
    return [
        {
            "label": label,
            "description": f"选择 {label} 会按该方向继续。",
            "recommended": index == 0,
        }
        for index, label in enumerate(labels)
    ]


async def test_ask_user_bridge_forwards_request_and_returns_result():
    seen: list[AskUserRequest] = []

    async def callback(request: AskUserRequest) -> AskUserResult:
        seen.append(request)
        return AskUserResult(
            answers=[DecisionAnswer(group_id="choice", selected=["A"])],
            text="extra",
        )

    async with AskUserBridge(callback) as bridge:
        payload = {
            "context": {"title": "Choose", "summary": "Pick one option so the flow can continue."},
            "groups": [
                {"id": "choice", "label": "Choose", "type": "single", "options": _decision_options(["A", "B", "C"])}
            ]
        }
        request = urllib.request.Request(
            bridge.url,
            data=json.dumps(payload).encode("utf-8"),
            method="POST",
            headers={
                "Authorization": f"Bearer {bridge.token}",
                "Content-Type": "application/json",
            },
        )

        def post() -> dict[str, Any]:
            with urllib.request.urlopen(request, timeout=5) as response:  # noqa: S310 - local bridge
                return json.loads(response.read().decode("utf-8"))

        result = await asyncio.to_thread(post)

    assert seen
    assert seen[0].groups[0].id == "choice"
    assert seen[0].tool_use_id.startswith("toolu_mira_")
    assert result["answers"] == [{"group_id": "choice", "selected": ["A"]}]
    assert result["text"] == "extra"


async def test_ask_user_mcp_server_lists_and_calls_tool():
    async def callback(request: AskUserRequest) -> AskUserResult:
        assert request.groups[0].label == "Choose"
        return AskUserResult(answers=[DecisionAnswer(group_id="choice", selected=["B"])])

    async with AskUserBridge(callback) as bridge:
        env = {
            **os.environ,
            "MIRA_ASK_USER_BRIDGE_URL": bridge.url,
            "MIRA_ASK_USER_BRIDGE_TOKEN": bridge.token,
        }
        process = subprocess.Popen(
            [sys.executable, str(SCRIPT)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
        )
        try:
            listed = await asyncio.to_thread(
                _request,
                process,
                {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
            )
            assert listed["result"]["tools"][0]["name"] == "ask_user"
            schema = listed["result"]["tools"][0]["inputSchema"]
            assert "context" in schema["required"]
            assert schema["properties"]["context"]["required"] == ["title", "summary"]

            called = await asyncio.to_thread(
                _request,
                process,
                {
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "tools/call",
                    "params": {
                        "name": "ask_user",
                        "arguments": {
                            "context": {"title": "Choose", "summary": "Pick one option so the flow can continue."},
                            "groups": [
                                {
                                    "id": "choice",
                                    "label": "Choose",
                                    "type": "single",
                                    "options": _decision_options(["A", "B", "C"]),
                                }
                            ]
                        },
                    },
                },
            )
        finally:
            process.terminate()
            process.wait(timeout=5)

    result = called["result"]
    assert result["isError"] is False
    content = json.loads(result["content"][0]["text"])
    assert content["answers"] == [{"group_id": "choice", "selected": ["B"]}]


def test_ask_user_mcp_server_returns_tool_error_for_invalid_groups():
    process = subprocess.Popen(
        [sys.executable, str(SCRIPT)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env={
            **os.environ,
            "MIRA_ASK_USER_BRIDGE_URL": "http://127.0.0.1:9/ask_user",
            "MIRA_ASK_USER_BRIDGE_TOKEN": "test",
        },
    )
    try:
        called = _request(
            process,
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": "ask_user", "arguments": {"groups": []}},
            },
        )
    finally:
        process.terminate()
        process.wait(timeout=5)

    assert called["result"]["isError"] is True


def _request(process: subprocess.Popen, payload: dict[str, Any]) -> dict[str, Any]:
    assert process.stdin is not None
    assert process.stdout is not None
    raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    process.stdin.write(raw + b"\n")
    process.stdin.flush()
    return _read_message(process.stdout)


def _read_message(stdout) -> dict[str, Any]:  # noqa: ANN001 - subprocess pipe
    return json.loads(stdout.readline().decode("utf-8"))
