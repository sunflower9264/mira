"""run agent session tree and workspace checkpoints

Revision ID: 0020_run_agent_workspace
Revises: 0019_workflow_run_contract
Create Date: 2026-08-23
"""

from alembic import op
import sqlalchemy as sa

revision = "0020_run_agent_workspace"
down_revision = "0019_workflow_run_contract"
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
    if "runtime_version" not in run_columns:
        with op.batch_alter_table("runs") as batch_op:
            batch_op.add_column(sa.Column("runtime_version", sa.Integer(), nullable=False, server_default="1"))

    step_columns = _columns("steps")
    with op.batch_alter_table("steps") as batch_op:
        if "branch_id" not in step_columns:
            batch_op.add_column(sa.Column("branch_id", sa.String(length=80), nullable=True))
            batch_op.create_index("ix_steps_branch_id", ["branch_id"], unique=False)
        if "pre_checkpoint_id" not in step_columns:
            batch_op.add_column(sa.Column("pre_checkpoint_id", sa.String(length=80), nullable=True))
        if "post_checkpoint_id" not in step_columns:
            batch_op.add_column(sa.Column("post_checkpoint_id", sa.String(length=80), nullable=True))

    op.create_table(
        "run_agent_branches",
        sa.Column("id", sa.String(length=80), primary_key=True),
        sa.Column("run_id", sa.String(length=80), sa.ForeignKey("runs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("parent_branch_id", sa.String(length=80), nullable=True),
        sa.Column("fork_node_id", sa.String(length=120), nullable=True),
        sa.Column("base_checkpoint_id", sa.String(length=80), nullable=True),
        sa.Column("provider_session_id", sa.String(length=255), nullable=True),
        sa.Column("fork_from_session_id", sa.String(length=255), nullable=True),
        sa.Column("workspace_relpath", sa.Text(), nullable=False),
        sa.Column("state", sa.String(length=24), nullable=False, server_default="active"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_run_agent_branches_run_id", "run_agent_branches", ["run_id"])
    op.create_index("ix_run_agent_branches_parent_branch_id", "run_agent_branches", ["parent_branch_id"])
    op.create_index("ix_run_agent_branches_base_checkpoint_id", "run_agent_branches", ["base_checkpoint_id"])
    op.create_index("ix_run_agent_branches_state", "run_agent_branches", ["state"])
    op.create_index("ix_run_agent_branches_run_state", "run_agent_branches", ["run_id", "state"])

    op.create_table(
        "run_workspace_checkpoints",
        sa.Column("id", sa.String(length=80), primary_key=True),
        sa.Column("run_id", sa.String(length=80), sa.ForeignKey("runs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("step_id", sa.String(length=80), nullable=True),
        sa.Column("node_id", sa.String(length=120), nullable=True),
        sa.Column("branch_id", sa.String(length=80), nullable=True),
        sa.Column("kind", sa.String(length=24), nullable=False, server_default="post_node"),
        sa.Column("snapshot_relpath", sa.Text(), nullable=False),
        sa.Column("tree_hash", sa.String(length=64), nullable=False),
        sa.Column("output_digest", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_run_workspace_checkpoints_run_id", "run_workspace_checkpoints", ["run_id"])
    op.create_index("ix_run_workspace_checkpoints_step_id", "run_workspace_checkpoints", ["step_id"])
    op.create_index("ix_run_workspace_checkpoints_node_id", "run_workspace_checkpoints", ["node_id"])
    op.create_index("ix_run_workspace_checkpoints_branch_id", "run_workspace_checkpoints", ["branch_id"])
    op.create_index("ix_run_workspace_checkpoints_run_step", "run_workspace_checkpoints", ["run_id", "step_id"])

    op.create_table(
        "run_agent_operations",
        sa.Column("id", sa.String(length=80), primary_key=True),
        sa.Column("run_id", sa.String(length=80), sa.ForeignKey("runs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("step_id", sa.String(length=80), nullable=True),
        sa.Column("branch_id", sa.String(length=80), nullable=True),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False, server_default="running"),
        sa.Column("provider_session_id", sa.String(length=255), nullable=True),
        sa.Column("request_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("result_json", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_run_agent_operations_run_id", "run_agent_operations", ["run_id"])
    op.create_index("ix_run_agent_operations_step_id", "run_agent_operations", ["step_id"])
    op.create_index("ix_run_agent_operations_branch_id", "run_agent_operations", ["branch_id"])
    op.create_index("ix_run_agent_operations_run_status", "run_agent_operations", ["run_id", "status"])


def downgrade() -> None:
    op.drop_table("run_agent_operations")
    op.drop_table("run_workspace_checkpoints")
    op.drop_table("run_agent_branches")
    with op.batch_alter_table("steps") as batch_op:
        for column in ("post_checkpoint_id", "pre_checkpoint_id", "branch_id"):
            if column in _columns("steps"):
                batch_op.drop_column(column)
    if "runtime_version" in _columns("runs"):
        with op.batch_alter_table("runs") as batch_op:
            batch_op.drop_column("runtime_version")
