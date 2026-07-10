"""user is_admin

Revision ID: 0003_user_is_admin
Revises: 0002_skill_archive_md5
Create Date: 2026-05-15
"""

from alembic import op
import sqlalchemy as sa

revision = "0003_user_is_admin"
down_revision = "0002_skill_archive_md5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("users")}
    if "is_admin" not in columns:
        with op.batch_alter_table("users") as batch:
            batch.add_column(
                sa.Column("is_admin", sa.Boolean(), nullable=False, server_default=sa.text("0"))
            )


def downgrade() -> None:
    columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("users")}
    if "is_admin" in columns:
        with op.batch_alter_table("users") as batch:
            batch.drop_column("is_admin")
