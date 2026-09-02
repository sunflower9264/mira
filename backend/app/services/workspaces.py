from __future__ import annotations

import asyncio
import hashlib
import mimetypes
import os
import shutil
import stat
import tempfile
import zipfile
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import urlparse

from cryptography.fernet import Fernet, InvalidToken
from fastapi import HTTPException, UploadFile
from sqlalchemy import delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import (
    App,
    WikiRevision,
    Workspace,
    WorkspaceEvent,
    WorkspaceGitConfig,
    WorkspaceSession,
    WorkspaceTurn,
    WorkspaceWorkflowProposal,
)
from app.runtime.base import AgentChunk, DecisionAttachment, DecisionResult
from app.schemas import (
    WorkspaceEventOut,
    WorkspaceFileOut,
    WorkspaceFilePreviewOut,
    WorkspaceFilesOut,
    WorkspaceGitConfigOut,
    WorkspaceOut,
    WorkspaceSessionOut,
    WorkspaceTurnOut,
    WorkspaceWorkflowProposalOut,
)
from app.services.apps import EMPTY_GRAPH, get_owned_app_or_404, patch_owned_app
from app.services.decision_prompts import validate_decision_answers
from app.services.graph_inputs import clean_graph_input, prepare_prompt_valid_graph
from app.services.graph_validation import GraphValidationError
from app.services.settings import get_or_create_settings_row
from app.services.uploads import resolve_upload
from app.services.wiki import (
    _publish_tree,
    _revision_tree,
    _scan_tree,
    _tree_hash,
    _validate_wiki_tree,
    get_or_create_wiki,
)
from app.services.workflow_lint import lint_workflow
from app.utils import dumps, iso, loads, new_id, now_utc


WORKSPACE_LIMIT = 10
WORKSPACE_PREVIEW_BYTES = 1_000_000
WORKSPACE_ZIP_FILE_LIMIT = 10_000
WORKSPACE_ZIP_EXPANDED_LIMIT = 500_000_000
WORKSPACE_HIDDEN_ROOTS = {".git", ".mira", ".playwright-cli"}
WORKSPACE_TEXT_SUFFIXES = {
    ".c", ".cc", ".cpp", ".css", ".csv", ".go", ".h", ".hpp", ".html", ".ini",
    ".java", ".js", ".json", ".jsx", ".md", ".py", ".rs", ".sh", ".sql", ".svg",
    ".toml", ".ts", ".tsx", ".txt", ".xml", ".yaml", ".yml",
}


def workspace_project_path(owner_id: str, workspace_id: str) -> Path:
    return get_settings().runtime_dir.resolve() / "persistent-workspaces" / owner_id / workspace_id / "project"


def workspace_metadata_path(owner_id: str, workspace_id: str) -> Path:
    return get_settings().runtime_dir.resolve() / "persistent-workspaces" / owner_id / workspace_id / ".mira"


def workspace_wiki_working_tree_path(owner_id: str, workspace_id: str) -> Path:
    return workspace_metadata_path(owner_id, workspace_id) / "wiki" / "working-tree"


async def get_owned_workspace_or_404(db: AsyncSession, workspace_id: str, owner_id: str) -> Workspace:
    row = (
        await db.execute(select(Workspace).where(Workspace.id == workspace_id, Workspace.owner_id == owner_id))
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="工作空间不存在")
    return row


async def list_workspaces(db: AsyncSession, owner_id: str) -> list[WorkspaceOut]:
    rows = (
        await db.execute(
            select(Workspace).where(Workspace.owner_id == owner_id).order_by(Workspace.updated_at.desc(), Workspace.id.desc())
        )
    ).scalars().all()
    return [_workspace_out(row) for row in rows]


async def get_workspace(db: AsyncSession, workspace_id: str, owner_id: str) -> WorkspaceOut:
    return _workspace_out(await get_owned_workspace_or_404(db, workspace_id, owner_id))


async def create_workspace(
    db: AsyncSession,
    owner_id: str,
    *,
    name: str,
    description: str,
    source: dict[str, Any],
) -> WorkspaceOut:
    count = await db.scalar(select(func.count()).select_from(Workspace).where(Workspace.owner_id == owner_id))
    if int(count or 0) >= WORKSPACE_LIMIT:
        raise HTTPException(status_code=409, detail=f"每个用户最多创建 {WORKSPACE_LIMIT} 个工作空间")
    workspace_id = new_id("ws")
    project = workspace_project_path(owner_id, workspace_id)
    project.mkdir(parents=True, exist_ok=False)
    now = now_utc()
    row = Workspace(
        id=workspace_id,
        owner_id=owner_id,
        name=_required_text(name, "名称", 120),
        description=_bounded_text(description, "描述", 4000),
        project_relpath=project.relative_to(get_settings().runtime_dir.resolve()).as_posix(),
        runtime_status="stopped",
        wiki_sync_status="pending",
        created_at=now,
        updated_at=now,
    )
    db.add(row)
    try:
        await db.flush()
        if source.get("kind") == "git":
            await save_workspace_git_config(
                db,
                row,
                repository_url=str(source.get("repository_url") or ""),
                default_branch=str(source.get("default_branch") or "main"),
                access_token=source.get("access_token"),
                commit=False,
            )
        await db.commit()
    except Exception:
        await db.rollback()
        shutil.rmtree(project.parent, ignore_errors=True)
        raise
    return _workspace_out(row)


async def patch_workspace(
    db: AsyncSession,
    workspace_id: str,
    owner_id: str,
    *,
    name: str | None,
    description: str | None,
) -> WorkspaceOut:
    row = await get_owned_workspace_or_404(db, workspace_id, owner_id)
    if name is not None:
        row.name = _required_text(name, "名称", 120)
    if description is not None:
        row.description = _bounded_text(description, "描述", 4000)
    row.updated_at = now_utc()
    await db.commit()
    return _workspace_out(row)


async def delete_workspace(db: AsyncSession, workspace_id: str, owner_id: str) -> None:
    row = await get_owned_workspace_or_404(db, workspace_id, owner_id)
    try:
        from app.runtime.workspace_runtime import stop_workspace_runtime

        await stop_workspace_runtime(row.id)
    except (ImportError, ModuleNotFoundError):
        pass
    root = workspace_project_path(owner_id, workspace_id).parent
    await db.execute(
        delete(WorkspaceEvent).where(WorkspaceEvent.workspace_id == workspace_id)
    )
    await db.execute(
        delete(WorkspaceWorkflowProposal).where(
            WorkspaceWorkflowProposal.workspace_id == workspace_id
        )
    )
    await db.execute(
        delete(WorkspaceTurn).where(WorkspaceTurn.workspace_id == workspace_id)
    )
    await db.execute(
        delete(WorkspaceSession).where(WorkspaceSession.workspace_id == workspace_id)
    )
    await db.execute(
        delete(WorkspaceGitConfig).where(WorkspaceGitConfig.workspace_id == workspace_id)
    )
    await db.delete(row)
    await db.commit()
    shutil.rmtree(root, ignore_errors=True)


async def update_workspace_runtime_state(
    db: AsyncSession,
    *,
    workspace_id: str,
    status: str,
    container_id: str | None = None,
    started_at=None,
    last_error: str | None = None,
) -> None:
    row = await db.get(Workspace, workspace_id)
    if row is None:
        return
    row.runtime_status = status
    row.container_id = container_id
    row.runtime_started_at = started_at
    row.runtime_last_error = last_error
    row.updated_at = now_utc()
    await db.commit()


async def list_workspace_sessions(
    db: AsyncSession,
    workspace_id: str,
    owner_id: str,
    *,
    limit: int = 30,
    offset: int = 0,
    query: str | None = None,
) -> tuple[list[WorkspaceSessionOut], bool, int | None]:
    await get_owned_workspace_or_404(db, workspace_id, owner_id)
    limit = max(1, min(limit, 100))
    offset = max(0, offset)
    normalized_query = (query or "").strip()
    content_match = None
    if normalized_query:
        message_text = func.json_extract(WorkspaceEvent.payload_json, "$.text")
        content_match = select(WorkspaceEvent.session_id).where(
            WorkspaceEvent.session_id == WorkspaceSession.id,
            WorkspaceEvent.event_type == "message_completed",
            message_text.icontains(normalized_query, autoescape=True),
        ).exists()
    rows = (
        await db.execute(
            select(WorkspaceSession)
            .where(WorkspaceSession.workspace_id == workspace_id)
            .where(
                or_(
                    WorkspaceSession.title.icontains(normalized_query, autoescape=True),
                    content_match,
                )
                if normalized_query
                else True
            )
            .order_by(WorkspaceSession.updated_at.desc(), WorkspaceSession.id.desc())
            .offset(offset)
            .limit(limit + 1)
        )
    ).scalars().all()
    has_more = len(rows) > limit
    visible = rows[:limit]
    items: list[WorkspaceSessionOut] = []
    for row in visible:
        context = None
        if normalized_query and normalized_query.lower() not in row.title.lower():
            payload = await db.scalar(
                select(WorkspaceEvent.payload_json)
                .where(
                    WorkspaceEvent.session_id == row.id,
                    WorkspaceEvent.event_type == "message_completed",
                    func.json_extract(WorkspaceEvent.payload_json, "$.text").icontains(
                        normalized_query, autoescape=True
                    ),
                )
                .order_by(WorkspaceEvent.id.desc())
                .limit(1)
            )
            if isinstance(payload, str):
                data = loads(payload, {}) or {}
                text = str(data.get("text") or "") if isinstance(data, dict) else ""
                lowered = text.lower()
                index = lowered.find(normalized_query.lower())
                context = text[max(0, index - 36): index + len(normalized_query) + 72] if index >= 0 else None
        items.append(_session_out(row, match_context=context))
    return items, has_more, offset + len(visible) if has_more else None


async def create_workspace_session(
    db: AsyncSession, workspace_id: str, owner_id: str, title: str
) -> WorkspaceSessionOut:
    workspace = await get_owned_workspace_or_404(db, workspace_id, owner_id)
    now = now_utc()
    row = WorkspaceSession(
        id=new_id("wss"),
        workspace_id=workspace.id,
        title=_normalized_title(title),
        status="idle",
        created_at=now,
        updated_at=now,
    )
    db.add(row)
    workspace.updated_at = now
    await db.commit()
    return _session_out(row)


async def get_owned_workspace_session_or_404(
    db: AsyncSession, session_id: str, owner_id: str
) -> tuple[Workspace, WorkspaceSession]:
    result = await db.execute(
        select(Workspace, WorkspaceSession)
        .join(WorkspaceSession, WorkspaceSession.workspace_id == Workspace.id)
        .where(WorkspaceSession.id == session_id, Workspace.owner_id == owner_id)
    )
    item = result.one_or_none()
    if item is None:
        raise HTTPException(status_code=404, detail="工作空间会话不存在")
    return item[0], item[1]


async def patch_workspace_session(
    db: AsyncSession, session_id: str, owner_id: str, title: str
) -> WorkspaceSessionOut:
    _workspace, row = await get_owned_workspace_session_or_404(db, session_id, owner_id)
    row.title = _normalized_title(title)
    row.updated_at = now_utc()
    await db.commit()
    return _session_out(row)


async def delete_workspace_session(db: AsyncSession, session_id: str, owner_id: str) -> None:
    _workspace, row = await get_owned_workspace_session_or_404(db, session_id, owner_id)
    if row.status in {"running", "waiting"}:
        raise HTTPException(status_code=409, detail="会话正在执行，不能删除")
    await db.delete(row)
    await db.commit()


async def append_workspace_event(
    db: AsyncSession,
    *,
    workspace_id: str,
    session_id: str,
    turn_id: str | None,
    event_type: str,
    payload: dict[str, Any],
) -> WorkspaceEvent:
    row = WorkspaceEvent(
        workspace_id=workspace_id,
        session_id=session_id,
        turn_id=turn_id,
        event_type=event_type[:80],
        payload_json=dumps(_sanitize_workspace_event_payload(event_type, payload)),
        created_at=now_utc(),
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return row


async def list_workspace_events(
    db: AsyncSession, session_id: str, owner_id: str, *, after_id: int | None = None
) -> list[WorkspaceEventOut]:
    _workspace, session = await get_owned_workspace_session_or_404(db, session_id, owner_id)
    query = select(WorkspaceEvent).where(WorkspaceEvent.session_id == session.id)
    if after_id is not None:
        query = query.where(WorkspaceEvent.id > after_id)
    rows = (await db.execute(query.order_by(WorkspaceEvent.id.asc()).limit(1000))).scalars().all()
    return [_event_out(row) for row in rows]


async def create_workspace_turn(
    db: AsyncSession,
    *,
    workspace_id: str,
    session_id: str,
    prompt: str,
    model: str | None,
    reasoning_effort: str | None,
    attachments: list[dict[str, str | None]] | None = None,
) -> WorkspaceTurn:
    active = await db.scalar(
        select(func.count()).select_from(WorkspaceTurn).where(
            WorkspaceTurn.workspace_id == workspace_id,
            WorkspaceTurn.status.in_({"pending", "running", "waiting"}),
        )
    )
    if active:
        raise HTTPException(status_code=409, detail="该工作空间已有任务正在执行")
    turn_id = new_id("wturn")
    prompt_text = _required_text(prompt, "消息", 1_000_000)
    display_text = prompt_text
    attachment_paths: list[str] = []
    attachment_items: list[dict[str, Any]] = []
    if attachments:
        workspace = await db.get(Workspace, workspace_id)
        if workspace is None:
            raise HTTPException(status_code=404, detail="工作空间不存在")
        destination_root = _verified_project_root(workspace) / "inputs" / turn_id
        for ref in attachments:
            resolved = resolve_upload(workspace.owner_id, str(ref.get("id") or ""))
            if resolved is None:
                raise HTTPException(status_code=404, detail="附件不存在")
            name = PurePosixPath(str(ref.get("name") or resolved.name).replace("\\", "/")).name
            destination_root.mkdir(parents=True, exist_ok=True)
            destination = destination_root / name
            shutil.copy2(resolved.path, destination)
            attachment_paths.append(destination.relative_to(_verified_project_root(workspace)).as_posix())
            attachment_items.append(
                {
                    "id": resolved.id,
                    "name": name,
                    "mime": resolved.mime,
                    "size": resolved.size,
                }
            )
    if attachment_paths:
        prompt_text += "\n\nWorkspace 附件：\n" + "\n".join(f"- {path}" for path in attachment_paths)
    row = WorkspaceTurn(
        id=turn_id,
        workspace_id=workspace_id,
        session_id=session_id,
        status="pending",
        prompt=prompt_text,
        model=model,
        reasoning_effort=reasoning_effort,
        created_at=now_utc(),
    )
    db.add(row)
    db.add(
        WorkspaceEvent(
            workspace_id=workspace_id,
            session_id=session_id,
            turn_id=turn_id,
            event_type="message_completed",
            payload_json=dumps(
                {"role": "user", "text": display_text, "attachments": attachment_items}
            ),
            created_at=now_utc(),
        )
    )
    await db.flush()
    return row


async def get_owned_workspace_turn_or_404(
    db: AsyncSession, turn_id: str, owner_id: str
) -> tuple[Workspace, WorkspaceSession, WorkspaceTurn]:
    result = await db.execute(
        select(Workspace, WorkspaceSession, WorkspaceTurn)
        .join(WorkspaceSession, WorkspaceSession.workspace_id == Workspace.id)
        .join(WorkspaceTurn, WorkspaceTurn.session_id == WorkspaceSession.id)
        .where(WorkspaceTurn.id == turn_id, Workspace.owner_id == owner_id)
    )
    item = result.one_or_none()
    if item is None:
        raise HTTPException(status_code=404, detail="Workspace turn 不存在")
    return item[0], item[1], item[2]


def workspace_turn_out(turn: WorkspaceTurn) -> WorkspaceTurnOut:
    return WorkspaceTurnOut(
        id=turn.id,
        workspace_id=turn.workspace_id,
        session_id=turn.session_id,
        status=turn.status,
        model=turn.model,
        reasoning_effort=turn.reasoning_effort,
        started_at=iso(turn.started_at),
        finished_at=iso(turn.finished_at),
        error=turn.error,
        created_at=iso(turn.created_at) or "",
    )


async def build_workspace_decision_result(
    db: AsyncSession,
    *,
    turn: WorkspaceTurn,
    owner_id: str,
    request_id: str,
    answers,
    text: str | None,
    attachments,
) -> DecisionResult:
    if turn.status != "waiting":
        raise HTTPException(status_code=409, detail="Workspace turn 当前不在等待状态")
    event = (
        await db.execute(
            select(WorkspaceEvent)
            .where(
                WorkspaceEvent.turn_id == turn.id,
                WorkspaceEvent.event_type == "decision_request",
            )
            .order_by(WorkspaceEvent.id.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    payload = loads(event.payload_json, {}) if event else {}
    if payload.get("request_id") != request_id:
        raise HTTPException(status_code=409, detail="提问请求已失效")
    from app.schemas.decision import DecisionGroup

    try:
        groups = [DecisionGroup.model_validate(item) for item in payload.get("groups", [])]
    except Exception:
        raise HTTPException(status_code=409, detail="提问请求记录无效") from None
    answer_error = validate_decision_answers(groups, list(answers or [])) if answers else None
    normalized_text = (text or "").strip() or None
    if answer_error and not normalized_text:
        raise HTTPException(status_code=400, detail=answer_error)
    if not answers and not normalized_text and not attachments:
        raise HTTPException(status_code=400, detail="必须至少提供一项输入")
    if normalized_text and len(normalized_text.encode("utf-8")) > get_settings().max_resume_text_bytes:
        raise HTTPException(status_code=400, detail="补充文本过长")
    resolved_attachments: list[DecisionAttachment] = []
    for ref in attachments or []:
        resolved = resolve_upload(owner_id, ref.id)
        if resolved is None:
            raise HTTPException(status_code=404, detail="附件不存在")
        resolved_attachments.append(
            DecisionAttachment(
                id=resolved.id,
                name=ref.name or resolved.name,
                path=str(resolved.path),
                download_url=resolved.download_url(owner_id),
                mime=resolved.mime,
                size=resolved.size,
            )
        )
    return DecisionResult(
        ok=True,
        answers=list(answers or []),
        text=normalized_text,
        attachments=resolved_attachments,
    )


async def finish_workspace_turn(
    db: AsyncSession, turn_id: str, *, status: str, error: str | None = None
) -> None:
    turn = await db.get(WorkspaceTurn, turn_id)
    if turn is None:
        return
    turn.status = status
    turn.error = error
    turn.finished_at = now_utc()
    session = await db.get(WorkspaceSession, turn.session_id)
    if session is not None:
        session.status = "idle" if status in {"success", "cancelled"} else "error"
        session.last_turn_at = turn.finished_at
        session.updated_at = turn.finished_at
    await db.commit()


async def list_workspace_files(db: AsyncSession, workspace_id: str, owner_id: str) -> WorkspaceFilesOut:
    workspace = await get_owned_workspace_or_404(db, workspace_id, owner_id)
    root = _verified_project_root(workspace)
    files: list[WorkspaceFileOut] = []
    for current in sorted(root.rglob("*"), key=lambda path: path.relative_to(root).as_posix().lower()):
        relative = current.relative_to(root)
        if relative.parts and relative.parts[0] in WORKSPACE_HIDDEN_ROOTS:
            continue
        if current.is_symlink() or (not current.is_file() and not current.is_dir()):
            continue
        info = current.stat()
        files.append(
            WorkspaceFileOut(
                path=relative.as_posix(),
                name=current.name,
                kind="directory" if current.is_dir() else "file",
                size=0 if current.is_dir() else info.st_size,
                mime=None if current.is_dir() else (mimetypes.guess_type(current.name)[0] or "application/octet-stream"),
                updated_at=iso(datetime.fromtimestamp(info.st_mtime, tz=UTC)),
            )
        )
    return WorkspaceFilesOut(files=files)


async def preview_workspace_file(
    db: AsyncSession, workspace_id: str, owner_id: str, relative_path: str
) -> WorkspaceFilePreviewOut:
    workspace = await get_owned_workspace_or_404(db, workspace_id, owner_id)
    target = _safe_project_file(_verified_project_root(workspace), relative_path)
    if not target.is_file() or target.is_symlink():
        raise HTTPException(status_code=404, detail="文件不存在")
    size = target.stat().st_size
    mime = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
    content: str | None = None
    if target.suffix.lower() in WORKSPACE_TEXT_SUFFIXES and size <= WORKSPACE_PREVIEW_BYTES:
        try:
            content = target.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            content = None
    return WorkspaceFilePreviewOut(
        path=target.relative_to(_verified_project_root(workspace)).as_posix(),
        mime=mime,
        size=size,
        content=content,
        download_url=f"/api/workspaces/{workspace.id}/files/download?path={target.relative_to(_verified_project_root(workspace)).as_posix()}",
    )


async def resolve_workspace_download(
    db: AsyncSession, workspace_id: str, owner_id: str, relative_path: str
) -> Path:
    workspace = await get_owned_workspace_or_404(db, workspace_id, owner_id)
    target = _safe_project_file(_verified_project_root(workspace), relative_path)
    if not target.is_file() or target.is_symlink():
        raise HTTPException(status_code=404, detail="文件不存在")
    return target


async def upload_workspace_files(
    db: AsyncSession,
    workspace_id: str,
    owner_id: str,
    files: list[UploadFile],
    target_path: str | None,
) -> WorkspaceFilesOut:
    workspace = await get_owned_workspace_or_404(db, workspace_id, owner_id)
    root = _verified_project_root(workspace)
    target_dir = root if not target_path else _safe_project_file(root, target_path, allow_directory=True)
    target_dir.mkdir(parents=True, exist_ok=True)
    if target_dir.is_symlink() or not target_dir.is_dir():
        raise HTTPException(status_code=400, detail="上传目录无效")
    for upload in files:
        name = PurePosixPath((upload.filename or "file").replace("\\", "/")).name
        if not name or name in {".", ".."}:
            raise HTTPException(status_code=400, detail="上传文件名无效")
        raw = await upload.read()
        if not raw:
            raise HTTPException(status_code=400, detail="上传文件为空")
        if len(raw) > get_settings().max_upload_bytes:
            raise HTTPException(status_code=413, detail="文件超出大小限制")
        if name.lower().endswith(".zip") or upload.content_type in {"application/zip", "application/x-zip-compressed"}:
            await asyncio.to_thread(_extract_safe_zip, raw, target_dir)
            continue
        destination = _safe_project_file(target_dir, name)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(raw)
    workspace.updated_at = now_utc()
    await db.commit()
    return await list_workspace_files(db, workspace_id, owner_id)


async def workspace_git_config_out(
    db: AsyncSession, workspace_id: str, owner_id: str
) -> WorkspaceGitConfigOut:
    workspace = await get_owned_workspace_or_404(db, workspace_id, owner_id)
    row = await db.get(WorkspaceGitConfig, workspace.id)
    return WorkspaceGitConfigOut(
        repository_url=row.repository_url if row else None,
        default_branch=row.default_branch if row else None,
        token_configured=bool(row and row.token_ciphertext),
        allowed_hosts=await workspace_git_allowed_hosts(db),
    )


async def workspace_git_allowed_hosts(db: AsyncSession) -> list[str]:
    row = await get_or_create_settings_row(db)
    return [str(item) for item in loads(row.workspace_git_allowed_hosts_json, []) if isinstance(item, str)]


async def save_workspace_git_config(
    db: AsyncSession,
    workspace: Workspace,
    *,
    repository_url: str,
    default_branch: str,
    access_token: str | None,
    commit: bool = True,
) -> WorkspaceGitConfigOut:
    repository_url, _host = await _validate_git_url(db, repository_url)
    branch = _required_text(default_branch, "默认分支", 255)
    if (
        any(char.isspace() or char in "~^:?*[\\" for char in branch)
        or branch.startswith(("-", "."))
        or branch.endswith(("/", ".", ".lock"))
        or ".." in branch
        or "@{" in branch
        or "//" in branch
    ):
        raise HTTPException(status_code=400, detail="Git 默认分支无效")
    row = await db.get(WorkspaceGitConfig, workspace.id)
    now = now_utc()
    if row is None:
        row = WorkspaceGitConfig(
            workspace_id=workspace.id,
            repository_url=repository_url,
            default_branch=branch,
            created_at=now,
            updated_at=now,
        )
        db.add(row)
    else:
        row.repository_url = repository_url
        row.default_branch = branch
        row.updated_at = now
    if access_token is not None:
        row.token_ciphertext = _encrypt_secret(access_token) if access_token else None
    workspace.updated_at = now
    if commit:
        await db.commit()
    else:
        await db.flush()
    return WorkspaceGitConfigOut(
        repository_url=row.repository_url,
        default_branch=row.default_branch,
        token_configured=bool(row.token_ciphertext),
        allowed_hosts=await workspace_git_allowed_hosts(db),
    )


async def get_workspace_git_credentials(
    db: AsyncSession, workspace_id: str
) -> tuple[str, str, str | None] | None:
    row = await db.get(WorkspaceGitConfig, workspace_id)
    if row is None:
        return None
    return row.repository_url, row.default_branch, _decrypt_secret(row.token_ciphertext) if row.token_ciphertext else None


async def prepare_workspace_wiki_copy(db: AsyncSession, workspace: Workspace) -> dict[str, Any]:
    wiki = await get_or_create_wiki(db, workspace.owner_id)
    if not wiki.current_revision_id:
        raise HTTPException(status_code=409, detail="Wiki 尚无可用版本")
    current = await db.get(WikiRevision, wiki.current_revision_id)
    if current is None:
        raise HTTPException(status_code=409, detail="Wiki 当前版本不存在")
    metadata = workspace_metadata_path(workspace.owner_id, workspace.id) / "wiki"
    working_tree = workspace_wiki_working_tree_path(workspace.owner_id, workspace.id)
    base_tree = metadata / "base"
    source_tree = _revision_tree(workspace.owner_id, current.id)
    workspace.wiki_sync_status = "syncing"
    workspace.wiki_sync_error = None
    await db.commit()
    try:
        if not workspace.wiki_base_revision_id or not base_tree.is_dir():
            _sync_working_tree_in_place(source_tree, working_tree)
            _replace_wiki_tree(source_tree, base_tree)
            workspace.wiki_base_revision_id = current.id
        elif workspace.wiki_base_revision_id != current.id:
            _validate_workspace_wiki_changes(base_tree, working_tree)
            if _directory_hash(working_tree) != _directory_hash(base_tree):
                proposed = metadata / "proposed" / new_id("wiki")
                _replace_wiki_tree(working_tree, proposed)
                workspace.wiki_sync_status = "conflict"
                workspace.wiki_sync_error = "Wiki 已在其他工作空间更新，需要三方合并"
                workspace.wiki_proposed_relpath = proposed.relative_to(metadata.parent).as_posix()
                await db.commit()
                return _wiki_sync_result(workspace)
            _sync_working_tree_in_place(source_tree, working_tree)
            _replace_wiki_tree(source_tree, base_tree)
            workspace.wiki_base_revision_id = current.id
        workspace.wiki_sync_status = "ready"
        workspace.wiki_sync_error = None
        workspace.wiki_proposed_relpath = None
        workspace.updated_at = now_utc()
        await db.commit()
        return _wiki_sync_result(workspace)
    except Exception as exc:
        workspace.wiki_sync_status = "failed"
        workspace.wiki_sync_error = str(exc)[:1000]
        await db.commit()
        raise


async def publish_workspace_wiki_copy(db: AsyncSession, workspace: Workspace) -> dict[str, Any]:
    wiki = await get_or_create_wiki(db, workspace.owner_id)
    current = await db.get(WikiRevision, wiki.current_revision_id) if wiki.current_revision_id else None
    if current is None:
        raise HTTPException(status_code=409, detail="Wiki 当前版本不存在")
    metadata = workspace_metadata_path(workspace.owner_id, workspace.id) / "wiki"
    working_tree = workspace_wiki_working_tree_path(workspace.owner_id, workspace.id)
    base_tree = metadata / "base"
    _validate_workspace_wiki_changes(base_tree, working_tree)
    if _directory_hash(working_tree) == _directory_hash(base_tree):
        workspace.wiki_sync_status = "ready"
        workspace.wiki_sync_error = None
        await db.commit()
        return _wiki_sync_result(workspace)
    if workspace.wiki_base_revision_id != current.id:
        proposed = metadata / "proposed" / new_id("wiki")
        _replace_wiki_tree(working_tree, proposed)
        workspace.wiki_sync_status = "conflict"
        workspace.wiki_sync_error = "Wiki 基础版本已变化，需要三方合并"
        workspace.wiki_proposed_relpath = proposed.relative_to(metadata.parent).as_posix()
        await db.commit()
        return _wiki_sync_result(workspace)
    return await _publish_workspace_wiki_tree(db, workspace, wiki, current, working_tree, "Workspace 自动发布")


async def finalize_workspace_wiki_merge(
    db: AsyncSession, workspace: Workspace, merged_wiki_tree: Path
) -> dict[str, Any]:
    wiki = await get_or_create_wiki(db, workspace.owner_id)
    current = await db.get(WikiRevision, wiki.current_revision_id) if wiki.current_revision_id else None
    if current is None:
        raise HTTPException(status_code=409, detail="Wiki 当前版本不存在")
    return await _publish_workspace_wiki_tree(db, workspace, wiki, current, merged_wiki_tree, "Workspace 三方合并")


async def retry_workspace_wiki_merge(db: AsyncSession, workspace: Workspace) -> dict[str, Any]:
    if workspace.wiki_sync_status not in {"conflict", "failed"} or not workspace.wiki_proposed_relpath:
        raise HTTPException(status_code=409, detail="当前没有可重试的 Wiki 合并")
    proposed = workspace_metadata_path(workspace.owner_id, workspace.id) / workspace.wiki_proposed_relpath
    metadata = workspace_metadata_path(workspace.owner_id, workspace.id) / "wiki"
    base = metadata / "base"
    wiki = await get_or_create_wiki(db, workspace.owner_id)
    current = await db.get(WikiRevision, wiki.current_revision_id) if wiki.current_revision_id else None
    if current is None or not proposed.is_dir() or not base.is_dir():
        raise HTTPException(status_code=409, detail="Wiki 合并证据不完整")
    workspace.wiki_sync_status = "syncing"
    workspace.wiki_sync_error = None
    await db.commit()
    work_parent = metadata / ".merge"
    work_parent.mkdir(parents=True, exist_ok=True)
    try:
        with tempfile.TemporaryDirectory(prefix="workspace-wiki-merge-", dir=work_parent) as temp_name:
            root = Path(temp_name)
            shutil.copytree(base, root / "base")
            shutil.copytree(_revision_tree(workspace.owner_id, current.id), root / "current")
            shutil.copytree(proposed, root / "proposed")
            shutil.copytree(_revision_tree(workspace.owner_id, current.id), root / "merged")
            for tree in (root / "base", root / "current", root / "proposed", root / "merged"):
                _make_tree_writable(tree)
            from app.runtime.factory import get_runtime

            prompt = "\n".join([
                "你是 Mira Wiki Maintainer，负责一次严格的三方合并。",
                "base/ 是 Workspace 开始编辑时的 Wiki，current/ 是当前正式 Wiki，proposed/ 是 Workspace 的修改。",
                "请把双方对 base 的有效修改合并到 merged/wiki/。只允许修改 merged/wiki/**/*.md。",
                "不得修改 merged/raw/、merged/purpose.md、merged/schema.md，也不得修改 base/current/proposed。",
                "不得简单用 proposed 覆盖 current；冲突时保留双方事实并在 Markdown 中清晰整理。",
                "完成后只需简短说明合并结果。",
            ])
            result = await get_runtime().execute(
                prompt=prompt,
                session_id=wiki.maintainer_session_id,
                model=None,
                reasoning_effort=None,
                cwd=root,
                on_chunk=_ignore_agent_chunk,
                cancel_event=asyncio.Event(),
                on_decision_request=None,
                runtime_tools=None,
                runtime_policy="execute",
                output_schema=None,
                session_scope=f"wiki:{wiki.id}",
                fork_session=False,
            )
            if result.finished_with != "done":
                raise RuntimeError(result.error or "Wiki Maintainer 合并失败")
            merged = root / "merged"
            _validate_workspace_wiki_changes(root / "current", merged)
            outcome = await finalize_workspace_wiki_merge(db, workspace, merged)
            if result.session_id:
                wiki.maintainer_session_id = result.session_id
                await db.commit()
            return outcome
    except Exception as exc:
        await db.rollback()
        workspace = await db.get(Workspace, workspace.id)
        if workspace is not None:
            workspace.wiki_sync_status = "failed"
            workspace.wiki_sync_error = str(exc)[:1000]
            await db.commit()
        raise HTTPException(status_code=409, detail="Wiki 自动合并失败，可稍后重试") from exc


async def _publish_workspace_wiki_tree(
    db: AsyncSession,
    workspace: Workspace,
    wiki,
    current: WikiRevision,
    proposed_tree: Path,
    message: str,
) -> dict[str, Any]:
    if not proposed_tree.is_dir():
        raise HTTPException(status_code=400, detail="Workspace Wiki 修改不存在")
    current_tree = _revision_tree(workspace.owner_id, current.id)
    _validate_workspace_wiki_changes(current_tree, proposed_tree)
    _validate_wiki_tree(proposed_tree)
    manifest = _scan_tree(proposed_tree)
    with tempfile.TemporaryDirectory(prefix="mira-workspace-wiki-") as temp_name:
        candidate = Path(temp_name) / "tree"
        shutil.copytree(proposed_tree, candidate)
        revision_id = new_id("wrev")
        _publish_tree(candidate, _revision_tree(workspace.owner_id, revision_id))
    revision = WikiRevision(
        id=revision_id,
        wiki_id=wiki.id,
        parent_revision_id=current.id,
        operation_id=None,
        message=message,
        manifest_json=dumps(manifest),
        raw_manifest_json=current.raw_manifest_json,
        tree_hash=_tree_hash(manifest),
        created_at=now_utc(),
    )
    db.add(revision)
    wiki.current_revision_id = revision.id
    wiki.updated_at = now_utc()
    workspace.wiki_base_revision_id = revision.id
    workspace.wiki_sync_status = "ready"
    workspace.wiki_sync_error = None
    workspace.wiki_proposed_relpath = None
    workspace.updated_at = now_utc()
    await db.commit()
    metadata = workspace_metadata_path(workspace.owner_id, workspace.id) / "wiki"
    _replace_wiki_tree(_revision_tree(workspace.owner_id, revision.id), metadata / "base")
    _sync_working_tree_in_place(
        _revision_tree(workspace.owner_id, revision.id),
        workspace_wiki_working_tree_path(workspace.owner_id, workspace.id),
    )
    shutil.rmtree(metadata / "proposed", ignore_errors=True)
    return _wiki_sync_result(workspace)


def discard_workspace_wiki_changes(workspace: Workspace) -> None:
    metadata = workspace_metadata_path(workspace.owner_id, workspace.id) / "wiki"
    base = metadata / "base"
    working = workspace_wiki_working_tree_path(workspace.owner_id, workspace.id)
    if base.is_dir():
        _sync_working_tree_in_place(base, working)


async def create_workflow_proposal(
    db: AsyncSession,
    workspace_id: str,
    owner_id: str,
    *,
    session_id: str | None,
    kind: str,
    app_id: str | None,
    name: str,
    description: str,
    base_graph_sha256: str | None,
    graph: dict[str, Any],
) -> WorkspaceWorkflowProposalOut:
    workspace = await get_owned_workspace_or_404(db, workspace_id, owner_id)
    if session_id:
        _session_workspace, session = await get_owned_workspace_session_or_404(db, session_id, owner_id)
        if session.workspace_id != workspace.id:
            raise HTTPException(status_code=400, detail="会话不属于该工作空间")
    if kind == "update":
        app = await get_owned_app_or_404(db, app_id or "", owner_id)
        current_sha = graph_sha256(loads(app.graph_json, EMPTY_GRAPH))
        if base_graph_sha256 is not None and base_graph_sha256 != current_sha:
            raise HTTPException(status_code=409, detail="工作流已变化，请重新读取后再提交修改")
    else:
        app = None
        current_sha = None
    try:
        cleaned = clean_graph_input(graph)
    except GraphValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    lint = lint_workflow(cleaned)
    now = now_utc()
    row = WorkspaceWorkflowProposal(
        id=new_id("wfp"),
        workspace_id=workspace.id,
        session_id=session_id,
        kind=kind,
        app_id=app.id if app else None,
        name=_required_text(name, "应用名称", 200),
        description=_bounded_text(description, "应用描述", 8000),
        base_graph_sha256=current_sha,
        graph_json=dumps(cleaned),
        lint_json=dumps(lint),
        status="pending",
        created_at=now,
        updated_at=now,
    )
    db.add(row)
    await db.commit()
    return _proposal_out(row)


async def list_workflow_proposals(
    db: AsyncSession, workspace_id: str, owner_id: str
) -> list[WorkspaceWorkflowProposalOut]:
    await get_owned_workspace_or_404(db, workspace_id, owner_id)
    rows = (
        await db.execute(
            select(WorkspaceWorkflowProposal)
            .where(WorkspaceWorkflowProposal.workspace_id == workspace_id)
            .order_by(WorkspaceWorkflowProposal.created_at.desc(), WorkspaceWorkflowProposal.id.desc())
        )
    ).scalars().all()
    return [_proposal_out(row) for row in rows]


async def get_workflow_proposal_or_404(
    db: AsyncSession, workspace_id: str, proposal_id: str, owner_id: str
) -> WorkspaceWorkflowProposal:
    await get_owned_workspace_or_404(db, workspace_id, owner_id)
    row = (
        await db.execute(
            select(WorkspaceWorkflowProposal).where(
                WorkspaceWorkflowProposal.id == proposal_id,
                WorkspaceWorkflowProposal.workspace_id == workspace_id,
            )
        )
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="工作流提案不存在")
    return row


async def confirm_workflow_proposal(
    db: AsyncSession, workspace_id: str, proposal_id: str, owner_id: str
) -> WorkspaceWorkflowProposalOut:
    row = await get_workflow_proposal_or_404(db, workspace_id, proposal_id, owner_id)
    if row.status != "pending":
        raise HTTPException(status_code=409, detail="工作流提案已处理")
    graph = loads(row.graph_json, EMPTY_GRAPH)
    lint = lint_workflow(graph)
    if not lint.get("ok"):
        raise HTTPException(status_code=400, detail="工作流检查未通过，不能应用")
    try:
        graph = prepare_prompt_valid_graph(graph, owner_id=owner_id)
    except GraphValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    if row.kind == "update":
        app = await get_owned_app_or_404(db, row.app_id or "", owner_id)
        if graph_sha256(loads(app.graph_json, EMPTY_GRAPH)) != row.base_graph_sha256:
            row.status = "stale"
            row.updated_at = now_utc()
            await db.commit()
            raise HTTPException(status_code=409, detail="工作流已变化，请重新生成提案")
        await patch_owned_app(db, app.id, owner_id, {"name": row.name, "description": row.description, "graph": graph})
    else:
        now = now_utc()
        app = App(
            id=new_id("app"),
            owner_id=owner_id,
            name=row.name,
            description=row.description,
            status="draft",
            visibility="public",
            market_access="cloneable",
            graph_json=dumps(graph),
            created_at=now,
            updated_at=now,
        )
        db.add(app)
        await db.flush()
        row.app_id = app.id
    row.status = "applied"
    row.applied_at = now_utc()
    row.updated_at = row.applied_at
    await db.commit()
    return _proposal_out(row)


async def reject_workflow_proposal(
    db: AsyncSession, workspace_id: str, proposal_id: str, owner_id: str
) -> WorkspaceWorkflowProposalOut:
    row = await get_workflow_proposal_or_404(db, workspace_id, proposal_id, owner_id)
    if row.status != "pending":
        raise HTTPException(status_code=409, detail="工作流提案已处理")
    row.status = "rejected"
    row.updated_at = now_utc()
    await db.commit()
    return _proposal_out(row)


def graph_sha256(graph: dict[str, Any]) -> str:
    return hashlib.sha256(dumps(clean_graph_input(graph)).encode("utf-8")).hexdigest()


def _workspace_out(row: Workspace) -> WorkspaceOut:
    return WorkspaceOut(
        id=row.id,
        name=row.name,
        description=row.description,
        runtime_status=row.runtime_status,
        runtime_started_at=iso(row.runtime_started_at),
        runtime_last_error=row.runtime_last_error,
        wiki_base_revision_id=row.wiki_base_revision_id,
        wiki_sync_status=row.wiki_sync_status,
        wiki_sync_error=row.wiki_sync_error,
        created_at=iso(row.created_at) or "",
        updated_at=iso(row.updated_at) or "",
    )


def _session_out(row: WorkspaceSession, *, match_context: str | None = None) -> WorkspaceSessionOut:
    return WorkspaceSessionOut(
        id=row.id,
        workspace_id=row.workspace_id,
        title=row.title,
        thread_id=row.thread_id,
        status=row.status,
        last_turn_at=iso(row.last_turn_at),
        created_at=iso(row.created_at) or "",
        updated_at=iso(row.updated_at) or "",
        match_context=match_context,
    )


def _event_out(row: WorkspaceEvent) -> WorkspaceEventOut:
    return WorkspaceEventOut(
        id=row.id,
        workspace_id=row.workspace_id,
        session_id=row.session_id,
        turn_id=row.turn_id,
        event_type=row.event_type,
        payload=loads(row.payload_json, {}),
        created_at=iso(row.created_at) or "",
    )


def _proposal_out(row: WorkspaceWorkflowProposal) -> WorkspaceWorkflowProposalOut:
    return WorkspaceWorkflowProposalOut(
        id=row.id,
        workspace_id=row.workspace_id,
        session_id=row.session_id,
        kind=row.kind,
        app_id=row.app_id,
        name=row.name,
        description=row.description,
        base_graph_sha256=row.base_graph_sha256,
        graph=loads(row.graph_json, EMPTY_GRAPH),
        lint=loads(row.lint_json, {}),
        status=row.status,
        created_at=iso(row.created_at) or "",
        updated_at=iso(row.updated_at) or "",
        applied_at=iso(row.applied_at),
    )


def _verified_project_root(workspace: Workspace) -> Path:
    expected = workspace_project_path(workspace.owner_id, workspace.id).resolve()
    configured = (get_settings().runtime_dir.resolve() / workspace.project_relpath).resolve()
    if configured != expected:
        raise HTTPException(status_code=500, detail="工作空间目录记录无效")
    expected.mkdir(parents=True, exist_ok=True)
    return expected


def _safe_project_file(root: Path, value: str, *, allow_directory: bool = False) -> Path:
    raw = str(value or "").strip().replace("\\", "/")
    path = PurePosixPath(raw)
    if not raw or path.is_absolute() or any(part in {"", ".", ".."} for part in raw.split("/")):
        raise HTTPException(status_code=400, detail="文件路径无效")
    if path.parts[0] in WORKSPACE_HIDDEN_ROOTS:
        raise HTTPException(status_code=400, detail="不能访问工作空间内部目录")
    unresolved = root / path.as_posix()
    current = root
    for part in path.parts:
        current = current / part
        if current.is_symlink():
            raise HTTPException(status_code=400, detail="文件路径不能经过符号链接")
    target = unresolved.resolve()
    try:
        target.relative_to(root)
    except ValueError:
        raise HTTPException(status_code=400, detail="文件路径无效") from None
    if not allow_directory and target.is_dir():
        raise HTTPException(status_code=400, detail="请选择文件")
    return target


def _extract_safe_zip(raw: bytes, target: Path) -> None:
    with tempfile.TemporaryDirectory(prefix="mira-workspace-zip-") as temp_name:
        archive_path = Path(temp_name) / "upload.zip"
        archive_path.write_bytes(raw)
        try:
            archive = zipfile.ZipFile(archive_path)
        except zipfile.BadZipFile:
            raise HTTPException(status_code=400, detail="ZIP 文件无效") from None
        with archive:
            members = archive.infolist()
            if len(members) > WORKSPACE_ZIP_FILE_LIMIT:
                raise HTTPException(status_code=413, detail="ZIP 文件数量过多")
            expanded = sum(item.file_size for item in members)
            if expanded > WORKSPACE_ZIP_EXPANDED_LIMIT:
                raise HTTPException(status_code=413, detail="ZIP 解压后体积过大")
            destinations: list[tuple[zipfile.ZipInfo, Path]] = []
            for item in members:
                raw_name = item.filename.replace("\\", "/")
                path = PurePosixPath(raw_name)
                mode = (item.external_attr >> 16) & 0o170000
                if (
                    not raw_name
                    or path.is_absolute()
                    or any(part in {"", ".", ".."} for part in raw_name.rstrip("/").split("/"))
                    or (path.parts and path.parts[0] in WORKSPACE_HIDDEN_ROOTS)
                    or mode in {stat.S_IFLNK, stat.S_IFCHR, stat.S_IFBLK, stat.S_IFIFO, stat.S_IFSOCK}
                ):
                    raise HTTPException(status_code=400, detail="ZIP 包含不安全路径或文件")
                destination = (target / path.as_posix()).resolve()
                try:
                    destination.relative_to(target.resolve())
                except ValueError:
                    raise HTTPException(status_code=400, detail="ZIP 包含不安全路径") from None
                destinations.append((item, destination))
            for item, destination in destinations:
                if item.is_dir():
                    destination.mkdir(parents=True, exist_ok=True)
                else:
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    with archive.open(item) as source, destination.open("wb") as output:
                        shutil.copyfileobj(source, output)


async def _validate_git_url(db: AsyncSession, value: str) -> tuple[str, str]:
    raw = str(value or "").strip()
    parsed = urlparse(raw)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise HTTPException(status_code=400, detail="Git 仓库必须使用不含凭据的 HTTPS 地址")
    host = parsed.hostname.lower().rstrip(".")
    allowed = await workspace_git_allowed_hosts(db)
    if host not in allowed:
        raise HTTPException(status_code=400, detail=f"Git host 未在管理员白名单中：{host}")
    return raw, host


def _fernet() -> Fernet:
    secret = get_settings().codex_config_secret.strip()
    if not secret:
        raise HTTPException(status_code=500, detail="CODEX_CONFIG_SECRET 缺失，无法保存 Git 凭据")
    try:
        return Fernet(secret.encode("utf-8"))
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=500, detail="CODEX_CONFIG_SECRET 格式非法，无法保存 Git 凭据") from exc


def _encrypt_secret(value: str) -> str:
    return _fernet().encrypt(value.encode("utf-8")).decode("utf-8")


def _decrypt_secret(value: str) -> str:
    try:
        return _fernet().decrypt(value.encode("utf-8")).decode("utf-8")
    except InvalidToken as exc:
        raise HTTPException(status_code=500, detail="Git 凭据解密失败") from exc


def _replace_wiki_tree(source: Path, target: Path) -> None:
    if not source.is_dir():
        raise FileNotFoundError("Wiki tree 不存在")
    temporary = target.parent / f".{target.name}.tmp-{new_id('tree')}"
    temporary.parent.mkdir(parents=True, exist_ok=True)
    shutil.rmtree(temporary, ignore_errors=True)
    shutil.copytree(source, temporary)
    temporary.chmod(0o700)
    for path in temporary.rglob("*"):
        if path.is_file():
            path.chmod(0o600)
        elif path.is_dir():
            path.chmod(0o700)
    if target.exists():
        _make_tree_writable(target)
        shutil.rmtree(target)
    os.replace(temporary, target)


def _sync_working_tree_in_place(source: Path, target: Path) -> None:
    if not source.is_dir():
        raise FileNotFoundError("Wiki tree 不存在")
    target.mkdir(parents=True, exist_ok=True)
    _make_tree_writable(target)
    for child in list(target.iterdir()):
        if child.is_dir() and not child.is_symlink():
            shutil.rmtree(child)
        else:
            child.unlink(missing_ok=True)
    for child in source.iterdir():
        destination = target / child.name
        if child.is_dir():
            shutil.copytree(child, destination)
        else:
            shutil.copy2(child, destination)
    _make_tree_writable(target)


def _make_tree_writable(root: Path) -> None:
    root.chmod(0o700)
    for path in root.rglob("*"):
        path.chmod(0o700 if path.is_dir() else 0o600)


def _validate_workspace_wiki_changes(base: Path, candidate: Path) -> None:
    if not base.is_dir() or not candidate.is_dir():
        raise HTTPException(status_code=409, detail="Wiki Working Copy 不完整")
    base_protected = _protected_tree_hashes(base)
    candidate_protected = _protected_tree_hashes(candidate)
    if candidate_protected != base_protected:
        raise HTTPException(status_code=400, detail="Workspace 不得修改 raw/、purpose.md 或 schema.md")
    for path in candidate.rglob("*"):
        if path.is_symlink() or (not path.is_file() and not path.is_dir()):
            raise HTTPException(status_code=400, detail="Wiki Working Copy 包含链接或特殊文件")
        relative = path.relative_to(candidate)
        if relative.parts and relative.parts[0] == "wiki" and path.is_file() and path.suffix.lower() != ".md":
            raise HTTPException(status_code=400, detail="Workspace 只允许修改 wiki/*.md")
        if relative.parts and relative.parts[0] not in {"raw", "wiki"} and relative.as_posix() not in {"purpose.md", "schema.md"}:
            raise HTTPException(status_code=400, detail="Wiki Working Copy 包含约定外文件")


def _protected_tree_hashes(root: Path) -> dict[str, str]:
    hashes: dict[str, str] = {}
    for path in sorted(root.rglob("*"), key=lambda item: item.relative_to(root).as_posix()):
        if not path.is_file() or path.is_symlink():
            continue
        relative = path.relative_to(root).as_posix()
        if relative.startswith("wiki/"):
            continue
        hashes[relative] = hashlib.sha256(path.read_bytes()).hexdigest()
    return hashes


async def _ignore_agent_chunk(_chunk: AgentChunk) -> None:
    return None


def _directory_hash(root: Path) -> str:
    digest = hashlib.sha256()
    if not root.is_dir():
        return ""
    for path in sorted(root.rglob("*"), key=lambda item: item.relative_to(root).as_posix()):
        if not path.is_file() or path.is_symlink():
            continue
        relative = path.relative_to(root).as_posix()
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(hashlib.sha256(path.read_bytes()).digest())
    return digest.hexdigest()


def _wiki_sync_result(workspace: Workspace) -> dict[str, Any]:
    return {
        "status": workspace.wiki_sync_status,
        "base_revision_id": workspace.wiki_base_revision_id,
        "error": workspace.wiki_sync_error,
    }


def _sanitize_workspace_event_payload(event_type: str, payload: dict[str, Any]) -> dict[str, Any]:
    forbidden = {"command", "cmd", "stdout", "stderr", "output", "aggregated_output"}

    def scrub(value: Any) -> Any:
        if isinstance(value, dict):
            return {key: scrub(item) for key, item in value.items() if key.lower() not in forbidden}
        if isinstance(value, list):
            return [scrub(item) for item in value]
        return value

    sanitized = scrub(payload)
    if any(label in event_type.lower() for label in ("shell", "command", "terminal")):
        allowed = {"status", "duration_ms", "started_at", "finished_at", "name", "tool"}
        return {key: value for key, value in sanitized.items() if key in allowed}
    return sanitized


def _required_text(value: str, label: str, limit: int) -> str:
    normalized = " ".join(str(value or "").split())
    if not normalized:
        raise HTTPException(status_code=400, detail=f"{label}不能为空")
    if len(normalized) > limit:
        raise HTTPException(status_code=400, detail=f"{label}不能超过 {limit} 个字符")
    return normalized


def _bounded_text(value: str, label: str, limit: int) -> str:
    text = str(value or "").strip()
    if len(text) > limit:
        raise HTTPException(status_code=400, detail=f"{label}不能超过 {limit} 个字符")
    return text


def _normalized_title(value: str) -> str:
    return _required_text(value or "新会话", "会话名称", 120)
