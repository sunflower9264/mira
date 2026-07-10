from __future__ import annotations

import html
import re
from datetime import timedelta
from pathlib import Path
from typing import Literal
from urllib.parse import quote

import jwt
from fastapi import HTTPException

from app.config import get_settings
from app.models import Run
from app.services.runtime_paths import run_workspace
from app.utils import now_utc

DOWNLOAD_TOKEN_TTL_DAYS = 30

_ABSOLUTE_PATH_RE = re.compile(r"/[^\s`\"'<>()\[\]{}]+")
_TRAILING_PUNCTUATION = ".,，。;；:：!！?？"


def signed_upload_download_url(user_id: str, upload_id: str) -> str:
    token = _encode_download_token({"kind": "upload", "sub": user_id, "upload_id": upload_id})
    return f"/api/uploads/{quote(upload_id, safe='')}?download_token={quote(token, safe='')}"


def signed_run_artifact_download_url(run: Run, relative_path: str) -> str:
    token = _encode_download_token({
        "kind": "run_artifact",
        "sub": run.owner_id,
        "run_id": run.id,
        "path": relative_path,
    })
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


def verify_run_artifact_download_token(run_id: str, relative_path: str, token: str) -> str:
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
    return user_id


def resolve_run_artifact(run: Run, relative_path: str) -> Path | None:
    if not relative_path or relative_path.startswith("/") or "\\" in relative_path:
        return None
    workspace = run_workspace(run.owner_id, run.app_id, run.id).resolve()
    candidate = (workspace / relative_path).resolve()
    try:
        candidate.relative_to(workspace)
    except ValueError:
        return None
    if not candidate.is_file():
        return None
    return candidate


def replace_workspace_paths_for_prompt(text: str, run: Run) -> str:
    return _replace_workspace_paths(text, run, mode="prompt")


def replace_workspace_paths_in_html(text: str, run: Run) -> str:
    return _replace_workspace_paths(text, run, mode="html")


def replace_workspace_paths_for_prompt_with_workspace(text: str, run: Run, workspace: Path, workspace_text: str) -> str:
    return _replace_workspace_paths(text, run, mode="prompt", workspace=workspace, workspace_text=workspace_text)


def replace_workspace_paths_in_html_with_workspace(text: str, run: Run, workspace: Path, workspace_text: str) -> str:
    return _replace_workspace_paths(text, run, mode="html", workspace=workspace, workspace_text=workspace_text)


def _replace_workspace_paths(
    text: str,
    run: Run,
    *,
    mode: Literal["prompt", "html"],
    workspace: Path | None = None,
    workspace_text: str | None = None,
) -> str:
    if not text:
        return text
    if workspace is None:
        workspace = run_workspace(run.owner_id, run.app_id, run.id).resolve()
    if workspace_text is None:
        workspace_text = str(workspace)
    if workspace_text not in text:
        return text

    matches: dict[str, str] = {}
    for match in _ABSOLUTE_PATH_RE.finditer(text):
        raw = match.group(0)
        stripped = raw.rstrip(_TRAILING_PUNCTUATION)
        if not stripped.startswith(workspace_text):
            continue
        replacement = _replacement_for_path(stripped, raw[len(stripped):], run, workspace, mode)
        if replacement is not None:
            matches[raw] = replacement

    result = text
    for raw, replacement in sorted(matches.items(), key=lambda item: len(item[0]), reverse=True):
        result = result.replace(raw, replacement)
    return result


def _replacement_for_path(
    raw_path: str,
    suffix: str,
    run: Run,
    workspace: Path,
    mode: Literal["prompt", "html"],
) -> str | None:
    path = Path(raw_path).resolve()
    try:
        relative = path.relative_to(workspace).as_posix()
    except ValueError:
        return None
    name = path.name or relative
    if path.is_file():
        url = signed_run_artifact_download_url(run, relative)
        if mode == "html":
            return f'<a href="{html.escape(url, quote=True)}" download>{html.escape(name)}</a>{suffix}'
        return f'{name} (download_url: {url}){suffix}'
    if path.is_dir():
        if mode == "html":
            return f'{html.escape(name)}（目录，请下载对应压缩包）{suffix}'
        return f'{name}（目录，请下载对应压缩包）{suffix}'
    return None


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
