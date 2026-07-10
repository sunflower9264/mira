"""app market access

Revision ID: 0013_app_market_access
Revises: 0012_app_visibility
Create Date: 2026-06-05
"""

from alembic import op
import sqlalchemy as sa

revision = "0013_app_market_access"
down_revision = "0012_app_visibility"
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
    if not app_columns or "market_access" in app_columns:
        return
    op.add_column(
        "apps",
        sa.Column("market_access", sa.String(length=24), nullable=False, server_default="cloneable"),
    )


def downgrade() -> None:
    app_columns = _columns("apps")
    if "market_access" in app_columns:
        op.drop_column("apps", "market_access")
