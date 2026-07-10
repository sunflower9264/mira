"""agent config ciphertext

Revision ID: 0004_agent_configs
Revises: 0003_user_is_admin
Create Date: 2026-05-16
"""

from alembic import op
import sqlalchemy as sa

revision = "0004_agent_configs"
down_revision = "0003_user_is_admin"
branch_labels = None
depends_on = None


def upgrade() -> None:
    tables = set(sa.inspect(op.get_bind()).get_table_names())
    if "agent_configs" not in tables:
        op.create_table(
            "agent_configs",
            sa.Column("agent_id", sa.String(length=40), primary_key=True),
            sa.Column("content_ciphertext", sa.Text(), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        )


def downgrade() -> None:
    tables = set(sa.inspect(op.get_bind()).get_table_names())
    if "agent_configs" in tables:
        op.drop_table("agent_configs")
