from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fastapi import HTTPException, UploadFile

from app.config import get_settings
from app.schemas import UploadOut
from app.services.artifacts import signed_upload_download_url
from app.services.runtime_paths import uploads_dir
from app.utils import dumps, iso, loads, new_id, now_utc


@dataclass(frozen=True)
class UploadRef:
    """运行时供 Run.inputs / resume tool_result 复用的引用：
    包含落盘绝对路径（给 Agent 自行读取）、浏览器下载 URL 与基本元数据。后端不解析文件内容。"""

    id: str
    name: str
    mime: str
    size: int
    path: Path

    def download_url(self, user_id: str) -> str:
        return signed_upload_download_url(user_id, self.id)

    def to_tool_payload(self, user_id: str) -> dict[str, Any]:
        # spec §4：写进 tool_result.content[0].text JSON 字符串里的字段。
        return {
            "id": self.id,
            "name": self.name,
            "path": str(self.path),
            "download_url": self.download_url(user_id),
            "mime": self.mime,
            "size": self.size,
        }


def _user_root(user_id: str) -> Path:
    return uploads_dir(user_id)


def _entry_dir(user_id: str, upload_id: str) -> Path:
    return _user_root(user_id) / upload_id


def _meta_path(entry: Path) -> Path:
    return entry / "meta.json"


def _read_meta(entry: Path) -> dict[str, Any] | None:
    meta_file = _meta_path(entry)
    if not meta_file.exists():
        return None
    raw = meta_file.read_text(encoding="utf-8")
    data = loads(raw, None)
    return data if isinstance(data, dict) else None


def is_upload_id(value: str | None) -> bool:
    return bool(value and value.startswith("upl_") and "/" not in value and "\\" not in value)


async def save_upload(user_id: str, file: UploadFile) -> UploadOut:
    """落盘一个用户附件，返回 UploadOut。后端不嗅探文件类型，mime 直接采用客户端声明。"""

    raw = await file.read()
    if not raw:
        raise HTTPException(status_code=400, detail="未提供文件")
    max_size = get_settings().max_upload_bytes
    if len(raw) > max_size:
        raise HTTPException(status_code=400, detail="文件超出大小限制")

    upload_id = new_id("upl")
    name = (file.filename or upload_id).strip() or upload_id
    mime = (file.content_type or "application/octet-stream").strip() or "application/octet-stream"
    return write_upload(user_id, upload_id, raw, name, mime)


def write_upload(user_id: str, upload_id: str, raw: bytes, name: str, mime: str) -> UploadOut:
    if not raw:
        raise HTTPException(status_code=400, detail="未提供文件")
    if not is_upload_id(upload_id):
        raise HTTPException(status_code=400, detail="upload id 无效")
    entry = _entry_dir(user_id, upload_id)
    entry.mkdir(parents=True, exist_ok=True)
    blob = entry / "blob"
    blob.write_bytes(raw)

    safe_name = name.strip() or upload_id
    safe_mime = mime.strip() or "application/octet-stream"
    created_at = iso(now_utc()) or ""
    meta = {
        "id": upload_id,
        "name": safe_name,
        "mime": safe_mime,
        "size": len(raw),
        "created_at": created_at,
        "blob": "blob",
    }
    _meta_path(entry).write_text(dumps(meta), encoding="utf-8")
    return UploadOut(id=upload_id, name=safe_name, mime=safe_mime, size=len(raw), created_at=created_at)


def seed_upload_from_file(user_id: str, upload_id: str, source: Path, name: str, mime: str) -> UploadOut | None:
    if not source.is_file():
        return None
    return write_upload(user_id, upload_id, source.read_bytes(), name, mime)


def copy_upload(source_user_id: str, target_user_id: str, upload_id: str) -> UploadOut | None:
    source = resolve_upload(source_user_id, upload_id)
    if source is None:
        return None
    return write_upload(target_user_id, new_id("upl"), source.path.read_bytes(), source.name, source.mime)


def resolve_upload(user_id: str, upload_id: str) -> UploadRef | None:
    """根据 upload_id 解析出落盘路径与元数据；不存在 / 不归属当前用户时返回 None。"""

    if not upload_id or "/" in upload_id or "\\" in upload_id:
        return None
    entry = _entry_dir(user_id, upload_id)
    if not entry.is_dir():
        return None
    meta = _read_meta(entry)
    if not meta:
        return None
    blob_name = str(meta.get("blob") or "blob")
    blob = (entry / blob_name).resolve()
    if not blob.is_file():
        return None
    return UploadRef(
        id=upload_id,
        name=str(meta.get("name") or upload_id),
        mime=str(meta.get("mime") or "application/octet-stream"),
        size=int(meta.get("size") or 0),
        path=blob,
    )


def delete_upload(user_id: str, upload_id: str) -> None:
    """随 Run 删除时清理。不存在时静默返回。"""

    if not upload_id or "/" in upload_id or "\\" in upload_id:
        return
    entry = _entry_dir(user_id, upload_id)
    if entry.exists():
        shutil.rmtree(entry, ignore_errors=True)
