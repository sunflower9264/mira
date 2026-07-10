"""app visibility

Revision ID: 0012_app_visibility
Revises: 0011_run_graph_snapshot
Create Date: 2026-06-05
"""

from alembic import op
import sqlalchemy as sa

revision = "0012_app_visibility"
down_revision = "0011_run_graph_snapshot"
branch_labels = None
depends_on = None


def _columns(table: str) -> set[str]:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if table not in set(inspector.get_table_names()):
        return set()
    return {column["name"] for column in inspector.get_columns(table)}


def upgrade() -> None:
    app_columns = _columns("apps")
    if not app_columns or "visibility" in app_columns:
        return
    op.add_column("apps", sa.Column("visibility", sa.String(length=24), nullable=False, server_default="public"))


def downgrade() -> None:
    app_columns = _columns("apps")
    if "visibility" in app_columns:
        op.drop_column("apps", "visibility")
