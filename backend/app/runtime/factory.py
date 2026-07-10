from __future__ import annotations

from app.runtime.base import AgentRuntime
from app.runtime.claude_runtime import ClaudeAgentRuntime
from app.runtime.codex_runtime import CodexCliRuntime

_override_runtime: AgentRuntime | None = None


def set_runtime_override(runtime: AgentRuntime | None) -> None:
    global _override_runtime
    _override_runtime = runtime


def get_runtime(agent: str, user_id: str) -> AgentRuntime:
    if _override_runtime is not None:
        return _override_runtime
    if agent == "codex":
        return CodexCliRuntime(user_id)
    return ClaudeAgentRuntime(user_id)

