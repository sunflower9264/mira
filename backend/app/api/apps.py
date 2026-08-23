from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.db import get_db
from app.models import App, Run, User
from app.schemas.requests import AppCreateIn, AppPatchIn, AppPublishIn, VersionCreateIn, WorkflowLintIn
from app.schemas.types import WorkflowLintIssueOut, WorkflowLintOut
from app.services.apps import (
    EMPTY_GRAPH,
    GALLERY_OWNER_ID,
    MARKET_ACCESS_CLONEABLE,
    app_recent_runs_filter,
    clone_gallery_or_visible_app,
    clone_owned_version,
    clone_visible_app,
    create_owned_version,
    delete_owned_app,
    get_visible_app_or_404,
    list_owned_versions,
    patch_owned_app,
    publish_owned_app,
    should_redact_app_source,
    unpublish_owned_app,
)
from app.services.graph_inputs import clean_graph_input
from app.services.serializers import app_to_out, version_to_out
from app.services.settings import settings_out
from app.services.uploads import is_upload_id, resolve_upload
from app.services.workflow_lint import lint_workflow
from app.utils import dumps, loads, new_id, now_utc

router = APIRouter(tags=["apps"])


def _redacted_lint_out(result: dict[str, Any]) -> WorkflowLintOut:
    lint = WorkflowLintOut.model_validate(result)
    if lint.ok:
        return WorkflowLintOut(ok=True, summary=lint.summary, issues=[])
    return WorkflowLintOut(
        ok=False,
        summary=lint.summary,
        issues=[
            WorkflowLintIssueOut(
                severity="error",
                code="protected_workflow_error",
                title="应用暂不可运行",
                detail="发布者需要修复应用后才能运行。",
            )
        ],
    )


@router.get("/apps")
async def list_apps(
    gallery: bool = Query(False),
    market: bool = Query(False),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if gallery:
        result = await db.execute(
            select(App)
            .where(
                App.owner_id == GALLERY_OWNER_ID,
                App.status == "published",
                App.archived_at.is_(None),
            )
            .order_by(App.updated_at.desc())
        )
        return [app_to_out(app, viewer_id=user.id) for app in result.scalars().all()]

    if market:
        result = await db.execute(
            select(App)
            .where(
                App.owner_id != GALLERY_OWNER_ID,
                App.status == "published",
                App.archived_at.is_(None),
                or_(
                    App.visibility == "public",
                    App.owner_id == user.id,
                ),
            )
            .order_by(App.updated_at.desc())
        )
        return [app_to_out(app, viewer_id=user.id) for app in result.scalars().all()]

    owner_id = user.id
    result = await db.execute(
        select(App).where(App.owner_id == owner_id, App.archived_at.is_(None)).order_by(App.updated_at.desc())
    )
    return [app_to_out(app, viewer_id=user.id) for app in result.scalars().all()]


@router.post("/apps")
async def create_app(payload: AppCreateIn, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    now = now_utc()
    app = App(
        id=new_id("app"),
        owner_id=user.id,
        name=(payload.name or "未命名 Mira 应用").strip() or "未命名 Mira 应用",
        description=payload.description or "",
        cover=None,
        status="draft",
        visibility="public",
        market_access=MARKET_ACCESS_CLONEABLE,
        graph_json=dumps(EMPTY_GRAPH),
        created_at=now,
        updated_at=now,
    )
    db.add(app)
    await db.commit()
    await db.refresh(app)
    return app_to_out(app, viewer_id=user.id)


@router.get("/apps/recent-runs")
async def list_recent_run_apps(
    limit: int = Query(8, ge=1, le=50),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    latest_runs = (
        select(
            Run.app_id.label("app_id"),
            func.max(Run.started_at).label("last_run_at"),
            func.max(Run.id).label("last_run_id"),
        )
        .where(Run.owner_id == user.id)
        .group_by(Run.app_id)
        .subquery()
    )
    result = await db.execute(
        select(App)
        .join(latest_runs, latest_runs.c.app_id == App.id)
        .where(app_recent_runs_filter(user.id))
        .order_by(latest_runs.c.last_run_at.desc().nulls_last(), latest_runs.c.last_run_id.desc())
        .limit(limit)
    )
    return [app_to_out(app, viewer_id=user.id) for app in result.scalars().all()]


@router.get("/apps/{app_id}")
async def get_app(app_id: str, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    return app_to_out(await get_visible_app_or_404(db, app_id, user.id), viewer_id=user.id)


@router.patch("/apps/{app_id}")
async def patch_app(
    app_id: str,
    payload: AppPatchIn,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    app = await patch_owned_app(db, app_id, user.id, payload.model_dump(exclude_unset=True))
    return app_to_out(app, viewer_id=user.id)


@router.get("/apps/{app_id}/cover")
async def get_app_cover(
    app_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> FileResponse:
    app = await get_visible_app_or_404(db, app_id, user.id)
    if not is_upload_id(app.cover):
        raise HTTPException(status_code=404, detail="封面不存在")
    ref = resolve_upload(app.owner_id, app.cover or "")
    if ref is None:
        raise HTTPException(status_code=404, detail="封面不存在")
    return FileResponse(ref.path, media_type=ref.mime, filename=ref.name)


@router.delete("/apps/{app_id}", status_code=204)
async def delete_app(app_id: str, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    await delete_owned_app(db, app_id, user.id)


@router.post("/apps/{app_id}/clone")
async def clone_app(app_id: str, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    return app_to_out(await clone_visible_app(db, app_id, user.id), viewer_id=user.id)


@router.post("/apps/clone/{template_id}")
async def clone_from_gallery(
    template_id: str, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
):
    return app_to_out(await clone_gallery_or_visible_app(db, template_id, user.id), viewer_id=user.id)


@router.get("/apps/{app_id}/versions")
async def list_versions(app_id: str, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    return [version_to_out(version) for version in await list_owned_versions(db, app_id, user.id)]


@router.post("/apps/{app_id}/versions")
async def create_app_version(
    app_id: str,
    payload: VersionCreateIn,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return version_to_out(await create_owned_version(db, app_id, user.id, payload.label))


@router.post("/apps/{app_id}/publish")
async def publish_app(
    app_id: str,
    payload: AppPublishIn | None = None,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    app, version = await publish_owned_app(
        db,
        app_id,
        user.id,
        visibility=payload.visibility if payload else None,
        market_access=payload.market_access if payload else None,
    )
    return {"app": app_to_out(app, viewer_id=user.id), "version": version_to_out(version)}


@router.post("/apps/{app_id}/lint", response_model=WorkflowLintOut)
async def lint_app(
    app_id: str,
    payload: WorkflowLintIn,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    app = await get_visible_app_or_404(db, app_id, user.id)
    redacted = should_redact_app_source(app, user.id)
    graph = clean_graph_input(
        loads(app.graph_json, EMPTY_GRAPH)
        if redacted
        else payload.graph
        if payload.graph is not None
        else loads(app.graph_json, EMPTY_GRAPH)
    )
    settings = await settings_out(db)
    enabled_agents = {agent.runtime for agent in settings.agents if agent.enabled}
    enabled_tool_ids = {tool.id for tool in settings.tools if tool.enabled}
    result = lint_workflow(graph, enabled_agents=enabled_agents, enabled_tool_ids=enabled_tool_ids)
    return _redacted_lint_out(result) if redacted else result


@router.post("/apps/{app_id}/unpublish")
async def unpublish_app(app_id: str, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    return app_to_out(await unpublish_owned_app(db, app_id, user.id), viewer_id=user.id)


@router.post("/versions/{version_id}/clone")
async def clone_from_version(
    version_id: str, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
):
    return app_to_out(await clone_owned_version(db, version_id, user.id), viewer_id=user.id)
