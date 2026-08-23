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
from app.services.artifacts import file_sha256
from app.services import node_handlers, output_contracts, run_orchestrator
from app.services.office_documents import OfficeValidationUnavailable
from app.services.run_orchestrator import start_run
from app.services.runtime_paths import run_workspace, uploads_dir
from app.runtime.base import AgentChunk, AgentExecutionResult, AgentRuntimeStatus, AskUserRequest
from app.runtime.factory import set_runtime_override
from app.schemas.decision import DecisionGroup
from app.services.admin import ADMIN_USER_ID
from app.services.node_handlers import NodeResult
from app.services.workflow_data import WorkflowDataIntegrityError
from app.utils import dumps, loads, now_utc
from tests.runtime_mock import MockRuntime, _merge_workspace


class ReasoningCaptureRuntime:
    def __init__(self) -> None:
        self.calls: list[dict[str, str | list[str] | None]] = []

    async def detect_status(self) -> AgentRuntimeStatus:
        return AgentRuntimeStatus(
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
        model: str | None,
        reasoning_effort: str | None,
        cwd: Path,
        on_chunk,
        cancel_event: asyncio.Event,
        on_ask_user=None,
        runtime_tools=None,
        runtime_policy="execute",
        output_schema=None,
        session_scope=None,
        fork_session=False,
    ) -> AgentExecutionResult:
        self.calls.append(
            {
                "model": model,
                "reasoning_effort": reasoning_effort,
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

    async def detect_status(self) -> AgentRuntimeStatus:
        return AgentRuntimeStatus(
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
        model: str | None,
        reasoning_effort: str | None,
        cwd: Path,
        on_chunk,
        cancel_event: asyncio.Event,
        on_ask_user=None,
        runtime_tools=None,
        runtime_policy="execute",
        output_schema=None,
        session_scope=None,
        fork_session=False,
    ) -> AgentExecutionResult:
        if "你是 Mira RunAgent 的 fan-in 合并协调 Agent" in prompt:
            text = _merge_workspace(cwd)
            await on_chunk(AgentChunk(type="text", text=text))
            return AgentExecutionResult(
                session_id=f"{session_id or 'probe'}_join",
                total_text=text,
                finished_with="done",
            )
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
    async def detect_status(self) -> AgentRuntimeStatus:
        return AgentRuntimeStatus(
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
        model: str | None,
        reasoning_effort: str | None,
        cwd: Path,
        on_chunk,
        cancel_event: asyncio.Event,
        on_ask_user=None,
        runtime_tools=None,
        runtime_policy="execute",
        output_schema=None,
        session_scope=None,
        fork_session=False,
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


class GeneratedImageRuntime:
    async def detect_status(self) -> AgentRuntimeStatus:
        return AgentRuntimeStatus(
            installed=True,
            runnable=True,
            identity="generated-image",
            method="test",
            checked_at=now_utc(),
        )

    async def execute(
        self,
        *,
        prompt: str,
        session_id: str | None,
        model: str | None,
        reasoning_effort: str | None,
        cwd: Path,
        on_chunk,
        cancel_event: asyncio.Event,
        on_ask_user=None,
        runtime_tools=None,
        runtime_policy="execute",
        output_schema=None,
        session_scope=None,
        fork_session=False,
    ) -> AgentExecutionResult:
        if runtime_policy == "ask_user_plan":
            text = '{"action":"complete","decision_summary":"无需额外提问。","reason":"测试场景不需要补充用户决策。"}'
            await on_chunk(AgentChunk(type="text", text=text))
            return AgentExecutionResult(session_id=session_id, total_text=text, finished_with="done")
        if "你正在生成 Mira output 节点" in prompt:
            assert (cwd / "cover.png").read_bytes() == b"png-bytes"
            text = _structured_text("<section>FINAL</section>", output_schema)
            await on_chunk(AgentChunk(type="text", text=text))
            return AgentExecutionResult(
                session_id=session_id or "generated_image_session",
                total_text=text,
                finished_with="done",
            )
        image = cwd / "cover.png"
        image.write_bytes(b"png-bytes")
        text = json.dumps(
            {
                "artifacts": [{"name": "cover.png", "path": str(image)}]
            },
            ensure_ascii=False,
        )
        await on_chunk(AgentChunk(type="text", text=text))
        return AgentExecutionResult(
            session_id=session_id or "generated_image_session",
            total_text=text,
            finished_with="done",
        )


class ToolResultOnlyHtmlRuntime:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def detect_status(self) -> AgentRuntimeStatus:
        return AgentRuntimeStatus(
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
        model: str | None,
        reasoning_effort: str | None,
        cwd: Path,
        on_chunk,
        cancel_event: asyncio.Event,
        on_ask_user=None,
        runtime_tools=None,
        runtime_policy="execute",
        output_schema=None,
        session_scope=None,
        fork_session=False,
    ) -> AgentExecutionResult:
        self.calls.append(
            {
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
    async def detect_status(self) -> AgentRuntimeStatus:
        return AgentRuntimeStatus(
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
        model: str | None,
        reasoning_effort: str | None,
        cwd: Path,
        on_chunk,
        cancel_event: asyncio.Event,
        on_ask_user=None,
        runtime_tools=None,
        runtime_policy="execute",
        output_schema=None,
        session_scope=None,
        fork_session=False,
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


def _minimal_docx_bytes() -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as document:
        document.writestr(
            "[Content_Types].xml",
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Override PartName="/word/document.xml" ContentType="application/xml"/>'
            "</Types>",
        )
        document.writestr(
            "word/document.xml",
            '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
            "<w:body><w:p/></w:body></w:document>",
        )
    return buffer.getvalue()


class OfficeArtifactRepairRuntime:
    def __init__(self) -> None:
        self.prompts: list[str] = []

    async def detect_status(self) -> AgentRuntimeStatus:
        return AgentRuntimeStatus(
            installed=True,
            runnable=True,
            identity="office-artifact-repair",
            method="test",
            checked_at=now_utc(),
        )

    async def execute(
        self,
        *,
        prompt: str,
        session_id: str | None,
        model: str | None,
        reasoning_effort: str | None,
        cwd: Path,
        on_chunk,
        cancel_event: asyncio.Event,
        on_ask_user=None,
        runtime_tools=None,
        runtime_policy="execute",
        output_schema=None,
        session_scope=None,
        fork_session=False,
    ) -> AgentExecutionResult:
        if runtime_policy == "ask_user_plan":
            text = '{"action":"complete","decision_summary":"无需额外提问。","reason":"测试无需用户决策。"}'
            await on_chunk(AgentChunk(type="text", text=text))
            return AgentExecutionResult(session_id=session_id, total_text=text, finished_with="done")
        if _is_output_prompt(prompt):
            text = _structured_text("<section>OK</section>", output_schema)
            await on_chunk(AgentChunk(type="text", text=text))
            return AgentExecutionResult(
                session_id=session_id or "office_artifact_repair_session",
                total_text=text,
                finished_with="done",
            )
        self.prompts.append(prompt)
        artifact = cwd / "documents.zip"
        if not artifact.exists():
            with zipfile.ZipFile(artifact, "w") as bundle:
                bundle.writestr("document.docx", _minimal_docx_bytes())
        text = json.dumps(
            {"artifacts": [{"name": "Documents", "path": str(artifact)}]},
            ensure_ascii=False,
        )
        await on_chunk(AgentChunk(type="text", text=text))
        return AgentExecutionResult(
            session_id=session_id or "office_artifact_repair_session",
            total_text=text,
            finished_with="done",
        )


class SequenceRuntime:
    def __init__(self, outputs: list[str]) -> None:
        self.outputs = outputs
        self.prompts: list[str] = []
        self.session_ids: list[str | None] = []

    async def detect_status(self) -> AgentRuntimeStatus:
        return AgentRuntimeStatus(
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
        model: str | None,
        reasoning_effort: str | None,
        cwd: Path,
        on_chunk,
        cancel_event: asyncio.Event,
        on_ask_user=None,
        runtime_tools=None,
        runtime_policy="execute",
        output_schema=None,
        session_scope=None,
        fork_session=False,
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
        self.session_ids.append(session_id)
        self.prompts.append(prompt)
        text = self.outputs.pop(0) if self.outputs else ""
        await on_chunk(AgentChunk(type="text", text=text))
        return AgentExecutionResult(
            session_id=session_id or f"sequence_session_{len(self.prompts)}",
            total_text=text,
            finished_with="done",
        )


class OutputContractRepairRuntime:
    def __init__(self) -> None:
        self.execute_calls: list[dict[str, Any]] = []

    async def detect_status(self) -> AgentRuntimeStatus:
        return AgentRuntimeStatus(
            installed=True,
            runnable=True,
            identity="output-contract-repair",
            method="test",
            checked_at=now_utc(),
        )

    async def execute(self, **kwargs) -> AgentExecutionResult:
        if kwargs["runtime_policy"] == "ask_user_plan":
            text = '{"action":"complete","decision_summary":"无需额外提问。","reason":"测试场景不需要补充用户决策。"}'
            return AgentExecutionResult(
                session_id=kwargs["session_id"],
                total_text=text,
                finished_with="done",
            )
        self.execute_calls.append(
            {
                "prompt": kwargs["prompt"],
                "session_id": kwargs["session_id"],
                "cwd": kwargs["cwd"],
                "output_schema": kwargs["output_schema"],
            }
        )
        call_number = len(self.execute_calls)
        text = {
            1: "UPSTREAM",
            2: "这不是 HTML 契约输出",
            3: '{"html":"<section>RECOVERED</section>"}',
        }[call_number]
        await kwargs["on_chunk"](AgentChunk(type="text", text=text))
        return AgentExecutionResult(
            session_id=kwargs["session_id"] or "output_repair_session",
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

    async def detect_status(self) -> AgentRuntimeStatus:
        return AgentRuntimeStatus(
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
        model: str | None,
        reasoning_effort: str | None,
        cwd: Path,
        on_chunk,
        cancel_event: asyncio.Event,
        on_ask_user=None,
        runtime_tools=None,
        runtime_policy="execute",
        output_schema=None,
        session_scope=None,
        fork_session=False,
    ) -> AgentExecutionResult:
        if "你是 Mira RunAgent 的 fan-in 合并协调 Agent" in prompt:
            text = _merge_workspace(cwd)
            await on_chunk(AgentChunk(type="text", text=text))
            return AgentExecutionResult(
                session_id=f"{session_id or 'probe'}_join",
                total_text=text,
                finished_with="done",
            )
        if runtime_policy == "ask_user_plan":
            self._next_session += 1
            next_session = (
                f"{session_id}_fork_{self._next_session}"
                if fork_session and session_id
                else session_id or f"probe_session_{self._next_session}"
            )
            self.execute_calls.append(
                {
                    "prompt": prompt,
                    "session_id": session_id,
                    "cwd": str(cwd),
                    "session_scope": session_scope,
                    "fork_session": str(fork_session),
                    "runtime_policy": runtime_policy,
                }
            )
            text = '{"action":"complete","decision_summary":"无需额外提问。","reason":"测试场景不需要补充用户决策。"}'
            await on_chunk(AgentChunk(type="text", text=text))
            return AgentExecutionResult(session_id=next_session, total_text=text, finished_with="done")
        async with self._lock:
            self.active += 1
            self.max_active = max(self.max_active, self.active)
            self.execute_calls.append(
                {
                    "prompt": prompt,
                    "session_id": session_id,
                    "cwd": str(cwd),
                    "session_scope": session_scope,
                    "fork_session": str(fork_session),
                    "runtime_policy": runtime_policy,
                }
            )
            self._next_session += 1
            next_session = (
                f"{session_id}_fork_{self._next_session}"
                if fork_session and session_id
                else session_id or f"probe_session_{self._next_session}"
            )
        try:
            await asyncio.sleep(self.delay)
            text = _structured_text(_respond_marker(prompt) or f"probe:{self._next_session}", output_schema)
            await on_chunk(AgentChunk(type="text", text=text))
            return AgentExecutionResult(session_id=next_session, total_text=text, finished_with="done")
        finally:
            async with self._lock:
                self.active -= 1


class SharedWorkspaceRuntime:
    async def detect_status(self) -> AgentRuntimeStatus:
        return AgentRuntimeStatus(
            installed=True,
            runnable=True,
            identity="shared-workspace",
            method="test",
            checked_at=now_utc(),
        )

    async def execute(self, **kwargs) -> AgentExecutionResult:
        prompt = kwargs["prompt"]
        session_id = kwargs["session_id"]
        cwd = kwargs["cwd"]
        on_chunk = kwargs["on_chunk"]
        if kwargs["runtime_policy"] == "ask_user_plan":
            text = '{"action":"complete","decision_summary":"无需额外提问。","reason":"测试场景不需要补充用户决策。"}'
        elif "write-private-file" in prompt:
            (cwd / "private-upstream.txt").write_text("implicit context", encoding="utf-8")
            text = "A"
        elif "read-private-file" in prompt:
            assert (cwd / "private-upstream.txt").read_text(encoding="utf-8") == "implicit context"
            text = "B"
        else:
            text = _structured_text("<section>DONE</section>", kwargs["output_schema"])
        await on_chunk(AgentChunk(type="text", text=text))
        return AgentExecutionResult(
            session_id=session_id or "shared_workspace_session",
            total_text=text,
            finished_with="done",
        )


class CheckpointRerunWorkspaceRuntime:
    def __init__(self) -> None:
        self.rerun_cut_initial_state: str | None = None

    async def detect_status(self) -> AgentRuntimeStatus:
        return AgentRuntimeStatus(
            installed=True,
            runnable=True,
            identity="checkpoint-rerun-workspace",
            method="test",
            checked_at=now_utc(),
        )

    async def execute(self, **kwargs) -> AgentExecutionResult:
        prompt = kwargs["prompt"]
        session_id = kwargs["session_id"]
        cwd = kwargs["cwd"]
        on_chunk = kwargs["on_chunk"]
        fork_session = kwargs["fork_session"]
        if "你是 Mira RunAgent 的 fan-in 合并协调 Agent" in prompt:
            text = _merge_workspace(cwd)
        elif kwargs["runtime_policy"] == "ask_user_plan":
            text = '{"action":"complete","decision_summary":"无需额外提问。","reason":"测试场景不需要补充用户决策。"}'
        elif "checkpoint-before" in prompt:
            (cwd / "checkpoint-state.txt").write_text("BEFORE_CUT", encoding="utf-8")
            text = "BEFORE"
        elif "source-cut" in prompt:
            (cwd / "checkpoint-state.txt").write_text("SOURCE_CUT", encoding="utf-8")
            text = "SOURCE_CUT"
        elif "source-after-cut" in prompt:
            (cwd / "checkpoint-state.txt").write_text("AFTER_CUT", encoding="utf-8")
            text = "AFTER_CUT"
        elif "rerun-cut" in prompt:
            self.rerun_cut_initial_state = (cwd / "checkpoint-state.txt").read_text(encoding="utf-8")
            (cwd / "checkpoint-state.txt").write_text("RERUN_CUT", encoding="utf-8")
            text = "RERUN_CUT"
        else:
            text = _structured_text("<section>DONE</section>", kwargs["output_schema"])
        await on_chunk(AgentChunk(type="text", text=text))
        next_session_id = session_id or "checkpoint_rerun_session"
        if fork_session and session_id:
            next_session_id = f"{session_id}_fork"
        return AgentExecutionResult(
            session_id=next_session_id,
            total_text=text,
            finished_with="done",
        )


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


def _read_workspace_context(cwd: Path) -> str:
    context_root = cwd / ".mira" / "run-context"
    if not context_root.is_dir():
        return ""
    return "\n".join(
        path.read_text(encoding="utf-8")
        for path in sorted(context_root.glob("*.json"))
    )


class AskUserJudgmentRuntime:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.plan_prompts: list[str] = []
        self.ask_calls = 0

    async def detect_status(self) -> AgentRuntimeStatus:
        return AgentRuntimeStatus(
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
        model: str | None,
        reasoning_effort: str | None,
        cwd: Path,
        on_chunk,
        cancel_event: asyncio.Event,
        on_ask_user=None,
        runtime_tools=None,
        runtime_policy="execute",
        output_schema=None,
        session_scope=None,
        fork_session=False,
    ) -> AgentExecutionResult:
        self.calls.append(runtime_policy)
        if runtime_policy == "ask_user_plan":
            self.plan_prompts.append(prompt)
            context = prompt + "\n" + _read_workspace_context(cwd)
            should_ask = "我不知道看啥" in context and "直接推荐" not in context
            summary = "无需额外提问。"
            if should_ask and on_ask_user is not None:
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
                result = await on_ask_user(request)
                if not result.ok:
                    return AgentExecutionResult(
                        session_id=session_id,
                        total_text="",
                        finished_with="error",
                        error=result.error,
                    )
                summary = "已根据用户回答收敛阅读偏好。"
            text = json.dumps(
                {"decision_summary": summary, "reason": "测试 planning turn 已完成决策判断。"},
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


class PlanningCaptureRuntime:
    def __init__(self) -> None:
        self.plan_prompts: list[str] = []

    async def detect_status(self) -> AgentRuntimeStatus:
        return AgentRuntimeStatus(
            installed=True,
            runnable=True,
            identity="planning-capture",
            method="test",
            checked_at=now_utc(),
        )

    async def execute(
        self,
        *,
        prompt: str,
        session_id: str | None,
        model: str | None,
        reasoning_effort: str | None,
        cwd: Path,
        on_chunk,
        cancel_event: asyncio.Event,
        on_ask_user=None,
        runtime_tools=None,
        runtime_policy="execute",
        output_schema=None,
        session_scope=None,
        fork_session=False,
    ) -> AgentExecutionResult:
        if runtime_policy == "ask_user_plan":
            self.plan_prompts.append(prompt)
            text = json.dumps(
                {"decision_summary": "无需额外提问。", "reason": "测试输入信息充分。"},
                ensure_ascii=False,
            )
        elif _is_output_prompt(prompt):
            text = _structured_text("<section>OK</section>", output_schema)
        else:
            text = json.dumps({"result": "SCRIPT_RESULT"}) if output_schema is not None else "SCRIPT_RESULT"
        await on_chunk(AgentChunk(type="text", text=text))
        return AgentExecutionResult(
            session_id=session_id or "planning_capture_session",
            total_text=text,
            finished_with="done",
        )


class ParallelTemplateRuntime:
    def __init__(self) -> None:
        self.plan_prompts: list[str] = []
        self.execute_prompts: list[str] = []

    async def detect_status(self) -> AgentRuntimeStatus:
        return AgentRuntimeStatus(
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
        model: str | None,
        reasoning_effort: str | None,
        cwd: Path,
        on_chunk,
        cancel_event: asyncio.Event,
        on_ask_user=None,
        runtime_tools=None,
        runtime_policy="execute",
        output_schema=None,
        session_scope=None,
        fork_session=False,
    ) -> AgentExecutionResult:
        if "你是 Mira RunAgent 的 fan-in 合并协调 Agent" in prompt:
            text = _merge_workspace(cwd)
            await on_chunk(AgentChunk(type="text", text=text))
            return AgentExecutionResult(
                session_id=session_id or "parallel_template_join_session",
                total_text=text,
                finished_with="done",
            )
        context = prompt + "\n" + _read_workspace_context(cwd)
        if runtime_policy == "ask_user_plan":
            self.plan_prompts.append(prompt)
            summary = "当前节点不需要额外提问。"
            if "并行示例：必须先调用 ask_user" in context and on_ask_user is not None:
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
                result = await on_ask_user(request)
                if not result.ok:
                    return AgentExecutionResult(
                        session_id=session_id,
                        total_text="",
                        finished_with="error",
                        error=result.error,
                    )
                summary = "已确认目标受众和输出风格。"
            text = json.dumps(
                {"decision_summary": summary, "reason": "测试 planning turn 已完成决策判断。"},
                ensure_ascii=False,
            )
            await on_chunk(AgentChunk(type="text", text=text))
            return AgentExecutionResult(session_id=session_id, total_text=text, finished_with="done")

        self.execute_prompts.append(prompt)
        if _is_output_prompt(prompt):
            text = _structured_text("<section>FINAL_HTML</section>", output_schema)
        elif "并行拆解" in context:
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
        next_graph.setdefault("execution_edges", []).append({"id": "e_auto_out", "source": source, "target": "n_auto_out"})
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


def test_executor_runs_input_generate_output(auth_client, configure_codex):
    configure_codex()
    graph = {
        "nodes": [
            USER_INPUT_NODE,
            _generate_node("n_gen", prompt="请生成 [[respond:GENERATED-TEXT]]"),
            _output_node("n_out", source="n_gen", prompt="渲染 [[respond:<section>FINAL-HTML</section>]]"),
        ],
        "execution_edges": [
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
    assert "agent_session_id" not in by_id["n_gen"]
    assert by_id["n_out"]["status"] == "success"
    assert by_id["n_out"]["output"] == "<section>FINAL-HTML</section>"
    assert "agent_session_id" not in by_id["n_out"]


def test_run_cannot_succeed_when_output_step_is_skipped(auth_client, configure_codex, monkeypatch):
    configure_codex()
    graph = {
        "nodes": [
            _generate_node("n_gen", prompt="生成 [[respond:READY]]"),
            _output_node("n_out", source="n_gen", prompt="展示 [[respond:<section>OK</section>]]"),
        ],
        "execution_edges": [{"id": "e1", "source": "n_gen", "target": "n_out"}],
    }
    original_run_node = run_orchestrator.run_node

    async def skip_output(ctx, node, step):
        if node.get("type") == "output":
            return NodeResult(status="skipped")
        return await original_run_node(ctx, node, step)

    monkeypatch.setattr(run_orchestrator, "run_node", skip_output)
    app_id = _build_app(auth_client, graph=graph)
    created = auth_client.post("/api/runs", json={"app_id": app_id, "inputs": {}})
    final = _wait_for_terminal(auth_client, created.json()["run_id"])

    assert final["status"] == "failed"
    assert final["failure_kind"] == "routing"
    assert "最终输出节点未执行成功" in final["error"]
    assert next(step for step in final["steps"] if step["node_id"] == "n_out")["status"] == "skipped"


def test_executor_runs_independent_generate_nodes_concurrently(auth_client, configure_codex):
    configure_codex()
    runtime = ParallelProbeRuntime(delay=0.25)
    set_runtime_override(runtime)
    graph = {
        "nodes": [
            _generate_node("n_gen_a", prompt="A [[respond:A]]"),
            _generate_node("n_gen_b", prompt="B [[respond:B]]"),
            _output_node("n_out", source="n_gen_a", prompt="merge [[respond:<section>DONE</section>]]"),
        ],
        "execution_edges": [
            {"id": "e1", "source": "n_gen_a", "target": "n_out"},
            {"id": "e2", "source": "n_gen_b", "target": "n_out"},
        ],
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


def test_executor_waits_for_parallel_upstreams_before_merge(auth_client, configure_codex):
    configure_codex()
    graph = {
        "nodes": [
            _generate_node("n_gen_a", prompt="A [[delay:0.2]] [[respond:A_OUT]]"),
            _generate_node("n_gen_b", prompt="B [[delay:0.2]] [[respond:B_OUT]]"),
            _output_node("n_out", source="n_gen_a", prompt="merge [[respond:<section>MERGED</section>]]"),
        ],
        "execution_edges": [
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
    prompt = by_id["n_out"]["input"]["prompt"]
    assert "A_OUT" not in prompt
    assert "B_OUT" not in prompt
    assert "/mnt/results" not in prompt
    assert not (run_workspace(ADMIN_USER_ID, app_id, run["run_id"]) / "nodes").exists()
    assert by_id["n_out"]["started_at"] >= by_id["n_gen_a"]["finished_at"]
    assert by_id["n_out"]["started_at"] >= by_id["n_gen_b"]["finished_at"]


def test_executor_does_not_start_new_downstream_after_parallel_failure(auth_client, configure_codex):
    configure_codex()
    graph = {
        "nodes": [
            _generate_node("n_gen_fail", prompt="please fail-now"),
            _generate_node("n_gen_slow", prompt="please [[delay:0.2]] [[respond:SLOW_OK]]"),
            _output_node("n_out", source="n_gen_slow"),
        ],
        "execution_edges": [
            {"id": "e1", "source": "n_gen_slow", "target": "n_out"},
            {"id": "e2", "source": "n_gen_fail", "target": "n_out"},
        ],
    }
    app_id = _build_app(auth_client, graph=graph)
    run = auth_client.post("/api/runs", json={"app_id": app_id, "inputs": {}}).json()
    final = _wait_for_terminal(auth_client, run["run_id"])
    by_id = {step["node_id"]: step for step in final["steps"]}
    assert final["status"] == "failed"
    assert by_id["n_gen_fail"]["status"] == "failed"
    assert by_id["n_gen_slow"]["status"] == "cancelled"
    assert by_id["n_out"]["status"] == "pending"


def test_rerun_from_parallel_failure_reuses_only_ancestors(auth_client, configure_codex):
    configure_codex()
    graph = {
        "nodes": [
            USER_INPUT_NODE,
            _generate_node("n_gen_fail", prompt="please fail-now"),
            _generate_node("n_gen_sibling", prompt="sibling [[delay:0.2]] [[respond:OLD_SIBLING]]"),
            _output_node("n_out", source="n_gen_fail", prompt="merge [[respond:<section>OLD_OUT</section>]]"),
        ],
        "execution_edges": [
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
    assert source_by_id["n_gen_sibling"]["status"] == "cancelled"
    assert source_by_id["n_gen_sibling"]["output"] is None
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
    assert by_id["n_gen_sibling"]["status"] == "checkpoint_reused"
    assert by_id["n_gen_sibling"]["output"] is None
    assert by_id["n_gen_sibling"]["reused_from_run_id"] is None
    assert by_id["n_out"]["output"] == "<section>MERGED</section>"

    source_after = auth_client.get(f"/api/runs/{source['run_id']}").json()
    source_after_by_id = {step["node_id"]: step for step in source_after["steps"]}
    assert source_after["status"] == "failed"
    assert source_after_by_id["n_gen_fail"]["status"] == "failed"
    assert source_after_by_id["n_gen_sibling"]["output"] is None


def test_executor_forks_sessions_only_for_real_fanout(auth_client, configure_codex):
    configure_codex()
    runtime = ParallelProbeRuntime(delay=0.05)
    set_runtime_override(runtime)
    graph = {
        "nodes": [
            _generate_node("n_root", prompt="root [[respond:ROOT]]"),
            _generate_node("n_child_a", prompt="child a [[respond:A]]"),
            _generate_node("n_child_b", prompt="child b [[respond:B]]"),
            _output_node("n_out", source="n_child_a", prompt="out [[respond:<section>OUT</section>]]"),
        ],
        "execution_edges": [
            {"id": "e1", "source": "n_root", "target": "n_child_a"},
            {"id": "e2", "source": "n_root", "target": "n_child_b"},
            {"id": "e3", "source": "n_child_a", "target": "n_out"},
            {"id": "e4", "source": "n_child_b", "target": "n_out"},
        ],
    }
    try:
        app_id = _build_app(auth_client, graph=graph)
        run = auth_client.post("/api/runs", json={"app_id": app_id, "inputs": {}}).json()
        final = _wait_for_terminal(auth_client, run["run_id"])
        by_id = {step["node_id"]: step for step in final["steps"]}
        assert final["status"] == "success", final
        assert all("agent_session_id" not in step for step in by_id.values())
        calls_by_task = {
            marker: next(
                call
                for call in runtime.execute_calls
                if marker in str(call["prompt"]) and call["runtime_policy"] == "execute"
            )
            for marker in ("root [[respond:ROOT]]", "child a [[respond:A]]", "child b [[respond:B]]", "out [[respond:")
        }
        plan_calls = {
            marker: next(
                call
                for call in runtime.execute_calls
                if marker in str(call["prompt"]) and call["runtime_policy"] == "ask_user_plan"
            )
            for marker in ("root [[respond:ROOT]]", "child a [[respond:A]]", "child b [[respond:B]]")
        }
        assert calls_by_task["root [[respond:ROOT]]"]["fork_session"] == "False"
        assert plan_calls["child a [[respond:A]]"]["fork_session"] == "True", runtime.execute_calls
        assert plan_calls["child b [[respond:B]]"]["fork_session"] == "True", runtime.execute_calls
        assert calls_by_task["child a [[respond:A]]"]["fork_session"] == "False"
        assert calls_by_task["child b [[respond:B]]"]["fork_session"] == "False"
        assert calls_by_task["child a [[respond:A]]"]["cwd"] != calls_by_task["child b [[respond:B]]"]["cwd"]
        assert calls_by_task["out [[respond:"]["cwd"] not in {
            calls_by_task["child a [[respond:A]]"]["cwd"],
            calls_by_task["child b [[respond:B]]"]["cwd"],
        }
    finally:
        set_runtime_override(MockRuntime())


def test_executor_reuses_one_session_and_workspace_for_linear_nodes(auth_client, configure_codex):
    configure_codex()
    runtime = ParallelProbeRuntime(delay=0.01)
    set_runtime_override(runtime)
    graph = {
        "nodes": [
            _generate_node("n_a", prompt="linear a [[respond:A]]"),
            _generate_node("n_b", prompt="linear b [[respond:B]]"),
            _output_node("n_out", source="n_b", prompt="linear out [[respond:<section>OUT</section>]]"),
        ],
        "execution_edges": [
            {"id": "e1", "source": "n_a", "target": "n_b"},
            {"id": "e2", "source": "n_b", "target": "n_out"},
        ],
    }
    try:
        app_id = _build_app(auth_client, graph=graph)
        created = auth_client.post("/api/runs", json={"app_id": app_id, "inputs": {}}).json()
        final = _wait_for_terminal(auth_client, created["run_id"])
        assert final["status"] == "success", final
        calls = [
            next(
                call
                for call in runtime.execute_calls
                if marker in str(call["prompt"]) and call["runtime_policy"] == "execute"
            )
            for marker in ("linear a", "linear b", "linear out")
        ]
        assert len({call["cwd"] for call in calls}) == 1
        assert {call["session_scope"] for call in calls} == {f"run:{created['run_id']}"}
        assert all(call["fork_session"] == "False" for call in calls)
        assert {call["session_id"] for call in calls} == {"probe_session_1"}
        assert all("agent_session_id" not in step for step in final["steps"])
    finally:
        set_runtime_override(MockRuntime())


def test_linear_downstream_reads_undeclared_upstream_workspace_file(auth_client, configure_codex):
    configure_codex()
    set_runtime_override(SharedWorkspaceRuntime())
    graph = {
        "nodes": [
            _generate_node("n_a", prompt="write-private-file"),
            _generate_node("n_b", prompt="read-private-file"),
            _output_node("n_out", source="n_b", prompt="render"),
        ],
        "execution_edges": [
            {"id": "e1", "source": "n_a", "target": "n_b"},
            {"id": "e2", "source": "n_b", "target": "n_out"},
        ],
    }
    try:
        app_id = _build_app(auth_client, graph=graph)
        created = auth_client.post("/api/runs", json={"app_id": app_id, "inputs": {}}).json()
        final = _wait_for_terminal(auth_client, created["run_id"])
        assert final["status"] == "success", final
        artifacts = auth_client.get(f"/api/runs/{created['run_id']}/artifacts").json()["artifacts"]
        assert artifacts == []
        assert "/mnt/results" not in str({step["node_id"]: step["input"] for step in final["steps"]})
    finally:
        set_runtime_override(MockRuntime())


def test_executor_hides_sessions_on_diamond_fan_in(auth_client, configure_codex):
    configure_codex()
    runtime = ParallelProbeRuntime(delay=0.05)
    set_runtime_override(runtime)
    graph = {
        "nodes": [
            _generate_node("n_root", prompt="root [[respond:ROOT]]"),
            _generate_node("n_child", prompt="child [[respond:CHILD]]"),
            _output_node("n_out", source="n_root", prompt="out [[respond:<section>OUT</section>]]"),
        ],
        "execution_edges": [
            {"id": "e1", "source": "n_root", "target": "n_child"},
            {"id": "e2", "source": "n_child", "target": "n_out"},
            {"id": "e3", "source": "n_root", "target": "n_out"},
        ],
    }
    try:
        app_id = _build_app(auth_client, graph=graph)
        run = auth_client.post("/api/runs", json={"app_id": app_id, "inputs": {}}).json()
        final = _wait_for_terminal(auth_client, run["run_id"])
        by_id = {step["node_id"]: step for step in final["steps"]}
        assert final["status"] == "success", final
        assert all("agent_session_id" not in step for step in by_id.values())
    finally:
        set_runtime_override(MockRuntime())


def test_parallel_ask_user_is_visible_before_sibling_finishes(auth_client, configure_codex):
    configure_codex()
    ask = json.dumps(_ask_action("choice", "选择方向？", ["A", "B"])["request"], ensure_ascii=False)
    graph = {
        "nodes": [
            _generate_node("n_ask", prompt=f"ask now [[ask_user:{ask}]] [[respond:ASK_DONE]]"),
            _generate_node("n_delay", prompt="delayed sibling [[delay:0.8]] [[respond:SLOW_DONE]]"),
            _output_node("n_out", source="n_ask", prompt="render [[respond:<section>DONE</section>]]"),
        ],
        "execution_edges": [
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


def test_parallel_sibling_failure_overrides_visible_ask_user(auth_client, configure_codex):
    configure_codex()
    ask = json.dumps(_ask_action("choice", "选择方向？", ["A", "B"])["request"], ensure_ascii=False)
    graph = {
        "nodes": [
            _generate_node("n_ask", prompt=f"ask now [[ask_user:{ask}]] [[respond:ASK_DONE]]"),
            _generate_node("n_fail", prompt="delayed failure [[delay:0.4]] fail-now"),
            _output_node("n_out", source="n_ask", prompt="render [[respond:<section>DONE</section>]]"),
        ],
        "execution_edges": [
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
    assert by_id["n_ask"]["status"] == "cancelled"
    assert by_id["n_fail"]["status"] == "failed"
    assert by_id["n_out"]["status"] == "pending"


def test_parallel_ask_user_requests_are_queued(auth_client, configure_codex):
    configure_codex()
    ask_a = json.dumps(_ask_action("choice_a", "选择 A？", ["A1", "A2"])["request"], ensure_ascii=False)
    ask_b = json.dumps(_ask_action("choice_b", "选择 B？", ["B1", "B2"])["request"], ensure_ascii=False)
    graph = {
        "nodes": [
            _generate_node("n_gen_a", prompt=f"ask a [[ask_user:{ask_a}]] [[respond:A_DONE]]"),
            _generate_node("n_gen_b", prompt=f"ask b [[ask_user:{ask_b}]] [[respond:B_DONE]]"),
            _output_node("n_out", source="n_gen_a", prompt="render [[respond:<section>DONE</section>]]"),
        ],
        "execution_edges": [
            {"id": "e_a_out", "source": "n_gen_a", "target": "n_out"},
            {"id": "e_b_out", "source": "n_gen_b", "target": "n_out"},
        ],
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


def test_parallel_second_ask_after_resume_keeps_run_waiting(auth_client, configure_codex):
    configure_codex()
    ask_first = json.dumps(_ask_action("choice_first", "选择第一步？", ["A1", "A2"])["request"], ensure_ascii=False)
    ask_second = json.dumps(_ask_action("choice_second", "选择第二步？", ["B1", "B2"])["request"], ensure_ascii=False)
    graph = {
        "nodes": [
            _generate_node("n_first", prompt=f"first ask [[ask_user:{ask_first}]] [[respond:FIRST_DONE]]"),
            _generate_node("n_short", prompt="short sibling [[delay:0.3]] [[respond:SHORT_DONE]]"),
            _generate_node("n_long", prompt="long sibling [[delay:3.0]] [[respond:LONG_DONE]]"),
            _generate_node("n_second", prompt=f"second ask [[ask_user:{ask_second}]] [[respond:SECOND_DONE]]"),
            _output_node("n_out", source="n_second", prompt="render [[respond:<section>DONE</section>]]"),
        ],
        "execution_edges": [
            {"id": "e_first_second", "source": "n_first", "target": "n_second"},
            {"id": "e_first_long", "source": "n_first", "target": "n_long"},
            {"id": "e_second_out", "source": "n_second", "target": "n_out"},
            {"id": "e_short_out", "source": "n_short", "target": "n_out"},
            {"id": "e_long_out", "source": "n_long", "target": "n_out"},
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


def test_parallel_ask_gallery_template_runs_and_waits_for_user(auth_client, configure_codex):
    configure_codex()
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
        assert by_id["n_parallel_research"]["status"] in {"running", "success"}, by_id["n_parallel_research"]
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
        assert "PERSONAL_STRATEGY" not in output_prompt
        assert "PARALLEL_RESEARCH" not in output_prompt
        assert "/mnt/results" not in output_prompt
    finally:
        set_runtime_override(MockRuntime())


def test_vague_recommendation_input_enters_ask_user_waiting(auth_client, configure_codex):
    configure_codex()
    runtime = AskUserJudgmentRuntime()
    set_runtime_override(runtime)
    run_id: str | None = None
    graph = {
        "nodes": [
            USER_INPUT_NODE,
            _generate_node("n_gen", prompt="你是一名专业文学助手。请根据用户的读书偏好推荐 5 本书。"),
        ],
        "execution_edges": [{"id": "e1", "source": "n_input", "target": "n_gen"}],
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


def test_specific_recommendation_input_can_skip_ask_user(auth_client, configure_codex):
    configure_codex()
    runtime = AskUserJudgmentRuntime()
    set_runtime_override(runtime)
    graph = {
        "nodes": [
            USER_INPUT_NODE,
            _generate_node("n_gen", prompt="你是一名专业文学助手。请根据用户的读书偏好推荐 5 本书。"),
        ],
        "execution_edges": [{"id": "e1", "source": "n_input", "target": "n_gen"}],
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



def test_executor_uses_run_graph_snapshot_after_app_changes(auth_client, configure_codex, monkeypatch):
    configure_codex()

    from app.api import runs as runs_api

    def _no_schedule(_run_id: str):
        return None

    monkeypatch.setattr(runs_api, "schedule_run", _no_schedule)
    graph = {
        "nodes": [
            USER_INPUT_NODE,
            _generate_node("n_gen", prompt="请生成 [[respond:SNAPSHOT]]"),
            _output_node("n_out", source="n_gen", prompt="整理输出 [[respond:<section>SNAPSHOT_OUT</section>]]"),
        ],
        "execution_edges": [
            {"id": "e1", "source": "n_input", "target": "n_gen"},
            {"id": "e2", "source": "n_gen", "target": "n_out"},
        ],
    }
    app_id = _build_app(auth_client, graph=graph)
    created = auth_client.post(
        "/api/runs", json={"app_id": app_id, "inputs": {"n_input": "hello"}}
    ).json()

    changed_graph = {
        "nodes": [USER_INPUT_NODE],
        "execution_edges": [],
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


def test_rerun_from_reuses_ancestor_outputs_and_uses_current_graph(auth_client, configure_codex):
    configure_codex()
    graph = {
        "nodes": [
            USER_INPUT_NODE,
            _generate_node("n_gen_a", prompt="第一步 [[respond:OLD_A]]"),
            _generate_node("n_gen_b", prompt="第二步 [[respond:OLD_B]]"),
            _output_node("n_out", source="n_gen_b", prompt="输出 [[respond:<section>OLD_OUT</section>]]"),
        ],
        "execution_edges": [
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
        "nodes": [
            USER_INPUT_NODE,
            _generate_node("n_gen_a", prompt="第一步 [[respond:NEW_A_SHOULD_NOT_RUN]]"),
            _generate_node("n_gen_b", prompt="第二步 [[respond:NEW_B]]"),
            _output_node("n_out", source="n_gen_b", prompt="输出 [[respond:<section>NEW_OUT</section>]]"),
        ],
        "execution_edges": graph["execution_edges"],
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
    assert final["source_run_id"] == source["run_id"]
    assert final["rerun_from_node_id"] == "n_gen_b"
    by_id = {step["node_id"]: step for step in final["steps"]}
    assert by_id["n_input"]["output"]["value"] == "hello"
    assert by_id["n_gen_a"]["output"] == "OLD_A"
    assert by_id["n_gen_a"]["reused_from_run_id"] == source["run_id"]
    assert by_id["n_gen_a"]["reused_from_step_id"].startswith("step_")
    assert by_id["n_gen_b"]["output"] == "NEW_B"
    assert by_id["n_gen_b"]["reused_from_run_id"] is None
    assert "OLD_A" not in by_id["n_gen_b"]["input"]["prompt"]
    assert "/mnt/results" not in by_id["n_gen_b"]["input"]["prompt"]
    workspace = run_workspace(ADMIN_USER_ID, app_id, final["id"])
    assert not (workspace / "nodes").exists()
    assert any((workspace / "agent" / "checkpoints").iterdir())
    assert by_id["n_out"]["output"] == "<section>NEW_OUT</section>"
    assert final["graph"]["nodes"][1]["prompt"] == "第一步 [[respond:NEW_A_SHOULD_NOT_RUN]]"

    source_after = auth_client.get(f"/api/runs/{source['run_id']}").json()
    assert source_after["status"] == "success"
    assert {step["node_id"]: step for step in source_after["steps"]}["n_gen_a"]["output"] == "OLD_A"


def test_rerun_from_forks_frozen_checkpoint_session_without_exposing_it(auth_client, configure_codex):
    configure_codex()
    runtime = ParallelProbeRuntime(delay=0.01)
    set_runtime_override(runtime)
    try:
        graph = {
            "nodes": [
                USER_INPUT_NODE,
                _generate_node("n_gen_a", prompt="第一步 [[respond:OLD_A]]"),
                _generate_node("n_gen_b", prompt="第二步 [[respond:OLD_B]]"),
                _output_node("n_out", source="n_gen_b", prompt="输出 [[respond:<section>OLD_OUT</section>]]"),
            ],
            "execution_edges": [
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
        assert "agent_session_id" not in source_by_id["n_gen_a"]

        current_graph = {
            "nodes": [
                USER_INPUT_NODE,
                _generate_node("n_gen_a", prompt="第一步 [[respond:NEW_A_SHOULD_NOT_RUN]]"),
                _generate_node("n_gen_b", prompt="第二步 [[respond:NEW_B]]"),
                _output_node("n_out", source="n_gen_b", prompt="输出 [[respond:<section>NEW_OUT</section>]]"),
            ],
            "execution_edges": graph["execution_edges"],
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
        assert by_id["n_gen_b"]["output"] == "NEW_B"
        assert all("agent_session_id" not in step for step in by_id.values())

        gen_b_plan_call = next(
            call
            for call in runtime.execute_calls
            if "第二步" in str(call["prompt"]) and call["runtime_policy"] == "ask_user_plan"
        )
        assert gen_b_plan_call["session_id"] is not None
        assert gen_b_plan_call["fork_session"] == "True"
        assert gen_b_plan_call["session_scope"] == f"run:{final['id']}"
    finally:
        set_runtime_override(MockRuntime())


def test_rerun_from_restores_cut_checkpoint_when_same_branch_has_later_frozen_checkpoint(
    auth_client,
    configure_codex,
):
    configure_codex()
    runtime = CheckpointRerunWorkspaceRuntime()
    set_runtime_override(runtime)
    source_graph = {
        "nodes": [
            _generate_node("n_before", prompt="checkpoint-before"),
            _generate_node("n_cut", prompt="source-cut"),
            _generate_node("n_after", prompt="source-after-cut"),
            _output_node("n_out", source="n_after", prompt="source-output"),
        ],
        "execution_edges": [
            {"id": "e1", "source": "n_before", "target": "n_cut"},
            {"id": "e2", "source": "n_cut", "target": "n_after"},
            {"id": "e3", "source": "n_after", "target": "n_out"},
        ],
    }
    try:
        app_id = _build_app(auth_client, graph=source_graph)
        source = auth_client.post("/api/runs", json={"app_id": app_id, "inputs": {}}).json()
        source_final = _wait_for_terminal(auth_client, source["run_id"])
        assert source_final["status"] == "success", source_final

        current_graph = {
            "nodes": [
                _generate_node("n_before", prompt="frozen-before"),
                _generate_node("n_cut", prompt="rerun-cut"),
                _generate_node("n_after", prompt="frozen-after"),
                _output_node("n_out", source="n_cut", prompt="rerun-output"),
            ],
            "execution_edges": [
                {"id": "e1", "source": "n_before", "target": "n_cut"},
                {"id": "e2", "source": "n_before", "target": "n_after"},
                {"id": "e3", "source": "n_cut", "target": "n_out"},
                {"id": "e4", "source": "n_after", "target": "n_out"},
            ],
        }
        response = auth_client.patch(f"/api/apps/{app_id}", json={"graph": current_graph})
        assert response.status_code == 200, response.text

        created = auth_client.post(
            f"/api/runs/{source['run_id']}/rerun-from",
            json={"app_id": app_id, "node_id": "n_cut"},
        )
        assert created.status_code == 200, created.text
        final = _wait_for_terminal(auth_client, created.json()["run_id"])

        assert final["status"] == "success", final
        by_id = {step["node_id"]: step for step in final["steps"]}
        assert by_id["n_before"]["status"] == "success"
        assert by_id["n_after"]["status"] == "success"
        assert by_id["n_cut"]["output"] == "RERUN_CUT"
        assert runtime.rerun_cut_initial_state == "BEFORE_CUT"
    finally:
        set_runtime_override(MockRuntime())


def test_rerun_cut_bypasses_new_upstream_and_ignores_upstream_input_override(auth_client, configure_codex):
    configure_codex()
    source_graph = {
        "nodes": [
            USER_INPUT_NODE,
            _generate_node("n_a", prompt="source a [[respond:OLD_A]]"),
            _generate_node("n_cut", prompt="source cut [[respond:OLD_CUT]]"),
            _output_node("n_out", source="n_cut", prompt="source out [[respond:<section>OLD</section>]]"),
        ],
        "execution_edges": [
            {"id": "e1", "source": "n_input", "target": "n_a"},
            {"id": "e2", "source": "n_a", "target": "n_cut"},
            {"id": "e3", "source": "n_cut", "target": "n_out"},
        ],
    }
    app_id = _build_app(auth_client, graph=source_graph)
    source = auth_client.post(
        "/api/runs", json={"app_id": app_id, "inputs": {"n_input": "old input"}}
    ).json()
    assert _wait_for_terminal(auth_client, source["run_id"])["status"] == "success"

    latest_graph = {
        "nodes": [
            USER_INPUT_NODE,
            _generate_node("n_new", prompt="must not run [[respond:NEW_UPSTREAM]]"),
            _generate_node("n_a", prompt="must stay frozen [[respond:NEW_A]]"),
            _generate_node("n_cut", prompt="latest cut [[respond:NEW_CUT]]"),
            _output_node("n_out", source="n_cut", prompt="latest out [[respond:<section>NEW</section>]]"),
        ],
        "execution_edges": [
            {"id": "e1", "source": "n_input", "target": "n_new"},
            {"id": "e2", "source": "n_new", "target": "n_a"},
            {"id": "e3", "source": "n_a", "target": "n_cut"},
            {"id": "e4", "source": "n_cut", "target": "n_out"},
        ],
    }
    assert auth_client.patch(f"/api/apps/{app_id}", json={"graph": latest_graph}).status_code == 200
    created = auth_client.post(
        f"/api/runs/{source['run_id']}/rerun-from",
        json={
            "app_id": app_id,
            "node_id": "n_cut",
            "inputs": {"n_input": "ignored new input"},
        },
    )
    assert created.status_code == 200, created.text
    final = _wait_for_terminal(auth_client, created.json()["run_id"])
    assert final["status"] == "success", final
    by_id = {step["node_id"]: step for step in final["steps"]}
    assert final["inputs"]["n_input"]["value"] == "old input"
    assert by_id["n_input"]["output"]["value"] == "old input"
    assert by_id["n_new"]["status"] == "checkpoint_reused"
    assert by_id["n_new"]["output"] is None
    assert by_id["n_a"]["output"] == "OLD_A"
    assert by_id["n_cut"]["output"] == "NEW_CUT"
    assert by_id["n_out"]["output"] == "<section>NEW</section>"


def test_rerun_from_rejects_cut_without_pre_checkpoint(auth_client, configure_codex):
    configure_codex()
    graph = {
        "nodes": [
            _generate_node("n_gen_fail", prompt="please fail-now"),
            _output_node("n_out", source="n_gen_fail"),
        ],
        "execution_edges": [{"id": "e1", "source": "n_gen_fail", "target": "n_out"}],
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
    assert "workspace checkpoint" in response.json()["detail"]


def test_rerun_from_condition_branch_override_forces_branch(auth_client, configure_codex):
    configure_codex()
    graph = {
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
            _generate_node("n_merge", prompt="汇总 [[respond:MERGED]]"),
            _output_node("n_out", source="n_merge", prompt="输出 [[respond:<section>OUT</section>]]"),
        ],
        "execution_edges": [
            {"id": "e1", "source": "n_input", "target": "n_cond"},
            {"id": "e2", "source": "n_cond", "target": "n_yes", "branch_key": "true"},
            {"id": "e3", "source": "n_cond", "target": "n_no", "branch_key": "false"},
            {"id": "e4", "source": "n_yes", "target": "n_merge"},
            {"id": "e5", "source": "n_no", "target": "n_merge"},
            {"id": "e6", "source": "n_merge", "target": "n_out"},
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
            _generate_node("n_merge", prompt="汇总 [[respond:MERGED_NEW]]"),
            _output_node("n_out", source="n_merge", prompt="输出 [[respond:<section>OUT_NEW</section>]]"),
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


def test_rerun_replays_reused_condition_branch_on_current_graph(auth_client, configure_codex):
    configure_codex()
    graph = {
        "nodes": [
            _condition_node(
                "n_cond",
                mode="binary",
                branches=[{"key": "true"}, {"key": "false"}],
                prompt="判断 [[respond:true]]",
            ),
            _generate_node("n_yes", prompt="是 [[respond:YES]]"),
            _generate_node("n_no", prompt="否 [[respond:NO]]"),
            _generate_node("n_merge", prompt="汇总 [[respond:MERGED]]"),
            _output_node("n_out", source="n_merge", prompt="输出 [[respond:<section>OUT</section>]]"),
        ],
        "execution_edges": [
            {"id": "e1", "source": "n_cond", "target": "n_yes", "branch_key": "true"},
            {"id": "e2", "source": "n_cond", "target": "n_no", "branch_key": "false"},
            {"id": "e3", "source": "n_yes", "target": "n_merge"},
            {"id": "e4", "source": "n_no", "target": "n_merge"},
            {"id": "e5", "source": "n_merge", "target": "n_out"},
        ],
    }
    app_id = _build_app(auth_client, graph=graph)
    source = auth_client.post("/api/runs", json={"app_id": app_id, "inputs": {}}).json()
    source_final = _wait_for_terminal(auth_client, source["run_id"])
    assert source_final["status"] == "success"

    created = auth_client.post(
        f"/api/runs/{source['run_id']}/rerun-from",
        json={"app_id": app_id, "node_id": "n_yes"},
    )
    assert created.status_code == 200, created.text
    final = _wait_for_terminal(auth_client, created.json()["run_id"])
    by_id = {step["node_id"]: step for step in final["steps"]}

    assert final["status"] == "success", final
    assert by_id["n_cond"]["output"] == "true"
    assert by_id["n_cond"]["reused_from_run_id"] == source["run_id"]
    assert by_id["n_yes"]["status"] == "success"
    assert by_id["n_no"]["status"] == "skipped"


def test_rerun_rejects_skipped_condition_branch_without_checkpoint(auth_client, configure_codex):
    configure_codex()
    graph = {
        "nodes": [
            _condition_node(
                "n_cond",
                mode="binary",
                branches=[{"key": "true"}, {"key": "false"}],
                prompt="判断 [[respond:true]]",
            ),
            _generate_node("n_yes", prompt="是 [[respond:YES]]"),
            _generate_node("n_no", prompt="否 [[respond:NO]]"),
            _generate_node("n_merge", prompt="汇总 [[respond:MERGED]]"),
            _output_node("n_out", source="n_merge", prompt="输出 [[respond:<section>OUT</section>]]"),
        ],
        "execution_edges": [
            {"id": "e1", "source": "n_cond", "target": "n_yes", "branch_key": "true"},
            {"id": "e2", "source": "n_cond", "target": "n_no", "branch_key": "false"},
            {"id": "e3", "source": "n_yes", "target": "n_merge"},
            {"id": "e4", "source": "n_no", "target": "n_merge"},
            {"id": "e5", "source": "n_merge", "target": "n_out"},
        ],
    }
    app_id = _build_app(auth_client, graph=graph)
    source = auth_client.post("/api/runs", json={"app_id": app_id, "inputs": {}}).json()
    assert _wait_for_terminal(auth_client, source["run_id"])["status"] == "success"

    response = auth_client.post(
        f"/api/runs/{source['run_id']}/rerun-from",
        json={"app_id": app_id, "node_id": "n_no"},
    )

    assert response.status_code == 409
    assert "workspace checkpoint" in response.json()["detail"]


def test_rerun_from_condition_branch_override_rejects_invalid_branch(auth_client, configure_codex):
    configure_codex()
    graph = {
        "nodes": [
            _condition_node(
                "n_cond",
                mode="binary",
                branches=[{"key": "true"}, {"key": "false"}],
                prompt="判断 [[respond:true]]",
            ),
            _generate_node("n_yes", prompt="是 [[respond:YES]]"),
            _generate_node("n_no", prompt="否 [[respond:NO]]"),
            _output_node("n_out", source="n_yes", prompt="输出 [[respond:<section>OUT</section>]]"),
        ],
        "execution_edges": [
            {"id": "e1", "source": "n_cond", "target": "n_yes", "branch_key": "true"},
            {"id": "e2", "source": "n_cond", "target": "n_no", "branch_key": "false"},
            {"id": "e3", "source": "n_yes", "target": "n_out"},
            {"id": "e4", "source": "n_no", "target": "n_out"},
        ],
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


def test_rerun_from_failed_node_uses_current_fixed_prompt(auth_client, configure_codex):
    configure_codex()
    graph = {
        "nodes": [
            USER_INPUT_NODE,
            _generate_node("n_gen", prompt="please fail-now"),
            _output_node("n_out", source="n_gen", prompt="输出 [[respond:<section>OUT</section>]]"),
        ],
        "execution_edges": [
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


def test_rerun_from_failed_node_ignores_changed_input_before_checkpoint(auth_client, configure_codex):
    configure_codex()
    graph = {
        "nodes": [
            USER_INPUT_NODE,
            _generate_node("n_gen", prompt="please fail-now"),
            _output_node("n_out", source="n_gen", prompt="输出 [[respond:<section>OUT</section>]]"),
        ],
        "execution_edges": [
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
    assert by_id["n_input"]["output"]["value"] == "old input"
    assert "new input" not in by_id["n_gen"]["input"]["prompt"]
    assert "old input" not in by_id["n_gen"]["input"]["prompt"]
    context_files = list(
        (run_workspace(ADMIN_USER_ID, app_id, final["id"]) / "agent" / "branches").glob(
            "*/work/.mira/run-context/n_input.json"
        )
    )
    assert len(context_files) == 1
    assert json.loads(context_files[0].read_text(encoding="utf-8"))["value"]["value"] == "old input"
    assert by_id["n_gen"]["output"] == "REPAIRED"


def test_rerun_from_allows_deleted_old_input_when_inputs_omitted(auth_client, configure_codex):
    configure_codex()
    graph = {
        "nodes": [
            USER_INPUT_NODE,
            _generate_node("n_gen", prompt="生成 [[respond:OLD]]"),
        ],
        "execution_edges": [{"id": "e1", "source": "n_input", "target": "n_gen"}],
    }
    app_id = _build_app(auth_client, graph=graph)
    source = auth_client.post(
        "/api/runs", json={"app_id": app_id, "inputs": {"n_input": "old input"}}
    ).json()
    assert _wait_for_terminal(auth_client, source["run_id"])["status"] == "success"

    current_graph = {
        "nodes": [_generate_node("n_gen", prompt="生成 [[respond:NEW]]")],
        "execution_edges": [],
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


def test_executor_passes_node_reasoning_effort_with_low_default(auth_client, configure_codex):
    configure_codex()
    runtime = ReasoningCaptureRuntime()
    set_runtime_override(runtime)
    try:
        graph = {
            "nodes": [
                _generate_node("n_gen", prompt="生成"),
                {
                    **_output_node("n_out", source="n_gen"),
                    "reasoning_effort": "high",
                },
            ],
            "execution_edges": [{"id": "e1", "source": "n_gen", "target": "n_out"}],
        }
        app_id = _build_app(auth_client, graph=graph)
        run = auth_client.post("/api/runs", json={"app_id": app_id, "inputs": {}}).json()
        final = _wait_for_terminal(auth_client, run["run_id"])
    finally:
        set_runtime_override(MockRuntime())

    assert final["status"] == "success", final
    execute_calls = _execute_calls(runtime)
    assert [call["reasoning_effort"] for call in execute_calls] == ["low", "high"]


def test_executor_passes_app_scoped_tools(auth_client, configure_codex):
    configure_codex()
    allowed_skill = _upload_skill(auth_client, "allowed-skill")
    blocked_skill = _upload_skill(auth_client, "blocked-skill")
    assert auth_client.post(
        "/api/settings/mcp",
        json={
            "id": "mcp_allowed",
            "name": "allowed-mcp",
            "enabled": True,
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
            "url": "http://localhost:9999/blocked",
            "headers": [],
            "env_var_names": [],
        },
    ).status_code == 200

    runtime = ReasoningCaptureRuntime()
    set_runtime_override(runtime)
    try:
        graph = {
            "tools": {
                "disabled_tool_ids": [
                    "mcp:mcp_blocked",
                    f"skill:{blocked_skill['id']}",
                ],
            },
            "nodes": [_generate_node("n_gen", prompt="生成")],
            "execution_edges": [],
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


def test_executor_trusts_run_tools_snapshot(auth_client, configure_codex, monkeypatch):
    configure_codex()
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
            "url": "http://localhost:9999/snapshot",
            "headers": [],
            "env_var_names": [],
        },
    ).status_code == 200

    graph = {
        "nodes": [_generate_node("n_gen", prompt="生成")],
        "execution_edges": [],
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


def test_executor_decision_plan_passes_only_planning_safe_tools(auth_client, configure_codex):
    configure_codex()
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
            "url": "http://localhost:9999/execute",
            "headers": [],
            "env_var_names": [],
        },
    ).status_code == 200

    runtime = ReasoningCaptureRuntime()
    set_runtime_override(runtime)
    try:
        graph = {
            "nodes": [_generate_node("n_gen", prompt="生成")],
            "execution_edges": [],
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


def test_executor_validates_generate_json_output_contract(auth_client, configure_codex):
    configure_codex()
    runtime = SequenceRuntime(['{"summary":"done"}'])
    set_runtime_override(runtime)
    try:
        graph = {
            "nodes": [
                _contract_node(
                    "n_gen",
                    prompt="生成结构化结果",
                    output_contract=_json_contract("summary"),
                ),
            ],
            "execution_edges": [],
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


def test_executor_keeps_output_contract_out_of_decision_plan(auth_client, configure_codex):
    configure_codex()
    runtime = PlanningCaptureRuntime()
    set_runtime_override(runtime)
    try:
        graph = {
            "nodes": [
                _contract_node(
                    "n_gen",
                    prompt="生成纯文本结果",
                    output_contract=_json_contract("result"),
                ),
            ],
            "execution_edges": [],
        }
        app_id = _build_app(auth_client, graph=graph)
        run = auth_client.post("/api/runs", json={"app_id": app_id, "inputs": {}}).json()
        final = _wait_for_terminal(auth_client, run["run_id"])
    finally:
        set_runtime_override(MockRuntime())

    assert final["status"] == "success", final
    generate_plan_prompts = [
        prompt for prompt in runtime.plan_prompts if "生成纯文本结果" in prompt
    ]
    assert len(generate_plan_prompts) == 1
    assert "# 输出契约" not in generate_plan_prompts[0]
    assert "最终回复必须是严格符合后端 JSON Schema" not in generate_plan_prompts[0]
    step = final["steps"][0]
    assert "# 输出契约" in step["input"]["prompt"]
    assert "最终回复必须是严格符合后端 JSON Schema" in step["input"]["prompt"]


def test_executor_skips_decision_plan_for_output_node(auth_client, configure_codex):
    configure_codex()
    runtime = PlanningCaptureRuntime()
    set_runtime_override(runtime)
    try:
        graph = {
            "nodes": [
                _generate_node("n_gen", prompt="生成中间内容"),
                _output_node("n_out", source="n_gen", prompt="展示最终 HTML"),
            ],
            "execution_edges": [{"id": "e1", "source": "n_gen", "target": "n_out"}],
        }
        app_id = _build_app(auth_client, graph=graph)
        run = auth_client.post("/api/runs", json={"app_id": app_id, "inputs": {}}).json()
        final = _wait_for_terminal(auth_client, run["run_id"])
    finally:
        set_runtime_override(MockRuntime())

    assert final["status"] == "success", final
    assert len(runtime.plan_prompts) == 1
    assert "生成中间内容" in runtime.plan_prompts[0]
    assert "展示最终 HTML" not in runtime.plan_prompts[0]
    by_id = {step["node_id"]: step for step in final["steps"]}
    assert by_id["n_out"]["output"] == "<section>OK</section>"


def test_executor_skips_forced_decision_plan_when_disabled(auth_client, configure_codex):
    configure_codex()
    runtime = PlanningCaptureRuntime()
    set_runtime_override(runtime)
    try:
        node = _generate_node("n_gen", prompt="必须先调用 ask_user，再生成纯文本结果")
        node["ask_user_enabled"] = False
        graph = {
            "nodes": [node],
            "execution_edges": [],
        }
        app_id = _build_app(auth_client, graph=graph)
        run = auth_client.post("/api/runs", json={"app_id": app_id, "inputs": {}}).json()
        final = _wait_for_terminal(auth_client, run["run_id"])
    finally:
        set_runtime_override(MockRuntime())

    assert final["status"] == "success", final
    assert runtime.plan_prompts == []
    by_id = {step["node_id"]: step for step in final["steps"]}
    assert by_id["n_gen"]["output"] == "SCRIPT_RESULT"


def test_executor_keeps_decision_plan_when_explicitly_enabled(auth_client, configure_codex):
    configure_codex()
    runtime = PlanningCaptureRuntime()
    set_runtime_override(runtime)
    try:
        node = _generate_node("n_gen", prompt="生成纯文本结果")
        node["ask_user_enabled"] = True
        graph = {
            "nodes": [node],
            "execution_edges": [],
        }
        app_id = _build_app(auth_client, graph=graph)
        run = auth_client.post("/api/runs", json={"app_id": app_id, "inputs": {}}).json()
        final = _wait_for_terminal(auth_client, run["run_id"])
    finally:
        set_runtime_override(MockRuntime())

    assert final["status"] == "success", final
    assert len(runtime.plan_prompts) == 1
    assert "生成纯文本结果" in runtime.plan_prompts[0]


def test_executor_runs_decision_plan_for_contract_generate_with_user_input(auth_client, configure_codex):
    configure_codex()
    runtime = PlanningCaptureRuntime()
    set_runtime_override(runtime)
    try:
        graph = {
            "nodes": [
                USER_INPUT_NODE,
                _contract_node(
                    "n_gen",
                    prompt="根据用户输入生成结构化结果。",
                    output_contract=_json_contract("result"),
                ),
            ],
            "execution_edges": [{"id": "e1", "source": "n_input", "target": "n_gen"}],
        }
        app_id = _build_app(auth_client, graph=graph)
        run = auth_client.post("/api/runs", json={"app_id": app_id, "inputs": {"n_input": "农业"}}).json()
        final = _wait_for_terminal(auth_client, run["run_id"])
    finally:
        set_runtime_override(MockRuntime())

    assert final["status"] == "success", final
    assert len(runtime.plan_prompts) == 1
    assert "根据用户输入生成结构化结果" in runtime.plan_prompts[0]
    by_id = {step["node_id"]: step for step in final["steps"]}
    assert by_id["n_gen"]["output"] == {"result": "SCRIPT_RESULT"}


def test_executor_repairs_generate_contract_output_once(auth_client, configure_codex):
    configure_codex()
    original_output = '结果如下：{"title":"ORIGINAL_FACT_125"}\n请查收。'
    runtime = SequenceRuntime([original_output, '{"title":"ORIGINAL_FACT_125"}'])
    set_runtime_override(runtime)
    try:
        graph = {
            "nodes": [
                _contract_node(
                    "n_gen",
                    prompt="生成结构化结果",
                    output_contract=_json_contract("title"),
                ),
            ],
            "execution_edges": [],
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
    assert runtime.session_ids == [None, "sequence_session_1"]
    assert any("输出契约校验失败，尝试自动修正" in log["text"] for log in step["logs"])


def test_executor_repairs_output_contract_once_in_same_session_and_workspace(
    auth_client,
    configure_codex,
):
    configure_codex()
    runtime = OutputContractRepairRuntime()
    set_runtime_override(runtime)
    try:
        generate = _generate_node("n_gen", prompt="生成中间结果")
        generate["ask_user_enabled"] = False
        graph = {
            "nodes": [
                generate,
                _output_node("n_out", source="n_gen", prompt="展示最终 HTML"),
            ],
            "execution_edges": [{"id": "e1", "source": "n_gen", "target": "n_out"}],
        }
        app_id = _build_app(auth_client, graph=graph)
        run = auth_client.post("/api/runs", json={"app_id": app_id, "inputs": {}}).json()
        final = _wait_for_terminal(auth_client, run["run_id"])
    finally:
        set_runtime_override(MockRuntime())

    assert final["status"] == "success", final
    by_id = {step["node_id"]: step for step in final["steps"]}
    assert by_id["n_out"]["output"] == "<section>RECOVERED</section>"
    output_calls = [
        call for call in runtime.execute_calls if call["output_schema"] is not None
    ]
    assert len(output_calls) == 2
    assert output_calls[0]["session_id"] == output_calls[1]["session_id"] == "output_repair_session"
    assert output_calls[0]["cwd"] == output_calls[1]["cwd"]
    assert "这不是 HTML 契约输出" in output_calls[1]["prompt"]


def test_executor_repairs_office_artifact_validation_once(
    auth_client,
    configure_codex,
    monkeypatch,
):
    configure_codex()
    runtime = OfficeArtifactRepairRuntime()
    validations: list[Path] = []

    def validate_office(path: Path, **_kwargs) -> str | None:
        validations.append(path)
        return "Office 文档无法打开" if len(validations) == 1 else None

    monkeypatch.setattr(output_contracts, "validate_office_documents", validate_office)
    set_runtime_override(runtime)
    try:
        node = _contract_node(
            "n_gen",
            prompt="生成 Office 资料包",
            output_contract={
                "type": "artifact",
                "artifact_kind": "zip",
                "max_count": 1,
                "validate_office_documents": True,
            },
        )
        node["ask_user_enabled"] = False
        app_id = _build_app(auth_client, graph={"nodes": [node], "execution_edges": []})
        run = auth_client.post("/api/runs", json={"app_id": app_id, "inputs": {}}).json()
        final = _wait_for_terminal(auth_client, run["run_id"])
    finally:
        set_runtime_override(MockRuntime())

    assert final["status"] == "success", final
    assert len(validations) == 2
    assert len(runtime.prompts) == 2
    assert "Office 文档无法打开" in runtime.prompts[1]
    manifest = final["steps"][0]["output"]
    assert manifest[0]["path"].startswith("artifacts/n_gen/artifact_")
    assert manifest[0]["path"].endswith("/documents.zip")
    assert manifest[0]["manifest_version"] == 1


def test_executor_does_not_repair_when_office_validator_is_unavailable(
    auth_client,
    configure_codex,
    monkeypatch,
):
    configure_codex()
    runtime = OfficeArtifactRepairRuntime()

    def unavailable(_path: Path, **_kwargs) -> str | None:
        raise OfficeValidationUnavailable("Office 文档深检不可用：缺少 pdfinfo")

    monkeypatch.setattr(output_contracts, "validate_office_documents", unavailable)
    set_runtime_override(runtime)
    try:
        node = _contract_node(
            "n_gen",
            prompt="生成 Office 资料包",
            output_contract={
                "type": "artifact",
                "artifact_kind": "zip",
                "max_count": 1,
                "validate_office_documents": True,
            },
        )
        node["ask_user_enabled"] = False
        app_id = _build_app(auth_client, graph={"nodes": [node], "execution_edges": []})
        run = auth_client.post("/api/runs", json={"app_id": app_id, "inputs": {}}).json()
        final = _wait_for_terminal(auth_client, run["run_id"])
    finally:
        set_runtime_override(MockRuntime())

    assert final["status"] == "failed"
    assert len(runtime.prompts) == 1
    assert "输出契约校验不可用" in (final["steps"][0].get("error") or "")


def test_executor_repairs_unicode_damaged_contract_field_in_same_session(
    auth_client,
    configure_codex,
):
    configure_codex()
    runtime = SequenceRuntime(['{"title":"设备���面"}', '{"title":"设备页面"}'])
    set_runtime_override(runtime)
    try:
        graph = {
            "nodes": [
                _contract_node(
                    "n_gen",
                    prompt="生成结构化结果",
                    output_contract=_json_contract("title"),
                ),
            ],
            "execution_edges": [],
        }
        app_id = _build_app(auth_client, graph=graph)
        run = auth_client.post("/api/runs", json={"app_id": app_id, "inputs": {}}).json()
        final = _wait_for_terminal(auth_client, run["run_id"])
    finally:
        set_runtime_override(MockRuntime())

    assert final["status"] == "success", final
    step = final["steps"][0]
    assert step["output"] == {"title": "设备页面"}
    assert runtime.session_ids == [None, "sequence_session_1"]
    assert "agent_session_id" not in step
    repair_prompt = runtime.prompts[1]
    assert "[[MIRA_CORRUPTED_TEXT]]" in repair_prompt
    assert "损坏字符修复要求" in repair_prompt
    assert "设备���面" not in repair_prompt
    assert "不得只删除标记后保留残缺词语" in repair_prompt


def test_executor_marks_escaped_unicode_damage_before_contract_repair(
    auth_client,
    configure_codex,
):
    configure_codex()
    runtime = SequenceRuntime(['{"title":"设备\\ufffd面"}', '{"title":"设备页面"}'])
    set_runtime_override(runtime)
    try:
        graph = {
            "nodes": [
                _contract_node(
                    "n_gen",
                    prompt="生成结构化结果",
                    output_contract=_json_contract("title"),
                ),
            ],
            "execution_edges": [],
        }
        app_id = _build_app(auth_client, graph=graph)
        run = auth_client.post("/api/runs", json={"app_id": app_id, "inputs": {}}).json()
        final = _wait_for_terminal(auth_client, run["run_id"])
    finally:
        set_runtime_override(MockRuntime())

    assert final["status"] == "success", final
    assert final["steps"][0]["output"] == {"title": "设备页面"}
    repair_prompt = runtime.prompts[1]
    assert "[[MIRA_CORRUPTED_TEXT]]" in repair_prompt
    assert "\\ufffd" not in repair_prompt.lower()


def test_executor_rejects_unicode_damage_that_survives_contract_repair(
    auth_client,
    configure_codex,
):
    configure_codex()
    runtime = SequenceRuntime(['{"title":"设备���面"}', '{"title":"仍然���坏"}'])
    set_runtime_override(runtime)
    try:
        graph = {
            "nodes": [
                _contract_node(
                    "n_gen",
                    prompt="生成结构化结果",
                    output_contract=_json_contract("title"),
                ),
            ],
            "execution_edges": [],
        }
        app_id = _build_app(auth_client, graph=graph)
        run = auth_client.post("/api/runs", json={"app_id": app_id, "inputs": {}}).json()
        final = _wait_for_terminal(auth_client, run["run_id"])
    finally:
        set_runtime_override(MockRuntime())

    assert final["status"] == "failed"
    step = final["steps"][0]
    assert step["status"] == "failed"
    assert "U+FFFD" in (step.get("error") or "")


def test_executor_fails_when_contract_repair_still_invalid(auth_client, configure_codex):
    configure_codex()
    runtime = SequenceRuntime(["not-json", "still-not-json"])
    set_runtime_override(runtime)
    try:
        graph = {
            "nodes": [
                _contract_node(
                    "n_gen",
                    prompt="生成结构化结果",
                    output_contract=_json_contract("title"),
                ),
            ],
            "execution_edges": [],
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


def test_executor_keeps_output_node_html_only(auth_client, configure_codex):
    configure_codex()
    graph = {
        "nodes": [
            _contract_node(
                "n_gen",
                prompt="生成结构化结果 [[respond:{\"title\":\"OK\"}]]",
                output_contract=_json_contract("title"),
            ),
            _output_node("n_out", source="n_gen", prompt="展示 [[respond:<section>OK</section>]]"),
        ],
        "execution_edges": [{"id": "e1", "source": "n_gen", "target": "n_out"}],
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


def test_executor_output_node_accepts_json_wrapped_html(auth_client, configure_codex):
    configure_codex()
    graph = {
        "nodes": [
            _generate_node("n_gen", prompt="生成 [[respond:UPSTREAM]]"),
            _output_node("n_out", source="n_gen", prompt='展示 [[respond:{"html":"<section>JSON_OK</section>"}]]'),
        ],
        "execution_edges": [{"id": "e1", "source": "n_gen", "target": "n_out"}],
    }
    app_id = _build_app(auth_client, graph=graph)
    run = auth_client.post("/api/runs", json={"app_id": app_id, "inputs": {}}).json()
    final = _wait_for_terminal(auth_client, run["run_id"])

    assert final["status"] == "success", final
    by_id = {step["node_id"]: step for step in final["steps"]}
    assert by_id["n_out"]["output"] == "<section>JSON_OK</section>"


def test_executor_output_node_returns_raw_html_without_sanitizing(auth_client, configure_codex):
    configure_codex()
    html = (
        "<!DOCTYPE html><html lang=\"zh-CN\"><head><meta charset=\"utf-8\">"
        "<style>body { color: red; }</style><script>window.rawHtml = true;</script></head>"
        "<body><img src=\"javascript:alert(1)\" onerror=\"alert(2)\"><section>VISIBLE</section></body></html>"
    )
    graph = {
        "nodes": [
            _generate_node("n_gen", prompt="生成 [[respond:UPSTREAM]]"),
            _output_node("n_out", source="n_gen", prompt=f"展示 [[respond:{html}]]"),
        ],
        "execution_edges": [{"id": "e1", "source": "n_gen", "target": "n_out"}],
    }
    app_id = _build_app(auth_client, graph=graph)
    run = auth_client.post("/api/runs", json={"app_id": app_id, "inputs": {}}).json()
    final = _wait_for_terminal(auth_client, run["run_id"])

    assert final["status"] == "success", final
    output = {step["node_id"]: step for step in final["steps"]}["n_out"]["output"]
    assert output == html


def test_executor_output_node_rejects_tool_result_html_without_final_html(auth_client, configure_codex):
    configure_codex()
    runtime = ToolResultOnlyHtmlRuntime()
    set_runtime_override(runtime)
    graph = {
        "nodes": [
            _generate_node("n_gen", prompt="生成"),
            _output_node("n_out", source="n_gen", prompt="展示最终 HTML"),
        ],
        "execution_edges": [{"id": "e1", "source": "n_gen", "target": "n_out"}],
    }

    try:
        app_id = _build_app(auth_client, graph=graph)
        run = auth_client.post("/api/runs", json={"app_id": app_id, "inputs": {}}).json()
        final = _wait_for_terminal(auth_client, run["run_id"])
    finally:
        set_runtime_override(MockRuntime())

    assert final["status"] == "failed", final
    assert final["failure_kind"] == "contract"
    by_id = {step["node_id"]: step for step in final["steps"]}
    assert by_id["n_gen"]["status"] == "success"
    assert by_id["n_out"]["status"] == "failed"
    assert "最终展示节点必须返回可渲染 HTML" in (by_id["n_out"].get("error") or "")
    assert by_id["n_out"].get("output") is None


def test_executor_propagates_failure(auth_client, configure_codex):
    configure_codex()
    graph = {
        "nodes": [
            _generate_node("n_gen_fail", prompt="please fail-now"),
            _output_node("n_out", source="n_gen_fail"),
        ],
        "execution_edges": [{"id": "e1", "source": "n_gen_fail", "target": "n_out"}],
    }
    app_id = _build_app(auth_client, graph=graph)
    run = auth_client.post("/api/runs", json={"app_id": app_id, "inputs": {}}).json()
    final = _wait_for_terminal(auth_client, run["run_id"])
    assert final["status"] == "failed"
    assert final["failure_kind"] == "runtime"
    by_id = {step["node_id"]: step for step in final["steps"]}
    assert by_id["n_gen_fail"]["status"] == "failed"
    assert "mock failed" in (by_id["n_gen_fail"].get("error") or "")
    # 上游失败后下游 step 应保持 pending（未启动）。
    assert by_id["n_out"]["status"] == "pending"


def test_executor_classifies_unavailable_upstream_artifact_as_integrity(
    auth_client,
    configure_codex,
    monkeypatch,
):
    configure_codex()
    original_refs = node_handlers._runtime_upload_refs_for_node

    def unavailable_refs(ctx, node, step):
        if node.get("id") == "n_out":
            raise WorkflowDataIntegrityError("artifacts/n_gen/missing.txt")
        return original_refs(ctx, node, step)

    monkeypatch.setattr(node_handlers, "_runtime_upload_refs_for_node", unavailable_refs)
    graph = {
        "nodes": [
            _generate_node("n_gen", prompt="生成 [[respond:READY]]"),
            _output_node("n_out", source="n_gen"),
        ],
        "execution_edges": [{"id": "e1", "source": "n_gen", "target": "n_out"}],
    }
    app_id = _build_app(auth_client, graph=graph)
    run = auth_client.post("/api/runs", json={"app_id": app_id, "inputs": {}}).json()
    final = _wait_for_terminal(auth_client, run["run_id"])

    assert final["status"] == "failed"
    assert final["failure_kind"] == "integrity"
    output_step = next(step for step in final["steps"] if step["node_id"] == "n_out")
    assert output_step["failure_kind"] == "integrity"
    assert "输入 artifact 完整性校验失败" in output_step["error"]


def test_executor_cancel_during_running_step(auth_client, configure_codex):
    configure_codex()
    graph = {
        "nodes": [
            _generate_node("n_gen_slow", prompt="please [[delay:1.5]] [[respond:LATE]]"),
        ],
        "execution_edges": [],
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


def test_executor_late_success_after_cancel_does_not_overwrite_cancelled_step(auth_client, configure_codex):
    configure_codex()
    runtime = LateSuccessAfterCancelRuntime()
    set_runtime_override(runtime)
    try:
        graph = {
            "nodes": [
                _generate_node("n_gen_late", prompt="ignore cancel and return late success"),
            ],
            "execution_edges": [],
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
        assert "agent_session_id" not in step
    finally:
        set_runtime_override(MockRuntime())


def test_executor_passes_upstream_output_into_generate_prompt(auth_client, configure_codex):
    """generate 节点应该能在自己的 prompt 中看到上游 asset 的 output。"""

    configure_codex()
    graph = {
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
        "execution_edges": [{"id": "e1", "source": "n_asset", "target": "n_gen"}],
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
    assert "不要再次向用户提问" in prompt


def test_executor_output_reads_all_ancestors_without_embedding_values_in_prompt(auth_client, configure_codex):
    configure_codex()
    saved = auth_client.put(
        "/api/settings/prompts/output_html_rendering",
        json={"content": "$user_prompt\n\nMIRA_DB_HTML_RULE"},
    )
    assert saved.status_code == 200, saved.text
    graph = {
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
        "execution_edges": [
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
    assert "PRIMARY_MARK" not in prompt
    assert "OTHER_MARK" not in prompt
    assert "你拥有一个名为 `ask_user` 的工具" not in prompt
    assert "MIRA_DB_HTML_RULE" in prompt
    assert prompt.index("整理 [[respond:<section>FINAL</section>]]") < prompt.index("MIRA_DB_HTML_RULE")
    context_files = list(
        (run_workspace(ADMIN_USER_ID, app_id, run["run_id"]) / "agent" / "branches").glob(
            "*/work/.mira/run-context/n_asset.json"
        )
    )
    assert len(context_files) == 1
    assert json.loads(context_files[0].read_text(encoding="utf-8"))["value"] == "OTHER_MARK"


def test_executor_file_asset_outputs_upload_meta_list(auth_client, configure_codex):
    configure_codex()
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
        "execution_edges": [],
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


def test_executor_url_asset_outputs_url_list(auth_client, configure_codex):
    configure_codex()
    graph = {
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
        "execution_edges": [],
    }
    app_id = _build_app(auth_client, graph=graph)
    run = auth_client.post("/api/runs", json={"app_id": app_id, "inputs": {}}).json()
    final = _wait_for_terminal(auth_client, run["run_id"])
    assert final["status"] == "success", final
    step = final["steps"][0]
    assert step["status"] == "success"
    assert step["output"] == ["https://example.com/a", "https://example.com/b"]
    assert step["input"] == {"asset_kind": "url", "urls": ["https://example.com/a", "https://example.com/b"]}


def test_executor_user_input_attachment_prompt_includes_download_url(auth_client, configure_codex):
    configure_codex()
    uploaded = auth_client.post(
        "/api/uploads",
        files={"file": ("reference.txt", b"reference content", "text/plain")},
    )
    assert uploaded.status_code == 200, uploaded.text
    upload = uploaded.json()
    graph = {
        "nodes": [
            USER_INPUT_NODE,
            _generate_node("n_gen", prompt="读取附件 [[respond:DONE]]"),
        ],
        "execution_edges": [{"id": "e1", "source": "n_input", "target": "n_gen"}],
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
    assert f'"/api/uploads/{upload["id"]}?download_token=' not in prompt
    context_files = list(
        (run_workspace(ADMIN_USER_ID, app_id, run["run_id"]) / "agent" / "branches").glob(
            "*/work/.mira/run-context/n_input.json"
        )
    )
    assert len(context_files) == 1
    attachment = json.loads(context_files[0].read_text(encoding="utf-8"))["value"]["attachments"][0]
    assert attachment["download_url"].startswith(f"/api/uploads/{upload['id']}?download_token=")
    assert attachment["workspace_path"] == f"/workspace/inputs/{upload['id']}/reference.txt"
    copied = context_files[0].parents[2] / "inputs" / upload["id"] / "reference.txt"
    assert copied.read_bytes() == b"reference content"
    assert str(uploads_dir("user_admin") / upload["id"] / "blob") not in json.dumps(final)


def test_executor_redacts_undeclared_workspace_paths_without_download_side_channel(
    auth_client,
    configure_codex,
):
    configure_codex()
    graph = {
        "nodes": [
            _generate_node("n_gen", prompt="生成文件路径 [[respond:PLACEHOLDER]]"),
            _output_node("n_out", source="n_gen", prompt="展示 [[respond:<section>FINAL</section>]]"),
        ],
        "execution_edges": [{"id": "e1", "source": "n_gen", "target": "n_out"}],
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

    assert final["status"] == "success", final
    by_id = {step["node_id"]: step for step in final["steps"]}
    gen_prompt = by_id["n_out"]["input"]["prompt"]
    output_html = by_id["n_out"]["output"]
    assert "[local path redacted]" not in gen_prompt
    assert "[local path redacted]" in output_html
    assert "/runtime/workspaces/" not in gen_prompt
    assert "/runtime/workspaces/" not in output_html
    assert "download_url: /api/runs/" not in gen_prompt
    assert "/api/runs/" not in output_html
    assert "download_token=" not in output_html

    artifacts_response = auth_client.get(f"/api/runs/{run_id}/artifacts")
    assert artifacts_response.status_code == 200, artifacts_response.text
    artifacts_body = artifacts_response.json()
    assert artifacts_body["truncated"] is False
    assert artifacts_body["artifacts"] == []


def test_executor_transfers_generated_images_only_as_declared_artifacts(auth_client, configure_codex):
    configure_codex()
    graph = {
        "nodes": [
            _contract_node(
                "n_gen",
                prompt="生成配图",
                output_contract={"type": "artifact", "artifact_kind": "image", "max_count": 1},
            ),
            _output_node("n_out", source="n_gen", prompt="展示 [[respond:<section>FINAL</section>]]"),
        ],
        "execution_edges": [{"id": "e1", "source": "n_gen", "target": "n_out"}],
    }
    app_id = _build_app(auth_client, graph=graph)
    runtime = GeneratedImageRuntime()
    set_runtime_override(runtime)
    try:
        run = auth_client.post("/api/runs", json={"app_id": app_id, "inputs": {}}).json()
        run_id = run["run_id"]
        final = _wait_for_terminal(auth_client, run_id)
    finally:
        set_runtime_override(MockRuntime())

    assert final["status"] == "success", final
    by_id = {step["node_id"]: step for step in final["steps"]}
    gen_output = by_id["n_gen"]["output"]
    assert len(gen_output) == 1
    assert gen_output[0]["path"].startswith("artifacts/n_gen/artifact_")
    assert gen_output[0]["path"].endswith("/cover.png")
    assert "/mnt/results" not in by_id["n_out"]["input"]["prompt"]
    assert "generated_images" not in by_id["n_out"]["input"]["prompt"]
    workspace_files = list(
        (run_workspace(ADMIN_USER_ID, app_id, run_id) / "agent" / "branches").glob(
            "*/work/cover.png"
        )
    )
    assert len(workspace_files) == 1
    assert workspace_files[0].read_bytes() == b"png-bytes"

    output_html = by_id["n_out"]["output"]
    assert output_html == "<section>FINAL</section>"

    artifacts_response = auth_client.get(f"/api/runs/{run_id}/artifacts")
    assert artifacts_response.status_code == 200, artifacts_response.text
    artifacts = artifacts_response.json()["artifacts"]
    assert len(artifacts) == 1
    assert artifacts[0]["name"] == "cover.png"
    assert artifacts[0]["integrity"] == "verified"

    src = artifacts[0]["download_url"]
    headers = dict(auth_client.headers)
    auth_client.headers.pop("Authorization", None)
    try:
        downloaded = auth_client.get(src)
    finally:
        auth_client.headers.update(headers)
    assert downloaded.status_code == 200, downloaded.text
    assert downloaded.content == b"png-bytes"


def test_run_artifacts_list_uses_artifact_contract_metadata(auth_client, configure_codex):
    configure_codex()
    graph = {
        "nodes": [
            _contract_node(
                "n_gen",
                prompt="生成文件产物",
                output_contract={"type": "artifact", "artifact_kind": "file"},
            ),
        ],
        "execution_edges": [],
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
    assert "path" not in artifact
    assert artifact["size"] == len("artifact report".encode("utf-8"))
    assert "source_kind" not in artifact
    assert artifact["origin_run_id"] == run_id
    assert artifact["origin_artifact_id"] == artifact["id"]
    assert artifact["origin_node_id"] == "n_gen"
    assert artifact["origin_node_title"] == "n_gen"
    assert artifact["mime"] == "text/plain"
    assert artifact["download_url"].startswith(f"/api/runs/{run_id}/artifacts/artifacts/n_gen/artifact_")
    assert "/tmp/private/export.pdf" not in json.dumps(body)
    assert "javascript:" not in json.dumps(body)


def test_file_asset_missing_upload_is_rejected_when_saving_graph(auth_client):
    graph = {
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
        "execution_edges": [],
    }
    created = auth_client.post("/api/apps", json={"name": "RunExecApp"}).json()
    response = auth_client.patch(f"/api/apps/{created['id']}", json={"graph": _ensure_output(graph)})
    assert response.status_code == 400
    assert response.json()["detail"] == "素材上传文件不存在"
