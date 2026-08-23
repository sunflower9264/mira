from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db import SessionLocal
from app.models import App, PromptAssistantGenerationRow
from app.runtime.base import AgentChunk, DecisionAttachment, DecisionRequest, DecisionResult
from app.runtime.factory import get_runtime
from app.schemas.requests import PromptAssistantGenerateIn, PromptAssistantResumeIn
from app.services import runtime_config
from app.services.decision_prompts import append_none_option, validate_decision_groups, validate_decision_answers
from app.services.execution_plan import ExecutionPlanError, compile_execution_plan
from app.services.graph_inputs import prepare_planning_graph
from app.services.output_contracts import validate_output_contract_config
from app.services.prompts import get_prompt_content, render_prompt
from app.services.prompt_contracts import (
    build_structured_repair_prompt,
    max_attempts_for,
    output_schema_for,
)
from app.services.reasoning_effort import normalize_reasoning_effort
from app.services.runtime_paths import prompt_assistant_workspace
from app.services.structured_output import parse_structured_json_object
from app.services.tools import RuntimeToolConfig, planning_runtime_tools_for_graph
from app.services.uploads import resolve_upload
from app.utils import now_utc

NODE_TYPE_LABELS = {
    "user_input": "用户输入",
    "generate": "生成",
    "condition": "判断",
    "output": "输出",
    "asset": "素材",
}
PROMPT_NODE_TYPES = {"generate", "condition", "output"}
_TARGET_PROMPT_MAX_BYTES = 200 * 1024
_ASSISTANT_PROMPT_MAX_BYTES = 200 * 1024
_RELATED_PROMPT_LIMIT = 1200

_CANCELLED_DETAIL = "提示词生成已取消"
_DECISION_WAITING_STOPPED_DETAIL = "decision_request runtime stopped while waiting; resume from persisted pending request"
_PROMPT_ASSISTANT_TERMINAL_STATUSES = {"completed", "failed", "cancelled"}
_PROMPT_ASSISTANT_ACTIVE_STATUSES = {"running", "waiting_for_user", "interrupted"}


@dataclass
class PromptAssistantSession:
    id: str
    user_id: str
    app_id: str
    response_future: asyncio.Future[dict[str, Any]]
    cancel_event: asyncio.Event
    prompt: str
    model: str | None
    reasoning_effort: str | None
    graph: dict[str, Any]
    task: asyncio.Task[None] | None = None
    resume_future: asyncio.Future[DecisionResult] | None = None
    current_request: DecisionRequest | None = None
    waiter_count: int = 0


@dataclass(frozen=True)
class PromptAssistantResult:
    prompt: str
    output_contract: dict[str, Any] | None = None


_generation_sessions: dict[str, PromptAssistantSession] = {}


def _json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def _json_loads(value: str | None, default: Any) -> Any:
    if value is None:
        return default
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return default


def _prompt_payload(row: PromptAssistantGenerationRow) -> dict[str, Any]:
    payload = _json_loads(row.prompt_json, {})
    return payload if isinstance(payload, dict) else {}


def _history_from_row(row: PromptAssistantGenerationRow) -> list[dict[str, Any]]:
    history = _json_loads(row.history_json, [])
    return history if isinstance(history, list) else []


def _apply_row_update(
    row: PromptAssistantGenerationRow,
    *,
    status: str | None = None,
    history: list[dict[str, Any]] | None = None,
    pending_request: dict[str, Any] | None = None,
    result: dict[str, Any] | None = None,
    error: str | None = None,
) -> None:
    if status is not None:
        row.status = status
    if history is not None:
        row.history_json = _json_dumps(history)
    if pending_request is not None:
        row.pending_request_json = _json_dumps(pending_request)
    elif status in {"running", "completed", "failed", "cancelled"}:
        row.pending_request_json = None
    if result is not None:
        row.result_json = _json_dumps(result)
    row.error = error
    row.updated_at = now_utc()


async def generate_prompt_assistant(
    db: AsyncSession,
    user_id: str,
    app: App,
    payload: PromptAssistantGenerateIn,
) -> dict[str, Any]:
    generation_id = (payload.generation_id or "").strip() or f"pa_{uuid4().hex[:16]}"
    if generation_id in _generation_sessions:
        raise HTTPException(status_code=409, detail="提示词生成会话已存在")
    existing = await db.get(PromptAssistantGenerationRow, generation_id)
    if existing is not None:
        raise HTTPException(status_code=409, detail="提示词生成会话已存在")

    graph = prepare_planning_graph(payload.graph)
    await runtime_config.write_configs(db)
    template = await get_prompt_content(db, "prompt_assistant")
    prompt = build_prompt_assistant_prompt(
        graph=graph,
        node_id=payload.node_id,
        user_request=payload.user_request,
        template=template,
    )
    row = PromptAssistantGenerationRow(
        id=generation_id,
        user_id=user_id,
        app_id=app.id,
        status="running",
        prompt_json=_json_dumps({"prompt": prompt, "graph": graph}),
        model=(payload.model or "").strip() or None,
        reasoning_effort=(payload.reasoning_effort or "").strip() or None,
        history_json="[]",
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    session = await _start_prompt_assistant_session_from_row(db, row)
    return await _await_prompt_assistant_response(session)


async def _start_prompt_assistant_session_from_row(
    db: AsyncSession,
    row: PromptAssistantGenerationRow,
    *,
    replay_result: DecisionResult | None = None,
) -> PromptAssistantSession:
    payload = _prompt_payload(row)
    prompt = str(payload.get("prompt") or "")
    graph = prepare_planning_graph(payload.get("graph")) if isinstance(payload.get("graph"), dict) else {}
    if not prompt or not graph:
        raise HTTPException(status_code=502, detail="提示词生成会话缺少可恢复上下文")
    await runtime_config.write_configs(db)
    runtime = get_runtime()
    planning_runtime_tools = await planning_runtime_tools_for_graph(db, graph)
    session = PromptAssistantSession(
        id=row.id,
        user_id=row.user_id,
        app_id=row.app_id,
        response_future=asyncio.get_running_loop().create_future(),
        cancel_event=asyncio.Event(),
        prompt=prompt,
        model=row.model,
        reasoning_effort=row.reasoning_effort,
        graph=graph,
    )
    _generation_sessions[row.id] = session
    session.task = asyncio.create_task(
        _run_prompt_assistant_session(
            session=session,
            runtime=runtime,
            runtime_tools=planning_runtime_tools,
            replay_result=replay_result,
        ),
        name=f"prompt-assistant-{row.id}",
    )
    return session


async def _run_prompt_assistant_session(
    *,
    session: PromptAssistantSession,
    runtime: object,
    runtime_tools: RuntimeToolConfig | None,
    replay_result: DecisionResult | None = None,
) -> None:
    replayed_result = replay_result

    async def on_decision_request(request: DecisionRequest) -> DecisionResult:
        nonlocal replayed_result
        if replayed_result is not None:
            result = replayed_result
            replayed_result = None
            return result
        protocol_error = validate_decision_groups(request.groups)
        if protocol_error:
            return DecisionResult(ok=False, error=protocol_error)
        request = request.model_copy(update={"groups": append_none_option(request.groups)})
        if session.resume_future is not None and not session.resume_future.done():
            return DecisionResult(ok=False, error="不允许并发 decision_request")
        session.current_request = request
        session.resume_future = asyncio.get_running_loop().create_future()
        request_payload = request.model_dump(exclude_none=True)
        await _write_generation_row(
            session.id,
            status="waiting_for_user",
            pending_request=request_payload,
            error=None,
        )
        _publish_prompt_assistant_response(
            session,
            {
                "status": "waiting_for_user",
                "generation_id": session.id,
                "request": request_payload,
            },
        )
        try:
            return await session.resume_future
        finally:
            session.current_request = None
            session.resume_future = None

    try:
        generated = await run_prompt_assistant(
            runtime=runtime,
            user_id=session.user_id,
            prompt=session.prompt,
            model=session.model,
            reasoning_effort=session.reasoning_effort,
            cancel_event=session.cancel_event,
            on_decision_request=on_decision_request,
            runtime_tools=runtime_tools,
            runtime_policy="plan",
        )
        completed = {
            "status": "completed",
            "prompt": generated.prompt,
            "output_contract": generated.output_contract,
        }
        await _write_generation_row(
            session.id,
            status="completed",
            pending_request=None,
            result=completed,
            error=None,
        )
        _publish_prompt_assistant_response(
            session,
            completed,
        )
    except HTTPException as exc:
        if await _preserve_waiting_prompt_assistant_session(session):
            return
        await _write_generation_row(session.id, status="failed", pending_request=None, error=str(exc.detail))
        _publish_prompt_assistant_exception(session, exc)
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001
        if await _preserve_waiting_prompt_assistant_session(session):
            return
        await _write_generation_row(session.id, status="failed", pending_request=None, error=f"Agent 生成提示词失败: {exc}")
        _publish_prompt_assistant_exception(
            session,
            HTTPException(status_code=502, detail=f"Agent 生成提示词失败: {exc}"),
        )


async def resume_prompt_assistant(
    db: AsyncSession,
    user_id: str,
    generation_id: str,
    payload: PromptAssistantResumeIn,
) -> dict[str, Any]:
    row = await db.get(PromptAssistantGenerationRow, generation_id)
    if row is None or row.user_id != user_id:
        raise HTTPException(status_code=404, detail="提示词生成会话不存在")
    if row.status in _PROMPT_ASSISTANT_TERMINAL_STATUSES:
        raise HTTPException(status_code=404, detail="提示词生成会话不存在")
    session = _generation_sessions.get(generation_id)
    if session is not None and session.user_id != user_id:
        raise HTTPException(status_code=404, detail="提示词生成会话不存在")

    request_payload = _json_loads(row.pending_request_json, None)
    if session is not None and session.current_request is not None:
        request = session.current_request
    elif isinstance(request_payload, dict):
        request = DecisionRequest.model_validate(request_payload)
    else:
        raise HTTPException(status_code=409, detail="当前没有等待输入")

    result = _build_decision_request_result(user_id, request, payload)
    history = _history_from_row(row)
    history.append(_qa_history_entry(request, result))
    _apply_row_update(row, status="running", history=history, pending_request=None, error=None)
    await db.commit()

    if session is not None and session.resume_future is not None and not session.resume_future.done():
        session.resume_future.set_result(result)
        return await _await_prompt_assistant_response(session)

    await db.refresh(row)
    session = await _start_prompt_assistant_session_from_row(db, row, replay_result=result)
    return await _await_prompt_assistant_response(session)


def _build_decision_request_result(
    user_id: str,
    request: DecisionRequest,
    payload: PromptAssistantResumeIn,
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


def _qa_history_entry(request: DecisionRequest, result: DecisionResult) -> dict[str, Any]:
    return {
        "kind": "decision_request",
        "request": request.model_dump(exclude_none=True),
        "result": result.model_dump(exclude_none=True),
    }


async def _write_generation_row(
    generation_id: str,
    *,
    status: str | None = None,
    history: list[dict[str, Any]] | None = None,
    pending_request: dict[str, Any] | None = None,
    result: dict[str, Any] | None = None,
    error: str | None = None,
) -> None:
    async with SessionLocal() as db:
        row = await db.get(PromptAssistantGenerationRow, generation_id)
        if row is None:
            return
        _apply_row_update(
            row,
            status=status,
            history=history,
            pending_request=pending_request,
            result=result,
            error=error,
        )
        await db.commit()


async def _preserve_waiting_prompt_assistant_session(session: PromptAssistantSession) -> bool:
    async with SessionLocal() as db:
        row = await db.get(PromptAssistantGenerationRow, session.id)
        request_payload = _json_loads(row.pending_request_json if row is not None else None, None)
        if row is None or row.status != "waiting_for_user" or not isinstance(request_payload, dict):
            return False

    if session.resume_future is not None and not session.resume_future.done():
        session.resume_future.set_result(DecisionResult(ok=False, error=_DECISION_WAITING_STOPPED_DETAIL))
    if not session.response_future.done():
        session.response_future.set_result(
            {
                "status": "waiting_for_user",
                "generation_id": session.id,
                "request": request_payload,
            }
        )
    _generation_sessions.pop(session.id, None)
    return True


async def run_prompt_assistant(
    *,
    runtime: object,
    user_id: str,
    prompt: str,
    model: str | None,
    reasoning_effort: str | None,
    cancel_event: asyncio.Event,
    on_decision_request=None,
    runtime_tools: RuntimeToolConfig | None = None,
    runtime_policy: str = "execute",
) -> PromptAssistantResult:
    prompt_contract_key = "prompt_assistant"
    current_prompt = prompt
    previous_output = ""
    last_error = ""
    for attempt in range(1, max_attempts_for(prompt_contract_key) + 1):
        result_text = await _execute_prompt_assistant_once(
            runtime=runtime,
            user_id=user_id,
            prompt=current_prompt,
            model=model,
            reasoning_effort=reasoning_effort,
            cancel_event=cancel_event,
            on_decision_request=on_decision_request if attempt == 1 else None,
            runtime_tools=runtime_tools if attempt == 1 else None,
            runtime_policy=runtime_policy if attempt == 1 else "execute",
        )
        previous_output = result_text
        try:
            return _parse_prompt_assistant_output(result_text)
        except HTTPException as exc:
            last_error = str(exc.detail)
            if attempt >= max_attempts_for(prompt_contract_key):
                raise
            current_prompt = build_structured_repair_prompt(
                task_name="提示词助手",
                original_prompt=prompt,
                previous_output=previous_output,
                validation_error=last_error,
                output_shape='{"prompt":"完整 prompt 正文","output_contract_json":"{...}" 或 null}',
                output_schema=output_schema_for(prompt_contract_key),
            )
    raise HTTPException(status_code=502, detail=last_error or "Agent 未返回提示词，请调整描述后重试")


async def _execute_prompt_assistant_once(
    *,
    runtime: object,
    user_id: str,
    prompt: str,
    model: str | None,
    reasoning_effort: str | None,
    cancel_event: asyncio.Event,
    on_decision_request=None,
    runtime_tools: RuntimeToolConfig | None = None,
    runtime_policy: str = "execute",
) -> str:
    chunks: list[str] = []

    async def on_chunk(chunk: AgentChunk) -> None:
        if chunk.type == "text" and chunk.text:
            chunks.append(chunk.text)

    execute_coro = runtime.execute(
        prompt=prompt,
        session_id=None,
        model=(model or "").strip() or None,
        reasoning_effort=normalize_reasoning_effort(reasoning_effort),
        cwd=prompt_assistant_workspace(user_id),
        on_chunk=on_chunk,
        cancel_event=cancel_event,
        on_decision_request=on_decision_request,
        runtime_tools=runtime_tools,
        runtime_policy=runtime_policy,
        output_schema=output_schema_for("prompt_assistant"),
    )
    result = await execute_coro
    if cancel_event.is_set() or result.finished_with == "cancelled":
        raise HTTPException(status_code=409, detail=_CANCELLED_DETAIL)
    if result.finished_with != "done":
        detail = "Agent 生成提示词失败，请检查 Agent 配置或稍后重试"
        error = str(result.error or "").strip()
        if error:
            detail = f"{detail}：{_clip(error, 500)}"
        raise HTTPException(status_code=502, detail=detail)
    return result.total_text or "".join(chunks)


async def cancel_prompt_assistant(db: AsyncSession, user_id: str, generation_id: str) -> None:
    row = await db.get(PromptAssistantGenerationRow, generation_id)
    session = _generation_sessions.get(generation_id)
    if row is None or row.user_id != user_id:
        raise HTTPException(status_code=404, detail="提示词生成会话不存在")
    _apply_row_update(row, status="cancelled", pending_request=None, error=_CANCELLED_DETAIL)
    await db.commit()
    if session is None:
        return
    session.cancel_event.set()
    if session.resume_future is not None and not session.resume_future.done():
        session.resume_future.set_result(DecisionResult(ok=False, error=_CANCELLED_DETAIL))
    _generation_sessions.pop(generation_id, None)


async def active_prompt_assistant_for_app(db: AsyncSession, user_id: str, app_id: str) -> dict[str, Any] | None:
    row = (
        await db.execute(
            select(PromptAssistantGenerationRow)
            .where(
                PromptAssistantGenerationRow.user_id == user_id,
                PromptAssistantGenerationRow.app_id == app_id,
                PromptAssistantGenerationRow.status.in_(("waiting_for_user", "interrupted")),
            )
            .order_by(PromptAssistantGenerationRow.updated_at.desc(), PromptAssistantGenerationRow.created_at.desc())
        )
    ).scalars().first()
    if row is None:
        return None
    if row.status == "interrupted":
        return {"status": "interrupted", "generation_id": row.id, "error": row.error or "提示词生成已中断"}
    request = _json_loads(row.pending_request_json, None)
    if not isinstance(request, dict):
        return None
    return {"status": "waiting_for_user", "generation_id": row.id, "request": request}


async def mark_active_prompt_assistant_sessions_interrupted(db: AsyncSession) -> int:
    rows = (
        await db.execute(
            select(PromptAssistantGenerationRow)
            .where(PromptAssistantGenerationRow.status == "running")
            .order_by(PromptAssistantGenerationRow.created_at.asc(), PromptAssistantGenerationRow.id.asc())
        )
    ).scalars().all()
    changed = 0
    for row in rows:
        _apply_row_update(row, status="interrupted", error="后端进程重启，提示词生成已暂停")
        changed += 1
    if changed:
        await db.commit()
    return changed


async def _await_prompt_assistant_response(session: PromptAssistantSession) -> dict[str, Any]:
    session.waiter_count += 1
    try:
        payload = await session.response_future
    except HTTPException:
        _generation_sessions.pop(session.id, None)
        raise
    finally:
        session.waiter_count = max(0, session.waiter_count - 1)
    if payload.get("status") == "waiting_for_user":
        session.response_future = asyncio.get_running_loop().create_future()
        return payload
    _generation_sessions.pop(session.id, None)
    return payload


def _publish_prompt_assistant_response(session: PromptAssistantSession, payload: dict[str, Any]) -> None:
    if not session.response_future.done():
        session.response_future.set_result(payload)


def _publish_prompt_assistant_exception(session: PromptAssistantSession, exc: HTTPException) -> None:
    if session.waiter_count > 0 and not session.response_future.done():
        session.response_future.set_exception(exc)


def build_prompt_assistant_prompt(
    *,
    graph: dict,
    node_id: str,
    user_request: str,
    template: str,
    plan_context: str | None = None,
) -> str:
    node = _node_by_id(graph, node_id)
    if node is None:
        raise HTTPException(status_code=404, detail="节点不存在")
    if node.get("type") not in PROMPT_NODE_TYPES:
        raise HTTPException(status_code=400, detail="该节点不支持生成提示词")

    target_prompt = str(node.get("prompt") or "")
    if len(target_prompt.encode("utf-8")) > _TARGET_PROMPT_MAX_BYTES:
        raise HTTPException(
            status_code=400,
            detail="当前节点提示词超过 200 KiB，提示词助手无法完整处理，请先缩短后重试",
        )

    request_section = user_request.strip() or "（用户没有额外描述，请根据节点职责和上下游关系优化）"
    supports_contract = node.get("type") == "generate"
    contract_rules = (
        """output_contract 规则：
	- 当前节点是 generate 节点时，请同时给出建议的 output_contract。
	- output_contract_json 只表示需要新增或变更的设置；当前设置仍合适且无需修改时返回 null，以保留现状。
	- output_contract 可选形状：
	  - {"type":"json","json_schema":{"type":"object","additionalProperties":false,"properties":{...},"required":[...]}}
	  - {"type":"html"}
	  - {"type":"artifact","artifact_kind":"image|code|html|markdown|csv|excel|docx|ppt|pdf|archive|zip|file","validate_office_documents":true|false（可选）}
	- 自由文本是默认选择；普通写作、总结、分析、推荐、草稿和说明类节点不需要 output_contract，返回 null。
	- 只有用户明确要求 JSON、结构化字段、固定字段，或下游明显需要机器读取字段时，才建议 json，并给出 strict object JSON Schema：根 type 必须是 object，additionalProperties 必须是 false，required 必须包含所有 properties 字段；根对象及每个 properties 业务字段（含嵌套字段）都必须有简短准确的中文 title 和 description。
	- 只有用户明确要求当前 generate 节点直接产出可嵌入预览的 HTML 片段时，才建议 html；最终展示通常由 output 节点负责，不要为了“好看”给中间 generate 节点套 html。
	- 当用户明确要求图片、代码包、HTML 文件、Markdown 文件、CSV、Excel、DOCX、PPT、PDF、压缩包或其他可下载文件时，建议 artifact 并选择最贴近的 artifact_kind。"""
        if supports_contract
        else "output_contract 规则：当前节点不是 generate 节点，output_contract 必须返回 null。"
    )

    prompt = render_prompt(
        template,
        {
            "user_request": request_section,
            "plan_context": (plan_context or "").strip() or "（无）",
            "node_context": _node_summary(node, detail="full"),
            "upstream_context": _related_nodes_summary(graph, node_id, direction="upstream"),
            "downstream_context": _related_nodes_summary(graph, node_id, direction="downstream"),
            "contract_rules": contract_rules,
        },
    ).strip()
    if len(prompt.encode("utf-8")) > _ASSISTANT_PROMPT_MAX_BYTES:
        raise HTTPException(
            status_code=400,
            detail="提示词助手上下文超过 200 KiB，请缩短用户说明或相邻节点提示词后重试",
        )
    return prompt


def _node_by_id(graph: dict, node_id: str) -> dict | None:
    for node in graph.get("nodes", []):
        if isinstance(node, dict) and node.get("id") == node_id:
            return node
    return None


def _related_nodes_summary(graph: dict, node_id: str, *, direction: str) -> str:
    try:
        plan = compile_execution_plan(graph)
    except ExecutionPlanError:
        return "（工作流结构尚未形成有效执行图）"
    if direction == "upstream":
        ancestor_ids = plan.ancestor_ids(node_id)
        if not ancestor_ids:
            return "（无执行祖先）"
        lines: list[str] = []
        for source_id in ancestor_ids:
            source = plan.nodes_by_id.get(source_id)
            if not source:
                continue
            relation = f"执行祖先 `{source_id}`：运行时结果会自动出现在当前节点的只读结果池"
            lines.append(f"{relation}\n{_node_summary(source, detail='related')}")
        return "\n\n".join(lines) if lines else "（无执行祖先）"

    child_ids = plan.children.get(node_id, frozenset())
    if not child_ids:
        return "（无直接下游）"
    lines = []
    for target_id in plan.ordered_node_ids:
        if target_id not in child_ids:
            continue
        target = plan.nodes_by_id.get(target_id)
        if not target:
            continue
        relation = f"直接执行后继：`{node_id}` -> `{target_id}`"
        lines.append(f"{relation}\n{_node_summary(target, detail='related')}")
    return "\n\n".join(lines) if lines else "（无直接下游）"


def _node_summary(node: dict, *, detail: str) -> str:
    node_type = str(node.get("type") or "unknown")
    lines = [
        f"- ID：`{node.get('id') or ''}`",
        f"- 类型：{NODE_TYPE_LABELS.get(node_type, node_type)} (`{node_type}`)",
        f"- 标题：{node.get('title') or '未命名'}",
    ]
    description = str(node.get("description") or "").strip()
    if description:
        lines.append(f"- 描述：{_clip(description, 500)}")
    if node.get("model"):
        lines.append(f"- 模型：{node.get('model')}")
    if node.get("reasoning_effort"):
        lines.append(f"- 推理强度：{node.get('reasoning_effort')}")

    if node_type in PROMPT_NODE_TYPES:
        prompt = str(node.get("prompt") or "")
        if not prompt.strip():
            prompt = "（当前没有提示词）"
        elif detail != "full":
            prompt = _clip_head_tail(prompt, _RELATED_PROMPT_LIMIT)
        lines.append(f"- 当前提示词：\n{prompt}")
    if node_type == "generate":
        contract = node.get("output_contract")
        if isinstance(contract, dict):
            contract_text = json.dumps(contract, ensure_ascii=False, sort_keys=True)
            if detail != "full":
                contract_text = _clip_head_tail(contract_text, _RELATED_PROMPT_LIMIT)
            lines.append(f"- 当前 output_contract：{contract_text}")
        else:
            lines.append("- 当前 output_contract：未设置（自由文本）")
    if node_type == "condition":
        lines.append(f"- 分支：{_compact_json(node.get('branches'), 1200)}")
    if node_type == "user_input":
        lines.append(f"- 输入 schema：{_compact_json(node.get('input_schema'), 1000)}")
    if node_type == "asset":
        lines.append(f"- 素材类型：{node.get('asset_kind') or ''}")
        if node.get("content"):
            lines.append(f"- 素材内容：{_clip(str(node.get('content')), 1000)}")
        if node.get("urls"):
            lines.append(f"- 素材链接：{_compact_json(node.get('urls'), 1000)}")
        if node.get("uploads"):
            lines.append(f"- 上传文件：{_compact_json(node.get('uploads'), 1000)}")
        if node.get("upload"):
            lines.append(f"- 上传文件：{_compact_json(node.get('upload'), 1000)}")
    return "\n".join(lines)


def _compact_json(value: object, limit: int) -> str:
    return _clip(json.dumps(value, ensure_ascii=False, sort_keys=True), limit)


def _clip(text: str, limit: int) -> str:
    normalized = text.strip()
    if len(normalized) <= limit:
        return normalized
    return normalized[:limit].rstrip() + "\n（已截断）"


def _clip_head_tail(text: str, limit: int) -> str:
    normalized = text.strip()
    if len(normalized) <= limit:
        return normalized
    head_length = limit // 2
    tail_length = limit - head_length
    omitted = len(normalized) - head_length - tail_length
    return (
        normalized[:head_length].rstrip()
        + f"\n（中间已省略 {omitted} 字符）\n"
        + normalized[-tail_length:].lstrip()
    )


def _parse_prompt_assistant_output(text: str) -> PromptAssistantResult:
    try:
        parsed = parse_structured_json_object(text, label="提示词助手输出")
    except ValueError as exc:
        raise HTTPException(status_code=502, detail="Agent 未按 JSON 格式返回提示词，请稍后重试") from exc
    prompt = parsed.get("prompt")
    if not isinstance(prompt, str) or not prompt.strip():
        raise HTTPException(status_code=502, detail="Agent 未返回提示词，请调整描述后重试")
    if "output_contract_json" not in parsed:
        raise HTTPException(status_code=502, detail="Agent 返回的提示词缺少 output_contract，请稍后重试")
    output_contract = _output_contract_from_json(parsed.get("output_contract_json"))
    return PromptAssistantResult(
        prompt=prompt.strip(),
        output_contract=output_contract,
    )


def _output_contract_from_json(value: Any) -> dict[str, Any] | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return None
    return _clean_output_contract(parsed)


def _clean_output_contract(value: Any) -> dict[str, Any] | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        return None
    output_type = value.get("type")
    if output_type not in {"json", "html", "artifact"}:
        return None
    cleaned: dict[str, Any] = {"type": output_type}
    if output_type == "json":
        json_schema = value.get("json_schema")
        if isinstance(json_schema, dict):
            cleaned["json_schema"] = json_schema
    if output_type == "artifact":
        artifact_kind = value.get("artifact_kind")
        if isinstance(artifact_kind, str) and artifact_kind.strip():
            cleaned["artifact_kind"] = artifact_kind.strip()
        max_count = value.get("max_count")
        if isinstance(max_count, int) and not isinstance(max_count, bool):
            cleaned["max_count"] = max_count
        validate_office = value.get("validate_office_documents")
        if isinstance(validate_office, bool):
            cleaned["validate_office_documents"] = validate_office
    error = validate_output_contract_config(
        {"id": "prompt_assistant", "type": "generate", "title": "提示词助手", "output_contract": cleaned}
    )
    return None if error else cleaned
