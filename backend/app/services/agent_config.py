from __future__ import annotations

import json
import tomllib
from pathlib import Path
from typing import Any, Literal

from cryptography.fernet import Fernet, InvalidToken
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import AgentConfig
from app.schemas import AgentConfigFile
from app.services.runtime_paths import claude_home, codex_home, runtime_dir
from app.utils import now_utc

AgentId = Literal["claude-code", "codex", "codex-auth"]
AGENT_CONFIG_IDS: tuple[AgentId, ...] = ("claude-code", "codex", "codex-auth")


def config_path(agent_id: AgentId) -> Path:
    if agent_id == "claude-code":
        return claude_home() / ".claude" / "settings.json"
    if agent_id == "codex":
        return codex_home() / "config.toml"
    if agent_id == "codex-auth":
        return codex_home() / "auth.json"
    raise HTTPException(status_code=404, detail="未知 Agent")


async def ensure_agent_config_rows(db: AsyncSession, *, commit: bool = True) -> None:
    created = False
    for agent_id in AGENT_CONFIG_IDS:
        row = await db.get(AgentConfig, agent_id)
        if row is not None:
            continue
        content = default_content(agent_id)
        validate_content(agent_id, content)
        content = normalize_content(agent_id, content)
        validate_content(agent_id, content)
        db.add(
            AgentConfig(
                agent_id=agent_id,
                content_ciphertext=_encrypt_content(content),
                updated_at=now_utc(),
            )
        )
        created = True
    if not created:
        return
    if commit:
        await db.commit()
    else:
        await db.flush()


async def get_config_content(db: AsyncSession, agent_id: AgentId) -> str:
    row = await _get_or_create_row(db, agent_id)
    return _decrypt_content(row.content_ciphertext)


async def read_config_file(db: AsyncSession, agent_id: AgentId) -> AgentConfigFile:
    content = await get_config_content(db, agent_id)
    return AgentConfigFile(
        agent_id=agent_id,
        path=_display_path(config_path(agent_id)),
        content=content,
    )


async def write_config_file(
    db: AsyncSession,
    agent_id: AgentId,
    content: str,
    *,
    commit: bool = True,
    mark_user_modified: bool = True,
) -> AgentConfigFile:
    validate_content(agent_id, content)
    content = normalize_content(agent_id, content)
    row = await db.get(AgentConfig, agent_id)
    if row is None:
        row = AgentConfig(agent_id=agent_id, content_ciphertext="", updated_at=now_utc())
        db.add(row)
    row.content_ciphertext = _encrypt_content(content)
    if mark_user_modified and content.strip():
        row.user_modified = True
    row.updated_at = now_utc()
    if commit:
        await db.commit()
        await db.refresh(row)
    else:
        await db.flush()
    return AgentConfigFile(
        agent_id=agent_id,
        path=_display_path(config_path(agent_id)),
        content=content,
    )


async def agent_setup_completed(db: AsyncSession) -> bool:
    await ensure_agent_config_rows(db)
    claude = await db.get(AgentConfig, "claude-code")
    codex = await db.get(AgentConfig, "codex")
    codex_auth = await db.get(AgentConfig, "codex-auth")
    return bool(
        (claude and claude.user_modified)
        or (codex and codex.user_modified and codex_auth and codex_auth.user_modified)
    )


def write_runtime_config_file(agent_id: AgentId, content: str) -> None:
    path = config_path(agent_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def default_content(agent_id: AgentId) -> str:
    if agent_id in AGENT_CONFIG_IDS:
        return ""
    raise HTTPException(status_code=404, detail="未知 Agent")


async def _get_or_create_row(db: AsyncSession, agent_id: AgentId) -> AgentConfig:
    await ensure_agent_config_rows(db)
    row = await db.get(AgentConfig, agent_id)
    if row is None:
        raise HTTPException(status_code=404, detail="未知 Agent")
    return row


def _display_path(path: Path) -> str:
    resolved = path.resolve()
    base = runtime_dir()
    try:
        return resolved.relative_to(base).as_posix()
    except ValueError:
        return resolved.as_posix()


def _fernet() -> Fernet:
    secret = get_settings().agent_config_secret.strip()
    if not secret:
        raise HTTPException(status_code=500, detail="AGENT_CONFIG_SECRET 缺失，无法读取 Agent 配置")
    try:
        return Fernet(secret.encode("utf-8"))
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=500, detail="AGENT_CONFIG_SECRET 格式非法，无法读取 Agent 配置") from exc


def _encrypt_content(content: str) -> str:
    return _fernet().encrypt(content.encode("utf-8")).decode("utf-8")


def _decrypt_content(ciphertext: str) -> str:
    try:
        return _fernet().decrypt(ciphertext.encode("utf-8")).decode("utf-8")
    except InvalidToken as exc:
        raise HTTPException(status_code=500, detail="Agent 配置解密失败，请检查 AGENT_CONFIG_SECRET") from exc


def normalize_content(agent_id: AgentId, content: str) -> str:
    if agent_id == "codex":
        return normalize_codex_config_content(content)
    if agent_id == "codex-auth":
        return normalize_codex_auth_content(content)
    return content


def normalize_codex_config_content(content: str) -> str:
    tomllib.loads(content)
    return content


def normalize_codex_auth_content(content: str) -> str:
    data = _json_object(content, "Codex auth")
    api_key = data.get("OPENAI_API_KEY")
    if isinstance(api_key, str) and "auth_mode" not in data:
        return json.dumps({"auth_mode": "apikey", **data}, ensure_ascii=False, indent=2) + "\n"
    return content


def _json_object(content: str, label: str) -> dict[str, Any]:
    try:
        data = json.loads(content or "{}")
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail=f"{label} 配置不是合法 JSON: {exc.msg}") from None
    if not isinstance(data, dict):
        raise HTTPException(status_code=400, detail=f"{label} 配置必须是 JSON object")
    return data


def validate_content(agent_id: AgentId, content: str) -> None:
    try:
        if agent_id == "claude-code":
            data = json.loads(content or "{}")
            if not isinstance(data, dict):
                raise HTTPException(status_code=400, detail="Claude 配置必须是 JSON object")
        elif agent_id == "codex":
            tomllib.loads(content)
        elif agent_id == "codex-auth":
            _json_object(content, "Codex auth")
        else:
            raise HTTPException(status_code=404, detail="未知 Agent")
    except json.JSONDecodeError as exc:
        label = "Claude" if agent_id == "claude-code" else "Codex auth"
        raise HTTPException(status_code=400, detail=f"{label} 配置不是合法 JSON: {exc.msg}") from None
    except tomllib.TOMLDecodeError as exc:
        raise HTTPException(status_code=400, detail=f"Codex 配置不是合法 TOML: {exc}") from None
