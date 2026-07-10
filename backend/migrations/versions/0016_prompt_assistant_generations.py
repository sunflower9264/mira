"""prompt assistant generations

Revision ID: 0016_prompt_assistant_generations
Revises: 0015_skill_planning_enabled
Create Date: 2026-06-11
"""

from alembic import op
import sqlalchemy as sa

revision = "0016_prompt_assistant_generations"
down_revision = "0015_skill_planning_enabled"
branch_labels = None
depends_on = None


def upgrade() -> None:
    tables = set(sa.inspect(op.get_bind()).get_table_names())
    if "prompt_assistant_generations" in tables:
        return
    op.create_table(
        "prompt_assistant_generations",
        sa.Column("id", sa.String(length=80), primary_key=True),
        sa.Column("user_id", sa.String(length=80), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("app_id", sa.String(length=80), sa.ForeignKey("apps.id", ondelete="CASCADE"), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False, server_default="running"),
        sa.Column("agent", sa.String(length=32), nullable=False),
        sa.Column("prompt_json", sa.Text(), nullable=False),
        sa.Column("model", sa.String(length=120), nullable=True),
        sa.Column("reasoning_effort", sa.String(length=32), nullable=True),
        sa.Column("history_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("pending_request_json", sa.Text(), nullable=True),
        sa.Column("result_json", sa.Text(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_prompt_assistant_generations_user_id", "prompt_assistant_generations", ["user_id"])
    op.create_index("ix_prompt_assistant_generations_app_id", "prompt_assistant_generations", ["app_id"])


def downgrade() -> None:
    tables = set(sa.inspect(op.get_bind()).get_table_names())
    if "prompt_assistant_generations" in tables:
        op.drop_table("prompt_assistant_generations")
