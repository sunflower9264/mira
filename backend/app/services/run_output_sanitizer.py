from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from pathlib import PureWindowsPath
from typing import Any

from app.models import Run
from app.services.artifacts import (
    replace_workspace_paths_for_prompt_with_workspace,
    replace_workspace_paths_in_html_with_workspace,
)
from app.services.runtime_paths import data_dir, runtime_dir

_ABSOLUTE_PATH_RE = re.compile(r"/[^\s`\"'<>()\[\]{}]+")
_TRAILING_PUNCTUATION = ".,，。;；:：!！?？"
_REDACTED_LOCAL_PATH = "[local path redacted]"


@dataclass(frozen=True)
class RunSanitizeContext:
    run: Run
    workspace: Path
    workspace_text: str
    local_roots: tuple[str, ...]


def build_run_sanitize_context(run: Run) -> RunSanitizeContext:
    runtime_root = runtime_dir()
    data_root = data_dir()
    workspace = runtime_root / "workspaces" / run.owner_id / run.app_id / run.id
    return RunSanitizeContext(
        run=run,
        workspace=workspace,
        workspace_text=str(workspace),
        local_roots=(
            str(data_root / run.owner_id / "uploads"),
            str(runtime_root / "workspaces" / run.owner_id),
            str(runtime_root / "homes"),
        ),
    )


def sanitize_run_value(value: Any, run: Run | RunSanitizeContext, *, html_mode: bool = False) -> Any:
    context = run if isinstance(run, RunSanitizeContext) else build_run_sanitize_context(run)
    if isinstance(value, str):
        return sanitize_run_text(value, context, html_mode=html_mode)
    if isinstance(value, list):
        return [sanitize_run_value(item, context, html_mode=html_mode) for item in value]
    if isinstance(value, dict):
        return {
            key: None
            if key == "path" and is_owned_local_path(item, context)
            else sanitize_run_value(item, context, html_mode=html_mode)
            for key, item in value.items()
        }
    return value


def sanitize_run_text(text: str, run: Run | RunSanitizeContext, *, html_mode: bool = False) -> str:
    context = run if isinstance(run, RunSanitizeContext) else build_run_sanitize_context(run)
    if html_mode:
        text = replace_workspace_paths_in_html_with_workspace(
            text,
            context.run,
            context.workspace,
            context.workspace_text,
        )
    else:
        text = replace_workspace_paths_for_prompt_with_workspace(
            text,
            context.run,
            context.workspace,
            context.workspace_text,
        )
    return _scrub_local_paths(text, context)


def is_owned_local_path(value: Any, run: Run | RunSanitizeContext) -> bool:
    if not isinstance(value, str) or not value:
        return False
    context = run if isinstance(run, RunSanitizeContext) else build_run_sanitize_context(run)
    return PureWindowsPath(value).is_absolute() or any(
        value.startswith(root) for root in context.local_roots
    )


def _scrub_local_paths(text: str, context: RunSanitizeContext) -> str:
    if not text:
        return text
    roots = context.local_roots
    if not any(root in text for root in roots):
        return text
    matches: dict[str, str] = {}
    for match in _ABSOLUTE_PATH_RE.finditer(text):
        raw = match.group(0)
        stripped = raw.rstrip(_TRAILING_PUNCTUATION)
        if any(stripped.startswith(root) for root in roots):
            matches[raw] = f"{_REDACTED_LOCAL_PATH}{raw[len(stripped):]}"
    result = text
    for raw, replacement in sorted(matches.items(), key=lambda item: len(item[0]), reverse=True):
        result = result.replace(raw, replacement)
    return result
