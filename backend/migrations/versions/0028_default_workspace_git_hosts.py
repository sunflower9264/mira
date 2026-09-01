"""add default workspace Git hosts

Revision ID: 0028_default_workspace_git_hosts
Revises: 0027_workspaces
Create Date: 2026-09-01
"""

from alembic import op


revision = "0028_default_workspace_git_hosts"
down_revision = "0027_workspaces"
branch_labels = None
depends_on = None


DEFAULT_HOSTS_JSON = '["github.com","gitlab.com","bitbucket.org","gitee.com"]'


def upgrade() -> None:
    op.execute(
        "UPDATE settings "
        f"SET workspace_git_allowed_hosts_json = '{DEFAULT_HOSTS_JSON}' "
        "WHERE workspace_git_allowed_hosts_json IS NULL "
        "OR TRIM(workspace_git_allowed_hosts_json) IN ('', '[]')"
    )


def downgrade() -> None:
    op.execute(
        "UPDATE settings "
        "SET workspace_git_allowed_hosts_json = '[]' "
        f"WHERE workspace_git_allowed_hosts_json = '{DEFAULT_HOSTS_JSON}'"
    )
