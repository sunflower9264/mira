"""skill archive md5

Revision ID: 0002_skill_archive_md5
Revises: 0001_baseline
Create Date: 2026-05-15
"""

from alembic import op
import sqlalchemy as sa

revision = "0002_skill_archive_md5"
down_revision = "0001_baseline"
branch_labels = None
depends_on = None


def upgrade() -> None:
    columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("skills")}
    if "archive_md5" not in columns:
        with op.batch_alter_table("skills") as batch:
            batch.add_column(
                sa.Column("archive_md5", sa.String(length=32), nullable=False, server_default="")
            )


def downgrade() -> None:
    columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("skills")}
    if "archive_md5" in columns:
        with op.batch_alter_table("skills") as batch:
            batch.drop_column("archive_md5")
