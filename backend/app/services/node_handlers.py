"""单节点 handler：把 graph 中各种节点类型映射到具体的执行动作。

执行模型：
- 每次 run 拿到一个 ``ExecutionContext``，包含 cancel_event、上游节点输出表、
  hub 通道、user_id / app_id / inputs。
- handler 返回 ``NodeResult``：success+output、failed+error 或 cancelled。
- generate / output / condition 的 LLM 调用统一走 ``app.runtime.factory.get_runtime``。

``_run_llm`` 先用受限 planning 状态机处理 ``ask_user``，再执行节点主体。
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Run, Step, StepLog
from app.runtime.base import AgentChunk, AskUserRequest
from app.runtime.factory import get_runtime
from app.schemas import RunInputValue
from app.services.decision_prompts import append_ask_user_none_option, validate_ask_request_groups
from app.services.artifacts import (
    collect_workspace_image_refs,
    ensure_html_images,
    fill_image_download_urls,
    import_runtime_images,
    replace_workspace_paths_for_prompt,
    replace_workspace_paths_in_html,
)
from app.services.output_contracts import (
    ContractValidationResult,
    contract_prompt_suffix,
    contract_repair_description,
    schema_for_contract,
    validate_contract_output,
)
from app.services.prompts import get_prompt_content, render_prompt
from app.services.reasoning_effort import normalize_reasoning_effort_for_agent
from app.services.run_hub import RunChannel
from app.services.runs import attachments_meta
from app.services.runtime_uploads import RuntimeUploadRef, rewrite_runtime_upload_paths, runtime_upload_context
from app.services.tools import RuntimeToolConfig
from app.services.text_integrity import UNICODE_REPLACEMENT_ERROR, contains_unicode_replacement
from app.services.uploads import resolve_upload
from app.services.run_serializer import log_to_out
from app.utils import dumps, loads, new_id, now_utc

logger = logging.getLogger(__name__)
_TEST_ASK_USER_RE = re.compile(r"\[\[ask_user:\{.*?\}\]\]", re.DOTALL)
_ASK_USER_PREFLIGHT_MAX_ATTEMPTS = 2
_OFFICE_VALIDATION_CONCURRENCY = 2
_OFFICE_VALIDATION_SEMAPHORE = asyncio.Semaphore(_OFFICE_VALIDATION_CONCURRENCY)
_UNICODE_REPAIR_MARKER = "[[MIRA_CORRUPTED_TEXT]]"
_UNICODE_REPLACEMENT_ESCAPE_RE = re.compile(r"\\u[fF]{3}[dD]")
_ASK_USER_PREFLIGHT_OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "action": {"type": "string", "enum": ["ask", "complete"]},
        "rationale": {"type": ["string", "null"]},
        "request": {
            "type": ["object", "null"],
            "additionalProperties": False,
            "properties": {
                "context": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "title": {"type": "string", "minLength": 1, "maxLength": 80},
                        "summary": {"type": "string", "minLength": 1, "maxLength": 240},
                    },
                    "required": ["title", "summary"],
                },
                "groups": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": 3,
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "properties": {
                            "id": {"type": "string", "minLength": 1},
                            "label": {"type": "string", "minLength": 1},
                            "type": {"type": "string", "enum": ["single", "multi"]},
                            "options": {
                                "type": "array",
                                "minItems": 2,
                                "maxItems": 3,
                                "items": {
                                    "type": "object",
                                    "additionalProperties": False,
                                    "properties": {
                                        "label": {"type": "string", "minLength": 1},
                                        "description": {"type": "string", "minLength": 1},
                                        "recommended": {"type": "boolean"},
                                    },
                                    "required": ["label", "description", "recommended"],
                                },
                            },
                            "placeholder": {"type": ["string", "null"]},
                        },
                        "required": ["id", "label", "type", "options", "placeholder"],
                    },
                },
            },
            "required": ["context", "groups"],
        },
        "decision_summary": {"type": ["string", "null"]},
        "reason": {"type": ["string", "null"]},
    },
    "required": ["action", "rationale", "request", "decision_summary", "reason"],
}


# --- 数据结构 ----------------------------------------------------------------


@dataclass
class NodeResult:
    status: str  # "success" / "failed" / "skipped" / "waiting" / "cancelled"
    output: Any = None
    error: str | None = None
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
    agent: str
    workspace: Path
    # 用户启动时收集的 user_input 节点输入：{node_id: RunInputValue}。
    inputs: dict[str, RunInputValue]
    runtime_tools: RuntimeToolConfig | None = None
    planning_runtime_tools: RuntimeToolConfig | None = None
    agent_session_id: str | None = None
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
    agent: str,
    workspace: Path,
    inputs: dict[str, RunInputValue],
    runtime_tools: RuntimeToolConfig | None = None,
    planning_runtime_tools: RuntimeToolConfig | None = None,
) -> ExecutionContext:
    return ExecutionContext(
        db=db,
        channel=channel,
        user_id=user_id,
        asset_owner_id=asset_owner_id or user_id,
        app_id=app_id,
        run_id=run_id,
        graph=graph,
        agent=agent,
        workspace=workspace,
        inputs=inputs,
        runtime_tools=runtime_tools,
        planning_runtime_tools=planning_runtime_tools,
        nodes_by_id={
            node["id"]: node for node in graph.get("nodes", []) if isinstance(node.get("id"), str)
        },
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
    return NodeResult(status="failed", error=f"未知节点类型: {node_type}")


# --- 具体 handler -----------------------------------------------------------


async def _handle_user_input(ctx: ExecutionContext, node: dict[str, Any], step: Step) -> NodeResult:
    node_id = node["id"]
    raw = ctx.inputs.get(node_id, RunInputValue())
    metas = attachments_meta(ctx.user_id, raw)
    payload: dict[str, Any] = {"value": raw.value}
    if metas:
        payload["attachments"] = metas
    step.input_json = dumps(payload)
    await _append_log(ctx, step, "info", "读取用户输入")
    return NodeResult(status="success", output=payload)


async def _handle_asset(ctx: ExecutionContext, node: dict[str, Any], step: Step) -> NodeResult:
    asset_kind = str(node.get("asset_kind") or "text")
    if asset_kind == "file":
        uploads = node.get("uploads")
        if not isinstance(uploads, list) or not uploads:
            return NodeResult(status="failed", error="asset 节点 uploads 缺失")
        payloads: list[dict[str, Any]] = []
        for upload in uploads:
            upload_id = upload.get("id") if isinstance(upload, dict) else None
            if not isinstance(upload_id, str) or not upload_id.strip():
                return NodeResult(status="failed", error="asset 节点 upload 缺失")
            resolved = resolve_upload(ctx.asset_owner_id, upload_id)
            if resolved is None:
                return NodeResult(status="failed", error="asset 节点上传文件不存在")
            payloads.append(resolved.to_tool_payload(ctx.asset_owner_id))
        step.input_json = dumps({"asset_kind": asset_kind, "uploads": payloads})
        await _append_log(ctx, step, "info", "读取上传素材")
        return NodeResult(status="success", output=payloads)

    if asset_kind == "drawing":
        upload = node.get("upload")
        upload_id = upload.get("id") if isinstance(upload, dict) else None
        if not isinstance(upload_id, str) or not upload_id.strip():
            return NodeResult(status="failed", error="asset 节点 upload 缺失")
        resolved = resolve_upload(ctx.asset_owner_id, upload_id)
        if resolved is None:
            return NodeResult(status="failed", error="asset 节点上传文件不存在")
        payload = resolved.to_tool_payload(ctx.asset_owner_id)
        step.input_json = dumps({"asset_kind": asset_kind, "upload": payload})
        await _append_log(ctx, step, "info", "读取上传素材")
        return NodeResult(status="success", output=payload)

    if asset_kind == "url":
        urls = node.get("urls")
        if not isinstance(urls, list):
            return NodeResult(status="failed", error="asset 节点 urls 缺失")
        payload = [url.strip() for url in urls if isinstance(url, str) and url.strip()]
        step.input_json = dumps({"asset_kind": asset_kind, "urls": payload})
        await _append_log(ctx, step, "info", "读取素材链接")
        return NodeResult(status="success", output=payload)

    content = node.get("content")
    if not isinstance(content, str):
        return NodeResult(status="failed", error="asset 节点 content 缺失")
    step.input_json = dumps({"asset_kind": asset_kind, "content": content})
    await _append_log(ctx, step, "info", "读取素材内容")
    return NodeResult(status="success", output=content)


async def _handle_generate(ctx: ExecutionContext, node: dict[str, Any], step: Step) -> NodeResult:
    return await _run_llm(ctx, node, step, expects_text=True)


async def _handle_output(ctx: ExecutionContext, node: dict[str, Any], step: Step) -> NodeResult:
    result = await _run_llm(ctx, node, step, expects_text=True)
    if result.status == "success" and isinstance(result.output, str):
        result.output = await asyncio.to_thread(_finalize_output_html, ctx, result.output)
    return result


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
        return NodeResult(status="failed", error="condition 节点 branches 数量不足")
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
    user_prompt = await _compose_prompt(ctx, node, include_ask_user_protocol=False)
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
    answer = _strip_text(str(result.output or "")).lower()
    chosen = _match_branch(answer, valid_keys, node.get("mode") == "binary")
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
    agent_kind = ctx.agent.strip()
    if not agent_kind:
        return NodeResult(status="failed", error="应用未配置 Agent")
    runtime = get_runtime(agent_kind, ctx.user_id)
    cwd = ctx.workspace
    with runtime_upload_context(cwd, _runtime_upload_refs_for_node(ctx, node, step)):
        return await _run_llm_with_upload_context(
            ctx,
            node,
            step,
            expects_text=expects_text,
            override_prompt=override_prompt,
            agent_kind=agent_kind,
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
    agent_kind: str,
    runtime,
    cwd: Path,
) -> NodeResult:
    task_prompt = override_prompt if override_prompt is not None else await _compose_prompt(ctx, node, include_ask_user_protocol=False)
    prompt = task_prompt
    if override_prompt is None:
        prompt = _append_prompt(prompt, contract_prompt_suffix(node))
    output_schema = schema_for_contract(node) if override_prompt is None else None
    prompt = _append_recovery_resume_context(prompt, step)
    prompt = rewrite_runtime_upload_paths(prompt)
    input_payload = loads(step.input_json, {}) or {}
    if not isinstance(input_payload, dict):
        input_payload = {}
    model = str(node.get("model") or "").strip() or None
    reasoning_effort = normalize_reasoning_effort_for_agent(agent_kind, node.get("reasoning_effort"))

    preflight: str | NodeResult = ""
    if _should_run_ask_user_preflight(ctx, node, task_prompt):
        preflight = await _run_ask_user_preflight(
            ctx,
            node,
            step,
            runtime=runtime,
            prompt=rewrite_runtime_upload_paths(_append_recovery_resume_context(task_prompt, step)),
            model=model,
            reasoning_effort=reasoning_effort,
            cwd=cwd,
        )
    if isinstance(preflight, NodeResult):
        return preflight
    input_payload = loads(step.input_json, {}) or {}
    if not isinstance(input_payload, dict):
        input_payload = {}
    if preflight:
        input_payload["ask_user_plan"] = {"summary": preflight}
        prompt = _append_prompt(
            prompt,
            "\n".join(
                [
                    "# 用户决策摘要",
                    preflight,
                    "# 执行要求",
                    "请基于上述用户决策完成当前节点；不要再次调用 ask_user。",
                ]
            ),
        )
    input_payload["prompt"] = prompt
    step.input_json = dumps(input_payload)
    chunks: list[str] = []

    async def on_chunk(chunk: AgentChunk) -> None:
        session_from_chunk = _extract_session_id(chunk.raw) if isinstance(chunk.raw, dict) else None
        if session_from_chunk and session_from_chunk != ctx.agent_session_id:
            ctx.agent_session_id = session_from_chunk
            step.agent_session_id = session_from_chunk
            await ctx.db.commit()
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
            allowed_tools=None,
            model=model,
            reasoning_effort=reasoning_effort,
            cwd=cwd,
            on_chunk=on_chunk,
            cancel_event=ctx.channel.cancel_event,
            on_ask_user=None,
            runtime_tools=ctx.runtime_tools,
            runtime_policy="execute",
            output_schema=output_schema,
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("runtime crashed for node=%s", node.get("id"))
        return NodeResult(
            status="failed",
            error=f"Agent 执行异常: {exc}",
            agent_session_id=ctx.agent_session_id,
        )

    next_session_id = result.session_id or ctx.agent_session_id
    if next_session_id:
        ctx.agent_session_id = next_session_id

    if result.finished_with == "cancelled":
        return NodeResult(status="cancelled", agent_session_id=next_session_id)
    if result.finished_with == "error":
        return NodeResult(
            status="failed",
            error=result.error or "Agent 执行失败",
            agent_session_id=next_session_id,
        )
    text = result.total_text or "".join(chunks)
    if expects_text and node.get("type") == "output":
        validated = await _validate_node_output(ctx, node, text)
        if ctx.channel.cancel_event.is_set():
            return NodeResult(status="cancelled", agent_session_id=next_session_id)
        if not validated.ok:
            await _append_log(ctx, step, "warn", f"output 节点最终输出无效：{validated.error}")
            return NodeResult(
                status="failed",
                error=_contract_failure_message(node, validated.error or "输出无效"),
                agent_session_id=next_session_id,
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
            if repair_result.output is not None:
                repair_result.output = await asyncio.to_thread(
                    import_runtime_images,
                    repair_result.output,
                    workspace=ctx.workspace,
                )
            return repair_result
        output = validated.output
    else:
        output = text if expects_text else text
    if expects_text and node.get("type") == "generate":
        output = await asyncio.to_thread(import_runtime_images, output, workspace=ctx.workspace)
    await _append_log(ctx, step, "info", "节点执行完成")
    return NodeResult(status="success", output=output, agent_session_id=next_session_id)


async def _run_ask_user_preflight(
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
    core_protocol = await get_prompt_content(ctx.db, "ask_user_protocol")
    preflight_protocol = await get_prompt_content(ctx.db, "ask_user_preflight_protocol")
    ask_user_protocol = f"{core_protocol.strip()}\n\n{preflight_protocol.strip()}"
    input_payload = loads(step.input_json, {}) or {}
    if not isinstance(input_payload, dict):
        input_payload = {}
    state = _preflight_state(input_payload)
    final = state.get("final")
    if isinstance(final, dict):
        summary = str(final.get("decision_summary") or "").strip()
        if summary:
            return summary

    feedback: str | None = None
    for _attempt in range(_ASK_USER_PREFLIGHT_MAX_ATTEMPTS):
        preflight_prompt = _build_ask_user_preflight_prompt(
            prompt,
            ask_user_protocol,
            state,
            feedback=feedback,
        )
        chunks: list[str] = []

        async def on_preflight_chunk(chunk: AgentChunk) -> None:
            if chunk.type == "text" and chunk.text:
                chunks.append(chunk.text)

        try:
            result = await runtime.execute(
                prompt=preflight_prompt,
                session_id=None,
                allowed_tools=None,
                model=model,
                reasoning_effort=reasoning_effort,
                cwd=cwd,
                on_chunk=on_preflight_chunk,
                cancel_event=ctx.channel.cancel_event,
                on_ask_user=None,
                runtime_tools=ctx.planning_runtime_tools,
                runtime_policy="ask_user_plan",
                output_schema=_ASK_USER_PREFLIGHT_OUTPUT_SCHEMA,
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("runtime ask_user preflight crashed for node=%s", node.get("id"))
            return NodeResult(
                status="failed",
                error=f"Agent 提问预检异常: {exc}",
                agent_session_id=ctx.agent_session_id,
            )

        if result.finished_with == "cancelled":
            return NodeResult(status="cancelled", agent_session_id=ctx.agent_session_id)
        if result.finished_with == "error":
            return NodeResult(
                status="failed",
                error=result.error or "Agent 提问预检失败",
                agent_session_id=ctx.agent_session_id,
            )

        raw_output = result.total_text or "".join(chunks)
        action_payload, feedback = _parse_preflight_action(raw_output)
        if action_payload is None:
            await _append_log(
                ctx,
                step,
                "warn",
                f"Agent 提问预检输出无效：{feedback or '未返回合法 action'}；原始输出：{_truncate_log_text(raw_output)}",
            )
            continue
        action = action_payload["action"]
        if action == "ask":
            request, feedback = _preflight_ask_request(action_payload)
            if request is None:
                continue
            return await _enter_ask_user_waiting(
                ctx,
                node,
                step,
                request,
                state=state,
                rationale=str(action_payload.get("rationale") or "").strip() or None,
            )

        summary, reason, feedback = _preflight_complete(action_payload, prompt, state)
        if summary is None or reason is None:
            continue
        state["final"] = {"decision_summary": summary, "reason": reason}
        input_payload = loads(step.input_json, {}) or {}
        if not isinstance(input_payload, dict):
            input_payload = {}
        input_payload["ask_user_preflight"] = state
        input_payload.pop("ask_user", None)
        step.input_json = dumps(input_payload)
        await ctx.db.commit()
        return summary

    return NodeResult(status="failed", error=f"Agent 提问预检输出无效：{feedback or '未返回合法 action'}")


def _preflight_state(input_payload: dict[str, Any]) -> dict[str, Any]:
    state = input_payload.get("ask_user_preflight")
    if not isinstance(state, dict):
        state = {}
    history = state.get("history")
    if not isinstance(history, list):
        state["history"] = []
    return dict(state)


def _should_run_ask_user_preflight(ctx: ExecutionContext, node: dict[str, Any], task_prompt: str) -> bool:
    if node.get("type") == "output":
        return False
    if node.get("type") == "generate" and node.get("ask_user_enabled") is False:
        return False
    if _prompt_forces_ask_user(task_prompt):
        return True
    if node.get("type") == "generate" and isinstance(node.get("output_contract"), dict):
        return not _has_direct_user_input_value(ctx, node)
    return True


def _prompt_forces_ask_user(prompt: str) -> bool:
    lowered = prompt.lower()
    return any(
        marker in lowered
        for marker in (
            "必须先调用 ask_user",
            "必须调用 ask_user",
            "先调用 ask_user",
            "先询问",
            "先提问",
            "ask_user",
        )
    )


def _has_direct_user_input_value(ctx: ExecutionContext, node: dict[str, Any]) -> bool:
    node_id = node.get("id")
    for edge in ctx.graph.get("edges", []):
        if not isinstance(edge, dict) or edge.get("target") != node_id:
            continue
        source_id = edge.get("source")
        if not isinstance(source_id, str):
            continue
        source_node = ctx.nodes_by_id.get(source_id, {})
        if source_node.get("type") != "user_input":
            continue
        value = ctx.outputs.get(source_id)
        if _has_meaningful_input_value(value):
            return True
    return False


def _has_meaningful_input_value(value: Any) -> bool:
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, dict):
        text = value.get("value")
        attachments = value.get("attachments")
        return (isinstance(text, str) and bool(text.strip())) or (isinstance(attachments, list) and bool(attachments))
    return value is not None


def _build_ask_user_preflight_prompt(
    prompt: str,
    ask_user_protocol: str,
    state: dict[str, Any],
    *,
    feedback: str | None,
) -> str:
    history = state.get("history") if isinstance(state.get("history"), list) else []
    sections = [
        prompt,
        ask_user_protocol,
        "# 受限提问阶段",
        "你现在处于 planning/read-only 阶段，只能判断是否需要向用户补齐会影响当前节点执行的真实决策。",
        "不要完成当前节点任务，不要生成文件、代码、最终答案或运行结果。",
        "推荐、选择、个性化、需求澄清、方案收敛类任务中，如果上游输入缺少会显著影响结果的偏好、约束、目标或交付形式，必须先进入提问。",
        "如果用户明确要求先问几个问题、先了解偏好再决定、或明确要求调用 ask_user，在没有历史回答前必须返回 action=ask。",
        "用户回答后，如果仍缺关键决策，可以继续返回 action=ask；如果信息已经足够，返回 action=complete。",
        "没有历史回答时，不得声称用户已回答、用户取消了提问、已获得用户偏好，或当前无法继续获取更多决策信息。",
        "# 已有提问历史 JSON",
        dumps(history),
        "# 输出格式",
        "只输出一个 JSON 对象，不要输出 Markdown 代码块或解释。",
        "输出该 JSON 对象后必须立即停止，禁止继续输出当前节点任务结果、示例、代码块或第二个 JSON。",
        "需要继续提问时输出：{\"action\":\"ask\",\"rationale\":\"...\",\"request\":{\"context\":{\"title\":\"...\",\"summary\":\"...\"},\"groups\":[...]}}。",
        "信息足够时输出：{\"action\":\"complete\",\"decision_summary\":\"...\",\"reason\":\"...\"}。",
        "request.context 必须包含面向用户的 title 和 summary，用于说明本轮提问主题和为什么现在需要选择。",
        "request.groups 必须遵守 ask_user 协议：1-3 组问题，每组 2-3 个真实业务选项，且一个 recommended=true 的推荐项排第一。",
    ]
    if feedback:
        sections.extend(["# 上一次输出无效，请修正", feedback])
    return "\n\n".join(section for section in sections if section)


def _parse_preflight_action(text: str) -> tuple[dict[str, Any] | None, str | None]:
    payload = _json_object_from_text(text)
    if payload is None:
        return None, "必须输出合法 JSON 对象"
    if contains_unicode_replacement(payload):
        return None, UNICODE_REPLACEMENT_ERROR
    action = payload.get("action")
    if action == "ask":
        return payload, None
    if action == "complete":
        return payload, None
    if isinstance(payload.get("ask_user"), dict):
        return {
            "action": "ask",
            "request": payload["ask_user"],
            "rationale": payload.get("rationale") or payload.get("reason"),
        }, None
    if isinstance(payload.get("decision_summary"), str):
        return {
            "action": "complete",
            "decision_summary": payload.get("decision_summary"),
            "reason": payload.get("reason"),
        }, None
    return None, "JSON 必须包含 action=ask 或 action=complete"


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


def _truncate_log_text(text: str, limit: int = 1000) -> str:
    cleaned = text.strip()
    if len(cleaned) <= limit:
        return cleaned
    return f"{cleaned[:limit]}..."


def _preflight_ask_request(action_payload: dict[str, Any]) -> tuple[AskUserRequest | None, str | None]:
    raw_request = action_payload.get("request")
    if not isinstance(raw_request, dict):
        return None, "action=ask 必须包含 request 对象"
    request_payload = dict(raw_request)
    request_payload["tool_use_id"] = str(request_payload.get("tool_use_id") or new_id("ask"))
    try:
        request = AskUserRequest.model_validate(request_payload)
    except Exception as exc:  # noqa: BLE001
        return None, f"ask_user request 不合法：{exc}"
    protocol_error = _validate_ask_request(request)
    if protocol_error:
        return None, protocol_error
    return request, None


def _preflight_complete(
    action_payload: dict[str, Any],
    prompt: str,
    state: dict[str, Any],
) -> tuple[str | None, str | None, str | None]:
    summary = str(action_payload.get("decision_summary") or "").strip()
    reason = str(action_payload.get("reason") or "").strip()
    if not summary:
        return None, None, "action=complete 必须包含非空 decision_summary"
    if not reason:
        return None, None, "action=complete 必须包含非空 reason"
    history = state.get("history") if isinstance(state.get("history"), list) else []
    if not history and _prompt_requires_questions(prompt):
        return None, None, "用户明确要求先提问或调用 ask_user；没有历史回答前不能 complete"
    if not history and _claims_user_interaction(summary + "\n" + reason):
        return None, None, "没有历史回答时不能声称用户已回答、取消或已经提供偏好"
    return summary, reason, None


def _prompt_requires_questions(prompt: str) -> bool:
    patterns = [
        r"问我.{0,20}问题",
        r"先.{0,12}问",
        r"提.{0,12}问题.{0,12}再",
        r"了解.{0,12}偏好.{0,12}再",
        r"调用\s*ask_user",
        r"触发\s*ask_user",
    ]
    return any(re.search(pattern, prompt, flags=re.IGNORECASE) for pattern in patterns)


def _claims_user_interaction(text: str) -> bool:
    patterns = [
        r"用户.{0,12}取消",
        r"用户.{0,12}已.{0,6}回答",
        r"用户.{0,12}回答",
        r"根据用户回答",
        r"收到用户",
        r"已获得.{0,12}偏好",
        r"无法继续获取",
    ]
    return any(re.search(pattern, text) for pattern in patterns)


async def _enter_ask_user_waiting(
    ctx: ExecutionContext,
    node: dict[str, Any],
    step: Step,
    request: AskUserRequest,
    *,
    state: dict[str, Any],
    rationale: str | None,
) -> NodeResult:
    request = request.model_copy(update={"groups": append_ask_user_none_option(request.groups)})
    if ctx.channel.cancel_event.is_set():
        return NodeResult(status="cancelled", agent_session_id=ctx.agent_session_id)

    input_payload = loads(step.input_json, {}) or {}
    if not isinstance(input_payload, dict):
        input_payload = {}
    state = dict(state)
    state["current_request"] = request.model_dump(exclude_none=True)
    if rationale:
        state["rationale"] = rationale
    input_payload["ask_user_preflight"] = state
    input_payload["ask_user"] = request.model_dump(exclude_none=True)
    step.input_json = dumps(input_payload)
    step.status = "waiting_for_user"

    label = request.groups[0].label if request.groups else "需要补充输入"
    if len(request.groups) > 1:
        label = f"{label} 等 {len(request.groups)} 个问题"
    await _append_log(ctx, step, "info", f"向用户提问：{label}")

    return NodeResult(status="waiting", agent_session_id=ctx.agent_session_id)


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
    if unicode_repair:
        repair_session_id = None
        ctx.agent_session_id = None
        step.agent_session_id = None
    try:
        result = await runtime.execute(
            prompt=repair_prompt,
            session_id=repair_session_id,
            allowed_tools=None,
            model=model,
            reasoning_effort=reasoning_effort,
            cwd=cwd,
            on_chunk=on_repair_chunk,
            cancel_event=ctx.channel.cancel_event,
            on_ask_user=None,
            runtime_tools=ctx.runtime_tools,
            runtime_policy="execute",
            output_schema=output_schema,
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("runtime repair crashed for node=%s", node.get("id"))
        return NodeResult(
            status="failed",
            error=f"Agent 修正输出异常: {exc}",
            agent_session_id=ctx.agent_session_id,
        )

    next_session_id = result.session_id or ctx.agent_session_id
    if next_session_id:
        ctx.agent_session_id = next_session_id
    if result.finished_with == "cancelled":
        return NodeResult(status="cancelled", agent_session_id=next_session_id)
    if result.finished_with == "error":
        return NodeResult(
            status="failed",
            error=result.error or "Agent 修正输出失败",
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


async def _compose_prompt(ctx: ExecutionContext, node: dict[str, Any], *, include_ask_user_protocol: bool = True) -> str:
    prompt = await asyncio.to_thread(_compose_node_prompt, ctx, node)
    if include_ask_user_protocol:
        ask_user_protocol = await get_prompt_content(ctx.db, "ask_user_protocol")
        prompt = _append_prompt(prompt, ask_user_protocol)
    if node.get("type") == "output":
        template_content = await get_prompt_content(ctx.db, "output_html_rendering")
        return render_prompt(template_content, {"user_prompt": prompt})
    return prompt


def _append_recovery_resume_context(prompt: str, step: Step) -> str:
    payload = loads(step.input_json, {}) or {}
    if not isinstance(payload, dict):
        return prompt
    ask = payload.get("ask_user")
    resume = payload.get("resume")
    if not isinstance(ask, dict) or not isinstance(resume, dict):
        return prompt
    clean_prompt = _TEST_ASK_USER_RE.sub("", prompt).strip()
    sections = [
        clean_prompt,
        "# 恢复上下文",
        "本节点在等待用户输入后运行被中断。请基于下面的用户回答继续完成当前节点，不要再次调用 ask_user。",
        "## 已提出的问题",
        dumps(ask),
        "## 用户回答",
        dumps(resume),
    ]
    return "\n\n".join(section for section in sections if section)


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
    _materialize_context_images(ctx)
    base = str(node.get("prompt") or "").strip()
    if node.get("type") == "output":
        source_id = node.get("source_node_id")
        if isinstance(source_id, str) and source_id in ctx.outputs:
            upstream = _format_upstream_context(ctx, node, exclude_source_ids={source_id})
            main_input = ctx.outputs[source_id]
            primary = f"# 主输入（来自 {source_id}）\n{_format_value(ctx, main_input)}"
            sections = [primary]
            if upstream:
                sections.append("# 其它上游上下文\n" + upstream)
            sections.append("# 输出指令\n" + base)
            return "\n\n".join(sections)
    upstream = _format_upstream_context(ctx, node)
    sections: list[str] = []
    if upstream:
        sections.append("# 上游上下文\n" + upstream)
    sections.append("# 当前任务\n" + base)
    return "\n\n".join(sections)


def _append_prompt(prompt: str, suffix: str) -> str:
    parts = [part.strip() for part in (prompt, suffix) if part and part.strip()]
    return "\n\n".join(parts)


def _format_upstream_context(
    ctx: ExecutionContext,
    node: dict[str, Any],
    *,
    exclude_source_ids: set[str] | None = None,
) -> str:
    node_id = node["id"]
    parts: list[str] = []
    seen_source_ids: set[str] = set()
    excluded = exclude_source_ids or set()
    for edge in ctx.graph.get("edges", []):
        if not isinstance(edge, dict):
            continue
        if edge.get("target") != node_id:
            continue
        source_id = edge.get("source")
        if not isinstance(source_id, str) or source_id not in ctx.outputs:
            continue
        if source_id in excluded or source_id in seen_source_ids:
            continue
        seen_source_ids.add(source_id)
        upstream_node = ctx.nodes_by_id.get(source_id, {})
        title = upstream_node.get("title") or source_id
        parts.append(f"## {title} ({source_id})\n{_format_value(ctx, ctx.outputs[source_id])}")
    return "\n\n".join(parts)


def _runtime_upload_refs_for_node(ctx: ExecutionContext, node: dict[str, Any], step: Step) -> list[RuntimeUploadRef]:
    refs: dict[str, RuntimeUploadRef] = {}
    for source_id in _prompt_source_ids(ctx, node):
        source_node = ctx.nodes_by_id.get(source_id, {})
        if source_node.get("type") not in {"user_input", "asset"}:
            continue
        owner_id = ctx.asset_owner_id if source_node.get("type") == "asset" else ctx.user_id
        _collect_upload_refs_from_value(owner_id, ctx.outputs.get(source_id), refs)
    _collect_resume_upload_refs(ctx, step, refs)
    return list(refs.values())


def _prompt_source_ids(ctx: ExecutionContext, node: dict[str, Any]) -> set[str]:
    node_id = node["id"]
    source_ids = {
        edge.get("source")
        for edge in ctx.graph.get("edges", [])
        if isinstance(edge, dict) and edge.get("target") == node_id and isinstance(edge.get("source"), str)
    }
    source_id = node.get("source_node_id")
    if isinstance(source_id, str):
        source_ids.add(source_id)
    return {source_id for source_id in source_ids if isinstance(source_id, str) and source_id in ctx.outputs}


def _collect_resume_upload_refs(ctx: ExecutionContext, step: Step, refs: dict[str, RuntimeUploadRef]) -> None:
    payload = loads(step.input_json, {}) or {}
    if not isinstance(payload, dict):
        return
    resume = payload.get("resume")
    if isinstance(resume, dict):
        _collect_upload_refs_from_value(ctx.user_id, resume.get("attachments"), refs)
    preflight = payload.get("ask_user_preflight")
    if isinstance(preflight, dict):
        history = preflight.get("history")
        if isinstance(history, list):
            for entry in history:
                if not isinstance(entry, dict):
                    continue
                response = entry.get("response")
                if isinstance(response, dict):
                    _collect_upload_refs_from_value(ctx.user_id, response.get("attachments"), refs)


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


def _format_value(ctx: ExecutionContext, value: Any) -> str:
    prepared = _materialize_runtime_images(ctx, value)
    if isinstance(prepared, str):
        return rewrite_runtime_upload_paths(replace_workspace_paths_for_prompt(prepared, _run_from_context(ctx)))
    try:
        return rewrite_runtime_upload_paths(replace_workspace_paths_for_prompt(dumps(prepared), _run_from_context(ctx)))
    except Exception:  # noqa: BLE001
        return rewrite_runtime_upload_paths(replace_workspace_paths_for_prompt(str(prepared), _run_from_context(ctx)))


def _materialize_context_images(ctx: ExecutionContext) -> None:
    for source_id, value in list(ctx.outputs.items()):
        ctx.outputs[source_id] = _materialize_runtime_images(ctx, value)


def _materialize_runtime_images(ctx: ExecutionContext, value: Any) -> Any:
    workspace = ctx.workspace
    imported = import_runtime_images(value, workspace=workspace)
    return fill_image_download_urls(imported, _run_from_context(ctx), workspace)


def _finalize_output_html(ctx: ExecutionContext, html_text: str) -> str:
    run = _run_from_context(ctx)
    _materialize_context_images(ctx)
    html_text = replace_workspace_paths_in_html(html_text, run)
    refs: list[tuple[str, str]] = []
    for value in ctx.outputs.values():
        refs.extend(collect_workspace_image_refs(value, run, ctx.workspace))
    return ensure_html_images(html_text, refs)


def _run_from_context(ctx: ExecutionContext) -> Run:
    return Run(id=ctx.run_id, app_id=ctx.app_id, owner_id=ctx.user_id)


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


def _match_branch(answer: str, valid_keys: list[str], binary_mode: bool) -> str:
    lowered = {key.lower(): key for key in valid_keys}
    if answer in lowered:
        return lowered[answer]
    # 包含关系兜底：LLM 输出 "true。" 这种带标点的情况。
    for key_lower, key in lowered.items():
        if key_lower and key_lower in answer:
            return key
    if binary_mode:
        if "false" in lowered:
            return lowered["false"]
        return valid_keys[0] if valid_keys else DEFAULT_BRANCH_KEY
    return DEFAULT_BRANCH_KEY


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
    其它时候返回 source_handle 等于 branch_key（或 cases 模式下命中 __default__）的 target。
    """

    targets: set[str] = set()
    for edge in ctx.graph.get("edges", []):
        if not isinstance(edge, dict) or edge.get("source") != condition_id:
            continue
        handle = edge.get("source_handle")
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


def _downstream_nodes(ctx: ExecutionContext, start_id: str) -> set[str]:
    """从 start_id 起，收集所有可达的下游节点（含自身）。"""

    visited: set[str] = set()
    stack = [start_id]
    while stack:
        current = stack.pop()
        if current in visited:
            continue
        visited.add(current)
        for edge in ctx.graph.get("edges", []):
            if isinstance(edge, dict) and edge.get("source") == current:
                target = edge.get("target")
                if isinstance(target, str):
                    stack.append(target)
    return visited


def _reachable_from_targets(ctx: ExecutionContext, targets: set[str]) -> set[str]:
    reachable: set[str] = set()
    for target in targets:
        reachable.update(_downstream_nodes(ctx, target))
    return reachable


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
