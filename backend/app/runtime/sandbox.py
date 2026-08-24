from __future__ import annotations

import asyncio
import codecs
import logging
import os
import re
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Awaitable, Callable

from app.config import get_settings
from app.services.runtime_uploads import current_runtime_upload_context

logger = logging.getLogger(__name__)

CONTAINER_WORKSPACE = Path("/workspace")
CONTAINER_HOME = Path("/home/mira")
CONTAINER_UPLOADS = Path("/mnt/inputs")


class DockerSandboxError(RuntimeError):
    """Raised when the Docker sandbox cannot start or complete a runtime call."""


@dataclass(frozen=True)
class DockerSandboxStatus:
    ok: bool
    error: str | None = None


@dataclass(frozen=True)
class RuntimePathMap:
    workspace_host: Path
    home_host: Path
    uploads_host: Path | None = None
    workspace_container: Path = CONTAINER_WORKSPACE
    home_container: Path = CONTAINER_HOME
    uploads_container: Path = CONTAINER_UPLOADS

    @classmethod
    def for_call(cls, *, workspace: Path, home: Path) -> "RuntimePathMap":
        upload_context = current_runtime_upload_context()
        return cls(
            workspace_host=workspace.resolve(),
            home_host=home.resolve(),
            uploads_host=upload_context.staging_dir.resolve() if upload_context is not None else None,
        )

    def host_to_container_path(self, value: Path) -> Path:
        path = value.resolve()
        for host_root, container_root in self._host_to_container_pairs():
            try:
                relative = path.relative_to(host_root)
            except ValueError:
                continue
            return container_root / relative
        return path

    def host_to_container_text(self, value: str) -> str:
        return self._replace_text(
            value,
            self._host_to_container_pairs(),
        )

    def container_to_host_text(self, value: str) -> str:
        return self._replace_text(
            value,
            self._container_to_host_pairs(),
        )

    def _host_to_container_pairs(self) -> tuple[tuple[Path, Path], ...]:
        pairs = [
            (self.workspace_host, self.workspace_container),
            (self.home_host, self.home_container),
        ]
        if self.uploads_host is not None:
            pairs.append((self.uploads_host, self.uploads_container))
        return tuple(pairs)

    def _container_to_host_pairs(self) -> tuple[tuple[Path, Path], ...]:
        pairs = [
            (self.workspace_container, self.workspace_host),
            (self.home_container, self.home_host),
        ]
        if self.uploads_host is not None:
            pairs.append((self.uploads_container, self.uploads_host))
        return tuple(pairs)

    @staticmethod
    def _replace_text(value: str, pairs: tuple[tuple[Path, Path], ...]) -> str:
        result = value
        # Longest paths first avoids replacing /workspace before /workspace/foo-like roots
        # if a future map adds nested mounts.
        for source, target in sorted(pairs, key=lambda item: len(item[0].as_posix()), reverse=True):
            source_text = source.as_posix()
            target_text = target.as_posix()
            if source_text in result:
                pattern = rf"(?<![A-Za-z0-9_.-]){re.escape(source_text)}"
                result = re.sub(pattern, target_text, result)
        return result


@dataclass(frozen=True)
class DockerSandboxSpec:
    command: list[str]
    prompt: str
    env: dict[str, str]
    path_map: RuntimePathMap
    workspace_read_only: bool = False


@dataclass(frozen=True)
class DockerSandboxResult:
    return_code: int
    stderr: str = ""


@dataclass(frozen=True)
class DockerSandboxReply:
    input: str | None = None
    complete: bool = False


InteractiveStdoutCallback = Callable[[str], Awaitable[DockerSandboxReply | None]]


class DockerSandboxRunner:
    def __init__(self, *, client=None) -> None:  # noqa: ANN001 - docker SDK client type is optional at import time
        self._client = client

    async def check_available(self) -> DockerSandboxStatus:
        try:
            await asyncio.to_thread(self._check_available_sync)
        except Exception as exc:  # noqa: BLE001
            return DockerSandboxStatus(ok=False, error=str(exc) or "Docker sandbox 不可用")
        return DockerSandboxStatus(ok=True)

    async def run_interactive(
        self,
        spec: DockerSandboxSpec,
        *,
        on_stdout_line: InteractiveStdoutCallback,
        cancel_event: asyncio.Event,
    ) -> DockerSandboxResult:
        loop = asyncio.get_running_loop()

        def forward_line(line: str) -> DockerSandboxReply | None:
            future = asyncio.run_coroutine_threadsafe(on_stdout_line(line), loop)
            return future.result()

        worker = asyncio.create_task(
            asyncio.to_thread(self._run_interactive_sync, spec, forward_line, cancel_event)
        )
        try:
            return await asyncio.shield(worker)
        except asyncio.CancelledError:
            cancel_event.set()
            try:
                await asyncio.shield(worker)
            except Exception:  # noqa: BLE001
                pass
            raise

    def _client_or_create(self):  # noqa: ANN202
        if self._client is not None:
            return self._client
        try:
            import docker
        except ImportError as exc:
            raise DockerSandboxError("缺少 docker Python SDK，请先安装后端依赖") from exc
        try:
            self._client = docker.from_env()
        except Exception as exc:  # noqa: BLE001
            raise DockerSandboxError(f"Docker daemon 不可用: {exc}") from exc
        return self._client

    def _check_available_sync(self) -> None:
        client = self._client_or_create()
        image = get_settings().runtime_sandbox_image
        try:
            client.ping()
        except Exception as exc:  # noqa: BLE001
            raise DockerSandboxError(f"Docker daemon 不可用: {exc}") from exc
        try:
            client.images.get(image)
        except Exception as exc:  # noqa: BLE001
            raise DockerSandboxError(f"未找到 runtime sandbox 镜像 {image}: {exc}") from exc

    def _run_interactive_sync(
        self,
        spec: DockerSandboxSpec,
        on_stdout_line: Callable[[str], DockerSandboxReply | None],
        cancel_event: asyncio.Event,
    ) -> DockerSandboxResult:
        client = self._client_or_create()
        settings = get_settings()
        volumes = _volumes(spec.path_map, workspace_read_only=spec.workspace_read_only)
        host_config = {
            "mem_limit": settings.runtime_container_memory,
            "pids_limit": settings.runtime_container_pids_limit,
            "cap_drop": ["ALL"],
            "security_opt": ["no-new-privileges:true"],
        }
        if settings.runtime_container_cpus > 0:
            host_config["nano_cpus"] = int(settings.runtime_container_cpus * 1_000_000_000)
        create_kwargs = {
            "image": settings.runtime_sandbox_image,
            "command": spec.command,
            "detach": True,
            "init": True,
            "stdin_open": True,
            "tty": False,
            "environment": spec.env,
            "working_dir": str(CONTAINER_WORKSPACE),
            "user": _container_user(),
            "volumes": volumes,
            "extra_hosts": {"host.docker.internal": "host-gateway"},
            "labels": {
                "mira.runtime": "agent",
            },
            **host_config,
        }
        if settings.runtime_docker_network.strip():
            create_kwargs["network"] = settings.runtime_docker_network.strip()

        container = None
        attached = None
        watcher_stop = threading.Event()
        stderr_parts: list[str] = []
        try:
            container = client.containers.create(**create_kwargs)
            attached = container.attach_socket(
                params={"stdin": 1, "stdout": 1, "stderr": 1, "stream": 1}
            )
            container.start()
            watcher = threading.Thread(
                target=_watch_cancel,
                args=(container, cancel_event, watcher_stop),
                daemon=True,
            )
            watcher.start()
            initial = spec.path_map.host_to_container_text(spec.prompt)
            _write_socket(attached, initial)
            decoder = _MultiplexedLineDecoder()
            while not cancel_event.is_set():
                frame = _read_multiplexed_frame(attached)
                if frame is None:
                    break
                stream_type, chunk = frame
                if stream_type == 2:
                    stderr_parts.extend(decoder.feed_stderr(chunk))
                    continue
                if stream_type != 1:
                    continue
                for line in decoder.feed_stdout(chunk):
                    reply = on_stdout_line(line)
                    if reply is None:
                        continue
                    if reply.input:
                        _write_socket(attached, spec.path_map.host_to_container_text(reply.input))
                    if reply.complete:
                        _stop_container(container)
                        return DockerSandboxResult(
                            return_code=0,
                            stderr=spec.path_map.container_to_host_text("\n".join(stderr_parts)),
                        )
            stderr_parts.extend(decoder.finish_stderr())
            if cancel_event.is_set():
                _stop_container(container)
                return DockerSandboxResult(return_code=130, stderr="cancelled")
            wait_result = container.wait(timeout=5)
            return DockerSandboxResult(
                return_code=int(wait_result.get("StatusCode") or 0),
                stderr=spec.path_map.container_to_host_text("\n".join(stderr_parts)),
            )
        except DockerSandboxError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise DockerSandboxError(f"Agent sandbox 交互执行失败: {exc}") from exc
        finally:
            watcher_stop.set()
            if attached is not None:
                try:
                    attached.close()
                except Exception:  # noqa: BLE001
                    pass
            if container is not None:
                try:
                    container.remove(force=True)
                except Exception:  # noqa: BLE001
                    logger.warning("failed to remove runtime sandbox container", exc_info=True)

class _MultiplexedLineDecoder:
    def __init__(self) -> None:
        self._stdout_decoder = codecs.getincrementaldecoder("utf-8")("replace")
        self._stderr_decoder = codecs.getincrementaldecoder("utf-8")("replace")
        self._stdout_buffer = ""
        self._stderr_buffer = ""

    def feed_stdout(self, chunk: bytes) -> list[str]:
        self._stdout_buffer += self._stdout_decoder.decode(chunk)
        lines, self._stdout_buffer = _split_complete_lines(self._stdout_buffer)
        return lines

    def feed_stderr(self, chunk: bytes) -> list[str]:
        self._stderr_buffer += self._stderr_decoder.decode(chunk)
        lines, self._stderr_buffer = _split_complete_lines(self._stderr_buffer)
        return lines

    def finish_stderr(self) -> list[str]:
        self._stderr_buffer += self._stderr_decoder.decode(b"", final=True)
        return [self._stderr_buffer] if self._stderr_buffer else []


def _split_complete_lines(value: str) -> tuple[list[str], str]:
    parts = value.split("\n")
    return parts[:-1], parts[-1]


def _read_multiplexed_frame(attached) -> tuple[int, bytes] | None:  # noqa: ANN001
    header = _read_exactly(attached, 8)
    if header is None:
        return None
    size = int.from_bytes(header[4:8], byteorder="big")
    payload = _read_exactly(attached, size)
    if payload is None:
        raise DockerSandboxError("Agent sandbox 输出流意外中断")
    return header[0], payload


def _read_exactly(attached, size: int) -> bytes | None:  # noqa: ANN001
    chunks: list[bytes] = []
    remaining = size
    while remaining:
        chunk = attached._sock.recv(remaining)  # noqa: SLF001 - docker SDK only exposes the hijacked socket here
        if not chunk:
            if not chunks:
                return None
            raise DockerSandboxError("Agent sandbox 输出流意外中断")
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def _write_socket(attached, value: str) -> None:  # noqa: ANN001
    payload = value.encode("utf-8")
    if payload:
        attached._sock.sendall(payload)  # noqa: SLF001 - docker SDK only exposes the hijacked socket here


def _container_user() -> str:
    getuid = getattr(os, "getuid", None)
    getgid = getattr(os, "getgid", None)
    if getuid is None or getgid is None:
        return "mira"
    return f"{getuid()}:{getgid()}"


def _volumes(
    path_map: RuntimePathMap,
    *,
    workspace_read_only: bool = False,
) -> dict[str, dict[str, str]]:
    volumes = {
        str(path_map.workspace_host): {
            "bind": str(path_map.workspace_container),
            "mode": "ro" if workspace_read_only else "rw",
        },
        str(path_map.home_host): {"bind": str(path_map.home_container), "mode": "rw"},
    }
    if path_map.uploads_host is not None and path_map.uploads_host.exists():
        volumes[str(path_map.uploads_host)] = {"bind": str(path_map.uploads_container), "mode": "ro"}
    return volumes


def _stop_container(container) -> None:  # noqa: ANN001
    try:
        container.kill()
    except Exception:  # noqa: BLE001
        try:
            container.stop(timeout=1)
        except Exception:
            pass


def _watch_cancel(container, cancel_event: asyncio.Event, stop_event: threading.Event) -> None:  # noqa: ANN001
    while not stop_event.wait(0.2):
        if cancel_event.is_set():
            _stop_container(container)
            return
