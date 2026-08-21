from __future__ import annotations

import hashlib
from datetime import timedelta
from pathlib import Path
from urllib.parse import quote

import jwt
from fastapi import HTTPException

from app.config import get_settings
from app.models import Run
from app.utils import now_utc

DOWNLOAD_TOKEN_TTL_DAYS = 30


def signed_upload_download_url(user_id: str, upload_id: str) -> str:
    token = _encode_download_token({"kind": "upload", "sub": user_id, "upload_id": upload_id})
    return f"/api/uploads/{quote(upload_id, safe='')}?download_token={quote(token, safe='')}"


def signed_run_artifact_download_url(run: Run, relative_path: str, sha256: str | None = None) -> str:
    payload: dict[str, object] = {
        "kind": "run_artifact",
        "sub": run.owner_id,
        "run_id": run.id,
        "path": relative_path,
    }
    if sha256:
        payload["sha256"] = sha256
    token = _encode_download_token(payload)
    encoded_path = quote(relative_path, safe="/")
    return f"/api/runs/{quote(run.id, safe='')}/artifacts/{encoded_path}?download_token={quote(token, safe='')}"


def verify_upload_download_token(upload_id: str, token: str) -> str:
    payload = _decode_download_token(token)
    if payload.get("kind") != "upload" or payload.get("upload_id") != upload_id:
        raise HTTPException(status_code=401, detail="下载链接已失效")
    user_id = payload.get("sub")
    if not isinstance(user_id, str) or not user_id:
        raise HTTPException(status_code=401, detail="下载链接已失效")
    return user_id


def verify_run_artifact_download_token(
    run_id: str,
    relative_path: str,
    token: str,
    *,
    sha256: str | None = None,
    allow_missing_sha256: bool = False,
) -> str:
    payload = _decode_download_token(token)
    if (
        payload.get("kind") != "run_artifact"
        or payload.get("run_id") != run_id
        or payload.get("path") != relative_path
    ):
        raise HTTPException(status_code=401, detail="下载链接已失效")
    user_id = payload.get("sub")
    if not isinstance(user_id, str) or not user_id:
        raise HTTPException(status_code=401, detail="下载链接已失效")
    token_sha256 = payload.get("sha256")
    if sha256 is not None:
        if token_sha256 is None:
            if not allow_missing_sha256:
                raise HTTPException(status_code=401, detail="下载链接已失效")
        elif token_sha256 != sha256:
            raise HTTPException(status_code=401, detail="下载链接已失效")
    return user_id


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _encode_download_token(payload: dict[str, object]) -> str:
    now = now_utc()
    body = {
        **payload,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(days=DOWNLOAD_TOKEN_TTL_DAYS)).timestamp()),
    }
    return jwt.encode(body, get_settings().jwt_secret, algorithm="HS256")


def _decode_download_token(token: str) -> dict[str, object]:
    try:
        payload = jwt.decode(token, get_settings().jwt_secret, algorithms=["HS256"])
    except jwt.PyJWTError as exc:
        raise HTTPException(status_code=401, detail="下载链接已失效") from exc
    if not isinstance(payload, dict):
        raise HTTPException(status_code=401, detail="下载链接已失效")
    return payload
