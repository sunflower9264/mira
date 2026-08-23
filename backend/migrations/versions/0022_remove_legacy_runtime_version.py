"""remove the legacy run runtime version discriminator

Revision ID: 0022_remove_legacy_runtime_version
Revises: 0021_checkpoint_session
Create Date: 2026-08-23
"""

from alembic import op
import sqlalchemy as sa

revision = "0022_remove_legacy_runtime_version"
down_revision = "0021_checkpoint_session"
branch_labels = None
depends_on = None


def upgrade() -> None:
    legacy_runs = "SELECT id FROM runs WHERE runtime_version < 2"
    op.execute(
        sa.text(
            "DELETE FROM step_logs WHERE step_id IN "
            f"(SELECT id FROM steps WHERE run_id IN ({legacy_runs}))"
        )
    )
    for table in (
        "run_agent_operations",
        "run_workspace_checkpoints",
        "run_agent_branches",
        "run_events",
        "steps",
    ):
        op.execute(sa.text(f"DELETE FROM {table} WHERE run_id IN ({legacy_runs})"))
    op.execute(sa.text("DELETE FROM runs WHERE runtime_version < 2"))
    with op.batch_alter_table("runs") as batch_op:
        batch_op.drop_column("runtime_version")


def downgrade() -> None:
    with op.batch_alter_table("runs") as batch_op:
        batch_op.add_column(
            sa.Column("runtime_version", sa.Integer(), nullable=False, server_default="2")
        )
