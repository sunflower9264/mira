"""add user wiki storage, revisions, operations and run snapshots

Revision ID: 0026_user_wiki
Revises: 0025_skill_dependency_layers
Create Date: 2026-08-25
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0026_user_wiki"
down_revision = "0025_skill_dependency_layers"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "wikis",
        sa.Column("id", sa.String(length=80), nullable=False),
        sa.Column("owner_id", sa.String(length=80), nullable=False),
        sa.Column("purpose", sa.Text(), nullable=False),
        sa.Column("schema", sa.Text(), nullable=False),
        sa.Column("current_revision_id", sa.String(length=80), nullable=True),
        sa.Column("maintainer_session_id", sa.String(length=255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("owner_id", name="ux_wikis_owner_id"),
    )
    op.create_index("ix_wikis_owner_id", "wikis", ["owner_id"])
    op.create_table(
        "wiki_sources",
        sa.Column("id", sa.String(length=80), nullable=False),
        sa.Column("wiki_id", sa.String(length=80), nullable=False),
        sa.Column("path", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("mime", sa.String(length=255), nullable=False),
        sa.Column("size", sa.Integer(), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("storage_relpath", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["wiki_id"], ["wikis.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_wiki_sources_wiki_id", "wiki_sources", ["wiki_id"])
    op.create_index("ix_wiki_sources_wiki_status", "wiki_sources", ["wiki_id", "status"])
    op.create_table(
        "wiki_revisions",
        sa.Column("id", sa.String(length=80), nullable=False),
        sa.Column("wiki_id", sa.String(length=80), nullable=False),
        sa.Column("parent_revision_id", sa.String(length=80), nullable=True),
        sa.Column("operation_id", sa.String(length=80), nullable=True),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("manifest_json", sa.Text(), nullable=False),
        sa.Column("raw_manifest_json", sa.Text(), nullable=False),
        sa.Column("tree_hash", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["wiki_id"], ["wikis.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_wiki_revisions_wiki_id", "wiki_revisions", ["wiki_id"])
    op.create_index("ix_wiki_revisions_wiki_created", "wiki_revisions", ["wiki_id", "created_at", "id"])
    op.create_table(
        "wiki_operations",
        sa.Column("id", sa.String(length=80), nullable=False),
        sa.Column("wiki_id", sa.String(length=80), nullable=False),
        sa.Column("source_id", sa.String(length=80), nullable=True),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("instruction", sa.Text(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["wiki_id"], ["wikis.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_wiki_operations_wiki_id", "wiki_operations", ["wiki_id"])
    op.create_index("ix_wiki_operations_source_id", "wiki_operations", ["source_id"])
    op.create_index("ix_wiki_operations_wiki_status", "wiki_operations", ["wiki_id", "status"])
    op.create_table(
        "run_wiki_snapshots",
        sa.Column("run_id", sa.String(length=80), nullable=False),
        sa.Column("wiki_id", sa.String(length=80), nullable=False),
        sa.Column("revision_id", sa.String(length=80), nullable=False),
        sa.Column("tree_hash", sa.String(length=64), nullable=False),
        sa.Column("raw_manifest_json", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["run_id"], ["runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("run_id"),
    )
    op.create_table(
        "wiki_access_grants",
        sa.Column("id", sa.String(length=80), nullable=False),
        sa.Column("user_id", sa.String(length=80), nullable=False),
        sa.Column("app_id", sa.String(length=80), nullable=False),
        sa.Column("graph_sha256", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["app_id"], ["apps.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "app_id", "graph_sha256", name="ux_wiki_access_grant_scope"),
    )
    op.create_index("ix_wiki_access_grants_app_id", "wiki_access_grants", ["app_id"])
    op.create_index("ix_wiki_access_grants_user_id", "wiki_access_grants", ["user_id"])
    op.create_index("ix_wiki_access_grants_user_app", "wiki_access_grants", ["user_id", "app_id"])


def downgrade() -> None:
    op.drop_table("wiki_access_grants")
    op.drop_table("run_wiki_snapshots")
    op.drop_table("wiki_operations")
    op.drop_table("wiki_revisions")
    op.drop_table("wiki_sources")
    op.drop_table("wikis")
