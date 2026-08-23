from __future__ import annotations

from app.runtime.base import AgentRuntime
from app.runtime.codex_runtime import CodexRuntime

_override_runtime: AgentRuntime | None = None


def set_runtime_override(runtime: AgentRuntime | None) -> None:
    global _override_runtime
    _override_runtime = runtime


def get_runtime(user_id: str) -> AgentRuntime:
    if _override_runtime is not None:
        return _override_runtime
    return CodexRuntime(user_id)
