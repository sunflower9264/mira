from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Skill
from app.schemas import McpHeader, McpServerConfig, RuntimeMcpServerConfig, RuntimeSkillConfig, ToolConfig
from app.utils import loads

RUNTIME_TOOLS_KEY = "_runtime_tools"
MCP_TOOL_PREFIX = "mcp:"
SKILL_TOOL_PREFIX = "skill:"


@dataclass
class RuntimeToolConfig:
    mcp_servers: list[RuntimeMcpServerConfig] = field(default_factory=list)
    skills: list[RuntimeSkillConfig] = field(default_factory=list)


def mcp_tool_id(server_id: str) -> str:
    return f"{MCP_TOOL_PREFIX}{server_id}"


def skill_tool_id(skill_id: str) -> str:
    return f"{SKILL_TOOL_PREFIX}{skill_id}"


def tool_inventory(skills: Iterable[Skill], mcp_servers: Iterable[McpServerConfig | dict[str, Any]]) -> list[ToolConfig]:
    tools: list[ToolConfig] = []
    for server in mcp_servers:
        item = server if isinstance(server, McpServerConfig) else McpServerConfig.model_validate(server)
        tools.append(
            ToolConfig(
                id=mcp_tool_id(item.id),
                name=item.name,
                description=item.url,
                enabled=item.enabled,
                planning_enabled=item.planning_enabled,
            )
        )
    for skill in skills:
        tools.append(
            ToolConfig(
                id=skill_tool_id(skill.id),
                name=skill.name,
                description=skill.description,
                enabled=skill.enabled,
                planning_enabled=skill.planning_enabled,
            )
        )
    tools.sort(key=lambda item: item.name.lower())
    return tools


async def stamp_run_tools_snapshot(db: AsyncSession, graph: dict[str, Any], agent: str) -> dict[str, Any]:
    snapshot = dict(graph)
    allowed = await allowed_tool_ids_for_graph(db, graph, agent)
    snapshot[RUNTIME_TOOLS_KEY] = {"allowed_tool_ids": sorted(allowed)}
    return snapshot


def strip_runtime_tools_snapshot(graph: dict[str, Any]) -> dict[str, Any]:
    if RUNTIME_TOOLS_KEY not in graph:
        return graph
    sanitized = dict(graph)
    sanitized.pop(RUNTIME_TOOLS_KEY, None)
    return sanitized


async def runtime_tools_for_graph(
    db: AsyncSession,
    graph: dict[str, Any],
    agent: str,
    *,
    trust_snapshot: bool = False,
) -> RuntimeToolConfig:
    allowed_ids = _snapshot_allowed_tool_ids(graph) if trust_snapshot else None
    if allowed_ids is None:
        allowed_ids = await allowed_tool_ids_for_graph(db, graph, agent)
    return await _runtime_tools_for_allowed_ids(db, allowed_ids, agent, planning_only=False)


async def planning_runtime_tools_for_graph(
    db: AsyncSession,
    graph: dict[str, Any],
    agent: str,
    *,
    trust_snapshot: bool = False,
) -> RuntimeToolConfig:
    allowed_ids = _snapshot_allowed_tool_ids(graph) if trust_snapshot else None
    if allowed_ids is None:
        allowed_ids = await allowed_tool_ids_for_graph(db, graph, agent)
    return await _runtime_tools_for_allowed_ids(db, allowed_ids, agent, planning_only=True)


async def _runtime_tools_for_allowed_ids(
    db: AsyncSession,
    allowed_ids: set[str],
    agent: str,
    *,
    planning_only: bool,
) -> RuntimeToolConfig:
    provider_id = _provider_id_for_agent(agent)
    mcp_servers = [
        _runtime_mcp_server(server)
        for server in await _enabled_mcp_servers(db)
        if mcp_tool_id(server.id) in allowed_ids
        and (provider_id is None or provider_id in server.provider_ids)
        and (not planning_only or server.planning_enabled)
    ]
    skills = [
        RuntimeSkillConfig(id=skill.id, archive_path=skill.archive_path, archive_md5=skill.archive_md5)
        for skill in await _enabled_skills(db)
        if skill_tool_id(skill.id) in allowed_ids and (not planning_only or skill.planning_enabled)
    ]
    return RuntimeToolConfig(mcp_servers=mcp_servers, skills=skills)


async def allowed_tool_ids_for_graph(db: AsyncSession, graph: dict[str, Any], agent: str) -> set[str]:
    provider_id = _provider_id_for_agent(agent)
    disabled = _disabled_tool_ids(graph)
    allowed: set[str] = set()
    for server in await _enabled_mcp_servers(db):
        if provider_id is not None and provider_id not in server.provider_ids:
            continue
        tool_id = mcp_tool_id(server.id)
        if tool_id not in disabled:
            allowed.add(tool_id)
    for skill in await _enabled_skills(db):
        tool_id = skill_tool_id(skill.id)
        if tool_id not in disabled:
            allowed.add(tool_id)
    return allowed


def _runtime_mcp_server(server: McpServerConfig) -> RuntimeMcpServerConfig:
    return RuntimeMcpServerConfig(
        name=server.name,
        url=server.url,
        headers=[McpHeader(name=header.name, value=header.value) for header in server.headers],
    )


async def _enabled_skills(db: AsyncSession) -> list[Skill]:
    from app.services.admin import ADMIN_USER_ID

    return list(
        (await db.execute(select(Skill).where(Skill.owner_id == ADMIN_USER_ID, Skill.enabled.is_(True))))
        .scalars()
        .all()
    )


async def _enabled_mcp_servers(db: AsyncSession) -> list[McpServerConfig]:
    from app.services.settings import get_or_create_settings_row

    row = await get_or_create_settings_row(db)
    servers = []
    for item in loads(row.mcp_servers_json, []):
        try:
            server = McpServerConfig.model_validate(item)
        except Exception:
            continue
        if server.enabled:
            servers.append(server)
    return servers


def _disabled_tool_ids(graph: dict[str, Any]) -> set[str]:
    tools = graph.get("tools") if isinstance(graph, dict) else None
    if not isinstance(tools, dict):
        return set()
    raw = tools.get("disabled_tool_ids")
    if not isinstance(raw, list):
        return set()
    return {item for item in raw if isinstance(item, str) and item}


def _snapshot_allowed_tool_ids(graph: dict[str, Any]) -> set[str] | None:
    snapshot = graph.get(RUNTIME_TOOLS_KEY) if isinstance(graph, dict) else None
    if not isinstance(snapshot, dict):
        return None
    raw = snapshot.get("allowed_tool_ids")
    if not isinstance(raw, list):
        return None
    return {item for item in raw if isinstance(item, str) and item}


def _provider_id_for_agent(agent: str) -> str | None:
    if agent == "claude":
        return "claude-code"
    if agent == "codex":
        return "codex"
    return None
