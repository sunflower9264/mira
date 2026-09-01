"""add persistent Codex workspaces, sessions, events and workflow proposals

Revision ID: 0027_workspaces
Revises: 0026_user_wiki
Create Date: 2026-09-01
"""

from alembic import op
import sqlalchemy as sa

revision = "0027_workspaces"
down_revision = "0026_user_wiki"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("settings") as batch_op:
        batch_op.add_column(
            sa.Column("workspace_git_allowed_hosts_json", sa.Text(), nullable=False, server_default="[]")
        )
    op.create_table(
        "workspaces",
        sa.Column("id", sa.String(length=80), nullable=False),
        sa.Column("owner_id", sa.String(length=80), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("project_relpath", sa.Text(), nullable=False),
        sa.Column("runtime_status", sa.String(length=24), nullable=False),
        sa.Column("container_id", sa.String(length=255), nullable=True),
        sa.Column("runtime_started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("runtime_last_error", sa.Text(), nullable=True),
        sa.Column("wiki_base_revision_id", sa.String(length=80), nullable=True),
        sa.Column("wiki_sync_status", sa.String(length=24), nullable=False),
        sa.Column("wiki_sync_error", sa.Text(), nullable=True),
        sa.Column("wiki_proposed_relpath", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_workspaces_owner_id", "workspaces", ["owner_id"])
    op.create_index("ix_workspaces_owner_updated", "workspaces", ["owner_id", "updated_at", "id"])
    op.create_table(
        "workspace_sessions",
        sa.Column("id", sa.String(length=80), nullable=False),
        sa.Column("workspace_id", sa.String(length=80), nullable=False),
        sa.Column("title", sa.String(length=120), nullable=False),
        sa.Column("thread_id", sa.String(length=255), nullable=True),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("last_turn_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_workspace_sessions_workspace_id", "workspace_sessions", ["workspace_id"])
    op.create_index("ix_workspace_sessions_workspace_updated", "workspace_sessions", ["workspace_id", "updated_at", "id"])
    op.create_table(
        "workspace_turns",
        sa.Column("id", sa.String(length=80), nullable=False),
        sa.Column("workspace_id", sa.String(length=80), nullable=False),
        sa.Column("session_id", sa.String(length=80), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("prompt", sa.Text(), nullable=False),
        sa.Column("model", sa.String(length=120), nullable=True),
        sa.Column("reasoning_effort", sa.String(length=24), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["session_id"], ["workspace_sessions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_workspace_turns_session_id", "workspace_turns", ["session_id"])
    op.create_index("ix_workspace_turns_workspace_id", "workspace_turns", ["workspace_id"])
    op.create_index("ix_workspace_turns_session_created", "workspace_turns", ["session_id", "created_at", "id"])
    op.create_table(
        "workspace_events",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("workspace_id", sa.String(length=80), nullable=False),
        sa.Column("session_id", sa.String(length=80), nullable=False),
        sa.Column("turn_id", sa.String(length=80), nullable=True),
        sa.Column("event_type", sa.String(length=80), nullable=False),
        sa.Column("payload_json", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["session_id"], ["workspace_sessions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["turn_id"], ["workspace_turns.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_workspace_events_workspace_id", "workspace_events", ["workspace_id"])
    op.create_index("ix_workspace_events_session_id", "workspace_events", ["session_id"])
    op.create_index("ix_workspace_events_turn_id", "workspace_events", ["turn_id"])
    op.create_index("ix_workspace_events_session_id_created", "workspace_events", ["session_id", "id"], unique=False)
    op.create_table(
        "workspace_git_configs",
        sa.Column("workspace_id", sa.String(length=80), nullable=False),
        sa.Column("repository_url", sa.Text(), nullable=False),
        sa.Column("default_branch", sa.String(length=255), nullable=False),
        sa.Column("token_ciphertext", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("workspace_id"),
    )
    op.create_table(
        "workspace_workflow_proposals",
        sa.Column("id", sa.String(length=80), nullable=False),
        sa.Column("workspace_id", sa.String(length=80), nullable=False),
        sa.Column("session_id", sa.String(length=80), nullable=True),
        sa.Column("kind", sa.String(length=24), nullable=False),
        sa.Column("app_id", sa.String(length=80), nullable=True),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("base_graph_sha256", sa.String(length=64), nullable=True),
        sa.Column("graph_json", sa.Text(), nullable=False),
        sa.Column("lint_json", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("applied_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["app_id"], ["apps.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["session_id"], ["workspace_sessions.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("workspace_id", "id", name="ux_workspace_workflow_proposal_scope"),
    )
    op.create_index("ix_workspace_workflow_proposals_app_id", "workspace_workflow_proposals", ["app_id"])
    op.create_index("ix_workspace_workflow_proposals_workspace_id", "workspace_workflow_proposals", ["workspace_id"])
    op.create_index("ix_workspace_workflow_proposals_workspace_created", "workspace_workflow_proposals", ["workspace_id", "created_at", "id"])


def downgrade() -> None:
    op.drop_table("workspace_workflow_proposals")
    op.drop_table("workspace_git_configs")
    op.drop_table("workspace_events")
    op.drop_table("workspace_turns")
    op.drop_table("workspace_sessions")
    op.drop_table("workspaces")
    with op.batch_alter_table("settings") as batch_op:
        batch_op.drop_column("workspace_git_allowed_hosts_json")
