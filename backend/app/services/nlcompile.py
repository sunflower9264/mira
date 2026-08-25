from __future__ import annotations

import asyncio
import json
import logging
import math
import re
import time
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db import SessionLocal
from app.models import NlCompileSessionRow
from app.runtime.base import AgentChunk, DecisionAttachment, DecisionRequest, DecisionResult
from app.runtime.factory import get_runtime
from app.schemas.requests import NlCompileRefineIn, NlCompileResumeIn
from app.schemas.runs import RunAttachmentRef
from app.services.decision_prompts import append_none_option, validate_decision_groups, validate_decision_answers
from app.services.execution_plan import ExecutionPlanError, compile_execution_plan
from app.services.graph_validation import (
    GraphValidationError,
    topological_order,
    validate_prompt_nodes,
)
from app.services.graph_inputs import prepare_planning_graph, prepare_structural_graph
from app.services.graph_layout import beautify_graph_layout_with_runtime
from app.services import runtime_config
from app.services.prompts import get_prompt_content, render_prompt
from app.services.prompt_assistant import (
    build_nlcompile_prompt_refiner_prompt,
    prompt_internal_terms,
    run_prompt_assistant,
)
from app.services.prompt_contracts import (
    append_patch_protocol,
    build_structured_repair_prompt,
    max_attempts_for,
    output_schema_for,
)
from app.services.runtime_paths import nlcompile_workspace
from app.services.runtime_uploads import RuntimeUploadRef, rewrite_runtime_upload_paths, runtime_upload_context
from app.services.reasoning_effort import max_reasoning_effort
from app.services.structured_output import parse_structured_json_object
from app.services.template import contains_template_token, strip_template_tokens
from app.services.tools import RuntimeToolConfig, planning_runtime_tools_for_graph
from app.services.uploads import resolve_upload
from app.utils import now_utc

logger = logging.getLogger(__name__)

# 与前端 CONDITION_DEFAULT_BRANCH_KEY 对齐；condition cases 模式的隐式兜底 handle，
# 用户分支不能复用这个 key。
DEFAULT_BRANCH_KEY = "__default__"

NODE_TYPE_LABELS = {
    "user_input": "用户输入",
    "generate": "生成",
    "output": "输出",
    "asset": "素材",
    "condition": "条件分支",
}


@dataclass
class NlCompileSession:
    id: str
    user_id: str
    app_id: str
    response_future: asyncio.Future[dict[str, Any]]
    instruction: str
    current_graph: dict[str, Any]
    confirmed_plan: dict[str, Any] | None = None
    plan_markdown: str | None = None
    history: list[dict[str, Any]] = field(default_factory=list)
    qa_history: list[dict[str, Any]] = field(default_factory=list)
    task: asyncio.Task[None] | None = None
    resume_future: asyncio.Future[DecisionResult] | None = None
    current_request: DecisionRequest | None = None
    cancel_event: asyncio.Event | None = None
    waiter_count: int = 0


@dataclass
class PatchAttempt:
    index: int
    sanitized: dict[str, Any]
    reason: str


@dataclass
class PatchFailure:
    kind: str
    reason: str
    index: int | None = None
    patch: dict[str, Any] | None = None


_compile_sessions: dict[str, NlCompileSession] = {}
_cancel_tombstones: dict[tuple[str, str], float] = {}
_CANCEL_TOMBSTONE_TTL_SEC = 30.0
_CANCELLED_DETAIL = "编译已取消"
_NL_COMPILE_REPAIR_FAILED_DETAIL = "Agent 生成的修改未通过校验，请调整描述后重试"
_NL_COMPILE_PROMPT_MAX_BYTES = 200 * 1024
_NL_COMPILE_REFINE_LIMIT = 5
_NL_COMPILE_STALE_ACTIVE_SEC = 24 * 60 * 60
_NL_COMPILE_TERMINAL_STATUSES = {"completed", "failed", "cancelled"}
_NL_COMPILE_ACTIVE_STATUSES = {"planning", "waiting_for_user", "planned", "applying", "interrupted"}
_FALLBACK_NODE_X_GAP = 320.0
_FALLBACK_NODE_Y_GAP = 180.0
_FALLBACK_NODE_COLUMNS = 4


def _ensure_prompt_size(prompt: str) -> None:
    if len(prompt.encode("utf-8")) > _NL_COMPILE_PROMPT_MAX_BYTES:
        raise HTTPException(status_code=400, detail="画布或素材内容过大，无法进行自然语言编辑；请精简素材内容后重试")


def _json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def _json_loads(value: str | None, fallback: Any) -> Any:
    if not value:
        return deepcopy(fallback)
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return deepcopy(fallback)


def _history_from_row(row: NlCompileSessionRow) -> list[dict[str, Any]]:
    history = _json_loads(row.history_json, [])
    return [item for item in history if isinstance(item, dict)] if isinstance(history, list) else []


def _qa_history_from_history(history: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [entry for entry in history if entry.get("kind") == "decision_request"]


def _initial_attachment_history(
    user_id: str,
    attachments: list[RunAttachmentRef],
) -> list[dict[str, Any]]:
    items: list[dict[str, str]] = []
    seen: set[str] = set()
    for ref in attachments:
        if ref.id in seen:
            continue
        resolved = resolve_upload(user_id, ref.id)
        if resolved is None:
            raise HTTPException(status_code=404, detail="附件不存在")
        seen.add(ref.id)
        items.append({"id": resolved.id, "name": ref.name or resolved.name})
    return [{"kind": "initial_attachments", "attachments": items}] if items else []


def _initial_attachment_runtime_refs(
    user_id: str,
    history: list[dict[str, Any]],
) -> list[RuntimeUploadRef]:
    refs: list[RuntimeUploadRef] = []
    seen: set[str] = set()
    for entry in history:
        if entry.get("kind") != "initial_attachments":
            continue
        items = entry.get("attachments")
        if not isinstance(items, list):
            continue
        for item in items:
            if not isinstance(item, dict):
                continue
            upload_id = item.get("id")
            if not isinstance(upload_id, str) or upload_id in seen:
                continue
            resolved = resolve_upload(user_id, upload_id)
            if resolved is None:
                raise HTTPException(status_code=404, detail="附件不存在")
            name = item.get("name") if isinstance(item.get("name"), str) else resolved.name
            refs.append(RuntimeUploadRef(id=resolved.id, path=resolved.path, name=name))
            seen.add(upload_id)
    return refs


def _append_initial_attachment_context(prompt: str, refs: list[RuntimeUploadRef]) -> str:
    if not refs:
        return prompt
    lines = [
        "## 用户首次提交的附件",
        "以下文件是本次自然语言编辑的输入。请按需使用工具读取文件内容，不要只根据文件名推测。",
    ]
    for ref in refs:
        lines.append(f"- {ref.name or ref.path.name}：{ref.path}")
    return "\n\n".join([prompt.strip(), "\n".join(lines)])


def _plan_from_row(row: NlCompileSessionRow) -> dict[str, Any] | None:
    plan = _json_loads(row.plan_json, None)
    return plan if isinstance(plan, dict) else None


def _graph_from_row(row: NlCompileSessionRow) -> dict[str, Any]:
    graph = _json_loads(row.graph_json, {"nodes": [], "execution_edges": []})
    return prepare_planning_graph(graph) if isinstance(graph, dict) else {"nodes": [], "execution_edges": []}


async def _get_compile_row(db: AsyncSession, user_id: str, compile_id: str) -> NlCompileSessionRow:
    row = await db.get(NlCompileSessionRow, compile_id)
    if row is None or row.user_id != user_id:
        raise HTTPException(status_code=404, detail="编译会话不存在")
    return row


async def _write_compile_row(
    compile_id: str,
    *,
    status: str | None = None,
    history: list[dict[str, Any]] | None = None,
    pending_request: dict[str, Any] | None | object = ...,
    plan: dict[str, Any] | None | object = ...,
    plan_markdown: str | None | object = ...,
    error: str | None | object = ...,
) -> None:
    async with SessionLocal() as db:
        row = await db.get(NlCompileSessionRow, compile_id)
        if row is None:
            return
        _apply_row_update(
            row,
            status=status,
            history=history,
            pending_request=pending_request,
            plan=plan,
            plan_markdown=plan_markdown,
            error=error,
        )
        await db.commit()


def _apply_row_update(
    row: NlCompileSessionRow,
    *,
    status: str | None = None,
    history: list[dict[str, Any]] | None = None,
    pending_request: dict[str, Any] | None | object = ...,
    plan: dict[str, Any] | None | object = ...,
    plan_markdown: str | None | object = ...,
    error: str | None | object = ...,
) -> None:
    if status is not None:
        row.status = status
    if history is not None:
        row.history_json = _json_dumps(history)
    if pending_request is not ...:
        row.pending_request_json = None if pending_request is None else _json_dumps(pending_request)
    if plan is not ...:
        row.plan_json = None if plan is None else _json_dumps(plan)
    if plan_markdown is not ...:
        row.plan_markdown = plan_markdown
    if error is not ...:
        row.error = error
    row.updated_at = now_utc()


def _session_from_row(row: NlCompileSessionRow) -> NlCompileSession:
    history = _history_from_row(row)
    return NlCompileSession(
        id=row.id,
        user_id=row.user_id,
        app_id=row.app_id,
        response_future=asyncio.get_running_loop().create_future(),
        instruction=row.instruction,
        current_graph=deepcopy(_graph_from_row(row)),
        confirmed_plan=_plan_from_row(row),
        plan_markdown=row.plan_markdown,
        history=history,
        qa_history=_qa_history_from_history(history),
        cancel_event=asyncio.Event(),
    )


def _active_payload_from_row(row: NlCompileSessionRow) -> dict[str, Any]:
    plan = _plan_from_row(row)
    request = _json_loads(row.pending_request_json, None)
    if row.status == "waiting_for_user" and isinstance(request, dict):
        return {"status": "waiting_for_user", "compile_id": row.id, "request": request}
    if row.status == "planned" and plan is not None and row.plan_markdown:
        return {
            "status": "planned",
            "compile_id": row.id,
            "plan": plan,
            "plan_markdown": row.plan_markdown,
        }
    return {
        "status": row.status,
        "compile_id": row.id,
        "instruction": row.instruction,
        "request": request if isinstance(request, dict) else None,
        "plan": plan,
        "plan_markdown": row.plan_markdown,
        "error": row.error,
    }


def _row_age_seconds(row: NlCompileSessionRow) -> float:
    updated_at = row.updated_at
    now = now_utc()
    if updated_at.tzinfo is None:
        updated_at = updated_at.replace(tzinfo=now.tzinfo)
    return (now - updated_at).total_seconds()


async def _create_compile_row(
    db: AsyncSession,
    *,
    session_id: str,
    user_id: str,
    app_id: str,
    instruction: str,
    current_graph: dict[str, Any],
    history: list[dict[str, Any]],
) -> NlCompileSessionRow:
    row = NlCompileSessionRow(
        id=session_id,
        user_id=user_id,
        app_id=app_id,
        status="planning",
        instruction=instruction,
        graph_json=_json_dumps(current_graph),
        history_json=_json_dumps(history),
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return row


# --- 编译入口 -------------------------------------------------------------


async def compile_graph(
    db: AsyncSession,
    user_id: str,
    app_id: str,
    instruction: str,
    current_graph: dict[str, Any],
    compile_id: str | None = None,
    attachments: list[RunAttachmentRef] | None = None,
) -> dict[str, Any]:
    """根据自然语言指令调用 LLM 生成待确认方案，不提前生成 graph patch。"""

    current_graph = prepare_planning_graph(current_graph)
    session_id = (compile_id or "").strip() or f"nlc_{uuid4().hex[:16]}"
    _purge_cancel_tombstones()
    if _consume_cancel_tombstone(user_id, session_id):
        raise HTTPException(status_code=409, detail=_CANCELLED_DETAIL)
    existing = _compile_sessions.get(session_id)
    if existing is not None:
        if existing.user_id != user_id:
            raise HTTPException(status_code=404, detail="编译会话不存在")
        return await _await_compile_response(existing)

    row = await db.get(NlCompileSessionRow, session_id)
    if row is None:
        history = _initial_attachment_history(user_id, attachments or [])
        row = await _create_compile_row(
            db,
            session_id=session_id,
            user_id=user_id,
            app_id=app_id,
            instruction=instruction,
            current_graph=deepcopy(current_graph),
            history=history,
        )
    elif row.user_id != user_id or row.app_id != app_id:
        raise HTTPException(status_code=404, detail="编译会话不存在")
    elif row.status == "interrupted":
        _apply_row_update(row, status="planning", pending_request=None, error=None)
        await db.commit()
        await db.refresh(row)
    elif row.status in {"waiting_for_user", "planned"}:
        return _active_payload_from_row(row)
    elif row.status in {"planning", "applying"}:
        return _active_payload_from_row(row)
    else:
        raise HTTPException(status_code=409, detail="编译会话已结束")

    try:
        session = await _start_plan_session_from_row(db, row)
    except HTTPException as exc:
        _apply_row_update(row, status="failed", error=str(exc.detail))
        await db.commit()
        raise
    return await _await_compile_response(session)


async def apply_compile(
    db: AsyncSession,
    user_id: str,
    compile_id: str,
) -> dict[str, Any]:
    row = await _get_compile_row(db, user_id, compile_id)
    if row.status not in {"planned", "interrupted"} or _plan_from_row(row) is None:
        raise HTTPException(status_code=409, detail="当前编译会话不能应用")

    session = _compile_sessions.get(compile_id)
    if session is None or session.user_id != user_id:
        session = _session_from_row(row)
    session.confirmed_plan = _plan_from_row(row)
    session.plan_markdown = row.plan_markdown
    session.history = _history_from_row(row)
    session.qa_history = _qa_history_from_history(session.history)

    await runtime_config.write_configs(db)
    prompt_template = await get_prompt_content(db, "nlcompile_graph_patch")
    prompt_assistant_template = await get_prompt_content(db, "nlcompile_prompt_refiner")
    graph_layout_template = await get_prompt_content(db, "graph_layout_beautify")
    attachment_refs = _initial_attachment_runtime_refs(user_id, session.history)
    prompt = build_patch_prompt(
        session.instruction,
        session.current_graph,
        session.confirmed_plan,
        prompt_template,
        history=session.history,
    )
    prompt = _append_initial_attachment_context(prompt, attachment_refs)
    _ensure_prompt_size(prompt)
    runtime = get_runtime()
    _apply_row_update(row, status="applying", error=None)
    await db.commit()
    try:
        result = await _execute_apply_session(
            session=session,
            runtime=runtime,
            prompt=prompt,
            prompt_assistant_template=prompt_assistant_template,
            graph_layout_template=graph_layout_template,
            user_id=user_id,
            attachment_refs=attachment_refs,
        )
        _apply_row_update(row, status="completed", error=None)
        await db.commit()
        return result
    except HTTPException as exc:
        await db.refresh(row)
        if row.status != "cancelled":
            _apply_row_update(row, status="failed", error=str(exc.detail))
            await db.commit()
        raise
    finally:
        _compile_sessions.pop(session.id, None)


async def cancel_compile(db: AsyncSession, user_id: str, compile_id: str) -> None:
    _purge_cancel_tombstones()
    session = _compile_sessions.get(compile_id)
    row = await db.get(NlCompileSessionRow, compile_id)
    if row is not None and row.user_id != user_id:
        raise HTTPException(status_code=404, detail="编译会话不存在")
    if row is not None:
        _apply_row_update(row, status="cancelled", error=_CANCELLED_DETAIL)
        await db.commit()
    if session is None:
        if row is None:
            _cancel_tombstones[(user_id, compile_id)] = time.monotonic()
        return
    if session.user_id != user_id:
        raise HTTPException(status_code=404, detail="编译会话不存在")
    _cancel_compile_session(session, notify_waiter=True)


async def resume_compile(
    db: AsyncSession,
    user_id: str,
    compile_id: str,
    payload: NlCompileResumeIn,
) -> dict[str, Any]:
    row = await _get_compile_row(db, user_id, compile_id)
    if row.status in _NL_COMPILE_TERMINAL_STATUSES:
        raise HTTPException(status_code=404, detail="编译会话不存在")
    session = _compile_sessions.get(compile_id)
    if session is not None and session.user_id != user_id:
        raise HTTPException(status_code=404, detail="编译会话不存在")

    request_payload = _json_loads(row.pending_request_json, None)
    if session is not None and session.current_request is not None:
        request = session.current_request
    elif isinstance(request_payload, dict):
        request = DecisionRequest.model_validate(request_payload)
    else:
        raise HTTPException(status_code=409, detail="当前没有等待输入")

    result = _build_decision_request_result(user_id, request, payload)
    history = session.history if session is not None else _history_from_row(row)
    entry = _qa_history_entry(request, result)
    history.append(entry)
    if session is not None:
        session.history = history
        session.qa_history.append(entry)
    _apply_row_update(row, status="planning", history=history, pending_request=None, error=None)
    await db.commit()

    if session is not None and session.resume_future is not None and not session.resume_future.done():
        session.resume_future.set_result(result)
        return await _await_compile_response(session)

    await db.refresh(row)
    try:
        session = await _start_plan_session_from_row(db, row)
    except HTTPException as exc:
        _apply_row_update(row, status="failed", error=str(exc.detail))
        await db.commit()
        raise
    return await _await_compile_response(session)


def _build_decision_request_result(
    user_id: str,
    request: DecisionRequest,
    payload: NlCompileResumeIn,
) -> DecisionResult:
    text = (payload.text or "").strip() or None
    attachment_refs = list(payload.attachments or [])
    if not payload.answers and not text and not attachment_refs:
        raise HTTPException(status_code=400, detail="必须至少提供一项输入")
    if payload.answers:
        answer_error = validate_decision_answers(request.groups, payload.answers)
        if answer_error:
            raise HTTPException(status_code=400, detail=answer_error)

    max_text = get_settings().max_resume_text_bytes
    if text is not None and len(text.encode("utf-8")) > max_text:
        raise HTTPException(status_code=400, detail="补充文本过长")

    attachments: list[DecisionAttachment] = []
    for ref in attachment_refs:
        resolved = resolve_upload(user_id, ref.id)
        if resolved is None:
            raise HTTPException(status_code=404, detail="附件不存在")
        attachments.append(
            DecisionAttachment(
                id=resolved.id,
                name=ref.name or resolved.name,
                path=str(resolved.path),
                download_url=resolved.download_url(user_id),
                mime=resolved.mime,
                size=resolved.size,
            )
        )
    return DecisionResult(ok=True, answers=payload.answers, text=text, attachments=attachments)


async def refine_compile(
    db: AsyncSession,
    user_id: str,
    compile_id: str,
    payload: NlCompileRefineIn,
) -> dict[str, Any]:
    feedback = payload.feedback.strip()
    if not feedback:
        raise HTTPException(status_code=400, detail="补充修改说明不能为空")
    row = await _get_compile_row(db, user_id, compile_id)
    if row.status != "planned" or _plan_from_row(row) is None:
        raise HTTPException(status_code=409, detail="当前编译会话不能继续调整方案")
    history = _history_from_row(row)
    if sum(1 for entry in history if entry.get("kind") == "refine") >= _NL_COMPILE_REFINE_LIMIT:
        raise HTTPException(status_code=400, detail="方案调整轮数过多，请重新发起自然语言编辑")
    history.append({"kind": "refine", "feedback": feedback})
    _apply_row_update(row, status="planning", history=history, pending_request=None, error=None)
    await db.commit()
    await db.refresh(row)

    existing = _compile_sessions.get(compile_id)
    if existing is not None and existing.task is not None and not existing.task.done():
        _cancel_compile_session(existing)
    try:
        session = await _start_plan_session_from_row(db, row)
    except HTTPException as exc:
        _apply_row_update(row, status="failed", error=str(exc.detail))
        await db.commit()
        raise
    return await _await_compile_response(session)


async def active_compile_for_app(db: AsyncSession, user_id: str, app_id: str) -> dict[str, Any] | None:
    row = (
        await db.execute(
            select(NlCompileSessionRow)
            .where(
                NlCompileSessionRow.user_id == user_id,
                NlCompileSessionRow.app_id == app_id,
                NlCompileSessionRow.status.in_(_NL_COMPILE_ACTIVE_STATUSES),
            )
            .order_by(NlCompileSessionRow.updated_at.desc(), NlCompileSessionRow.created_at.desc())
        )
    ).scalars().first()
    if row is None:
        return None
    if _row_age_seconds(row) > _NL_COMPILE_STALE_ACTIVE_SEC:
        _apply_row_update(row, status="cancelled", error="自然语言编辑会话已过期")
        await db.commit()
        return None
    return _active_payload_from_row(row)


async def mark_active_nlcompile_sessions_interrupted(db: AsyncSession) -> int:
    rows = (
        await db.execute(
            select(NlCompileSessionRow)
            .where(NlCompileSessionRow.status.in_(["planning", "waiting_for_user", "applying"]))
            .order_by(NlCompileSessionRow.created_at.asc(), NlCompileSessionRow.id.asc())
        )
    ).scalars().all()
    changed = 0
    for row in rows:
        _apply_row_update(row, status="interrupted", error="后端进程重启，自然语言编辑已暂停")
        changed += 1
    if changed:
        await db.commit()
    return changed


async def _start_plan_session_from_row(db: AsyncSession, row: NlCompileSessionRow) -> NlCompileSession:
    graph = _graph_from_row(row)

    await runtime_config.write_configs(db)
    prompt_template = await get_prompt_content(db, "nlcompile_plan")
    history = _history_from_row(row)
    attachment_refs = _initial_attachment_runtime_refs(row.user_id, history)
    prompt = build_plan_prompt(row.instruction, graph, prompt_template, history=history)
    prompt = _append_initial_attachment_context(prompt, attachment_refs)
    _ensure_prompt_size(prompt)
    runtime = get_runtime()
    planning_runtime_tools = await planning_runtime_tools_for_graph(db, graph)
    session = _session_from_row(row)
    session.response_future = asyncio.get_running_loop().create_future()
    session.cancel_event = asyncio.Event()
    _compile_sessions[session.id] = session
    session.task = asyncio.create_task(
        _run_plan_session(
            session=session,
            runtime=runtime,
            prompt=prompt,
            runtime_tools=planning_runtime_tools,
            attachment_refs=attachment_refs,
        ),
        name=f"nlcompile-{session.id}",
    )
    return session


async def _run_plan_session(
    *,
    session: NlCompileSession,
    runtime: Any,
    prompt: str,
    runtime_tools: RuntimeToolConfig | None,
    attachment_refs: list[RuntimeUploadRef],
) -> None:
    async def on_decision_request(request: DecisionRequest) -> DecisionResult:
        protocol_error = validate_decision_groups(request.groups)
        if protocol_error:
            return DecisionResult(ok=False, error=protocol_error)
        request = request.model_copy(update={"groups": append_none_option(request.groups)})
        if session.resume_future is not None and not session.resume_future.done():
            return DecisionResult(ok=False, error="不允许并发 decision_request")
        loop = asyncio.get_running_loop()
        session.current_request = request
        session.resume_future = loop.create_future()
        request_payload = request.model_dump(exclude_none=True)
        await _write_compile_row(
            session.id,
            status="waiting_for_user",
            pending_request=request_payload,
            error=None,
        )
        _publish_compile_response(
            session,
            {
                "status": "waiting_for_user",
                "compile_id": session.id,
                "request": request_payload,
            },
        )
        try:
            return await session.resume_future
        finally:
            session.current_request = None
            session.resume_future = None

    try:
        chunks: list[str] = []
        async def on_chunk(chunk: AgentChunk) -> None:
            if chunk.type == "text" and chunk.text:
                chunks.append(chunk.text)

        workspace = nlcompile_workspace(session.user_id)
        with runtime_upload_context(workspace, attachment_refs):
            runtime_prompt = rewrite_runtime_upload_paths(prompt)
            result = await runtime.execute(
                prompt=runtime_prompt,
                session_id=None,
                model=None,
                reasoning_effort=max_reasoning_effort(),
                cwd=workspace,
                on_chunk=on_chunk,
                cancel_event=session.cancel_event or asyncio.Event(),
                on_decision_request=on_decision_request,
                runtime_tools=runtime_tools,
                runtime_policy="plan",
                output_schema=output_schema_for("nlcompile_plan"),
            )
        if result.finished_with != "done":
            logger.warning("nlcompile plan runtime non-done: status=%s error=%s", result.finished_with, result.error)
            await _write_compile_row(
                session.id,
                status="failed",
                pending_request=None,
                error="Agent 编译失败，请检查 Agent 配置或稍后重试",
            )
            _publish_compile_exception(
                session,
                HTTPException(status_code=502, detail="Agent 编译失败，请检查 Agent 配置或稍后重试"),
            )
            return

        raw_output = result.total_text or "".join(chunks)
        plan = await _repair_plan_output_if_needed(
            runtime=runtime,
            prompt=runtime_prompt,
            raw_output=raw_output,
            user_id=session.user_id,
            cancel_event=session.cancel_event or asyncio.Event(),
            attachment_refs=attachment_refs,
        )
        plan_markdown = render_nlcompile_plan_markdown(plan, session.instruction)
        session.confirmed_plan = plan
        session.plan_markdown = plan_markdown
        session.history.append({"kind": "plan", "plan": plan})
        await _write_compile_row(
            session.id,
            status="planned",
            history=session.history,
            pending_request=None,
            plan=plan,
            plan_markdown=plan_markdown,
            error=None,
        )
        _publish_compile_response(
            session,
            {
                "status": "planned",
                "compile_id": session.id,
                "plan": plan,
                "plan_markdown": plan_markdown,
            },
        )
    except HTTPException as exc:
        await _write_compile_row(session.id, status="failed", pending_request=None, error=str(exc.detail))
        _publish_compile_exception(session, exc)
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001
        logger.warning("nlcompile plan runtime failed: %r", exc)
        await _write_compile_row(
            session.id,
            status="failed",
            pending_request=None,
            error="Agent 编译失败，请检查 Agent 配置或稍后重试",
        )
        _publish_compile_exception(
            session,
            HTTPException(status_code=502, detail="Agent 编译失败，请检查 Agent 配置或稍后重试"),
        )


async def _repair_plan_output_if_needed(
    *,
    runtime: Any,
    prompt: str,
    raw_output: str,
    user_id: str,
    cancel_event: asyncio.Event,
    attachment_refs: list[RuntimeUploadRef],
) -> dict[str, Any]:
    try:
        return extract_plan(raw_output)
    except Exception as exc:  # noqa: BLE001
        last_error = str(exc) or "plan JSON 解析失败"

    attempt_prompt = prompt
    previous_output = raw_output
    for attempt in range(2, max_attempts_for("nlcompile_plan") + 1):
        repair_prompt = build_structured_repair_prompt(
            task_name="NL 编译方案确认",
            original_prompt=attempt_prompt,
            previous_output=previous_output,
            validation_error=last_error,
            output_shape='{"plan":{...}}',
            output_schema=output_schema_for("nlcompile_plan"),
        )
        _ensure_prompt_size(repair_prompt)
        chunks: list[str] = []

        async def on_repair_chunk(chunk: AgentChunk) -> None:
            if chunk.type == "text" and chunk.text:
                chunks.append(chunk.text)

        workspace = nlcompile_workspace(user_id)
        with runtime_upload_context(workspace, attachment_refs):
            result = await runtime.execute(
                prompt=rewrite_runtime_upload_paths(repair_prompt),
                session_id=None,
                model=None,
                reasoning_effort=max_reasoning_effort(),
                cwd=workspace,
                on_chunk=on_repair_chunk,
                cancel_event=cancel_event,
                on_decision_request=None,
                runtime_tools=None,
                runtime_policy="plan",
                output_schema=output_schema_for("nlcompile_plan"),
            )
        if result.finished_with != "done":
            raise HTTPException(status_code=502, detail="Agent 编译失败，请检查 Agent 配置或稍后重试")
        previous_output = result.total_text or "".join(chunks)
        try:
            return extract_plan(previous_output)
        except Exception as exc:  # noqa: BLE001
            last_error = str(exc) or "plan JSON 解析失败"
            logger.warning("nlcompile plan repair attempt %s failed: %s", attempt, last_error)

    raise HTTPException(status_code=502, detail=f"Agent 生成的方案未通过校验：{last_error}")


async def _execute_apply_session(
    *,
    session: NlCompileSession,
    runtime: Any,
    prompt: str,
    prompt_assistant_template: str,
    graph_layout_template: str,
    user_id: str,
    attachment_refs: list[RuntimeUploadRef],
) -> dict[str, Any]:
    async def on_decision_request(_: DecisionRequest) -> DecisionResult:
        return DecisionResult(ok=False, error="确认方案后不允许继续 decision_request")

    attempt_prompt = prompt
    previous_output = ""
    last_failure: PatchFailure | None = None
    max_attempts = max_attempts_for("nlcompile_graph_patch")
    for attempt in range(1, max_attempts + 1):
        chunks: list[str] = []

        async def on_chunk(chunk: AgentChunk) -> None:
            if chunk.type == "text" and chunk.text:
                chunks.append(chunk.text)

        workspace = nlcompile_workspace(user_id)
        with runtime_upload_context(workspace, attachment_refs):
            result = await runtime.execute(
                prompt=rewrite_runtime_upload_paths(attempt_prompt),
                session_id=None,
                model=None,
                reasoning_effort=max_reasoning_effort(),
                cwd=workspace,
                on_chunk=on_chunk,
                cancel_event=session.cancel_event or asyncio.Event(),
                on_decision_request=on_decision_request,
                runtime_policy="execute",
                output_schema=output_schema_for("nlcompile_graph_patch"),
            )
        if result.finished_with != "done":
            logger.warning("nlcompile apply runtime non-done: status=%s error=%s", result.finished_with, result.error)
            raise HTTPException(status_code=502, detail="Agent 编译失败，请检查 Agent 配置或稍后重试")

        previous_output = result.total_text or "".join(chunks)
        completed, failure = await _compile_completed_result(
            previous_output,
            session.current_graph,
            session.instruction,
            runtime=runtime,
            graph_layout_template=graph_layout_template,
            user_id=user_id,
            prompt_assistant_template=prompt_assistant_template,
            cancel_event=session.cancel_event or asyncio.Event(),
            confirmed_plan=session.confirmed_plan,
            qa_history=session.qa_history,
        )
        if completed is not None:
            return completed

        last_failure = failure
        _log_compile_failure(attempt, max_attempts, failure)
        if attempt >= max_attempts:
            break
        attempt_prompt = build_repair_prompt(
            instruction=session.instruction,
            current_graph=session.current_graph,
            confirmed_plan=session.confirmed_plan,
            previous_output=previous_output,
            failure=failure,
        )
        attempt_prompt = _append_initial_attachment_context(attempt_prompt, attachment_refs)

    _log_compile_final_failure(last_failure)
    raise HTTPException(status_code=502, detail=_NL_COMPILE_REPAIR_FAILED_DETAIL)


async def _compile_completed_result(
    text: str,
    current_graph: dict[str, Any],
    instruction: str,
    *,
    runtime: Any,
    graph_layout_template: str,
    user_id: str,
    prompt_assistant_template: str,
    cancel_event: asyncio.Event,
    confirmed_plan: dict[str, Any] | None = None,
    qa_history: list[dict[str, Any]] | None = None,
) -> tuple[dict[str, Any] | None, PatchFailure | None]:
    try:
        patches = extract_patches(text)
    except Exception as exc:  # noqa: BLE001
        return None, PatchFailure(kind="parse", reason=str(exc) or "patch JSON 解析失败")

    new_graph: dict[str, Any] = {
        "nodes": [dict(node) for node in current_graph.get("nodes", [])],
        "execution_edges": [dict(edge) for edge in current_graph.get("execution_edges", [])],
    }
    if "viewport" in current_graph:
        new_graph["viewport"] = current_graph["viewport"]

    applied, failure = _simulate_patches(new_graph, patches)
    if failure is not None:
        return None, failure

    try:
        new_graph = prepare_structural_graph(new_graph)
        validate_prompt_nodes(new_graph)
        topological_order(new_graph)
    except GraphValidationError as exc:
        return None, PatchFailure(kind="graph_validation", reason=str(exc) or "graph 全量校验失败")

    pruned_edge_ids = _prune_nlcompile_redundant_edges(new_graph, applied)
    try:
        new_graph = prepare_structural_graph(new_graph)
        validate_prompt_nodes(new_graph)
        topological_order(new_graph)
    except GraphValidationError as exc:
        return None, PatchFailure(kind="graph_validation", reason=str(exc) or "graph 全量校验失败")

    prompt_failure = await _apply_prompt_assistant_to_patches(
        applied,
        new_graph,
        instruction,
        runtime=runtime,
        user_id=user_id,
        template=prompt_assistant_template,
        cancel_event=cancel_event,
        confirmed_plan=confirmed_plan,
        qa_history=qa_history,
    )
    if prompt_failure is not None:
        return None, prompt_failure

    try:
        validate_prompt_nodes(new_graph)
    except GraphValidationError as exc:
        return None, PatchFailure(kind="graph_validation", reason=str(exc) or "prompt 节点校验失败")

    _ensure_added_node_positions(new_graph, applied)

    warnings: list[str] = []
    if pruned_edge_ids:
        warnings.append(f"已自动清理 {len(pruned_edge_ids)} 条冗余连线")

    try:
        new_graph = await beautify_graph_layout_with_runtime(
            runtime=runtime,
            user_id=user_id,
            graph=new_graph,
            node_sizes={},
            template=graph_layout_template,
            cancel_event=cancel_event,
        )
    except HTTPException as exc:
        detail = str(exc.detail or "Agent 美化布局失败，请稍后重试")
        logger.warning("nlcompile layout beautify skipped: %s", detail)
        warnings.append(f"布局美化失败，已保留原布局：{detail}")

    plan_markdown = render_plan_markdown(applied, current_graph, new_graph, instruction)

    return {
        "status": "completed",
        "new_graph": new_graph,
        "applied_patches": applied,
        "warnings": warnings,
        "plan_markdown": plan_markdown,
    }, None


def _ensure_added_node_positions(graph: dict[str, Any], applied_patches: list[dict[str, Any]]) -> None:
    added_ids: list[str] = []
    for patch in applied_patches:
        node = patch.get("node")
        if patch.get("op") != "add_node" or not isinstance(node, dict):
            continue
        node_id = node.get("id")
        if isinstance(node_id, str):
            added_ids.append(node_id)
    if not added_ids:
        return

    nodes_by_id = {
        node.get("id"): node
        for node in graph.get("nodes", [])
        if isinstance(node, dict) and isinstance(node.get("id"), str)
    }
    missing = [
        nodes_by_id[node_id]
        for node_id in added_ids
        if node_id in nodes_by_id and not _valid_node_position(nodes_by_id[node_id])
    ]
    if not missing:
        return

    known_positions = [
        node["position"]
        for node in nodes_by_id.values()
        if _valid_node_position(node)
    ]
    start_x = max((float(position["x"]) for position in known_positions), default=-_FALLBACK_NODE_X_GAP)
    start_x += _FALLBACK_NODE_X_GAP
    start_y = min((float(position["y"]) for position in known_positions), default=0.0)

    for index, node in enumerate(missing):
        node["position"] = {
            "x": round(start_x + (index % _FALLBACK_NODE_COLUMNS) * _FALLBACK_NODE_X_GAP, 2),
            "y": round(start_y + (index // _FALLBACK_NODE_COLUMNS) * _FALLBACK_NODE_Y_GAP, 2),
        }


def _valid_node_position(node: dict[str, Any]) -> bool:
    position = node.get("position")
    if not isinstance(position, dict):
        return False
    return all(
        isinstance(position.get(axis), int | float)
        and not isinstance(position.get(axis), bool)
        and math.isfinite(float(position[axis]))
        for axis in ("x", "y")
    )


def _simulate_patches(
    graph: dict[str, Any],
    patches: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], PatchFailure | None]:
    applied_attempts: list[PatchAttempt] = []
    pending_retry: list[PatchAttempt] = []
    for index, patch in enumerate(patches, start=1):
        sanitized = _sanitize_prompt_tokens(patch)
        ok, reason = _apply_valid_patch(graph, sanitized)
        attempt = PatchAttempt(index=index, sanitized=sanitized, reason=reason or "")
        if ok:
            applied_attempts.append(attempt)
        elif _can_retry_patch(sanitized, reason):
            pending_retry.append(attempt)
        else:
            return [], PatchFailure(kind="patch_validation", reason=reason or "patch 校验失败", index=index, patch=sanitized)

    while pending_retry:
        remaining: list[PatchAttempt] = []
        made_progress = False
        for attempt in pending_retry:
            ok, reason = _apply_valid_patch(graph, attempt.sanitized)
            if ok:
                applied_attempts.append(attempt)
                made_progress = True
            else:
                attempt.reason = reason or attempt.reason
                remaining.append(attempt)
        if not made_progress:
            failed = remaining[0]
            return [], PatchFailure(
                kind="patch_validation",
                reason=failed.reason or "patch 校验失败",
                index=failed.index,
                patch=failed.sanitized,
            )
        pending_retry = remaining

    applied = [attempt.sanitized for attempt in sorted(applied_attempts, key=lambda item: item.index)]
    return applied, None


_PROMPT_ASSISTANT_MAX_CONCURRENCY = 3


async def _apply_prompt_assistant_to_patches(
    applied: list[dict[str, Any]],
    graph: dict[str, Any],
    instruction: str,
    *,
    runtime: Any,
    user_id: str,
    template: str,
    cancel_event: asyncio.Event,
    confirmed_plan: dict[str, Any] | None = None,
    qa_history: list[dict[str, Any]] | None = None,
) -> PatchFailure | None:
    targets: list[tuple[int, dict[str, Any], dict[str, Any]]] = []
    for index, patch in enumerate(applied, start=1):
        node_id = _prompt_patch_node_id(patch)
        if node_id is None:
            continue
        node = _node_by_id(graph, node_id)
        if not node or node.get("type") not in {"generate", "condition", "output"}:
            continue
        targets.append((index, patch, node))
    if not targets:
        return None

    plan_context = _plan_context_for_prompt_assistant(instruction, confirmed_plan, qa_history or [])
    semaphore = asyncio.Semaphore(_PROMPT_ASSISTANT_MAX_CONCURRENCY)

    async def generate_for(index: int, patch: dict[str, Any], node: dict[str, Any]) -> Any:
        async with semaphore:
            try:
                assistant_prompt = build_nlcompile_prompt_refiner_prompt(
                    graph=graph,
                    node_id=str(node.get("id") or ""),
                    user_request=(
                        "NL 编译正在根据用户指令和已确认方案生成或优化该节点提示词。"
                        "请让当前节点 prompt 落实方案上下文中的目标、数据流和用户已确认的决策，"
                        f"并结合当前画布上下游关系打磨。\n\n用户指令：{instruction}"
                    ),
                    template=template,
                    plan_context=plan_context,
                )
                return await run_prompt_assistant(
                    runtime=runtime,
                    user_id=user_id,
                    prompt=assistant_prompt,
                    model=str(node.get("model") or "").strip() or None,
                    reasoning_effort=max_reasoning_effort(),
                    cancel_event=cancel_event,
                    forbidden_prompt_terms=prompt_internal_terms(graph),
                )
            except HTTPException as exc:
                return PatchFailure(
                    kind="prompt_assistant",
                    reason=f"提示词助手生成失败：{exc.detail}",
                    index=index,
                    patch=patch,
                )
            except Exception as exc:  # noqa: BLE001
                return PatchFailure(
                    kind="prompt_assistant",
                    reason=f"提示词助手生成失败：{exc}",
                    index=index,
                    patch=patch,
                )

    results = await asyncio.gather(*(generate_for(index, patch, node) for index, patch, node in targets))
    failures = [item for item in results if isinstance(item, PatchFailure)]
    if failures:
        return min(failures, key=lambda item: item.index or 0)
    for (_index, patch, node), generated in zip(targets, results):
        node["prompt"] = generated.prompt
        output_contract = generated.output_contract if node.get("type") == "generate" else None
        if output_contract is not None:
            node["output_contract"] = output_contract
        _apply_generated_prompt_to_patch(patch, generated.prompt, output_contract=output_contract)
    return None


def _qa_history_entry(request: DecisionRequest, result: DecisionResult) -> dict[str, Any]:
    return {
        "kind": "decision_request",
        "request": request.model_dump(exclude_none=True),
        "answers": [answer.model_dump() for answer in result.answers],
        "text": result.text,
        "attachment_names": [item.name for item in result.attachments],
    }


def _plan_context_for_prompt_assistant(
    instruction: str,
    confirmed_plan: dict[str, Any] | None,
    qa_history: list[dict[str, Any]],
) -> str:
    sections: list[str] = [f"原始用户指令：{instruction}"]
    plan_lines: list[str] = []
    plan = confirmed_plan if isinstance(confirmed_plan, dict) else {}
    for key, label in (
        ("goal_summary", "目标"),
        ("data_flow", "数据流"),
        ("implementation_steps", "实施步骤"),
        ("acceptance_criteria", "验收标准"),
    ):
        value = plan.get(key)
        if isinstance(value, str) and value.strip():
            plan_lines.append(f"- {label}：{value.strip()}")
        elif isinstance(value, list):
            joined = "；".join(str(item).strip() for item in value if str(item).strip())
            if joined:
                plan_lines.append(f"- {label}：{joined}")
    if plan_lines:
        sections.append("已确认方案摘要：\n" + "\n".join(plan_lines))
    qa_lines = _qa_history_lines(qa_history)
    if qa_lines:
        sections.append("用户问答摘要（必须遵守）：\n" + "\n".join(qa_lines))
    return "\n\n".join(sections)


def _qa_history_lines(qa_history: list[dict[str, Any]]) -> list[str]:
    lines: list[str] = []
    for entry in qa_history:
        if not isinstance(entry, dict):
            continue
        request = entry.get("request") if isinstance(entry.get("request"), dict) else {}
        groups = request.get("groups") if isinstance(request.get("groups"), list) else []
        answers = entry.get("answers") if isinstance(entry.get("answers"), list) else []
        selected_by_group: dict[str, list[str]] = {}
        for answer in answers:
            if isinstance(answer, dict):
                selected = answer.get("selected")
                selected_by_group[str(answer.get("group_id") or "")] = [
                    str(item) for item in (selected if isinstance(selected, list) else [])
                ]
        for group in groups:
            if not isinstance(group, dict):
                continue
            label = str(group.get("label") or group.get("id") or "").strip()
            selected = selected_by_group.get(str(group.get("id") or ""), [])
            if label and selected:
                lines.append(f"- {label} → {'、'.join(selected)}")
        text = str(entry.get("text") or "").strip()
        if text:
            lines.append(f"- 用户补充：{text}")
        raw_names = entry.get("attachment_names")
        names = [str(name) for name in (raw_names if isinstance(raw_names, list) else []) if str(name).strip()]
        if names:
            lines.append(f"- 用户附件：{'、'.join(names)}")
    return lines


def _prompt_patch_node_id(patch: dict[str, Any]) -> str | None:
    if patch.get("op") == "add_node" and isinstance(patch.get("node"), dict):
        node = patch["node"]
        if node.get("type") in {"generate", "condition", "output"} and isinstance(node.get("prompt"), str):
            node_id = node.get("id")
            return node_id if isinstance(node_id, str) else None
    if patch.get("op") == "update_node" and isinstance(patch.get("patch"), dict):
        if isinstance(patch["patch"].get("prompt"), str):
            node_id = patch.get("id")
            return node_id if isinstance(node_id, str) else None
    return None


def _apply_generated_prompt_to_patch(
    patch: dict[str, Any],
    generated: str,
    *,
    output_contract: dict[str, Any] | None = None,
) -> None:
    if patch.get("op") == "add_node" and isinstance(patch.get("node"), dict):
        patch["node"]["prompt"] = generated
        if output_contract is not None:
            patch["node"]["output_contract"] = output_contract
        return
    if patch.get("op") == "update_node" and isinstance(patch.get("patch"), dict):
        patch["patch"]["prompt"] = generated
        if output_contract is not None:
            patch["patch"]["output_contract"] = output_contract


async def _await_compile_response(session: NlCompileSession) -> dict[str, Any]:
    session.waiter_count += 1
    try:
        payload = await session.response_future
    except HTTPException:
        _compile_sessions.pop(session.id, None)
        raise
    finally:
        session.waiter_count = max(0, session.waiter_count - 1)
    if payload.get("status") == "waiting_for_user":
        session.response_future = asyncio.get_running_loop().create_future()
        return payload
    if payload.get("status") == "planned":
        return payload
    _compile_sessions.pop(session.id, None)
    return payload


def _publish_compile_response(session: NlCompileSession, payload: dict[str, Any]) -> None:
    if not session.response_future.done():
        session.response_future.set_result(payload)


def _publish_compile_exception(session: NlCompileSession, exc: HTTPException) -> None:
    if not session.response_future.done():
        session.response_future.set_exception(exc)


def _cancel_compile_session(session: NlCompileSession, *, notify_waiter: bool = False) -> None:
    if session.cancel_event is not None:
        session.cancel_event.set()
    if notify_waiter and session.waiter_count > 0 and not session.response_future.done():
        session.response_future.set_exception(HTTPException(status_code=409, detail=_CANCELLED_DETAIL))
    if session.task is not None and not session.task.done():
        session.task.cancel()
    _compile_sessions.pop(session.id, None)


def _purge_cancel_tombstones() -> None:
    now = time.monotonic()
    expired = [key for key, created in _cancel_tombstones.items() if now - created > _CANCEL_TOMBSTONE_TTL_SEC]
    for key in expired:
        _cancel_tombstones.pop(key, None)


def _consume_cancel_tombstone(user_id: str, compile_id: str) -> bool:
    return _cancel_tombstones.pop((user_id, compile_id), None) is not None


# --- prompt 渲染 ----------------------------------------------------------

_ASSET_CONTENT_PROMPT_LIMIT = 2000
_GRAPH_LAYOUT_FIELDS = {"position", "width", "height", "measured", "selected", "dragging"}


def _slim_graph_for_prompt(graph: dict[str, Any]) -> dict[str, Any]:
    """只影响发给 LLM 的 graph 文本：剔除布局字段、截断素材长文；真实 graph 不变。"""
    slim = {key: value for key, value in graph.items() if key not in {"nodes", "execution_edges", "viewport"}}
    nodes: list[dict[str, Any]] = []
    for node in graph.get("nodes", []):
        if not isinstance(node, dict):
            continue
        slim_node = {key: value for key, value in node.items() if key not in _GRAPH_LAYOUT_FIELDS}
        content = slim_node.get("content")
        if isinstance(content, str) and len(content) > _ASSET_CONTENT_PROMPT_LIMIT:
            slim_node["content"] = content[:_ASSET_CONTENT_PROMPT_LIMIT] + "…（素材内容已截断）"
        nodes.append(slim_node)
    slim["nodes"] = nodes
    slim["execution_edges"] = list(graph.get("execution_edges", []))
    return slim


def _history_context_for_plan(history: list[dict[str, Any]]) -> str:
    if not history:
        return ""
    sections: list[str] = []

    qa_lines = _qa_history_lines(history)
    if qa_lines:
        sections.append("### 用户问答历史\n" + "\n".join(qa_lines))

    plan_entries = [entry.get("plan") for entry in history if entry.get("kind") == "plan" and isinstance(entry.get("plan"), dict)]
    if plan_entries:
        previous = plan_entries[:-1]
        if previous:
            lines: list[str] = []
            for index, plan in enumerate(previous, start=1):
                goal = _plan_text(plan.get("goal_summary")) or "未提供目标"
                changes = _plan_list(plan.get("graph_changes"))
                suffix = f"；变更：{'；'.join(changes[:5])}" if changes else ""
                lines.append(f"- 第 {index} 轮方案摘要：{goal}{suffix}")
            sections.append("### 更早方案摘要\n" + "\n".join(lines))
        sections.append(
            "### 最近一轮方案 JSON\n"
            + json.dumps(plan_entries[-1], ensure_ascii=False, indent=2)
        )

    refine_lines = [
        f"- 第 {index} 轮反馈：{str(entry.get('feedback') or '').strip()}"
        for index, entry in enumerate((item for item in history if item.get("kind") == "refine"), start=1)
        if str(entry.get("feedback") or "").strip()
    ]
    if refine_lines:
        sections.append("### 用户补充修改说明\n" + "\n".join(refine_lines))

    return "\n\n".join(sections)


def build_plan_prompt(
    instruction: str,
    graph: dict[str, Any],
    template: str,
    history: list[dict[str, Any]] | None = None,
) -> str:
    base = render_prompt(
        template,
        {
            "graph_json": json.dumps(_slim_graph_for_prompt(graph), ensure_ascii=False),
            "instruction": instruction,
        },
    )
    history_context = _history_context_for_plan(history or [])
    if history_context:
        base = "\n\n".join([base.strip(), "## 会话历史（必须继承）\n" + history_context])
    return base.strip()


def build_patch_prompt(
    instruction: str,
    graph: dict[str, Any],
    confirmed_plan: dict[str, Any],
    template: str,
    history: list[dict[str, Any]] | None = None,
) -> str:
    prompt = render_prompt(
        template,
        {
            "graph_json": json.dumps(_slim_graph_for_prompt(graph), ensure_ascii=False),
            "instruction": instruction,
            "confirmed_plan": json.dumps(confirmed_plan, ensure_ascii=False, indent=2),
        },
    )
    history_context = _history_context_for_plan(history or [])
    if history_context:
        prompt = "\n\n".join([prompt.strip(), "## 会话历史（必须继承）\n" + history_context])
    return append_patch_protocol(prompt)


def build_repair_prompt(
    *,
    instruction: str,
    current_graph: dict[str, Any],
    confirmed_plan: dict[str, Any] | None,
    previous_output: str,
    failure: PatchFailure | None,
) -> str:
    failure_info = _failure_payload(failure)
    prompt = f"""
你刚才为 Mira 自然语言编辑生成的 graph patch 未通过后端校验。请根据失败原因重新生成完整 patch 列表。

要求：
- 只输出一个 JSON 对象，形状必须是 {{"patches":[...]}}。
- 不要输出 markdown、解释、注释或代码块。
- 不要只输出失败 patch；必须重新输出本次编辑所需的完整 patches。
- patches 必须能一次性应用到当前 graph，并通过完整 graph 结构、prompt 节点和拓扑校验。

原始用户指令：
{instruction}

已确认方案 JSON：
{json.dumps(confirmed_plan or {}, ensure_ascii=False, indent=2)}

当前 graph JSON：
{json.dumps(_slim_graph_for_prompt(current_graph), ensure_ascii=False)}

上一轮 Agent 输出：
{previous_output}

失败信息 JSON：
{json.dumps(failure_info, ensure_ascii=False)}
""".strip()
    return append_patch_protocol(prompt)


def _failure_payload(failure: PatchFailure | None) -> dict[str, Any]:
    if failure is None:
        return {"type": "unknown", "reason": "未知错误"}
    payload: dict[str, Any] = {"type": failure.kind, "reason": failure.reason}
    if failure.index is not None:
        payload["patch_index"] = failure.index
    if failure.patch is not None:
        payload["patch_op"] = failure.patch.get("op")
        payload["patch_target"] = _patch_target(failure.patch)
        payload["patch"] = failure.patch
    return payload


def _log_compile_failure(attempt: int, max_attempts: int, failure: PatchFailure | None) -> None:
    patch = failure.patch if failure else None
    logger.warning(
        "nlcompile patch generation failed: attempt=%s/%s type=%s index=%s op=%s target=%s reason=%s",
        attempt,
        max_attempts,
        failure.kind if failure else "unknown",
        failure.index if failure else None,
        patch.get("op") if patch else None,
        _patch_target(patch) if patch else "",
        failure.reason if failure else "未知错误",
    )


def _log_compile_final_failure(failure: PatchFailure | None) -> None:
    patch = failure.patch if failure else None
    logger.warning(
        "nlcompile final failure: type=%s index=%s op=%s target=%s reason=%s",
        failure.kind if failure else "unknown",
        failure.index if failure else None,
        patch.get("op") if patch else None,
        _patch_target(patch) if patch else "",
        failure.reason if failure else "未知错误",
    )


# --- LLM 输出解析 ---------------------------------------------------------


def extract_patches(text: str) -> list[dict[str, Any]]:
    """解析 structured output 返回的 {"patches":[...]}。"""

    data = parse_structured_json_object(text, label="patch 输出")
    if not isinstance(data, dict) or "patches" not in data:
        raise ValueError("输出缺少 patches 数组")
    raw_patches = data.get("patches")
    if not isinstance(raw_patches, list):
        raise ValueError("patches 必须是数组")
    if not raw_patches:
        raise ValueError("patches 不能为空，必须至少包含 1 个 patch")
    if not all(isinstance(patch, dict) for patch in raw_patches):
        raise ValueError("patches 数组项必须是对象")
    return [_normalize_structured_patch(patch) for patch in raw_patches]


def _normalize_structured_patch(patch: dict[str, Any]) -> dict[str, Any]:
    op = patch.get("op")
    if op == "add_node":
        if isinstance(patch.get("node"), dict):
            return patch
        return {"op": "add_node", "node": _parse_json_object_field(patch.get("node_json"), "node_json")}
    if op == "remove_node":
        return {"op": "remove_node", "id": patch.get("id")}
    if op == "update_node":
        if isinstance(patch.get("patch"), dict):
            return patch
        return {
            "op": "update_node",
            "id": patch.get("id"),
            "patch": _parse_json_object_field(patch.get("patch_json"), "patch_json"),
        }
    if op == "add_edge":
        if isinstance(patch.get("edge"), dict):
            return patch
        edge = {
            "id": patch.get("edge_id"),
            "source": patch.get("edge_source"),
            "target": patch.get("edge_target"),
        }
        branch_key = patch.get("edge_branch_key")
        if isinstance(branch_key, str) and branch_key:
            edge["branch_key"] = branch_key
        return {"op": "add_edge", "edge": edge}
    if op == "remove_edge":
        return {"op": "remove_edge", "id": patch.get("id")}
    return patch


def _parse_json_object_field(value: Any, field_name: str) -> dict[str, Any]:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} 必须是 JSON 对象字符串")
    parsed = json.loads(value)
    if not isinstance(parsed, dict):
        raise ValueError(f"{field_name} 必须解析为 JSON 对象")
    return parsed


def extract_plan(text: str) -> dict[str, Any]:
    """解析 structured output 返回的 {"plan": {...}}。"""

    data = parse_structured_json_object(text, label="plan 输出")
    if not isinstance(data, dict) or not isinstance(data.get("plan"), dict):
        raise ValueError("输出缺少 plan 对象")
    plan = data["plan"]
    normalized = {
        "goal_summary": _plan_text(plan.get("goal_summary")),
        "assumptions": _plan_list(plan.get("assumptions")),
        "data_flow": _plan_list(plan.get("data_flow")),
        "implementation_steps": _plan_list(plan.get("implementation_steps")),
        "graph_changes": _plan_list(plan.get("graph_changes")),
        "expected_inputs": _plan_list(plan.get("expected_inputs")),
        "expected_outputs": _plan_list(plan.get("expected_outputs")),
        "acceptance_criteria": _plan_list(plan.get("acceptance_criteria")),
    }
    if not normalized["goal_summary"]:
        raise ValueError("plan.goal_summary 不能为空")
    for field_name in (
        "implementation_steps",
        "graph_changes",
        "expected_inputs",
        "expected_outputs",
        "acceptance_criteria",
    ):
        if not normalized[field_name]:
            raise ValueError(f"plan.{field_name} 至少需要 1 项")
    return normalized


def _plan_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    return str(value).strip()


def _plan_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [text for item in value if (text := _plan_text(item))]
    text = _plan_text(value)
    return [text] if text else []


# --- patch 应用 -----------------------------------------------------------


def _apply_patch(graph: dict[str, Any], patch: dict[str, Any]) -> bool:
    nodes = graph.setdefault("nodes", [])
    edges = graph.setdefault("execution_edges", [])
    op = patch.get("op")
    if op == "add_node" and patch.get("node"):
        if patch["node"].get("id") in _node_ids(graph):
            return False
        nodes.append(patch["node"])
        return True
    if op == "add_edge" and patch.get("edge"):
        edges.append(patch["edge"])
        return True
    if op == "update_node" and patch.get("id") and patch.get("patch"):
        for node in nodes:
            if node.get("id") == patch["id"]:
                node.update({k: v for k, v in patch["patch"].items() if k not in {"id", "type"}})
                return True
        return False
    if op == "remove_edge" and patch.get("id"):
        before = len(edges)
        graph["execution_edges"] = [edge for edge in edges if edge.get("id") != patch["id"]]
        return len(graph["execution_edges"]) != before
    if op == "remove_node" and patch.get("id"):
        before = len(nodes)
        graph["nodes"] = [node for node in nodes if node.get("id") != patch["id"]]
        graph["execution_edges"] = [
            edge
            for edge in edges
            if edge.get("source") != patch["id"] and edge.get("target") != patch["id"]
        ]
        return len(graph["nodes"]) != before
    return False


# --- patch 校验 -----------------------------------------------------------


def _apply_valid_patch(graph: dict[str, Any], patch: dict[str, Any]) -> tuple[bool, str | None]:
    reason = _patch_validation_error(graph, patch)
    if reason is not None:
        return False, reason
    if not _apply_patch(graph, patch):
        return False, "patch 应用失败"
    return True, None


def _patch_validation_error(graph: dict[str, Any], patch: dict[str, Any]) -> str | None:
    op = patch.get("op")
    ids = _node_ids(graph)
    if op == "add_node":
        return _new_node_validation_error(patch.get("node"), ids)
    if op == "remove_node":
        return None if patch.get("id") in ids else "remove_node.id 不存在"
    if op == "update_node":
        node_patch = patch.get("patch")
        node = _node_by_id(graph, patch.get("id"))
        if not node:
            return "update_node.id 不存在"
        if not isinstance(node_patch, dict):
            return "update_node.patch 缺失或格式非法"
        if {"id", "type"} & set(node_patch):
            return "update_node.patch 不能包含 id 或 type"
        if not _valid_existing_node({**node, **node_patch}):
            return "update_node.patch 不符合节点类型约束"
        return _candidate_graph_error(graph, patch)
    if op == "add_edge":
        return _new_edge_validation_error(graph, patch.get("edge"))
    if op == "remove_edge":
        return None if any(edge.get("id") == patch.get("id") for edge in graph.get("execution_edges", [])) else "remove_edge.id 不存在"
    if op == "delete_edge":
        return "patch.op 不支持；删除连线只能使用 remove_edge"
    return "patch.op 不支持"


def _can_retry_patch(patch: dict[str, Any], reason: str | None) -> bool:
    if patch.get("op") == "update_node":
        return reason == "update_node.id 不存在"
    if patch.get("op") == "add_edge":
        return reason in {"add_edge.edge.source 不存在", "add_edge.edge.target 不存在"}
    return False


def _patch_target(patch: dict[str, Any]) -> str:
    op = patch.get("op")
    if op in {"update_node", "remove_node", "remove_edge"}:
        value = patch.get("id")
        return value if isinstance(value, str) else ""
    if op == "add_node" and isinstance(patch.get("node"), dict):
        value = patch["node"].get("id")
        return value if isinstance(value, str) else ""
    if op == "add_edge" and isinstance(patch.get("edge"), dict):
        value = patch["edge"].get("id")
        return value if isinstance(value, str) else ""
    return ""


def _sanitize_prompt_tokens(patch: dict[str, Any]) -> dict[str, Any]:
    op = patch.get("op")
    if op == "add_node" and isinstance(patch.get("node"), dict):
        node = dict(patch["node"])
        if isinstance(node.get("prompt"), str):
            node["prompt"] = strip_template_tokens(node["prompt"])
        return {**patch, "node": node}
    if op == "update_node" and isinstance(patch.get("patch"), dict):
        node_patch = dict(patch["patch"])
        if isinstance(node_patch.get("prompt"), str):
            node_patch["prompt"] = strip_template_tokens(node_patch["prompt"])
        return {**patch, "patch": node_patch}
    return patch


def _new_node_validation_error(node: Any, existing_ids: set[str]) -> str | None:
    if not isinstance(node, dict):
        return "add_node.node 缺失或格式非法"
    node_id = node.get("id")
    if not isinstance(node_id, str) or not node_id:
        return "add_node.node.id 缺失或格式非法"
    if node_id in existing_ids:
        return "add_node.node.id 已存在"
    if not _valid_existing_node(node):
        return "add_node.node 不符合节点类型约束"
    return None


def _valid_existing_node(node: dict[str, Any]) -> bool:
    node_type = node.get("type")
    if node_type == "generate":
        return _valid_prompt_node(node)
    if node_type == "user_input":
        return isinstance(node.get("input_schema"), dict)
    if node_type == "output":
        # 与前端 OutputNode 契约对齐：不强制 render；prompt 必填。
        return _valid_prompt_node(node)
    if node_type == "asset":
        asset_kind = node.get("asset_kind")
        if asset_kind == "text":
            return isinstance(node.get("content"), str)
        if asset_kind == "url":
            return isinstance(node.get("urls"), list) and all(isinstance(url, str) for url in node.get("urls", []))
        if asset_kind == "file":
            uploads = node.get("uploads")
            return isinstance(uploads, list) and all(isinstance(upload, dict) for upload in uploads)
        if asset_kind == "drawing":
            upload = node.get("upload")
            return upload is None or isinstance(upload, dict)
        return False
    if node_type == "condition":
        if node.get("mode") not in {"binary", "cases"}:
            return False
        if not _valid_prompt_node(node):
            return False
        branches = node.get("branches")
        if not isinstance(branches, list) or len(branches) < 2:
            return False
        keys: list[str] = []
        for branch in branches:
            if not isinstance(branch, dict):
                return False
            key = branch.get("key")
            if not isinstance(key, str) or not key:
                return False
            if key == DEFAULT_BRANCH_KEY:
                return False
            if re.match(r"^[a-zA-Z0-9_]+$", key) is None:
                return False
            keys.append(key)
        if len(set(keys)) != len(keys):
            return False
        if node.get("mode") == "binary":
            return len(keys) == 2 and set(keys) == {"true", "false"}
        return True
    return False


def _valid_prompt_node(node: dict[str, Any]) -> bool:
    prompt = node.get("prompt")
    return (
        isinstance(prompt, str)
        and bool(prompt.strip())
        and not contains_template_token(prompt)
    )


def _new_edge_validation_error(graph: dict[str, Any], edge: Any) -> str | None:
    if not isinstance(edge, dict):
        return "add_edge.edge 缺失或格式非法"
    edge_id = edge.get("id")
    if not isinstance(edge_id, str) or not edge_id:
        return "add_edge.edge.id 缺失或格式非法"
    if any(existing.get("id") == edge_id for existing in graph.get("execution_edges", [])):
        return "add_edge.edge.id 已存在"
    source = _node_by_id(graph, edge.get("source"))
    target = _node_by_id(graph, edge.get("target"))
    if not source:
        return "add_edge.edge.source 不存在"
    if not target:
        return "add_edge.edge.target 不存在"
    if edge.get("source") == edge.get("target"):
        return "节点不能连接到自身"
    if source.get("type") == "output":
        return f"{source.get('type')} 节点不能作为连线起点"
    if target.get("type") in {"user_input", "asset"}:
        return "user_input 和 asset 节点不能作为连线终点"
    if source.get("type") == "condition":
        handle = edge.get("branch_key")
        if not isinstance(handle, str) or handle not in _condition_handles(source):
            return "condition 出边 branch_key 无效"
        for existing in graph.get("execution_edges", []):
            if (
                existing.get("source") == edge.get("source")
                and existing.get("branch_key") == handle
            ):
                return "同一个 condition 分支最多只能连接一条出边"
    else:
        if "branch_key" in edge:
            return "非 condition 出边不能包含 branch_key"
        for existing in graph.get("execution_edges", []):
            if existing.get("source") == edge.get("source") and existing.get("target") == edge.get("target"):
                return "连线重复"
    return _candidate_topology_error(graph, {"op": "add_edge", "edge": edge})


def _candidate_graph_error(graph: dict[str, Any], patch: dict[str, Any]) -> str | None:
    candidate = deepcopy(graph)
    if not _apply_patch(candidate, patch):
        return "patch 应用失败"
    try:
        prepare_structural_graph(candidate)
    except GraphValidationError as exc:
        return str(exc) or "graph 结构校验失败"
    return None


def _candidate_topology_error(graph: dict[str, Any], patch: dict[str, Any]) -> str | None:
    candidate = deepcopy(graph)
    if not _apply_patch(candidate, patch):
        return "patch 应用失败"
    try:
        topological_order(candidate)
    except GraphValidationError as exc:
        return str(exc) or "graph 拓扑校验失败"
    return None


def _prune_nlcompile_redundant_edges(
    graph: dict[str, Any],
    applied_patches: list[dict[str, Any]],
) -> list[str]:
    redundant_edge_ids = _nlcompile_redundant_edge_ids(graph)
    if not redundant_edge_ids:
        return []

    redundant_edge_id_set = set(redundant_edge_ids)
    graph["execution_edges"] = [
        edge
        for edge in graph.get("execution_edges", [])
        if not (isinstance(edge, dict) and edge.get("id") in redundant_edge_id_set)
    ]
    _sync_applied_patches_for_pruned_edges(applied_patches, redundant_edge_ids)
    return redundant_edge_ids


def _sync_applied_patches_for_pruned_edges(
    applied_patches: list[dict[str, Any]],
    pruned_edge_ids: list[str],
) -> None:
    pruned_edge_id_set = set(pruned_edge_ids)
    pruned_add_edge_ids: set[str] = set()
    retained: list[dict[str, Any]] = []
    for patch in applied_patches:
        edge_id = None
        if patch.get("op") == "add_edge" and isinstance(patch.get("edge"), dict):
            edge_id = patch["edge"].get("id")
        if isinstance(edge_id, str) and edge_id in pruned_edge_id_set:
            pruned_add_edge_ids.add(edge_id)
            continue
        retained.append(patch)

    applied_patches[:] = retained
    for edge_id in pruned_edge_ids:
        if edge_id not in pruned_add_edge_ids:
            applied_patches.append({"op": "remove_edge", "id": edge_id})


def _nlcompile_redundant_edge_ids(graph: dict[str, Any]) -> list[str]:
    nodes = {
        node.get("id"): node
        for node in graph.get("nodes", [])
        if isinstance(node, dict) and isinstance(node.get("id"), str)
    }
    redundant_edge_ids: list[str] = []
    for edge in graph.get("execution_edges", []):
        if not isinstance(edge, dict):
            continue
        source_id = edge.get("source")
        target_id = edge.get("target")
        edge_id = edge.get("id")
        if not isinstance(source_id, str) or not isinstance(target_id, str) or not isinstance(edge_id, str):
            continue
        source_node = nodes.get(source_id)
        if not source_node or source_node.get("type") == "condition":
            continue
        candidate = {
            **graph,
            "execution_edges": [
                current
                for current in graph.get("execution_edges", [])
                if not (isinstance(current, dict) and current.get("id") == edge_id)
            ],
        }
        try:
            plan = compile_execution_plan(candidate)
        except ExecutionPlanError:
            continue
        if target_id in plan.descendant_ids({source_id}):
            redundant_edge_ids.append(edge_id)
    return redundant_edge_ids


def _condition_handles(node: dict[str, Any]) -> set[str]:
    mode = node.get("mode")
    branches = node.get("branches")
    if mode == "binary":
        return {"true", "false"}
    if mode != "cases" or not isinstance(branches, list):
        return set()
    keys = {
        branch.get("key")
        for branch in branches
        if isinstance(branch, dict) and isinstance(branch.get("key"), str) and branch.get("key")
    }
    keys.add(DEFAULT_BRANCH_KEY)
    return keys


# --- plan_markdown --------------------------------------------------------


def render_nlcompile_plan_markdown(plan: dict[str, Any], instruction: str) -> str:
    instruction_short = instruction.strip().splitlines()[0] if instruction.strip() else ""
    parts: list[str] = ["## 方案确认"]
    if instruction_short:
        parts.append(f"> 指令：{instruction_short}")
    parts.append("")
    parts.append("### 目标摘要")
    parts.append(_plan_text(plan.get("goal_summary")) or "未提供")
    _append_plan_list(parts, "关键假设", plan.get("assumptions"))
    _append_plan_list(parts, "数据流说明", plan.get("data_flow"))
    _append_plan_list(parts, "实施步骤", plan.get("implementation_steps"))
    _append_plan_list(parts, "画布变更", plan.get("graph_changes"))
    _append_plan_list(parts, "预计输入", plan.get("expected_inputs"))
    _append_plan_list(parts, "预计输出", plan.get("expected_outputs"))
    _append_plan_list(parts, "验收标准", plan.get("acceptance_criteria"))
    return "\n".join(parts).strip() + "\n"


def _append_plan_list(parts: list[str], title: str, items: Any) -> None:
    values = _plan_list(items)
    parts.append("")
    parts.append(f"### {title}")
    if not values:
        parts.append("- 无")
        return
    for item in values:
        parts.append(f"- {item}")


def render_plan_markdown(
    applied_patches: list[dict[str, Any]],
    original_graph: dict[str, Any],
    new_graph: dict[str, Any],
    instruction: str,
) -> str:
    """将 patches 渲染成可读的中文 markdown 计划，供前端弹窗确认。"""

    instruction_short = instruction.strip().splitlines()[0] if instruction.strip() else ""
    parts: list[str] = []
    parts.append("## 计划摘要")
    if instruction_short:
        parts.append(f"> 指令：{instruction_short}")
    parts.append("")

    if not applied_patches:
        parts.append("没有要应用的更改。")
    else:
        parts.append(f"将对画布执行以下 {len(applied_patches)} 项更改：")
        parts.append("")
        original_nodes_by_id = {
            node.get("id"): node for node in original_graph.get("nodes", []) if node.get("id")
        }
        new_nodes_by_id = {
            node.get("id"): node for node in new_graph.get("nodes", []) if node.get("id")
        }
        original_edges_by_id = {
            edge.get("id"): edge for edge in original_graph.get("execution_edges", []) if edge.get("id")
        }
        for index, patch in enumerate(applied_patches, start=1):
            _append_patch_details(
                parts,
                index,
                patch,
                original_graph,
                original_nodes_by_id,
                new_nodes_by_id,
                original_edges_by_id,
            )

    new_node_count = len(new_graph.get("nodes", []))
    new_edge_count = len(new_graph.get("execution_edges", []))
    parts.append("")
    parts.append(f"应用后画布包含 {new_node_count} 个节点 / {new_edge_count} 条连线。")
    return "\n".join(parts).strip() + "\n"


def _append_patch_details(
    parts: list[str],
    index: int,
    patch: dict[str, Any],
    original_graph: dict[str, Any],
    original_nodes: dict[str, Any],
    new_nodes: dict[str, Any],
    original_edges: dict[str, Any],
) -> None:
    op = patch.get("op")
    if op == "add_node":
        node = patch.get("node") or {}
        parts.append(f"### {index}. 新增{_node_label(node)}")
        _append_node_basics(parts, node)
        _append_node_fields(parts, node)
        parts.append("")
        return
    if op == "remove_node":
        node_id = patch.get("id") or ""
        original = original_nodes.get(node_id)
        parts.append(f"### {index}. 删除{_node_label(original, fallback_id=node_id)}")
        if original:
            _append_node_basics(parts, original)
        affected_edges = [
            edge
            for edge in original_graph.get("execution_edges", [])
            if edge.get("source") == node_id or edge.get("target") == node_id
        ]
        parts.append(f"- 同时移除关联连线：{len(affected_edges)} 条")
        for edge in affected_edges:
            parts.append(f"  - {_edge_label(edge, original_nodes)}")
        parts.append("")
        return
    if op == "update_node":
        node_id = patch.get("id") or ""
        original = original_nodes.get(node_id)
        node_patch = patch.get("patch") or {}
        parts.append(f"### {index}. 更新{_node_label(original, fallback_id=node_id)}")
        for field in sorted(node_patch.keys()):
            parts.append(f"#### 字段 `{field}`")
            parts.append("修改前：")
            parts.append(_value_block(original.get(field) if original else None, field))
            parts.append("修改后：")
            parts.append(_value_block(node_patch.get(field), field))
        parts.append("")
        return
    if op == "add_edge":
        edge = patch.get("edge") or {}
        parts.append(f"### {index}. 新增连线")
        parts.append(f"- {_edge_label(edge, new_nodes)}")
        if edge.get("branch_key"):
            parts.append(f"- 分支 handle：`{edge.get('branch_key')}`")
        parts.append("")
        return
    if op == "remove_edge":
        edge_id = patch.get("id")
        edge = original_edges.get(edge_id)
        parts.append(f"### {index}. 删除连线")
        if edge:
            parts.append(f"- {_edge_label(edge, original_nodes)}")
        else:
            parts.append(f"- 连线 ID：`{edge_id}`")
        parts.append("")
        return
    parts.append(f"### {index}. 未知更改")
    parts.append(_value_block(patch))
    parts.append("")


def _node_label(node: dict[str, Any] | None, fallback_id: str | None = None) -> str:
    if not node:
        return f"节点「{fallback_id or '未知'}」"
    type_label = NODE_TYPE_LABELS.get(node.get("type", ""), node.get("type") or "节点")
    title = node.get("title") or node.get("id") or fallback_id or "未命名"
    node_id = node.get("id") or fallback_id or "未知"
    return f"{type_label}「{title}」(`{node_id}`)"


def _append_node_basics(parts: list[str], node: dict[str, Any]) -> None:
    parts.append(f"- 类型：`{node.get('type') or 'unknown'}`")
    parts.append(f"- 标题：{node.get('title') or '未命名'}")
    parts.append(f"- ID：`{node.get('id') or '未知'}`")
    position = node.get("position")
    if isinstance(position, dict):
        parts.append(f"- 位置：x={position.get('x', 0)}, y={position.get('y', 0)}")


def _append_node_fields(parts: list[str], node: dict[str, Any]) -> None:
    keys = [
        key
        for key in node.keys()
        if key not in {"id", "type", "position", "title"}
    ]
    for key in sorted(keys):
        value = node.get(key)
        if value is None or value == "":
            continue
        parts.append(f"#### 字段 `{key}`")
        parts.append(_value_block(value, key))


def _edge_label(edge: dict[str, Any], nodes: dict[str, Any]) -> str:
    source_id = edge.get("source")
    target_id = edge.get("target")
    source = nodes.get(source_id)
    target = nodes.get(target_id)
    source_title = (source or {}).get("title") or source_id or "未知"
    target_title = (target or {}).get("title") or target_id or "未知"
    label = f"{source_title} (`{source_id}`) → {target_title} (`{target_id}`)"
    if edge.get("id"):
        label += f"，连线 ID：`{edge.get('id')}`"
    return label


def _value_block(value: Any, field: str | None = None) -> str:
    if isinstance(value, str):
        return _fenced_block(value or "（空）", "text")
    if value is None:
        return "无"
    if isinstance(value, bool):
        return "是" if value else "否"
    if isinstance(value, int | float):
        return str(value)
    if field == "input_schema" and isinstance(value, dict):
        return _input_schema_block(value)
    if field == "output_contract" and isinstance(value, dict):
        return _output_contract_block(value)
    if field == "branches" and isinstance(value, list):
        return _branches_block(value)
    if isinstance(value, list):
        return _list_block(value)
    if isinstance(value, dict):
        return _dict_block(value)
    return str(value)


def _input_schema_block(schema: dict[str, Any]) -> str:
    lines: list[str] = []
    label = schema.get("label")
    if label:
        lines.append(f"- 显示文案：{label}")
    placeholder = schema.get("placeholder")
    if placeholder:
        lines.append(f"- 输入提示：{placeholder}")
    kind = schema.get("kind")
    if kind:
        lines.append(f"- 输入类型：{_input_kind_label(kind)}")
    if "required" in schema:
        lines.append(f"- 是否必填：{_plain_value(schema.get('required'))}")
    return "\n".join(lines) if lines else "无表单配置"


def _output_contract_block(contract: dict[str, Any]) -> str:
    lines: list[str] = []
    output_type = contract.get("type")
    if output_type:
        lines.append(f"- 输出类型：{_output_contract_type_label(output_type)}")
    artifact_kind = contract.get("artifact_kind")
    if isinstance(artifact_kind, str) and artifact_kind:
        lines.append(f"- 文件类型：{_artifact_kind_label(artifact_kind)}")
    return "\n".join(lines) if lines else "无输出契约"


def _output_contract_type_label(value: Any) -> str:
    labels = {
        "json": "结构化数据（由 AI 自动维护）",
        "html": "HTML",
        "artifact": "文件产物引用",
    }
    return labels.get(str(value), str(value))


def _artifact_kind_label(value: Any) -> str:
    labels = {
        "image": "图片",
        "code": "代码包",
        "html": "HTML",
        "markdown": "Markdown 文件",
        "csv": "CSV",
        "excel": "Excel",
        "docx": "DOCX",
        "ppt": "PPT",
        "pdf": "PDF",
        "archive": "压缩包",
        "file": "其他文件",
    }
    return labels.get(str(value), str(value))


def _branches_block(branches: list[Any]) -> str:
    lines: list[str] = []
    for branch in branches:
        if not isinstance(branch, dict):
            lines.append(f"- 分支：{_plain_value(branch)}")
            continue
        key = branch.get("key")
        label = branch.get("label") or key or "未命名"
        if key:
            lines.append(f"- 分支：{label}（key：`{key}`）")
        else:
            lines.append(f"- 分支：{label}")
    return "\n".join(lines) if lines else "无分支"


def _list_block(items: list[Any]) -> str:
    if not items:
        return "无"
    lines: list[str] = []
    for item in items:
        if isinstance(item, dict):
            lines.append(f"- {_dict_summary(item)}")
        else:
            lines.append(f"- {_plain_value(item)}")
    return "\n".join(lines)


def _dict_block(value: dict[str, Any]) -> str:
    if not value:
        return "无"
    return "\n".join(f"- {_field_label(key)}：{_plain_value(item)}" for key, item in sorted(value.items()))


def _dict_summary(value: dict[str, Any]) -> str:
    if not value:
        return "空对象"
    return "，".join(f"{_field_label(key)}：{_plain_value(item)}" for key, item in sorted(value.items()))


def _plain_value(value: Any) -> str:
    if value is None:
        return "无"
    if isinstance(value, bool):
        return "是" if value else "否"
    if isinstance(value, list):
        return "、".join(_plain_value(item) for item in value) if value else "无"
    if isinstance(value, dict):
        return _dict_summary(value)
    return str(value)


def _compact_json(value: Any, limit: int) -> str:
    try:
        text = json.dumps(value, ensure_ascii=False, indent=2)
    except TypeError:
        text = str(value)
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"


def _input_kind_label(kind: Any) -> str:
    labels = {
        "text": "文本",
        "textarea": "文本",
        "file": "文件上传",
        "select": "文本",
    }
    return labels.get(str(kind), str(kind))


def _field_label(field: Any) -> str:
    labels = {
        "asset_kind": "素材类型",
        "created_at": "创建时间",
        "height": "高度",
        "id": "ID",
        "kind": "类型",
        "label": "显示文案",
        "mime": "MIME 类型",
        "model": "模型",
        "name": "名称",
        "options": "可选项",
        "placeholder": "输入提示",
        "reasoning_effort": "推理等级",
        "required": "是否必填",
        "size": "大小",
        "source": "来源节点",
        "branch_key": "分支 handle",
        "target": "目标节点",
        "url": "链接",
        "width": "宽度",
        "x": "x",
        "y": "y",
        "zoom": "缩放",
    }
    return labels.get(str(field), str(field))


def _fenced_block(text: str, language: str) -> str:
    max_ticks = max((len(match.group(0)) for match in re.finditer(r"`+", text)), default=0)
    fence = "`" * max(3, max_ticks + 1)
    return f"{fence}{language}\n{text}\n{fence}"


# --- 工具 -----------------------------------------------------------------


def _node_ids(graph: dict[str, Any]) -> set[str]:
    return {node["id"] for node in graph.get("nodes", []) if isinstance(node.get("id"), str)}


def _node_by_id(graph: dict[str, Any], node_id: Any) -> dict[str, Any] | None:
    if not isinstance(node_id, str):
        return None
    return next((node for node in graph.get("nodes", []) if node.get("id") == node_id), None)
