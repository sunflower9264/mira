from __future__ import annotations

import asyncio
import os
from types import SimpleNamespace
from unittest.mock import MagicMock

from app.config import get_settings
from app.runtime.sandbox import (
    DockerSandboxReply,
    DockerSandboxRunner,
    DockerSandboxSpec,
    RuntimePathMap,
    _MultiplexedLineDecoder,
)
from app.services.runtime_paths import uploads_dir
from app.services.runtime_uploads import RuntimeUploadRef, runtime_upload_context


class _FakeSocket:
    def __init__(self, payload: bytes = b"") -> None:
        self.buffer = bytearray(payload)
        self.sent = bytearray()

    def recv(self, size: int) -> bytes:
        chunk = bytes(self.buffer[:size])
        del self.buffer[:size]
        return chunk

    def sendall(self, payload: bytes) -> None:
        self.sent.extend(payload)


def _fake_docker_client(payload: bytes = b""):
    attached = MagicMock()
    attached._sock = _FakeSocket(payload)
    container = MagicMock()
    container.attach_socket.return_value = attached
    container.wait.return_value = {"StatusCode": 0}
    containers = MagicMock()
    containers.create.return_value = container
    return SimpleNamespace(containers=containers), container, attached


def test_runtime_path_map_rewrites_workspace_home_and_uploads(tmp_path):
    path_map = RuntimePathMap(
        workspace_host=tmp_path / "workspace",
        home_host=tmp_path / "home",
        uploads_host=tmp_path / "uploads",
    )
    for path in (path_map.workspace_host, path_map.home_host, path_map.uploads_host):
        path.mkdir(parents=True)

    text = f"{path_map.workspace_host}/a.txt {path_map.home_host}/.mira/prompt.txt {path_map.uploads_host}/upl_1/blob"
    container_text = path_map.host_to_container_text(text)

    assert "/workspace/a.txt" in container_text
    assert "/home/mira/.mira/prompt.txt" in container_text
    assert "/mnt/inputs/upl_1/blob" in container_text
    assert str(path_map.workspace_host / "a.txt") in path_map.container_to_host_text(container_text)


def test_runtime_path_map_for_call_does_not_use_user_uploads_root(tmp_path):
    workspace = tmp_path / "workspace"
    home = tmp_path / "home"
    workspace.mkdir()
    home.mkdir()
    user_uploads = uploads_dir("sandbox_user")
    user_uploads.mkdir(parents=True, exist_ok=True)

    path_map = RuntimePathMap.for_call(workspace=workspace, home=home)

    assert path_map.uploads_host is None
    rewritten = path_map.host_to_container_text(str(user_uploads / "upl_secret" / "blob"))
    assert rewritten == str(user_uploads / "upl_secret" / "blob")
    assert "/mnt/inputs" not in rewritten


def test_runtime_path_map_for_call_uses_staged_uploads_only(tmp_path):
    workspace = tmp_path / "workspace"
    home = tmp_path / "home"
    source = tmp_path / "uploads" / "upl_allowed" / "blob"
    workspace.mkdir()
    home.mkdir()
    source.parent.mkdir(parents=True)
    source.write_bytes(b"allowed")

    with runtime_upload_context(workspace, [RuntimeUploadRef(id="upl_allowed", path=source)]) as upload_context:
        path_map = RuntimePathMap.for_call(workspace=workspace, home=home)
        staged_text = upload_context.rewrite_text(str(source))
        container_text = path_map.host_to_container_text(staged_text)

    assert path_map.uploads_host is not None
    assert path_map.uploads_host != source.parent.parent
    assert "/mnt/inputs/upl_allowed/blob" in container_text
    assert str(source) not in container_text


async def test_interactive_runner_applies_container_security_and_cleans_up(tmp_path) -> None:
    payload = b'ready\n'
    frame = b"\x01\x00\x00\x00" + len(payload).to_bytes(4, "big") + payload
    client, container, attached = _fake_docker_client(frame)
    workspace = tmp_path / "workspace"
    home = tmp_path / "home"
    uploads = tmp_path / "uploads"
    for path in (workspace, home, uploads):
        path.mkdir()
    runner = DockerSandboxRunner(client=client)
    lines: list[str] = []

    async def on_line(line: str) -> DockerSandboxReply:
        lines.append(line)
        return DockerSandboxReply(complete=True)

    result = await runner.run_interactive(
        DockerSandboxSpec(
            command=["codex", "app-server"],
            prompt=f"initialize {workspace}/input.txt\n",
            env={"HOME": "/home/mira"},
            path_map=RuntimePathMap(
                workspace_host=workspace,
                home_host=home,
                uploads_host=uploads,
            ),
        ),
        on_stdout_line=on_line,
        cancel_event=asyncio.Event(),
    )

    assert result.return_code == 0
    assert lines == ["ready"]
    kwargs = client.containers.create.call_args.kwargs
    assert kwargs["command"] == ["codex", "app-server"]
    assert kwargs["working_dir"] == "/workspace"
    assert kwargs["user"] == _expected_container_user()
    assert kwargs["init"] is True
    assert kwargs["stdin_open"] is True
    assert kwargs["tty"] is False
    assert kwargs["cap_drop"] == ["ALL"]
    assert kwargs["security_opt"] == ["no-new-privileges:true"]
    assert kwargs["mem_limit"] == get_settings().runtime_container_memory
    assert kwargs["pids_limit"] == get_settings().runtime_container_pids_limit
    if get_settings().runtime_container_cpus > 0:
        assert kwargs["nano_cpus"] == int(get_settings().runtime_container_cpus * 1_000_000_000)
    assert kwargs["extra_hosts"] == {"host.docker.internal": "host-gateway"}
    assert kwargs["volumes"][str(workspace)]["bind"] == "/workspace"
    assert kwargs["volumes"][str(home)]["bind"] == "/home/mira"
    assert kwargs["volumes"][str(uploads)]["mode"] == "ro"
    assert bytes(attached._sock.sent) == b"initialize /workspace/input.txt\n"
    container.start.assert_called_once_with()
    container.kill.assert_called()
    attached.close.assert_called_once_with()
    container.remove.assert_called_once_with(force=True)


async def test_interactive_runner_honors_cancel_event_and_cleans_up(tmp_path) -> None:
    client, container, attached = _fake_docker_client()
    workspace = tmp_path / "workspace"
    home = tmp_path / "home"
    workspace.mkdir()
    home.mkdir()
    cancel_event = asyncio.Event()
    cancel_event.set()
    runner = DockerSandboxRunner(client=client)

    result = await runner.run_interactive(
        DockerSandboxSpec(
            command=["codex", "app-server"],
            prompt="initialize\n",
            env={"HOME": "/home/mira"},
            path_map=RuntimePathMap(workspace_host=workspace, home_host=home),
        ),
        on_stdout_line=lambda _line: None,
        cancel_event=cancel_event,
    )

    assert result.return_code == 130
    container.kill.assert_called()
    attached.close.assert_called_once_with()
    container.remove.assert_called_once_with(force=True)


async def test_interactive_runner_mounts_planning_workspace_read_only(tmp_path) -> None:
    client, _container, _attached = _fake_docker_client()
    workspace = tmp_path / "workspace"
    home = tmp_path / "home"
    workspace.mkdir()
    home.mkdir()
    cancel_event = asyncio.Event()
    cancel_event.set()
    runner = DockerSandboxRunner(client=client)

    await runner.run_interactive(
        DockerSandboxSpec(
            command=["codex", "app-server"],
            prompt="initialize\n",
            env={"HOME": "/home/mira"},
            path_map=RuntimePathMap(workspace_host=workspace, home_host=home),
            workspace_read_only=True,
        ),
        on_stdout_line=lambda _line: None,
        cancel_event=cancel_event,
    )

    volumes = client.containers.create.call_args.kwargs["volumes"]
    assert volumes[str(workspace)] == {"bind": "/workspace", "mode": "ro"}
    assert volumes[str(home)] == {"bind": "/home/mira", "mode": "rw"}


def _expected_container_user() -> str:
    getuid = getattr(os, "getuid", None)
    getgid = getattr(os, "getgid", None)
    if getuid is None or getgid is None:
        return "mira"
    return f"{getuid()}:{getgid()}"


def test_multiplexed_line_decoder_keeps_cjk_character_split_across_chunks() -> None:
    missing = "缺"
    prefix = '{"html":"封面图，'.encode("utf-8")
    suffix = '少详情"}\n'.encode("utf-8")
    encoded = missing.encode("utf-8")
    assert len(encoded) == 3
    decoder = _MultiplexedLineDecoder()
    lines = decoder.feed_stdout(prefix + encoded[:1])
    lines.extend(decoder.feed_stdout(encoded[1:] + suffix))
    assert lines == ['{"html":"封面图，缺少详情"}']
    assert "\ufffd" not in lines[0]
