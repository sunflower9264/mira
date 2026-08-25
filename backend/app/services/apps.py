from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

from fastapi import HTTPException
from sqlalchemy import and_, delete, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import App, AppVersion, Run, Step, StepLog, User
from app.services.graph_inputs import (
    clean_graph_input,
    prepare_executable_graph,
    prepare_prompt_valid_graph,
    validate_asset_upload_ownership,
)
from app.services.graph_validation import GraphValidationError
from app.services.runtime_paths import remove_run_scoped_home, runtime_dir
from app.services.uploads import copy_upload, delete_upload, is_upload_id, resolve_upload, seed_upload_from_file
from app.utils import dumps, loads, new_id, now_utc

GALLERY_OWNER_ID = "system_gallery"
VERSION_LIMIT = 50
MARKET_ACCESS_CLONEABLE = "cloneable"
MARKET_ACCESS_RUN_ONLY = "run_only"

GALLERY_SEED_PATH = Path(__file__).resolve().parents[2] / "seeds" / "gallery.json"
GALLERY_ASSETS_DIR = GALLERY_SEED_PATH.parent / "assets"


EMPTY_GRAPH = {"nodes": [], "execution_edges": []}


async def seed_gallery(db: AsyncSession, seed_path: Path | None = None) -> None:
    seed_path = seed_path or GALLERY_SEED_PATH
    if not seed_path.exists():
        return
    templates = json.loads(seed_path.read_text(encoding="utf-8"))
    if not templates:
        return
    user = await db.get(User, GALLERY_OWNER_ID)
    if not user:
        db.add(User(id=GALLERY_OWNER_ID, username=GALLERY_OWNER_ID, password_hash=""))
    for tpl in templates:
        app_id = tpl["id"]
        cover = _seed_gallery_cover(tpl)
        existing = await db.get(App, app_id)
        if existing:
            existing.owner_id = GALLERY_OWNER_ID
            existing.source_template_id = None
            existing.name = tpl["name"]
            existing.description = tpl.get("description", "")
            existing.cover = cover
            existing.status = "published"
            existing.visibility = "public"
            existing.market_access = MARKET_ACCESS_CLONEABLE
            existing.archived_at = None
            existing.graph_json = dumps(tpl["graph"])
            existing.updated_at = now_utc()
            continue
        now = now_utc()
        db.add(
            App(
                id=app_id,
                owner_id=GALLERY_OWNER_ID,
                name=tpl["name"],
                description=tpl.get("description", ""),
                cover=cover,
                status="published",
                visibility="public",
                market_access=MARKET_ACCESS_CLONEABLE,
                published_at=now,
                graph_json=dumps(tpl["graph"]),
                created_at=now,
                updated_at=now,
            )
        )
    await db.commit()


def _seed_gallery_cover(tpl: dict[str, Any]) -> str | None:
    cover = tpl.get("cover")
    if not is_upload_id(cover):
        return None
    source = GALLERY_ASSETS_DIR / f"{tpl['id']}_cover.jpg"
    uploaded = seed_upload_from_file(
        GALLERY_OWNER_ID,
        cover,
        source,
        f"{tpl['id']}_cover.jpg",
        "image/jpeg",
    )
    return uploaded.id if uploaded else None


def clone_graph(
    graph: dict[str, Any],
    *,
    source_owner_id: str | None = None,
    target_owner_id: str | None = None,
) -> dict[str, Any]:
    cloned = clean_graph_input(loads(dumps(graph), EMPTY_GRAPH))
    if source_owner_id:
        validate_asset_upload_ownership(cloned, source_owner_id)
    should_copy_uploads = bool(source_owner_id and target_owner_id and source_owner_id != target_owner_id)
    for node in cloned.get("nodes", []):
        if not should_copy_uploads or not isinstance(node, dict) or node.get("type") != "asset":
            continue
        if node.get("asset_kind") == "file":
            uploads = node.get("uploads")
            copied_uploads: list[dict[str, Any]] = []
            if isinstance(uploads, list):
                for upload in uploads:
                    copied = _copy_graph_upload(source_owner_id or "", target_owner_id or "", upload)
                    if copied is not None:
                        copied_uploads.append(copied)
            node["uploads"] = copied_uploads
        elif node.get("asset_kind") == "drawing":
            node["upload"] = _copy_graph_upload(source_owner_id or "", target_owner_id or "", node.get("upload"))
    return cloned


def _copy_graph_upload(source_owner_id: str, target_owner_id: str, upload: Any) -> dict[str, Any] | None:
    upload_id = upload.get("id") if isinstance(upload, dict) else None
    if not isinstance(upload_id, str) or not upload_id:
        return None
    copied = copy_upload(source_owner_id, target_owner_id, upload_id)
    if copied is None:
        raise GraphValidationError("素材上传文件不存在")
    return copied.model_dump()


def app_market_access(app: App) -> str:
    access = getattr(app, "market_access", MARKET_ACCESS_CLONEABLE)
    return access if access in {MARKET_ACCESS_CLONEABLE, MARKET_ACCESS_RUN_ONLY} else MARKET_ACCESS_CLONEABLE


def is_app_archived(app: App | None) -> bool:
    return app is not None and getattr(app, "archived_at", None) is not None


def can_run_app(app: App | None) -> bool:
    return app is not None and not is_app_archived(app)


def can_clone_app(app: App, viewer_id: str | None) -> bool:
    if is_app_archived(app):
        return False
    if viewer_id is None or app.owner_id == viewer_id:
        return True
    return (
        app.status == "published"
        and app.visibility == "public"
        and app_market_access(app) == MARKET_ACCESS_CLONEABLE
    )


def can_edit_app(app: App, viewer_id: str | None) -> bool:
    return viewer_id is not None and not is_app_archived(app) and app.owner_id == viewer_id and app.owner_id != GALLERY_OWNER_ID


def can_view_app_source(app: App, viewer_id: str | None) -> bool:
    if is_app_archived(app):
        return False
    if viewer_id is None or app.owner_id == viewer_id:
        return True
    return app_market_access(app) != MARKET_ACCESS_RUN_ONLY


def should_redact_app_source(app: App | None, viewer_id: str | None) -> bool:
    if app is None:
        return False
    return not can_view_app_source(app, viewer_id)


def graph_for_viewer(app: App, viewer_id: str | None, graph: dict[str, Any] | None = None) -> dict[str, Any]:
    source = clean_graph_input(loads(dumps(graph if graph is not None else loads(app.graph_json, EMPTY_GRAPH)), EMPTY_GRAPH))
    if not should_redact_app_source(app, viewer_id):
        return source
    return public_run_graph(source)


def public_run_graph(graph: dict[str, Any]) -> dict[str, Any]:
    nodes: list[dict[str, Any]] = []
    for node in graph.get("nodes", []):
        if not isinstance(node, dict):
            continue
        node_id = node.get("id")
        node_type = node.get("type")
        if not isinstance(node_id, str):
            continue
        if node_type == "user_input":
            schema = node.get("input_schema") if isinstance(node.get("input_schema"), dict) else {}
            label = str(schema.get("label") or node.get("title") or "输入")
            nodes.append(
                {
                    "id": node_id,
                    "type": "user_input",
                    "position": node.get("position") if isinstance(node.get("position"), dict) else {"x": 0, "y": 0},
                    "title": label,
                    "description": "",
                    "input_schema": schema,
                }
            )
        elif node_type == "output":
            nodes.append(
                {
                    "id": node_id,
                    "type": "output",
                    "position": node.get("position") if isinstance(node.get("position"), dict) else {"x": 0, "y": 0},
                    "title": "输出",
                    "description": "",
                    "prompt": "",
                }
            )
    user_input_ids = [node["id"] for node in nodes if node.get("type") == "user_input"]
    output_ids = [node["id"] for node in nodes if node.get("type") == "output"]
    edges = [
        {"id": f"public_{user_input_ids[0]}_{output_id}", "source": user_input_ids[0], "target": output_id}
        for output_id in output_ids
        if user_input_ids
    ]
    return {"nodes": nodes, "execution_edges": edges}


async def get_owned_app_or_404(db: AsyncSession, app_id: str, owner_id: str) -> App:
    app = (
        await db.execute(
            select(App).where(
                App.id == app_id,
                App.owner_id == owner_id,
                App.owner_id != GALLERY_OWNER_ID,
                App.archived_at.is_(None),
            )
        )
    ).scalar_one_or_none()
    if not app:
        raise HTTPException(status_code=404, detail=f"找不到应用 {app_id}")
    return app


def visible_app_filter(user_id: str):
    return and_(
        App.archived_at.is_(None),
        or_(
            App.owner_id == user_id,
            and_(
                App.status == "published",
                App.visibility == "public",
            ),
        ),
    )


def archived_app_history_filter(user_id: str):
    return and_(
        App.archived_at.is_not(None),
        select(Run.id).where(Run.app_id == App.id, Run.owner_id == user_id).exists(),
    )


def visible_or_archived_history_app_filter(user_id: str):
    return or_(
        visible_app_filter(user_id),
        archived_app_history_filter(user_id),
    )


def app_recent_runs_filter(user_id: str):
    return or_(
        visible_app_filter(user_id),
        archived_app_history_filter(user_id),
    )


async def get_visible_app_or_404(db: AsyncSession, app_id: str, user_id: str) -> App:
    app = (
        await db.execute(select(App).where(App.id == app_id, visible_or_archived_history_app_filter(user_id)))
    ).scalar_one_or_none()
    if not app:
        raise HTTPException(status_code=404, detail=f"找不到应用 {app_id}")
    return app


async def patch_owned_app(db: AsyncSession, app_id: str, owner_id: str, data: dict[str, Any]) -> App:
    app = await get_owned_app_or_404(db, app_id, owner_id)
    if "status" in data:
        raise HTTPException(status_code=400, detail="发布状态请使用 /publish 或 /unpublish 修改")
    if "visibility" in data or "market_access" in data:
        raise HTTPException(status_code=400, detail="发布设置请使用 /publish 修改")
    raw_graph = data.get("graph") if data.get("graph") is not None else loads(app.graph_json, EMPTY_GRAPH)
    if "cover" in data and data["cover"] is not None:
        cover = str(data["cover"])
        if not is_upload_id(cover) or resolve_upload(owner_id, cover) is None:
            raise HTTPException(status_code=400, detail="封面图片不存在")
    if data.get("graph") is not None:
        next_graph = _prepare_prompt_valid_graph(raw_graph, owner_id)
    else:
        next_graph = clean_graph_input(raw_graph)
    for key in ("name", "description"):
        if key in data and data[key] is not None:
            setattr(app, key, data[key])
    if "cover" in data:
        app.cover = data["cover"]
    if "graph" in data and data["graph"] is not None:
        app.graph_json = dumps(next_graph)
    app.updated_at = now_utc()
    await db.commit()
    await db.refresh(app)
    return app


async def delete_owned_app(db: AsyncSession, app_id: str, owner_id: str) -> None:
    app = await get_owned_app_or_404(db, app_id, owner_id)
    await delete_app_tree(db, app)


async def delete_app_tree(db: AsyncSession, app: App) -> None:
    run_rows = (await db.execute(select(Run.id, Run.owner_id, Run.inputs_json).where(Run.app_id == app.id))).all()
    if any(owner_id != app.owner_id for _run_id, owner_id, _inputs_json in run_rows):
        app.status = "draft"
        app.visibility = "private"
        app.archived_at = now_utc()
        app.updated_at = now_utc()
        await db.commit()
        return
    run_ids = [run_id for run_id, _owner_id, _inputs_json in run_rows]
    upload_ids_by_owner: dict[str, set[str]] = {}
    for _run_id, owner_id, inputs_json in run_rows:
        inputs = loads(inputs_json, {}) or {}
        for value in inputs.values():
            if isinstance(value, dict):
                for ref in value.get("attachments") or []:
                    if isinstance(ref, dict) and isinstance(ref.get("id"), str):
                        upload_ids_by_owner.setdefault(owner_id, set()).add(ref["id"])
    if run_ids:
        step_ids = (await db.execute(select(Step.id).where(Step.run_id.in_(run_ids)))).scalars().all()
        if step_ids:
            await db.execute(delete(StepLog).where(StepLog.step_id.in_(step_ids)))
        await db.execute(delete(Step).where(Step.run_id.in_(run_ids)))
        await db.execute(delete(Run).where(Run.id.in_(run_ids)))
    await db.execute(delete(AppVersion).where(AppVersion.app_id == app.id))
    workspace = runtime_dir() / "workspaces" / app.owner_id / app.id
    shutil.rmtree(workspace, ignore_errors=True)
    await db.delete(app)
    await db.commit()
    for run_id in run_ids:
        remove_run_scoped_home(run_id)
        shutil.rmtree(runtime_dir() / "wiki-snapshots" / "runs" / run_id, ignore_errors=True)
    for owner_id, upload_ids in upload_ids_by_owner.items():
        for upload_id in upload_ids:
            delete_upload(owner_id, upload_id)


async def create_version(
    db: AsyncSession, app: App, label: str | None = None, is_published: bool = False
) -> AppVersion:
    version = AppVersion(
        id=new_id("ver"),
        app_id=app.id,
        label=label,
        name=app.name,
        description=app.description,
        graph_json=app.graph_json,
        is_published=is_published,
        created_at=now_utc(),
    )
    db.add(version)
    if not is_published:
        count = (
            await db.execute(
                select(func.count()).select_from(AppVersion).where(AppVersion.app_id == app.id)
            )
        ).scalar_one()
        if count >= VERSION_LIMIT:
            old = (
                await db.execute(
                    select(AppVersion)
                    .where(AppVersion.app_id == app.id, AppVersion.is_published.is_(False))
                    .order_by(AppVersion.created_at.asc())
                    .limit(1)
                )
            ).scalar_one_or_none()
            if old:
                await db.delete(old)
    await db.commit()
    await db.refresh(version)
    return version


async def list_owned_versions(db: AsyncSession, app_id: str, owner_id: str) -> list[AppVersion]:
    await get_owned_app_or_404(db, app_id, owner_id)
    result = await db.execute(select(AppVersion).where(AppVersion.app_id == app_id).order_by(AppVersion.created_at.desc()))
    return list(result.scalars().all())


async def create_owned_version(db: AsyncSession, app_id: str, owner_id: str, label: str | None) -> AppVersion:
    app = await get_owned_app_or_404(db, app_id, owner_id)
    return await create_version(db, app, label, False)


async def publish_owned_app(
    db: AsyncSession,
    app_id: str,
    owner_id: str,
    *,
    visibility: str | None = None,
    market_access: str | None = None,
) -> tuple[App, AppVersion]:
    app = await get_owned_app_or_404(db, app_id, owner_id)
    graph = _prepare_executable_graph(loads(app.graph_json, EMPTY_GRAPH), app.owner_id)
    app.graph_json = dumps(graph)
    count = (
        await db.execute(
            select(func.count()).select_from(AppVersion).where(AppVersion.app_id == app.id, AppVersion.is_published.is_(True))
        )
    ).scalar_one()
    app.status = "published"
    if visibility:
        app.visibility = visibility
    if market_access:
        app.market_access = market_access
    app.published_at = now_utc()
    app.updated_at = now_utc()
    version = await create_version(db, app, f"已发布 v{count + 1}", True)
    await db.refresh(app)
    return app, version


async def unpublish_owned_app(db: AsyncSession, app_id: str, owner_id: str) -> App:
    app = await get_owned_app_or_404(db, app_id, owner_id)
    app.status = "draft"
    app.updated_at = now_utc()
    await db.commit()
    await db.refresh(app)
    return app


async def clone_visible_app(db: AsyncSession, app_id: str, user_id: str) -> App:
    source = await get_visible_app_or_404(db, app_id, user_id)
    if not can_clone_app(source, user_id):
        raise HTTPException(status_code=403, detail="该应用不允许克隆")
    now = now_utc()
    app = App(
        id=new_id("app"),
        owner_id=user_id,
        name=f"{source.name}（副本）",
        description=source.description,
        cover=copy_cover_for_owner(source.owner_id, user_id, source.cover),
        status="draft",
        visibility="public",
        market_access=MARKET_ACCESS_CLONEABLE,
        published_at=None,
        graph_json=_clone_graph_json(loads(source.graph_json, EMPTY_GRAPH), source_owner_id=source.owner_id, target_owner_id=user_id),
        created_at=now,
        updated_at=now,
    )
    db.add(app)
    await db.commit()
    await db.refresh(app)
    return app


async def clone_gallery_or_visible_app(db: AsyncSession, template_id: str, user_id: str) -> App:
    template = await get_visible_app_or_404(db, template_id, user_id)
    if not can_clone_app(template, user_id):
        raise HTTPException(status_code=403, detail="该应用不允许克隆")
    if template.owner_id == GALLERY_OWNER_ID:
        existing = await get_imported_template_app(db, user_id, template_id)
        if existing:
            return existing
    now = now_utc()
    app = App(
        id=new_id("app"),
        owner_id=user_id,
        source_template_id=template_id if template.owner_id == GALLERY_OWNER_ID else None,
        name=f"{template.name} Remix" if template.owner_id == GALLERY_OWNER_ID else f"{template.name}（副本）",
        description=template.description,
        cover=copy_cover_for_owner(template.owner_id, user_id, template.cover),
        status="draft",
        visibility="public",
        market_access=MARKET_ACCESS_CLONEABLE,
        graph_json=_clone_graph_json(loads(template.graph_json, EMPTY_GRAPH), source_owner_id=template.owner_id, target_owner_id=user_id),
        created_at=now,
        updated_at=now,
    )
    db.add(app)
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        if template.owner_id == GALLERY_OWNER_ID:
            existing = await get_imported_template_app(db, user_id, template_id)
            if existing:
                return existing
        raise exc
    await db.refresh(app)
    return app


async def clone_owned_version(db: AsyncSession, version_id: str, owner_id: str) -> App:
    version = (await db.execute(select(AppVersion).where(AppVersion.id == version_id))).scalar_one_or_none()
    if not version:
        raise HTTPException(status_code=404, detail=f"找不到版本 {version_id}")
    source_app = await get_owned_app_or_404(db, version.app_id, owner_id)
    versions = (
        await db.execute(select(AppVersion.id).where(AppVersion.app_id == source_app.id).order_by(AppVersion.created_at.asc()))
    ).scalars().all()
    ordinal = versions.index(version_id) + 1 if version_id in versions else 1
    now = now_utc()
    app = App(
        id=new_id("app"),
        owner_id=owner_id,
        name=f"{version.name}（v{ordinal}）",
        description=version.description,
        cover=copy_cover_for_owner(source_app.owner_id, owner_id, source_app.cover),
        status="draft",
        visibility="public",
        market_access=MARKET_ACCESS_CLONEABLE,
        graph_json=_clone_graph_json(loads(version.graph_json, EMPTY_GRAPH), source_owner_id=source_app.owner_id, target_owner_id=owner_id),
        created_at=now,
        updated_at=now,
    )
    db.add(app)
    await db.commit()
    await db.refresh(app)
    return app


async def get_imported_template_app(db: AsyncSession, owner_id: str, template_id: str) -> App | None:
    return (
        await db.execute(select(App).where(App.owner_id == owner_id, App.source_template_id == template_id))
    ).scalar_one_or_none()


def _prepare_prompt_valid_graph(graph: dict[str, Any], owner_id: str) -> dict[str, Any]:
    try:
        return prepare_prompt_valid_graph(graph, owner_id=owner_id)
    except GraphValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None


def _prepare_executable_graph(graph: dict[str, Any], owner_id: str) -> dict[str, Any]:
    try:
        return prepare_executable_graph(graph, owner_id=owner_id)
    except GraphValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None


def _clone_graph_json(graph: dict[str, Any], *, source_owner_id: str, target_owner_id: str) -> str:
    try:
        return dumps(clone_graph(graph, source_owner_id=source_owner_id, target_owner_id=target_owner_id))
    except GraphValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None


def copy_cover_for_owner(source_owner_id: str, target_owner_id: str, cover: str | None) -> str | None:
    if not is_upload_id(cover):
        return None
    if source_owner_id == target_owner_id:
        return cover if resolve_upload(source_owner_id, cover) else None
    copied = copy_upload(source_owner_id, target_owner_id, cover)
    return copied.id if copied else None
