from __future__ import annotations

import asyncio
from datetime import datetime
from pathlib import Path
from typing import Awaitable, Callable, Literal, Protocol

from pydantic import BaseModel, Field

from app.services.tools import RuntimeToolConfig

from app.schemas.decision import DecisionAnswer, DecisionGroup, DecisionRequestContext


class AgentChunk(BaseModel):
    type: Literal["text", "tool_call", "tool_result", "error", "done"]
    text: str | None = None
    raw: dict | None = None


class AgentExecutionResult(BaseModel):
    session_id: str | None = None
    total_text: str = ""
    finished_with: Literal["done", "error", "cancelled"]
    error: str | None = None


class AgentProviderStatus(BaseModel):
    installed: bool
    # runnable=None 表示尚未跑过真实 smoke；True/False 由 refresh_agent_status 填。
    runnable: bool | None = None
    identity: str | None = None
    method: str | None = None
    error: str | None = None
    checked_at: datetime


# --- ask_user 中段交互协议 ---------------------------------------------------
#
# 见 docs/plans/runtime-waiting-spec.md。Agent 通过约定的 `ask_user` tool_use
# 向后端发起单选 / 多选问题；runtime 在 stream 解析层识别后调用 ``on_ask_user``，
# 等待 orchestrator 拿到用户 resume，最终把结果回填给 LLM 继续生成。


class AskUserRequest(BaseModel):
    """LLM 调用 ask_user 时携带的参数。spec §1.1 中的 input_schema。"""

    context: DecisionRequestContext
    groups: list[DecisionGroup]
    tool_use_id: str


class AskUserAttachment(BaseModel):
    """resume 回填 tool_result 时给 Agent 的附件视图（路径给 runtime 读，URL 给浏览器下载）。"""

    id: str
    name: str
    path: str | None = None
    download_url: str | None = None
    mime: str | None = None
    size: int | None = None


class AskUserResult(BaseModel):
    """用户提交回来的内容。``ok=False`` 表示协议错误，runtime 应把
    ``tool_result.is_error=true`` 与 ``error`` 文本回填给 Agent，让其重试。
    """

    ok: bool = True
    answers: list[DecisionAnswer] = Field(default_factory=list)
    text: str | None = None
    attachments: list[AskUserAttachment] = []
    error: str | None = None


AskUserCallback = Callable[[AskUserRequest], Awaitable[AskUserResult]]
RuntimePolicy = Literal["execute", "ask_user_plan"]


class AgentRuntime(Protocol):
    async def execute(
        self,
        *,
        prompt: str,
        session_id: str | None,
        allowed_tools: list[str] | None,
        model: str | None,
        reasoning_effort: str | None,
        cwd: Path,
        on_chunk: Callable[[AgentChunk], Awaitable[None]],
        cancel_event: asyncio.Event,
        on_ask_user: AskUserCallback | None = None,
        runtime_tools: RuntimeToolConfig | None = None,
        runtime_policy: RuntimePolicy = "execute",
        output_schema: dict | None = None,
        session_scope: str | None = None,
        fork_session: bool = False,
    ) -> AgentExecutionResult: ...

    async def detect_status(self) -> AgentProviderStatus: ...
