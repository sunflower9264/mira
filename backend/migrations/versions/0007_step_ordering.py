"""step ordering column

Revision ID: 0007_step_ordering
Revises: 0006_agent_config_setup_state
Create Date: 2026-05-19
"""

from alembic import op
import sqlalchemy as sa

revision = "0007_step_ordering"
down_revision = "0006_agent_config_setup_state"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())
    if "steps" not in tables:
        return
    columns = {column["name"] for column in inspector.get_columns("steps")}
    if "ordering" not in columns:
        op.add_column(
            "steps",
            sa.Column("ordering", sa.Integer(), nullable=False, server_default="0"),
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())
    if "steps" not in tables:
        return
    columns = {column["name"] for column in inspector.get_columns("steps")}
    if "ordering" in columns:
        op.drop_column("steps", "ordering")
