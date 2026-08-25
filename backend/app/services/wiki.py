from __future__ import annotations

import asyncio
import hashlib
import json
import mimetypes
import os
import shutil
import tempfile
from pathlib import Path, PurePosixPath
from typing import Any

from fastapi import HTTPException, UploadFile
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db import SessionLocal
from app.models import (
    App,
    RunWikiSnapshot,
    Wiki,
    WikiAccessGrant,
    WikiOperation,
    WikiRevision,
    WikiSource,
)
from app.runtime.base import AgentChunk
from app.schemas import (
    WikiAccessOut,
    WikiFileContentOut,
    WikiFileOut,
    WikiLintIssueOut,
    WikiLintOut,
    WikiOperationOut,
    WikiOut,
    WikiRevisionOut,
    WikiSourceOut,
)
from app.services.artifacts import file_sha256, signed_wiki_file_download_url
from app.services.apps import get_visible_app_or_404
from app.services.runtime_paths import wiki_data_dir, wiki_run_snapshot_path
from app.services.wiki_parser import WikiParseError, conversion_kind, convert_to_markdown
from app.utils import dumps, iso, loads, new_id, now_utc


REQUIRED_WIKI_FILES = ("wiki/index.md", "wiki/log.md", "wiki/overview.md")
TEXT_PREVIEW_SUFFIXES = {".md", ".markdown", ".txt", ".json", ".csv", ".html", ".htm", ".xml"}
_operation_tasks: dict[str, asyncio.Task] = {}
_wiki_operation_locks: dict[str, asyncio.Lock] = {}


def normalize_wiki_path(value: str) -> str:
    raw = str(value or "").strip()
    if not raw or "\\" in raw or "\x00" in raw or "\ufffd" in raw:
        raise HTTPException(status_code=400, detail="Wiki 文件路径无效")
    path = PurePosixPath(raw)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in raw.split("/")):
        raise HTTPException(status_code=400, detail="Wiki 文件路径无效")
    if any(ord(char) < 32 for char in raw):
        raise HTTPException(status_code=400, detail="Wiki 文件路径无效")
    return path.as_posix()


async def get_or_create_wiki(db: AsyncSession, owner_id: str) -> Wiki:
    row = (await db.execute(select(Wiki).where(Wiki.owner_id == owner_id))).scalar_one_or_none()
    if row is not None:
        return row
    now = now_utc()
    row = Wiki(
        id=new_id("wiki"),
        owner_id=owner_id,
        purpose="沉淀可长期复用、可追溯的个人知识，供 Mira 工作流按需读取。",
        schema="以主题组织 Markdown；index.md 负责导航，overview.md 负责全局概览，log.md 记录维护历史。",
        created_at=now,
        updated_at=now,
    )
    db.add(row)
    await db.flush()
    revision = await asyncio.to_thread(_create_initial_revision, row)
    db.add(revision)
    row.current_revision_id = revision.id
    await db.commit()
    await db.refresh(row)
    return row


async def wiki_out(db: AsyncSession, owner_id: str) -> WikiOut:
    wiki = await get_or_create_wiki(db, owner_id)
    sources = await _sources(db, wiki.id)
    manifest = await _current_manifest(db, wiki)
    return WikiOut(
        id=wiki.id,
        purpose=wiki.purpose,
        schema=wiki.schema,
        current_revision_id=wiki.current_revision_id,
        file_count=len(manifest),
        source_count=sum(1 for source in sources if source.status != "pending_delete"),
        total_size=sum(source.size for source in sources if source.status != "pending_delete"),
        created_at=iso(wiki.created_at) or "",
        updated_at=iso(wiki.updated_at) or "",
    )


async def patch_wiki(db: AsyncSession, owner_id: str, *, purpose: str | None, schema: str | None) -> WikiOut:
    wiki = await get_or_create_wiki(db, owner_id)
    if purpose is not None:
        wiki.purpose = _bounded_text(purpose, "用途", 8_000)
    if schema is not None:
        wiki.schema = _bounded_text(schema, "结构说明", 8_000)
    wiki.updated_at = now_utc()
    await db.commit()
    operation = await enqueue_maintenance(db, owner_id, "根据更新后的 purpose.md 与 schema.md 重新整理 Wiki。", kind="maintenance")
    schedule_operation(operation.id)
    return await wiki_out(db, owner_id)


async def list_sources(db: AsyncSession, owner_id: str) -> list[WikiSourceOut]:
    wiki = await get_or_create_wiki(db, owner_id)
    return [_source_out(row) for row in await _sources(db, wiki.id)]


async def create_source(
    db: AsyncSession,
    owner_id: str,
    file: UploadFile,
    logical_path: str | None,
) -> tuple[WikiSourceOut, WikiOperationOut]:
    wiki = await get_or_create_wiki(db, owner_id)
    raw = await file.read()
    if not raw:
        raise HTTPException(status_code=400, detail="未提供文件")
    settings = get_settings()
    if len(raw) > settings.max_upload_bytes:
        raise HTTPException(status_code=413, detail="文件超出大小限制")
    name = (file.filename or "source").strip() or "source"
    path = normalize_wiki_path(logical_path or name)
    existing = await _sources(db, wiki.id)
    if any(row.path == path and row.status != "pending_delete" for row in existing):
        raise HTTPException(status_code=409, detail="Wiki 中已存在同名原始文件")
    total = sum(row.size for row in existing if row.status != "pending_delete")
    if total + len(raw) > settings.max_wiki_bytes:
        raise HTTPException(status_code=413, detail="Wiki 总容量已超出限制")
    source_id = new_id("wsrc")
    source_root = wiki_data_dir(owner_id) / "sources" / source_id
    source_root.mkdir(parents=True, exist_ok=False)
    blob = source_root / "blob"
    blob.write_bytes(raw)
    digest = hashlib.sha256(raw).hexdigest()
    now = now_utc()
    source = WikiSource(
        id=source_id,
        wiki_id=wiki.id,
        path=path,
        name=PurePosixPath(path).name,
        mime=(file.content_type or "application/octet-stream").strip() or "application/octet-stream",
        size=len(raw),
        sha256=digest,
        storage_relpath=blob.relative_to(wiki_data_dir(owner_id)).as_posix(),
        status="pending",
        created_at=now,
        updated_at=now,
    )
    operation = WikiOperation(
        id=new_id("wop"),
        wiki_id=wiki.id,
        source_id=source.id,
        kind="ingest",
        status="pending",
        created_at=now,
    )
    db.add_all([source, operation])
    await db.commit()
    schedule_operation(operation.id)
    return _source_out(source), _operation_out(operation)


async def rename_source(db: AsyncSession, owner_id: str, source_id: str, path: str) -> WikiSourceOut:
    wiki, source = await _owned_source(db, owner_id, source_id)
    normalized = normalize_wiki_path(path)
    rows = await _sources(db, wiki.id)
    if any(row.id != source.id and row.path == normalized and row.status != "pending_delete" for row in rows):
        raise HTTPException(status_code=409, detail="Wiki 中已存在同名原始文件")
    source.path = normalized
    source.name = PurePosixPath(normalized).name
    source.updated_at = now_utc()
    await db.commit()
    operation = await enqueue_maintenance(db, owner_id, f"原始文件已重命名为 {normalized}，请同步导航与引用。", kind="rename")
    schedule_operation(operation.id)
    return _source_out(source)


async def delete_source(db: AsyncSession, owner_id: str, source_id: str) -> WikiOperationOut:
    wiki, source = await _owned_source(db, owner_id, source_id)
    if source.status == "pending_delete":
        raise HTTPException(status_code=409, detail="文件已在删除队列中")
    source.status = "pending_delete"
    source.updated_at = now_utc()
    operation = WikiOperation(
        id=new_id("wop"),
        wiki_id=wiki.id,
        source_id=source.id,
        kind="delete",
        status="pending",
        created_at=now_utc(),
    )
    db.add(operation)
    await db.commit()
    schedule_operation(operation.id)
    return _operation_out(operation)


async def enqueue_maintenance(
    db: AsyncSession,
    owner_id: str,
    instruction: str,
    *,
    kind: str = "maintenance",
) -> WikiOperation:
    wiki = await get_or_create_wiki(db, owner_id)
    operation = WikiOperation(
        id=new_id("wop"),
        wiki_id=wiki.id,
        source_id=None,
        kind=kind,
        status="pending",
        instruction=_bounded_text(instruction, "维护指令", 4_000),
        created_at=now_utc(),
    )
    db.add(operation)
    await db.commit()
    return operation


async def list_operations(db: AsyncSession, owner_id: str) -> list[WikiOperationOut]:
    wiki = await get_or_create_wiki(db, owner_id)
    rows = (
        await db.execute(
            select(WikiOperation)
            .where(WikiOperation.wiki_id == wiki.id)
            .order_by(WikiOperation.created_at.desc(), WikiOperation.id.desc())
            .limit(100)
        )
    ).scalars().all()
    return [_operation_out(row) for row in rows]


async def retry_operation(db: AsyncSession, owner_id: str, operation_id: str) -> WikiOperationOut:
    wiki = await get_or_create_wiki(db, owner_id)
    operation = await db.get(WikiOperation, operation_id)
    if operation is None or operation.wiki_id != wiki.id:
        raise HTTPException(status_code=404, detail="Wiki 操作不存在")
    if operation.status not in {"failed", "cancelled"}:
        raise HTTPException(status_code=409, detail="当前操作不能重试")
    operation.status = "pending"
    operation.error = None
    operation.started_at = None
    operation.finished_at = None
    if operation.source_id:
        source = await db.get(WikiSource, operation.source_id)
        if source is not None and source.status == "failed":
            source.status = "pending"
            source.error = None
    await db.commit()
    schedule_operation(operation.id)
    return _operation_out(operation)


async def cancel_operation(db: AsyncSession, owner_id: str, operation_id: str) -> WikiOperationOut:
    wiki = await get_or_create_wiki(db, owner_id)
    operation = await db.get(WikiOperation, operation_id)
    if operation is None or operation.wiki_id != wiki.id:
        raise HTTPException(status_code=404, detail="Wiki 操作不存在")
    if operation.status not in {"pending", "running"}:
        raise HTTPException(status_code=409, detail="当前操作不能取消")
    task = _operation_tasks.get(operation.id)
    if task is not None:
        task.cancel()
    operation.status = "cancelled"
    operation.finished_at = now_utc()
    await db.commit()
    return _operation_out(operation)


def schedule_operation(operation_id: str) -> None:
    current = _operation_tasks.get(operation_id)
    if current is not None and not current.done():
        return
    task = asyncio.create_task(run_operation(operation_id))
    _operation_tasks[operation_id] = task
    task.add_done_callback(lambda _task: _operation_tasks.pop(operation_id, None))


async def run_operation(operation_id: str) -> None:
    async with SessionLocal() as db:
        operation = await db.get(WikiOperation, operation_id)
        if operation is None or operation.status != "pending":
            return
        lock = _wiki_operation_locks.setdefault(operation.wiki_id, asyncio.Lock())
        async with lock:
            await db.refresh(operation)
            if operation.status != "pending":
                return
            wiki = await db.get(Wiki, operation.wiki_id)
            if wiki is None:
                return
            operation.status = "running"
            operation.started_at = now_utc()
            await db.commit()
            try:
                await _execute_operation(db, wiki, operation)
            except asyncio.CancelledError:
                operation.status = "cancelled"
                operation.finished_at = now_utc()
                await db.commit()
                raise
            except Exception as exc:  # noqa: BLE001
                await db.rollback()
                operation = await db.get(WikiOperation, operation_id)
                if operation is None:
                    return
                operation.status = "failed"
                operation.error = _short_error(exc)
                operation.finished_at = now_utc()
                if operation.source_id:
                    source = await db.get(WikiSource, operation.source_id)
                    if source is not None and source.status == "pending":
                        source.status = "failed"
                        source.error = operation.error
                        source.updated_at = now_utc()
                await db.commit()


async def _execute_operation(db: AsyncSession, wiki: Wiki, operation: WikiOperation) -> None:
    sources = await _sources(db, wiki.id)
    active = [source for source in sources if source.status != "pending_delete"]
    parsed_source = await db.get(WikiSource, operation.source_id) if operation.source_id else None
    parsed_kind = conversion_kind(parsed_source.path) if parsed_source is not None else None
    if parsed_source is not None and parsed_source.status == "pending" and parsed_kind == "document":
        source_path = wiki_data_dir(wiki.owner_id) / parsed_source.storage_relpath
        converted = await convert_to_markdown(source_path)
        (source_path.parent / "converted.md").write_text(converted, encoding="utf-8")

    current = await _current_revision(db, wiki)
    if current is None:
        raise RuntimeError("Wiki 当前 revision 不存在")
    current_tree = _revision_tree(wiki.owner_id, current.id)
    work_parent = wiki_data_dir(wiki.owner_id) / ".operations"
    work_parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=f"{operation.id}-", dir=work_parent) as temp_name:
        workspace = Path(temp_name) / "workspace"
        shutil.copytree(current_tree, workspace)
        _reset_writable(workspace)
        _rebuild_raw_tree(workspace, active, wiki.owner_id)
        (workspace / "purpose.md").write_text(wiki.purpose.strip() + "\n", encoding="utf-8")
        (workspace / "schema.md").write_text(wiki.schema.strip() + "\n", encoding="utf-8")
        _seed_compiled_pages(workspace, active, operation)
        protected_hashes = _protected_hashes(workspace)
        should_run_agent = operation.kind != "ingest" or parsed_kind in {"document", "image"}
        session_id = wiki.maintainer_session_id
        if should_run_agent:
            result = await _run_maintainer(workspace, wiki, operation, active)
            if result.finished_with != "done":
                raise RuntimeError(result.error or "Wiki Maintainer 执行失败")
            session_id = result.session_id or session_id
        if _protected_hashes(workspace) != protected_hashes:
            raise RuntimeError("Wiki Maintainer 修改了只读原始来源或结构说明")
        _validate_wiki_tree(workspace)
        revision = await asyncio.to_thread(
            _publish_revision,
            wiki,
            current,
            operation,
            workspace,
            active,
        )

    db.add(revision)
    wiki.current_revision_id = revision.id
    wiki.maintainer_session_id = session_id
    wiki.updated_at = now_utc()
    operation.status = "success"
    operation.error = None
    operation.finished_at = now_utc()
    for source in active:
        if source.status == "pending":
            source.status = "unsupported" if conversion_kind(source.path) == "unsupported" else "ready"
            source.error = "当前格式仅保存原文，不参与自动编译" if source.status == "unsupported" else None
            source.updated_at = now_utc()
    deleted_sources = [source for source in sources if source.status == "pending_delete"]
    for source in deleted_sources:
        await db.delete(source)
    await db.commit()
    for source in deleted_sources:
        shutil.rmtree((wiki_data_dir(wiki.owner_id) / source.storage_relpath).parent, ignore_errors=True)


async def _run_maintainer(workspace: Path, wiki: Wiki, operation: WikiOperation, sources: list[WikiSource]):
    from app.runtime.factory import get_runtime
    prompt = "\n".join([
        "你是 Mira Wiki Maintainer。请在当前 workspace 内维护长期 Wiki。",
        "原始文件位于 raw/，其中可转换文档的 Markdown 副本位于 .mira-imports/。",
        "只允许修改 wiki/ 下的 Markdown；不得修改或删除 raw/、purpose.md、schema.md。",
        "必须保留 wiki/index.md、wiki/log.md、wiki/overview.md，并让 index.md 能导航到主题页面。",
        "把事实组织成简洁、可追溯的 Markdown；不要写入工作流运行输出。",
        f"本次操作：{operation.kind}",
        f"维护指令：{operation.instruction or '根据原始来源更新 Wiki，合并重复内容并记录维护日志。'}",
        "来源：" + ("、".join(source.path for source in sources) or "无"),
        "完成文件修改后，用一句话说明结果。",
    ])
    chunks: list[str] = []

    async def on_chunk(chunk: AgentChunk) -> None:
        if chunk.type == "text" and chunk.text:
            chunks.append(chunk.text)

    return await get_runtime().execute(
        prompt=prompt,
        session_id=wiki.maintainer_session_id,
        model=None,
        reasoning_effort=None,
        cwd=workspace,
        on_chunk=on_chunk,
        cancel_event=asyncio.Event(),
        on_decision_request=None,
        runtime_tools=None,
        runtime_policy="execute",
        output_schema=None,
        session_scope=f"wiki:{wiki.id}",
        fork_session=False,
    )


async def list_tree(db: AsyncSession, owner_id: str) -> list[WikiFileOut]:
    wiki = await get_or_create_wiki(db, owner_id)
    revision = await _current_revision(db, wiki)
    if revision is None:
        return []
    return [
        WikiFileOut(
            path=item["path"],
            size=item["size"],
            sha256=item["sha256"],
            mime=item["mime"],
            download_url=signed_wiki_file_download_url(owner_id, revision.id, item["path"], item["sha256"]),
        )
        for item in _manifest(revision)
    ]


async def read_file_content(db: AsyncSession, owner_id: str, path: str) -> WikiFileContentOut:
    normalized = normalize_wiki_path(path)
    wiki = await get_or_create_wiki(db, owner_id)
    revision = await _current_revision(db, wiki)
    if revision is None:
        raise HTTPException(status_code=404, detail="Wiki 文件不存在")
    item = next((entry for entry in _manifest(revision) if entry["path"] == normalized), None)
    if item is None:
        raise HTTPException(status_code=404, detail="Wiki 文件不存在")
    if Path(normalized).suffix.lower() not in TEXT_PREVIEW_SUFFIXES or item["size"] > 2_000_000:
        raise HTTPException(status_code=415, detail="该文件不支持文本预览")
    target = _safe_tree_file(_revision_tree(owner_id, revision.id), normalized)
    try:
        content = target.read_text(encoding="utf-8")
    except UnicodeError:
        raise HTTPException(status_code=415, detail="该文件不是有效 UTF-8 文本") from None
    return WikiFileContentOut(path=normalized, mime=item["mime"], content=content)


async def resolve_file_download(
    db: AsyncSession,
    owner_id: str,
    revision_id: str,
    path: str,
    sha256: str,
) -> tuple[Path, str, str]:
    normalized = normalize_wiki_path(path)
    wiki = await get_or_create_wiki(db, owner_id)
    revision = await db.get(WikiRevision, revision_id)
    if revision is None or revision.wiki_id != wiki.id:
        raise HTTPException(status_code=404, detail="Wiki 文件不存在")
    item = next((entry for entry in _manifest(revision) if entry["path"] == normalized), None)
    if item is None or item["sha256"] != sha256:
        raise HTTPException(status_code=404, detail="Wiki 文件不存在")
    target = _safe_tree_file(_revision_tree(owner_id, revision.id), normalized)
    if not target.is_file() or file_sha256(target) != sha256:
        raise HTTPException(status_code=409, detail="Wiki 文件完整性校验失败")
    return target, item["mime"], PurePosixPath(normalized).name


async def list_revisions(db: AsyncSession, owner_id: str) -> list[WikiRevisionOut]:
    wiki = await get_or_create_wiki(db, owner_id)
    rows = (
        await db.execute(
            select(WikiRevision)
            .where(WikiRevision.wiki_id == wiki.id)
            .order_by(WikiRevision.created_at.desc(), WikiRevision.id.desc())
        )
    ).scalars().all()
    return [
        WikiRevisionOut(
            id=row.id,
            parent_revision_id=row.parent_revision_id,
            message=row.message,
            tree_hash=row.tree_hash,
            file_count=len(_manifest(row)),
            created_at=iso(row.created_at) or "",
            current=row.id == wiki.current_revision_id,
        )
        for row in rows
    ]


async def restore_revision(db: AsyncSession, owner_id: str, revision_id: str) -> WikiRevisionOut:
    wiki = await get_or_create_wiki(db, owner_id)
    source = await db.get(WikiRevision, revision_id)
    if source is None or source.wiki_id != wiki.id:
        raise HTTPException(status_code=404, detail="Wiki 版本不存在")
    active_operation = (
        await db.execute(
            select(WikiOperation.id).where(
                WikiOperation.wiki_id == wiki.id,
                WikiOperation.status.in_(["pending", "running"]),
            ).limit(1)
        )
    ).scalar_one_or_none()
    if active_operation is not None:
        raise HTTPException(status_code=409, detail="请等待当前 Wiki 维护完成后再恢复版本")
    current = await _current_revision(db, wiki)
    if current is None:
        raise HTTPException(status_code=409, detail="Wiki 当前版本不存在")
    revision = await asyncio.to_thread(_copy_revision, wiki, current, source)
    db.add(revision)
    wiki.current_revision_id = revision.id
    wiki.updated_at = now_utc()
    await db.commit()
    return WikiRevisionOut(
        id=revision.id,
        parent_revision_id=revision.parent_revision_id,
        message=revision.message,
        tree_hash=revision.tree_hash,
        file_count=len(_manifest(revision)),
        created_at=iso(revision.created_at) or "",
        current=True,
    )


async def lint_wiki(db: AsyncSession, owner_id: str) -> WikiLintOut:
    wiki = await get_or_create_wiki(db, owner_id)
    revision = await _current_revision(db, wiki)
    issues: list[WikiLintIssueOut] = []
    if revision is None:
        issues.append(WikiLintIssueOut(severity="error", detail="Wiki 没有可用版本"))
        return WikiLintOut(ok=False, issues=issues)
    paths = {item["path"] for item in _manifest(revision)}
    for required in REQUIRED_WIKI_FILES:
        if required not in paths:
            issues.append(WikiLintIssueOut(severity="error", path=required, detail="缺少必备 Wiki 文件"))
    try:
        await asyncio.to_thread(_verify_revision, wiki.owner_id, revision)
    except Exception as exc:  # noqa: BLE001
        issues.append(WikiLintIssueOut(severity="error", detail=f"完整性校验失败：{_short_error(exc)}"))
    sources = await _sources(db, wiki.id)
    for source in sources:
        if source.status == "unsupported":
            issues.append(WikiLintIssueOut(severity="warning", path=f"raw/{source.path}", detail="该格式仅保存原文"))
        elif source.status == "failed":
            issues.append(WikiLintIssueOut(severity="error", path=f"raw/{source.path}", detail=source.error or "自动入库失败"))
    return WikiLintOut(ok=not any(issue.severity == "error" for issue in issues), issues=issues)


def graph_sha256(graph: dict[str, Any]) -> str:
    canonical = json.dumps(graph, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


async def wiki_access_status(db: AsyncSession, user_id: str, app_id: str) -> WikiAccessOut:
    app = await get_visible_app_or_404(db, app_id, user_id)
    graph = loads(app.graph_json, {}) or {}
    digest = graph_sha256(graph if isinstance(graph, dict) else {})
    wiki = (await db.execute(select(Wiki).where(Wiki.owner_id == user_id))).scalar_one_or_none()
    has_wiki = False
    if wiki is not None:
        revision = await _current_revision(db, wiki)
        raw_manifest = loads(revision.raw_manifest_json, []) if revision is not None else []
        has_wiki = bool(isinstance(raw_manifest, list) and raw_manifest)
    owner_app = app.owner_id == user_id
    grant = None
    if has_wiki and not owner_app:
        grant = (
            await db.execute(
                select(WikiAccessGrant).where(
                    WikiAccessGrant.user_id == user_id,
                    WikiAccessGrant.app_id == app.id,
                    WikiAccessGrant.graph_sha256 == digest,
                )
            )
        ).scalar_one_or_none()
    return WikiAccessOut(
        app_id=app.id,
        graph_sha256=digest,
        has_wiki=has_wiki,
        owner_app=owner_app,
        requires_consent=has_wiki and not owner_app and grant is None,
        granted=owner_app or grant is not None,
    )


async def grant_wiki_access(db: AsyncSession, user_id: str, app_id: str, digest: str) -> WikiAccessOut:
    status = await wiki_access_status(db, user_id, app_id)
    if status.owner_app or not status.has_wiki:
        return status
    if digest != status.graph_sha256:
        raise HTTPException(status_code=409, detail="应用已更新，请重新确认 Wiki 授权")
    if not status.granted:
        db.add(WikiAccessGrant(
            id=new_id("wgrant"),
            user_id=user_id,
            app_id=app_id,
            graph_sha256=digest,
            created_at=now_utc(),
        ))
        await db.commit()
    return await wiki_access_status(db, user_id, app_id)


async def revoke_wiki_access(db: AsyncSession, user_id: str, app_id: str) -> None:
    await db.execute(delete(WikiAccessGrant).where(
        WikiAccessGrant.user_id == user_id,
        WikiAccessGrant.app_id == app_id,
    ))
    await db.commit()


async def freeze_wiki_for_run(
    db: AsyncSession,
    *,
    user_id: str,
    app: App,
    run_id: str,
    wiki_mode: str,
) -> None:
    status = await wiki_access_status(db, user_id, app.id)
    if not status.has_wiki or wiki_mode == "without":
        return
    if not status.owner_app and not status.granted:
        raise HTTPException(status_code=409, detail="运行前需要确认是否允许此应用读取你的 Wiki")
    wiki = (await db.execute(select(Wiki).where(Wiki.owner_id == user_id))).scalar_one()
    revision = await _current_revision(db, wiki)
    if revision is None:
        return
    await asyncio.to_thread(_verify_revision, user_id, revision)
    target = wiki_run_snapshot_path(run_id)
    await asyncio.to_thread(_materialize_snapshot, _revision_tree(user_id, revision.id), target, revision)
    db.add(RunWikiSnapshot(
        run_id=run_id,
        wiki_id=wiki.id,
        revision_id=revision.id,
        tree_hash=revision.tree_hash,
        raw_manifest_json=revision.raw_manifest_json,
        created_at=now_utc(),
    ))


async def clone_run_wiki_snapshot(db: AsyncSession, source_run_id: str, target_run_id: str) -> None:
    source = await db.get(RunWikiSnapshot, source_run_id)
    if source is None:
        return
    source_path = wiki_run_snapshot_path(source_run_id)
    target_path = wiki_run_snapshot_path(target_run_id)
    await asyncio.to_thread(_copy_snapshot, source_path, target_path)
    db.add(RunWikiSnapshot(
        run_id=target_run_id,
        wiki_id=source.wiki_id,
        revision_id=source.revision_id,
        tree_hash=source.tree_hash,
        raw_manifest_json=source.raw_manifest_json,
        created_at=now_utc(),
    ))


def remove_run_wiki_snapshot(run_id: str) -> None:
    shutil.rmtree(wiki_run_snapshot_path(run_id), ignore_errors=True)


def runtime_wiki_mount(run_id: str) -> Path | None:
    root = wiki_run_snapshot_path(run_id)
    tree = root / "tree"
    meta = loads((root / "snapshot.json").read_text(encoding="utf-8"), None) if (root / "snapshot.json").is_file() else None
    if not tree.is_dir() or not isinstance(meta, dict) or not isinstance(meta.get("tree_hash"), str):
        return None
    if _tree_hash(_scan_tree(tree)) != meta["tree_hash"]:
        raise RuntimeError("Run Wiki 快照完整性校验失败")
    return tree


async def recover_wiki_operations(db: AsyncSession) -> int:
    rows = (
        await db.execute(
            select(WikiOperation).where(WikiOperation.status.in_(["pending", "running"]))
        )
    ).scalars().all()
    for operation in rows:
        operation.status = "pending"
        operation.started_at = None
        operation.finished_at = None
        operation.error = None
    if rows:
        await db.commit()
        for operation in rows:
            schedule_operation(operation.id)
    return len(rows)


async def _owned_source(db: AsyncSession, owner_id: str, source_id: str) -> tuple[Wiki, WikiSource]:
    wiki = await get_or_create_wiki(db, owner_id)
    source = await db.get(WikiSource, source_id)
    if source is None or source.wiki_id != wiki.id:
        raise HTTPException(status_code=404, detail="Wiki 原始文件不存在")
    return wiki, source


async def _sources(db: AsyncSession, wiki_id: str) -> list[WikiSource]:
    return list((await db.execute(
        select(WikiSource).where(WikiSource.wiki_id == wiki_id).order_by(WikiSource.created_at.asc(), WikiSource.id.asc())
    )).scalars().all())


async def _current_revision(db: AsyncSession, wiki: Wiki) -> WikiRevision | None:
    if not wiki.current_revision_id:
        return None
    revision = await db.get(WikiRevision, wiki.current_revision_id)
    return revision if revision is not None and revision.wiki_id == wiki.id else None


async def _current_manifest(db: AsyncSession, wiki: Wiki) -> list[dict[str, Any]]:
    revision = await _current_revision(db, wiki)
    return _manifest(revision) if revision is not None else []


def _manifest(revision: WikiRevision) -> list[dict[str, Any]]:
    value = loads(revision.manifest_json, []) or []
    return value if isinstance(value, list) else []


def _source_out(source: WikiSource) -> WikiSourceOut:
    return WikiSourceOut(
        id=source.id,
        path=source.path,
        name=source.name,
        mime=source.mime,
        size=source.size,
        sha256=source.sha256,
        status=source.status,
        error=source.error,
        created_at=iso(source.created_at) or "",
        updated_at=iso(source.updated_at) or "",
    )


def _operation_out(operation: WikiOperation) -> WikiOperationOut:
    return WikiOperationOut(
        id=operation.id,
        source_id=operation.source_id,
        kind=operation.kind,
        status=operation.status,
        instruction=operation.instruction,
        error=operation.error,
        created_at=iso(operation.created_at) or "",
        started_at=iso(operation.started_at),
        finished_at=iso(operation.finished_at),
    )


def _create_initial_revision(wiki: Wiki) -> WikiRevision:
    revision_id = new_id("wrev")
    with tempfile.TemporaryDirectory(prefix="mira-wiki-initial-") as temp_name:
        root = Path(temp_name)
        (root / "wiki").mkdir(parents=True)
        (root / "purpose.md").write_text(wiki.purpose + "\n", encoding="utf-8")
        (root / "schema.md").write_text(wiki.schema + "\n", encoding="utf-8")
        (root / "wiki" / "index.md").write_text("# Wiki 索引\n\n- [概览](overview.md)\n- [维护日志](log.md)\n", encoding="utf-8")
        (root / "wiki" / "overview.md").write_text("# 概览\n\n尚未加入原始资料。\n", encoding="utf-8")
        (root / "wiki" / "log.md").write_text("# 维护日志\n\n- 初始化 Wiki。\n", encoding="utf-8")
        manifest = _scan_tree(root)
        target = _revision_tree(wiki.owner_id, revision_id)
        _publish_tree(root, target)
    return WikiRevision(
        id=revision_id,
        wiki_id=wiki.id,
        parent_revision_id=None,
        operation_id=None,
        message="初始化 Wiki",
        manifest_json=dumps(manifest),
        raw_manifest_json="[]",
        tree_hash=_tree_hash(manifest),
        created_at=now_utc(),
    )


def _rebuild_raw_tree(workspace: Path, sources: list[WikiSource], owner_id: str) -> None:
    shutil.rmtree(workspace / "raw", ignore_errors=True)
    shutil.rmtree(workspace / ".mira-imports", ignore_errors=True)
    (workspace / "raw").mkdir(parents=True)
    imports = workspace / ".mira-imports"
    imports.mkdir(parents=True)
    for source in sources:
        source_root = wiki_data_dir(owner_id) / source.storage_relpath
        target = _safe_tree_file(workspace / "raw", source.path)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source_root, target)
        converted = source_root.parent / "converted.md"
        if converted.is_file():
            shutil.copy2(converted, imports / f"{source.id}.md")


def _seed_compiled_pages(workspace: Path, sources: list[WikiSource], operation: WikiOperation) -> None:
    wiki_root = workspace / "wiki"
    source_pages = wiki_root / "sources"
    source_pages.mkdir(parents=True, exist_ok=True)
    active_ids = {source.id for source in sources}
    for old in source_pages.glob("*.md"):
        if old.stem not in active_ids:
            old.unlink()
    links = ["- [概览](overview.md)", "- [维护日志](log.md)"]
    for source in sources:
        converted = workspace / ".mira-imports" / f"{source.id}.md"
        page = source_pages / f"{source.id}.md"
        if converted.is_file():
            page.write_text(f"# {source.name}\n\n来源：`raw/{source.path}`\n\n{converted.read_text(encoding='utf-8')}\n", encoding="utf-8")
            links.append(f"- [{source.path}](sources/{source.id}.md)")
    (wiki_root / "index.md").write_text("# Wiki 索引\n\n" + "\n".join(links) + "\n", encoding="utf-8")
    overview = ["# 概览", "", f"当前收录 {len(sources)} 个原始文件。", ""]
    overview.extend(f"- `{source.path}`（{source.status}）" for source in sources)
    (wiki_root / "overview.md").write_text("\n".join(overview) + "\n", encoding="utf-8")
    log = wiki_root / "log.md"
    previous = log.read_text(encoding="utf-8") if log.is_file() else "# 维护日志\n"
    log.write_text(previous.rstrip() + f"\n\n- {iso(now_utc())}: {operation.kind}。\n", encoding="utf-8")


def _validate_wiki_tree(root: Path) -> None:
    for required in REQUIRED_WIKI_FILES:
        target = _safe_tree_file(root, required)
        if not target.is_file() or not target.read_text(encoding="utf-8").strip():
            raise RuntimeError(f"Wiki Maintainer 未生成必备文件：{required}")
    for path in root.rglob("*"):
        if path.is_symlink() or (path.exists() and not path.is_file() and not path.is_dir()):
            raise RuntimeError("Wiki 包含不支持的链接或特殊文件")
        if path.is_file() and path.suffix.lower() in {".md", ".txt", ".json", ".csv", ".html", ".xml"}:
            if "\ufffd" in path.read_text(encoding="utf-8"):
                raise RuntimeError("Wiki 文本包含损坏字符 U+FFFD")
        relative = path.relative_to(root)
        if relative.parts and relative.parts[0] not in {"raw", "wiki", ".mira-imports"} and relative.as_posix() not in {"purpose.md", "schema.md"}:
            raise RuntimeError("Wiki Maintainer 写入了约定目录之外的文件")
        if relative.parts and relative.parts[0] == "wiki" and path.is_file() and path.suffix.lower() != ".md":
            raise RuntimeError("编译后的 Wiki 只允许 Markdown 文件")
    shutil.rmtree(root / ".mira-imports", ignore_errors=True)


def _publish_revision(
    wiki: Wiki,
    current: WikiRevision,
    operation: WikiOperation,
    workspace: Path,
    sources: list[WikiSource],
) -> WikiRevision:
    revision_id = new_id("wrev")
    manifest = _scan_tree(workspace)
    target = _revision_tree(wiki.owner_id, revision_id)
    _publish_tree(workspace, target)
    raw_manifest = [
        {"source_id": source.id, "path": source.path, "size": source.size, "sha256": source.sha256, "mime": source.mime}
        for source in sources
    ]
    return WikiRevision(
        id=revision_id,
        wiki_id=wiki.id,
        parent_revision_id=current.id,
        operation_id=operation.id,
        message=operation.instruction or {"ingest": "自动入库", "delete": "删除原始文件", "rename": "重命名原始文件"}.get(operation.kind, "维护 Wiki"),
        manifest_json=dumps(manifest),
        raw_manifest_json=dumps(raw_manifest),
        tree_hash=_tree_hash(manifest),
        created_at=now_utc(),
    )


def _copy_revision(wiki: Wiki, current: WikiRevision, source: WikiRevision) -> WikiRevision:
    revision_id = new_id("wrev")
    target = _revision_tree(wiki.owner_id, revision_id)
    _copy_snapshot_tree(_revision_tree(wiki.owner_id, source.id), target)
    return WikiRevision(
        id=revision_id,
        wiki_id=wiki.id,
        parent_revision_id=current.id,
        operation_id=None,
        message=f"恢复版本 {source.id}",
        manifest_json=source.manifest_json,
        raw_manifest_json=source.raw_manifest_json,
        tree_hash=source.tree_hash,
        created_at=now_utc(),
    )


def _revision_tree(owner_id: str, revision_id: str) -> Path:
    return wiki_data_dir(owner_id) / "revisions" / revision_id / "tree"


def _publish_tree(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        raise FileExistsError(target)
    temporary = target.parent / f".{target.name}.tmp-{new_id('tree')}"
    try:
        shutil.copytree(source, temporary)
        _make_read_only(temporary)
        os.replace(temporary, target)
    except Exception:
        shutil.rmtree(temporary, ignore_errors=True)
        raise


def _materialize_snapshot(source: Path, target: Path, revision: WikiRevision) -> None:
    if target.exists():
        raise FileExistsError(target)
    target.mkdir(parents=True)
    try:
        _copy_snapshot_tree(source, target / "tree")
        (target / "snapshot.json").write_text(dumps({
            "revision_id": revision.id,
            "tree_hash": revision.tree_hash,
        }), encoding="utf-8")
    except Exception:
        shutil.rmtree(target, ignore_errors=True)
        raise


def _copy_snapshot(source: Path, target: Path) -> None:
    if not source.is_dir():
        raise FileNotFoundError("来源 Run Wiki 快照不存在")
    if target.exists():
        raise FileExistsError(target)
    shutil.copytree(source, target, copy_function=_link_or_copy)


def _copy_snapshot_tree(source: Path, target: Path) -> None:
    shutil.copytree(source, target, copy_function=_link_or_copy)
    _make_read_only(target)


def _link_or_copy(source: str, target: str) -> str:
    try:
        os.link(source, target)
        return target
    except OSError:
        return shutil.copy2(source, target)


def _scan_tree(root: Path) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for path in sorted(root.rglob("*"), key=lambda item: item.relative_to(root).as_posix()):
        if path.is_symlink():
            raise RuntimeError("Wiki 不允许符号链接")
        if path.is_dir():
            continue
        if not path.is_file():
            raise RuntimeError("Wiki 不允许特殊文件")
        relative = normalize_wiki_path(path.relative_to(root).as_posix())
        entries.append({
            "path": relative,
            "size": path.stat().st_size,
            "sha256": file_sha256(path),
            "mime": mimetypes.guess_type(relative)[0] or "application/octet-stream",
        })
    return entries


def _tree_hash(manifest: list[dict[str, Any]]) -> str:
    canonical = json.dumps(manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _protected_hashes(root: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    for relative in ("purpose.md", "schema.md"):
        target = root / relative
        result[relative] = file_sha256(target)
    raw = root / "raw"
    if raw.is_dir():
        for path in sorted(raw.rglob("*")):
            if path.is_file() and not path.is_symlink():
                result[path.relative_to(root).as_posix()] = file_sha256(path)
    return result


def _verify_revision(owner_id: str, revision: WikiRevision) -> None:
    tree = _revision_tree(owner_id, revision.id)
    actual = _scan_tree(tree)
    if _tree_hash(actual) != revision.tree_hash or actual != _manifest(revision):
        raise RuntimeError("Wiki revision manifest 或 tree hash 不匹配")


def _safe_tree_file(root: Path, relative_path: str) -> Path:
    target = (root / normalize_wiki_path(relative_path)).resolve()
    try:
        target.relative_to(root.resolve())
    except ValueError:
        raise HTTPException(status_code=400, detail="Wiki 文件路径无效") from None
    return target


def _make_read_only(root: Path) -> None:
    for path in sorted(root.rglob("*"), reverse=True):
        path.chmod(0o555 if path.is_dir() else 0o444)
    root.chmod(0o555)


def _reset_writable(root: Path) -> None:
    root.chmod(0o755)
    for path in root.rglob("*"):
        path.chmod(0o755 if path.is_dir() else 0o644)


def _bounded_text(value: str, label: str, max_bytes: int) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise HTTPException(status_code=400, detail=f"{label}不能为空")
    if len(normalized.encode("utf-8")) > max_bytes:
        raise HTTPException(status_code=400, detail=f"{label}过长")
    if "\ufffd" in normalized:
        raise HTTPException(status_code=400, detail=f"{label}包含损坏字符")
    return normalized


def _short_error(exc: BaseException) -> str:
    detail = str(exc).strip() or exc.__class__.__name__
    return detail if len(detail) <= 500 else detail[:500] + "…"
