from __future__ import annotations

import json
import tomllib
import tomli_w

from sqlalchemy.ext.asyncio import AsyncSession

from .agent_config import get_config_content, write_runtime_config_file
from .runtime_paths import claude_mcp_config_path


async def write_configs(db: AsyncSession) -> None:
    claude_settings = _read_json_config(await get_config_content(db, "claude-code"))
    new_claude_settings = dict(claude_settings)
    new_claude_settings.pop("mcpServers", None)
    write_runtime_config_file("claude-code", json.dumps(new_claude_settings, ensure_ascii=False))
    claude_mcp_path = claude_mcp_config_path()
    if claude_mcp_path.exists():
        claude_mcp_path.unlink()

    codex_config = _read_toml_config(await get_config_content(db, "codex"))
    new_codex_config = dict(codex_config)
    new_codex_config.pop("mcp_servers", None)
    write_runtime_config_file("codex", tomli_w.dumps(new_codex_config))
    write_runtime_config_file("codex-auth", await get_config_content(db, "codex-auth"))


def _read_json_config(content: str) -> dict:
    try:
        data = json.loads(content or "{}")
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def _read_toml_config(content: str) -> dict:
    try:
        data = tomllib.loads(content)
    except tomllib.TOMLDecodeError:
        return {}
    return data if isinstance(data, dict) else {}
