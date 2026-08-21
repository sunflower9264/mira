"""Runs / SSE 事件流 HTTP 接口。

阶段 2：CRUD + SSE 骨架；阶段 3 替换 orchestrator 为真实执行器；阶段 4 加 resume。
"""

from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from fastapi.responses import FileResponse, StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_current_user_optional
from app.db import get_db
from app.models import App, Run, User
from app.schemas import RunArtifactsOut, RunCreateIn, RunCreatedOut, RunOut, RunPatchIn, RunRerunFromIn, RunResumeIn, RunStepTraceOut, RunSummaryOut
from app.services.artifacts import verify_run_artifact_download_token
from app.services.apps import public_run_graph, should_redact_app_source
from app.services.graph_validation import sanitize_prompt_template_tokens
from app.services.run_events import event_to_sse_frame, iter_run_events
from app.services.run_hub import StoredEvent, get_run_hub
from app.services.run_serializer import REDACTED_RUN_ERROR
from app.services.run_artifacts import find_run_artifact, list_run_artifacts
from app.services.run_trace import get_run_step_trace
from app.services.run_orchestrator import (
    cancel_run as cancel_run_signal,
    schedule_run,
)
from app.services.runs import (
    cancel_run_record,
    continue_run_record,
    create_run_record,
    create_rerun_from_record,
    delete_run_record,
    list_run_summaries_for_app,
    list_runs_for_app,
    load_run_or_404,
    resume_groups_for_waiting_step,
    submit_persisted_resume,
    submit_resume,
    update_run_name,
)
from app.utils import loads

logger = logging.getLogger(__name__)

router = APIRouter(tags=["runs"])


def _protected_event_transform(run: Run, app: App | None, user_id: str):
    if not should_redact_app_source(app, user_id):
        return None
    public_graph = public_run_graph(loads(run.graph_json, {"nodes": [], "edges": []}) or {"nodes": [], "edges": []})
    nodes_by_id = {
        node.get("id"): node
        for node in public_graph.get("nodes", [])
        if isinstance(node, dict) and isinstance(node.get("id"), str)
    }
    visible_node_ids = set(nodes_by_id)
    output_node_ids = {
        node_id for node_id, node in nodes_by_id.items()
        if node.get("type") == "output"
    }

    def transform(event_id: int, event: str, data: dict) -> tuple[str, dict] | None:
        node_id = data.get("node_id")
        if event == "run.end" and data.get("error"):
            return event, {**data, "error": REDACTED_RUN_ERROR}
        if event in {"step.start", "step.end"}:
            if not isinstance(node_id, str) or node_id not in visible_node_ids:
                return None
            if event == "step.end" and isinstance(data.get("step"), dict):
                step = dict(data["step"])
                step["input"] = None
                step["logs"] = []
                step["agent_session_id"] = None
                if step.get("error"):
                    step["error"] = REDACTED_RUN_ERROR
                if node_id not in output_node_ids:
                    step["output"] = None
                return event, {**data, "step": step}
            return event, data
        if event in {"step.delta", "step.log"}:
            return None
        return event, data

    return transform


def _stored_event_transform(transform):
    if transform is None:
        return None

    def apply(item: StoredEvent) -> StoredEvent | None:
        transformed = transform(item.id, item.event, item.data)
        if transformed is None:
            return None
        event, data = transformed
        return StoredEvent(id=item.id, event=event, data=data)

    return apply


@router.post("/runs", response_model=RunCreatedOut)
async def create_run(
    payload: RunCreateIn,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> RunCreatedOut:
    run_id, graph = await create_run_record(db, user.id, payload.app_id, payload.inputs)
    # 必须在派发后台任务前先把 channel 注册进 hub，否则 orchestrator
    # 启动竞速时 hub.get(run_id) 可能拿到 None 而直接 publish run.end failed。
    await get_run_hub().create(run_id)
    schedule_run(run_id)
    return RunCreatedOut(run_id=run_id, graph=sanitize_prompt_template_tokens(graph))


@router.post("/runs/{run_id}/rerun-from", response_model=RunCreatedOut)
async def rerun_from(
    run_id: str,
    payload: RunRerunFromIn,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> RunCreatedOut:
    new_run_id, graph = await create_rerun_from_record(
        db,
        user.id,
        run_id,
        payload.app_id,
        payload.node_id,
        payload.inputs,
        payload.condition_branch_override.model_dump(mode="json") if payload.condition_branch_override else None,
    )
    await get_run_hub().create(new_run_id)
    schedule_run(new_run_id, continuation=True)
    return RunCreatedOut(run_id=new_run_id, graph=sanitize_prompt_template_tokens(graph))


@router.get("/runs/{run_id}", response_model=RunOut)
async def get_run(
    run_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> RunOut:
    return await load_run_or_404(db, run_id, user.id)


@router.get("/runs/{run_id}/steps/{node_id}/trace", response_model=RunStepTraceOut)
async def get_run_step_trace_endpoint(
    run_id: str,
    node_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> RunStepTraceOut:
    return await get_run_step_trace(db, run_id, node_id, user.id)


@router.patch("/runs/{run_id}", response_model=RunOut)
async def patch_run(
    run_id: str,
    payload: RunPatchIn,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> RunOut:
    return await update_run_name(db, run_id, user.id, payload.name)


@router.get("/apps/{app_id}/runs", response_model=list[RunOut])
async def list_runs_for_app_endpoint(
    app_id: str,
    limit: int = Query(50, ge=1, le=200),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[RunOut]:
    return await list_runs_for_app(db, app_id, user.id, limit=limit)


@router.get("/apps/{app_id}/runs/summary", response_model=list[RunSummaryOut])
async def list_run_summaries_for_app_endpoint(
    app_id: str,
    limit: int = Query(50, ge=1, le=200),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[RunSummaryOut]:
    return await list_run_summaries_for_app(db, app_id, user.id, limit=limit)


@router.post("/runs/{run_id}/cancel", status_code=204)
async def cancel_run(
    run_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> None:
    row = (
        await db.execute(select(Run.status).where(Run.id == run_id, Run.owner_id == user.id))
    ).first()
    previous_status = row[0] if row is not None else None
    changed = await cancel_run_record(db, run_id, user.id)
    if changed:
        if previous_status == "waiting_for_user":
            channel = get_run_hub().get(run_id)
            if channel is not None:
                await channel.publish("run.end", {"status": "cancelled"})
            await get_run_hub().discard(run_id)
        else:
            cancel_run_signal(run_id)


@router.post("/runs/{run_id}/resume", status_code=204)
async def resume_run(
    run_id: str,
    payload: RunResumeIn,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> None:
    # service 校验：owner、status、附件归属、空 payload、文本长度。
    result = await submit_resume(db, user.id, run_id, payload)
    # 持久化 resume；若仍有并行节点在运行，则由当前 live orchestrator 接续。
    groups = await resume_groups_for_waiting_step(db, run_id, payload.node_id)
    should_schedule = await submit_persisted_resume(db, user.id, run_id, payload, result, groups)
    if not should_schedule:
        return
    await get_run_hub().discard(run_id)
    await get_run_hub().create(run_id)
    schedule_run(run_id, continuation=True)


@router.post("/runs/{run_id}/continue", response_model=RunOut)
async def continue_run(
    run_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> RunOut:
    run = await continue_run_record(db, run_id, user.id)
    await get_run_hub().discard(run_id)
    await get_run_hub().create(run_id)
    schedule_run(run_id, continuation=True)
    return run


@router.delete("/runs/{run_id}", status_code=204)
async def delete_run(
    run_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> None:
    await delete_run_record(db, run_id, user.id)
    await get_run_hub().discard(run_id)


@router.get("/runs/{run_id}/artifacts", response_model=RunArtifactsOut)
async def list_run_artifacts_endpoint(
    run_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> RunArtifactsOut:
    return await list_run_artifacts(db, run_id, user.id)


@router.get("/runs/{run_id}/artifacts/{relative_path:path}")
async def get_run_artifact(
    run_id: str,
    relative_path: str,
    download_token: str | None = Query(default=None),
    user: User | None = Depends(get_current_user_optional),
    db: AsyncSession = Depends(get_db),
) -> FileResponse:
    user_id = (
        verify_run_artifact_download_token(run_id, relative_path, download_token)
        if download_token
        else user.id if user else None
    )
    if user_id is None:
        raise HTTPException(status_code=401, detail="未登录")
    run = (
        await db.execute(select(Run).where(Run.id == run_id, Run.owner_id == user_id))
    ).scalar_one_or_none()
    if run is None:
        raise HTTPException(status_code=404, detail="运行记录不存在")
    artifact = await find_run_artifact(db, run, relative_path)
    if artifact is None:
        raise HTTPException(status_code=404, detail="文件不存在")
    if download_token:
        verify_run_artifact_download_token(
            run_id,
            relative_path,
            download_token,
            sha256=artifact.sha256,
            allow_missing_sha256=False,
        )
    if artifact.integrity == "modified":
        raise HTTPException(status_code=409, detail="文件完整性校验失败")
    disposition = "inline" if (artifact.mime or "").startswith("image/") else "attachment"
    return FileResponse(
        artifact.file_path,
        filename=artifact.file_path.name,
        content_disposition_type=disposition,
    )


@router.get("/runs/{run_id}/events")
async def stream_run_events(
    request: Request,
    run_id: str,
    last_event_id_header: Annotated[str | None, Header(alias="Last-Event-ID")] = None,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> StreamingResponse:
    # 先校验 run 归属，避免泄漏存在性。
    run = (
        await db.execute(select(Run).where(Run.id == run_id, Run.owner_id == user.id))
    ).scalar_one_or_none()
    if run is None:
        raise HTTPException(status_code=404, detail="运行记录不存在")
    app = await db.get(App, run.app_id)
    event_transform = _protected_event_transform(run, app, user.id)
    channel = get_run_hub().get(run_id)
    last_event_id: int | None = None
    if last_event_id_header:
        try:
            last_event_id = int(last_event_id_header.strip())
        except (TypeError, ValueError):
            raise HTTPException(status_code=410, detail="Last-Event-ID 无效") from None

    async def stream():
        subscription = await channel.subscribe() if channel is not None else None
        cursor = last_event_id
        try:
            async for row in iter_run_events(db, run_id, after_id=cursor):
                cursor = row.id
                if await request.is_disconnected():
                    return
                frame = event_to_sse_frame(row, transform=event_transform)
                if frame is not None:
                    yield frame.encode("utf-8")
            if channel is None or subscription is None:
                return
            async for chunk in channel.iter_live(
                subscription,
                cursor,
                _stored_event_transform(event_transform),
            ):
                if await request.is_disconnected():
                    return
                yield chunk
        finally:
            if channel is not None and subscription is not None:
                await channel.unsubscribe(subscription)

    headers = {
        "Cache-Control": "no-cache",
        "X-Accel-Buffering": "no",
        "Connection": "keep-alive",
    }
    if channel is None:
        headers["X-Mira-Replay-Only"] = "true"
    return StreamingResponse(stream(), media_type="text/event-stream", headers=headers)
