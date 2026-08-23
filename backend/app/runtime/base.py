from __future__ import annotations

import asyncio
from datetime import datetime
from pathlib import Path
from typing import Awaitable, Callable, Literal, Protocol

from pydantic import BaseModel, Field

from app.services.tools import RuntimeToolConfig

from app.schemas.decision import DecisionAnswer, DecisionGroup, DecisionRequestContext


class AgentChunk(BaseModel):
    type: Literal["text", "tool_call", "tool_result", "error", "done", "session"]
    text: str | None = None
    raw: dict | None = None


class AgentExecutionResult(BaseModel):
    session_id: str | None = None
    total_text: str = ""
    finished_with: Literal["done", "error", "cancelled"]
    error: str | None = None


class AgentRuntimeStatus(BaseModel):
    installed: bool
    # runnable=None 表示尚未跑过真实 smoke；True/False 由 Codex status probe 填。
    runnable: bool | None = None
    identity: str | None = None
    method: str | None = None
    error: str | None = None
    checked_at: datetime


# --- decision_request 中段交互协议 ---------------------------------------------------
#
# Codex 在 Plan mode 中通过原生 requestUserInput 发起单选 / 多选问题；runtime
# 归一化后调用 ``on_decision_request``，等待用户 resume，再按原 JSON-RPC request id
# 把结果回填给同一 turn 继续生成。


class DecisionRequest(BaseModel):
    """Codex 原生 requestUserInput 归一化后的 Mira 提问请求。"""

    context: DecisionRequestContext
    groups: list[DecisionGroup]
    request_id: str


class DecisionAttachment(BaseModel):
    """resume 回填原生请求时给 Agent 的附件视图（路径给 runtime 读，URL 给浏览器下载）。"""

    id: str
    name: str
    path: str | None = None
    download_url: str | None = None
    mime: str | None = None
    size: int | None = None


class DecisionResult(BaseModel):
    """用户提交回来的内容；``ok=False`` 时 runtime 返回 JSON-RPC error。"""

    ok: bool = True
    answers: list[DecisionAnswer] = Field(default_factory=list)
    text: str | None = None
    attachments: list[DecisionAttachment] = Field(default_factory=list)
    error: str | None = None


DecisionCallback = Callable[[DecisionRequest], Awaitable[DecisionResult]]
RuntimePolicy = Literal["execute", "plan"]


class AgentRuntime(Protocol):
    async def execute(
        self,
        *,
        prompt: str,
        session_id: str | None,
        model: str | None,
        reasoning_effort: str | None,
        cwd: Path,
        on_chunk: Callable[[AgentChunk], Awaitable[None]],
        cancel_event: asyncio.Event,
        on_decision_request: DecisionCallback | None = None,
        runtime_tools: RuntimeToolConfig | None = None,
        runtime_policy: RuntimePolicy = "execute",
        output_schema: dict | None = None,
        session_scope: str | None = None,
        fork_session: bool = False,
    ) -> AgentExecutionResult: ...

    async def detect_status(self) -> AgentRuntimeStatus: ...
