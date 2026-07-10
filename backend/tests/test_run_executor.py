from __future__ import annotations

import asyncio
from copy import deepcopy
import io
import json
import threading
import time
import zipfile
from pathlib import Path
from typing import Any

from sqlalchemy import select

from app.db import SessionLocal
from app.models import Run, Step
from app.services.run_orchestrator import start_run
from app.services.runtime_paths import run_workspace, uploads_dir
from app.runtime.base import AgentChunk, AgentExecutionResult, AgentProviderStatus, AskUserRequest
from app.runtime.factory import set_runtime_override
from app.schemas.decision import DecisionGroup
from app.services.node_handlers import _ASK_USER_PREFLIGHT_OUTPUT_SCHEMA
from app.utils import dumps, loads, now_utc
from tests.runtime_mock import MockRuntime


class ReasoningCaptureRuntime:
    def __init__(self) -> None:
        self.calls: list[dict[str, str | list[str] | None]] = []

    async def detect_status(self) -> AgentProviderStatus:
        return AgentProviderStatus(
            installed=True,
            runnable=True,
            identity="reasoning-capture",
            method="test",
            checked_at=now_utc(),
        )

    async def execute(
        self,
        *,
        prompt: str,
        session_id: str | None,
        allowed_tools: list[str] | None,
        model: str | None,
        reasoning_effort: str | None,
        cwd: Path,
        on_chunk,
        cancel_event: asyncio.Event,
        on_ask_user=None,
        runtime_tools=None,
        runtime_policy="execute",
        output_schema=None,
    ) -> AgentExecutionResult:
        self.calls.append(
            {
                "model": model,
                "reasoning_effort": reasoning_effort,
                "allowed_tools": allowed_tools,
                "runtime_tools": runtime_tools,
                "runtime_policy": runtime_policy,
            }
        )
        if runtime_policy == "ask_user_plan":
            text = json.dumps(
                {
                    "action": "complete",
                    "decision_summary": "无需额外提问。",
                    "reason": "测试场景不需要补充用户决策。",
                },
                ensure_ascii=False,
            )
        elif "你正在生成 Mira output 节点" in prompt:
            text = _structured_text("<section>OK</section>", output_schema)
        else:
            text = "OK"
        await on_chunk(AgentChunk(type="text", text=text))
        return AgentExecutionResult(
            session_id=session_id or "reasoning_session",
            total_text=text,
            finished_with="done",
        )


class LateSuccessAfterCancelRuntime:
    def __init__(self) -> None:
        self.started = threading.Event()
        self.cancel_seen = threading.Event()
        self.returned = threading.Event()

    async def detect_status(self) -> AgentProviderStatus:
        return AgentProviderStatus(
            installed=True,
            runnable=True,
            identity="late-success-after-cancel",
            method="test",
            checked_at=now_utc(),
        )

    async def execute(
        self,
        *,
        prompt: str,
        session_id: str | None,
        allowed_tools: list[str] | None,
        model: str | None,
        reasoning_effort: str | None,
        cwd: Path,
        on_chunk,
        cancel_event: asyncio.Event,
        on_ask_user=None,
        runtime_tools=None,
        runtime_policy="execute",
        output_schema=None,
    ) -> AgentExecutionResult:
        if runtime_policy == "ask_user_plan":
            text = '{"action":"complete","decision_summary":"无需额外提问。","reason":"测试场景不需要补充用户决策。"}'
            await on_chunk(AgentChunk(type="text", text=text))
            return AgentExecutionResult(session_id=session_id, total_text=text, finished_with="done")
        self.started.set()
        deadline = time.time() + 2.0
        while not cancel_event.is_set() and time.time() < deadline:
            await asyncio.sleep(0.01)
        if cancel_event.is_set():
            self.cancel_seen.set()
        text = "LATE_SUCCESS"
        await on_chunk(AgentChunk(type="text", text=text))
        self.returned.set()
        return AgentExecutionResult(
            session_id=session_id or "late_success_session",
            total_text=text,
            finished_with="done",
        )


class WorkspacePathRuntime:
    async def detect_status(self) -> AgentProviderStatus:
        return AgentProviderStatus(
            installed=True,
            runnable=True,
            identity="workspace-path",
            method="test",
            checked_at=now_utc(),
        )

    async def execute(
        self,
        *,
        prompt: str,
        session_id: str | None,
        allowed_tools: list[str] | None,
        model: str | None,
        reasoning_effort: str | None,
        cwd: Path,
        on_chunk,
        cancel_event: asyncio.Event,
        on_ask_user=None,
        runtime_tools=None,
        runtime_policy="execute",
        output_schema=None,
    ) -> AgentExecutionResult:
        if runtime_policy == "ask_user_plan":
            text = '{"action":"complete","decision_summary":"无需额外提问。","reason":"测试场景不需要补充用户决策。"}'
            await on_chunk(AgentChunk(type="text", text=text))
            return AgentExecutionResult(session_id=session_id, total_text=text, finished_with="done")
        artifact = cwd / "deliverable.zip"
        artifact.write_bytes(b"artifact")
        text = f'<section>文件：{artifact}</section>' if "展示" in prompt else str(artifact)
        text = _structured_text(text, output_schema)
        await on_chunk(AgentChunk(type="text", text=text))
        return AgentExecutionResult(
            session_id=session_id or "workspace_path_session",
            total_text=text,
            finished_with="done",
        )


class ToolResultOnlyHtmlRuntime:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def detect_status(self) -> AgentProviderStatus:
        return AgentProviderStatus(
            installed=True,
            runnable=True,
            identity="tool-result-html",
            method="test",
            checked_at=now_utc(),
        )

    async def execute(
        self,
        *,
        prompt: str,
        session_id: str | None,
        allowed_tools: list[str] | None,
        model: str | None,
        reasoning_effort: str | None,
        cwd: Path,
        on_chunk,
        cancel_event: asyncio.Event,
        on_ask_user=None,
        runtime_tools=None,
        runtime_policy="execute",
        output_schema=None,
    ) -> AgentExecutionResult:
        self.calls.append(
            {
                "allowed_tools": allowed_tools,
                "runtime_tools": runtime_tools,
                "runtime_policy": runtime_policy,
            }
        )
        if runtime_policy == "ask_user_plan":
            text = '{"action":"complete","decision_summary":"无需额外提问。","reason":"测试场景不需要补充用户决策。"}'
            await on_chunk(AgentChunk(type="text", text=text))
            return AgentExecutionResult(session_id=session_id, total_text=text, finished_with="done")
        if "你正在生成 Mira output 节点" not in prompt:
            text = "UPSTREAM"
            await on_chunk(AgentChunk(type="text", text=text))
            return AgentExecutionResult(
                session_id=session_id or "tool_result_session",
                total_text=text,
                finished_with="done",
            )
        text = "The HTML output above is the final rendered page."
        await on_chunk(AgentChunk(type="tool_result", text="<section>TOOL_HTML</section>"))
        await on_chunk(AgentChunk(type="text", text=text))
        return AgentExecutionResult(
            session_id=session_id or "tool_result_session",
            total_text=text,
            finished_with="done",
        )


class ArtifactContractRuntime:
    async def detect_status(self) -> AgentProviderStatus:
        return AgentProviderStatus(
            installed=True,
            runnable=True,
            identity="artifact-contract",
            method="test",
            checked_at=now_utc(),
        )

    async def execute(
        self,
        *,
        prompt: str,
        session_id: str | None,
        allowed_tools: list[str] | None,
        model: str | None,
        reasoning_effort: str | None,
        cwd: Path,
        on_chunk,
        cancel_event: asyncio.Event,
        on_ask_user=None,
        runtime_tools=None,
        runtime_policy="execute",
        output_schema=None,
    ) -> AgentExecutionResult:
        if runtime_policy == "ask_user_plan":
            text = '{"action":"complete","decision_summary":"无需额外提问。","reason":"测试场景不需要补充用户决策。"}'
            await on_chunk(AgentChunk(type="text", text=text))
            return AgentExecutionResult(session_id=session_id, total_text=text, finished_with="done")
        if _is_output_prompt(prompt):
            text = _structured_text("<section>OK</section>", output_schema)
            await on_chunk(AgentChunk(type="text", text=text))
            return AgentExecutionResult(
                session_id=session_id or "artifact_contract_session",
                total_text=text,
                finished_with="done",
            )
        artifact = cwd / "report.txt"
        artifact.write_text("artifact report", encoding="utf-8")
        text = json.dumps(
            {"artifacts": [{"name": "Report", "path": str(artifact)}]},
            ensure_ascii=False,
        )
        await on_chunk(AgentChunk(type="text", text=text))
        return AgentExecutionResult(
            session_id=session_id or "artifact_contract_session",
            total_text=text,
            finished_with="done",
        )


class ReusedArtifactPathRuntime:
    async def detect_status(self) -> AgentProviderStatus:
        return AgentProviderStatus(
            installed=True,
            runnable=True,
            identity="reused-artifact-path",
            method="test",
            checked_at=now_utc(),
        )

    async def execute(
        self,
        *,
        prompt: str,
        session_id: str | None,
        allowed_tools: list[str] | None,
        model: str | None,
        reasoning_effort: str | None,
        cwd: Path,
        on_chunk,
        cancel_event: asyncio.Event,
        on_ask_user=None,
        runtime_tools=None,
        runtime_policy="execute",
        output_schema=None,
    ) -> AgentExecutionResult:
        if runtime_policy == "ask_user_plan":
            text = '{"action":"complete","decision_summary":"无需额外提问。","reason":"测试场景不需要补充用户决策。"}'
        elif _is_output_prompt(prompt):
            text = _structured_text("<section>OK</section>", output_schema)
        elif "write-bundle" in prompt:
            bundle = cwd / "bundle.zip"
            bundle.write_bytes(b"bundle")
            text = json.dumps({"artifacts": [{"name": "Bundle", "path": str(bundle)}]}, ensure_ascii=False)
        elif "relative-ref" in prompt:
            text = json.dumps({"code_package_ref": {"path": "bundle.zip"}}, ensure_ascii=False)
        else:
            text = "OK"
        await on_chunk(AgentChunk(type="text", text=text))
        return AgentExecutionResult(
            session_id=session_id or "reused_artifact_path_session",
            total_text=text,
            finished_with="done",
        )


class SequenceRuntime:
    def __init__(self, outputs: list[str]) -> None:
        self.outputs = outputs
        self.prompts: list[str] = []

    async def detect_status(self) -> AgentProviderStatus:
        return AgentProviderStatus(
            installed=True,
            runnable=True,
            identity="sequence",
            method="test",
            checked_at=now_utc(),
        )

    async def execute(
        self,
        *,
        prompt: str,
        session_id: str | None,
        allowed_tools: list[str] | None,
        model: str | None,
        reasoning_effort: str | None,
        cwd: Path,
        on_chunk,
        cancel_event: asyncio.Event,
        on_ask_user=None,
        runtime_tools=None,
        runtime_policy="execute",
        output_schema=None,
    ) -> AgentExecutionResult:
        if runtime_policy == "ask_user_plan":
            text = '{"action":"complete","decision_summary":"无需额外提问。","reason":"测试场景不需要补充用户决策。"}'
            await on_chunk(AgentChunk(type="text", text=text))
            return AgentExecutionResult(session_id=session_id, total_text=text, finished_with="done")
        if _is_output_prompt(prompt):
            text = _structured_text("<section>OK</section>", output_schema)
            await on_chunk(AgentChunk(type="text", text=text))
            return AgentExecutionResult(
                session_id=session_id or "sequence_session",
                total_text=text,
                finished_with="done",
            )
        self.prompts.append(prompt)
        text = self.outputs.pop(0) if self.outputs else ""
        await on_chunk(AgentChunk(type="text", text=text))
        return AgentExecutionResult(
            session_id=session_id or "sequence_session",
            total_text=text,
            finished_with="done",
        )


class ParallelProbeRuntime:
    def __init__(self, *, delay: float = 0.2) -> None:
        self.delay = delay
        self.active = 0
        self.max_active = 0
        self.execute_calls: list[dict[str, str | None]] = []
        self._lock = asyncio.Lock()
        self._next_session = 0

    async def detect_status(self) -> AgentProviderStatus:
        return AgentProviderStatus(
            installed=True,
            runnable=True,
            identity="parallel-probe",
            method="test",
            checked_at=now_utc(),
        )

    async def execute(
        self,
        *,
        prompt: str,
        session_id: str | None,
        allowed_tools: list[str] | None,
        model: str | None,
        reasoning_effort: str | None,
        cwd: Path,
        on_chunk,
        cancel_event: asyncio.Event,
        on_ask_user=None,
        runtime_tools=None,
        runtime_policy="execute",
        output_schema=None,
    ) -> AgentExecutionResult:
        if runtime_policy == "ask_user_plan":
            text = '{"action":"complete","decision_summary":"无需额外提问。","reason":"测试场景不需要补充用户决策。"}'
            await on_chunk(AgentChunk(type="text", text=text))
            return AgentExecutionResult(session_id=session_id, total_text=text, finished_with="done")
        async with self._lock:
            self.active += 1
            self.max_active = max(self.max_active, self.active)
            self.execute_calls.append({"prompt": prompt, "session_id": session_id})
            self._next_session += 1
            next_session = session_id or f"probe_session_{self._next_session}"
        try:
            await asyncio.sleep(self.delay)
            text = _structured_text(_respond_marker(prompt) or f"probe:{self._next_session}", output_schema)
            await on_chunk(AgentChunk(type="text", text=text))
            return AgentExecutionResult(session_id=next_session, total_text=text, finished_with="done")
        finally:
            async with self._lock:
                self.active -= 1


def _respond_marker(prompt: str) -> str | None:
    marker = "[[respond:"
    start = prompt.find(marker)
    if start == -1:
        return None
    start += len(marker)
    end = prompt.find("]]", start)
    if end == -1:
        return None
    return prompt[start:end].strip()


def _is_output_prompt(prompt: str) -> bool:
    return "你正在生成 Mira output 节点" in prompt


def _structured_text(text: str, output_schema: dict | None) -> str:
    if (
        isinstance(output_schema, dict)
        and isinstance(output_schema.get("properties"), dict)
        and "html" in output_schema["properties"]
        and text.lstrip().startswith("<")
    ):
        return json.dumps({"html": text}, ensure_ascii=False)
    return text


class AskUserJudgmentRuntime:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.preflight_prompts: list[str] = []
        self.ask_calls = 0

    async def detect_status(self) -> AgentProviderStatus:
        return AgentProviderStatus(
            installed=True,
            runnable=True,
            identity="ask-user-judgment",
            method="test",
            checked_at=now_utc(),
        )

    async def execute(
        self,
        *,
        prompt: str,
        session_id: str | None,
        allowed_tools: list[str] | None,
        model: str | None,
        reasoning_effort: str | None,
        cwd: Path,
        on_chunk,
        cancel_event: asyncio.Event,
        on_ask_user=None,
        runtime_tools=None,
        runtime_policy="execute",
        output_schema=None,
    ) -> AgentExecutionResult:
        self.calls.append(runtime_policy)
        if runtime_policy == "ask_user_plan":
            self.preflight_prompts.append(prompt)
            if _is_output_prompt(prompt):
                text = json.dumps(
                    {
                        "action": "complete",
                        "decision_summary": "无需额外提问。",
                        "reason": "output 渲染不需要补充用户决策。",
                    },
                    ensure_ascii=False,
                )
                await on_chunk(AgentChunk(type="text", text=text))
                return AgentExecutionResult(session_id=session_id, total_text=text, finished_with="done")
            assert "推荐、选择、个性化、需求澄清、方案收敛类任务" in prompt
            should_ask = "我不知道看啥" in prompt and "直接推荐" not in prompt
            if should_ask:
                if "轻松入门" in prompt and "# 已有提问历史 JSON" in prompt:
                    text = json.dumps(
                        {
                            "action": "complete",
                            "decision_summary": "已根据用户回答收敛阅读偏好。",
                            "reason": "用户已回答阅读偏好问题。",
                        },
                        ensure_ascii=False,
                    )
                else:
                    self.ask_calls += 1
                    request = AskUserRequest(
                        context={"title": "确认阅读偏好", "summary": "推荐书籍前需要确认用户希望优先考虑的阅读方向。"},
                        tool_use_id="toolu_judgment",
                        groups=[
                            DecisionGroup.model_validate(
                                {
                                    "id": "reading_taste",
                                    "label": "你更想读哪类书？",
                                    "type": "single",
                                    "options": [
                                        {
                                            "label": "轻松入门",
                                            "description": "推荐门槛低、节奏顺、适合重新开始阅读的书。",
                                            "recommended": True,
                                        },
                                        {
                                            "label": "文学深度",
                                            "description": "推荐主题更厚重、表达更有文学性的作品。",
                                            "recommended": False,
                                        },
                                        {
                                            "label": "类型娱乐",
                                            "description": "推荐剧情驱动、阅读快感更强的类型小说。",
                                            "recommended": False,
                                        },
                                    ],
                                }
                            )
                        ],
                    )
                    text = json.dumps(
                        {
                            "action": "ask",
                            "rationale": "用户输入没有给出阅读偏好。",
                            "request": request.model_dump(exclude_none=True),
                        },
                        ensure_ascii=False,
                    )
            else:
                text = json.dumps(
                    {
                        "action": "complete",
                        "decision_summary": "无需额外提问。",
                        "reason": "用户偏好已足够具体或明确要求直接推荐。",
                    },
                    ensure_ascii=False,
                )
            await on_chunk(AgentChunk(type="text", text=text))
            return AgentExecutionResult(session_id=session_id, total_text=text, finished_with="done")

        if _is_output_prompt(prompt):
            text = _structured_text("<section>OK</section>", output_schema)
        else:
            text = "ASKED_RESULT" if self.ask_calls else "DIRECT_RESULT"
        await on_chunk(AgentChunk(type="text", text=text))
        return AgentExecutionResult(
            session_id=session_id or "ask_user_judgment_session",
            total_text=text,
            finished_with="done",
        )


class PreflightScriptRuntime:
    def __init__(self, preflight_outputs: list[dict[str, Any] | str]) -> None:
        self.preflight_outputs = list(preflight_outputs)
        self.preflight_prompts: list[str] = []
        self.preflight_output_schemas: list[dict[str, Any] | None] = []
        self.execute_calls = 0

    async def detect_status(self) -> AgentProviderStatus:
        return AgentProviderStatus(
            installed=True,
            runnable=True,
            identity="preflight-script",
            method="test",
            checked_at=now_utc(),
        )

    async def execute(
        self,
        *,
        prompt: str,
        session_id: str | None,
        allowed_tools: list[str] | None,
        model: str | None,
        reasoning_effort: str | None,
        cwd: Path,
        on_chunk,
        cancel_event: asyncio.Event,
        on_ask_user=None,
        runtime_tools=None,
        runtime_policy="execute",
        output_schema=None,
    ) -> AgentExecutionResult:
        if runtime_policy == "ask_user_plan":
            self.preflight_prompts.append(prompt)
            self.preflight_output_schemas.append(output_schema)
            payload = self.preflight_outputs.pop(0) if self.preflight_outputs else _complete_action()
            text = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)
            await on_chunk(AgentChunk(type="text", text=text))
            return AgentExecutionResult(session_id=session_id, total_text=text, finished_with="done")
        if _is_output_prompt(prompt):
            text = _structured_text("<section>OK</section>", output_schema)
            await on_chunk(AgentChunk(type="text", text=text))
            return AgentExecutionResult(
                session_id=session_id or "preflight_script_session",
                total_text=text,
                finished_with="done",
            )
        self.execute_calls += 1
        text = json.dumps({"result": "SCRIPT_RESULT"}, ensure_ascii=False) if output_schema is not None else "SCRIPT_RESULT"
        await on_chunk(AgentChunk(type="text", text=text))
        return AgentExecutionResult(
            session_id=session_id or "preflight_script_session",
            total_text=text,
            finished_with="done",
        )


class ParallelTemplateRuntime:
    def __init__(self) -> None:
        self.preflight_prompts: list[str] = []
        self.execute_prompts: list[str] = []

    async def detect_status(self) -> AgentProviderStatus:
        return AgentProviderStatus(
            installed=True,
            runnable=True,
            identity="parallel-template",
            method="test",
            checked_at=now_utc(),
        )

    async def execute(
        self,
        *,
        prompt: str,
        session_id: str | None,
        allowed_tools: list[str] | None,
        model: str | None,
        reasoning_effort: str | None,
        cwd: Path,
        on_chunk,
        cancel_event: asyncio.Event,
        on_ask_user=None,
        runtime_tools=None,
        runtime_policy="execute",
        output_schema=None,
    ) -> AgentExecutionResult:
        if runtime_policy == "ask_user_plan":
            self.preflight_prompts.append(prompt)
            if "并行示例：必须先调用 ask_user" in prompt and "创始团队" not in prompt:
                request = AskUserRequest(
                    context={"title": "确认并行示例方向", "summary": "继续执行前需要确认并行示例的目标受众。"},
                    tool_use_id="toolu_parallel_template",
                    groups=[
                        DecisionGroup.model_validate(
                            {
                                "id": "target_audience",
                                "label": "主要面向谁？",
                                "type": "single",
                                "options": [
                                    {
                                        "label": "创始团队",
                                        "description": "强调策略取舍、资源投入和落地优先级。",
                                        "recommended": True,
                                    },
                                    {
                                        "label": "市场运营",
                                        "description": "强调活动创意、触达渠道和执行细节。",
                                        "recommended": False,
                                    },
                                ],
                            }
                        ),
                        DecisionGroup.model_validate(
                            {
                                "id": "output_style",
                                "label": "结果风格偏好？",
                                "type": "single",
                                "options": [
                                    {
                                        "label": "行动清单",
                                        "description": "输出紧凑、直接、便于立即执行。",
                                        "recommended": True,
                                    },
                                    {
                                        "label": "创意提案",
                                        "description": "输出更有包装感，适合给团队展示。",
                                        "recommended": False,
                                    },
                                ],
                            }
                        ),
                    ],
                )
                text = json.dumps(
                    {
                        "action": "ask",
                        "rationale": "示例模板要求先确认关键偏好。",
                        "request": request.model_dump(exclude_none=True),
                    },
                    ensure_ascii=False,
                )
            elif "并行示例：必须先调用 ask_user" in prompt:
                text = json.dumps(
                    {
                        "action": "complete",
                        "decision_summary": "已确认目标受众和输出风格。",
                        "reason": "用户回答已足够驱动示例模板继续执行。",
                    },
                    ensure_ascii=False,
                )
            else:
                text = json.dumps(
                    {
                        "action": "complete",
                        "decision_summary": "当前节点不需要额外提问。",
                        "reason": "该并行分支可以直接根据已有主题执行。",
                    },
                    ensure_ascii=False,
                )
            await on_chunk(AgentChunk(type="text", text=text))
            return AgentExecutionResult(session_id=session_id, total_text=text, finished_with="done")

        self.execute_prompts.append(prompt)
        if "PERSONAL_STRATEGY" in prompt and "PARALLEL_RESEARCH" in prompt:
            text = _structured_text("<section>FINAL_HTML</section>", output_schema)
        elif "并行拆解" in prompt:
            text = "PARALLEL_RESEARCH"
        else:
            text = "PERSONAL_STRATEGY"
        await on_chunk(AgentChunk(type="text", text=text))
        return AgentExecutionResult(
            session_id=session_id or "parallel_template_session",
            total_text=text,
            finished_with="done",
        )


def _ask_action(group_id: str, label: str, options: list[str]) -> dict[str, Any]:
    return {
        "action": "ask",
        "rationale": f"需要补齐 {label}",
        "request": {
            "context": {"title": "确认运行方向", "summary": f"继续运行前需要确认：{label}"},
            "groups": [
                {
                    "id": group_id,
                    "label": label,
                    "type": "single",
                    "options": [
                        {
                            "label": option,
                            "description": f"选择 {option} 会影响后续推荐。",
                            "recommended": index == 0,
                        }
                        for index, option in enumerate(options)
                    ],
                }
            ]
        },
    }


def _complete_action(summary: str = "无需额外提问。", reason: str = "信息已足够。") -> dict[str, Any]:
    return {"action": "complete", "decision_summary": summary, "reason": reason}


def _assert_strict_object_schema(schema: dict[str, Any], path: str = "$") -> None:
    schema_type = schema.get("type")
    is_object = schema_type == "object" or (isinstance(schema_type, list) and "object" in schema_type)
    if is_object:
        assert schema.get("additionalProperties") is False, path
        properties = schema.get("properties")
        if isinstance(properties, dict):
            assert set(schema.get("required", [])) == set(properties), path
            for key, value in properties.items():
                if isinstance(value, dict):
                    _assert_strict_object_schema(value, f"{path}.properties.{key}")
    items = schema.get("items")
    if isinstance(items, dict):
        _assert_strict_object_schema(items, f"{path}.items")


def test_ask_user_preflight_output_schema_is_codex_strict():
    _assert_strict_object_schema(_ASK_USER_PREFLIGHT_OUTPUT_SCHEMA)


def _execute_calls(runtime: ReasoningCaptureRuntime) -> list[dict[str, Any]]:
    return [call for call in runtime.calls if call.get("runtime_policy") == "execute"]


def _planning_calls(runtime: ReasoningCaptureRuntime) -> list[dict[str, Any]]:
    return [call for call in runtime.calls if call.get("runtime_policy") == "ask_user_plan"]


USER_INPUT_NODE = {
    "id": "n_input",
    "type": "user_input",
    "position": {"x": 0, "y": 0},
    "title": "Input",
    "input_schema": {"label": "input", "kind": "textarea"},
}


def _generate_node(node_id: str, *, prompt: str) -> dict:
    return {
        "id": node_id,
        "type": "generate",
        "position": {"x": 0, "y": 0},
        "title": node_id,
        "prompt": prompt,
    }


def _condition_node(node_id: str, *, mode: str, branches: list[dict], prompt: str) -> dict:
    return {
        "id": node_id,
        "type": "condition",
        "position": {"x": 0, "y": 0},
        "title": node_id,
        "prompt": prompt,
        "mode": mode,
        "branches": branches,
    }


def _contract_node(node_id: str, *, prompt: str, output_contract: dict[str, Any]) -> dict:
    node = _generate_node(node_id, prompt=prompt)
    node["output_contract"] = output_contract
    return node


def _json_contract(*fields: str) -> dict[str, Any]:
    names = fields or ("result",)
    return {
        "type": "json",
        "json_schema": {
            "type": "object",
            "additionalProperties": False,
            "properties": {field: {"type": "string"} for field in names},
            "required": list(names),
        },
    }


def _output_node(node_id: str, *, source: str, prompt: str = "整理输出") -> dict:
    return {
        "id": node_id,
        "type": "output",
        "position": {"x": 0, "y": 0},
        "title": node_id,
        "prompt": prompt,
        "source_node_id": source,
    }


def _ensure_output(graph: dict) -> dict:
    if any(node.get("type") == "output" for node in graph.get("nodes", []) if isinstance(node, dict)):
        return graph
    next_graph = deepcopy(graph)
    nodes = next_graph.setdefault("nodes", [])
    source = next(
        (node.get("id") for node in reversed(nodes) if isinstance(node, dict) and isinstance(node.get("id"), str)),
        "",
    )
    nodes.append(_output_node("n_auto_out", source=source, prompt="render [[respond:<section>ok</section>]]"))
    if source:
        next_graph.setdefault("edges", []).append({"id": "e_auto_out", "source": source, "target": "n_auto_out"})
    return next_graph


def _build_app(auth_client, *, graph: dict) -> str:
    created = auth_client.post("/api/apps", json={"name": "RunExecApp"}).json()
    response = auth_client.patch(f"/api/apps/{created['id']}", json={"graph": _ensure_output(graph)})
    assert response.status_code == 200, response.text
    return created["id"]


def _upload_skill(auth_client, name: str) -> dict:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        zf.writestr("SKILL.md", f"---\nname: {name}\ndescription: {name}\n---\n")
    buffer.seek(0)
    response = auth_client.post(
        "/api/skills/parse",
        files={"archive": (f"{name}.zip", buffer.getvalue(), "application/zip")},
    )
    assert response.status_code == 200, response.text
    return response.json()


def _wait_for_terminal(auth_client, run_id: str, *, timeout: float = 6.0) -> dict:
    deadline = time.time() + timeout
    last: dict | None = None
    while time.time() < deadline:
        body = auth_client.get(f"/api/runs/{run_id}").json()
        last = body
        if body["status"] in {"success", "failed", "cancelled"}:
            return body
        time.sleep(0.05)
    raise AssertionError(f"run did not finish: {last}")


def _wait_for_status(auth_client, run_id: str, expected: set[str], *, timeout: float = 6.0) -> dict:
    deadline = time.time() + timeout
    last: dict | None = None
    while time.time() < deadline:
        body = auth_client.get(f"/api/runs/{run_id}").json()
        last = body
        if body["status"] in expected:
            return body
        time.sleep(0.05)
    raise AssertionError(f"run did not enter {expected}: last={last}")


def test_executor_runs_input_generate_output(auth_client, enable_claude_agent):
    enable_claude_agent()
    graph = {
        "agent": "claude",
        "nodes": [
            USER_INPUT_NODE,
            _generate_node("n_gen", prompt="请生成 [[respond:GENERATED-TEXT]]"),
            _output_node("n_out", source="n_gen", prompt="渲染 [[respond:<section>FINAL-HTML</section>]]"),
        ],
        "edges": [
            {"id": "e1", "source": "n_input", "target": "n_gen"},
            {"id": "e2", "source": "n_gen", "target": "n_out"},
        ],
    }
    app_id = _build_app(auth_client, graph=graph)
    run = auth_client.post(
        "/api/runs", json={"app_id": app_id, "inputs": {"n_input": "hello"}}
    ).json()
    final = _wait_for_terminal(auth_client, run["run_id"])
    assert final["status"] == "success", final
    by_id = {step["node_id"]: step for step in final["steps"]}
    assert by_id["n_input"]["status"] == "success"
    assert by_id["n_input"]["output"]["value"] == "hello"
    assert by_id["n_gen"]["status"] == "success"
    assert by_id["n_gen"]["output"] == "GENERATED-TEXT"
    assert by_id["n_gen"]["agent_session_id"] is not None
    assert by_id["n_out"]["status"] == "success"
    assert by_id["n_out"]["output"] == "<section>FINAL-HTML</section>"
    assert by_id["n_out"]["agent_session_id"] == by_id["n_gen"]["agent_session_id"]


def test_executor_runs_independent_generate_nodes_concurrently(auth_client, enable_claude_agent):
    enable_claude_agent()
    runtime = ParallelProbeRuntime(delay=0.25)
    set_runtime_override(runtime)
    graph = {
        "agent": "claude",
        "nodes": [
            _generate_node("n_gen_a", prompt="A [[respond:A]]"),
            _generate_node("n_gen_b", prompt="B [[respond:B]]"),
        ],
        "edges": [],
    }
    try:
        app_id = _build_app(auth_client, graph=graph)
        run = auth_client.post("/api/runs", json={"app_id": app_id, "inputs": {}}).json()
        final = _wait_for_terminal(auth_client, run["run_id"])
        by_id = {step["node_id"]: step for step in final["steps"]}
        assert final["status"] == "success", final
        assert by_id["n_gen_a"]["output"] == "A"
        assert by_id["n_gen_b"]["output"] == "B"
        assert runtime.max_active >= 2
    finally:
        set_runtime_override(MockRuntime())


def test_executor_waits_for_parallel_upstreams_before_merge(auth_client, enable_claude_agent):
    enable_claude_agent()
    graph = {
        "agent": "claude",
        "nodes": [
            _generate_node("n_gen_a", prompt="A [[delay:0.2]] [[respond:A_OUT]]"),
            _generate_node("n_gen_b", prompt="B [[delay:0.2]] [[respond:B_OUT]]"),
            _output_node("n_out", source="n_gen_a", prompt="merge [[respond:<section>MERGED</section>]]"),
        ],
        "edges": [
            {"id": "e1", "source": "n_gen_a", "target": "n_out"},
            {"id": "e2", "source": "n_gen_b", "target": "n_out"},
        ],
    }
    app_id = _build_app(auth_client, graph=graph)
    run = auth_client.post("/api/runs", json={"app_id": app_id, "inputs": {}}).json()
    final = _wait_for_terminal(auth_client, run["run_id"])
    by_id = {step["node_id"]: step for step in final["steps"]}
    assert final["status"] == "success", final
    assert by_id["n_out"]["output"] == "<section>MERGED</section>"
    assert "A_OUT" in by_id["n_out"]["input"]["prompt"]
    assert "B_OUT" in by_id["n_out"]["input"]["prompt"]
    assert by_id["n_out"]["started_at"] >= by_id["n_gen_a"]["finished_at"]
    assert by_id["n_out"]["started_at"] >= by_id["n_gen_b"]["finished_at"]


def test_executor_does_not_start_new_downstream_after_parallel_failure(auth_client, enable_claude_agent):
    enable_claude_agent()
    graph = {
        "agent": "claude",
        "nodes": [
            _generate_node("n_gen_fail", prompt="please fail-now"),
            _generate_node("n_gen_slow", prompt="please [[delay:0.2]] [[respond:SLOW_OK]]"),
            _output_node("n_out", source="n_gen_slow"),
        ],
        "edges": [{"id": "e1", "source": "n_gen_slow", "target": "n_out"}],
    }
    app_id = _build_app(auth_client, graph=graph)
    run = auth_client.post("/api/runs", json={"app_id": app_id, "inputs": {}}).json()
    final = _wait_for_terminal(auth_client, run["run_id"])
    by_id = {step["node_id"]: step for step in final["steps"]}
    assert final["status"] == "failed"
    assert by_id["n_gen_fail"]["status"] == "failed"
    assert by_id["n_gen_slow"]["status"] == "success"
    assert by_id["n_out"]["status"] == "pending"


def test_rerun_from_parallel_failure_reuses_only_ancestors(auth_client, enable_claude_agent):
    enable_claude_agent()
    graph = {
        "agent": "claude",
        "nodes": [
            USER_INPUT_NODE,
            _generate_node("n_gen_fail", prompt="please fail-now"),
            _generate_node("n_gen_sibling", prompt="sibling [[delay:0.2]] [[respond:OLD_SIBLING]]"),
            _output_node("n_out", source="n_gen_fail", prompt="merge [[respond:<section>OLD_OUT</section>]]"),
        ],
        "edges": [
            {"id": "e_input_fail", "source": "n_input", "target": "n_gen_fail"},
            {"id": "e_input_sibling", "source": "n_input", "target": "n_gen_sibling"},
            {"id": "e_fail_out", "source": "n_gen_fail", "target": "n_out"},
            {"id": "e_sibling_out", "source": "n_gen_sibling", "target": "n_out"},
        ],
    }
    app_id = _build_app(auth_client, graph=graph)
    source = auth_client.post(
        "/api/runs", json={"app_id": app_id, "inputs": {"n_input": "parallel input"}}
    ).json()
    source_final = _wait_for_terminal(auth_client, source["run_id"])
    source_by_id = {step["node_id"]: step for step in source_final["steps"]}
    assert source_final["status"] == "failed"
    assert source_final["recovery"] is None
    assert source_by_id["n_gen_fail"]["status"] == "failed"
    assert source_by_id["n_gen_sibling"]["status"] == "success"
    assert source_by_id["n_gen_sibling"]["output"] == "OLD_SIBLING"
    assert source_by_id["n_out"]["status"] == "pending"

    continued = auth_client.post(f"/api/runs/{source['run_id']}/continue")
    assert continued.status_code == 409
    assert continued.json()["detail"] == "当前运行不需要继续"

    fixed_graph = {
        **graph,
        "nodes": [
            USER_INPUT_NODE,
            _generate_node("n_gen_fail", prompt="fixed [[respond:REPAIRED_FAIL]]"),
            _generate_node("n_gen_sibling", prompt="sibling [[respond:NEW_SIBLING]]"),
            _output_node("n_out", source="n_gen_fail", prompt="merge [[respond:<section>MERGED</section>]]"),
        ],
    }
    response = auth_client.patch(f"/api/apps/{app_id}", json={"graph": fixed_graph})
    assert response.status_code == 200, response.text

    created = auth_client.post(
        f"/api/runs/{source['run_id']}/rerun-from",
        json={"app_id": app_id, "node_id": "n_gen_fail"},
    )
    assert created.status_code == 200, created.text
    final = _wait_for_terminal(auth_client, created.json()["run_id"])
    assert final["status"] == "success", final
    assert final["id"] != source["run_id"]
    by_id = {step["node_id"]: step for step in final["steps"]}
    assert by_id["n_input"]["output"]["value"] == "parallel input"
    assert by_id["n_gen_fail"]["output"] == "REPAIRED_FAIL"
    assert by_id["n_gen_sibling"]["status"] == "success"
    assert by_id["n_gen_sibling"]["output"] == "NEW_SIBLING"
    assert by_id["n_out"]["output"] == "<section>MERGED</section>"

    source_after = auth_client.get(f"/api/runs/{source['run_id']}").json()
    source_after_by_id = {step["node_id"]: step for step in source_after["steps"]}
    assert source_after["status"] == "failed"
    assert source_after_by_id["n_gen_fail"]["status"] == "failed"
    assert source_after_by_id["n_gen_sibling"]["output"] == "OLD_SIBLING"


def test_executor_reuses_session_for_linear_chain_but_not_fanout(auth_client, enable_claude_agent):
    enable_claude_agent()
    runtime = ParallelProbeRuntime(delay=0.05)
    set_runtime_override(runtime)
    graph = {
        "agent": "claude",
        "nodes": [
            _generate_node("n_root", prompt="root [[respond:ROOT]]"),
            _generate_node("n_child_a", prompt="child a [[respond:A]]"),
            _generate_node("n_child_b", prompt="child b [[respond:B]]"),
            _output_node("n_out", source="n_child_a", prompt="out [[respond:<section>OUT</section>]]"),
        ],
        "edges": [
            {"id": "e1", "source": "n_root", "target": "n_child_a"},
            {"id": "e2", "source": "n_root", "target": "n_child_b"},
            {"id": "e3", "source": "n_child_a", "target": "n_out"},
        ],
    }
    try:
        app_id = _build_app(auth_client, graph=graph)
        run = auth_client.post("/api/runs", json={"app_id": app_id, "inputs": {}}).json()
        final = _wait_for_terminal(auth_client, run["run_id"])
        by_id = {step["node_id"]: step for step in final["steps"]}
        assert final["status"] == "success", final
        assert by_id["n_child_a"]["agent_session_id"] != by_id["n_root"]["agent_session_id"]
        assert by_id["n_child_b"]["agent_session_id"] != by_id["n_root"]["agent_session_id"]
        assert by_id["n_out"]["agent_session_id"] == by_id["n_child_a"]["agent_session_id"]
    finally:
        set_runtime_override(MockRuntime())


def test_parallel_ask_user_is_visible_before_sibling_finishes(auth_client, enable_claude_agent):
    enable_claude_agent()
    ask = json.dumps(_ask_action("choice", "选择方向？", ["A", "B"])["request"], ensure_ascii=False)
    graph = {
        "agent": "claude",
        "nodes": [
            _generate_node("n_ask", prompt=f"ask now [[ask_user:{ask}]] [[respond:ASK_DONE]]"),
            _generate_node("n_delay", prompt="delayed sibling [[delay:0.8]] [[respond:SLOW_DONE]]"),
            _output_node("n_out", source="n_ask", prompt="render [[respond:<section>DONE</section>]]"),
        ],
        "edges": [
            {"id": "e_ask_out", "source": "n_ask", "target": "n_out"},
            {"id": "e_delay_out", "source": "n_delay", "target": "n_out"},
        ],
    }
    app_id = _build_app(auth_client, graph=graph)
    run = auth_client.post("/api/runs", json={"app_id": app_id, "inputs": {}}).json()

    waiting = _wait_for_status(auth_client, run["run_id"], {"waiting_for_user"})
    by_id = {step["node_id"]: step for step in waiting["steps"]}
    assert waiting["recovery"]["resume_from_node_id"] == "n_ask"
    assert by_id["n_ask"]["status"] == "waiting_for_user"
    assert by_id["n_delay"]["status"] == "running"

    response = auth_client.post(
        f"/api/runs/{run['run_id']}/resume",
        json={
            "node_id": "n_ask",
            "tool_use_id": by_id["n_ask"]["input"]["ask_user"]["tool_use_id"],
            "answers": [{"group_id": "choice", "selected": ["A"]}],
        },
    )
    assert response.status_code == 204, response.text

    final = _wait_for_terminal(auth_client, run["run_id"])
    by_id = {step["node_id"]: step for step in final["steps"]}
    assert final["status"] == "success", final
    assert by_id["n_ask"]["output"].startswith("ASK_DONE")
    assert by_id["n_delay"]["output"] == "SLOW_DONE"
    assert by_id["n_out"]["output"] == "<section>DONE</section>"


def test_parallel_sibling_failure_overrides_visible_ask_user(auth_client, enable_claude_agent):
    enable_claude_agent()
    ask = json.dumps(_ask_action("choice", "选择方向？", ["A", "B"])["request"], ensure_ascii=False)
    graph = {
        "agent": "claude",
        "nodes": [
            _generate_node("n_ask", prompt=f"ask now [[ask_user:{ask}]] [[respond:ASK_DONE]]"),
            _generate_node("n_fail", prompt="delayed failure [[delay:0.4]] fail-now"),
            _output_node("n_out", source="n_ask", prompt="render [[respond:<section>DONE</section>]]"),
        ],
        "edges": [
            {"id": "e_ask_out", "source": "n_ask", "target": "n_out"},
            {"id": "e_fail_out", "source": "n_fail", "target": "n_out"},
        ],
    }
    app_id = _build_app(auth_client, graph=graph)
    run = auth_client.post("/api/runs", json={"app_id": app_id, "inputs": {}}).json()

    waiting = _wait_for_status(auth_client, run["run_id"], {"waiting_for_user"})
    by_id = {step["node_id"]: step for step in waiting["steps"]}
    assert by_id["n_ask"]["status"] == "waiting_for_user"
    assert by_id["n_fail"]["status"] == "running"

    final = _wait_for_terminal(auth_client, run["run_id"])
    by_id = {step["node_id"]: step for step in final["steps"]}
    assert final["status"] == "failed"
    assert final["recovery"] is None
    assert by_id["n_ask"]["status"] == "waiting_for_user"
    assert by_id["n_fail"]["status"] == "failed"
    assert by_id["n_out"]["status"] == "pending"


def test_parallel_ask_user_requests_are_queued(auth_client, enable_claude_agent):
    enable_claude_agent()
    ask_a = json.dumps(_ask_action("choice_a", "选择 A？", ["A1", "A2"])["request"], ensure_ascii=False)
    ask_b = json.dumps(_ask_action("choice_b", "选择 B？", ["B1", "B2"])["request"], ensure_ascii=False)
    graph = {
        "agent": "claude",
        "nodes": [
            _generate_node("n_gen_a", prompt=f"ask a [[ask_user:{ask_a}]] [[respond:A_DONE]]"),
            _generate_node("n_gen_b", prompt=f"ask b [[ask_user:{ask_b}]] [[respond:B_DONE]]"),
        ],
        "edges": [],
    }
    try:
        app_id = _build_app(auth_client, graph=graph)
        run = auth_client.post("/api/runs", json={"app_id": app_id, "inputs": {}}).json()
        run_id = run["run_id"]

        expected_groups = {"n_gen_a": "choice_a", "n_gen_b": "choice_b"}
        expected_answers = {"choice_a": "A1", "choice_b": "B1"}
        first = _wait_for_status(auth_client, run_id, {"waiting_for_user"})
        first_node = first["recovery"]["resume_from_node_id"]
        assert first_node in expected_groups
        first_step = {step["node_id"]: step for step in first["steps"]}[first_node]
        first_group = first_step["input"]["ask_user"]["groups"][0]["id"]
        assert first_group == expected_groups[first_node]
        response = auth_client.post(
            f"/api/runs/{run_id}/resume",
            json={
                "node_id": first_node,
                "tool_use_id": first_step["input"]["ask_user"]["tool_use_id"],
                "answers": [{"group_id": first_group, "selected": [expected_answers[first_group]]}],
            },
        )
        assert response.status_code == 204, response.text

        second = _wait_for_status(auth_client, run_id, {"waiting_for_user"})
        second_node = second["recovery"]["resume_from_node_id"]
        assert second_node in expected_groups
        assert second_node != first_node
        second_step = {step["node_id"]: step for step in second["steps"]}[second_node]
        second_group = second_step["input"]["ask_user"]["groups"][0]["id"]
        assert second_group == expected_groups[second_node]
        response = auth_client.post(
            f"/api/runs/{run_id}/resume",
            json={
                "node_id": second_node,
                "tool_use_id": second_step["input"]["ask_user"]["tool_use_id"],
                "answers": [{"group_id": second_group, "selected": [expected_answers[second_group]]}],
            },
        )
        assert response.status_code == 204, response.text

        final = _wait_for_terminal(auth_client, run_id)
        by_id = {step["node_id"]: step for step in final["steps"]}
        assert final["status"] == "success", final
        assert by_id["n_gen_a"]["output"].startswith("A_DONE")
        assert by_id["n_gen_b"]["output"].startswith("B_DONE")
    finally:
        set_runtime_override(MockRuntime())


def test_parallel_second_ask_after_resume_keeps_run_waiting(auth_client, enable_claude_agent):
    enable_claude_agent()
    ask_first = json.dumps(_ask_action("choice_first", "选择第一步？", ["A1", "A2"])["request"], ensure_ascii=False)
    ask_second = json.dumps(_ask_action("choice_second", "选择第二步？", ["B1", "B2"])["request"], ensure_ascii=False)
    graph = {
        "agent": "claude",
        "nodes": [
            _generate_node("n_first", prompt=f"first ask [[ask_user:{ask_first}]] [[respond:FIRST_DONE]]"),
            _generate_node("n_short", prompt="short sibling [[delay:0.3]] [[respond:SHORT_DONE]]"),
            _generate_node("n_long", prompt="long sibling [[delay:3.0]] [[respond:LONG_DONE]]"),
            _generate_node("n_second", prompt=f"second ask [[ask_user:{ask_second}]] [[respond:SECOND_DONE]]"),
            _output_node("n_out", source="n_second", prompt="render [[respond:<section>DONE</section>]]"),
        ],
        "edges": [
            {"id": "e_first_second", "source": "n_first", "target": "n_second"},
            {"id": "e_first_long", "source": "n_first", "target": "n_long"},
            {"id": "e_second_out", "source": "n_second", "target": "n_out"},
        ],
    }
    try:
        app_id = _build_app(auth_client, graph=graph)
        run = auth_client.post("/api/runs", json={"app_id": app_id, "inputs": {}}).json()
        run_id = run["run_id"]

        first = _wait_for_status(auth_client, run_id, {"waiting_for_user"})
        first_step = {step["node_id"]: step for step in first["steps"]}["n_first"]
        assert {step["node_id"]: step for step in first["steps"]}["n_short"]["status"] == "running"
        assert {step["node_id"]: step for step in first["steps"]}["n_long"]["status"] == "pending"
        response = auth_client.post(
            f"/api/runs/{run_id}/resume",
            json={
                "node_id": "n_first",
                "tool_use_id": first_step["input"]["ask_user"]["tool_use_id"],
                "answers": [{"group_id": "choice_first", "selected": ["A1"]}],
            },
        )
        assert response.status_code == 204, response.text

        second = _wait_for_status(auth_client, run_id, {"waiting_for_user"})
        by_id = {step["node_id"]: step for step in second["steps"]}
        assert second["recovery"]["resume_from_node_id"] == "n_second"
        assert by_id["n_second"]["status"] == "waiting_for_user"
        assert by_id["n_long"]["status"] == "running"
        response = auth_client.post(
            f"/api/runs/{run_id}/resume",
            json={
                "node_id": "n_second",
                "tool_use_id": by_id["n_second"]["input"]["ask_user"]["tool_use_id"],
                "answers": [{"group_id": "choice_second", "selected": ["B1"]}],
            },
        )
        assert response.status_code == 204, response.text

        final = _wait_for_terminal(auth_client, run_id, timeout=15.0)
        by_id = {step["node_id"]: step for step in final["steps"]}
        assert final["status"] == "success", final
        assert by_id["n_first"]["output"].startswith("FIRST_DONE")
        assert by_id["n_second"]["output"].startswith("SECOND_DONE")
        assert by_id["n_long"]["output"] == "LONG_DONE"
    finally:
        set_runtime_override(MockRuntime())


def test_parallel_ask_gallery_template_runs_and_waits_for_user(auth_client, enable_claude_agent):
    enable_claude_agent()
    runtime = ParallelTemplateRuntime()
    set_runtime_override(runtime)
    try:
        cloned = auth_client.post("/api/apps/clone/tpl_parallel_ask_demo")
        assert cloned.status_code == 200, cloned.text
        app = cloned.json()
        assert app["cover"] and app["cover"] != "upl_tpl_parallel_ask_demo_cover"
        node_ids = {node["id"] for node in app["graph"]["nodes"]}
        assert {"n_parallel_strategy", "n_parallel_research", "n_parallel_output"} <= node_ids

        run = auth_client.post(
            "/api/runs",
            json={
                "app_id": app["id"],
                "inputs": {"n_parallel_input": "给一家独立咖啡店做夏季会员活动"},
            },
        )
        assert run.status_code == 200, run.text
        run_id = run.json()["run_id"]

        waiting = _wait_for_status(auth_client, run_id, {"waiting_for_user"})
        assert waiting["recovery"]["resume_from_node_id"] == "n_parallel_strategy"
        by_id = {step["node_id"]: step for step in waiting["steps"]}
        ask = by_id["n_parallel_strategy"]["input"]["ask_user"]
        assert by_id["n_parallel_strategy"]["status"] == "waiting_for_user"
        assert by_id["n_parallel_research"]["status"] == "success", by_id["n_parallel_research"]
        assert [group["id"] for group in ask["groups"]] == ["target_audience", "output_style"]
        assert ask["groups"][0]["options"][-1]["label"] == "以上都不是"

        response = auth_client.post(
            f"/api/runs/{run_id}/resume",
            json={
                "node_id": "n_parallel_strategy",
                "tool_use_id": ask["tool_use_id"],
                "answers": [
                    {"group_id": "target_audience", "selected": ["创始团队"]},
                    {"group_id": "output_style", "selected": ["行动清单"]},
                ],
            },
        )
        assert response.status_code == 204, response.text
        final = _wait_for_terminal(auth_client, run_id)
        by_id = {step["node_id"]: step for step in final["steps"]}
        assert final["status"] == "success", final
        assert by_id["n_parallel_strategy"]["output"] == "PERSONAL_STRATEGY"
        assert by_id["n_parallel_research"]["output"] == "PARALLEL_RESEARCH"
        assert by_id["n_parallel_output"]["output"] == "<section>FINAL_HTML</section>"
        output_prompt = by_id["n_parallel_output"]["input"]["prompt"]
        assert "PERSONAL_STRATEGY" in output_prompt
        assert "PARALLEL_RESEARCH" in output_prompt
    finally:
        set_runtime_override(MockRuntime())


def test_vague_recommendation_input_enters_ask_user_waiting(auth_client, enable_claude_agent):
    enable_claude_agent()
    runtime = AskUserJudgmentRuntime()
    set_runtime_override(runtime)
    run_id: str | None = None
    graph = {
        "agent": "claude",
        "nodes": [
            USER_INPUT_NODE,
            _generate_node("n_gen", prompt="你是一名专业文学助手。请根据用户的读书偏好推荐 5 本书。"),
        ],
        "edges": [{"id": "e1", "source": "n_input", "target": "n_gen"}],
    }

    try:
        app_id = _build_app(auth_client, graph=graph)
        run = auth_client.post(
            "/api/runs",
            json={"app_id": app_id, "inputs": {"n_input": "我不知道看啥，最近想找本书"}},
        ).json()
        run_id = run["run_id"]

        waiting = _wait_for_status(auth_client, run_id, {"waiting_for_user"})
        by_id = {step["node_id"]: step for step in waiting["steps"]}
        ask = by_id["n_gen"]["input"]["ask_user"]
        assert by_id["n_gen"]["status"] == "waiting_for_user"
        assert runtime.ask_calls == 1
        assert ask["tool_use_id"] == "toolu_judgment"
        assert [option["label"] for option in ask["groups"][0]["options"]] == [
            "轻松入门",
            "文学深度",
            "类型娱乐",
            "以上都不是",
        ]

        response = auth_client.post(
            f"/api/runs/{run_id}/resume",
            json={
                "node_id": "n_gen",
                "tool_use_id": ask["tool_use_id"],
                "answers": [{"group_id": "reading_taste", "selected": ["轻松入门"]}],
            },
        )
        assert response.status_code == 204, response.text
        final = _wait_for_terminal(auth_client, run_id)
        by_id = {step["node_id"]: step for step in final["steps"]}
        assert final["status"] == "success"
        assert by_id["n_gen"]["output"] == "ASKED_RESULT"
        assert by_id["n_gen"]["input"]["resume"]["answers"] == [
            {"group_id": "reading_taste", "selected": ["轻松入门"]}
        ]
    finally:
        if run_id:
            auth_client.post(f"/api/runs/{run_id}/cancel")
        set_runtime_override(MockRuntime())


def test_specific_recommendation_input_can_skip_ask_user(auth_client, enable_claude_agent):
    enable_claude_agent()
    runtime = AskUserJudgmentRuntime()
    set_runtime_override(runtime)
    graph = {
        "agent": "claude",
        "nodes": [
            USER_INPUT_NODE,
            _generate_node("n_gen", prompt="你是一名专业文学助手。请根据用户的读书偏好推荐 5 本书。"),
        ],
        "edges": [{"id": "e1", "source": "n_input", "target": "n_gen"}],
    }

    try:
        app_id = _build_app(auth_client, graph=graph)
        run = auth_client.post(
            "/api/runs",
            json={
                "app_id": app_id,
                "inputs": {"n_input": "我想看慢热奇幻、角色复杂、篇幅不要太长，请直接推荐。"},
            },
        ).json()

        final = _wait_for_terminal(auth_client, run["run_id"])
        by_id = {step["node_id"]: step for step in final["steps"]}
        assert final["status"] == "success"
        assert runtime.ask_calls == 0
        assert by_id["n_gen"]["status"] == "success"
        assert by_id["n_gen"]["output"] == "DIRECT_RESULT"
        assert "ask_user" not in by_id["n_gen"]["input"]
    finally:
        set_runtime_override(MockRuntime())


def test_explicit_question_request_retries_invalid_preflight_complete(auth_client, enable_claude_agent):
    enable_claude_agent()
    runtime = PreflightScriptRuntime(
        [
            _complete_action("无需额外提问。", "按常见阅读偏好先推荐。"),
            _ask_action("reading_taste", "你想先按哪种阅读偏好筛选？", ["轻松", "文学", "悬疑"]),
        ]
    )
    set_runtime_override(runtime)
    run_id: str | None = None
    graph = {
        "agent": "claude",
        "nodes": [
            USER_INPUT_NODE,
            _generate_node("n_gen", prompt="请根据用户偏好推荐书。"),
        ],
        "edges": [{"id": "e1", "source": "n_input", "target": "n_gen"}],
    }

    try:
        app_id = _build_app(auth_client, graph=graph)
        run = auth_client.post(
            "/api/runs",
            json={"app_id": app_id, "inputs": {"n_input": "问我几个问题在确定找什么书"}},
        ).json()
        run_id = run["run_id"]
        waiting = _wait_for_status(auth_client, run_id, {"waiting_for_user"})
        by_id = {step["node_id"]: step for step in waiting["steps"]}
        ask = by_id["n_gen"]["input"]["ask_user"]
        assert len(runtime.preflight_prompts) == 2
        assert ask["groups"][0]["id"] == "reading_taste"
        assert runtime.execute_calls == 0
    finally:
        if run_id:
            auth_client.post(f"/api/runs/{run_id}/cancel")
        set_runtime_override(MockRuntime())


def test_preflight_ignores_extra_json_after_valid_action(auth_client, enable_claude_agent):
    enable_claude_agent()
    action = json.dumps(
        _complete_action(
            "已获取足够信息：行业为农业，需生成 3-5 个软著选题。",
            "用户已明确提供行业领域和输出要求，无需额外澄清。",
        ),
        ensure_ascii=False,
    )
    runtime = PreflightScriptRuntime(
        [
            "\n\n".join(
                [
                    action,
                    "```json",
                    json.dumps(
                        {
                            "topics": [
                                {
                                    "name": "农田智能灌溉决策系统",
                                    "description": "根据土壤墒情和气象数据生成灌溉策略。",
                                }
                            ]
                        },
                        ensure_ascii=False,
                    ),
                    "```",
                ]
            )
        ]
    )
    set_runtime_override(runtime)
    graph = {
        "agent": "claude",
        "nodes": [
            _generate_node("n_gen", prompt="请基于农业行业生成 3-5 个软著选题。"),
        ],
        "edges": [],
    }

    try:
        app_id = _build_app(auth_client, graph=graph)
        run = auth_client.post("/api/runs", json={"app_id": app_id, "inputs": {}}).json()

        final = _wait_for_terminal(auth_client, run["run_id"])
        by_id = {step["node_id"]: step for step in final["steps"]}
        assert final["status"] == "success", final
        assert by_id["n_gen"]["status"] == "success"
        assert by_id["n_gen"]["output"] == "SCRIPT_RESULT"
        assert runtime.execute_calls == 1
        assert runtime.preflight_output_schemas[0]["properties"]["action"]["enum"] == ["ask", "complete"]
        assert "输出该 JSON 对象后必须立即停止" in runtime.preflight_prompts[0]
    finally:
        set_runtime_override(MockRuntime())


def test_preflight_can_ask_multiple_rounds_before_execute(auth_client, enable_claude_agent):
    enable_claude_agent()
    runtime = PreflightScriptRuntime(
        [
            _ask_action("reading_taste", "你更想读哪类书？", ["轻松", "文学", "悬疑"]),
            _ask_action("reading_length", "你能接受多长篇幅？", ["短篇", "中篇", "长篇"]),
            _complete_action("已根据两轮回答收敛书单偏好。", "用户已回答类型和篇幅。"),
        ]
    )
    set_runtime_override(runtime)
    graph = {
        "agent": "claude",
        "nodes": [
            USER_INPUT_NODE,
            _generate_node("n_gen", prompt="请根据用户偏好推荐书。"),
        ],
        "edges": [{"id": "e1", "source": "n_input", "target": "n_gen"}],
    }

    try:
        app_id = _build_app(auth_client, graph=graph)
        run = auth_client.post(
            "/api/runs",
            json={"app_id": app_id, "inputs": {"n_input": "我不知道看啥，先问我几个问题"}},
        ).json()
        run_id = run["run_id"]

        first = _wait_for_status(auth_client, run_id, {"waiting_for_user"})
        first_ask = {step["node_id"]: step for step in first["steps"]}["n_gen"]["input"]["ask_user"]
        response = auth_client.post(
            f"/api/runs/{run_id}/resume",
            json={
                "node_id": "n_gen",
                "tool_use_id": first_ask["tool_use_id"],
                "answers": [{"group_id": "reading_taste", "selected": ["轻松"]}],
            },
        )
        assert response.status_code == 204, response.text

        second = _wait_for_status(auth_client, run_id, {"waiting_for_user"})
        second_step = {step["node_id"]: step for step in second["steps"]}["n_gen"]
        second_ask = second_step["input"]["ask_user"]
        assert second_ask["groups"][0]["id"] == "reading_length"
        response = auth_client.post(
            f"/api/runs/{run_id}/resume",
            json={
                "node_id": "n_gen",
                "tool_use_id": second_ask["tool_use_id"],
                "answers": [{"group_id": "reading_length", "selected": ["短篇"]}],
            },
        )
        assert response.status_code == 204, response.text

        final = _wait_for_terminal(auth_client, run_id)
        final_step = {step["node_id"]: step for step in final["steps"]}["n_gen"]
        assert final["status"] == "success", final
        assert final_step["output"] == "SCRIPT_RESULT"
        assert len(final_step["input"]["ask_user_preflight"]["history"]) == 2
        assert runtime.execute_calls == 1
    finally:
        set_runtime_override(MockRuntime())


def test_preflight_has_no_fixed_ask_user_round_limit(auth_client, enable_claude_agent):
    enable_claude_agent()
    rounds = [
        ("reading_taste", "你更想读哪类书？", "轻松"),
        ("reading_length", "你能接受多长篇幅？", "短篇"),
        ("reading_tone", "你偏好什么叙事风格？", "温和"),
        ("reading_region", "你更想看哪个地区作品？", "不限"),
    ]
    runtime = PreflightScriptRuntime(
        [
            *[
                _ask_action(group_id, label, [selected, f"{selected}备选"])
                for group_id, label, selected in rounds
            ],
            _complete_action("已根据四轮回答收敛书单偏好。", "用户已回答类型、篇幅、风格和地区。"),
        ]
    )
    set_runtime_override(runtime)
    graph = {
        "agent": "claude",
        "nodes": [
            USER_INPUT_NODE,
            _generate_node("n_gen", prompt="请根据用户偏好推荐书。"),
        ],
        "edges": [{"id": "e1", "source": "n_input", "target": "n_gen"}],
    }

    try:
        app_id = _build_app(auth_client, graph=graph)
        run = auth_client.post(
            "/api/runs",
            json={"app_id": app_id, "inputs": {"n_input": "我不知道看啥，先问我几个问题"}},
        ).json()
        run_id = run["run_id"]

        for group_id, _label, selected in rounds:
            waiting = _wait_for_status(auth_client, run_id, {"waiting_for_user"})
            ask = {step["node_id"]: step for step in waiting["steps"]}["n_gen"]["input"]["ask_user"]
            assert ask["groups"][0]["id"] == group_id
            response = auth_client.post(
                f"/api/runs/{run_id}/resume",
                json={
                    "node_id": "n_gen",
                    "tool_use_id": ask["tool_use_id"],
                    "answers": [{"group_id": group_id, "selected": [selected]}],
                },
            )
            assert response.status_code == 204, response.text

        final = _wait_for_terminal(auth_client, run_id)
        final_step = {step["node_id"]: step for step in final["steps"]}["n_gen"]
        assert final["status"] == "success", final
        assert final_step["output"] == "SCRIPT_RESULT"
        assert len(final_step["input"]["ask_user_preflight"]["history"]) == 4
        assert runtime.execute_calls == 1
    finally:
        set_runtime_override(MockRuntime())


def test_preflight_rejects_fabricated_user_cancellation(auth_client, enable_claude_agent):
    enable_claude_agent()
    runtime = PreflightScriptRuntime(
        [
            _complete_action(
                "用户取消了偏好提问；后续基于原始输入推荐。",
                "当前无法继续获取更多决策信息。",
            ),
            _complete_action(
                "用户取消了偏好提问；后续基于原始输入推荐。",
                "当前无法继续获取更多决策信息。",
            ),
        ]
    )
    set_runtime_override(runtime)
    graph = {
        "agent": "claude",
        "nodes": [
            USER_INPUT_NODE,
            _generate_node("n_gen", prompt="请根据用户偏好推荐书。"),
        ],
        "edges": [{"id": "e1", "source": "n_input", "target": "n_gen"}],
    }

    try:
        app_id = _build_app(auth_client, graph=graph)
        run = auth_client.post(
            "/api/runs",
            json={"app_id": app_id, "inputs": {"n_input": "我不知道看啥，最近想找本书"}},
        ).json()
        final = _wait_for_terminal(auth_client, run["run_id"])
        by_id = {step["node_id"]: step for step in final["steps"]}
        assert final["status"] == "failed"
        assert "没有历史回答" in (by_id["n_gen"]["error"] or "")
        assert runtime.execute_calls == 0
    finally:
        set_runtime_override(MockRuntime())


def test_executor_uses_run_graph_snapshot_after_app_changes(auth_client, enable_claude_agent, monkeypatch):
    enable_claude_agent()

    from app.api import runs as runs_api

    def _no_schedule(_run_id: str):
        return None

    monkeypatch.setattr(runs_api, "schedule_run", _no_schedule)
    graph = {
        "agent": "claude",
        "nodes": [
            USER_INPUT_NODE,
            _generate_node("n_gen", prompt="请生成 [[respond:SNAPSHOT]]"),
            _output_node("n_out", source="n_gen", prompt="整理输出 [[respond:<section>SNAPSHOT_OUT</section>]]"),
        ],
        "edges": [
            {"id": "e1", "source": "n_input", "target": "n_gen"},
            {"id": "e2", "source": "n_gen", "target": "n_out"},
        ],
    }
    app_id = _build_app(auth_client, graph=graph)
    created = auth_client.post(
        "/api/runs", json={"app_id": app_id, "inputs": {"n_input": "hello"}}
    ).json()

    changed_graph = {
        "agent": "claude",
        "nodes": [USER_INPUT_NODE],
        "edges": [],
    }
    response = auth_client.patch(f"/api/apps/{app_id}", json={"graph": changed_graph})
    assert response.status_code == 200, response.text

    snapshot = auth_client.get(f"/api/runs/{created['run_id']}").json()
    assert [node["id"] for node in snapshot["graph"]["nodes"]] == ["n_input", "n_gen", "n_out"]

    asyncio.run(start_run(created["run_id"]))
    final = auth_client.get(f"/api/runs/{created['run_id']}").json()
    assert final["status"] == "success", final
    by_id = {step["node_id"]: step for step in final["steps"]}
    assert by_id["n_gen"]["output"] == "SNAPSHOT"
    assert by_id["n_out"]["status"] == "success"


def test_rerun_from_reuses_ancestor_outputs_and_uses_current_graph(auth_client, enable_claude_agent):
    enable_claude_agent()
    graph = {
        "agent": "claude",
        "nodes": [
            USER_INPUT_NODE,
            _generate_node("n_gen_a", prompt="第一步 [[respond:OLD_A]]"),
            _generate_node("n_gen_b", prompt="第二步 [[respond:OLD_B]]"),
            _output_node("n_out", source="n_gen_b", prompt="输出 [[respond:<section>OLD_OUT</section>]]"),
        ],
        "edges": [
            {"id": "e1", "source": "n_input", "target": "n_gen_a"},
            {"id": "e2", "source": "n_gen_a", "target": "n_gen_b"},
            {"id": "e3", "source": "n_gen_b", "target": "n_out"},
        ],
    }
    app_id = _build_app(auth_client, graph=graph)
    source = auth_client.post(
        "/api/runs", json={"app_id": app_id, "inputs": {"n_input": "hello"}}
    ).json()
    source_final = _wait_for_terminal(auth_client, source["run_id"])
    assert source_final["status"] == "success"

    current_graph = {
        "agent": "claude",
        "nodes": [
            USER_INPUT_NODE,
            _generate_node("n_gen_a", prompt="第一步 [[respond:NEW_A_SHOULD_NOT_RUN]]"),
            _generate_node("n_gen_b", prompt="第二步 [[respond:NEW_B]]"),
            _output_node("n_out", source="n_gen_b", prompt="输出 [[respond:<section>NEW_OUT</section>]]"),
        ],
        "edges": graph["edges"],
    }
    response = auth_client.patch(f"/api/apps/{app_id}", json={"graph": current_graph})
    assert response.status_code == 200, response.text

    created = auth_client.post(
        f"/api/runs/{source['run_id']}/rerun-from",
        json={"app_id": app_id, "node_id": "n_gen_b"},
    )
    assert created.status_code == 200, created.text
    final = _wait_for_terminal(auth_client, created.json()["run_id"])
    assert final["status"] == "success", final
    assert final["id"] != source["run_id"]
    by_id = {step["node_id"]: step for step in final["steps"]}
    assert by_id["n_input"]["output"]["value"] == "hello"
    assert by_id["n_gen_a"]["output"] == "OLD_A"
    assert by_id["n_gen_b"]["output"] == "NEW_B"
    assert "OLD_A" in by_id["n_gen_b"]["input"]["prompt"]
    assert by_id["n_out"]["output"] == "<section>NEW_OUT</section>"
    assert final["graph"]["nodes"][1]["prompt"] == "第一步 [[respond:NEW_A_SHOULD_NOT_RUN]]"

    source_after = auth_client.get(f"/api/runs/{source['run_id']}").json()
    assert source_after["status"] == "success"
    assert {step["node_id"]: step for step in source_after["steps"]}["n_gen_a"]["output"] == "OLD_A"


def test_rerun_from_copies_reused_workspace_artifact_paths(auth_client, enable_claude_agent):
    enable_claude_agent()
    set_runtime_override(ReusedArtifactPathRuntime())
    try:
        graph = {
            "agent": "claude",
            "nodes": [
                USER_INPUT_NODE,
                _contract_node(
                    "n_bundle",
                    prompt="write-bundle",
                    output_contract={"type": "artifact", "artifact_kind": "archive"},
                ),
                _contract_node(
                    "n_ref",
                    prompt="relative-ref",
                    output_contract={
                        "type": "json",
                        "json_schema": {
                            "type": "object",
                            "additionalProperties": False,
                            "properties": {
                                "code_package_ref": {
                                    "type": "object",
                                    "additionalProperties": False,
                                    "properties": {"path": {"type": "string"}},
                                    "required": ["path"],
                                }
                            },
                            "required": ["code_package_ref"],
                        },
                    },
                ),
                _output_node("n_out", source="n_ref", prompt="输出"),
            ],
            "edges": [
                {"id": "e1", "source": "n_input", "target": "n_bundle"},
                {"id": "e2", "source": "n_bundle", "target": "n_ref"},
                {"id": "e3", "source": "n_ref", "target": "n_out"},
            ],
        }
        app_id = _build_app(auth_client, graph=graph)
        source = auth_client.post(
            "/api/runs", json={"app_id": app_id, "inputs": {"n_input": "hello"}}
        ).json()
        source_final = _wait_for_terminal(auth_client, source["run_id"])
        assert source_final["status"] == "success", source_final

        created = auth_client.post(
            f"/api/runs/{source['run_id']}/rerun-from",
            json={"app_id": app_id, "node_id": "n_out"},
        )
        assert created.status_code == 200, created.text
        rerun_id = created.json()["run_id"]
        final = _wait_for_terminal(auth_client, rerun_id)
        assert final["status"] == "success", final

        async def load_raw_outputs() -> dict[str, Any]:
            async with SessionLocal() as db:
                rows = (
                    await db.execute(
                        select(Step).where(Step.run_id == rerun_id, Step.node_id.in_(["n_bundle", "n_ref"]))
                    )
                ).scalars().all()
                return {row.node_id: loads(row.output_json, None) for row in rows}

        raw_outputs = asyncio.run(load_raw_outputs())
        bundle_output = raw_outputs["n_bundle"][0]
        bundle_path = Path(bundle_output["path"])
        assert bundle_path.exists()
        assert bundle_path.read_bytes() == b"bundle"
        assert rerun_id in str(bundle_path)

        ref_output = raw_outputs["n_ref"]
        assert ref_output["code_package_ref"]["path"] == "bundle.zip"
        assert (run_workspace("user_admin", app_id, rerun_id) / "bundle.zip").read_bytes() == b"bundle"
    finally:
        set_runtime_override(None)


def test_rerun_from_does_not_copy_reused_ancestor_agent_session(auth_client, enable_claude_agent):
    enable_claude_agent()
    runtime = ParallelProbeRuntime(delay=0.01)
    set_runtime_override(runtime)
    try:
        graph = {
            "agent": "claude",
            "nodes": [
                USER_INPUT_NODE,
                _generate_node("n_gen_a", prompt="第一步 [[respond:OLD_A]]"),
                _generate_node("n_gen_b", prompt="第二步 [[respond:OLD_B]]"),
                _output_node("n_out", source="n_gen_b", prompt="输出 [[respond:<section>OLD_OUT</section>]]"),
            ],
            "edges": [
                {"id": "e1", "source": "n_input", "target": "n_gen_a"},
                {"id": "e2", "source": "n_gen_a", "target": "n_gen_b"},
                {"id": "e3", "source": "n_gen_b", "target": "n_out"},
            ],
        }
        app_id = _build_app(auth_client, graph=graph)
        source = auth_client.post(
            "/api/runs", json={"app_id": app_id, "inputs": {"n_input": "hello"}}
        ).json()
        source_final = _wait_for_terminal(auth_client, source["run_id"])
        source_by_id = {step["node_id"]: step for step in source_final["steps"]}
        assert source_by_id["n_gen_a"]["agent_session_id"] is not None

        current_graph = {
            "agent": "claude",
            "nodes": [
                USER_INPUT_NODE,
                _generate_node("n_gen_a", prompt="第一步 [[respond:NEW_A_SHOULD_NOT_RUN]]"),
                _generate_node("n_gen_b", prompt="第二步 [[respond:NEW_B]]"),
                _output_node("n_out", source="n_gen_b", prompt="输出 [[respond:<section>NEW_OUT</section>]]"),
            ],
            "edges": graph["edges"],
        }
        response = auth_client.patch(f"/api/apps/{app_id}", json={"graph": current_graph})
        assert response.status_code == 200, response.text

        runtime.execute_calls.clear()
        created = auth_client.post(
            f"/api/runs/{source['run_id']}/rerun-from",
            json={"app_id": app_id, "node_id": "n_gen_b"},
        )
        assert created.status_code == 200, created.text
        final = _wait_for_terminal(auth_client, created.json()["run_id"])
        assert final["status"] == "success", final
        by_id = {step["node_id"]: step for step in final["steps"]}
        assert by_id["n_gen_a"]["output"] == "OLD_A"
        assert by_id["n_gen_a"]["agent_session_id"] is None
        assert by_id["n_gen_b"]["output"] == "NEW_B"
        assert by_id["n_gen_b"]["agent_session_id"] is not None
        assert by_id["n_out"]["agent_session_id"] == by_id["n_gen_b"]["agent_session_id"]

        gen_b_call = next(call for call in runtime.execute_calls if "第二步" in (call["prompt"] or ""))
        assert gen_b_call["session_id"] is None
    finally:
        set_runtime_override(MockRuntime())


def test_rerun_from_rejects_unusable_ancestor_result(auth_client, enable_claude_agent):
    enable_claude_agent()
    graph = {
        "agent": "claude",
        "nodes": [
            _generate_node("n_gen_fail", prompt="please fail-now"),
            _output_node("n_out", source="n_gen_fail"),
        ],
        "edges": [{"id": "e1", "source": "n_gen_fail", "target": "n_out"}],
    }
    app_id = _build_app(auth_client, graph=graph)
    source = auth_client.post("/api/runs", json={"app_id": app_id, "inputs": {}}).json()
    source_final = _wait_for_terminal(auth_client, source["run_id"])
    assert source_final["status"] == "failed"

    response = auth_client.post(
        f"/api/runs/{source['run_id']}/rerun-from",
        json={"app_id": app_id, "node_id": "n_out"},
    )
    assert response.status_code == 409
    assert "n_gen_fail" in response.json()["detail"]


def test_rerun_from_condition_branch_override_forces_branch(auth_client, enable_claude_agent):
    enable_claude_agent()
    graph = {
        "agent": "claude",
        "nodes": [
            USER_INPUT_NODE,
            _condition_node(
                "n_cond",
                mode="binary",
                branches=[{"key": "true"}, {"key": "false"}],
                prompt="判断 [[respond:true]]",
            ),
            _generate_node("n_yes", prompt="是 [[respond:YES]]"),
            _generate_node("n_no", prompt="否 [[respond:NO]]"),
        ],
        "edges": [
            {"id": "e1", "source": "n_input", "target": "n_cond"},
            {"id": "e2", "source": "n_cond", "target": "n_yes", "source_handle": "true"},
            {"id": "e3", "source": "n_cond", "target": "n_no", "source_handle": "false"},
        ],
    }
    app_id = _build_app(auth_client, graph=graph)
    source = auth_client.post(
        "/api/runs", json={"app_id": app_id, "inputs": {"n_input": "branch input"}}
    ).json()
    source_final = _wait_for_terminal(auth_client, source["run_id"])
    assert source_final["status"] == "success"
    source_by_id = {step["node_id"]: step for step in source_final["steps"]}
    assert source_by_id["n_cond"]["output"] == "true"
    assert source_by_id["n_yes"]["status"] == "success"
    assert source_by_id["n_no"]["status"] == "skipped"

    patched_graph = {
        **graph,
        "nodes": [
            USER_INPUT_NODE,
            _condition_node(
                "n_cond",
                mode="binary",
                branches=[{"key": "true"}, {"key": "false"}],
                prompt="please fail-now",
            ),
            _generate_node("n_yes", prompt="是 [[respond:YES_NEW]]"),
            _generate_node("n_no", prompt="否 [[respond:NO_NEW]]"),
        ],
    }
    response = auth_client.patch(f"/api/apps/{app_id}", json={"graph": _ensure_output(patched_graph)})
    assert response.status_code == 200, response.text

    created = auth_client.post(
        f"/api/runs/{source['run_id']}/rerun-from",
        json={
            "app_id": app_id,
            "node_id": "n_cond",
            "condition_branch_override": {"node_id": "n_cond", "branch_key": "false"},
        },
    )
    assert created.status_code == 200, created.text
    final = _wait_for_terminal(auth_client, created.json()["run_id"])
    assert final["status"] == "success"
    assert final["name"] == "分支测试 · n_cond · false"
    by_id = {step["node_id"]: step for step in final["steps"]}
    assert by_id["n_input"]["output"]["value"] == "branch input"
    assert by_id["n_cond"]["output"] == "false"
    assert by_id["n_cond"]["input"]["condition_result"] == {
        "chosen_branch": "false",
        "unchosen_branches": ["true"],
        "reason": "用户在分支测试中强制选择该分支",
        "raw_answer": None,
        "forced": True,
    }
    assert by_id["n_yes"]["status"] == "skipped"
    assert by_id["n_no"]["status"] == "success"
    assert by_id["n_no"]["output"] == "NO_NEW"


def test_rerun_from_condition_branch_override_rejects_invalid_branch(auth_client, enable_claude_agent):
    enable_claude_agent()
    graph = {
        "agent": "claude",
        "nodes": [
            _condition_node(
                "n_cond",
                mode="binary",
                branches=[{"key": "true"}, {"key": "false"}],
                prompt="判断 [[respond:true]]",
            ),
            _generate_node("n_yes", prompt="是 [[respond:YES]]"),
        ],
        "edges": [{"id": "e1", "source": "n_cond", "target": "n_yes", "source_handle": "true"}],
    }
    app_id = _build_app(auth_client, graph=graph)
    source = auth_client.post("/api/runs", json={"app_id": app_id, "inputs": {}}).json()
    assert _wait_for_terminal(auth_client, source["run_id"])["status"] == "success"

    invalid = auth_client.post(
        f"/api/runs/{source['run_id']}/rerun-from",
        json={
            "app_id": app_id,
            "node_id": "n_cond",
            "condition_branch_override": {"node_id": "n_cond", "branch_key": "maybe"},
        },
    )
    assert invalid.status_code == 400
    assert "condition 分支不存在" in invalid.json()["detail"]

    unconnected = auth_client.post(
        f"/api/runs/{source['run_id']}/rerun-from",
        json={
            "app_id": app_id,
            "node_id": "n_cond",
            "condition_branch_override": {"node_id": "n_cond", "branch_key": "false"},
        },
    )
    assert unconnected.status_code == 400
    assert "condition 分支未连接" in unconnected.json()["detail"]


def test_rerun_from_failed_node_uses_current_fixed_prompt(auth_client, enable_claude_agent):
    enable_claude_agent()
    graph = {
        "agent": "claude",
        "nodes": [
            USER_INPUT_NODE,
            _generate_node("n_gen", prompt="please fail-now"),
            _output_node("n_out", source="n_gen", prompt="输出 [[respond:<section>OUT</section>]]"),
        ],
        "edges": [
            {"id": "e1", "source": "n_input", "target": "n_gen"},
            {"id": "e2", "source": "n_gen", "target": "n_out"},
        ],
    }
    app_id = _build_app(auth_client, graph=graph)
    source = auth_client.post(
        "/api/runs", json={"app_id": app_id, "inputs": {"n_input": "old input"}}
    ).json()
    source_final = _wait_for_terminal(auth_client, source["run_id"])
    assert source_final["status"] == "failed"

    fixed_graph = {
        **graph,
        "nodes": [
            USER_INPUT_NODE,
            _generate_node("n_gen", prompt="fixed [[respond:REPAIRED]]"),
            _output_node("n_out", source="n_gen", prompt="输出 [[respond:<section>OUT</section>]]"),
        ],
    }
    response = auth_client.patch(f"/api/apps/{app_id}", json={"graph": fixed_graph})
    assert response.status_code == 200, response.text

    created = auth_client.post(
        f"/api/runs/{source['run_id']}/rerun-from",
        json={"app_id": app_id, "node_id": "n_gen"},
    )
    assert created.status_code == 200, created.text
    final = _wait_for_terminal(auth_client, created.json()["run_id"])
    assert final["status"] == "success"
    by_id = {step["node_id"]: step for step in final["steps"]}
    assert by_id["n_input"]["output"]["value"] == "old input"
    assert by_id["n_gen"]["output"] == "REPAIRED"
    assert by_id["n_out"]["output"] == "<section>OUT</section>"

    source_after = auth_client.get(f"/api/runs/{source['run_id']}").json()
    assert source_after["status"] == "failed"
    assert {step["node_id"]: step for step in source_after["steps"]}["n_gen"]["status"] == "failed"


def test_rerun_from_failed_node_replays_changed_input_ancestor(auth_client, enable_claude_agent):
    enable_claude_agent()
    graph = {
        "agent": "claude",
        "nodes": [
            USER_INPUT_NODE,
            _generate_node("n_gen", prompt="please fail-now"),
            _output_node("n_out", source="n_gen", prompt="输出 [[respond:<section>OUT</section>]]"),
        ],
        "edges": [
            {"id": "e1", "source": "n_input", "target": "n_gen"},
            {"id": "e2", "source": "n_gen", "target": "n_out"},
        ],
    }
    app_id = _build_app(auth_client, graph=graph)
    source = auth_client.post(
        "/api/runs", json={"app_id": app_id, "inputs": {"n_input": "old input"}}
    ).json()
    assert _wait_for_terminal(auth_client, source["run_id"])["status"] == "failed"

    fixed_graph = {
        **graph,
        "nodes": [
            USER_INPUT_NODE,
            _generate_node("n_gen", prompt="fixed [[respond:REPAIRED]]"),
            _output_node("n_out", source="n_gen", prompt="输出 [[respond:<section>OUT</section>]]"),
        ],
    }
    response = auth_client.patch(f"/api/apps/{app_id}", json={"graph": fixed_graph})
    assert response.status_code == 200, response.text

    created = auth_client.post(
        f"/api/runs/{source['run_id']}/rerun-from",
        json={"app_id": app_id, "node_id": "n_gen", "inputs": {"n_input": "new input"}},
    )
    assert created.status_code == 200, created.text
    final = _wait_for_terminal(auth_client, created.json()["run_id"])
    assert final["status"] == "success"
    by_id = {step["node_id"]: step for step in final["steps"]}
    assert by_id["n_input"]["output"]["value"] == "new input"
    assert "new input" in by_id["n_gen"]["input"]["prompt"]
    assert "old input" not in by_id["n_gen"]["input"]["prompt"]
    assert by_id["n_gen"]["output"] == "REPAIRED"


def test_rerun_from_allows_deleted_old_input_when_inputs_omitted(auth_client, enable_claude_agent):
    enable_claude_agent()
    graph = {
        "agent": "claude",
        "nodes": [
            USER_INPUT_NODE,
            _generate_node("n_gen", prompt="生成 [[respond:OLD]]"),
        ],
        "edges": [{"id": "e1", "source": "n_input", "target": "n_gen"}],
    }
    app_id = _build_app(auth_client, graph=graph)
    source = auth_client.post(
        "/api/runs", json={"app_id": app_id, "inputs": {"n_input": "old input"}}
    ).json()
    assert _wait_for_terminal(auth_client, source["run_id"])["status"] == "success"

    current_graph = {
        "agent": "claude",
        "nodes": [_generate_node("n_gen", prompt="生成 [[respond:NEW]]")],
        "edges": [],
    }
    response = auth_client.patch(f"/api/apps/{app_id}", json={"graph": _ensure_output(current_graph)})
    assert response.status_code == 200, response.text

    created = auth_client.post(
        f"/api/runs/{source['run_id']}/rerun-from",
        json={"app_id": app_id, "node_id": "n_gen"},
    )
    assert created.status_code == 200, created.text
    final = _wait_for_terminal(auth_client, created.json()["run_id"])
    assert final["status"] == "success"
    assert final["inputs"] == {}
    assert final["steps"][0]["output"] == "NEW"


def test_executor_passes_node_reasoning_effort_with_low_default(auth_client, enable_claude_agent):
    enable_claude_agent()
    runtime = ReasoningCaptureRuntime()
    set_runtime_override(runtime)
    try:
        graph = {
            "agent": "claude",
            "nodes": [
                _generate_node("n_gen", prompt="生成"),
                {
                    **_output_node("n_out", source="n_gen"),
                    "reasoning_effort": "high",
                },
            ],
            "edges": [{"id": "e1", "source": "n_gen", "target": "n_out"}],
        }
        app_id = _build_app(auth_client, graph=graph)
        run = auth_client.post("/api/runs", json={"app_id": app_id, "inputs": {}}).json()
        final = _wait_for_terminal(auth_client, run["run_id"])
    finally:
        set_runtime_override(MockRuntime())

    assert final["status"] == "success", final
    execute_calls = _execute_calls(runtime)
    assert [call["reasoning_effort"] for call in execute_calls] == ["low", "high"]


def test_executor_ignores_legacy_node_allowed_tools(auth_client, enable_claude_agent):
    enable_claude_agent()
    runtime = ReasoningCaptureRuntime()
    set_runtime_override(runtime)
    try:
        graph = {
            "agent": "claude",
            "nodes": [
                {
                    **_generate_node("n_gen", prompt="生成"),
                    "allowed_tools": ["legacy-mcp", "legacy-skill"],
                },
            ],
            "edges": [],
        }
        app_id = _build_app(auth_client, graph=graph)
        run = auth_client.post("/api/runs", json={"app_id": app_id, "inputs": {}}).json()
        final = _wait_for_terminal(auth_client, run["run_id"])
    finally:
        set_runtime_override(MockRuntime())

    assert final["status"] == "success", final
    execute_calls = _execute_calls(runtime)
    assert all(call["allowed_tools"] is None for call in execute_calls)


def test_executor_passes_app_scoped_tools(auth_client, enable_claude_agent):
    enable_claude_agent()
    allowed_skill = _upload_skill(auth_client, "allowed-skill")
    blocked_skill = _upload_skill(auth_client, "blocked-skill")
    assert auth_client.post(
        "/api/settings/mcp",
        json={
            "id": "mcp_allowed",
            "name": "allowed-mcp",
            "enabled": True,
            "provider_ids": ["claude-code", "codex"],
            "url": "http://localhost:9999/allowed",
            "headers": [{"name": "X-Test", "value": "1"}],
            "env_var_names": [],
        },
    ).status_code == 200
    assert auth_client.post(
        "/api/settings/mcp",
        json={
            "id": "mcp_blocked",
            "name": "blocked-mcp",
            "enabled": True,
            "provider_ids": ["claude-code", "codex"],
            "url": "http://localhost:9999/blocked",
            "headers": [],
            "env_var_names": [],
        },
    ).status_code == 200

    runtime = ReasoningCaptureRuntime()
    set_runtime_override(runtime)
    try:
        graph = {
            "agent": "claude",
            "tools": {
                "disabled_tool_ids": [
                    "mcp:mcp_blocked",
                    f"skill:{blocked_skill['id']}",
                ],
            },
            "nodes": [_generate_node("n_gen", prompt="生成")],
            "edges": [],
        }
        app_id = _build_app(auth_client, graph=graph)
        run = auth_client.post("/api/runs", json={"app_id": app_id, "inputs": {}}).json()
        final = _wait_for_terminal(auth_client, run["run_id"])
    finally:
        set_runtime_override(MockRuntime())

    assert final["status"] == "success", final
    runtime_tools = _execute_calls(runtime)[0]["runtime_tools"]
    assert [server.name for server in runtime_tools.mcp_servers] == ["allowed-mcp"]
    assert [server.headers[0].value for server in runtime_tools.mcp_servers] == ["1"]
    assert [skill.id for skill in runtime_tools.skills] == [allowed_skill["id"]]


def test_executor_trusts_run_tools_snapshot(auth_client, enable_claude_agent, monkeypatch):
    enable_claude_agent()
    from app.api import runs as runs_api

    def _no_schedule(_run_id: str):
        return None

    monkeypatch.setattr(runs_api, "schedule_run", _no_schedule)
    assert auth_client.post(
        "/api/settings/mcp",
        json={
            "id": "mcp_snapshot",
            "name": "snapshot-mcp",
            "enabled": True,
            "provider_ids": ["claude-code", "codex"],
            "url": "http://localhost:9999/snapshot",
            "headers": [],
            "env_var_names": [],
        },
    ).status_code == 200

    graph = {
        "agent": "claude",
        "nodes": [_generate_node("n_gen", prompt="生成")],
        "edges": [],
    }
    app_id = _build_app(auth_client, graph=graph)
    created = auth_client.post("/api/runs", json={"app_id": app_id, "inputs": {}}).json()

    async def replace_snapshot() -> None:
        async with SessionLocal() as db:
            run = await db.get(Run, created["run_id"])
            assert run is not None
            graph_json = loads(run.graph_json, {})
            graph_json["_runtime_tools"] = {"allowed_tool_ids": []}
            run.graph_json = dumps(graph_json)
            await db.commit()

    asyncio.run(replace_snapshot())

    runtime = ReasoningCaptureRuntime()
    set_runtime_override(runtime)
    try:
        asyncio.run(start_run(created["run_id"]))
        final = auth_client.get(f"/api/runs/{created['run_id']}").json()
    finally:
        set_runtime_override(MockRuntime())

    assert final["status"] == "success", final
    runtime_tools = _execute_calls(runtime)[0]["runtime_tools"]
    assert runtime_tools.mcp_servers == []


def test_executor_preflight_passes_only_planning_safe_tools(auth_client, enable_claude_agent):
    enable_claude_agent()
    planning_skill = _upload_skill(auth_client, "planning-skill")
    execute_skill = _upload_skill(auth_client, "execute-skill")
    assert auth_client.patch(
        f"/api/settings/skills/{planning_skill['id']}",
        json={"planning_enabled": True},
    ).status_code == 200
    assert auth_client.post(
        "/api/settings/mcp",
        json={
            "id": "mcp_planning",
            "name": "planning-mcp",
            "enabled": True,
            "planning_enabled": True,
            "provider_ids": ["claude-code", "codex"],
            "url": "http://localhost:9999/planning",
            "headers": [{"name": "X-Mode", "value": "planning"}],
            "env_var_names": [],
        },
    ).status_code == 200
    assert auth_client.post(
        "/api/settings/mcp",
        json={
            "id": "mcp_execute",
            "name": "execute-mcp",
            "enabled": True,
            "planning_enabled": False,
            "provider_ids": ["claude-code", "codex"],
            "url": "http://localhost:9999/execute",
            "headers": [],
            "env_var_names": [],
        },
    ).status_code == 200

    runtime = ReasoningCaptureRuntime()
    set_runtime_override(runtime)
    try:
        graph = {
            "agent": "claude",
            "nodes": [_generate_node("n_gen", prompt="生成")],
            "edges": [],
        }
        app_id = _build_app(auth_client, graph=graph)
        run = auth_client.post("/api/runs", json={"app_id": app_id, "inputs": {}}).json()
        final = _wait_for_terminal(auth_client, run["run_id"])
    finally:
        set_runtime_override(MockRuntime())

    assert final["status"] == "success", final
    planning_tools = _planning_calls(runtime)[0]["runtime_tools"]
    assert [server.name for server in planning_tools.mcp_servers] == ["planning-mcp"]
    assert [server.headers[0].value for server in planning_tools.mcp_servers] == ["planning"]
    assert [skill.id for skill in planning_tools.skills] == [planning_skill["id"]]

    execute_tools = _execute_calls(runtime)[0]["runtime_tools"]
    assert sorted(server.name for server in execute_tools.mcp_servers) == ["execute-mcp", "planning-mcp"]
    assert sorted(skill.id for skill in execute_tools.skills) == sorted([planning_skill["id"], execute_skill["id"]])


def test_executor_validates_generate_json_output_contract(auth_client, enable_claude_agent):
    enable_claude_agent()
    runtime = SequenceRuntime(['{"summary":"done"}'])
    set_runtime_override(runtime)
    try:
        graph = {
            "agent": "claude",
            "nodes": [
                _contract_node(
                    "n_gen",
                    prompt="生成结构化结果",
                    output_contract=_json_contract("summary"),
                ),
            ],
            "edges": [],
        }
        app_id = _build_app(auth_client, graph=graph)
        run = auth_client.post("/api/runs", json={"app_id": app_id, "inputs": {}}).json()
        final = _wait_for_terminal(auth_client, run["run_id"])
    finally:
        set_runtime_override(MockRuntime())

    assert final["status"] == "success", final
    step = final["steps"][0]
    assert step["output"] == {"summary": "done"}
    assert "输出契约" in step["input"]["prompt"]


def test_executor_keeps_output_contract_out_of_ask_user_preflight(auth_client, enable_claude_agent):
    enable_claude_agent()
    runtime = PreflightScriptRuntime([_complete_action()])
    set_runtime_override(runtime)
    try:
        graph = {
            "agent": "claude",
            "nodes": [
                _contract_node(
                    "n_gen",
                    prompt="生成纯文本结果",
                    output_contract=_json_contract("result"),
                ),
            ],
            "edges": [],
        }
        app_id = _build_app(auth_client, graph=graph)
        run = auth_client.post("/api/runs", json={"app_id": app_id, "inputs": {}}).json()
        final = _wait_for_terminal(auth_client, run["run_id"])
    finally:
        set_runtime_override(MockRuntime())

    assert final["status"] == "success", final
    generate_preflight_prompts = [
        prompt for prompt in runtime.preflight_prompts if "生成纯文本结果" in prompt
    ]
    assert len(generate_preflight_prompts) == 1
    assert "# 输出契约" not in generate_preflight_prompts[0]
    assert "最终回复必须是严格符合后端 JSON Schema" not in generate_preflight_prompts[0]
    step = final["steps"][0]
    assert "# 输出契约" in step["input"]["prompt"]
    assert "最终回复必须是严格符合后端 JSON Schema" in step["input"]["prompt"]


def test_executor_skips_ask_user_preflight_for_output_node(auth_client, enable_claude_agent):
    enable_claude_agent()
    runtime = PreflightScriptRuntime([_complete_action()])
    set_runtime_override(runtime)
    try:
        graph = {
            "agent": "claude",
            "nodes": [
                _generate_node("n_gen", prompt="生成中间内容"),
                _output_node("n_out", source="n_gen", prompt="展示最终 HTML"),
            ],
            "edges": [{"id": "e1", "source": "n_gen", "target": "n_out"}],
        }
        app_id = _build_app(auth_client, graph=graph)
        run = auth_client.post("/api/runs", json={"app_id": app_id, "inputs": {}}).json()
        final = _wait_for_terminal(auth_client, run["run_id"])
    finally:
        set_runtime_override(MockRuntime())

    assert final["status"] == "success", final
    assert len(runtime.preflight_prompts) == 1
    assert "生成中间内容" in runtime.preflight_prompts[0]
    assert "展示最终 HTML" not in runtime.preflight_prompts[0]
    by_id = {step["node_id"]: step for step in final["steps"]}
    assert by_id["n_out"]["output"] == "<section>OK</section>"


def test_executor_skips_preflight_for_contract_generate_with_user_input(auth_client, enable_claude_agent):
    enable_claude_agent()
    runtime = PreflightScriptRuntime([_complete_action()])
    set_runtime_override(runtime)
    try:
        graph = {
            "agent": "claude",
            "nodes": [
                USER_INPUT_NODE,
                _contract_node(
                    "n_gen",
                    prompt="根据用户输入生成结构化结果。",
                    output_contract=_json_contract("result"),
                ),
            ],
            "edges": [{"id": "e1", "source": "n_input", "target": "n_gen"}],
        }
        app_id = _build_app(auth_client, graph=graph)
        run = auth_client.post("/api/runs", json={"app_id": app_id, "inputs": {"n_input": "农业"}}).json()
        final = _wait_for_terminal(auth_client, run["run_id"])
    finally:
        set_runtime_override(MockRuntime())

    assert final["status"] == "success", final
    assert runtime.preflight_prompts == []
    by_id = {step["node_id"]: step for step in final["steps"]}
    assert by_id["n_gen"]["output"] == {"result": "SCRIPT_RESULT"}


def test_executor_repairs_generate_contract_output_once(auth_client, enable_claude_agent):
    enable_claude_agent()
    original_output = '结果如下：{"title":"ORIGINAL_FACT_125"}\n请查收。'
    runtime = SequenceRuntime([original_output, '{"title":"ORIGINAL_FACT_125"}'])
    set_runtime_override(runtime)
    try:
        graph = {
            "agent": "claude",
            "nodes": [
                _contract_node(
                    "n_gen",
                    prompt="生成结构化结果",
                    output_contract=_json_contract("title"),
                ),
            ],
            "edges": [],
        }
        app_id = _build_app(auth_client, graph=graph)
        run = auth_client.post("/api/runs", json={"app_id": app_id, "inputs": {}}).json()
        final = _wait_for_terminal(auth_client, run["run_id"])
    finally:
        set_runtime_override(MockRuntime())

    assert final["status"] == "success", final
    step = final["steps"][0]
    assert step["output"] == {"title": "ORIGINAL_FACT_125"}
    assert len(runtime.prompts) == 2
    repair_prompt = runtime.prompts[1]
    assert "校验失败原因" in repair_prompt
    assert "生成结构化结果" in repair_prompt
    assert original_output in repair_prompt
    assert "只做通过校验所需的最小结构或格式修正" in repair_prompt
    assert "不要总结、翻译、润色或补充新事实" in repair_prompt
    assert any("输出契约校验失败，尝试自动修正" in log["text"] for log in step["logs"])


def test_executor_fails_when_contract_repair_still_invalid(auth_client, enable_claude_agent):
    enable_claude_agent()
    runtime = SequenceRuntime(["not-json", "still-not-json"])
    set_runtime_override(runtime)
    try:
        graph = {
            "agent": "claude",
            "nodes": [
                _contract_node(
                    "n_gen",
                    prompt="生成结构化结果",
                    output_contract=_json_contract("title"),
                ),
            ],
            "edges": [],
        }
        app_id = _build_app(auth_client, graph=graph)
        run = auth_client.post("/api/runs", json={"app_id": app_id, "inputs": {}}).json()
        final = _wait_for_terminal(auth_client, run["run_id"])
    finally:
        set_runtime_override(MockRuntime())

    assert final["status"] == "failed"
    step = final["steps"][0]
    assert step["status"] == "failed"
    assert "输出契约校验失败" in (step.get("error") or "")


def test_executor_keeps_output_node_html_only(auth_client, enable_claude_agent):
    enable_claude_agent()
    graph = {
        "agent": "claude",
        "nodes": [
            _contract_node(
                "n_gen",
                prompt="生成结构化结果 [[respond:{\"title\":\"OK\"}]]",
                output_contract=_json_contract("title"),
            ),
            _output_node("n_out", source="n_gen", prompt="展示 [[respond:<section>OK</section>]]"),
        ],
        "edges": [{"id": "e1", "source": "n_gen", "target": "n_out"}],
    }
    app_id = _build_app(auth_client, graph=graph)
    run = auth_client.post("/api/runs", json={"app_id": app_id, "inputs": {}}).json()
    final = _wait_for_terminal(auth_client, run["run_id"])

    assert final["status"] == "success", final
    by_id = {step["node_id"]: step for step in final["steps"]}
    assert by_id["n_gen"]["output"] == {"title": "OK"}
    assert by_id["n_out"]["output"] == "<section>OK</section>"
    output_prompt = by_id["n_out"]["input"]["prompt"]
    assert "你正在生成 Mira output 节点的最终预览内容" in output_prompt
    assert "完整呈现上游中与最终结果有关" in output_prompt
    assert "box-sizing: border-box" in output_prompt
    assert "表格和代码在窄屏不得撑破页面" in output_prompt
    assert "不要默认把每项内容都做成卡片" in output_prompt


def test_executor_output_node_accepts_json_wrapped_html(auth_client, enable_claude_agent):
    enable_claude_agent()
    graph = {
        "agent": "claude",
        "nodes": [
            _generate_node("n_gen", prompt="生成 [[respond:UPSTREAM]]"),
            _output_node("n_out", source="n_gen", prompt='展示 [[respond:{"html":"<section>JSON_OK</section>"}]]'),
        ],
        "edges": [{"id": "e1", "source": "n_gen", "target": "n_out"}],
    }
    app_id = _build_app(auth_client, graph=graph)
    run = auth_client.post("/api/runs", json={"app_id": app_id, "inputs": {}}).json()
    final = _wait_for_terminal(auth_client, run["run_id"])

    assert final["status"] == "success", final
    by_id = {step["node_id"]: step for step in final["steps"]}
    assert by_id["n_out"]["output"] == "<section>JSON_OK</section>"


def test_executor_output_node_returns_raw_html_without_sanitizing(auth_client, enable_claude_agent):
    enable_claude_agent()
    html = (
        "<!DOCTYPE html><html lang=\"zh-CN\"><head><meta charset=\"utf-8\">"
        "<style>body { color: red; }</style><script>window.rawHtml = true;</script></head>"
        "<body><img src=\"javascript:alert(1)\" onerror=\"alert(2)\"><section>VISIBLE</section></body></html>"
    )
    graph = {
        "agent": "claude",
        "nodes": [
            _generate_node("n_gen", prompt="生成 [[respond:UPSTREAM]]"),
            _output_node("n_out", source="n_gen", prompt=f"展示 [[respond:{html}]]"),
        ],
        "edges": [{"id": "e1", "source": "n_gen", "target": "n_out"}],
    }
    app_id = _build_app(auth_client, graph=graph)
    run = auth_client.post("/api/runs", json={"app_id": app_id, "inputs": {}}).json()
    final = _wait_for_terminal(auth_client, run["run_id"])

    assert final["status"] == "success", final
    output = {step["node_id"]: step for step in final["steps"]}["n_out"]["output"]
    assert output == html


def test_executor_output_node_rejects_tool_result_html_without_final_html(auth_client, enable_claude_agent):
    enable_claude_agent()
    runtime = ToolResultOnlyHtmlRuntime()
    set_runtime_override(runtime)
    graph = {
        "agent": "claude",
        "nodes": [
            _generate_node("n_gen", prompt="生成"),
            _output_node("n_out", source="n_gen", prompt="展示最终 HTML"),
        ],
        "edges": [{"id": "e1", "source": "n_gen", "target": "n_out"}],
    }

    try:
        app_id = _build_app(auth_client, graph=graph)
        run = auth_client.post("/api/runs", json={"app_id": app_id, "inputs": {}}).json()
        final = _wait_for_terminal(auth_client, run["run_id"])
    finally:
        set_runtime_override(MockRuntime())

    assert final["status"] == "failed", final
    by_id = {step["node_id"]: step for step in final["steps"]}
    assert by_id["n_gen"]["status"] == "success"
    assert by_id["n_out"]["status"] == "failed"
    assert "最终展示节点必须返回可渲染 HTML" in (by_id["n_out"].get("error") or "")
    assert by_id["n_out"].get("output") is None
    execute_calls = [call for call in runtime.calls if call["runtime_policy"] == "execute"]
    assert execute_calls[-1]["allowed_tools"] is None


def test_executor_propagates_failure(auth_client, enable_claude_agent):
    enable_claude_agent()
    graph = {
        "agent": "claude",
        "nodes": [
            _generate_node("n_gen_fail", prompt="please fail-now"),
            _output_node("n_out", source="n_gen_fail"),
        ],
        "edges": [{"id": "e1", "source": "n_gen_fail", "target": "n_out"}],
    }
    app_id = _build_app(auth_client, graph=graph)
    run = auth_client.post("/api/runs", json={"app_id": app_id, "inputs": {}}).json()
    final = _wait_for_terminal(auth_client, run["run_id"])
    assert final["status"] == "failed"
    by_id = {step["node_id"]: step for step in final["steps"]}
    assert by_id["n_gen_fail"]["status"] == "failed"
    assert "mock failed" in (by_id["n_gen_fail"].get("error") or "")
    # 上游失败后下游 step 应保持 pending（未启动）。
    assert by_id["n_out"]["status"] == "pending"


def test_executor_cancel_during_running_step(auth_client, enable_claude_agent):
    enable_claude_agent()
    graph = {
        "agent": "claude",
        "nodes": [
            _generate_node("n_gen_slow", prompt="please [[delay:1.5]] [[respond:LATE]]"),
        ],
        "edges": [],
    }
    app_id = _build_app(auth_client, graph=graph)
    run = auth_client.post("/api/runs", json={"app_id": app_id, "inputs": {}}).json()
    # 等待 step 真正进入 running 状态，再 cancel。
    deadline = time.time() + 2.0
    while time.time() < deadline:
        body = auth_client.get(f"/api/runs/{run['run_id']}").json()
        if body["steps"] and body["steps"][0]["status"] == "running":
            break
        time.sleep(0.05)
    response = auth_client.post(f"/api/runs/{run['run_id']}/cancel")
    assert response.status_code == 204
    final = _wait_for_terminal(auth_client, run["run_id"])
    assert final["status"] == "cancelled"
    assert final["steps"][0]["status"] == "cancelled"


def test_executor_late_success_after_cancel_does_not_overwrite_cancelled_step(auth_client, enable_claude_agent):
    enable_claude_agent()
    runtime = LateSuccessAfterCancelRuntime()
    set_runtime_override(runtime)
    try:
        graph = {
            "agent": "claude",
            "nodes": [
                _generate_node("n_gen_late", prompt="ignore cancel and return late success"),
            ],
            "edges": [],
        }
        app_id = _build_app(auth_client, graph=graph)
        run = auth_client.post("/api/runs", json={"app_id": app_id, "inputs": {}}).json()

        assert runtime.started.wait(timeout=2.0)
        response = auth_client.post(f"/api/runs/{run['run_id']}/cancel")
        assert response.status_code == 204
        assert runtime.cancel_seen.wait(timeout=2.0)
        assert runtime.returned.wait(timeout=2.0)
        time.sleep(0.2)

        final = auth_client.get(f"/api/runs/{run['run_id']}").json()
        assert final["status"] == "cancelled"
        step = final["steps"][0]
        assert step["status"] == "cancelled"
        assert step["output"] is None
        assert step["agent_session_id"] is None
    finally:
        set_runtime_override(MockRuntime())


def test_executor_passes_upstream_output_into_generate_prompt(auth_client, enable_claude_agent):
    """generate 节点应该能在自己的 prompt 中看到上游 asset 的 output。"""

    enable_claude_agent()
    graph = {
        "agent": "claude",
        "nodes": [
            {
                "id": "n_asset",
                "type": "asset",
                "position": {"x": 0, "y": 0},
                "title": "Asset",
                "asset_kind": "text",
                "content": "UPSTREAM_MARK",
            },
            _generate_node("n_gen", prompt="请回显上游内容 [[respond:UPSTREAM_MARK]]"),
        ],
        "edges": [{"id": "e1", "source": "n_asset", "target": "n_gen"}],
    }
    app_id = _build_app(auth_client, graph=graph)
    run = auth_client.post("/api/runs", json={"app_id": app_id, "inputs": {}}).json()
    final = _wait_for_terminal(auth_client, run["run_id"])
    assert final["status"] == "success"
    by_id = {step["node_id"]: step for step in final["steps"]}
    # generate 节点的 input_json 应该体现 _compose_prompt 拼出来的完整 prompt，
    # 其中要包含上游 asset 的 output 文本。
    prompt = by_id["n_gen"]["input"]["prompt"]
    assert "UPSTREAM_MARK" in prompt
    assert "你拥有一个名为 `ask_user` 的工具" not in prompt
    assert "# 用户决策摘要" in prompt
    assert "不要再次调用 ask_user" in prompt


def test_executor_output_primary_input_is_not_duplicated(auth_client, enable_claude_agent):
    enable_claude_agent()
    saved = auth_client.put(
        "/api/settings/prompts/output_html_rendering",
        json={"content": "$user_prompt\n\nMIRA_DB_HTML_RULE"},
    )
    assert saved.status_code == 200, saved.text
    graph = {
        "agent": "claude",
        "nodes": [
            {
                "id": "n_asset",
                "type": "asset",
                "position": {"x": 0, "y": 0},
                "title": "Asset",
                "asset_kind": "text",
                "content": "OTHER_MARK",
            },
            _generate_node("n_gen", prompt="生成主输入 [[respond:PRIMARY_MARK]]"),
            _output_node("n_out", source="n_gen", prompt="整理 [[respond:<section>FINAL</section>]]"),
        ],
        "edges": [
            {"id": "e1", "source": "n_asset", "target": "n_out"},
            {"id": "e2", "source": "n_gen", "target": "n_out"},
        ],
    }
    app_id = _build_app(auth_client, graph=graph)
    run = auth_client.post("/api/runs", json={"app_id": app_id, "inputs": {}}).json()
    final = _wait_for_terminal(auth_client, run["run_id"])
    assert final["status"] == "success", final
    by_id = {step["node_id"]: step for step in final["steps"]}
    prompt = by_id["n_out"]["input"]["prompt"]
    assert prompt.count("PRIMARY_MARK") == 1
    assert "OTHER_MARK" in prompt
    assert "你拥有一个名为 `ask_user` 的工具" not in prompt
    assert "MIRA_DB_HTML_RULE" in prompt
    assert prompt.index("整理 [[respond:<section>FINAL</section>]]") < prompt.index("MIRA_DB_HTML_RULE")


def test_executor_file_asset_outputs_upload_meta_list(auth_client, enable_claude_agent):
    enable_claude_agent()
    uploaded_a = auth_client.post(
        "/api/uploads",
        files={"file": ("asset-a.txt", b"asset a", "text/plain")},
    )
    uploaded_b = auth_client.post(
        "/api/uploads",
        files={"file": ("asset-b.md", b"# asset b", "text/markdown")},
    )
    assert uploaded_a.status_code == 200, uploaded_a.text
    assert uploaded_b.status_code == 200, uploaded_b.text
    upload_a = uploaded_a.json()
    upload_b = uploaded_b.json()
    graph = {
        "agent": "claude",
        "nodes": [
            {
                "id": "n_asset",
                "type": "asset",
                "position": {"x": 0, "y": 0},
                "title": "Asset File",
                "asset_kind": "file",
                "uploads": [upload_a, upload_b],
            },
        ],
        "edges": [],
    }
    app_id = _build_app(auth_client, graph=graph)
    run = auth_client.post("/api/runs", json={"app_id": app_id, "inputs": {}}).json()
    final = _wait_for_terminal(auth_client, run["run_id"])
    assert final["status"] == "success", final
    step = final["steps"][0]
    assert step["status"] == "success"
    assert [item["id"] for item in step["output"]] == [upload_a["id"], upload_b["id"]]
    assert step["output"][0]["name"] == "asset-a.txt"
    assert step["output"][0]["mime"] == "text/plain"
    assert step["output"][0]["size"] == len(b"asset a")
    assert step["output"][0]["path"] is None
    assert step["output"][0]["download_url"].startswith(f"/api/uploads/{upload_a['id']}?download_token=")
    assert step["output"][1]["name"] == "asset-b.md"
    assert step["input"]["uploads"][1]["download_url"].startswith(f"/api/uploads/{upload_b['id']}?download_token=")
    assert step["input"]["uploads"][1]["path"] is None
    assert str(uploads_dir("user_admin") / upload_a["id"] / "blob") not in json.dumps(final)


def test_executor_url_asset_outputs_url_list(auth_client, enable_claude_agent):
    enable_claude_agent()
    graph = {
        "agent": "claude",
        "nodes": [
            {
                "id": "n_asset",
                "type": "asset",
                "position": {"x": 0, "y": 0},
                "title": "Links",
                "asset_kind": "url",
                "urls": [" https://example.com/a ", "", "https://example.com/b"],
            },
        ],
        "edges": [],
    }
    app_id = _build_app(auth_client, graph=graph)
    run = auth_client.post("/api/runs", json={"app_id": app_id, "inputs": {}}).json()
    final = _wait_for_terminal(auth_client, run["run_id"])
    assert final["status"] == "success", final
    step = final["steps"][0]
    assert step["status"] == "success"
    assert step["output"] == ["https://example.com/a", "https://example.com/b"]
    assert step["input"] == {"asset_kind": "url", "urls": ["https://example.com/a", "https://example.com/b"]}


def test_executor_user_input_attachment_prompt_includes_download_url(auth_client, enable_claude_agent):
    enable_claude_agent()
    uploaded = auth_client.post(
        "/api/uploads",
        files={"file": ("reference.txt", b"reference content", "text/plain")},
    )
    assert uploaded.status_code == 200, uploaded.text
    upload = uploaded.json()
    graph = {
        "agent": "claude",
        "nodes": [
            USER_INPUT_NODE,
            _generate_node("n_gen", prompt="读取附件 [[respond:DONE]]"),
        ],
        "edges": [{"id": "e1", "source": "n_input", "target": "n_gen"}],
    }
    app_id = _build_app(auth_client, graph=graph)
    run = auth_client.post(
        "/api/runs",
        json={
            "app_id": app_id,
            "inputs": {
                "n_input": {
                    "value": "please use the attachment",
                    "attachments": [{"id": upload["id"], "name": "reference.txt"}],
                }
            },
        },
    ).json()
    final = _wait_for_terminal(auth_client, run["run_id"])
    assert final["status"] == "success", final
    by_id = {step["node_id"]: step for step in final["steps"]}
    input_output = by_id["n_input"]["output"]
    prompt = by_id["n_gen"]["input"]["prompt"]
    assert input_output["attachments"][0]["download_url"].startswith(
        f"/api/uploads/{upload['id']}?download_token="
    )
    assert input_output["attachments"][0]["path"] is None
    assert f'"/api/uploads/{upload["id"]}?download_token=' in prompt
    assert '"path"' in prompt
    assert str(uploads_dir("user_admin") / upload["id"] / "blob") not in json.dumps(final)


def test_executor_replaces_workspace_paths_with_signed_download_urls(auth_client, enable_claude_agent):
    enable_claude_agent()
    graph = {
        "agent": "claude",
        "nodes": [
            _generate_node("n_gen", prompt="生成文件路径 [[respond:PLACEHOLDER]]"),
            _output_node("n_out", source="n_gen", prompt="展示 [[respond:<section>FINAL</section>]]"),
        ],
        "edges": [{"id": "e1", "source": "n_gen", "target": "n_out"}],
    }
    app_id = _build_app(auth_client, graph=graph)
    runtime = WorkspacePathRuntime()
    set_runtime_override(runtime)
    try:
        run = auth_client.post("/api/runs", json={"app_id": app_id, "inputs": {}}).json()
        run_id = run["run_id"]
        final = _wait_for_terminal(auth_client, run_id)
    finally:
        set_runtime_override(MockRuntime())

    artifact_path = str(
        run_workspace("user_admin", app_id, run_id) / "deliverable.zip"
    )
    assert final["status"] == "success", final
    by_id = {step["node_id"]: step for step in final["steps"]}
    gen_prompt = by_id["n_out"]["input"]["prompt"]
    output_html = by_id["n_out"]["output"]
    assert artifact_path not in gen_prompt
    assert artifact_path not in output_html
    assert "deliverable.zip (download_url: /api/runs/" in gen_prompt
    assert '<a href="/api/runs/' in output_html
    assert "download_token=" in output_html

    artifacts_response = auth_client.get(f"/api/runs/{run_id}/artifacts")
    assert artifacts_response.status_code == 200, artifacts_response.text
    artifacts_body = artifacts_response.json()
    assert artifacts_body["truncated"] is False
    assert len(artifacts_body["artifacts"]) == 1
    artifact = artifacts_body["artifacts"][0]
    assert artifact["name"] == "deliverable.zip"
    assert artifact["path"] == "deliverable.zip"
    assert artifact["size"] == len(b"artifact")
    assert artifact["source_kind"] == "workspace_file"
    assert artifact["source_node_id"] is None
    assert artifact["download_url"].startswith(f"/api/runs/{run_id}/artifacts/deliverable.zip")
    assert "/runtime/workspaces/" not in artifact["download_url"]

    href = output_html.split('href="', 1)[1].split('"', 1)[0].replace("&amp;", "&")
    headers = dict(auth_client.headers)
    auth_client.headers.pop("Authorization", None)
    try:
        downloaded = auth_client.get(href)
    finally:
        auth_client.headers.update(headers)
    assert downloaded.status_code == 200, downloaded.text
    assert downloaded.content == b"artifact"


def test_run_artifacts_list_uses_artifact_contract_metadata(auth_client, enable_claude_agent):
    enable_claude_agent()
    graph = {
        "agent": "claude",
        "nodes": [
            _contract_node(
                "n_gen",
                prompt="生成文件产物",
                output_contract={"type": "artifact", "artifact_kind": "file"},
            ),
        ],
        "edges": [],
    }
    app_id = _build_app(auth_client, graph=graph)
    runtime = ArtifactContractRuntime()
    set_runtime_override(runtime)
    try:
        run = auth_client.post("/api/runs", json={"app_id": app_id, "inputs": {}}).json()
        run_id = run["run_id"]
        final = _wait_for_terminal(auth_client, run_id)
    finally:
        set_runtime_override(MockRuntime())

    assert final["status"] == "success", final
    response = auth_client.get(f"/api/runs/{run_id}/artifacts")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["truncated"] is False
    assert len(body["artifacts"]) == 1
    artifacts = {artifact["name"]: artifact for artifact in body["artifacts"]}
    artifact = artifacts["Report"]
    assert artifact["name"] == "Report"
    assert artifact["path"] == "report.txt"
    assert artifact["size"] == len("artifact report".encode("utf-8"))
    assert artifact["source_kind"] == "artifact_contract"
    assert artifact["source_node_id"] == "n_gen"
    assert artifact["source_node_title"] == "n_gen"
    assert artifact["mime"] == "text/plain"
    assert artifact["download_url"].startswith(f"/api/runs/{run_id}/artifacts/report.txt")
    assert "/tmp/private/export.pdf" not in json.dumps(body)
    assert "javascript:" not in json.dumps(body)


def test_file_asset_missing_upload_is_rejected_when_saving_graph(auth_client):
    graph = {
        "agent": "claude",
        "nodes": [
            {
                "id": "n_asset",
                "type": "asset",
                "position": {"x": 0, "y": 0},
                "title": "Asset File",
                "asset_kind": "file",
                "uploads": [{"id": "upl_missing", "name": "missing.txt"}],
            },
        ],
        "edges": [],
    }
    created = auth_client.post("/api/apps", json={"name": "RunExecApp"}).json()
    response = auth_client.patch(f"/api/apps/{created['id']}", json={"graph": _ensure_output(graph)})
    assert response.status_code == 400
    assert response.json()["detail"] == "素材上传文件不存在"
