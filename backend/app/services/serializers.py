from __future__ import annotations

from app.models import App, AppVersion, Skill
from app.schemas import AppOut, AppVersionOut, SkillConfig
from app.services.apps import app_market_access, can_clone_app, can_edit_app, can_run_app, can_view_app_source, graph_for_viewer
from app.services.graph_validation import sanitize_prompt_template_tokens
from app.services.uploads import is_upload_id, resolve_upload
from app.utils import iso, loads


def app_to_out(app: App, *, viewer_id: str | None = None) -> AppOut:
    cover = app.cover if is_upload_id(app.cover) and resolve_upload(app.owner_id, app.cover) else None
    can_edit = can_edit_app(app, viewer_id)
    can_clone = can_clone_app(app, viewer_id)
    can_view_source = can_view_app_source(app, viewer_id)
    return AppOut(
        id=app.id,
        name=app.name,
        description=app.description,
        cover=cover,
        created_at=iso(app.created_at) or "",
        updated_at=iso(app.updated_at) or "",
        published_at=iso(app.published_at),
        archived_at=iso(app.archived_at),
        status=app.status,  # type: ignore[arg-type]
        visibility=app.visibility,  # type: ignore[arg-type]
        market_access=app_market_access(app),  # type: ignore[arg-type]
        can_edit=can_edit,
        can_clone=can_clone,
        can_run=can_run_app(app),
        can_view_source=can_view_source,
        graph=graph_for_viewer(app, viewer_id, loads(app.graph_json, {"nodes": [], "execution_edges": []})),
    )


def version_to_out(version: AppVersion) -> AppVersionOut:
    return AppVersionOut(
        id=version.id,
        app_id=version.app_id,
        label=version.label,
        name=version.name,
        description=version.description,
        graph=sanitize_prompt_template_tokens(loads(version.graph_json, {"nodes": [], "execution_edges": []})),
        created_at=iso(version.created_at) or "",
        is_published=version.is_published,
    )


def skill_to_config(skill: Skill) -> SkillConfig:
    return SkillConfig(
        id=skill.id,
        name=skill.name,
        description=skill.description,
        archive_name=skill.archive_name,
        archive_size=skill.archive_size,
        uploaded_at=iso(skill.uploaded_at) or "",
        enabled=skill.enabled,
        planning_enabled=skill.planning_enabled,
        dependency_status=skill.dependency_status,  # type: ignore[arg-type]
        dependency_error=skill.dependency_error,
    )
