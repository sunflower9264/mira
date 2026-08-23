from __future__ import annotations

from pathlib import Path
from app.schemas import InstructionFile
from app.services.runtime_paths import codex_home, runtime_dir


def instruction_path() -> Path:
    return codex_home() / "AGENTS.md"


def ensure_instruction_file() -> Path:
    path = instruction_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_text("", encoding="utf-8")
    return path


def read_instruction_file() -> InstructionFile:
    path = ensure_instruction_file()
    return InstructionFile(
        path=_display_path(path),
        content=path.read_text(encoding="utf-8", errors="ignore"),
    )


def write_instruction_file(content: str) -> InstructionFile:
    path = ensure_instruction_file()
    path.write_text(content, encoding="utf-8")
    return read_instruction_file()


def _display_path(path: Path) -> str:
    resolved = path.resolve()
    base = runtime_dir()
    try:
        return resolved.relative_to(base).as_posix()
    except ValueError:
        return resolved.as_posix()
