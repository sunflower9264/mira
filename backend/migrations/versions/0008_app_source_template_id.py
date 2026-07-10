"""app source template id

Revision ID: 0008_app_source_template_id
Revises: 0007_step_ordering
Create Date: 2026-05-19
"""

from alembic import op
import sqlalchemy as sa

revision = "0008_app_source_template_id"
down_revision = "0007_step_ordering"
branch_labels = None
depends_on = None

INDEX_NAME = "ux_apps_owner_source_template_id"


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())
    if "apps" not in tables:
        return

    columns = {column["name"] for column in inspector.get_columns("apps")}
    if "source_template_id" not in columns:
        op.add_column("apps", sa.Column("source_template_id", sa.String(length=80), nullable=True))

    indexes = {index["name"] for index in inspector.get_indexes("apps")}
    if INDEX_NAME not in indexes:
        op.create_index(INDEX_NAME, "apps", ["owner_id", "source_template_id"], unique=True)


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())
    if "apps" not in tables:
        return

    indexes = {index["name"] for index in inspector.get_indexes("apps")}
    if INDEX_NAME in indexes:
        op.drop_index(INDEX_NAME, table_name="apps")

    columns = {column["name"] for column in inspector.get_columns("apps")}
    if "source_template_id" in columns:
        op.drop_column("apps", "source_template_id")
