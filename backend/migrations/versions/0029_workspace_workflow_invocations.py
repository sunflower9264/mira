"""link workflow runs to workspace turns

Revision ID: 0029_workspace_workflow_invocations
Revises: 0028_default_workspace_git_hosts
Create Date: 2026-09-02
"""

from alembic import op
import sqlalchemy as sa


revision = "0029_workspace_workflow_invocations"
down_revision = "0028_default_workspace_git_hosts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("runs") as batch_op:
        batch_op.add_column(sa.Column("workspace_id", sa.String(length=80), nullable=True))
        batch_op.add_column(sa.Column("workspace_session_id", sa.String(length=80), nullable=True))
        batch_op.add_column(sa.Column("workspace_turn_id", sa.String(length=80), nullable=True))
        batch_op.create_foreign_key(
            "fk_runs_workspace_id", "workspaces", ["workspace_id"], ["id"], ondelete="SET NULL"
        )
        batch_op.create_foreign_key(
            "fk_runs_workspace_session_id",
            "workspace_sessions",
            ["workspace_session_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch_op.create_foreign_key(
            "fk_runs_workspace_turn_id",
            "workspace_turns",
            ["workspace_turn_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch_op.create_index(
            "ix_runs_workspace_started_id", ["workspace_id", "started_at", "id"], unique=False
        )


def downgrade() -> None:
    with op.batch_alter_table("runs") as batch_op:
        batch_op.drop_index("ix_runs_workspace_started_id")
        batch_op.drop_constraint("fk_runs_workspace_turn_id", type_="foreignkey")
        batch_op.drop_constraint("fk_runs_workspace_session_id", type_="foreignkey")
        batch_op.drop_constraint("fk_runs_workspace_id", type_="foreignkey")
        batch_op.drop_column("workspace_turn_id")
        batch_op.drop_column("workspace_session_id")
        batch_op.drop_column("workspace_id")
