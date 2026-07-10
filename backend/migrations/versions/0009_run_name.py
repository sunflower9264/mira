"""run name

Revision ID: 0009_run_name
Revises: 0008_app_source_template_id
Create Date: 2026-05-29
"""

from alembic import op
import sqlalchemy as sa

revision = "0009_run_name"
down_revision = "0008_app_source_template_id"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())
    if "runs" not in tables:
        return
    columns = {column["name"] for column in inspector.get_columns("runs")}
    if "name" not in columns:
        op.add_column("runs", sa.Column("name", sa.String(length=80), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())
    if "runs" not in tables:
        return
    columns = {column["name"] for column in inspector.get_columns("runs")}
    if "name" in columns:
        op.drop_column("runs", "name")
