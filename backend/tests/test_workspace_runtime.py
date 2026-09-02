from __future__ import annotations

import asyncio
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

from app.runtime.base import DecisionResult
from app.runtime.workspace_runtime import (
    WorkspaceCodexRuntime,
    WorkspaceRuntimeHandle,
    WorkspaceRuntimeSpec,
    WorkspaceTurnRequest,
    _read_or_create_token,
    _safe_event,
    _token_path,
    WorkspaceDynamicToolResult,
    _dynamic_tool_response,
)
from app.services import workspace_runtime as workspace_service


class _FakeContainer:
    id = "container-1"

    def __init__(self) -> None:
        self.attrs = {
            "Config": {"Labels": {}},
            "State": {"Running": True, "StartedAt": "2026-01-02T03:04:05Z"},
            "NetworkSettings": {
                "Ports": {"8765/tcp": [{"HostIp": "127.0.0.1", "HostPort": "32123"}]}
            },
        }
        self.removed = False

    def start(self) -> None:
        return None

    def reload(self) -> None:
        return None

    def remove(self, *, force: bool) -> None:
        assert force is True
        self.removed = True


class _FakeContainers:
    def __init__(self) -> None:
        self.created: dict | None = None
        self.container = _FakeContainer()

    def list(self, *, all: bool, filters: dict) -> list:  # noqa: A002
        assert all is True
        assert filters["label"]
        return []

    def create(self, **kwargs):
        self.created = kwargs
        self.container.attrs["Config"]["Labels"] = kwargs["labels"]
        return self.container


class _FakeClient:
    def __init__(self) -> None:
        self.containers = _FakeContainers()


class _FakeWebsocket:
    def __init__(self) -> None:
        self.messages: asyncio.Queue[str] = asyncio.Queue()
        self.sent: list[dict] = []

    async def send(self, raw: str) -> None:
        message = json.loads(raw)
        self.sent.append(message)
        method = message.get("method")
        if method == "initialize":
            await self.messages.put(json.dumps({"id": message["id"], "result": {}}))
        elif method == "thread/start":
            await self.messages.put(
                json.dumps({"id": message["id"], "result": {"thread": {"id": "thread-1"}}})
            )
        elif method == "turn/start":
            await self.messages.put(
                json.dumps({"id": message["id"], "result": {"turn": {"id": "turn-1"}}})
            )
            await self.messages.put(
                json.dumps(
                    {
                        "method": "item/started",
                        "params": {
                            "item": {
                                "id": "process-1",
                                "type": "commandExecution",
                                "command": "secret command",
                                "cwd": "/workspace",
                                "status": "inProgress",
                            }
                        },
                    }
                )
            )
            await self.messages.put(
                json.dumps(
                    {
                        "method": "item/completed",
                        "params": {
                            "item": {
                                "id": "process-1",
                                "type": "commandExecution",
                                "command": "secret command",
                                "aggregatedOutput": "secret output",
                                "status": "completed",
                                "durationMs": 42,
                            }
                        },
                    }
                )
            )
            await self.messages.put(
                json.dumps(
                    {"method": "item/agentMessage/delta", "params": {"delta": "hello"}}
                )
            )
            await self.messages.put(
                json.dumps(
                    {
                        "method": "item/completed",
                        "params": {"item": {"type": "agentMessage", "text": "hello"}},
                    }
                )
            )
            await self.messages.put(
                json.dumps(
                    {
                        "method": "turn/completed",
                        "params": {"turn": {"id": "turn-1", "status": "completed"}},
                    }
                )
            )

    async def recv(self) -> str:
        return await self.messages.get()


class _FakeConnection:
    def __init__(self, websocket: _FakeWebsocket) -> None:
        self.websocket = websocket

    async def __aenter__(self) -> _FakeWebsocket:
        return self.websocket

    async def __aexit__(self, exc_type, exc, traceback) -> None:  # noqa: ANN001
        return None


def test_workspace_container_uses_host_only_hashed_token_and_bridge(tmp_path, monkeypatch) -> None:
    settings = SimpleNamespace(
        runtime_dir=tmp_path / "runtime",
        runtime_sandbox_image="mira-agent-runtime:test",
        runtime_container_memory="2g",
        runtime_container_pids_limit=256,
        runtime_container_cpus=2.0,
        runtime_docker_network="host",
    )
    monkeypatch.setattr("app.runtime.workspace_runtime.get_settings", lambda: settings)
    spec = WorkspaceRuntimeSpec(
        workspace_id="workspace-1",
        project_path=tmp_path / "project",
        codex_home=tmp_path / "codex-home",
        wiki_path=tmp_path / "wiki-working-tree",
    )
    spec.project_path.mkdir()
    spec.codex_home.mkdir()
    spec.wiki_path.mkdir()
    token = _read_or_create_token(_token_path(spec.workspace_id))
    client = _FakeClient()
    runtime = WorkspaceCodexRuntime(client=client)

    handle = runtime._ensure_started_sync(spec)

    assert handle.port == 32123
    created = client.containers.created
    assert created is not None
    assert "network" not in created
    assert created["ports"] == {"8765/tcp": ("127.0.0.1", None)}
    assert "--ws-token-sha256" in created["command"]
    assert hashlib.sha256(token.encode()).hexdigest() in created["command"]
    assert token not in json.dumps(created, default=str)
    assert str(_token_path(spec.workspace_id).parent) not in created["volumes"]
    assert created["volumes"][str(spec.wiki_path.resolve())]["bind"] == "/mnt/wiki"


def test_command_events_never_expose_command_or_output() -> None:
    event = _safe_event(
        "item/completed",
        {
            "item": {
                "id": "process-1",
                "type": "commandExecution",
                "command": "print secret",
                "cwd": "/workspace",
                "aggregatedOutput": "secret output",
                "status": "completed",
                "durationMs": 87,
            }
        },
    )

    assert event is not None
    assert event.type == "process"
    assert event.payload == {"item_id": "process-1", "status": "completed", "duration_ms": 87}
    assert _safe_event("item/commandExecution/outputDelta", {"delta": "secret"}) is None


async def test_dynamic_tool_response_returns_safe_content_without_arguments() -> None:
    seen = {}

    async def callback(namespace, tool, arguments):  # noqa: ANN001
        seen.update(namespace=namespace, tool=tool, arguments=arguments)
        return WorkspaceDynamicToolResult(True, '{"ok":true}')

    raw = await _dynamic_tool_response(
        {
            "id": 42,
            "params": {
                "namespace": "mira_workflows",
                "tool": "list",
                "arguments": {"secret": "must-not-be-persisted"},
            },
        },
        callback,
        asyncio.Event(),
    )
    payload = json.loads(raw)
    assert payload["id"] == 42
    assert payload["result"] == {
        "success": True,
        "contentItems": [{"type": "inputText", "text": '{"ok":true}'}],
    }
    assert seen["arguments"]["secret"] == "must-not-be-persisted"


async def test_workspace_turn_streams_sanitized_events_and_reuses_thread(tmp_path, monkeypatch) -> None:
    websocket = _FakeWebsocket()
    runtime = WorkspaceCodexRuntime(connector=lambda *args, **kwargs: _FakeConnection(websocket))
    spec = WorkspaceRuntimeSpec(
        workspace_id="workspace-1",
        project_path=tmp_path / "project",
        codex_home=tmp_path / "codex-home",
    )
    spec.codex_home.mkdir(parents=True)
    (spec.codex_home / "config.toml").write_text('model = "gpt-test"\n', encoding="utf-8")
    settings = SimpleNamespace(runtime_dir=tmp_path / "runtime")
    monkeypatch.setattr("app.runtime.workspace_runtime.get_settings", lambda: settings)
    _read_or_create_token(_token_path(spec.workspace_id))
    handle = WorkspaceRuntimeHandle(
        workspace_id=spec.workspace_id,
        container_id="container-1",
        host="127.0.0.1",
        port=32123,
        started_at=datetime.now(timezone.utc),
    )

    async def ensure_started(_spec):  # noqa: ANN001
        return handle

    monkeypatch.setattr(runtime, "ensure_started", ensure_started)
    events = []

    async def on_event(event):  # noqa: ANN001
        events.append(event)

    result = await runtime.execute_turn(
        spec,
        WorkspaceTurnRequest(
            prompt="hello",
            dynamic_tools=[
                {
                    "type": "namespace",
                    "name": "mira_workflows",
                    "description": "workflows",
                    "tools": [],
                }
            ],
        ),
        on_event=on_event,
        cancel_event=asyncio.Event(),
    )

    assert result.finished_with == "done"
    assert result.session_id == "thread-1"
    assert result.total_text == "hello"
    thread_request = next(message for message in websocket.sent if message.get("method") == "thread/start")
    assert thread_request["params"]["dynamicTools"][0]["name"] == "mira_workflows"
    process_events = [event for event in events if event.type == "process"]
    assert len(process_events) == 2
    serialized = json.dumps([event.model_dump() for event in events])
    assert "secret command" not in serialized
    assert "secret output" not in serialized


async def test_workspace_turns_are_serialized_per_workspace(tmp_path, monkeypatch) -> None:
    runtime = WorkspaceCodexRuntime()
    spec = WorkspaceRuntimeSpec("workspace-1", tmp_path / "project", tmp_path / "home")
    active = 0
    maximum = 0

    async def fake_execute(*args, **kwargs):  # noqa: ANN002, ANN003
        nonlocal active, maximum
        active += 1
        maximum = max(maximum, active)
        await asyncio.sleep(0.01)
        active -= 1
        return "done"

    monkeypatch.setattr(runtime, "_execute_locked", fake_execute)

    async def on_event(event):  # noqa: ANN001
        return None

    await asyncio.gather(
        runtime.execute_turn(
            spec,
            WorkspaceTurnRequest(prompt="one"),
            on_event=on_event,
            cancel_event=asyncio.Event(),
        ),
        runtime.execute_turn(
            spec,
            WorkspaceTurnRequest(prompt="two"),
            on_event=on_event,
            cancel_event=asyncio.Event(),
        ),
    )
    assert maximum == 1


async def test_background_process_list_exposes_only_status_and_duration(tmp_path, monkeypatch) -> None:
    runtime = WorkspaceCodexRuntime()
    spec = WorkspaceRuntimeSpec("workspace-1", tmp_path / "project", tmp_path / "home")
    handle = WorkspaceRuntimeHandle(
        workspace_id=spec.workspace_id,
        container_id="container-1",
        host="127.0.0.1",
        port=32123,
        started_at=datetime.now(timezone.utc),
    )

    async def ensure_started(_spec):  # noqa: ANN001
        return handle

    async def fake_rpc(*args, **kwargs):  # noqa: ANN002, ANN003
        return {
            "data": [
                {
                    "processId": "process-1",
                    "itemId": "item-1",
                    "command": "secret command",
                    "cwd": "/workspace",
                    "osPid": 123,
                }
            ]
        }

    settings = SimpleNamespace(runtime_dir=tmp_path / "runtime")
    monkeypatch.setattr("app.runtime.workspace_runtime.get_settings", lambda: settings)
    _read_or_create_token(_token_path(spec.workspace_id))
    monkeypatch.setattr(runtime, "ensure_started", ensure_started)
    monkeypatch.setattr(runtime, "_rpc", fake_rpc)

    processes = await runtime.list_processes(spec, thread_id="thread-1")

    assert len(processes) == 1
    assert set(processes[0]) == {"process_id", "status", "duration_ms"}
    assert processes[0]["process_id"] == "process-1"
    assert "secret command" not in json.dumps(processes)


def test_git_pull_is_ff_only_and_push_requires_confirmation(tmp_path, monkeypatch) -> None:
    project = tmp_path / "project"
    (project / ".git").mkdir(parents=True)
    seen = []

    def fake_run(command, **kwargs):  # noqa: ANN001
        seen.append((command, kwargs))
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(workspace_service.subprocess, "run", fake_run)
    status, error = workspace_service._run_git_sync(
        project,
        "https://git.example.test/team/private.git",
        "main",
        "top-secret-token",
        "pull",
    )

    assert (status, error) == ("success", None)
    assert seen[0][0] == ["git", "pull", "--ff-only", "origin", "main"]
    assert seen[0][1]["stdout"] is workspace_service.subprocess.DEVNULL
    assert seen[0][1]["stderr"] is workspace_service.subprocess.DEVNULL
    assert "top-secret-token" not in seen[0][0]


def test_workspace_service_error_redacts_host_paths(tmp_path, monkeypatch) -> None:
    settings = SimpleNamespace(runtime_dir=tmp_path / "runtime", data_dir=tmp_path / "data")
    monkeypatch.setattr(workspace_service, "get_settings", lambda: settings)

    message = workspace_service._safe_error(
        RuntimeError(f"failed below {settings.runtime_dir.resolve()}/persistent-workspaces/user")
    )

    assert str(tmp_path) not in message
    assert "[Mira data]" in message


async def test_decision_resume_requires_matching_request_id() -> None:
    future = asyncio.get_running_loop().create_future()
    state = workspace_service._ActiveWorkspaceTurn(
        cancel_event=asyncio.Event(),
        decision_future=future,
        decision_request_id="request-1",
    )
    workspace_service._active_turns["turn-1"] = state
    try:
        assert (
            await workspace_service.resume_workspace_turn(
                "turn-1", "wrong-request", DecisionResult(ok=True)
            )
            is False
        )
        assert future.done() is False
        assert (
            await workspace_service.resume_workspace_turn(
                "turn-1", "request-1", DecisionResult(ok=True)
            )
            is True
        )
        assert future.result().ok is True
    finally:
        workspace_service._active_turns.pop("turn-1", None)
