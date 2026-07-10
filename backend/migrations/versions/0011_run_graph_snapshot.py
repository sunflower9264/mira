"""run graph snapshot

Revision ID: 0011_run_graph_snapshot
Revises: 0010_run_recovery
Create Date: 2026-06-01
"""

from alembic import op
import sqlalchemy as sa

revision = "0011_run_graph_snapshot"
down_revision = "0010_run_recovery"
branch_labels = None
depends_on = None


def _columns(table: str) -> set[str]:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if table not in set(inspector.get_table_names()):
        return set()
    return {column["name"] for column in inspector.get_columns(table)}


def upgrade() -> None:
    run_columns = _columns("runs")
    if not run_columns or "graph_json" in run_columns:
        return

    op.add_column("runs", sa.Column("graph_json", sa.Text(), nullable=True))

    bind = op.get_bind()
    bind.execute(
        sa.text(
            """
            UPDATE runs
            SET graph_json = COALESCE(
                (SELECT apps.graph_json FROM apps WHERE apps.id = runs.app_id),
                '{"nodes":[],"edges":[]}'
            )
            WHERE graph_json IS NULL
            """
        )
    )

    with op.batch_alter_table("runs") as batch_op:
        batch_op.alter_column("graph_json", existing_type=sa.Text(), nullable=False)


def downgrade() -> None:
    run_columns = _columns("runs")
    if "graph_json" in run_columns:
        op.drop_column("runs", "graph_json")
