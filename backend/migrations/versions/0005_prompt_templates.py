"""prompt templates

Revision ID: 0005_prompt_templates
Revises: 0004_agent_configs
Create Date: 2026-05-17
"""

from alembic import op
import sqlalchemy as sa

revision = "0005_prompt_templates"
down_revision = "0004_agent_configs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    tables = set(sa.inspect(op.get_bind()).get_table_names())
    if "prompt_templates" not in tables:
        op.create_table(
            "prompt_templates",
            sa.Column("key", sa.String(length=80), primary_key=True),
            sa.Column("name", sa.String(length=200), nullable=False),
            sa.Column("description", sa.Text(), nullable=False),
            sa.Column("content", sa.Text(), nullable=False),
            sa.Column("variables_json", sa.Text(), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        )


def downgrade() -> None:
    tables = set(sa.inspect(op.get_bind()).get_table_names())
    if "prompt_templates" in tables:
        op.drop_table("prompt_templates")
