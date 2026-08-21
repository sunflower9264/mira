"""workflow run contract

Revision ID: 0019_workflow_run_contract
Revises: 0018_run_history_indexes
Create Date: 2026-08-21
"""

from alembic import op
import sqlalchemy as sa

revision = "0019_workflow_run_contract"
down_revision = "0018_run_history_indexes"
branch_labels = None
depends_on = None


def _columns(table: str) -> set[str]:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if table not in set(inspector.get_table_names()):
        return set()
    return {column["name"] for column in inspector.get_columns(table)}


def upgrade() -> None:
    bind = op.get_bind()
    tables = set(sa.inspect(bind).get_table_names())
    if "runs" in tables:
        run_count = int(bind.execute(sa.text("SELECT COUNT(*) FROM runs")).scalar_one())
        if run_count:
            raise RuntimeError(
                "0019_workflow_run_contract requires an empty runs table; "
                "stop Mira and run scripts/reset_workflow_runs.py --apply first"
            )

    run_columns = _columns("runs")
    if run_columns:
        with op.batch_alter_table("runs") as batch_op:
            if "source_run_id" not in run_columns:
                batch_op.add_column(sa.Column("source_run_id", sa.String(length=80), nullable=True))
            if "rerun_from_node_id" not in run_columns:
                batch_op.add_column(sa.Column("rerun_from_node_id", sa.String(length=120), nullable=True))
            if "failure_kind" not in run_columns:
                batch_op.add_column(sa.Column("failure_kind", sa.String(length=24), nullable=True))

    step_columns = _columns("steps")
    if step_columns:
        with op.batch_alter_table("steps") as batch_op:
            if "reused_from_run_id" not in step_columns:
                batch_op.add_column(sa.Column("reused_from_run_id", sa.String(length=80), nullable=True))
            if "reused_from_step_id" not in step_columns:
                batch_op.add_column(sa.Column("reused_from_step_id", sa.String(length=80), nullable=True))
            if "failure_kind" not in step_columns:
                batch_op.add_column(sa.Column("failure_kind", sa.String(length=24), nullable=True))


def downgrade() -> None:
    step_columns = _columns("steps")
    if step_columns:
        with op.batch_alter_table("steps") as batch_op:
            for column in ("failure_kind", "reused_from_step_id", "reused_from_run_id"):
                if column in step_columns:
                    batch_op.drop_column(column)

    run_columns = _columns("runs")
    if run_columns:
        with op.batch_alter_table("runs") as batch_op:
            for column in ("failure_kind", "rerun_from_node_id", "source_run_id"):
                if column in run_columns:
                    batch_op.drop_column(column)
