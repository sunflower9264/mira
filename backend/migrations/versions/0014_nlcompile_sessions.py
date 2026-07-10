"""nlcompile sessions

Revision ID: 0014_nlcompile_sessions
Revises: 0013_app_market_access
Create Date: 2026-06-10
"""

from alembic import op
import sqlalchemy as sa

revision = "0014_nlcompile_sessions"
down_revision = "0013_app_market_access"
branch_labels = None
depends_on = None


def upgrade() -> None:
    tables = set(sa.inspect(op.get_bind()).get_table_names())
    if "nlcompile_sessions" in tables:
        return
    op.create_table(
        "nlcompile_sessions",
        sa.Column("id", sa.String(length=80), primary_key=True),
        sa.Column("user_id", sa.String(length=80), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("app_id", sa.String(length=80), sa.ForeignKey("apps.id", ondelete="CASCADE"), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False, server_default="planning"),
        sa.Column("instruction", sa.Text(), nullable=False),
        sa.Column("graph_json", sa.Text(), nullable=False),
        sa.Column("history_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("pending_request_json", sa.Text(), nullable=True),
        sa.Column("plan_json", sa.Text(), nullable=True),
        sa.Column("plan_markdown", sa.Text(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_nlcompile_sessions_user_id", "nlcompile_sessions", ["user_id"])
    op.create_index("ix_nlcompile_sessions_app_id", "nlcompile_sessions", ["app_id"])


def downgrade() -> None:
    tables = set(sa.inspect(op.get_bind()).get_table_names())
    if "nlcompile_sessions" in tables:
        op.drop_table("nlcompile_sessions")
