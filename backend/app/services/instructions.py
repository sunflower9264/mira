from __future__ import annotations

from pathlib import Path
from typing import Literal

from fastapi import HTTPException

from app.schemas import InstructionFile
from app.services.runtime_paths import claude_home, codex_home, runtime_dir

InstructionProvider = Literal["claude-code", "codex"]


def instruction_path(provider: InstructionProvider) -> Path:
    if provider == "claude-code":
        return claude_home() / "CLAUDE.md"
    if provider == "codex":
        return codex_home() / "AGENTS.md"
    raise HTTPException(status_code=404, detail="未知全局指令")


def ensure_instruction_file(provider: InstructionProvider) -> Path:
    path = instruction_path(provider)
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_text("", encoding="utf-8")
    return path


def ensure_instruction_files() -> None:
    ensure_instruction_file("claude-code")
    ensure_instruction_file("codex")


def read_instruction_file(provider: InstructionProvider) -> InstructionFile:
    path = ensure_instruction_file(provider)
    return InstructionFile(
        provider=provider,
        path=_display_path(path),
        content=path.read_text(encoding="utf-8", errors="ignore"),
    )


def write_instruction_file(provider: InstructionProvider, content: str) -> InstructionFile:
    path = ensure_instruction_file(provider)
    path.write_text(content, encoding="utf-8")
    return read_instruction_file(provider)


def _display_path(path: Path) -> str:
    resolved = path.resolve()
    base = runtime_dir()
    try:
        return resolved.relative_to(base).as_posix()
    except ValueError:
        return resolved.as_posix()
