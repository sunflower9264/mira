"""baseline

Revision ID: 0001_baseline
Revises:
Create Date: 2026-05-15
"""

from alembic import op
import sqlalchemy as sa

revision = "0001_baseline"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.String(length=80), primary_key=True),
        sa.Column("username", sa.String(length=64), nullable=False),
        sa.Column("password_hash", sa.String(length=255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_users_username", "users", ["username"], unique=True)
    op.create_table(
        "apps",
        sa.Column("id", sa.String(length=80), primary_key=True),
        sa.Column("owner_id", sa.String(length=80), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("cover", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("graph_json", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_apps_owner_id", "apps", ["owner_id"])
    op.create_table(
        "settings",
        sa.Column("owner_id", sa.String(length=80), sa.ForeignKey("users.id"), primary_key=True),
        sa.Column("agents_json", sa.Text(), nullable=False),
        sa.Column("skills_json", sa.Text(), nullable=False),
        sa.Column("mcp_servers_json", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "skills",
        sa.Column("id", sa.String(length=80), primary_key=True),
        sa.Column("owner_id", sa.String(length=80), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("archive_name", sa.String(length=255), nullable=False),
        sa.Column("archive_size", sa.Integer(), nullable=False),
        sa.Column("archive_path", sa.Text(), nullable=False),
        sa.Column("uploaded_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
    )
    op.create_index("ix_skills_owner_id", "skills", ["owner_id"])
    op.create_table(
        "app_versions",
        sa.Column("id", sa.String(length=80), primary_key=True),
        sa.Column("app_id", sa.String(length=80), sa.ForeignKey("apps.id", ondelete="CASCADE"), nullable=False),
        sa.Column("label", sa.String(length=200), nullable=True),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("graph_json", sa.Text(), nullable=False),
        sa.Column("is_published", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_app_versions_app_id", "app_versions", ["app_id"])
    op.create_table(
        "runs",
        sa.Column("id", sa.String(length=80), primary_key=True),
        sa.Column("app_id", sa.String(length=80), sa.ForeignKey("apps.id", ondelete="CASCADE"), nullable=False),
        sa.Column("owner_id", sa.String(length=80), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("inputs_json", sa.Text(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
    )
    op.create_index("ix_runs_app_id", "runs", ["app_id"])
    op.create_index("ix_runs_owner_id", "runs", ["owner_id"])
    op.create_table(
        "steps",
        sa.Column("id", sa.String(length=80), primary_key=True),
        sa.Column("run_id", sa.String(length=80), sa.ForeignKey("runs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("node_id", sa.String(length=120), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("input_json", sa.Text(), nullable=False),
        sa.Column("output_json", sa.Text(), nullable=True),
        sa.Column("agent_session_id", sa.String(length=255), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
    )
    op.create_index("ix_steps_run_id", "steps", ["run_id"])
    op.create_index("ix_steps_node_id", "steps", ["node_id"])
    op.create_table(
        "step_logs",
        sa.Column("id", sa.String(length=80), primary_key=True),
        sa.Column("step_id", sa.String(length=80), sa.ForeignKey("steps.id", ondelete="CASCADE"), nullable=False),
        sa.Column("ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column("level", sa.String(length=24), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
    )
    op.create_index("ix_step_logs_step_id", "step_logs", ["step_id"])


def downgrade() -> None:
    op.drop_table("step_logs")
    op.drop_table("steps")
    op.drop_table("runs")
    op.drop_table("app_versions")
    op.drop_table("skills")
    op.drop_table("settings")
    op.drop_table("apps")
    op.drop_table("users")

