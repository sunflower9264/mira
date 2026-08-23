"""Run 域的业务逻辑（与 SSE / orchestrator 解耦）。

只承担：
- POST /api/runs 的输入校验 + DB 落地（创建 Run + 每节点占位 Step）；
- GET / list / delete 的查询、序列化、级联清理；
- cancel 写库 + 通知 orchestrator（具体 cancel 语义在 run_orchestrator）。
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from fastapi import HTTPException
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db import SessionLocal
from app.models import (
    App,
    Run,
    RunAgentBranch,
    RunEvent,
    RunWorkspaceCheckpoint,
    Step,
    StepLog,
)
from app.runtime.base import DecisionAttachment, DecisionResult
from app.schemas.decision import DecisionGroup
from app.schemas import RunInputValue, RunOut, RunResumeIn, RunSummaryOut
from app.services.apps import EMPTY_GRAPH, can_run_app, get_owned_app_or_404, get_visible_app_or_404, graph_for_viewer, should_redact_app_source
from app.services.graph_validation import (
    GraphValidationError,
    topological_order,
    user_input_node_ids,
)
from app.services.graph_inputs import prepare_executable_graph
from app.services.decision_prompts import validate_decision_answers
from app.services.execution_plan import compile_execution_plan
from app.services.run_serializer import run_to_out, run_to_summary_out
from app.services.runtime_paths import clone_run_scoped_homes, run_workspace, run_workspace_path
from app.services.workspace_tree import WorkspaceTree, remove_tree
from app.services.workflow_data import copy_reused_output_envelope
from app.services.tools import stamp_run_tools_snapshot
from app.services.uploads import resolve_upload, delete_upload
from app.utils import display_now, dumps, loads, new_id, now_utc

logger = logging.getLogger(__name__)

RUN_NAME_MAX_LENGTH = 80
TERMINAL_RUN_STATUSES = {"success", "failed", "cancelled"}
RERUN_SOURCE_STATUSES = TERMINAL_RUN_STATUSES | {"interrupted"}
CONDITION_DEFAULT_BRANCH_KEY = "__default__"


# --- 输入归一与校验 ---------------------------------------------------------


def normalize_run_inputs(
    raw: dict[str, Any] | None,
    user_input_ids: set[str],
) -> dict[str, RunInputValue]:
    """把 POST /api/runs 的 inputs 规范化成 ``{node_id: RunInputValue}``。

    支持两种形态：
      1. ``{node_id: "纯文本"}``（旧契约）
      2. ``{node_id: {"value": "...", "attachments": [{"id": "upl_..."}]}}``（新契约）

    超出 graph user_input 节点范围的 key 直接 400，避免静默吞输入。
    """

    raw = raw or {}
    if not isinstance(raw, dict):
        raise HTTPException(status_code=400, detail="inputs 必须是对象")
    normalized: dict[str, RunInputValue] = {}
    for key, value in raw.items():
        if key not in user_input_ids:
            raise HTTPException(status_code=400, detail=f"输入节点不存在：{key}")
        if value is None:
            normalized[key] = RunInputValue()
            continue
        if isinstance(value, str):
            normalized[key] = RunInputValue(value=value)
            continue
        if isinstance(value, dict):
            try:
                normalized[key] = RunInputValue.model_validate(value)
            except Exception as exc:  # noqa: BLE001
                raise HTTPException(status_code=400, detail=f"输入节点 {key} 字段非法") from exc
            continue
        raise HTTPException(status_code=400, detail=f"输入节点 {key} 字段类型不支持")
    return normalized


def serialize_run_inputs(inputs: dict[str, RunInputValue]) -> dict[str, Any]:
    """落库前把 RunInputValue 转回 JSON 字典。"""

    return {key: value.model_dump(mode="json") for key, value in inputs.items()}


def default_run_name(app_name: str, inputs: dict[str, RunInputValue]) -> str:
    for value in inputs.values():
        text = _compact_text(value.value)
        if text:
            return _truncate_run_name(text)
    for value in inputs.values():
        for attachment in value.attachments:
            text = _compact_text(attachment.name or "")
            if text:
                return _truncate_run_name(text)
    return _truncate_run_name(f"{_compact_text(app_name) or '运行'} · {display_now().strftime('%m-%d %H:%M')}")


def normalize_run_name(name: str) -> str:
    normalized = _compact_text(name)
    if not normalized:
        raise HTTPException(status_code=400, detail="运行记录名称不能为空")
    if len(normalized) > RUN_NAME_MAX_LENGTH:
        raise HTTPException(status_code=400, detail=f"运行记录名称不能超过 {RUN_NAME_MAX_LENGTH} 个字符")
    return normalized


def _compact_text(text: str) -> str:
    return " ".join(str(text).split())


def _truncate_run_name(name: str) -> str:
    if len(name) <= RUN_NAME_MAX_LENGTH:
        return name
    return name[: RUN_NAME_MAX_LENGTH - 1] + "…"


def attachments_meta(user_id: str, value: RunInputValue) -> list[dict[str, Any]]:
    """把 RunInputValue.attachments 解析成包含 path/download_url/mime/size 的 meta。

    任一 id 不存在 / 不归属当前用户，抛 404。
    """

    metas: list[dict[str, Any]] = []
    for ref in value.attachments:
        resolved = resolve_upload(user_id, ref.id)
        if resolved is None:
            raise HTTPException(status_code=404, detail="附件不存在")
        metas.append({
            "id": resolved.id,
            "name": ref.name or resolved.name,
            "path": str(resolved.path),
            "download_url": resolved.download_url(user_id),
            "mime": resolved.mime,
            "size": resolved.size,
        })
    return metas


# --- 创建 -------------------------------------------------------------------


async def create_run_record(
    db: AsyncSession,
    user_id: str,
    app_id: str,
    raw_inputs: dict[str, Any] | None,
) -> tuple[str, dict[str, Any]]:
    """完成 POST /api/runs 的所有校验和 DB 落地，返回 (run_id, graph_snapshot)。

    不负责调度 orchestrator / 创建 hub channel —— 那些由路由层处理，避免
    service 层耦合 asyncio runtime。
    """

    app = await get_visible_app_or_404(db, app_id, user_id)
    if not can_run_app(app):
        raise HTTPException(status_code=400, detail="应用已下架，不能继续运行")
    try:
        graph = prepare_executable_graph(loads(app.graph_json, EMPTY_GRAPH), owner_id=app.owner_id)
    except GraphValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    try:
        ordered = topological_order(graph)
    except GraphValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None

    graph_snapshot = await stamp_run_tools_snapshot(db, graph)
    inputs = normalize_run_inputs(raw_inputs, user_input_node_ids(graph))
    inputs_payload = serialize_run_inputs(inputs)
    inputs_json = dumps(inputs_payload)
    if len(inputs_json.encode("utf-8")) > get_settings().max_input_size_bytes:
        raise HTTPException(status_code=400, detail="输入内容过大")
    # 提前校验所有附件归属，避免 orchestrator 启动后才发现 404。
    for value in inputs.values():
        attachments_meta(user_id, value)

    run = Run(
        id=new_id("run"),
        app_id=app.id,
        owner_id=user_id,
        status="pending",
        name=default_run_name(app.name, inputs),
        inputs_json=inputs_json,
        graph_json=dumps(graph_snapshot),
        heartbeat_at=None,
        interrupted_at=None,
        recovery_reason=None,
        resume_from_node_id=None,
        started_at=None,
        finished_at=None,
        error=None,
    )
    db.add(run)
    for index, node in enumerate(ordered):
        node_id = node.get("id")
        if not isinstance(node_id, str):
            continue
        db.add(
            Step(
                id=new_id("step"),
                run_id=run.id,
                node_id=node_id,
                ordering=index,
                status="pending",
                attempt=0,
                input_json="null",
                output_json=None,
            )
        )
    await db.commit()
    await db.refresh(run)
    return run.id, graph_for_viewer(app, user_id, graph_snapshot)


async def create_rerun_from_record(
    db: AsyncSession,
    user_id: str,
    source_run_id: str,
    app_id: str,
    node_id: str,
    raw_inputs: dict[str, Any] | None,
    condition_branch_override: dict[str, Any] | None = None,
) -> tuple[str, dict[str, Any]]:
    """基于历史 run 创建一个新 run，并预填起点之前可复用的 step 结果。"""

    source_run = (
        await db.execute(select(Run).where(Run.id == source_run_id, Run.owner_id == user_id))
    ).scalar_one_or_none()
    if source_run is None:
        raise HTTPException(status_code=404, detail="运行记录不存在")
    if source_run.app_id != app_id:
        raise HTTPException(status_code=400, detail="来源运行不属于当前应用")
    if source_run.status not in RERUN_SOURCE_STATUSES:
        raise HTTPException(status_code=409, detail="只能从已结束或已中断的运行重新执行")

    app = await get_owned_app_or_404(db, app_id, user_id)
    try:
        graph = prepare_executable_graph(loads(app.graph_json, EMPTY_GRAPH), owner_id=app.owner_id)
    except GraphValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    try:
        ordered = topological_order(graph)
    except GraphValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None

    nodes_by_id = {
        current.get("id"): current
        for current in graph.get("nodes", [])
        if isinstance(current, dict) and isinstance(current.get("id"), str)
    }
    if node_id not in nodes_by_id:
        raise HTTPException(status_code=404, detail="重新执行起点节点不存在")
    branch_test = _validate_condition_branch_override(
        graph,
        nodes_by_id,
        node_id,
        condition_branch_override,
    )

    input_node_ids = user_input_node_ids(graph)
    loaded_inputs = loads(source_run.inputs_json, {}) or {}
    execution_plan = compile_execution_plan(graph)
    rerun_node_ids = set(execution_plan.descendant_ids({node_id}))
    effective_inputs = (
        {key: value for key, value in loaded_inputs.items() if key in input_node_ids}
        if isinstance(loaded_inputs, dict)
        else {}
    )
    if raw_inputs is not None:
        for key, value in raw_inputs.items():
            if key not in input_node_ids:
                raise HTTPException(status_code=400, detail=f"输入节点不存在：{key}")
            if key in rerun_node_ids:
                effective_inputs[key] = value
    inputs = normalize_run_inputs(effective_inputs, input_node_ids)
    inputs_payload = serialize_run_inputs(inputs)
    inputs_json = dumps(inputs_payload)
    if len(inputs_json.encode("utf-8")) > get_settings().max_input_size_bytes:
        raise HTTPException(status_code=400, detail="输入内容过大")
    for value in inputs.values():
        attachments_meta(user_id, value)

    rerun_start_node_id = node_id
    frozen_node_ids = set(execution_plan.ordered_node_ids) - rerun_node_ids
    source_steps = (
        await db.execute(
            select(Step)
            .where(Step.run_id == source_run.id)
            .order_by(Step.ordering.asc(), Step.id.asc())
        )
    ).scalars().all()
    source_steps_by_node = {step.node_id: step for step in source_steps}
    source_cut_step = source_steps_by_node.get(node_id)
    if source_cut_step is None or not source_cut_step.pre_checkpoint_id:
        raise HTTPException(status_code=409, detail="来源运行在该节点前没有可复用的 workspace checkpoint")
    source_cut_checkpoint = await db.get(RunWorkspaceCheckpoint, source_cut_step.pre_checkpoint_id)
    if source_cut_checkpoint is None or source_cut_checkpoint.run_id != source_run.id:
        raise HTTPException(status_code=409, detail="来源运行的 workspace checkpoint 已失效")

    graph_snapshot = _graph_with_condition_branch_override(graph, branch_test) if branch_test else graph
    graph_snapshot = await stamp_run_tools_snapshot(db, graph_snapshot)
    run = Run(
        id=new_id("run"),
        app_id=app.id,
        owner_id=user_id,
        status="pending",
        name=_condition_branch_run_name(branch_test) if branch_test else default_run_name(app.name, inputs),
        inputs_json=inputs_json,
        graph_json=dumps(graph_snapshot),
        source_run_id=source_run.id,
        rerun_from_node_id=node_id,
        heartbeat_at=None,
        interrupted_at=None,
        recovery_reason=None,
        resume_from_node_id=rerun_start_node_id,
        started_at=None,
        finished_at=None,
        error=None,
    )
    db.add(run)
    source_workspace = run_workspace(source_run.owner_id, source_run.app_id, source_run.id)
    target_workspace = run_workspace(run.owner_id, run.app_id, run.id)
    new_steps_by_node: dict[str, Step] = {}
    for index, current in enumerate(ordered):
        current_node_id = current.get("id")
        if not isinstance(current_node_id, str):
            continue
        step = Step(
            id=new_id("step"),
            run_id=run.id,
            node_id=current_node_id,
            ordering=index,
            status="checkpoint_reused" if current_node_id in frozen_node_ids else "pending",
            attempt=0,
            input_json="null",
            output_json=None,
        )
        db.add(step)
        new_steps_by_node[current_node_id] = step

    try:
        checkpoint_maps = await _clone_checkpoint_state(
            db,
            source_run=source_run,
            target_run=run,
            cut_checkpoint=source_cut_checkpoint,
            frozen_node_ids=frozen_node_ids,
            source_steps_by_node=source_steps_by_node,
        )
        branch_id_map, checkpoint_id_map, cut_execution_branch_id = checkpoint_maps
        new_steps_by_node[node_id].branch_id = cut_execution_branch_id
        clone_run_scoped_homes(source_run.id, run.id)
        for frozen_node_id in frozen_node_ids:
            source_step = source_steps_by_node.get(frozen_node_id)
            target_step = new_steps_by_node[frozen_node_id]
            if source_step is None:
                target_step.branch_id = branch_id_map.get(source_cut_checkpoint.branch_id or "")
                continue
            if source_step.status not in {"success", "skipped"}:
                continue
            target_step.status = source_step.status
            target_step.attempt = source_step.attempt
            target_step.input_json = source_step.input_json
            target_step.output_json = _copy_reused_output_workspace_files(
                source_step.output_json,
                source_workspace=source_workspace,
                target_workspace=target_workspace,
                target_run_id=run.id,
                target_node_id=frozen_node_id,
                target_step_id=target_step.id,
            )
            target_step.started_at = source_step.started_at
            target_step.finished_at = source_step.finished_at
            target_step.duration_ms = source_step.duration_ms
            target_step.error = source_step.error
            target_step.reused_from_run_id = source_run.id
            target_step.reused_from_step_id = source_step.id
            target_step.failure_kind = source_step.failure_kind
            target_step.branch_id = branch_id_map.get(source_step.branch_id or "")
            target_step.pre_checkpoint_id = checkpoint_id_map.get(source_step.pre_checkpoint_id or "")
            target_step.post_checkpoint_id = checkpoint_id_map.get(source_step.post_checkpoint_id or "")
    except (OSError, ValueError) as exc:
        await db.rollback()
        try:
            remove_tree(target_workspace)
        except OSError:
            logger.warning("failed to clean rejected rerun workspace: %s", target_workspace, exc_info=True)
        raise HTTPException(
            status_code=409,
            detail=f"历史节点结果不可复用，请从更早节点重新执行：{exc}",
        ) from None

    await db.commit()
    await db.refresh(run)
    return run.id, graph_snapshot


# --- 查询与序列化 -----------------------------------------------------------


async def load_run_or_404(db: AsyncSession, run_id: str, user_id: str) -> RunOut:
    run = (
        await db.execute(select(Run).where(Run.id == run_id, Run.owner_id == user_id))
    ).scalar_one_or_none()
    if run is None:
        raise HTTPException(status_code=404, detail="运行记录不存在")
    return await _serialize_run(db, run, user_id)


async def update_run_name(db: AsyncSession, run_id: str, user_id: str, name: str) -> RunOut:
    run = (
        await db.execute(select(Run).where(Run.id == run_id, Run.owner_id == user_id))
    ).scalar_one_or_none()
    if run is None:
        raise HTTPException(status_code=404, detail="运行记录不存在")
    run.name = normalize_run_name(name)
    await db.commit()
    await db.refresh(run)
    return await _serialize_run(db, run, user_id)


async def list_run_summaries_for_app(
    db: AsyncSession, app_id: str, user_id: str, limit: int = 50
) -> list[RunSummaryOut]:
    app = await get_visible_app_or_404(db, app_id, user_id)
    rows = (
        await db.execute(
            select(Run)
            .where(Run.app_id == app_id, Run.owner_id == user_id)
            .order_by(Run.started_at.desc().nulls_last(), Run.id.desc())
            .limit(max(1, min(limit, 200)))
        )
    ).scalars().all()
    redact_source = should_redact_app_source(app, user_id)
    return [run_to_summary_out(run, redact_source=redact_source) for run in rows]


async def _serialize_run(db: AsyncSession, run: Run, user_id: str) -> RunOut:
    steps = (
        await db.execute(
            select(Step).where(Step.run_id == run.id).order_by(Step.ordering.asc(), Step.id.asc())
        )
    ).scalars().all()
    logs_by_step: dict[str, list[StepLog]] = {}
    if steps:
        step_ids = [step.id for step in steps]
        log_rows = (
            await db.execute(select(StepLog).where(StepLog.step_id.in_(step_ids)))
        ).scalars().all()
        for log in log_rows:
            logs_by_step.setdefault(log.step_id, []).append(log)
    app = await db.get(App, run.app_id)
    return run_to_out(run, list(steps), logs_by_step, redact_source=should_redact_app_source(app, user_id))


# --- 取消与删除 -------------------------------------------------------------


async def cancel_run_record(db: AsyncSession, run_id: str, user_id: str) -> bool:
    """把 run.status 置 cancelled（仅在 pending/running/waiting_for_user 状态生效）。

    返回 True 表示状态被改写，调用方应同时通知 orchestrator；False 表示已经是终态。
    """

    run = (
        await db.execute(select(Run).where(Run.id == run_id, Run.owner_id == user_id))
    ).scalar_one_or_none()
    if run is None:
        raise HTTPException(status_code=404, detail="运行记录不存在")
    if run.status in TERMINAL_RUN_STATUSES:
        return False
    run.status = "cancelled"
    run.finished_at = now_utc()
    # 把所有未结束的 step 也置 cancelled，保证 GET /api/runs/{id} 视图一致。
    steps = (
        await db.execute(select(Step).where(Step.run_id == run_id))
    ).scalars().all()
    for step in steps:
        if step.status in {"pending", "running", "waiting_for_user"}:
            step.status = "cancelled"
            step.finished_at = run.finished_at
            step.agent_session_id = None
    await db.commit()
    return True


async def delete_run_record(db: AsyncSession, run_id: str, user_id: str) -> None:
    run = (
        await db.execute(select(Run).where(Run.id == run_id, Run.owner_id == user_id))
    ).scalar_one_or_none()
    if run is None:
        raise HTTPException(status_code=404, detail="运行记录不存在")
    if run.status not in TERMINAL_RUN_STATUSES:
        raise HTTPException(status_code=400, detail="只能删除已结束的运行")
    # 收集附件 id 以便随 run 一起清理（避免 uploads 孤儿）。
    inputs = loads(run.inputs_json, {}) or {}
    upload_ids: set[str] = set()
    for value in inputs.values():
        if isinstance(value, dict):
            for ref in value.get("attachments") or []:
                if isinstance(ref, dict) and isinstance(ref.get("id"), str):
                    upload_ids.add(ref["id"])
    step_ids = (
        await db.execute(select(Step.id).where(Step.run_id == run.id))
    ).scalars().all()
    if step_ids:
        await db.execute(delete(StepLog).where(StepLog.step_id.in_(step_ids)))
    await db.execute(delete(RunEvent).where(RunEvent.run_id == run.id))
    await db.execute(delete(Step).where(Step.run_id == run.id))
    workspace = run_workspace_path(run.owner_id, run.app_id, run.id)
    await db.delete(run)
    await db.commit()
    try:
        remove_tree(workspace)
    except OSError:
        logger.warning("failed to delete run workspace: %s", workspace, exc_info=True)
    for upload_id in upload_ids:
        delete_upload(user_id, upload_id)


# --- resume （decision_request 续接）-----------------------------------------------


async def submit_resume(
    db: AsyncSession,
    user_id: str,
    run_id: str,
    payload: RunResumeIn,
) -> DecisionResult:
    """校验 POST /api/runs/{id}/resume 请求体并返回打包好的 DecisionResult。

    本函数只打包 DecisionResult；调用方校验当前 waiting request 后，
    直接完成仍在运行的原生提问 Future。

    校验顺序：
    1. run 归属当前用户 → 否则 404；
    2. run.status 必须 == ``waiting_for_user`` → 否则 409；
    3. ``answers`` / ``text`` / ``attachments`` 至少一项非空 → 否则 400；
    4. ``attachments`` 内每个 id 归属当前用户、未过期 → 否则 404；
    5. ``text`` 长度 ≤ ``max_resume_text_bytes`` → 否则 400。

    ``answers`` 是否匹配 decision_request.groups、node_id / request_id 是否匹配
    由 ``validate_live_resume`` 基于 step.input 中的 waiting request 校验。
    """

    run = (
        await db.execute(select(Run).where(Run.id == run_id, Run.owner_id == user_id))
    ).scalar_one_or_none()
    if run is None:
        raise HTTPException(status_code=404, detail="运行记录不存在")
    if run.status != "waiting_for_user":
        raise HTTPException(status_code=409, detail="当前运行不在等待状态")

    answers = list(payload.answers or [])
    text = (payload.text or "").strip() or None
    attachment_refs = list(payload.attachments or [])
    if not answers and not text and not attachment_refs:
        raise HTTPException(status_code=400, detail="必须至少提供一项输入")

    max_text = get_settings().max_resume_text_bytes
    if text is not None and len(text.encode("utf-8")) > max_text:
        raise HTTPException(status_code=400, detail="补充文本过长")

    metas: list[DecisionAttachment] = []
    for ref in attachment_refs:
        resolved = resolve_upload(user_id, ref.id)
        if resolved is None:
            raise HTTPException(status_code=404, detail="附件不存在")
        metas.append(
            DecisionAttachment(
                id=resolved.id,
                name=ref.name or resolved.name,
                path=str(resolved.path),
                download_url=resolved.download_url(user_id),
                mime=resolved.mime,
                size=resolved.size,
            )
        )

    return DecisionResult(ok=True, answers=answers, text=text, attachments=metas)


async def continue_run_record(db: AsyncSession, run_id: str, user_id: str) -> RunOut:
    run = (
        await db.execute(select(Run).where(Run.id == run_id, Run.owner_id == user_id))
    ).scalar_one_or_none()
    if run is None:
        raise HTTPException(status_code=404, detail="运行记录不存在")
    if run.status != "interrupted":
        raise HTTPException(status_code=409, detail="当前运行不需要继续")
    run.status = "running"
    run.finished_at = None
    run.error = None
    run.heartbeat_at = now_utc()
    await db.commit()
    await db.refresh(run)
    return await _serialize_run(db, run, user_id)


async def mark_active_runs_interrupted(db: AsyncSession) -> int:
    rows = (
        await db.execute(
            select(Run)
            .where(Run.status.in_(["pending", "running", "waiting_for_user"]))
            .order_by(Run.started_at.asc().nulls_first(), Run.id.asc())
        )
    ).scalars().all()
    changed = 0
    for run in rows:
        steps = (
            await db.execute(
                select(Step).where(Step.run_id == run.id).order_by(Step.ordering.asc(), Step.id.asc())
            )
        ).scalars().all()
        resume_step = _first_unfinished_step(list(steps))
        now = now_utc()
        run.status = "interrupted"
        run.interrupted_at = now
        run.recovery_reason = "后端进程重启，运行已暂停"
        run.resume_from_node_id = resume_step.node_id if resume_step else None
        for step in steps:
            if step.status in {"running", "waiting_for_user"}:
                step.status = "interrupted"
                step.finished_at = now
                step.error = "后端进程重启，节点已暂停"
        changed += 1
    if changed:
        await db.commit()
    return changed


async def touch_run_heartbeat(run_id: str) -> None:
    async with SessionLocal() as db:
        run = await db.get(Run, run_id)
        if run is None or run.status in TERMINAL_RUN_STATUSES:
            return
        run.heartbeat_at = now_utc()
        await db.commit()


async def validate_live_resume(
    db: AsyncSession,
    user_id: str,
    run_id: str,
    payload: RunResumeIn,
    result: DecisionResult,
) -> None:
    run = (
        await db.execute(select(Run).where(Run.id == run_id, Run.owner_id == user_id))
    ).scalar_one_or_none()
    if run is None:
        raise HTTPException(status_code=404, detail="运行记录不存在")
    if run.status != "waiting_for_user":
        raise HTTPException(status_code=409, detail="当前运行不在等待状态")
    step = (
        await db.execute(
            select(Step).where(Step.run_id == run_id, Step.node_id == payload.node_id)
        )
    ).scalar_one_or_none()
    if step is None or step.status != "waiting_for_user":
        raise HTTPException(status_code=409, detail="当前没有等待该节点的输入")
    request = _decision_request_payload(step)
    if not request or request.get("request_id") != payload.request_id:
        raise HTTPException(status_code=409, detail="当前决策请求已失效，请重新发起运行")
    groups_payload = request.get("groups")
    try:
        groups = [DecisionGroup.model_validate(item) for item in groups_payload]
    except Exception:  # noqa: BLE001
        raise HTTPException(status_code=409, detail="当前没有等待该节点的输入") from None
    if result.answers:
        answer_error = validate_decision_answers(groups, result.answers)
        if answer_error:
            raise HTTPException(status_code=400, detail=answer_error)


def _first_unfinished_step(steps: list[Step]) -> Step | None:
    for step in steps:
        if step.status not in {"success", "skipped"}:
            return step
    return None


def _copy_reused_output_workspace_files(
    output_json: str | None,
    *,
    source_workspace: Path,
    target_workspace: Path,
    target_run_id: str,
    target_node_id: str,
    target_step_id: str,
) -> str | None:
    if output_json is None:
        return None
    output = loads(output_json, None)
    if output is None:
        return output_json
    copied = copy_reused_output_envelope(
        output,
        source_workspace=source_workspace.resolve(),
        target_workspace=target_workspace.resolve(),
        target_run_id=target_run_id,
        target_node_id=target_node_id,
        target_step_id=target_step_id,
    )
    return dumps(copied)


async def _clone_checkpoint_state(
    db: AsyncSession,
    *,
    source_run: Run,
    target_run: Run,
    cut_checkpoint: RunWorkspaceCheckpoint,
    frozen_node_ids: set[str],
    source_steps_by_node: dict[str, Step],
) -> tuple[dict[str, str], dict[str, str], str]:
    source_branches = (
        await db.execute(
            select(RunAgentBranch)
            .where(RunAgentBranch.run_id == source_run.id)
            .order_by(RunAgentBranch.created_at.asc(), RunAgentBranch.id.asc())
        )
    ).scalars().all()
    source_checkpoints = (
        await db.execute(
            select(RunWorkspaceCheckpoint)
            .where(RunWorkspaceCheckpoint.run_id == source_run.id)
            .order_by(RunWorkspaceCheckpoint.created_at.asc(), RunWorkspaceCheckpoint.id.asc())
        )
    ).scalars().all()
    branches_by_id = {branch.id: branch for branch in source_branches}
    checkpoints_by_id = {checkpoint.id: checkpoint for checkpoint in source_checkpoints}
    required_checkpoint_ids = {cut_checkpoint.id}
    required_branch_ids: set[str] = set()
    for node_id in frozen_node_ids:
        step = source_steps_by_node.get(node_id)
        if step is None:
            continue
        if step.pre_checkpoint_id:
            required_checkpoint_ids.add(step.pre_checkpoint_id)
        if step.post_checkpoint_id:
            required_checkpoint_ids.add(step.post_checkpoint_id)
        if step.branch_id:
            required_branch_ids.add(step.branch_id)
    changed = True
    while changed:
        changed = False
        for checkpoint_id in tuple(required_checkpoint_ids):
            checkpoint = checkpoints_by_id.get(checkpoint_id)
            if checkpoint is not None and checkpoint.branch_id and checkpoint.branch_id not in required_branch_ids:
                required_branch_ids.add(checkpoint.branch_id)
                changed = True
        for branch_id in tuple(required_branch_ids):
            branch = branches_by_id.get(branch_id)
            if branch is None:
                raise ValueError(f"来源 checkpoint branch 不存在：{branch_id}")
            if branch.parent_branch_id and branch.parent_branch_id not in required_branch_ids:
                required_branch_ids.add(branch.parent_branch_id)
                changed = True
            if branch.base_checkpoint_id and branch.base_checkpoint_id not in required_checkpoint_ids:
                required_checkpoint_ids.add(branch.base_checkpoint_id)
                changed = True

    source_root = run_workspace(source_run.owner_id, source_run.app_id, source_run.id)
    target_root = run_workspace(target_run.owner_id, target_run.app_id, target_run.id)
    target_tree = WorkspaceTree(target_root)
    checkpoint_id_map: dict[str, str] = {}
    for checkpoint in source_checkpoints:
        if checkpoint.id not in required_checkpoint_ids:
            continue
        source_snapshot = (source_root / checkpoint.snapshot_relpath).resolve()
        try:
            source_snapshot.relative_to(source_root.resolve())
        except ValueError as exc:
            raise ValueError("来源 checkpoint 路径越界") from exc
        checkpoint_id = new_id("checkpoint")
        target_snapshot, digest = target_tree.clone_checkpoint_from(source_snapshot, checkpoint_id)
        cloned = RunWorkspaceCheckpoint(
            id=checkpoint_id,
            run_id=target_run.id,
            step_id=None,
            node_id=checkpoint.node_id,
            branch_id=None,
            kind="rerun_base" if checkpoint.id == cut_checkpoint.id else checkpoint.kind,
            snapshot_relpath=target_snapshot.relative_to(target_root).as_posix(),
            tree_hash=digest,
            provider_session_id=checkpoint.provider_session_id,
            output_digest=checkpoint.output_digest,
            created_at=checkpoint.created_at,
        )
        db.add(cloned)
        checkpoint_id_map[checkpoint.id] = checkpoint_id

    branch_id_map = {branch_id: new_id("branch") for branch_id in required_branch_ids}
    latest_checkpoint_by_branch: dict[str, RunWorkspaceCheckpoint | None] = {}
    for branch in source_branches:
        if branch.id not in required_branch_ids:
            continue
        branch_checkpoints = [
            checkpoint
            for checkpoint in source_checkpoints
            if checkpoint.id in required_checkpoint_ids and checkpoint.branch_id == branch.id
        ]
        latest = branch_checkpoints[-1] if branch_checkpoints else None
        latest_checkpoint_by_branch[branch.id] = latest
        target_branch_id = branch_id_map[branch.id]
        if latest is None:
            workspace = target_tree.create_empty_branch(target_branch_id)
        else:
            workspace = target_tree.fork_branch(checkpoint_id_map[latest.id], target_branch_id)
        target_branch = RunAgentBranch(
            id=target_branch_id,
            run_id=target_run.id,
            parent_branch_id=branch_id_map.get(branch.parent_branch_id or ""),
            fork_node_id=branch.fork_node_id,
            base_checkpoint_id=checkpoint_id_map.get(branch.base_checkpoint_id or ""),
            provider_session_id=None,
            fork_from_session_id=latest.provider_session_id if latest is not None else None,
            workspace_relpath=workspace.relative_to(target_root).as_posix(),
            state="active",
        )
        db.add(target_branch)
        for checkpoint in branch_checkpoints:
            cloned = await db.get(RunWorkspaceCheckpoint, checkpoint_id_map[checkpoint.id])
            if cloned is not None:
                cloned.branch_id = target_branch_id

    source_cut_branch_id = cut_checkpoint.branch_id
    if not source_cut_branch_id or source_cut_branch_id not in branch_id_map:
        raise ValueError("来源 cut checkpoint branch 不存在")
    cut_execution_branch_id = branch_id_map[source_cut_branch_id]
    latest_cut_branch_checkpoint = latest_checkpoint_by_branch.get(source_cut_branch_id)
    if latest_cut_branch_checkpoint is None or latest_cut_branch_checkpoint.id != cut_checkpoint.id:
        cut_execution_branch_id = new_id("branch")
        workspace = target_tree.fork_branch(
            checkpoint_id_map[cut_checkpoint.id],
            cut_execution_branch_id,
        )
        db.add(
            RunAgentBranch(
                id=cut_execution_branch_id,
                run_id=target_run.id,
                parent_branch_id=branch_id_map[source_cut_branch_id],
                fork_node_id=target_run.rerun_from_node_id,
                base_checkpoint_id=checkpoint_id_map[cut_checkpoint.id],
                provider_session_id=None,
                fork_from_session_id=cut_checkpoint.provider_session_id,
                workspace_relpath=workspace.relative_to(target_root).as_posix(),
                state="active",
            )
        )
    await db.flush()
    return branch_id_map, checkpoint_id_map, cut_execution_branch_id


def _validate_condition_branch_override(
    graph: dict[str, Any],
    nodes_by_id: dict[Any, dict[str, Any]],
    rerun_node_id: str,
    override: dict[str, Any] | None,
) -> dict[str, str] | None:
    if override is None:
        return None
    node_id = override.get("node_id")
    branch_key = override.get("branch_key")
    if node_id != rerun_node_id:
        raise HTTPException(status_code=400, detail="condition 分支测试起点必须等于覆盖节点")
    node = nodes_by_id.get(node_id)
    if not isinstance(node, dict) or node.get("type") != "condition":
        raise HTTPException(status_code=400, detail="只能测试 condition 节点分支")
    if not isinstance(branch_key, str) or not branch_key.strip():
        raise HTTPException(status_code=400, detail="condition 分支 key 必填")
    branch_key = branch_key.strip()
    handles = _condition_branch_handles(node)
    if branch_key not in handles:
        raise HTTPException(status_code=400, detail=f"condition 分支不存在：{branch_key}")
    if not _condition_branch_target_node_ids(graph, str(node_id), branch_key):
        raise HTTPException(status_code=400, detail=f"condition 分支未连接：{branch_key}")
    return {
        "node_id": str(node_id),
        "branch_key": branch_key,
        "node_title": str(node.get("title") or node_id),
        "branch_label": _condition_branch_label(node, branch_key),
    }


def _condition_branch_handles(node: dict[str, Any]) -> set[str]:
    if node.get("mode") == "binary":
        return {"true", "false"}
    if node.get("mode") != "cases":
        return set()
    branches = node.get("branches")
    handles: set[str] = set()
    if isinstance(branches, list):
        for branch in branches:
            if isinstance(branch, dict) and isinstance(branch.get("key"), str) and branch.get("key"):
                handles.add(branch["key"])
    handles.add(CONDITION_DEFAULT_BRANCH_KEY)
    return handles


def _condition_branch_label(node: dict[str, Any], branch_key: str) -> str:
    if branch_key == CONDITION_DEFAULT_BRANCH_KEY:
        return "其它"
    branches = node.get("branches")
    if isinstance(branches, list):
        for branch in branches:
            if not isinstance(branch, dict) or branch.get("key") != branch_key:
                continue
            label = str(branch.get("label") or "").strip()
            return label or branch_key
    return branch_key


def _condition_branch_target_node_ids(
    graph: dict[str, Any],
    condition_id: str,
    branch_key: str | None,
) -> set[str]:
    targets: set[str] = set()
    for edge in graph.get("execution_edges", []):
        if not isinstance(edge, dict):
            continue
        if edge.get("source") != condition_id:
            continue
        if branch_key is not None and edge.get("branch_key") != branch_key:
            continue
        target = edge.get("target")
        if isinstance(target, str):
            targets.add(target)
    return targets


def _graph_with_condition_branch_override(
    graph: dict[str, Any],
    branch_test: dict[str, str],
) -> dict[str, Any]:
    next_graph = dict(graph)
    next_graph["run_overrides"] = {
        "condition_branch": {
            "node_id": branch_test["node_id"],
            "branch_key": branch_test["branch_key"],
        }
    }
    return next_graph


def _condition_branch_run_name(branch_test: dict[str, str]) -> str:
    return _truncate_run_name(
        f"分支测试 · {branch_test['node_title']} · {branch_test['branch_label']}"
    )


def _decision_request_payload(step: Step) -> dict[str, Any] | None:
    payload = loads(step.input_json, {}) or {}
    if not isinstance(payload, dict):
        return None
    request = payload.get("decision_request")
    return request if isinstance(request, dict) else None
