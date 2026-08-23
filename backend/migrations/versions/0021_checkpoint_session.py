"""store the provider session frozen by each workspace checkpoint

Revision ID: 0021_checkpoint_session
Revises: 0020_run_agent_workspace
Create Date: 2026-08-23
"""

from alembic import op
import sqlalchemy as sa

revision = "0021_checkpoint_session"
down_revision = "0020_run_agent_workspace"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("run_workspace_checkpoints") as batch_op:
        batch_op.add_column(sa.Column("provider_session_id", sa.String(length=255), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("run_workspace_checkpoints") as batch_op:
        batch_op.drop_column("provider_session_id")
