"""run history indexes

Revision ID: 0018_run_history_indexes
Revises: 0017_app_archived_at
Create Date: 2026-07-06
"""

from alembic import op
import sqlalchemy as sa

revision = "0018_run_history_indexes"
down_revision = "0017_app_archived_at"
branch_labels = None
depends_on = None


def _indexes(table: str) -> set[str]:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if table not in set(inspector.get_table_names()):
        return set()
    return {index["name"] for index in inspector.get_indexes(table)}


def upgrade() -> None:
    run_indexes = _indexes("runs")
    if "ix_runs_owner_app_started_id" not in run_indexes:
        op.create_index(
            "ix_runs_owner_app_started_id",
            "runs",
            ["owner_id", "app_id", "started_at", "id"],
            unique=False,
        )

    step_indexes = _indexes("steps")
    if "ix_steps_run_ordering_id" not in step_indexes:
        op.create_index(
            "ix_steps_run_ordering_id",
            "steps",
            ["run_id", "ordering", "id"],
            unique=False,
        )


def downgrade() -> None:
    step_indexes = _indexes("steps")
    if "ix_steps_run_ordering_id" in step_indexes:
        op.drop_index("ix_steps_run_ordering_id", table_name="steps")

    run_indexes = _indexes("runs")
    if "ix_runs_owner_app_started_id" in run_indexes:
        op.drop_index("ix_runs_owner_app_started_id", table_name="runs")
