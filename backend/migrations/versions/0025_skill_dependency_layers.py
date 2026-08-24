"""add isolated Skill dependency layer metadata

Revision ID: 0025_skill_dependency_layers
Revises: 0024_remove_legacy_ask_user
Create Date: 2026-08-24
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0025_skill_dependency_layers"
down_revision = "0024_remove_legacy_ask_user"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("skills", sa.Column("skill_root", sa.Text(), server_default="", nullable=False))
    op.add_column(
        "skills",
        sa.Column(
            "dependency_status",
            sa.String(length=20),
            server_default="pending",
            nullable=False,
        ),
    )
    op.add_column(
        "skills",
        sa.Column("dependency_key", sa.String(length=64), server_default="", nullable=False),
    )
    op.add_column(
        "skills",
        sa.Column("dependency_error", sa.Text(), server_default="", nullable=False),
    )


def downgrade() -> None:
    op.drop_column("skills", "dependency_error")
    op.drop_column("skills", "dependency_key")
    op.drop_column("skills", "dependency_status")
    op.drop_column("skills", "skill_root")
