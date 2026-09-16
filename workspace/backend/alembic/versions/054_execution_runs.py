# -*- coding: utf-8 -*-
"""Add execution_runs — PAI Operator's live execution state.

Revision ID: 054
Revises: 053

PAI Operator is the hidden execution intelligence PAI Counselor delegates to
(see app/services/operator.py). It has no WorkspaceMember/CloudAgentConfig row
of its own — it is never listed as an agent, never selectable in any picker —
so this table is its only persistent footprint: one row per delegated
objective, tracking status/plan/progress so both PAI Counselor (via the
operator.status tool) and the frontend (via GET /v1/operator/runs) can read
"what is PAI doing right now" without blocking chat on the actual work.

Revision ID: 054
Revises: 053
Create Date: 2026-09-16
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "054"
down_revision = "053"
branch_labels = None
depends_on = None


def _has_table(inspector, table) -> bool:
    return table in inspector.get_table_names()


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if not _has_table(inspector, "execution_runs"):
        op.create_table(
            "execution_runs",
            sa.Column("id", sa.Text(), primary_key=True),
            sa.Column("workspace_id", UUID(as_uuid=False), sa.ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False),
            sa.Column("requested_by", sa.Text(), nullable=False),
            sa.Column("objective", sa.Text(), nullable=False),
            sa.Column("constraints", JSONB(), nullable=True),
            sa.Column("context_refs", JSONB(), nullable=True),
            sa.Column("status", sa.Text(), nullable=False, server_default="pending"),
            sa.Column("current_step", sa.Text(), nullable=True),
            sa.Column("plan", JSONB(), nullable=True),
            sa.Column("completed_steps", JSONB(), nullable=True),
            sa.Column("missing", JSONB(), nullable=True),
            sa.Column("approval_required_for", sa.Text(), nullable=True),
            sa.Column("error", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()")),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()")),
            sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        )
        op.create_index("idx_execution_runs_workspace", "execution_runs", ["workspace_id"])
        op.create_index("idx_execution_runs_workspace_status", "execution_runs", ["workspace_id", "status"])


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if _has_table(inspector, "execution_runs"):
        op.drop_index("idx_execution_runs_workspace_status", table_name="execution_runs")
        op.drop_index("idx_execution_runs_workspace", table_name="execution_runs")
        op.drop_table("execution_runs")
