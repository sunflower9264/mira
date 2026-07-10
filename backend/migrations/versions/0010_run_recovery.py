"""run recovery

Revision ID: 0010_run_recovery
Revises: 0009_run_name
Create Date: 2026-05-31
"""

from alembic import op
import sqlalchemy as sa

revision = "0010_run_recovery"
down_revision = "0009_run_name"
branch_labels = None
depends_on = None


def _columns(table: str) -> set[str]:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if table not in set(inspector.get_table_names()):
        return set()
    return {column["name"] for column in inspector.get_columns(table)}


def upgrade() -> None:
    run_columns = _columns("runs")
    if run_columns:
        if "heartbeat_at" not in run_columns:
            op.add_column("runs", sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True))
        if "interrupted_at" not in run_columns:
            op.add_column("runs", sa.Column("interrupted_at", sa.DateTime(timezone=True), nullable=True))
        if "recovery_reason" not in run_columns:
            op.add_column("runs", sa.Column("recovery_reason", sa.Text(), nullable=True))
        if "resume_from_node_id" not in run_columns:
            op.add_column("runs", sa.Column("resume_from_node_id", sa.String(length=120), nullable=True))

    step_columns = _columns("steps")
    if step_columns and "attempt" not in step_columns:
        op.add_column("steps", sa.Column("attempt", sa.Integer(), nullable=False, server_default="0"))

    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "run_events" not in set(inspector.get_table_names()):
        op.create_table(
            "run_events",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("run_id", sa.String(length=80), sa.ForeignKey("runs.id", ondelete="CASCADE"), nullable=False),
            sa.Column("event", sa.String(length=80), nullable=False),
            sa.Column("data_json", sa.Text(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        )
        op.create_index("ix_run_events_run_id", "run_events", ["run_id"])


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())
    if "run_events" in tables:
        op.drop_table("run_events")

    step_columns = _columns("steps")
    if "attempt" in step_columns:
        op.drop_column("steps", "attempt")

    run_columns = _columns("runs")
    for column in ("resume_from_node_id", "recovery_reason", "interrupted_at", "heartbeat_at"):
        if column in run_columns:
            op.drop_column("runs", column)
