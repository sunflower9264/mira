from __future__ import annotations

import asyncio
import codecs
import logging
import os
import re
import shlex
import threading
from collections.abc import Iterable, Iterator
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
    def for_call(cls, *, user_id: str, workspace: Path, home: Path) -> "RuntimePathMap":
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
    provider: str
    command: list[str]
    prompt: str
    env: dict[str, str]
    path_map: RuntimePathMap
    prompt_path: Path


@dataclass(frozen=True)
class DockerSandboxResult:
    return_code: int
    stderr: str = ""


StdoutCallback = Callable[[str], Awaitable[None]]


def iter_utf8_lines(chunks: Iterable[bytes]) -> Iterator[str]:
    """Decode stdout incrementally so a CJK character split across Docker log frames stays intact."""
    decoder = codecs.getincrementaldecoder("utf-8")("replace")
    buffer = ""
    for chunk in chunks:
        if isinstance(chunk, str):
            chunk = chunk.encode("utf-8")
        buffer += decoder.decode(chunk)
        while "\n" in buffer:
            line, buffer = buffer.split("\n", 1)
            yield line
    buffer += decoder.decode(b"", final=True)
    if buffer:
        yield buffer


def _stdout_chunks(stream, cancel_event: asyncio.Event):  # noqa: ANN001
    for chunk in stream:
        if cancel_event.is_set():
            return
        yield chunk


class DockerSandboxRunner:
    def __init__(self, *, client=None) -> None:  # noqa: ANN001 - docker SDK client type is optional at import time
        self._client = client

    async def check_available(self) -> DockerSandboxStatus:
        try:
            await asyncio.to_thread(self._check_available_sync)
        except Exception as exc:  # noqa: BLE001
            return DockerSandboxStatus(ok=False, error=str(exc) or "Docker sandbox 不可用")
        return DockerSandboxStatus(ok=True)

    async def run(
        self,
        spec: DockerSandboxSpec,
        *,
        on_stdout_line: StdoutCallback,
        cancel_event: asyncio.Event,
    ) -> DockerSandboxResult:
        loop = asyncio.get_running_loop()

        def forward_line(line: str) -> None:
            future = asyncio.run_coroutine_threadsafe(on_stdout_line(line), loop)
            future.result()

        return await asyncio.to_thread(self._run_sync, spec, forward_line, cancel_event)

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

    def _run_sync(
        self,
        spec: DockerSandboxSpec,
        on_stdout_line: Callable[[str], None],
        cancel_event: asyncio.Event,
    ) -> DockerSandboxResult:
        client = self._client_or_create()
        settings = get_settings()
        prompt_path = spec.prompt_path
        prompt_path.parent.mkdir(parents=True, exist_ok=True)
        prompt_path.write_text(spec.path_map.host_to_container_text(spec.prompt), encoding="utf-8")

        container_command = _shell_command(spec.command, spec.path_map.host_to_container_path(prompt_path))
        volumes = _volumes(spec.path_map)
        host_config = {
            "mem_limit": settings.runtime_container_memory,
            "pids_limit": settings.runtime_container_pids_limit,
            "cap_drop": ["ALL"],
            "security_opt": ["no-new-privileges:true"],
        }
        if settings.runtime_container_cpus > 0:
            host_config["nano_cpus"] = int(settings.runtime_container_cpus * 1_000_000_000)
        run_kwargs = {
            "image": settings.runtime_sandbox_image,
            "command": container_command,
            "detach": True,
            "init": True,
            "environment": spec.env,
            "working_dir": str(CONTAINER_WORKSPACE),
            "user": _container_user(),
            "volumes": volumes,
            "stdout": True,
            "stderr": True,
            "extra_hosts": {"host.docker.internal": "host-gateway"},
            "labels": {
                "mira.runtime": "agent",
                "mira.provider": spec.provider,
            },
            **host_config,
        }
        if settings.runtime_docker_network.strip():
            run_kwargs["network"] = settings.runtime_docker_network.strip()

        container = None
        watcher_stop = threading.Event()
        try:
            container = client.containers.run(**run_kwargs)
            watcher = threading.Thread(
                target=_watch_cancel,
                args=(container, cancel_event, watcher_stop),
                daemon=True,
            )
            watcher.start()
            stream = container.logs(stream=True, stdout=True, stderr=False, follow=True)
            for line in iter_utf8_lines(_stdout_chunks(stream, cancel_event)):
                on_stdout_line(spec.path_map.container_to_host_text(line))
            if cancel_event.is_set():
                _stop_container(container)
                return DockerSandboxResult(return_code=130, stderr="cancelled")
            wait_result = container.wait(timeout=5)
            status_code = int(wait_result.get("StatusCode") or 0)
            stderr = container.logs(stdout=False, stderr=True).decode("utf-8", errors="ignore")
            return DockerSandboxResult(
                return_code=status_code,
                stderr=spec.path_map.container_to_host_text(stderr),
            )
        except DockerSandboxError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise DockerSandboxError(f"Agent sandbox 执行失败: {exc}") from exc
        finally:
            watcher_stop.set()
            if container is not None:
                try:
                    container.remove(force=True)
                except Exception:  # noqa: BLE001
                    logger.warning("failed to remove runtime sandbox container", exc_info=True)


def _shell_command(command: list[str], prompt_path: Path) -> list[str]:
    return ["/bin/sh", "-c", f"exec {shlex.join(command)} < {shlex.quote(prompt_path.as_posix())}"]


def _container_user() -> str:
    getuid = getattr(os, "getuid", None)
    getgid = getattr(os, "getgid", None)
    if getuid is None or getgid is None:
        return "mira"
    return f"{getuid()}:{getgid()}"


def _volumes(path_map: RuntimePathMap) -> dict[str, dict[str, str]]:
    volumes = {
        str(path_map.workspace_host): {"bind": str(path_map.workspace_container), "mode": "rw"},
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
