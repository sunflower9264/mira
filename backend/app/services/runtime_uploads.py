from __future__ import annotations

import os
import shutil
import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Iterator

from app.runtime.base import AskUserAttachment, AskUserResult


@dataclass(frozen=True)
class RuntimeUploadRef:
    id: str
    path: Path
    name: str | None = None


_CURRENT_CONTEXT: ContextVar["RuntimeUploadContext | None"] = ContextVar(
    "runtime_upload_context",
    default=None,
)


class RuntimeUploadContext:
    def __init__(self, workspace: Path) -> None:
        self.workspace = workspace.resolve()
        self.staging_dir = self.workspace / ".inputs" / uuid.uuid4().hex
        self._path_rewrites: dict[str, str] = {}

    def add_refs(self, refs: Iterable[RuntimeUploadRef]) -> None:
        for ref in refs:
            self.stage_file(ref.id, ref.path, name=ref.name)

    def stage_file(self, upload_id: str, source: Path, *, name: str | None = None) -> Path | None:
        try:
            source_path = source.resolve()
        except OSError:
            return None
        if not source_path.is_file():
            return None
        safe_id = _safe_segment(upload_id) or uuid.uuid4().hex
        safe_name = _safe_filename(name) if name else "blob"
        target = self.staging_dir / safe_id / safe_name
        if str(source_path) in self._path_rewrites:
            return target
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.link(source_path, target)
        except OSError:
            shutil.copy2(source_path, target)
        staged = target.resolve()
        runtime_path = (Path("/mnt/inputs") / staged.relative_to(self.staging_dir)).as_posix()
        self._path_rewrites[str(source_path)] = runtime_path
        self._path_rewrites[str(staged)] = runtime_path
        return staged

    def rewrite_text(self, value: str) -> str:
        result = value
        for source, target in sorted(self._path_rewrites.items(), key=lambda item: len(item[0]), reverse=True):
            result = result.replace(source, target)
        return result

    def stage_ask_user_result(self, result: AskUserResult) -> AskUserResult:
        attachments: list[AskUserAttachment] = []
        for attachment in result.attachments:
            path = attachment.path
            staged_path: str | None = None
            if path:
                staged = self.stage_file(attachment.id, Path(path))
                staged_path = str(staged) if staged is not None else None
            attachments.append(attachment.model_copy(update={"path": staged_path}))
        return result.model_copy(update={"attachments": attachments})


@contextmanager
def runtime_upload_context(workspace: Path, refs: Iterable[RuntimeUploadRef] = ()) -> Iterator[RuntimeUploadContext]:
    current = _CURRENT_CONTEXT.get()
    if current is not None:
        current.add_refs(refs)
        yield current
        return
    context = RuntimeUploadContext(workspace)
    context.add_refs(refs)
    token = _CURRENT_CONTEXT.set(context)
    try:
        yield context
    finally:
        _CURRENT_CONTEXT.reset(token)


@contextmanager
def ensure_runtime_upload_context(workspace: Path) -> Iterator[RuntimeUploadContext]:
    current = _CURRENT_CONTEXT.get()
    if current is not None:
        yield current
        return
    with runtime_upload_context(workspace) as context:
        yield context


def current_runtime_upload_context() -> RuntimeUploadContext | None:
    return _CURRENT_CONTEXT.get()


def rewrite_runtime_upload_paths(value: str) -> str:
    context = _CURRENT_CONTEXT.get()
    return context.rewrite_text(value) if context is not None else value


def stage_ask_user_result_for_runtime(context: RuntimeUploadContext, result: AskUserResult) -> AskUserResult:
    return context.stage_ask_user_result(result)


def _safe_segment(value: str) -> str:
    return "".join(char if char.isalnum() or char in {"_", "-"} else "_" for char in value).strip("._")


def _safe_filename(value: str | None) -> str:
    name = Path(value or "blob").name
    safe = "".join(char if char.isalnum() or char in {"_", "-", "."} else "_" for char in name).strip(".")
    return safe or "blob"
