from __future__ import annotations

CLAUDE_REASONING_EFFORTS = ("low", "medium", "high", "xhigh", "max")
CODEX_REASONING_EFFORTS = ("low", "medium", "high", "xhigh")


def reasoning_efforts_for_agent(agent: str | None) -> tuple[str, ...]:
    if agent == "claude":
        return CLAUDE_REASONING_EFFORTS
    if agent == "codex":
        return CODEX_REASONING_EFFORTS
    return ()


def default_reasoning_effort_for_agent(agent: str | None) -> str | None:
    efforts = reasoning_efforts_for_agent(agent)
    return efforts[0] if efforts else None


def max_reasoning_effort_for_agent(agent: str | None) -> str | None:
    efforts = reasoning_efforts_for_agent(agent)
    return efforts[-1] if efforts else None


def normalize_reasoning_effort_for_agent(agent: str | None, value: object) -> str | None:
    efforts = reasoning_efforts_for_agent(agent)
    if not efforts:
        return None
    effort = value.strip() if isinstance(value, str) else ""
    return effort if effort in efforts else efforts[0]
