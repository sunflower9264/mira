from __future__ import annotations

import asyncio
import json
import secrets
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from fastapi import HTTPException

from app.config import get_settings
from app.runtime.base import AskUserCallback, AskUserRequest, AskUserResult


_INTERNAL_BRIDGES: dict[str, "InternalAskUserBridge"] = {}
_INTERNAL_BRIDGES_LOCK = threading.Lock()


class AskUserBridge:
    """One-shot local callback server used by the internal ask_user MCP tool."""

    def __init__(self, callback: AskUserCallback):
        self._callback = callback
        self.token = secrets.token_urlsafe(24)
        self._loop: asyncio.AbstractEventLoop | None = None
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    async def __aenter__(self) -> "AskUserBridge":
        self._loop = asyncio.get_running_loop()
        bridge = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:  # noqa: N802 - stdlib callback name
                bridge._handle_post(self)

            def log_message(self, _format: str, *args: Any) -> None:  # noqa: A002
                return

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        return self

    async def __aexit__(self, *_exc: object) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
        if self._thread is not None:
            self._thread.join(timeout=2)

    @property
    def url(self) -> str:
        if self._server is None:
            raise RuntimeError("ask_user bridge is not running")
        host, port = self._server.server_address
        return f"http://{host}:{port}/ask_user"

    def _handle_post(self, handler: BaseHTTPRequestHandler) -> None:
        if handler.headers.get("Authorization") != f"Bearer {self.token}":
            _write_json(handler, 403, {"ok": False, "error": "forbidden"})
            return
        try:
            length = int(handler.headers.get("Content-Length") or "0")
        except ValueError:
            length = 0
        try:
            payload = json.loads(handler.rfile.read(length).decode("utf-8") or "{}")
        except json.JSONDecodeError:
            _write_json(handler, 400, {"ok": False, "error": "invalid json"})
            return
        if not isinstance(payload, dict):
            _write_json(handler, 400, {"ok": False, "error": "invalid payload"})
            return
        payload.setdefault("tool_use_id", f"toolu_mira_{uuid.uuid4().hex[:12]}")
        try:
            request = AskUserRequest.model_validate(payload)
        except Exception as exc:  # noqa: BLE001
            _write_json(handler, 200, AskUserResult(ok=False, error=str(exc)).model_dump(exclude_none=True))
            return
        if self._loop is None:
            _write_json(handler, 503, {"ok": False, "error": "bridge is not ready"})
            return
        future = asyncio.run_coroutine_threadsafe(self._callback(request), self._loop)
        try:
            result = future.result()
        except Exception as exc:  # noqa: BLE001
            _write_json(handler, 200, AskUserResult(ok=False, error=str(exc)).model_dump(exclude_none=True))
            return
        _write_json(handler, 200, result.model_dump(exclude_none=True))


class InternalAskUserBridge:
    """Callback registry bridge reachable from sandbox containers through FastAPI."""

    def __init__(self, callback: AskUserCallback):
        self._callback = callback
        self.session_id = uuid.uuid4().hex
        self.token = secrets.token_urlsafe(24)

    async def __aenter__(self) -> "InternalAskUserBridge":
        with _INTERNAL_BRIDGES_LOCK:
            _INTERNAL_BRIDGES[self.session_id] = self
        return self

    async def __aexit__(self, *_exc: object) -> None:
        with _INTERNAL_BRIDGES_LOCK:
            _INTERNAL_BRIDGES.pop(self.session_id, None)

    @property
    def url(self) -> str:
        base = get_settings().runtime_callback_base_url.rstrip("/")
        return f"{base}/ask-user/{self.session_id}"

    async def handle(self, payload: dict[str, Any]) -> AskUserResult:
        payload.setdefault("tool_use_id", f"toolu_mira_{uuid.uuid4().hex[:12]}")
        try:
            request = AskUserRequest.model_validate(payload)
        except Exception as exc:  # noqa: BLE001
            return AskUserResult(ok=False, error=str(exc))
        try:
            return await self._callback(request)
        except Exception as exc:  # noqa: BLE001
            return AskUserResult(ok=False, error=str(exc))


async def handle_internal_ask_user(session_id: str, authorization: str | None, payload: Any) -> dict[str, Any]:
    with _INTERNAL_BRIDGES_LOCK:
        bridge = _INTERNAL_BRIDGES.get(session_id)
    if bridge is None:
        raise HTTPException(status_code=404, detail="ask_user bridge is not active")
    if authorization != f"Bearer {bridge.token}":
        raise HTTPException(status_code=403, detail="forbidden")
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="invalid payload")
    result = await bridge.handle(payload)
    return result.model_dump(exclude_none=True)
def _write_json(handler: BaseHTTPRequestHandler, status: int, payload: dict[str, Any]) -> None:
    raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(raw)))
    handler.end_headers()
    handler.wfile.write(raw)
