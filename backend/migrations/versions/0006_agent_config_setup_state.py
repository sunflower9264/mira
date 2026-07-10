"""agent config setup state

Revision ID: 0006_agent_config_setup_state
Revises: 0005_prompt_templates
Create Date: 2026-05-17
"""

from alembic import op
import sqlalchemy as sa

revision = "0006_agent_config_setup_state"
down_revision = "0005_prompt_templates"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())
    if "agent_configs" not in tables:
        return
    columns = {column["name"] for column in inspector.get_columns("agent_configs")}
    if "user_modified" not in columns:
        op.add_column(
            "agent_configs",
            sa.Column("user_modified", sa.Boolean(), nullable=False, server_default=sa.false()),
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())
    if "agent_configs" not in tables:
        return
    columns = {column["name"] for column in inspector.get_columns("agent_configs")}
    if "user_modified" in columns:
        op.drop_column("agent_configs", "user_modified")
