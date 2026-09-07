from __future__ import annotations

from typing import Literal

from pydantic import BaseModel


class AiActivityOut(BaseModel):
    id: str
    kind: Literal["app_run", "workspace_turn", "nl_compile", "prompt_assistant", "wiki"]
    status: Literal["queued", "working", "waiting_user"]
    title: str
    phase: str
    started_at: str | None = None
    cancellable: bool
    href: str
