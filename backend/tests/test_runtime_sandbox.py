from __future__ import annotations

import asyncio
import os
from pathlib import Path

from app.runtime.sandbox import DockerSandboxRunner, DockerSandboxSpec, RuntimePathMap, iter_utf8_lines
from app.services.runtime_paths import uploads_dir
from app.services.runtime_uploads import RuntimeUploadRef, runtime_upload_context


class FakeContainer:
    def __init__(self, chunks: list[bytes]):
        self.chunks = chunks
        self.removed = False
        self.killed = False

    def attach(self, **_kwargs):
        return iter(self.chunks)

    def wait(self, timeout=None):  # noqa: ANN001
        return {"StatusCode": 0}

    def logs(self, stream=False, stdout=True, stderr=True, **_kwargs):  # noqa: ANN001
        if stream and stdout and not stderr:
            return iter(self.chunks)
        return b""

    def remove(self, force=False):  # noqa: ANN001
        self.removed = True

    def kill(self):
        self.killed = True


class FakeContainers:
    def __init__(self):
        self.kwargs = None
        self.container = FakeContainer([b"created /workspace/out.txt\n"])

    def run(self, **kwargs):
        self.kwargs = kwargs
        return self.container


class FakeImages:
    def get(self, _image):
        return object()


class FakeDockerClient:
    def __init__(self):
        self.containers = FakeContainers()
        self.images = FakeImages()

    def ping(self):
        return True


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


async def test_docker_sandbox_runner_mounts_paths_and_rewrites_output(tmp_path):
    client = FakeDockerClient()
    workspace = tmp_path / "workspace"
    home = tmp_path / "home"
    uploads = tmp_path / "uploads"
    for path in (workspace, home, uploads):
        path.mkdir(parents=True)
    path_map = RuntimePathMap(
        workspace_host=workspace,
        home_host=home,
        uploads_host=uploads,
    )
    prompt_path = home / ".mira" / "calls" / "call_test" / "prompt.txt"
    runner = DockerSandboxRunner(client=client)
    lines: list[str] = []

    result = await runner.run(
        DockerSandboxSpec(
            command=["codex", "app-server"],
            prompt=f"use {workspace}/input.txt",
            env={"HOME": "/home/mira"},
            path_map=path_map,
            prompt_path=prompt_path,
        ),
        on_stdout_line=lambda line: _append_line(lines, line),
        cancel_event=asyncio.Event(),
    )

    assert result.return_code == 0
    assert lines == [f"created {workspace}/out.txt"]
    kwargs = client.containers.kwargs
    assert kwargs["working_dir"] == "/workspace"
    assert kwargs["user"] == _expected_container_user()
    assert kwargs["init"] is True
    assert kwargs["command"][:2] == ["/bin/sh", "-c"]
    assert kwargs["extra_hosts"] == {"host.docker.internal": "host-gateway"}
    assert kwargs["volumes"][str(workspace)]["bind"] == "/workspace"
    assert kwargs["volumes"][str(home)]["bind"] == "/home/mira"
    assert kwargs["volumes"][str(uploads)]["mode"] == "ro"
    assert all(volume["bind"] != "/mnt/results" for volume in kwargs["volumes"].values())
    assert client.containers.container.removed is True
    assert "/home/mira/.mira/calls/call_test/prompt.txt" in " ".join(kwargs["command"])
    assert prompt_path.read_text(encoding="utf-8") == "use /workspace/input.txt"


async def _append_line(lines: list[str], line: str) -> None:
    lines.append(line)


def _expected_container_user() -> str:
    getuid = getattr(os, "getuid", None)
    getgid = getattr(os, "getgid", None)
    if getuid is None or getgid is None:
        return "mira"
    return f"{getuid()}:{getgid()}"


def test_iter_utf8_lines_keeps_cjk_character_split_across_chunks() -> None:
    missing = "缺"
    prefix = '{"html":"封面图，'.encode("utf-8")
    suffix = '少详情"}\n'.encode("utf-8")
    encoded = missing.encode("utf-8")
    assert len(encoded) == 3
    lines = list(iter_utf8_lines([prefix + encoded[:1], encoded[1:] + suffix]))
    assert lines == ['{"html":"封面图，缺少详情"}']
    assert "\ufffd" not in lines[0]
