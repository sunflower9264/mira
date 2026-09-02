from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import secrets
import shutil
import time
from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Awaitable, Callable, Literal

from pydantic import BaseModel, Field
from websockets.asyncio.client import connect

from app.config import get_settings
from app.runtime.base import AgentExecutionResult, DecisionCallback, DecisionResult
from app.runtime.codex_runtime import (
    _clean_env,
    _configured_model,
    _jsonrpc_error,
    _native_answers,
    _normalize_request_user_input,
    _rpc_error_text,
    _thread_id_from_result,
    _thread_request,
    _turn_request,
)
from app.runtime.sandbox import CONTAINER_HOME, CONTAINER_WORKSPACE, _container_user
from app.utils import now_utc

logger = logging.getLogger(__name__)

_CONTAINER_PORT = 8765
_TOKEN_FILENAME = "capability-token"
_LABEL_RUNTIME = "mira.runtime"
_LABEL_WORKSPACE_ID = "mira.workspace_id"
_LABEL_WORKSPACE_PATH = "mira.workspace_path_sha256"
_LABEL_WIKI_PATH = "mira.wiki_path_sha256"
_INITIALIZE_ID = 1
_THREAD_ID = 2
_TURN_ID = 3
_CONTROL_ID = 4

WorkspaceEventCallback = Callable[["WorkspaceRuntimeEvent"], Awaitable[None]]


@dataclass(frozen=True)
class WorkspaceDynamicToolResult:
    success: bool
    text: str


WorkspaceDynamicToolCallback = Callable[[str, str, dict[str, Any]], Awaitable[WorkspaceDynamicToolResult]]


class WorkspaceRuntimeError(RuntimeError):
    """Workspace 常驻 Codex runtime 无法启动、连接或执行。"""


class WorkspaceRuntimeEvent(BaseModel):
    """可安全持久化和下发到 Web 的 Codex 事件。"""

    type: Literal[
        "thread_started",
        "turn_started",
        "message_delta",
        "message_completed",
        "reasoning_delta",
        "activity",
        "process",
        "error",
        "turn_completed",
    ]
    payload: dict[str, Any] = Field(default_factory=dict)


@dataclass(frozen=True)
class WorkspaceRuntimeSpec:
    workspace_id: str
    project_path: Path
    codex_home: Path
    wiki_path: Path | None = None


@dataclass(frozen=True)
class WorkspaceRuntimeHandle:
    workspace_id: str
    container_id: str
    host: str
    port: int
    started_at: datetime


@dataclass(frozen=True)
class WorkspaceTurnRequest:
    prompt: str
    thread_id: str | None = None
    model: str | None = None
    reasoning_effort: str | None = None
    dynamic_tools: list[dict[str, Any]] | None = None


@dataclass(frozen=True)
class WorkspaceRuntimeState:
    status: Literal["running", "stopped", "failed"]
    container_id: str | None = None
    started_at: datetime | None = None
    error: str | None = None


class WorkspaceCodexRuntime:
    """每个 Workspace 一个常驻 App Server，并在该 Workspace 内串行执行 turn。"""

    def __init__(self, *, client=None, connector=connect) -> None:  # noqa: ANN001
        self._client = client
        self._connector = connector
        self._turn_locks: dict[str, asyncio.Lock] = {}
        self._active_turns: dict[str, tuple[str, str]] = {}
        self._process_seen: dict[tuple[str, str], float] = {}

    async def ensure_started(self, spec: WorkspaceRuntimeSpec) -> WorkspaceRuntimeHandle:
        spec.project_path.mkdir(parents=True, exist_ok=True)
        spec.codex_home.mkdir(parents=True, exist_ok=True)
        if spec.wiki_path is not None:
            spec.wiki_path.mkdir(parents=True, exist_ok=True)
        token = _read_or_create_token(_token_path(spec.workspace_id))
        handle = await asyncio.to_thread(self._ensure_started_sync, spec)
        await self._initialize_connection(handle, token)
        return handle

    async def state(self, workspace_id: str) -> WorkspaceRuntimeState:
        try:
            handle = await asyncio.to_thread(self._find_running_sync, workspace_id, None, None)
        except Exception as exc:  # noqa: BLE001
            return WorkspaceRuntimeState(status="failed", error=_safe_error(exc))
        if handle is None:
            return WorkspaceRuntimeState(status="stopped")
        return WorkspaceRuntimeState(
            status="running",
            container_id=handle.container_id,
            started_at=handle.started_at,
        )

    async def execute_turn(
        self,
        spec: WorkspaceRuntimeSpec,
        request: WorkspaceTurnRequest,
        *,
        on_event: WorkspaceEventCallback,
        cancel_event: asyncio.Event,
        on_decision_request: DecisionCallback | None = None,
        on_dynamic_tool_call: WorkspaceDynamicToolCallback | None = None,
    ) -> AgentExecutionResult:
        lock = self._turn_locks.setdefault(spec.workspace_id, asyncio.Lock())
        async with lock:
            return await self._execute_locked(
                spec,
                request,
                on_event=on_event,
                cancel_event=cancel_event,
                on_decision_request=on_decision_request,
                on_dynamic_tool_call=on_dynamic_tool_call,
            )

    async def interrupt(self, spec: WorkspaceRuntimeSpec) -> bool:
        active = self._active_turns.get(spec.workspace_id)
        if active is None:
            return False
        handle = await self.ensure_started(spec)
        token = _read_token(_token_path(spec.workspace_id))
        thread_id, turn_id = active
        await self._rpc(
            handle,
            token,
            "turn/interrupt",
            {"threadId": thread_id, "turnId": turn_id},
        )
        return True

    async def thread_action(
        self,
        spec: WorkspaceRuntimeSpec,
        *,
        action: Literal["archive", "compact", "rename"],
        thread_id: str,
        name: str | None = None,
    ) -> dict[str, Any]:
        if action == "rename":
            normalized = (name or "").strip()
            if not normalized:
                raise WorkspaceRuntimeError("Session 名称不能为空")
            method = "thread/name/set"
            params = {"threadId": thread_id, "name": normalized[:120]}
        elif action == "archive":
            method = "thread/archive"
            params = {"threadId": thread_id}
        else:
            method = "thread/compact/start"
            params = {"threadId": thread_id}
        handle = await self.ensure_started(spec)
        token = _read_token(_token_path(spec.workspace_id))
        return await self._rpc(handle, token, method, params)

    async def delete_thread(self, spec: WorkspaceRuntimeSpec, *, thread_id: str) -> None:
        handle = await self.ensure_started(spec)
        token = _read_token(_token_path(spec.workspace_id))
        await self._rpc(handle, token, "thread/delete", {"threadId": thread_id})

    async def start_review(
        self,
        spec: WorkspaceRuntimeSpec,
        *,
        thread_id: str,
        instructions: str | None = None,
    ) -> dict[str, Any]:
        target: dict[str, str]
        if instructions and instructions.strip():
            target = {"type": "custom", "instructions": instructions.strip()[:4_000]}
        else:
            target = {"type": "uncommittedChanges"}
        handle = await self.ensure_started(spec)
        token = _read_token(_token_path(spec.workspace_id))
        return await self._rpc(
            handle,
            token,
            "review/start",
            {"threadId": thread_id, "target": target, "delivery": "inline"},
        )

    async def get_goal(self, spec: WorkspaceRuntimeSpec, *, thread_id: str) -> dict[str, Any] | None:
        handle = await self.ensure_started(spec)
        token = _read_token(_token_path(spec.workspace_id))
        result = await self._rpc(handle, token, "thread/goal/get", {"threadId": thread_id})
        goal = result.get("goal")
        if not isinstance(goal, dict):
            return None
        return {
            key: goal.get(key)
            for key in (
                "objective",
                "status",
                "tokenBudget",
                "tokensUsed",
                "timeUsedSeconds",
                "createdAt",
                "updatedAt",
            )
        }

    async def set_goal(
        self,
        spec: WorkspaceRuntimeSpec,
        *,
        thread_id: str,
        objective: str | None = None,
        status: str | None = None,
        token_budget: int | None = None,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {"threadId": thread_id}
        if objective is not None:
            params["objective"] = objective.strip()[:4_000]
        if status is not None:
            params["status"] = status
        if token_budget is not None:
            if token_budget <= 0:
                raise WorkspaceRuntimeError("token budget 必须大于 0")
            params["tokenBudget"] = token_budget
        handle = await self.ensure_started(spec)
        token = _read_token(_token_path(spec.workspace_id))
        return await self._rpc(handle, token, "thread/goal/set", params)

    async def clear_goal(self, spec: WorkspaceRuntimeSpec, *, thread_id: str) -> None:
        handle = await self.ensure_started(spec)
        token = _read_token(_token_path(spec.workspace_id))
        await self._rpc(handle, token, "thread/goal/clear", {"threadId": thread_id})

    async def list_processes(
        self,
        spec: WorkspaceRuntimeSpec,
        *,
        thread_id: str,
    ) -> list[dict[str, Any]]:
        handle = await self.ensure_started(spec)
        token = _read_token(_token_path(spec.workspace_id))
        result = await self._rpc(
            handle,
            token,
            "thread/backgroundTerminals/list",
            {"threadId": thread_id, "limit": 100},
        )
        now = time.monotonic()
        sanitized: list[dict[str, Any]] = []
        active_keys: set[tuple[str, str]] = set()
        for item in result.get("data") or []:
            if not isinstance(item, dict) or not isinstance(item.get("processId"), str):
                continue
            process_id = item["processId"]
            key = (spec.workspace_id, process_id)
            active_keys.add(key)
            started = self._process_seen.setdefault(key, now)
            sanitized.append(
                {
                    "process_id": process_id,
                    "status": "running",
                    "duration_ms": max(0, int((now - started) * 1_000)),
                }
            )
        for key in list(self._process_seen):
            if key[0] == spec.workspace_id and key not in active_keys:
                self._process_seen.pop(key, None)
        return sanitized

    async def stop_process(
        self,
        spec: WorkspaceRuntimeSpec,
        *,
        thread_id: str,
        process_id: str,
    ) -> None:
        handle = await self.ensure_started(spec)
        token = _read_token(_token_path(spec.workspace_id))
        await self._rpc(
            handle,
            token,
            "thread/backgroundTerminals/terminate",
            {"threadId": thread_id, "processId": process_id},
        )
        self._process_seen.pop((spec.workspace_id, process_id), None)

    async def clean_processes(self, spec: WorkspaceRuntimeSpec, *, thread_id: str) -> None:
        handle = await self.ensure_started(spec)
        token = _read_token(_token_path(spec.workspace_id))
        await self._rpc(
            handle,
            token,
            "thread/backgroundTerminals/clean",
            {"threadId": thread_id},
        )

    async def stop(self, workspace_id: str) -> None:
        self._active_turns.pop(workspace_id, None)
        for key in [key for key in self._process_seen if key[0] == workspace_id]:
            self._process_seen.pop(key, None)
        await asyncio.to_thread(self._stop_sync, workspace_id)
        shutil.rmtree(_control_dir(workspace_id), ignore_errors=True)

    async def _execute_locked(
        self,
        spec: WorkspaceRuntimeSpec,
        request: WorkspaceTurnRequest,
        *,
        on_event: WorkspaceEventCallback,
        cancel_event: asyncio.Event,
        on_decision_request: DecisionCallback | None,
        on_dynamic_tool_call: WorkspaceDynamicToolCallback | None,
    ) -> AgentExecutionResult:
        handle = await self.ensure_started(spec)
        token = _read_token(_token_path(spec.workspace_id))
        effective_model = (request.model or "").strip() or _configured_model(spec.codex_home)
        thread_request = _thread_request(
            session_id=request.thread_id,
            model=effective_model,
            runtime_policy="execute",
            fork_session=False,
        )
        if request.dynamic_tools:
            thread_request["params"]["dynamicTools"] = request.dynamic_tools
        thread_request["id"] = _THREAD_ID
        chunks: list[str] = []
        final_messages: list[str] = []
        errors: list[str] = []
        active_thread_id = request.thread_id
        active_turn_id: str | None = None
        turn_status: str | None = None
        cancel_sent = False
        uri = f"ws://{handle.host}:{handle.port}"

        try:
            async with self._connector(
                uri,
                additional_headers={"Authorization": f"Bearer {token}"},
                open_timeout=5,
                close_timeout=2,
                max_size=8 * 1024 * 1024,
            ) as websocket:
                await self._initialize(websocket)
                await websocket.send(json.dumps(thread_request, ensure_ascii=False))
                while True:
                    raw = await websocket.recv()
                    message = _json_object(raw)
                    if message is None:
                        continue
                    response_id = message.get("id")
                    is_response = "method" not in message
                    if is_response and response_id == _THREAD_ID:
                        if message.get("error"):
                            raise WorkspaceRuntimeError(_rpc_error_text(message["error"]))
                        active_thread_id = _thread_id_from_result(message.get("result")) or active_thread_id
                        if not active_thread_id:
                            raise WorkspaceRuntimeError("Codex App Server 未返回 thread id")
                        await on_event(
                            WorkspaceRuntimeEvent(
                                type="thread_started", payload={"thread_id": active_thread_id}
                            )
                        )
                        turn_request = _turn_request(
                            thread_id=active_thread_id,
                            prompt=request.prompt,
                            model=effective_model,
                            reasoning_effort=request.reasoning_effort,
                            runtime_policy="execute",
                            output_schema=None,
                        )
                        turn_request["id"] = _TURN_ID
                        await websocket.send(json.dumps(turn_request, ensure_ascii=False))
                        continue
                    if is_response and response_id == _TURN_ID:
                        if message.get("error"):
                            raise WorkspaceRuntimeError(_rpc_error_text(message["error"]))
                        active_turn_id = _turn_id_from_result(message.get("result"))
                        if active_thread_id and active_turn_id:
                            self._active_turns[spec.workspace_id] = (active_thread_id, active_turn_id)
                            await on_event(
                                WorkspaceRuntimeEvent(
                                    type="turn_started",
                                    payload={"thread_id": active_thread_id, "turn_id": active_turn_id},
                                )
                            )
                        if cancel_event.is_set() and active_thread_id and active_turn_id:
                            await websocket.send(
                                _rpc_json(
                                    _CONTROL_ID,
                                    "turn/interrupt",
                                    {"threadId": active_thread_id, "turnId": active_turn_id},
                                )
                            )
                            cancel_sent = True
                        continue

                    method = str(message.get("method") or "")
                    params = message.get("params")
                    if method == "item/tool/requestUserInput":
                        response = await _decision_response(
                            message,
                            on_decision_request,
                            cancel_event,
                        )
                        await websocket.send(response)
                        continue
                    if method == "item/tool/call":
                        response = await _dynamic_tool_response(
                            message,
                            on_dynamic_tool_call,
                            cancel_event,
                        )
                        await websocket.send(response)
                        continue
                    if method == "thread/started" and isinstance(params, dict):
                        active_thread_id = _thread_id_from_result(params) or active_thread_id
                        continue
                    if method == "turn/started" and isinstance(params, dict):
                        active_turn_id = _turn_id_from_result(params)
                        if active_thread_id and active_turn_id:
                            self._active_turns[spec.workspace_id] = (active_thread_id, active_turn_id)
                        continue
                    event = _safe_event(method, params)
                    if event is not None:
                        if event.type == "message_delta":
                            text = event.payload.get("text")
                            if isinstance(text, str):
                                chunks.append(text)
                        elif event.type == "message_completed":
                            text = event.payload.get("text")
                            if isinstance(text, str) and text:
                                final_messages.append(text)
                        elif event.type == "error":
                            text = event.payload.get("message")
                            if isinstance(text, str):
                                errors.append(text)
                        elif event.type == "turn_completed":
                            turn_status = str(event.payload.get("status") or "")
                        await on_event(event)
                    if cancel_event.is_set() and not cancel_sent and active_thread_id and active_turn_id:
                        await websocket.send(
                            _rpc_json(
                                _CONTROL_ID,
                                "turn/interrupt",
                                {"threadId": active_thread_id, "turnId": active_turn_id},
                            )
                        )
                        cancel_sent = True
                    if method == "turn/completed":
                        break
        except asyncio.CancelledError:
            if active_thread_id and active_turn_id:
                with suppress(Exception):
                    await self.interrupt(spec)
            raise
        except Exception as exc:  # noqa: BLE001
            detail = _safe_error(exc)
            logger.warning("Workspace Codex turn failed: workspace=%s error=%s", spec.workspace_id, detail)
            await on_event(WorkspaceRuntimeEvent(type="error", payload={"message": detail}))
            return AgentExecutionResult(
                session_id=active_thread_id,
                total_text=final_messages[-1] if final_messages else "".join(chunks),
                finished_with="error",
                error=detail,
            )
        finally:
            self._active_turns.pop(spec.workspace_id, None)

        total_text = final_messages[-1] if final_messages else "".join(chunks)
        if cancel_event.is_set() or turn_status == "interrupted":
            return AgentExecutionResult(
                session_id=active_thread_id,
                total_text=total_text,
                finished_with="cancelled",
            )
        if errors or turn_status not in {None, "completed"}:
            detail = errors[-1] if errors else f"Codex turn 状态异常：{turn_status or 'unknown'}"
            return AgentExecutionResult(
                session_id=active_thread_id,
                total_text=total_text,
                finished_with="error",
                error=detail,
            )
        return AgentExecutionResult(
            session_id=active_thread_id,
            total_text=total_text,
            finished_with="done",
        )

    async def _initialize_connection(self, handle: WorkspaceRuntimeHandle, token: str) -> None:
        uri = f"ws://{handle.host}:{handle.port}"
        deadline = asyncio.get_running_loop().time() + 8
        last_error: Exception | None = None
        while asyncio.get_running_loop().time() < deadline:
            try:
                async with self._connector(
                    uri,
                    additional_headers={"Authorization": f"Bearer {token}"},
                    open_timeout=2,
                    close_timeout=1,
                ) as websocket:
                    await self._initialize(websocket)
                    return
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                await asyncio.sleep(0.1)
        raise WorkspaceRuntimeError(f"Workspace App Server 启动后无法连接：{_safe_error(last_error)}")

    async def _initialize(self, websocket) -> None:  # noqa: ANN001
        await websocket.send(
            _rpc_json(
                _INITIALIZE_ID,
                "initialize",
                {
                    "clientInfo": {"name": "mira", "title": "Mira", "version": "1"},
                    "capabilities": {"experimentalApi": True},
                },
            )
        )
        while True:
            message = _json_object(await websocket.recv())
            if message is None or message.get("id") != _INITIALIZE_ID or "method" in message:
                continue
            if message.get("error"):
                raise WorkspaceRuntimeError(_rpc_error_text(message["error"]))
            break
        await websocket.send(json.dumps({"method": "initialized", "params": {}}))

    async def _rpc(
        self,
        handle: WorkspaceRuntimeHandle,
        token: str,
        method: str,
        params: dict[str, Any],
    ) -> dict[str, Any]:
        uri = f"ws://{handle.host}:{handle.port}"
        async with self._connector(
            uri,
            additional_headers={"Authorization": f"Bearer {token}"},
            open_timeout=5,
            close_timeout=2,
        ) as websocket:
            await self._initialize(websocket)
            await websocket.send(_rpc_json(_CONTROL_ID, method, params))
            while True:
                message = _json_object(await websocket.recv())
                if message is None or message.get("id") != _CONTROL_ID or "method" in message:
                    continue
                if message.get("error"):
                    raise WorkspaceRuntimeError(_rpc_error_text(message["error"]))
                result = message.get("result")
                return result if isinstance(result, dict) else {}

    def _ensure_started_sync(self, spec: WorkspaceRuntimeSpec) -> WorkspaceRuntimeHandle:
        expected_hash = _path_sha256(spec.project_path)
        expected_wiki_hash = _path_sha256(spec.wiki_path) if spec.wiki_path is not None else ""
        existing = self._find_running_sync(spec.workspace_id, expected_hash, expected_wiki_hash)
        if existing is not None:
            return existing
        client = self._client_or_create()
        self._remove_stale_sync(spec.workspace_id)
        settings = get_settings()
        host_config: dict[str, Any] = {
            "mem_limit": settings.runtime_container_memory,
            "pids_limit": settings.runtime_container_pids_limit,
            "cap_drop": ["ALL"],
            "security_opt": ["no-new-privileges:true"],
        }
        if settings.runtime_container_cpus > 0:
            host_config["nano_cpus"] = int(settings.runtime_container_cpus * 1_000_000_000)
        env = _clean_env(CONTAINER_HOME)
        token_digest = hashlib.sha256(_read_token(_token_path(spec.workspace_id)).encode("utf-8")).hexdigest()
        create_kwargs: dict[str, Any] = {
            "image": settings.runtime_sandbox_image,
            "command": [
                "codex",
                "app-server",
                "--listen",
                f"ws://0.0.0.0:{_CONTAINER_PORT}",
                "--ws-auth",
                "capability-token",
                "--ws-token-sha256",
                token_digest,
            ],
            "detach": True,
            "init": True,
            "environment": env,
            "working_dir": str(CONTAINER_WORKSPACE),
            "user": _container_user(),
            "volumes": {
                str(spec.project_path.resolve()): {"bind": str(CONTAINER_WORKSPACE), "mode": "rw"},
                str(spec.codex_home.resolve()): {"bind": str(CONTAINER_HOME), "mode": "rw"},
            },
            "ports": {f"{_CONTAINER_PORT}/tcp": ("127.0.0.1", None)},
            "extra_hosts": {"host.docker.internal": "host-gateway"},
            "labels": {
                _LABEL_RUNTIME: "workspace",
                _LABEL_WORKSPACE_ID: spec.workspace_id,
                _LABEL_WORKSPACE_PATH: expected_hash,
                _LABEL_WIKI_PATH: expected_wiki_hash,
            },
            **host_config,
        }
        if spec.wiki_path is not None:
            create_kwargs["volumes"][str(spec.wiki_path.resolve())] = {
                "bind": "/mnt/wiki",
                "mode": "rw",
            }
        # 普通 Run 可以继承部署网络；常驻 Workspace 必须固定 bridge，避免 host network
        # 令十个容器争用同一个 8765，同时 published port 只绑定宿主 loopback。
        container = client.containers.create(
            **create_kwargs,
        )
        try:
            container.start()
            container.reload()
            return _handle_from_container(spec.workspace_id, container)
        except Exception:
            with suppress(Exception):
                container.remove(force=True)
            raise

    def _find_running_sync(
        self,
        workspace_id: str,
        expected_path_hash: str | None,
        expected_wiki_hash: str | None,
    ) -> WorkspaceRuntimeHandle | None:
        client = self._client_or_create()
        containers = client.containers.list(
            all=True,
            filters={"label": [f"{_LABEL_RUNTIME}=workspace", f"{_LABEL_WORKSPACE_ID}={workspace_id}"]},
        )
        for container in containers:
            container.reload()
            labels = container.attrs.get("Config", {}).get("Labels", {}) or {}
            if expected_path_hash is not None and labels.get(_LABEL_WORKSPACE_PATH) != expected_path_hash:
                continue
            if expected_wiki_hash is not None and labels.get(_LABEL_WIKI_PATH) != expected_wiki_hash:
                continue
            if container.attrs.get("State", {}).get("Running") is True:
                return _handle_from_container(workspace_id, container)
        return None

    def _remove_stale_sync(self, workspace_id: str) -> None:
        client = self._client_or_create()
        containers = client.containers.list(
            all=True,
            filters={"label": [f"{_LABEL_RUNTIME}=workspace", f"{_LABEL_WORKSPACE_ID}={workspace_id}"]},
        )
        for container in containers:
            container.remove(force=True)

    def _stop_sync(self, workspace_id: str) -> None:
        self._remove_stale_sync(workspace_id)

    def _client_or_create(self):  # noqa: ANN202
        if self._client is not None:
            return self._client
        try:
            import docker
        except ImportError as exc:
            raise WorkspaceRuntimeError("缺少 docker Python SDK，请先安装后端依赖") from exc
        try:
            self._client = docker.from_env()
        except Exception as exc:  # noqa: BLE001
            raise WorkspaceRuntimeError(f"Docker daemon 不可用：{_safe_error(exc)}") from exc
        return self._client


def _handle_from_container(workspace_id: str, container) -> WorkspaceRuntimeHandle:  # noqa: ANN001
    ports = container.attrs.get("NetworkSettings", {}).get("Ports", {}) or {}
    bindings = ports.get(f"{_CONTAINER_PORT}/tcp") or []
    if not bindings:
        raise WorkspaceRuntimeError("Workspace App Server 未绑定本机端口")
    binding = bindings[0]
    host = str(binding.get("HostIp") or "127.0.0.1")
    if host in {"0.0.0.0", "::"}:
        host = "127.0.0.1"
    try:
        port = int(binding["HostPort"])
    except (KeyError, TypeError, ValueError) as exc:
        raise WorkspaceRuntimeError("Workspace App Server 端口无效") from exc
    started_at = _docker_datetime(container.attrs.get("State", {}).get("StartedAt"))
    return WorkspaceRuntimeHandle(
        workspace_id=workspace_id,
        container_id=str(container.id),
        host=host,
        port=port,
        started_at=started_at,
    )


def _read_or_create_token(path: Path) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        return _read_token(path)
    token = secrets.token_urlsafe(48)
    path.write_text(token, encoding="utf-8")
    os.chmod(path, 0o600)
    return token


def _read_token(path: Path) -> str:
    try:
        value = path.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise WorkspaceRuntimeError("Workspace runtime token 不可用") from exc
    if len(value) < 32:
        raise WorkspaceRuntimeError("Workspace runtime token 无效")
    return value


def _control_dir(workspace_id: str) -> Path:
    digest = hashlib.sha256(workspace_id.encode("utf-8")).hexdigest()[:24]
    return get_settings().runtime_dir.resolve() / "workspace-control" / digest


def _token_path(workspace_id: str) -> Path:
    return _control_dir(workspace_id) / _TOKEN_FILENAME


def _path_sha256(path: Path) -> str:
    return hashlib.sha256(str(path.resolve()).encode("utf-8")).hexdigest()


def _docker_datetime(value: Any) -> datetime:
    if isinstance(value, str) and value:
        with suppress(ValueError):
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
    return now_utc()


def _turn_id_from_result(value: Any) -> str | None:
    if not isinstance(value, dict):
        return None
    turn = value.get("turn")
    if isinstance(turn, dict) and isinstance(turn.get("id"), str):
        return turn["id"]
    if isinstance(value.get("turnId"), str):
        return value["turnId"]
    return None


def _json_object(raw: Any) -> dict[str, Any] | None:
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8", errors="replace")
    if not isinstance(raw, str):
        return None
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None


def _rpc_json(request_id: int, method: str, params: dict[str, Any]) -> str:
    return json.dumps({"id": request_id, "method": method, "params": params}, ensure_ascii=False)


async def _decision_response(
    message: dict[str, Any],
    callback: DecisionCallback | None,
    cancel_event: asyncio.Event,
) -> str:
    request_id = message.get("id")
    if callback is None:
        return json.dumps(_jsonrpc_error(request_id, "Mira 当前流程不接受用户提问"), ensure_ascii=False)
    try:
        request = _normalize_request_user_input(message.get("params"))
    except ValueError as exc:
        return json.dumps(_jsonrpc_error(request_id, str(exc)), ensure_ascii=False)
    callback_task = asyncio.create_task(callback(request))
    cancel_task = asyncio.create_task(cancel_event.wait())
    done, _ = await asyncio.wait({callback_task, cancel_task}, return_when=asyncio.FIRST_COMPLETED)
    if cancel_task in done:
        callback_task.cancel()
        with suppress(asyncio.CancelledError):
            await callback_task
        result = DecisionResult(ok=False, error="运行已取消")
    else:
        cancel_task.cancel()
        with suppress(asyncio.CancelledError):
            await cancel_task
        try:
            result = await callback_task
        except Exception:  # noqa: BLE001
            result = WorkspaceDynamicToolResult(False, "工具调用失败")
    if not result.ok:
        return json.dumps(_jsonrpc_error(request_id, result.error or "用户输入未通过校验"), ensure_ascii=False)
    return json.dumps({"id": request_id, "result": _native_answers(request, result)}, ensure_ascii=False)


async def _dynamic_tool_response(
    message: dict[str, Any],
    callback: WorkspaceDynamicToolCallback | None,
    cancel_event: asyncio.Event,
) -> str:
    request_id = message.get("id")
    params = message.get("params")
    if not isinstance(params, dict) or callback is None:
        return json.dumps(_jsonrpc_error(request_id, "Mira 当前未启用该工具"), ensure_ascii=False)
    namespace = params.get("namespace")
    tool = params.get("tool")
    arguments = params.get("arguments")
    if not isinstance(namespace, str) or not isinstance(tool, str) or not isinstance(arguments, dict):
        return json.dumps(_jsonrpc_error(request_id, "工具请求格式无效"), ensure_ascii=False)
    callback_task = asyncio.create_task(callback(namespace, tool, arguments))
    cancel_task = asyncio.create_task(cancel_event.wait())
    done, _ = await asyncio.wait({callback_task, cancel_task}, return_when=asyncio.FIRST_COMPLETED)
    if cancel_task in done:
        callback_task.cancel()
        with suppress(asyncio.CancelledError):
            await callback_task
        result = WorkspaceDynamicToolResult(False, "工作空间运行已取消")
    else:
        cancel_task.cancel()
        with suppress(asyncio.CancelledError):
            await cancel_task
        result = await callback_task
    payload = {
        "success": result.success,
        "contentItems": [{"type": "inputText", "text": result.text}],
    }
    return json.dumps({"id": request_id, "result": payload}, ensure_ascii=False)


def _safe_event(method: str, params: Any) -> WorkspaceRuntimeEvent | None:
    if not isinstance(params, dict):
        return None
    if method == "item/agentMessage/delta":
        text = params.get("delta")
        if isinstance(text, str) and text:
            return WorkspaceRuntimeEvent(type="message_delta", payload={"text": text})
        return None
    if method in {"item/reasoning/summaryTextDelta", "item/reasoning/textDelta"}:
        text = params.get("delta")
        if isinstance(text, str) and text:
            return WorkspaceRuntimeEvent(type="reasoning_delta", payload={"text": text})
        return None
    if method in {"item/started", "item/completed"}:
        item = params.get("item")
        if not isinstance(item, dict):
            return None
        item_type = str(item.get("type") or "unknown")
        if item_type == "agentMessage" and method == "item/completed":
            text = item.get("text")
            if isinstance(text, str) and text:
                return WorkspaceRuntimeEvent(type="message_completed", payload={"text": text})
            return None
        status = str(item.get("status") or ("running" if method == "item/started" else "completed"))
        payload: dict[str, Any] = {
            "item_id": str(item.get("id") or ""),
            "status": status,
        }
        duration = item.get("durationMs")
        if isinstance(duration, int) and duration >= 0:
            payload["duration_ms"] = duration
        if item_type == "commandExecution":
            # shell 命令、cwd、聚合输出与 outputDelta 永不进入事件或日志。
            return WorkspaceRuntimeEvent(type="process", payload=payload)
        payload["kind"] = _public_activity_kind(item_type)
        return WorkspaceRuntimeEvent(type="activity", payload=payload)
    if method in {"item/commandExecution/outputDelta", "item/commandExecution/terminalInteraction"}:
        return None
    if method == "error":
        return WorkspaceRuntimeEvent(type="error", payload={"message": _notification_error(params)})
    if method == "turn/completed":
        turn = params.get("turn")
        if not isinstance(turn, dict):
            return WorkspaceRuntimeEvent(type="turn_completed", payload={"status": "unknown"})
        payload = {"status": str(turn.get("status") or "unknown")}
        error = turn.get("error")
        if error:
            payload["error"] = _rpc_error_text(error)
        return WorkspaceRuntimeEvent(type="turn_completed", payload=payload)
    return None


def _public_activity_kind(item_type: str) -> str:
    return {
        "mcpToolCall": "mcp",
        "dynamicToolCall": "tool",
        "fileChange": "file_change",
        "webSearch": "web_search",
        "plan": "plan",
        "collabAgentToolCall": "agent",
    }.get(item_type, "activity")


def _notification_error(params: dict[str, Any]) -> str:
    value = params.get("error") or params
    return _rpc_error_text(value)


def _safe_error(exc: Any) -> str:
    text = str(exc or "Workspace runtime 执行失败").strip()
    return text[:1_000] or "Workspace runtime 执行失败"


_workspace_runtime = WorkspaceCodexRuntime()


def get_workspace_runtime() -> WorkspaceCodexRuntime:
    return _workspace_runtime


async def stop_workspace_runtime(workspace_id: str) -> None:
    """供 Workspace 删除流程调用；仅清理该 Workspace 的容器和 host-only token。"""

    await _workspace_runtime.stop(workspace_id)
