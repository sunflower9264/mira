from __future__ import annotations

CODEX_REASONING_EFFORTS = ("low", "medium", "high", "xhigh")


def reasoning_efforts() -> tuple[str, ...]:
    return CODEX_REASONING_EFFORTS


def max_reasoning_effort() -> str:
    return CODEX_REASONING_EFFORTS[-1]


def normalize_reasoning_effort(value: object) -> str:
    efforts = CODEX_REASONING_EFFORTS
    effort = value.strip() if isinstance(value, str) else ""
    return effort if effort in efforts else efforts[0]
