from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Query, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.db import get_db
from app.models import User
from app.schemas import (
    WikiAccessGrantIn,
    WikiAccessOut,
    WikiFileContentOut,
    WikiFileOut,
    WikiLintOut,
    WikiMaintenanceIn,
    WikiOperationOut,
    WikiOut,
    WikiPatchIn,
    WikiRevisionOut,
    WikiSourceOut,
    WikiSourcePatchIn,
)
from app.services.artifacts import verify_wiki_file_download_token
from app.services.wiki import (
    cancel_operation,
    create_source,
    delete_source,
    enqueue_maintenance,
    grant_wiki_access,
    lint_wiki,
    list_operations,
    list_revisions,
    list_sources,
    list_tree,
    patch_wiki,
    read_file_content,
    rename_source,
    resolve_file_download,
    restore_revision,
    retry_operation,
    revoke_wiki_access,
    schedule_operation,
    wiki_access_status,
    wiki_out,
)

router = APIRouter(tags=["wiki"])


@router.get("/wiki", response_model=WikiOut)
async def get_wiki(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)) -> WikiOut:
    return await wiki_out(db, user.id)


@router.patch("/wiki", response_model=WikiOut)
async def update_wiki(
    payload: WikiPatchIn,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> WikiOut:
    return await patch_wiki(db, user.id, purpose=payload.purpose, schema=payload.schema_)


@router.get("/wiki/sources", response_model=list[WikiSourceOut])
async def get_sources(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    return await list_sources(db, user.id)


@router.post("/wiki/sources")
async def upload_source(
    file: UploadFile,
    path: str | None = Form(default=None),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    source, operation = await create_source(db, user.id, file, path)
    return {"source": source, "operation": operation}


@router.patch("/wiki/sources/{source_id}", response_model=WikiSourceOut)
async def patch_source(
    source_id: str,
    payload: WikiSourcePatchIn,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> WikiSourceOut:
    return await rename_source(db, user.id, source_id, payload.path)


@router.delete("/wiki/sources/{source_id}", response_model=WikiOperationOut)
async def remove_source(
    source_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> WikiOperationOut:
    return await delete_source(db, user.id, source_id)


@router.get("/wiki/tree", response_model=list[WikiFileOut])
async def get_tree(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    return await list_tree(db, user.id)


@router.get("/wiki/content", response_model=WikiFileContentOut)
async def get_file_content(
    path: str = Query(...),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> WikiFileContentOut:
    return await read_file_content(db, user.id, path)


@router.get("/wiki/files/{path:path}")
async def download_file(
    path: str,
    revision_id: str = Query(...),
    sha256: str = Query(...),
    download_token: str = Query(...),
    inline: bool = Query(False),
    db: AsyncSession = Depends(get_db),
) -> FileResponse:
    owner_id = verify_wiki_file_download_token(revision_id, path, sha256, download_token)
    target, mime, name = await resolve_file_download(db, owner_id, revision_id, path, sha256)
    return FileResponse(target, media_type=mime, filename=name, content_disposition_type="inline" if inline else "attachment")


@router.get("/wiki/operations", response_model=list[WikiOperationOut])
async def get_operations(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    return await list_operations(db, user.id)


@router.post("/wiki/operations/{operation_id}/retry", response_model=WikiOperationOut)
async def retry(
    operation_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> WikiOperationOut:
    return await retry_operation(db, user.id, operation_id)


@router.post("/wiki/operations/{operation_id}/cancel", response_model=WikiOperationOut)
async def cancel(
    operation_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> WikiOperationOut:
    return await cancel_operation(db, user.id, operation_id)


@router.post("/wiki/maintenance", response_model=WikiOperationOut)
async def maintain(
    payload: WikiMaintenanceIn,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> WikiOperationOut:
    operation = await enqueue_maintenance(db, user.id, payload.instruction)
    schedule_operation(operation.id)
    return WikiOperationOut.model_validate({
        "id": operation.id,
        "source_id": operation.source_id,
        "kind": operation.kind,
        "status": operation.status,
        "instruction": operation.instruction,
        "error": operation.error,
        "created_at": operation.created_at.isoformat(),
        "started_at": None,
        "finished_at": None,
    })


@router.post("/wiki/lint", response_model=WikiLintOut)
async def lint(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)) -> WikiLintOut:
    return await lint_wiki(db, user.id)


@router.get("/wiki/revisions", response_model=list[WikiRevisionOut])
async def revisions(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    return await list_revisions(db, user.id)


@router.post("/wiki/revisions/{revision_id}/restore", response_model=WikiRevisionOut)
async def restore(
    revision_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> WikiRevisionOut:
    return await restore_revision(db, user.id, revision_id)


@router.get("/apps/{app_id}/wiki-access", response_model=WikiAccessOut)
async def get_access(
    app_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> WikiAccessOut:
    return await wiki_access_status(db, user.id, app_id)


@router.post("/apps/{app_id}/wiki-access", response_model=WikiAccessOut)
async def grant_access(
    app_id: str,
    payload: WikiAccessGrantIn,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> WikiAccessOut:
    return await grant_wiki_access(db, user.id, app_id, payload.graph_sha256)


@router.delete("/apps/{app_id}/wiki-access", status_code=204)
async def revoke_access(
    app_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> None:
    await revoke_wiki_access(db, user.id, app_id)
