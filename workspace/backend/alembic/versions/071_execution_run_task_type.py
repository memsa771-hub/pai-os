"""Persist deterministic Operator task routing keys.

Revision ID: 071
Revises: 070
"""

import sqlalchemy as sa
from alembic import op

revision = "071"
down_revision = "070"
branch_labels = None
depends_on = None


def _has_column(inspector, table, name):
    return any(column["name"] == name for column in inspector.get_columns(table))


def upgrade():
    inspector = sa.inspect(op.get_bind())
    if not _has_column(inspector, "execution_runs", "task_type"):
        op.add_column("execution_runs", sa.Column("task_type", sa.Text(), nullable=True))
        op.create_index("idx_execution_runs_workspace_task_type", "execution_runs", ["workspace_id", "task_type"])


def downgrade():
    inspector = sa.inspect(op.get_bind())
    if _has_column(inspector, "execution_runs", "task_type"):
        op.drop_index("idx_execution_runs_workspace_task_type", table_name="execution_runs")
        op.drop_column("execution_runs", "task_type")
