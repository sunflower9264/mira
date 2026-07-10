"""app archived at

Revision ID: 0017_app_archived_at
Revises: 0016_prompt_assistant_generations
Create Date: 2026-06-12
"""

from alembic import op
import sqlalchemy as sa

revision = "0017_app_archived_at"
down_revision = "0016_prompt_assistant_generations"
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
    if not app_columns or "archived_at" in app_columns:
        return
    op.add_column("apps", sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    app_columns = _columns("apps")
    if "archived_at" in app_columns:
        op.drop_column("apps", "archived_at")
