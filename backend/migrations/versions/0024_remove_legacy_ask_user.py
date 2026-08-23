"""remove legacy ask-user configuration and pending state

Revision ID: 0024_remove_legacy_ask_user
Revises: 0023_codex_only_control_plane
Create Date: 2026-08-24
"""

from __future__ import annotations

import json

from alembic import op
import sqlalchemy as sa

revision = "0024_remove_legacy_ask_user"
down_revision = "0023_codex_only_control_plane"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    for table_name in ("apps", "app_versions", "runs", "nlcompile_sessions"):
        _remove_node_setting(bind, table_name)
    _remove_step_state(bind)
    _interrupt_pending_sessions(bind, "nlcompile_sessions")
    _interrupt_pending_sessions(bind, "prompt_assistant_generations")


def downgrade() -> None:
    pass


def _remove_node_setting(bind, table_name: str) -> None:  # noqa: ANN001
    table = sa.table(table_name, sa.column("id"), sa.column("graph_json"))
    for row in bind.execute(sa.select(table)).mappings():
        graph = _json_object(row["graph_json"])
        changed = False
        for node in graph.get("nodes", []):
            if not isinstance(node, dict):
                continue
            changed = node.pop("ask_user_enabled", None) is not None or changed
            changed = node.pop("decision_request_enabled", None) is not None or changed
        if changed:
            bind.execute(
                table.update()
                .where(table.c.id == row["id"])
                .values(graph_json=json.dumps(graph, ensure_ascii=False))
            )


def _remove_step_state(bind) -> None:  # noqa: ANN001
    steps = sa.table("steps", sa.column("id"), sa.column("input_json"))
    legacy_keys = {"ask_user", "ask_user_history", "ask_user_plan", "decision_request_enabled"}
    for row in bind.execute(sa.select(steps)).mappings():
        payload = _json_object(row["input_json"])
        if not legacy_keys.intersection(payload):
            continue
        for key in legacy_keys:
            payload.pop(key, None)
        payload.pop("resume", None)
        bind.execute(
            steps.update()
            .where(steps.c.id == row["id"])
            .values(input_json=json.dumps(payload, ensure_ascii=False))
        )


def _interrupt_pending_sessions(bind, table_name: str) -> None:  # noqa: ANN001
    table = sa.table(
        table_name,
        sa.column("status"),
        sa.column("pending_request_json"),
        sa.column("error"),
    )
    bind.execute(
        table.update()
        .where(table.c.status == "waiting_for_user")
        .values(
            status="interrupted",
            pending_request_json=None,
            error="交互协议已升级，请重新发起",
        )
    )


def _json_object(value: object) -> dict:
    try:
        parsed = json.loads(value) if isinstance(value, str) else {}
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}
