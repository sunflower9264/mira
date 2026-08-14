from __future__ import annotations

import hashlib
import html
import json
import re
import shutil
from datetime import timedelta
from pathlib import Path
from typing import Any, Literal
from urllib.parse import quote

import jwt
from fastapi import HTTPException

from app.config import get_settings
from app.models import Run
from app.services.runtime_paths import runtime_dir, run_workspace, scoped_runtime_home
from app.utils import now_utc

DOWNLOAD_TOKEN_TTL_DAYS = 30

_ABSOLUTE_PATH_RE = re.compile(r"/[^\s`\"'<>()\[\]{}]+")
_TRAILING_PUNCTUATION = ".,，。;；:：!！?？"
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".svg"}
GENERATED_IMAGES_DIR = "generated_images"
_CONTAINER_GENERATED_PREFIX = "/home/mira/generated_images/"
_PLACEHOLDER_RE = re.compile(r"GPT 图片暂不可渲染")


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


def is_workspace_image_file(path: Path) -> bool:
    return path.is_file() and path.suffix.lower() in IMAGE_SUFFIXES


def import_runtime_images(value: Any, *, workspace: Path) -> Any:
    workspace = workspace.resolve()
    replacements = _collect_image_imports(value, workspace)
    if not replacements:
        return value
    return _rewrite_imported_paths(value, replacements)


def fill_image_download_urls(value: Any, run: Run, workspace: Path) -> Any:
    workspace = workspace.resolve()
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return value
        if isinstance(parsed, (dict, list)):
            return json.dumps(fill_image_download_urls(parsed, run, workspace), ensure_ascii=False)
        return value
    if isinstance(value, dict):
        updated = {key: fill_image_download_urls(item, run, workspace) for key, item in value.items()}
        source = updated.get("artifact_id") or updated.get("path") or updated.get("image_url")
        url = _signed_workspace_image_url(source, run, workspace)
        if url:
            current = updated.get("image_url")
            if not isinstance(current, str) or not current.strip() or _looks_like_local_path(current):
                updated["image_url"] = url
            if updated.get("render_status") in {None, "", "artifact_only"}:
                updated["render_status"] = "renderable"
        return updated
    if isinstance(value, list):
        return [fill_image_download_urls(item, run, workspace) for item in value]
    return value


def collect_workspace_image_refs(value: Any, run: Run, workspace: Path) -> list[tuple[str, str]]:
    workspace = workspace.resolve()
    refs: list[tuple[str, str]] = []
    seen: set[str] = set()

    def walk(item: Any) -> None:
        if isinstance(item, dict):
            url = item.get("image_url")
            name = item.get("image_id") or item.get("name") or item.get("artifact_id")
            if isinstance(url, str) and url.startswith("/api/runs/") and url not in seen:
                seen.add(url)
                alt = name if isinstance(name, str) and name.strip() else "generated image"
                refs.append((url, Path(str(alt)).name))
            source = item.get("artifact_id") or item.get("path") or item.get("image_url")
            signed = _signed_workspace_image_url(source, run, workspace)
            if signed and signed not in seen:
                seen.add(signed)
                alt = name if isinstance(name, str) and name.strip() else Path(str(source)).name
                refs.append((signed, Path(str(alt)).name))
            for child in item.values():
                walk(child)
            return
        if isinstance(item, list):
            for child in item:
                walk(child)
            return
        if isinstance(item, str):
            try:
                parsed = json.loads(item)
            except json.JSONDecodeError:
                parsed = None
            if isinstance(parsed, (dict, list)):
                walk(parsed)
                return
            candidates = [item]
            candidates.extend(match.group(0).rstrip(_TRAILING_PUNCTUATION) for match in _ABSOLUTE_PATH_RE.finditer(item))
            for candidate in candidates:
                signed = _signed_workspace_image_url(candidate, run, workspace)
                if signed and signed not in seen:
                    seen.add(signed)
                    refs.append((signed, Path(candidate).name))

    walk(value)
    return refs


def ensure_html_images(html_text: str, image_refs: list[tuple[str, str]]) -> str:
    if not html_text or not image_refs:
        return html_text
    result = html_text
    pending = [(url, alt) for url, alt in image_refs if url not in result]
    for url, alt in pending:
        tag = (
            f'<img src="{html.escape(url, quote=True)}" alt="{html.escape(alt, quote=True)}" '
            'style="max-width:100%;height:auto">'
        )
        updated, count = _PLACEHOLDER_RE.subn(tag, result, count=1)
        if count:
            result = updated
            continue
        result = _insert_html_before_close(result, tag)
    return result


def _collect_image_imports(value: Any, workspace: Path) -> dict[str, str]:
    replacements: dict[str, str] = {}

    def walk(item: Any) -> None:
        if isinstance(item, dict):
            for child in item.values():
                walk(child)
            return
        if isinstance(item, list):
            for child in item:
                walk(child)
            return
        if not isinstance(item, str) or "/" not in item:
            return
        if _looks_like_local_path(item):
            imported = _import_one_image(item, workspace)
            if imported:
                replacements[item] = imported
            return
        for match in _ABSOLUTE_PATH_RE.finditer(item):
            raw = match.group(0).rstrip(_TRAILING_PUNCTUATION)
            imported = _import_one_image(raw, workspace)
            if imported:
                replacements[raw] = imported

    walk(value)
    return replacements


def _import_one_image(raw_path: str, workspace: Path) -> str | None:
    source = _resolve_importable_image(raw_path, workspace)
    if source is None:
        return None
    try:
        relative = source.relative_to(workspace)
    except ValueError:
        dest = _destination_for_imported_image(workspace, source)
        dest.parent.mkdir(parents=True, exist_ok=True)
        if dest.resolve() != source:
            shutil.copy2(source, dest)
        return str(dest)
    return str(workspace / relative)


def _resolve_importable_image(raw_path: str, workspace: Path) -> Path | None:
    candidate = _container_generated_image(raw_path, workspace)
    if candidate is None:
        try:
            candidate = Path(raw_path)
        except (OSError, ValueError):
            return None
    try:
        resolved = candidate.resolve()
    except OSError:
        return None
    if not _is_allowed_image_source(resolved, workspace):
        return None
    return resolved


def _container_generated_image(raw_path: str, workspace: Path) -> Path | None:
    if not raw_path.startswith(_CONTAINER_GENERATED_PREFIX):
        return None
    relative = raw_path[len(_CONTAINER_GENERATED_PREFIX) :]
    if not relative or relative.startswith("/") or ".." in Path(relative).parts:
        return None
    return scoped_runtime_home("codex_home", workspace) / GENERATED_IMAGES_DIR / relative


def _is_allowed_image_source(path: Path, workspace: Path) -> bool:
    if not is_workspace_image_file(path):
        return False
    try:
        path.relative_to(workspace)
        return True
    except ValueError:
        pass
    scoped_root = (runtime_dir() / "homes" / "_scoped").resolve()
    try:
        relative = path.relative_to(scoped_root)
    except ValueError:
        return False
    return GENERATED_IMAGES_DIR in relative.parts


def _destination_for_imported_image(workspace: Path, source: Path) -> Path:
    dest_dir = workspace / GENERATED_IMAGES_DIR
    stem = source.stem or "image"
    suffix = source.suffix.lower() or ".png"
    candidate = dest_dir / f"{stem}{suffix}"
    if candidate.exists() and file_sha256(candidate) == file_sha256(source):
        return candidate
    index = 2
    while candidate.exists():
        candidate = dest_dir / f"{stem}_{index}{suffix}"
        if candidate.exists() and file_sha256(candidate) == file_sha256(source):
            return candidate
        index += 1
    return candidate


def _rewrite_imported_paths(value: Any, replacements: dict[str, str]) -> Any:
    if isinstance(value, dict):
        return {key: _rewrite_imported_paths(item, replacements) for key, item in value.items()}
    if isinstance(value, list):
        return [_rewrite_imported_paths(item, replacements) for item in value]
    if not isinstance(value, str):
        return value
    if value in replacements:
        return replacements[value]
    result = value
    for source, dest in sorted(replacements.items(), key=lambda item: len(item[0]), reverse=True):
        result = result.replace(source, dest)
    return result


def _signed_workspace_image_url(value: Any, run: Run, workspace: Path) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        path = Path(value).resolve()
        relative = path.relative_to(workspace).as_posix()
    except (OSError, RuntimeError, ValueError):
        return None
    if not is_workspace_image_file(path):
        return None
    return signed_run_artifact_download_url(run, relative, file_sha256(path))


def _looks_like_local_path(value: str) -> bool:
    if value.startswith("/api/"):
        return False
    return value.startswith("/") or value.startswith(_CONTAINER_GENERATED_PREFIX)


def _insert_html_before_close(html_text: str, snippet: str) -> str:
    for closer in ("</body>", "</html>", "</article>"):
        index = html_text.lower().rfind(closer)
        if index >= 0:
            return f"{html_text[:index]}{snippet}{html_text[index:]}"
    return f"{html_text}{snippet}"


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
        before = text[max(0, match.start() - 10) : match.start()].lower()
        in_src = before.endswith('src="') or before.endswith("src='")
        replacement = _replacement_for_path(
            stripped,
            raw[len(stripped) :],
            run,
            workspace,
            mode,
            in_src=in_src,
        )
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
    *,
    in_src: bool = False,
) -> str | None:
    path = Path(raw_path).resolve()
    try:
        relative = path.relative_to(workspace).as_posix()
    except ValueError:
        return None
    name = path.name or relative
    if path.is_file():
        url = signed_run_artifact_download_url(run, relative, file_sha256(path))
        if mode == "html":
            escaped_url = html.escape(url, quote=True)
            if in_src:
                return f"{escaped_url}{suffix}"
            if is_workspace_image_file(path):
                return (
                    f'<img src="{escaped_url}" alt="{html.escape(name, quote=True)}" '
                    f'style="max-width:100%;height:auto">{suffix}'
                )
            return f'<a href="{escaped_url}" download>{html.escape(name)}</a>{suffix}'
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
