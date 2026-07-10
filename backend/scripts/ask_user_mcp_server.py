#!/usr/bin/env python
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from typing import Any


PROTOCOL_VERSION = "2024-11-05"
TOOL_NAME = "ask_user"


ASK_USER_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["context", "groups"],
    "properties": {
        "context": {
            "type": "object",
            "required": ["title", "summary"],
            "properties": {
                "title": {"type": "string", "minLength": 1, "maxLength": 80},
                "summary": {"type": "string", "minLength": 1, "maxLength": 240},
            },
        },
        "groups": {
            "type": "array",
            "minItems": 1,
            "maxItems": 3,
            "items": {
                "type": "object",
                "required": ["id", "label", "type", "options"],
                "properties": {
                    "id": {"type": "string", "minLength": 1},
                    "label": {"type": "string", "minLength": 1, "maxLength": 200},
                    "type": {"type": "string", "enum": ["single", "multi"]},
                    "options": {
                        "type": "array",
                        "minItems": 2,
                        "maxItems": 3,
                        "description": "Provide 2-3 real choices. Put the recommended option first. Do not include 以上都不是; Mira appends it.",
                        "items": {
                            "type": "object",
                            "required": ["label", "description", "recommended"],
                            "properties": {
                                "label": {"type": "string", "minLength": 1, "maxLength": 80},
                                "description": {"type": "string", "minLength": 1, "maxLength": 200},
                                "recommended": {"type": "boolean"},
                            },
                        },
                    },
                    "placeholder": {"type": "string", "maxLength": 200},
                },
            },
        },
    },
}


def main() -> int:
    while True:
        message = _read_message()
        if message is None:
            return 0
        if not isinstance(message, dict):
            continue
        response = _handle_message(message)
        if response is not None:
            _write_message(response)


def _handle_message(message: dict[str, Any]) -> dict[str, Any] | None:
    msg_id = message.get("id")
    method = message.get("method")
    if msg_id is None:
        return None
    try:
        if method == "initialize":
            result = {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "mira-ask-user", "version": "0.1.0"},
            }
        elif method == "tools/list":
            result = {
                "tools": [
                    {
                        "name": TOOL_NAME,
                        "description": "Ask the final Mira app user one or more single-choice or multi-choice questions, then wait for their answer.",
                        "inputSchema": ASK_USER_SCHEMA,
                    }
                ]
            }
        elif method == "tools/call":
            params = message.get("params") if isinstance(message.get("params"), dict) else {}
            name = params.get("name")
            if name != TOOL_NAME:
                raise ValueError(f"unknown tool: {name}")
            arguments = params.get("arguments")
            if not isinstance(arguments, dict):
                arguments = {}
            result = _call_bridge(arguments)
        else:
            raise ValueError(f"unsupported method: {method}")
        return {"jsonrpc": "2.0", "id": msg_id, "result": result}
    except Exception as exc:  # noqa: BLE001
        return {
            "jsonrpc": "2.0",
            "id": msg_id,
            "error": {"code": -32000, "message": str(exc) or "ask_user MCP error"},
        }


def _call_bridge(arguments: dict[str, Any]) -> dict[str, Any]:
    url = os.environ.get("MIRA_ASK_USER_BRIDGE_URL", "")
    token = os.environ.get("MIRA_ASK_USER_BRIDGE_TOKEN", "")
    if not url or not token:
        return _tool_result("ask_user bridge is not configured", is_error=True)
    raw = json.dumps(arguments, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=raw,
        method="POST",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json; charset=utf-8",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=None) as response:  # noqa: S310 - local bridge URL
            payload = json.loads(response.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="ignore") or str(exc)
        return _tool_result(detail, is_error=True)
    except Exception as exc:  # noqa: BLE001
        return _tool_result(str(exc), is_error=True)
    if not isinstance(payload, dict):
        return _tool_result("ask_user bridge returned invalid payload", is_error=True)
    if payload.get("ok") is False:
        return _tool_result(str(payload.get("error") or "ask_user failed"), is_error=True)
    payload.pop("ok", None)
    payload.pop("error", None)
    return _tool_result(json.dumps(payload, ensure_ascii=False), is_error=False)


def _tool_result(text: str, *, is_error: bool) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": text}], "isError": is_error}


def _read_message() -> dict[str, Any] | None:
    line = sys.stdin.buffer.readline()
    if not line:
        return None
    return json.loads(line.decode("utf-8"))


def _write_message(payload: dict[str, Any]) -> None:
    raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    sys.stdout.buffer.write(raw + b"\n")
    sys.stdout.buffer.flush()


if __name__ == "__main__":
    raise SystemExit(main())
