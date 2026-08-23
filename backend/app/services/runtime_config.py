from __future__ import annotations

import tomllib
import tomli_w

from sqlalchemy.ext.asyncio import AsyncSession

from .codex_config import get_auth_content, get_config_content, write_runtime_config_files


async def write_configs(db: AsyncSession) -> None:
    codex_config = _read_toml_config(await get_config_content(db))
    new_codex_config = dict(codex_config)
    new_codex_config.pop("mcp_servers", None)
    write_runtime_config_files(tomli_w.dumps(new_codex_config), await get_auth_content(db))


def _read_toml_config(content: str) -> dict:
    try:
        data = tomllib.loads(content)
    except tomllib.TOMLDecodeError:
        return {}
    return data if isinstance(data, dict) else {}
