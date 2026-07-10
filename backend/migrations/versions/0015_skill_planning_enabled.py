"""skill planning enabled

Revision ID: 0015_skill_planning_enabled
Revises: 0014_nlcompile_sessions
Create Date: 2026-06-10
"""

from alembic import op
import sqlalchemy as sa

revision = "0015_skill_planning_enabled"
down_revision = "0014_nlcompile_sessions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    columns = {column["name"] for column in inspector.get_columns("skills")}
    if "planning_enabled" not in columns:
        op.add_column(
            "skills",
            sa.Column("planning_enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        )


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    columns = {column["name"] for column in inspector.get_columns("skills")}
    if "planning_enabled" in columns:
        op.drop_column("skills", "planning_enabled")
