from __future__ import annotations

import json
import tomllib
from pathlib import Path
from typing import Any

from cryptography.fernet import Fernet, InvalidToken
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import CodexConfig
from app.schemas import CodexConfigContent, CodexConfigFile
from app.services.runtime_paths import codex_home, runtime_dir
from app.utils import now_utc

CODEX_CONFIG_ID = "codex"


def config_path() -> Path:
    return codex_home() / "config.toml"


def auth_path() -> Path:
    return codex_home() / "auth.json"


async def ensure_codex_config(db: AsyncSession, *, commit: bool = True) -> CodexConfig:
    row = await db.get(CodexConfig, CODEX_CONFIG_ID)
    if row is not None:
        return row
    row = CodexConfig(
        id=CODEX_CONFIG_ID,
        config_ciphertext=_encrypt_content(""),
        auth_ciphertext=_encrypt_content(""),
        updated_at=now_utc(),
    )
    db.add(row)
    if commit:
        await db.commit()
        await db.refresh(row)
    else:
        await db.flush()
    return row


async def get_config_content(db: AsyncSession) -> str:
    row = await ensure_codex_config(db)
    return _decrypt_content(row.config_ciphertext)


async def get_auth_content(db: AsyncSession) -> str:
    row = await ensure_codex_config(db)
    return _decrypt_content(row.auth_ciphertext)


async def read_codex_config(db: AsyncSession) -> CodexConfigFile:
    row = await ensure_codex_config(db)
    return CodexConfigFile(
        path=_display_path(config_path()),
        content=_decrypt_content(row.config_ciphertext),
        auth=CodexConfigContent(
            path=_display_path(auth_path()),
            content=_decrypt_content(row.auth_ciphertext),
        ),
    )


async def save_codex_config(
    db: AsyncSession,
    content: str,
    auth_content: str,
    *,
    commit: bool = True,
) -> None:
    validate_config_content(content)
    normalized_auth = normalize_auth_content(auth_content)
    validate_auth_content(normalized_auth)
    row = await ensure_codex_config(db, commit=False)
    row.config_ciphertext = _encrypt_content(content)
    row.auth_ciphertext = _encrypt_content(normalized_auth)
    if content.strip():
        row.config_user_modified = True
    if normalized_auth.strip():
        row.auth_user_modified = True
    row.updated_at = now_utc()
    if commit:
        await db.commit()
        await db.refresh(row)
    else:
        await db.flush()


async def codex_setup_completed(db: AsyncSession) -> bool:
    row = await ensure_codex_config(db)
    return bool(row.config_user_modified and row.auth_user_modified)


def write_runtime_config_files(config_content: str, auth_content: str) -> None:
    config_file = config_path()
    auth_file = auth_path()
    config_file.parent.mkdir(parents=True, exist_ok=True)
    config_file.write_text(config_content, encoding="utf-8")
    auth_file.write_text(auth_content, encoding="utf-8")


def normalize_auth_content(content: str) -> str:
    data = _json_object(content, "Codex auth")
    api_key = data.get("OPENAI_API_KEY")
    if isinstance(api_key, str) and "auth_mode" not in data:
        return json.dumps({"auth_mode": "apikey", **data}, ensure_ascii=False, indent=2) + "\n"
    return content


def validate_config_content(content: str) -> None:
    try:
        tomllib.loads(content)
    except tomllib.TOMLDecodeError as exc:
        raise HTTPException(status_code=400, detail=f"Codex 配置不是合法 TOML: {exc}") from None


def validate_auth_content(content: str) -> None:
    _json_object(content, "Codex auth")


def _json_object(content: str, label: str) -> dict[str, Any]:
    try:
        data = json.loads(content or "{}")
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail=f"{label} 配置不是合法 JSON: {exc.msg}") from None
    if not isinstance(data, dict):
        raise HTTPException(status_code=400, detail=f"{label} 配置必须是 JSON object")
    return data


def _display_path(path: Path) -> str:
    resolved = path.resolve()
    base = runtime_dir()
    try:
        return resolved.relative_to(base).as_posix()
    except ValueError:
        return resolved.as_posix()


def _fernet() -> Fernet:
    secret = get_settings().codex_config_secret.strip()
    if not secret:
        raise HTTPException(status_code=500, detail="CODEX_CONFIG_SECRET 缺失，无法读取 Codex 配置")
    try:
        return Fernet(secret.encode("utf-8"))
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=500, detail="CODEX_CONFIG_SECRET 格式非法，无法读取 Codex 配置") from exc


def _encrypt_content(content: str) -> str:
    return _fernet().encrypt(content.encode("utf-8")).decode("utf-8")


def _decrypt_content(ciphertext: str) -> str:
    try:
        return _fernet().decrypt(ciphertext.encode("utf-8")).decode("utf-8")
    except InvalidToken as exc:
        raise HTTPException(status_code=500, detail="Codex 配置解密失败，请检查 CODEX_CONFIG_SECRET") from exc
