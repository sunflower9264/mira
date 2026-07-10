from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse

from app.api.deps import get_current_user, get_current_user_optional
from app.models import User
from app.schemas import UploadOut
from app.services.artifacts import verify_upload_download_token
from app.services.uploads import resolve_upload, save_upload

router = APIRouter(tags=["uploads"])


@router.post("/uploads", response_model=UploadOut)
async def create_upload(
    file: UploadFile,
    user: User = Depends(get_current_user),
) -> UploadOut:
    return await save_upload(user.id, file)


@router.get("/uploads/{upload_id}")
async def get_upload(
    upload_id: str,
    download_token: str | None = Query(default=None),
    user: User | None = Depends(get_current_user_optional),
) -> FileResponse:
    user_id = verify_upload_download_token(upload_id, download_token) if download_token else user.id if user else None
    if user_id is None:
        raise HTTPException(status_code=401, detail="未登录")
    ref = resolve_upload(user_id, upload_id)
    if ref is None:
        raise HTTPException(status_code=404, detail="附件不存在")
    return FileResponse(ref.path, media_type=ref.mime, filename=ref.name)
