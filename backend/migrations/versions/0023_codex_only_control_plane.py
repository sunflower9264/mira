"""replace provider selection with a single Codex control plane

Revision ID: 0023_codex_only_control_plane
Revises: 0022_remove_legacy_runtime_version
Create Date: 2026-08-24
"""

from __future__ import annotations

import json

from alembic import op
import sqlalchemy as sa

revision = "0023_codex_only_control_plane"
down_revision = "0022_remove_legacy_runtime_version"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    op.create_table(
        "codex_config",
        sa.Column("id", sa.String(length=20), primary_key=True),
        sa.Column("config_ciphertext", sa.Text(), nullable=False),
        sa.Column("auth_ciphertext", sa.Text(), nullable=False),
        sa.Column("config_user_modified", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("auth_user_modified", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )

    agent_configs = sa.table(
        "agent_configs",
        sa.column("agent_id"),
        sa.column("content_ciphertext"),
        sa.column("user_modified"),
        sa.column("updated_at"),
    )
    rows = {
        row.agent_id: row
        for row in bind.execute(
            sa.select(agent_configs).where(agent_configs.c.agent_id.in_(("codex", "codex-auth")))
        )
    }
    config_row = rows.get("codex")
    auth_row = rows.get("codex-auth")
    if config_row is not None and auth_row is not None:
        codex_config = sa.table(
            "codex_config",
            sa.column("id"),
            sa.column("config_ciphertext"),
            sa.column("auth_ciphertext"),
            sa.column("config_user_modified"),
            sa.column("auth_user_modified"),
            sa.column("updated_at"),
        )
        bind.execute(
            codex_config.insert().values(
                id="codex",
                config_ciphertext=config_row.content_ciphertext,
                auth_ciphertext=auth_row.content_ciphertext,
                config_user_modified=bool(config_row.user_modified),
                auth_user_modified=bool(auth_row.user_modified),
                updated_at=max(config_row.updated_at, auth_row.updated_at),
            )
        )

    op.add_column("settings", sa.Column("supported_models_json", sa.Text(), nullable=True))
    settings = sa.table(
        "settings",
        sa.column("owner_id"),
        sa.column("agents_json"),
        sa.column("supported_models_json"),
        sa.column("mcp_servers_json"),
    )
    for row in bind.execute(sa.select(settings)).mappings():
        agents = _json_list(row["agents_json"])
        codex = next((item for item in agents if isinstance(item, dict) and item.get("id") == "codex"), {})
        models = codex.get("supported_models") if isinstance(codex, dict) else []
        if not isinstance(models, list):
            models = []
        mcp_servers = _json_list(row["mcp_servers_json"])
        cleaned_servers = [
            {key: value for key, value in item.items() if key != "provider_ids"}
            if isinstance(item, dict)
            else item
            for item in mcp_servers
        ]
        bind.execute(
            settings.update()
            .where(settings.c.owner_id == row["owner_id"])
            .values(
                supported_models_json=json.dumps(models, ensure_ascii=False),
                mcp_servers_json=json.dumps(cleaned_servers, ensure_ascii=False),
            )
        )
    with op.batch_alter_table("settings") as batch_op:
        batch_op.alter_column("supported_models_json", nullable=False)
        batch_op.drop_column("agents_json")

    for table_name in ("apps", "app_versions", "runs", "nlcompile_sessions"):
        _remove_graph_agent(bind, table_name)

    prompt_templates = sa.table("prompt_templates", sa.column("key"))
    bind.execute(
        prompt_templates.delete().where(
            prompt_templates.c.key.in_(("ask_user_protocol", "ask_user_preflight_protocol"))
        )
    )

    with op.batch_alter_table("prompt_assistant_generations") as batch_op:
        batch_op.drop_column("agent")
    op.drop_table("agent_configs")


def downgrade() -> None:
    bind = op.get_bind()
    op.create_table(
        "agent_configs",
        sa.Column("agent_id", sa.String(length=40), primary_key=True),
        sa.Column("content_ciphertext", sa.Text(), nullable=False),
        sa.Column("user_modified", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    codex_config = sa.table(
        "codex_config",
        sa.column("id"),
        sa.column("config_ciphertext"),
        sa.column("auth_ciphertext"),
        sa.column("config_user_modified"),
        sa.column("auth_user_modified"),
        sa.column("updated_at"),
    )
    row = bind.execute(sa.select(codex_config).where(codex_config.c.id == "codex")).first()
    if row is not None:
        agent_configs = sa.table(
            "agent_configs",
            sa.column("agent_id"),
            sa.column("content_ciphertext"),
            sa.column("user_modified"),
            sa.column("updated_at"),
        )
        bind.execute(
            agent_configs.insert(),
            [
                {
                    "agent_id": "codex",
                    "content_ciphertext": row.config_ciphertext,
                    "user_modified": row.config_user_modified,
                    "updated_at": row.updated_at,
                },
                {
                    "agent_id": "codex-auth",
                    "content_ciphertext": row.auth_ciphertext,
                    "user_modified": row.auth_user_modified,
                    "updated_at": row.updated_at,
                },
            ],
        )

    op.add_column("settings", sa.Column("agents_json", sa.Text(), nullable=True))
    settings = sa.table(
        "settings",
        sa.column("owner_id"),
        sa.column("agents_json"),
        sa.column("supported_models_json"),
        sa.column("mcp_servers_json"),
    )
    for settings_row in bind.execute(sa.select(settings)).mappings():
        models = _json_list(settings_row["supported_models_json"])
        servers = _json_list(settings_row["mcp_servers_json"])
        restored_servers = [
            {**item, "provider_ids": ["codex"]} if isinstance(item, dict) else item
            for item in servers
        ]
        agents = [
            {
                "id": "codex",
                "name": "Codex",
                "description": "Codex runtime",
                "runtime": "codex",
                "enabled": True,
                "supported_models": models,
            }
        ]
        bind.execute(
            settings.update()
            .where(settings.c.owner_id == settings_row["owner_id"])
            .values(
                agents_json=json.dumps(agents, ensure_ascii=False),
                mcp_servers_json=json.dumps(restored_servers, ensure_ascii=False),
            )
        )
    with op.batch_alter_table("settings") as batch_op:
        batch_op.alter_column("agents_json", nullable=False)
        batch_op.drop_column("supported_models_json")

    for table_name in ("apps", "app_versions", "runs", "nlcompile_sessions"):
        _restore_graph_agent(bind, table_name)

    with op.batch_alter_table("prompt_assistant_generations") as batch_op:
        batch_op.add_column(
            sa.Column("agent", sa.String(length=32), nullable=False, server_default="codex")
        )
    op.drop_table("codex_config")


def _remove_graph_agent(bind, table_name: str) -> None:  # noqa: ANN001
    table = sa.table(table_name, sa.column("id"), sa.column("graph_json"))
    for row in bind.execute(sa.select(table)).mappings():
        graph = _json_object(row["graph_json"])
        if "agent" not in graph:
            continue
        graph.pop("agent", None)
        bind.execute(
            table.update()
            .where(table.c.id == row["id"])
            .values(graph_json=json.dumps(graph, ensure_ascii=False))
        )


def _restore_graph_agent(bind, table_name: str) -> None:  # noqa: ANN001
    table = sa.table(table_name, sa.column("id"), sa.column("graph_json"))
    for row in bind.execute(sa.select(table)).mappings():
        graph = _json_object(row["graph_json"])
        graph["agent"] = "codex"
        bind.execute(
            table.update()
            .where(table.c.id == row["id"])
            .values(graph_json=json.dumps(graph, ensure_ascii=False))
        )


def _json_list(value: object) -> list:
    try:
        parsed = json.loads(value) if isinstance(value, str) else []
    except json.JSONDecodeError:
        return []
    return parsed if isinstance(parsed, list) else []


def _json_object(value: object) -> dict:
    try:
        parsed = json.loads(value) if isinstance(value, str) else {}
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}
