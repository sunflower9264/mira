"""单节点 handler：把 graph 中各种节点类型映射到具体的执行动作。

执行模型：
- 每次 run 拿到一个 ``ExecutionContext``，包含 cancel_event、上游节点输出表、
  hub 通道、user_id / app_id / inputs。
- handler 返回 ``NodeResult``：success+output、failed+error 或 cancelled。
- generate / output / condition 的 LLM 调用统一走 ``app.runtime.factory.get_runtime``。

``_run_llm`` 先用 Codex Plan mode 补齐关键决策，再执行节点主体。
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Run, RunAgentBranch, Step, StepLog
from app.runtime.base import AgentChunk, AskUserRequest, AskUserResult
from app.runtime.factory import get_runtime
from app.schemas import RunInputValue
from app.services.decision_prompts import append_ask_user_none_option, validate_ask_request_groups
from app.services.execution_plan import ExecutionPlan, compile_execution_plan
from app.services.output_contracts import (
    ContractValidationResult,
    contract_prompt_suffix,
    contract_repair_description,
    schema_for_contract,
    validate_contract_output,
)
from app.services.prompts import get_prompt_content, render_prompt
from app.services.reasoning_effort import normalize_reasoning_effort
from app.services.run_hub import RunChannel
from app.services.runs import attachments_meta
from app.services.runtime_uploads import RuntimeUploadRef, rewrite_runtime_upload_paths, runtime_upload_context
from app.services.tools import RuntimeToolConfig
from app.services.text_integrity import UNICODE_REPLACEMENT_ERROR, contains_unicode_replacement
from app.services.uploads import resolve_upload
from app.services.run_serializer import log_to_out
from app.services.workflow_data import (
    WorkflowDataIntegrityError,
    workflow_data_prompt,
)
from app.utils import dumps, loads, new_id, now_utc

logger = logging.getLogger(__name__)
_OFFICE_VALIDATION_CONCURRENCY = 2
_OFFICE_VALIDATION_SEMAPHORE = asyncio.Semaphore(_OFFICE_VALIDATION_CONCURRENCY)
_UNICODE_REPAIR_MARKER = "[[MIRA_CORRUPTED_TEXT]]"
_UNICODE_REPLACEMENT_ESCAPE_RE = re.compile(r"\\u[fF]{3}[dD]")
_ASK_USER_PLAN_OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "decision_summary": {"type": "string", "minLength": 1},
        "reason": {"type": "string", "minLength": 1},
    },
    "required": ["decision_summary", "reason"],
}


# --- 数据结构 ----------------------------------------------------------------


@dataclass
class NodeResult:
    status: str  # "success" / "failed" / "skipped" / "cancelled"
    output: Any = None
    error: str | None = None
    failure_kind: str | None = None
    agent_session_id: str | None = None


@dataclass
class ExecutionContext:
    db: AsyncSession
    channel: RunChannel
    user_id: str
    asset_owner_id: str
    app_id: str
    run_id: str
    graph: dict[str, Any]
    workspace: Path
    # 用户启动时收集的 user_input 节点输入：{node_id: RunInputValue}。
    inputs: dict[str, RunInputValue]
    execution_plan: ExecutionPlan
    runtime_tools: RuntimeToolConfig | None = None
    planning_runtime_tools: RuntimeToolConfig | None = None
    agent_session_id: str | None = None
    fork_session: bool = False
    # 已经执行成功的节点 output：{node_id: any}；handler 通过它获取上游上下文。
    outputs: dict[str, Any] = field(default_factory=dict)
    # 节点类型 / 标题查表，避免重复扫 graph。
    nodes_by_id: dict[str, dict[str, Any]] = field(default_factory=dict)
    # 已知应跳过的节点 id（来自未选中的 condition 分支）。
    skipped_nodes: set[str] = field(default_factory=set)


def build_context(
    db: AsyncSession,
    channel: RunChannel,
    *,
    user_id: str,
    asset_owner_id: str | None = None,
    app_id: str,
    run_id: str,
    graph: dict[str, Any],
    workspace: Path,
    inputs: dict[str, RunInputValue],
    execution_plan: ExecutionPlan | None = None,
    runtime_tools: RuntimeToolConfig | None = None,
    planning_runtime_tools: RuntimeToolConfig | None = None,
) -> ExecutionContext:
    plan = execution_plan or compile_execution_plan(graph)
    return ExecutionContext(
        db=db,
        channel=channel,
        user_id=user_id,
        asset_owner_id=asset_owner_id or user_id,
        app_id=app_id,
        run_id=run_id,
        graph=graph,
        workspace=workspace,
        inputs=inputs,
        execution_plan=plan,
        runtime_tools=runtime_tools,
        planning_runtime_tools=planning_runtime_tools,
        nodes_by_id=plan.nodes_by_id,
    )


# --- 节点分发 ----------------------------------------------------------------


async def run_node(ctx: ExecutionContext, node: dict[str, Any], step: Step) -> NodeResult:
    """根据 node["type"] 分发到具体 handler。"""

    node_type = node.get("type")
    if ctx.channel.cancel_event.is_set():
        return NodeResult(status="cancelled")
    if node_type == "user_input":
        return await _handle_user_input(ctx, node, step)
    if node_type == "asset":
        return await _handle_asset(ctx, node, step)
    if node_type == "generate":
        return await _handle_generate(ctx, node, step)
    if node_type == "output":
        return await _handle_output(ctx, node, step)
    if node_type == "condition":
        return await _handle_condition(ctx, node, step)
    return NodeResult(status="failed", error=f"未知节点类型: {node_type}", failure_kind="routing")


# --- 具体 handler -----------------------------------------------------------


async def _handle_user_input(ctx: ExecutionContext, node: dict[str, Any], step: Step) -> NodeResult:
    node_id = node["id"]
    raw = ctx.inputs.get(node_id, RunInputValue())
    metas = attachments_meta(ctx.user_id, raw)
    payload: dict[str, Any] = {"value": raw.value}
    if metas:
        payload["attachments"] = metas
    await asyncio.to_thread(_persist_workspace_context, ctx, node, payload)
    step.input_json = dumps(payload)
    await _append_log(ctx, step, "info", "读取用户输入")
    return NodeResult(status="success", output=payload)


async def _handle_asset(ctx: ExecutionContext, node: dict[str, Any], step: Step) -> NodeResult:
    asset_kind = str(node.get("asset_kind") or "text")
    if asset_kind == "file":
        uploads = node.get("uploads")
        if not isinstance(uploads, list) or not uploads:
            return NodeResult(status="failed", error="asset 节点 uploads 缺失", failure_kind="runtime")
        payloads: list[dict[str, Any]] = []
        for upload in uploads:
            upload_id = upload.get("id") if isinstance(upload, dict) else None
            if not isinstance(upload_id, str) or not upload_id.strip():
                return NodeResult(status="failed", error="asset 节点 upload 缺失", failure_kind="runtime")
            resolved = resolve_upload(ctx.asset_owner_id, upload_id)
            if resolved is None:
                return NodeResult(status="failed", error="asset 节点上传文件不存在", failure_kind="runtime")
            payloads.append(resolved.to_tool_payload(ctx.asset_owner_id))
        step.input_json = dumps({"asset_kind": asset_kind, "uploads": payloads})
        await asyncio.to_thread(_persist_workspace_context, ctx, node, payloads)
        await _append_log(ctx, step, "info", "读取上传素材")
        return NodeResult(status="success", output=payloads)

    if asset_kind == "drawing":
        upload = node.get("upload")
        upload_id = upload.get("id") if isinstance(upload, dict) else None
        if not isinstance(upload_id, str) or not upload_id.strip():
            return NodeResult(status="failed", error="asset 节点 upload 缺失", failure_kind="runtime")
        resolved = resolve_upload(ctx.asset_owner_id, upload_id)
        if resolved is None:
            return NodeResult(status="failed", error="asset 节点上传文件不存在", failure_kind="runtime")
        payload = resolved.to_tool_payload(ctx.asset_owner_id)
        step.input_json = dumps({"asset_kind": asset_kind, "upload": payload})
        await asyncio.to_thread(_persist_workspace_context, ctx, node, payload)
        await _append_log(ctx, step, "info", "读取上传素材")
        return NodeResult(status="success", output=payload)

    if asset_kind == "url":
        urls = node.get("urls")
        if not isinstance(urls, list):
            return NodeResult(status="failed", error="asset 节点 urls 缺失", failure_kind="runtime")
        payload = [url.strip() for url in urls if isinstance(url, str) and url.strip()]
        step.input_json = dumps({"asset_kind": asset_kind, "urls": payload})
        await asyncio.to_thread(_persist_workspace_context, ctx, node, payload)
        await _append_log(ctx, step, "info", "读取素材链接")
        return NodeResult(status="success", output=payload)

    content = node.get("content")
    if not isinstance(content, str):
        return NodeResult(status="failed", error="asset 节点 content 缺失", failure_kind="runtime")
    step.input_json = dumps({"asset_kind": asset_kind, "content": content})
    await asyncio.to_thread(_persist_workspace_context, ctx, node, content)
    await _append_log(ctx, step, "info", "读取素材内容")
    return NodeResult(status="success", output=content)


async def _handle_generate(ctx: ExecutionContext, node: dict[str, Any], step: Step) -> NodeResult:
    return await _run_llm(ctx, node, step, expects_text=True)


async def _handle_output(ctx: ExecutionContext, node: dict[str, Any], step: Step) -> NodeResult:
    return await _run_llm(ctx, node, step, expects_text=True)


async def _handle_condition(ctx: ExecutionContext, node: dict[str, Any], step: Step) -> NodeResult:
    branches = node.get("branches") or []
    branch_options: list[dict[str, str]] = []
    for branch in branches:
        if not isinstance(branch, dict):
            continue
        key = str(branch.get("key") or "").strip()
        if not key:
            continue
        label = str(branch.get("label") or "").strip() or key
        branch_options.append({"key": key, "label": label})
    valid_keys = [option["key"] for option in branch_options]
    if len(valid_keys) < 2:
        return NodeResult(status="failed", error="condition 节点 branches 数量不足", failure_kind="routing")
    if node.get("mode") == "cases" and _branch_target_nodes(
        ctx,
        str(node.get("id") or ""),
        DEFAULT_BRANCH_KEY,
    ):
        branch_options.append({"key": DEFAULT_BRANCH_KEY, "label": "其它：以上分支均不匹配"})
        valid_keys.append(DEFAULT_BRANCH_KEY)
    forced_branch = _condition_branch_override(ctx, node)
    if forced_branch:
        chosen = forced_branch
        _apply_condition_branch(ctx, node, chosen)
        await _record_condition_result(
            ctx,
            node,
            step,
            chosen=chosen,
            raw_answer=None,
            forced=True,
        )
        return NodeResult(status="success", output=chosen)
    template_content = await get_prompt_content(ctx.db, "condition_choice")
    user_prompt = await _compose_prompt(ctx, node)
    prompt = render_prompt(
        template_content,
        {
            "user_prompt": user_prompt,
            "branch_options_json": json.dumps(branch_options, ensure_ascii=False, indent=2),
        },
    )
    result = await _run_llm(ctx, node, step, expects_text=True, override_prompt=prompt)
    if result.status != "success":
        return result
    answer = _strip_text(str(result.output or ""))
    chosen = _match_branch(answer, valid_keys)
    if chosen is None:
        return NodeResult(
            status="failed",
            error=f"condition 未返回合法 branch key：{answer or '空输出'}",
            failure_kind="routing",
            agent_session_id=result.agent_session_id,
        )
    _apply_condition_branch(ctx, node, chosen)
    await _record_condition_result(
        ctx,
        node,
        step,
        chosen=chosen,
        raw_answer=answer,
        forced=False,
    )
    return NodeResult(status="success", output=chosen, agent_session_id=result.agent_session_id)


# --- LLM 调用 ---------------------------------------------------------------


async def _run_llm(
    ctx: ExecutionContext,
    node: dict[str, Any],
    step: Step,
    *,
    expects_text: bool,
    override_prompt: str | None = None,
) -> NodeResult:
    runtime = get_runtime()
    cwd = ctx.workspace
    try:
        input_refs = _runtime_upload_refs_for_node(ctx, node, step)
    except WorkflowDataIntegrityError as exc:
        return NodeResult(
            status="failed",
            error=f"输入 artifact 完整性校验失败：{exc}",
            failure_kind="integrity",
        )
    with runtime_upload_context(cwd, input_refs):
        return await _run_llm_with_upload_context(
            ctx,
            node,
            step,
            expects_text=expects_text,
            override_prompt=override_prompt,
            runtime=runtime,
            cwd=cwd,
        )


async def _run_llm_with_upload_context(
    ctx: ExecutionContext,
    node: dict[str, Any],
    step: Step,
    *,
    expects_text: bool,
    override_prompt: str | None,
    runtime,
    cwd: Path,
) -> NodeResult:
    task_prompt = override_prompt if override_prompt is not None else await _compose_prompt(ctx, node)
    prompt = task_prompt
    if override_prompt is None:
        prompt = _append_prompt(prompt, contract_prompt_suffix(node))
    output_schema = schema_for_contract(node) if override_prompt is None else None
    prompt = rewrite_runtime_upload_paths(prompt)
    model = str(node.get("model") or "").strip() or None
    reasoning_effort = normalize_reasoning_effort(node.get("reasoning_effort"))

    decision_summary: str | NodeResult = ""
    if _should_run_decision_plan(node):
        decision_summary = await _run_decision_plan(
            ctx,
            node,
            step,
            runtime=runtime,
            prompt=rewrite_runtime_upload_paths(task_prompt),
            model=model,
            reasoning_effort=reasoning_effort,
            cwd=cwd,
        )
    if isinstance(decision_summary, NodeResult):
        return decision_summary
    input_payload = loads(step.input_json, {}) or {}
    if not isinstance(input_payload, dict):
        input_payload = {}
    if decision_summary:
        prompt = _append_prompt(
            prompt,
            "\n".join(
                [
                    "# 用户决策摘要",
                    decision_summary,
                    "# 执行要求",
                    "请基于上述用户决策完成当前节点；不要再次向用户提问。",
                ]
            ),
        )
    input_payload["prompt"] = prompt
    step.input_json = dumps(input_payload)
    chunks: list[str] = []

    async def on_chunk(chunk: AgentChunk) -> None:
        session_from_chunk = _extract_session_id(chunk.raw) if isinstance(chunk.raw, dict) else None
        if session_from_chunk and session_from_chunk != ctx.agent_session_id:
            await _persist_session_id(ctx, step, session_from_chunk)
        if chunk.type == "session":
            return
        if chunk.type == "text" and chunk.text:
            chunks.append(chunk.text)
        # 把 chunk 转发给前端：用 model_dump 让 SSE 中的 chunk 形态与前端 AgentChunk 类型一致。
        await ctx.channel.publish(
            "step.delta",
            {"node_id": node["id"], "chunk": chunk.model_dump(exclude_none=True)},
        )

    try:
        result = await runtime.execute(
            prompt=prompt,
            session_id=ctx.agent_session_id,
            # App Tools 是 run 级能力；旧节点工具字段不能限制它们。
            model=model,
            reasoning_effort=reasoning_effort,
            cwd=cwd,
            on_chunk=on_chunk,
            cancel_event=ctx.channel.cancel_event,
            on_ask_user=None,
            runtime_tools=ctx.runtime_tools,
            runtime_policy="execute",
            output_schema=output_schema,
            session_scope=f"run:{ctx.run_id}",
            fork_session=ctx.fork_session,
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("runtime crashed for node=%s", node.get("id"))
        return NodeResult(
            status="failed",
            error=f"Agent 执行异常: {exc}",
            failure_kind="runtime",
            agent_session_id=ctx.agent_session_id,
        )

    next_session_id = result.session_id or ctx.agent_session_id
    if next_session_id:
        ctx.agent_session_id = next_session_id
    ctx.fork_session = False

    if result.finished_with == "cancelled":
        return NodeResult(status="cancelled", agent_session_id=next_session_id)
    if result.finished_with == "error":
        return NodeResult(
            status="failed",
            error=result.error or "Agent 执行失败",
            failure_kind="runtime",
            agent_session_id=next_session_id,
        )
    text = result.total_text or "".join(chunks)
    if expects_text and node.get("type") == "output":
        validated = await _validate_node_output(ctx, node, text)
        if ctx.channel.cancel_event.is_set():
            return NodeResult(status="cancelled", agent_session_id=next_session_id)
        if not validated.ok:
            if not validated.repairable:
                await _append_log(ctx, step, "error", f"输出契约校验不可用：{validated.error}")
                return NodeResult(
                    status="failed",
                    error=f"输出契约校验不可用：{validated.error or '校验器不可用'}",
                    failure_kind="contract",
                    agent_session_id=next_session_id,
                )
            await _append_log(ctx, step, "warn", f"输出契约校验失败，尝试自动修正：{validated.error}")
            return await _repair_contract_output(
                ctx,
                node,
                step,
                runtime=runtime,
                original_output=text,
                task_context=prompt,
                validation_error=validated.error or "输出不符合契约",
                model=model,
                reasoning_effort=reasoning_effort,
                cwd=cwd,
                on_chunk=on_chunk,
                output_schema=output_schema,
            )
        output = validated.output
    elif expects_text and node.get("type") == "generate":
        validated = await _validate_node_output(ctx, node, text)
        if ctx.channel.cancel_event.is_set():
            return NodeResult(status="cancelled", agent_session_id=next_session_id)
        if not validated.ok:
            if not validated.repairable:
                await _append_log(ctx, step, "error", f"输出契约校验不可用：{validated.error}")
                return NodeResult(
                    status="failed",
                    error=f"输出契约校验不可用：{validated.error or '校验器不可用'}",
                    failure_kind="contract",
                    agent_session_id=next_session_id,
                )
            await _append_log(ctx, step, "warn", f"输出契约校验失败，尝试自动修正：{validated.error}")
            repair_result = await _repair_contract_output(
                ctx,
                node,
                step,
                runtime=runtime,
                original_output=text,
                task_context=prompt,
                validation_error=validated.error or "输出不符合契约",
                model=model,
                reasoning_effort=reasoning_effort,
                cwd=cwd,
                on_chunk=on_chunk,
                output_schema=output_schema,
            )
            if repair_result.status != "success":
                return repair_result
            return repair_result
        output = validated.output
    else:
        output = text if expects_text else text
    await _append_log(ctx, step, "info", "节点执行完成")
    return NodeResult(status="success", output=output, agent_session_id=next_session_id)


async def _run_decision_plan(
    ctx: ExecutionContext,
    node: dict[str, Any],
    step: Step,
    *,
    runtime,
    prompt: str,
    model: str | None,
    reasoning_effort: str | None,
    cwd: Path,
) -> str | NodeResult:
    plan_prompt = _build_decision_plan_prompt(prompt)
    chunks: list[str] = []

    async def on_plan_chunk(chunk: AgentChunk) -> None:
        session_from_chunk = _extract_session_id(chunk.raw) if isinstance(chunk.raw, dict) else None
        if session_from_chunk and session_from_chunk != ctx.agent_session_id:
            await _persist_session_id(ctx, step, session_from_chunk)
        if chunk.type == "text" and chunk.text:
            chunks.append(chunk.text)

    async def on_ask_user(request: AskUserRequest) -> AskUserResult:
        protocol_error = _validate_ask_request(request)
        if protocol_error:
            return AskUserResult(ok=False, error=protocol_error)
        request = request.model_copy(update={"groups": append_ask_user_none_option(request.groups)})
        async with ctx.channel.waiting_lock:
            if ctx.channel.cancel_event.is_set():
                return AskUserResult(ok=False, error="运行已取消")
            future = ctx.channel.begin_waiting(node["id"], request.tool_use_id)
            try:
                await _persist_live_ask_user(ctx, step, request)
                answer = await future
                await _persist_live_ask_user_answer(ctx, step, request, answer)
                ctx.channel.acknowledge_resume(node["id"], request.tool_use_id)
                return answer
            finally:
                ctx.channel.clear_waiting(node["id"], request.tool_use_id)

    try:
        result = await runtime.execute(
            prompt=plan_prompt,
            session_id=ctx.agent_session_id,
            model=model,
            reasoning_effort=reasoning_effort,
            cwd=cwd,
            on_chunk=on_plan_chunk,
            cancel_event=ctx.channel.cancel_event,
            on_ask_user=on_ask_user,
            runtime_tools=ctx.planning_runtime_tools,
            runtime_policy="ask_user_plan",
            output_schema=_ASK_USER_PLAN_OUTPUT_SCHEMA,
            session_scope=f"run:{ctx.run_id}",
            fork_session=ctx.fork_session,
        )
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001
        logger.exception("runtime ask_user plan crashed for node=%s", node.get("id"))
        return NodeResult(
            status="failed",
            error=f"Agent 提问规划异常: {exc}",
            failure_kind="runtime",
            agent_session_id=ctx.agent_session_id,
        )

    next_session_id = result.session_id or ctx.agent_session_id
    if next_session_id:
        ctx.agent_session_id = next_session_id
        step.agent_session_id = next_session_id
        await ctx.db.commit()
    ctx.fork_session = False
    if result.finished_with == "cancelled":
        return NodeResult(status="cancelled", agent_session_id=ctx.agent_session_id)
    if result.finished_with == "error":
        return NodeResult(
            status="failed",
            error=result.error or "Agent 提问规划失败",
            failure_kind="runtime",
            agent_session_id=ctx.agent_session_id,
        )
    payload = _json_object_from_text(result.total_text or "".join(chunks))
    summary = str(payload.get("decision_summary") or "").strip() if payload else ""
    if not summary:
        return NodeResult(
            status="failed",
            error="Agent 提问规划未返回决策摘要",
            failure_kind="contract",
            agent_session_id=ctx.agent_session_id,
        )
    input_payload = loads(step.input_json, {}) or {}
    if not isinstance(input_payload, dict):
        input_payload = {}
    input_payload["ask_user_plan"] = {
        "summary": summary,
        "reason": str(payload.get("reason") or "").strip(),
    }
    input_payload.pop("ask_user", None)
    step.input_json = dumps(input_payload)
    await ctx.db.commit()
    return summary


def _should_run_decision_plan(node: dict[str, Any]) -> bool:
    if node.get("type") == "output":
        return False
    if node.get("type") == "generate" and node.get("ask_user_enabled") is False:
        return False
    return True


def _build_decision_plan_prompt(prompt: str) -> str:
    return "\n\n".join(
        [
            prompt,
            "# 执行前规划",
            "只判断当前任务开始前是否缺少会显著改变结果的用户决策，不要执行任务或创建文件。",
            "如果缺少关键偏好、约束、目标或交付形式，请在规划阶段向用户提问；信息足够后继续规划。",
            "最后仅返回决策摘要和判断理由。",
        ]
    )


def _json_object_from_text(text: str) -> dict[str, Any] | None:
    raw = text.strip()
    if not raw:
        return None
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw, flags=re.IGNORECASE | re.MULTILINE).strip()
    try:
        payload = loads(cleaned, {})
    except Exception:  # noqa: BLE001
        payload = None
    if isinstance(payload, dict):
        return payload

    decoder = json.JSONDecoder()
    for index, char in enumerate(cleaned):
        if char != "{":
            continue
        try:
            payload, _end = decoder.raw_decode(cleaned[index:])
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            return payload
    return None


async def _persist_live_ask_user(
    ctx: ExecutionContext,
    step: Step,
    request: AskUserRequest,
) -> None:
    input_payload = loads(step.input_json, {}) or {}
    if not isinstance(input_payload, dict):
        input_payload = {}
    input_payload["ask_user"] = request.model_dump(exclude_none=True)
    step.input_json = dumps(input_payload)
    step.status = "waiting_for_user"
    run = await ctx.db.get(Run, ctx.run_id)
    if run is not None:
        run.status = "waiting_for_user"
        run.resume_from_node_id = step.node_id
    await ctx.db.commit()
    label = request.groups[0].label if request.groups else "需要补充输入"
    if len(request.groups) > 1:
        label = f"{label} 等 {len(request.groups)} 个问题"
    await _append_log(ctx, step, "info", f"向用户提问：{label}")
    await ctx.channel.publish(
        "step.waiting",
        {"node_id": step.node_id, "request": request.model_dump(exclude_none=True)},
    )
    await ctx.channel.publish("run.waiting_for_user", {"node_id": step.node_id})


async def _persist_session_id(ctx: ExecutionContext, step: Step, session_id: str) -> None:
    ctx.agent_session_id = session_id
    step.agent_session_id = session_id
    if step.branch_id:
        branch = await ctx.db.get(RunAgentBranch, step.branch_id)
        if branch is not None:
            branch.provider_session_id = session_id
            branch.fork_from_session_id = None
    await ctx.db.commit()


async def _persist_live_ask_user_answer(
    ctx: ExecutionContext,
    step: Step,
    request: AskUserRequest,
    result: AskUserResult,
) -> None:
    input_payload = loads(step.input_json, {}) or {}
    if not isinstance(input_payload, dict):
        input_payload = {}
    history = input_payload.get("ask_user_history")
    if not isinstance(history, list):
        history = []
    history.append(
        {
            "request": request.model_dump(exclude_none=True),
            "response": result.model_dump(exclude_none=True),
        }
    )
    input_payload["ask_user_history"] = history
    input_payload["resume"] = result.model_dump(exclude_none=True)
    input_payload.pop("ask_user", None)
    step.input_json = dumps(input_payload)
    step.status = "running"
    run = await ctx.db.get(Run, ctx.run_id)
    if run is not None and run.status == "waiting_for_user":
        run.status = "running"
        run.resume_from_node_id = None
    await ctx.db.commit()
    await ctx.channel.publish("run.resumed", {"node_id": step.node_id})


async def _repair_contract_output(
    ctx: ExecutionContext,
    node: dict[str, Any],
    step: Step,
    *,
    runtime,
    original_output: str,
    task_context: str,
    validation_error: str,
    model: str | None,
    reasoning_effort: str | None,
    cwd: Path,
    on_chunk,
    output_schema: dict[str, Any] | None,
) -> NodeResult:
    unicode_repair = (
        contains_unicode_replacement(original_output)
        or contains_unicode_replacement(validation_error)
        or bool(_UNICODE_REPLACEMENT_ESCAPE_RE.search(original_output))
        or bool(_UNICODE_REPLACEMENT_ESCAPE_RE.search(task_context))
    )
    repair_original_output = original_output
    repair_task_context = task_context
    repair_validation_error = validation_error
    if unicode_repair:
        repair_original_output = _mark_unicode_damage(original_output)
        repair_task_context = _mark_unicode_damage(task_context)
        repair_validation_error = validation_error.replace("\ufffd", "U+FFFD")
    template = await get_prompt_content(ctx.db, "output_contract_repair")
    repair_prompt = render_prompt(
        template,
        {
            "contract": contract_repair_description(node),
            "validation_error": repair_validation_error,
            "original_output": repair_original_output,
            "task_context": repair_task_context,
        },
    )
    if unicode_repair:
        repair_prompt = _append_prompt(
            repair_prompt,
            "\n".join(
                [
                    "## 损坏字符修复要求",
                    f"- {_UNICODE_REPAIR_MARKER} 表示原文本在该处已经损坏，不是需要保留的事实。",
                    "- 根据任务上下文和前后文重写包含该标记的完整字段值或句子，恢复完整语义。",
                    "- 最终输出不得包含该标记或 U+FFFD，也不得只删除标记后保留残缺词语。",
                ]
            ),
        )
    repair_prompt = rewrite_runtime_upload_paths(repair_prompt)
    repair_chunks: list[str] = []

    async def on_repair_chunk(chunk: AgentChunk) -> None:
        if chunk.type == "text" and chunk.text:
            repair_chunks.append(chunk.text)
        await on_chunk(chunk)

    repair_session_id = ctx.agent_session_id
    try:
        result = await runtime.execute(
            prompt=repair_prompt,
            session_id=repair_session_id,
            model=model,
            reasoning_effort=reasoning_effort,
            cwd=cwd,
            on_chunk=on_repair_chunk,
            cancel_event=ctx.channel.cancel_event,
            on_ask_user=None,
            runtime_tools=ctx.runtime_tools,
            runtime_policy="execute",
            output_schema=output_schema,
            session_scope=f"run:{ctx.run_id}",
            fork_session=ctx.fork_session,
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("runtime repair crashed for node=%s", node.get("id"))
        return NodeResult(
            status="failed",
            error=f"Agent 修正输出异常: {exc}",
            failure_kind="runtime",
            agent_session_id=ctx.agent_session_id,
        )

    next_session_id = result.session_id or ctx.agent_session_id
    if next_session_id:
        ctx.agent_session_id = next_session_id
    ctx.fork_session = False
    if result.finished_with == "cancelled":
        return NodeResult(status="cancelled", agent_session_id=next_session_id)
    if result.finished_with == "error":
        return NodeResult(
            status="failed",
            error=result.error or "Agent 修正输出失败",
            failure_kind="runtime",
            agent_session_id=next_session_id,
        )
    repaired_text = result.total_text or "".join(repair_chunks)
    validated = await _validate_node_output(ctx, node, repaired_text)
    if ctx.channel.cancel_event.is_set():
        return NodeResult(status="cancelled", agent_session_id=next_session_id)
    if not validated.ok:
        error = (
            f"输出契约校验不可用：{validated.error or '校验器不可用'}"
            if not validated.repairable
            else f"输出契约校验失败：{_contract_failure_message(node, validated.error or '输出无效')}"
        )
        return NodeResult(
            status="failed",
            error=error,
            failure_kind="contract",
            agent_session_id=next_session_id,
        )
    await _append_log(ctx, step, "info", "输出契约自动修正完成")
    return NodeResult(status="success", output=validated.output, agent_session_id=next_session_id)


def _mark_unicode_damage(value: str) -> str:
    marked = value.replace("\ufffd", _UNICODE_REPAIR_MARKER)
    return _UNICODE_REPLACEMENT_ESCAPE_RE.sub(_UNICODE_REPAIR_MARKER, marked)


async def _validate_node_output(
    ctx: ExecutionContext,
    node: dict[str, Any],
    text: str,
) -> ContractValidationResult:
    def validate() -> ContractValidationResult:
        return validate_contract_output(
            node,
            text,
            workspace=ctx.workspace,
            cancelled=ctx.channel.cancel_event.is_set,
        )

    contract = node.get("output_contract")
    office_validation = (
        isinstance(contract, dict)
        and contract.get("type") == "artifact"
        and contract.get("validate_office_documents") is True
    )
    if office_validation:
        acquired = False
        while not ctx.channel.cancel_event.is_set():
            try:
                await asyncio.wait_for(_OFFICE_VALIDATION_SEMAPHORE.acquire(), timeout=0.25)
                acquired = True
                break
            except TimeoutError:
                continue
        if not acquired:
            return ContractValidationResult(ok=False, error="Office 文档深检已取消")
        try:
            return await asyncio.to_thread(validate)
        finally:
            _OFFICE_VALIDATION_SEMAPHORE.release()
    return await asyncio.to_thread(validate)


def _contract_failure_message(node: dict[str, Any], validation_error: str) -> str:
    if node.get("type") == "output":
        return f"最终展示节点必须返回可渲染 HTML。请调整该节点提示词，确保最终结果能直接预览。技术细节：{validation_error}"
    contract = node.get("output_contract")
    output_type = contract.get("type") if isinstance(contract, dict) else None
    if output_type == "json":
        return f"节点要求结构化 JSON，但模型返回内容不符合字段要求。请调整提示词、放宽字段，或改为普通文本。技术细节：{validation_error}"
    if output_type == "artifact":
        return f"节点要求生成可下载文件，但文件产物未通过校验。请确认提示词要求写入当前工作目录，并最终返回文件路径。技术细节：{validation_error}"
    if output_type == "html":
        return f"节点要求 HTML 片段，但模型返回内容不可作为 HTML 保存。请调整提示词，或改为普通文本。技术细节：{validation_error}"
    return f"节点输出无效。请调整提示词后重试。技术细节：{validation_error}"


# --- ask_user 中段交互 -------------------------------------------------------


def _validate_ask_request(request: AskUserRequest) -> str | None:
    """spec §1.2 的校验。返回非 None 即代表协议错误。"""

    return validate_ask_request_groups(request.groups)


async def _compose_prompt(ctx: ExecutionContext, node: dict[str, Any]) -> str:
    prompt = await asyncio.to_thread(_compose_node_prompt, ctx, node)
    if node.get("type") == "output":
        template_content = await get_prompt_content(ctx.db, "output_html_rendering")
        return render_prompt(template_content, {"user_prompt": prompt})
    return prompt


def _extract_session_id(data: dict | None) -> str | None:
    if not isinstance(data, dict):
        return None
    for key in ("session_id", "sessionId", "thread_id", "threadId", "conversation_id"):
        value = data.get(key)
        if isinstance(value, str) and value:
            return value
    for value in data.values():
        if isinstance(value, dict):
            found = _extract_session_id(value)
            if found:
                return found
    return None


def _compose_node_prompt(ctx: ExecutionContext, node: dict[str, Any]) -> str:
    base = str(node.get("prompt") or "").strip()
    return "\n\n".join(
        [
            workflow_data_prompt(),
            "# 当前任务\n" + base,
        ]
    )


def _persist_workspace_context(ctx: ExecutionContext, node: dict[str, Any], value: Any) -> None:
    node_id = str(node.get("id") or "node")
    context_dir = ctx.workspace / ".mira" / "run-context"
    context_dir.mkdir(parents=True, exist_ok=True)
    stored_value = _copy_context_files(ctx.workspace, value)
    context_path = context_dir / f"{_safe_workspace_name(node_id)}.json"
    context_path.write_text(
        json.dumps(
            {
                "node_id": node_id,
                "node_type": node.get("type"),
                "title": node.get("title") or node_id,
                "value": stored_value,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


def _copy_context_files(workspace: Path, value: Any) -> Any:
    if isinstance(value, list):
        return [_copy_context_files(workspace, item) for item in value]
    if not isinstance(value, dict):
        return value
    copied = {key: _copy_context_files(workspace, item) for key, item in value.items() if key != "path"}
    source_text = value.get("path")
    upload_id = value.get("id")
    if isinstance(source_text, str) and isinstance(upload_id, str):
        source = Path(source_text)
        if source.is_file():
            name = _safe_workspace_name(str(value.get("name") or source.name))
            relative = Path("inputs") / _safe_workspace_name(upload_id) / name
            target = workspace / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
            copied["workspace_path"] = f"/workspace/{relative.as_posix()}"
    return copied


def _safe_workspace_name(value: str) -> str:
    safe = "".join(char if char.isalnum() or char in {"_", "-", "."} else "_" for char in value).strip(".")
    return safe or "item"


def _append_prompt(prompt: str, suffix: str) -> str:
    parts = [part.strip() for part in (prompt, suffix) if part and part.strip()]
    return "\n\n".join(parts)


def _runtime_upload_refs_for_node(ctx: ExecutionContext, node: dict[str, Any], step: Step) -> list[RuntimeUploadRef]:
    refs: dict[str, RuntimeUploadRef] = {}
    _collect_resume_upload_refs(ctx, step, refs)
    return list(refs.values())


def _collect_resume_upload_refs(ctx: ExecutionContext, step: Step, refs: dict[str, RuntimeUploadRef]) -> None:
    payload = loads(step.input_json, {}) or {}
    if not isinstance(payload, dict):
        return
    resume = payload.get("resume")
    if isinstance(resume, dict):
        _collect_upload_refs_from_value(ctx.user_id, resume.get("attachments"), refs)


def _collect_upload_refs_from_value(owner_id: str, value: Any, refs: dict[str, RuntimeUploadRef]) -> None:
    if isinstance(value, dict):
        upload_id = value.get("id")
        if isinstance(upload_id, str):
            resolved = resolve_upload(owner_id, upload_id)
            if resolved is not None:
                refs[resolved.id] = RuntimeUploadRef(id=resolved.id, path=resolved.path)
        for child in value.values():
            _collect_upload_refs_from_value(owner_id, child, refs)
        return
    if isinstance(value, list):
        for item in value:
            _collect_upload_refs_from_value(owner_id, item, refs)


# --- condition 分支选择 -----------------------------------------------------


DEFAULT_BRANCH_KEY = "__default__"


def _strip_text(text: str) -> str:
    cleaned = text.strip()
    # LLM 可能用 markdown / 引号包裹；尝试去掉常见噪音。
    for wrapper in ('"', "'", "`"):
        if cleaned.startswith(wrapper) and cleaned.endswith(wrapper) and len(cleaned) >= 2:
            cleaned = cleaned[1:-1].strip()
    if "\n" in cleaned:
        cleaned = cleaned.splitlines()[0].strip()
    return cleaned


def _match_branch(answer: str, valid_keys: list[str]) -> str | None:
    return answer if answer in valid_keys else None


def _condition_branch_override(ctx: ExecutionContext, node: dict[str, Any]) -> str | None:
    overrides = ctx.graph.get("run_overrides")
    if not isinstance(overrides, dict):
        return None
    condition_branch = overrides.get("condition_branch")
    if not isinstance(condition_branch, dict):
        return None
    if condition_branch.get("node_id") != node.get("id"):
        return None
    branch_key = condition_branch.get("branch_key")
    return branch_key if isinstance(branch_key, str) and branch_key else None


def _apply_condition_branch(ctx: ExecutionContext, node: dict[str, Any], chosen: str) -> None:
    # 把未中分支的下游节点全部标记成 skipped，由 orchestrator 在调度时跳过。
    chosen_targets = _branch_target_nodes(ctx, node["id"], chosen)
    all_targets = _branch_target_nodes(ctx, node["id"], None)
    chosen_reachable = _reachable_from_targets(ctx, chosen_targets)
    unchosen_reachable = _reachable_from_targets(ctx, all_targets - chosen_targets)
    ctx.skipped_nodes.update(unchosen_reachable - chosen_reachable)


async def _record_condition_result(
    ctx: ExecutionContext,
    node: dict[str, Any],
    step: Step,
    *,
    chosen: str,
    raw_answer: str | None,
    forced: bool,
) -> None:
    all_branches = _condition_branch_keys_for_result(ctx, node)
    unchosen = [branch for branch in all_branches if branch != chosen]
    if forced:
        reason = "用户在分支测试中强制选择该分支"
    elif chosen == DEFAULT_BRANCH_KEY:
        reason = f"模型输出 {raw_answer or '空'}，未匹配已有分支，使用默认分支"
    else:
        reason = f"模型输出 {raw_answer or '空'}，匹配到分支 {chosen}"

    payload = loads(step.input_json, {}) or {}
    if not isinstance(payload, dict):
        payload = {}
    payload["condition_result"] = {
        "chosen_branch": chosen,
        "unchosen_branches": unchosen,
        "reason": reason,
        "raw_answer": raw_answer,
        "forced": forced,
    }
    step.input_json = dumps(payload)

    await _append_log(ctx, step, "info", f"选择分支：{chosen}")
    if unchosen:
        await _append_log(ctx, step, "info", f"未走分支：{', '.join(unchosen)}")
    await _append_log(ctx, step, "info", f"判断理由：{reason}")


def _condition_branch_keys_for_result(ctx: ExecutionContext, node: dict[str, Any]) -> list[str]:
    keys: list[str] = []
    branches = node.get("branches")
    if isinstance(branches, list):
        for branch in branches:
            key = branch.get("key") if isinstance(branch, dict) else None
            if isinstance(key, str) and key and key not in keys:
                keys.append(key)
    if node.get("mode") == "cases" and _branch_target_nodes(ctx, node["id"], DEFAULT_BRANCH_KEY):
        keys.append(DEFAULT_BRANCH_KEY)
    return keys


def _branch_target_nodes(ctx: ExecutionContext, condition_id: str, branch_key: str | None) -> set[str]:
    """branch_key=None 时返回所有 condition 出边的 target；
    其它时候返回 branch_key 等于 branch_key（或 cases 模式下命中 __default__）的 target。
    """

    targets: set[str] = set()
    for edge in ctx.graph.get("execution_edges", []):
        if not isinstance(edge, dict) or edge.get("source") != condition_id:
            continue
        handle = edge.get("branch_key")
        target = edge.get("target")
        if not isinstance(target, str):
            continue
        if branch_key is None:
            targets.add(target)
        elif handle == branch_key:
            targets.add(target)
        elif branch_key == DEFAULT_BRANCH_KEY and handle == DEFAULT_BRANCH_KEY:
            targets.add(target)
    return targets


def _reachable_from_targets(ctx: ExecutionContext, targets: set[str]) -> set[str]:
    return set(ctx.execution_plan.descendant_ids(targets))


# --- log 持久化 -------------------------------------------------------------


async def _append_log(ctx: ExecutionContext, step: Step, level: str, text: str) -> None:
    log = StepLog(
        id=new_id("log"),
        step_id=step.id,
        ts=now_utc(),
        level=level,
        text=text,
    )
    ctx.db.add(log)
    await ctx.db.commit()
    await ctx.channel.publish(
        "step.log",
        {"node_id": step.node_id, "log": log_to_out(log).model_dump(mode="json")},
    )
