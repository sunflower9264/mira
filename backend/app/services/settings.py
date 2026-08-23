from __future__ import annotations

import json
from pathlib import Path

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import SettingsRow, Skill
from app.schemas import McpServerConfig, MiraSettings
from app.utils import dumps, iso, loads, now_utc

from .serializers import skill_to_config
from .tools import tool_inventory


AGENTS_SEED_PATH = Path(__file__).resolve().parents[2] / "seeds" / "agents.json"
NO_ENABLED_AGENT_DETAIL = "无可用 Agent，请先在设置中启用 Agent"


def default_agents(seed_path: Path = AGENTS_SEED_PATH) -> list[dict]:
    checked_at = iso(now_utc())
    if not seed_path.exists():
        return []
    raw = json.loads(seed_path.read_text(encoding="utf-8"))
    return [
        {
            **agent,
            "status": {"installed": False, "runnable": None, "checked_at": checked_at},
        }
        for agent in raw
    ]


async def get_or_create_settings_row(db: AsyncSession) -> SettingsRow:
    """读取 / 兜底创建全局 settings 行（owner_id=ADMIN_USER_ID）。

    正常流程下 ``scripts/init_admin.py`` 已在初始化阶段建好这一行；这里
    保留 fallback 创建，避免极端情况下（admin 未初始化）API 直接 500。
    """
    from app.services.admin import ADMIN_USER_ID

    row = await db.get(SettingsRow, ADMIN_USER_ID)
    if row:
        return row
    row = SettingsRow(
        owner_id=ADMIN_USER_ID,
        agents_json=dumps(default_agents()),
        skills_json=dumps([]),
        mcp_servers_json=dumps([]),
        updated_at=now_utc(),
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return row


def _clean_agent(agent: dict) -> dict:
    return {key: value for key, value in agent.items() if key not in {"api_key", "permission_mode"}}


def normalize_supported_models(values: list[str] | None) -> list[str]:
    seen: set[str] = set()
    models: list[str] = []
    for value in values or []:
        if value is None:
            continue
        model = value.strip()
        if not model or model in seen:
            continue
        seen.add(model)
        models.append(model)
    if not models:
        raise HTTPException(status_code=400, detail="请至少填写一个支持模型")
    return models


async def settings_out(db: AsyncSession, reveal_keys: bool = False) -> MiraSettings:
    from app.services.admin import ADMIN_USER_ID

    row = await get_or_create_settings_row(db)
    skills = (await db.execute(select(Skill).where(Skill.owner_id == ADMIN_USER_ID))).scalars().all()
    agents = [_clean_agent(agent) for agent in loads(row.agents_json, default_agents())]
    mcp_servers = loads(row.mcp_servers_json, [])
    return MiraSettings(
        agents=agents,
        skills=[skill_to_config(skill) for skill in skills],
        mcp_servers=mcp_servers if reveal_keys else _redact_mcp_headers(mcp_servers),
        tools=tool_inventory(skills, mcp_servers),
    )


def _redact_mcp_headers(mcp_servers: list[dict]) -> list[dict]:
    redacted = []
    for item in mcp_servers:
        if not isinstance(item, dict):
            redacted.append(item)
            continue
        server = dict(item)
        headers = server.get("headers")
        if isinstance(headers, list):
            server["headers"] = [
                {**header, "value": ""} if isinstance(header, dict) else header
                for header in headers
            ]
        redacted.append(server)
    return redacted


async def save_agent_config_metadata(
    db: AsyncSession,
    agent_id: str,
    *,
    enabled: bool | None,
    supported_models: list[str],
    commit: bool = True,
) -> MiraSettings:
    row = await get_or_create_settings_row(db)
    agents = loads(row.agents_json, default_agents())
    updated_agents = []
    for agent in agents:
        cleaned = _clean_agent(agent)
        if cleaned.get("id") == agent_id:
            cleaned = {**cleaned, "supported_models": supported_models}
            if enabled is not None:
                cleaned = {**cleaned, "enabled": enabled}
        updated_agents.append(cleaned)
    row.agents_json = dumps(updated_agents)
    row.updated_at = now_utc()
    if commit:
        await db.commit()
    else:
        await db.flush()
    return await settings_out(db, reveal_keys=True)


async def _snapshot_skills(db: AsyncSession, row: SettingsRow) -> None:
    """把 admin 名下的 Skill 行同步进 settings_row.skills_json 快照。"""
    from app.services.admin import ADMIN_USER_ID

    skills = (await db.execute(select(Skill).where(Skill.owner_id == ADMIN_USER_ID))).scalars().all()
    row.skills_json = dumps([skill_to_config(skill).model_dump(mode="json") for skill in skills])
    row.updated_at = now_utc()


async def update_skill_enabled(
    db: AsyncSession,
    skill_id: str,
    *,
    enabled: bool | None = None,
    planning_enabled: bool | None = None,
) -> MiraSettings:
    from app.services.admin import ADMIN_USER_ID

    if enabled is None and planning_enabled is None:
        raise HTTPException(status_code=400, detail="请至少提供一个 Skill 更新字段")
    skill = (
        await db.execute(select(Skill).where(Skill.id == skill_id, Skill.owner_id == ADMIN_USER_ID))
    ).scalar_one_or_none()
    if not skill:
        raise HTTPException(status_code=404, detail="未找到该 Skill")
    if enabled is not None:
        skill.enabled = enabled
    if planning_enabled is not None:
        skill.planning_enabled = planning_enabled
    row = await get_or_create_settings_row(db)
    await _snapshot_skills(db, row)
    await db.commit()
    return await settings_out(db, reveal_keys=True)


async def delete_skill_entry(db: AsyncSession, skill_id: str) -> None:
    from app.services.admin import ADMIN_USER_ID
    from app.services.skills import delete_skill

    skill = (
        await db.execute(select(Skill).where(Skill.id == skill_id, Skill.owner_id == ADMIN_USER_ID))
    ).scalar_one_or_none()
    if not skill:
        raise HTTPException(status_code=404, detail="未找到该 Skill")
    await delete_skill(db, skill_id)
    row = await get_or_create_settings_row(db)
    await _snapshot_skills(db, row)
    await db.commit()


def _load_mcp_servers(row: SettingsRow) -> list[dict]:
    return loads(row.mcp_servers_json, [])


def _save_mcp_servers(row: SettingsRow, servers: list[dict]) -> None:
    row.mcp_servers_json = dumps(servers)
    row.updated_at = now_utc()


async def add_mcp_server(db: AsyncSession, server: McpServerConfig) -> MiraSettings:
    row = await get_or_create_settings_row(db)
    servers = _load_mcp_servers(row)
    if any(item.get("id") == server.id for item in servers):
        raise HTTPException(status_code=409, detail=f"MCP {server.id} 已存在")
    servers.append(server.model_dump(mode="json"))
    _save_mcp_servers(row, servers)
    await db.commit()
    return await settings_out(db, reveal_keys=True)


async def update_mcp_server(db: AsyncSession, server_id: str, server: McpServerConfig) -> MiraSettings:
    if server.id != server_id:
        raise HTTPException(status_code=400, detail="MCP id 与路径不一致")
    row = await get_or_create_settings_row(db)
    servers = _load_mcp_servers(row)
    for index, item in enumerate(servers):
        if item.get("id") == server_id:
            servers[index] = server.model_dump(mode="json")
            _save_mcp_servers(row, servers)
            await db.commit()
            return await settings_out(db, reveal_keys=True)
    raise HTTPException(status_code=404, detail="未找到该 MCP")


async def delete_mcp_server(db: AsyncSession, server_id: str) -> None:
    row = await get_or_create_settings_row(db)
    servers = _load_mcp_servers(row)
    remaining = [item for item in servers if item.get("id") != server_id]
    if len(remaining) == len(servers):
        raise HTTPException(status_code=404, detail="未找到该 MCP")
    _save_mcp_servers(row, remaining)
    await db.commit()
