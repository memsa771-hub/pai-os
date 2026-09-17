# -*- coding: utf-8 -*-
"""Add execution_runs durable result columns + tool_calls history.

Revision ID: 056
Revises: 055

Two independent fixes to ExecutionRun (see app/services/operator.py):

1. There was no durable result field — only status/plan/missing/current_step.
   A finished run's actual output existed solely as the chat message posted
   back into the thread; if that post failed, the result was gone. Adds
   `result` (the flexible, caller-defined final output), `verification` (the
   full VERIFY-phase JSON, not just its flattened missing/approval columns),
   `result_type`, and `result_artifact_id`.

2. `completed_steps` previously recorded tool names (web.search, files.write,
   ...) as a stand-in for plan progress, which is a different thing — a
   thread could show "5 tool calls" and call it "5/5 steps" when the plan
   only had 3 steps. Adds `tool_calls` so raw tool-call history has its own
   column, separate from plan-step progress. `plan`/`completed_steps` change
   shape in application code only (JSON already, no schema change needed for
   the objects-instead-of-strings switch).

Create Date: 2026-09-17
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "056"
down_revision = "055"
branch_labels = None
depends_on = None

_NEW_COLUMNS = (
    ("result", postgresql.JSONB()),
    ("verification", postgresql.JSONB()),
    ("result_type", sa.Text()),
    ("result_artifact_id", sa.Text()),
    ("tool_calls", postgresql.JSONB()),
)


def _has_column(inspector, table, column) -> bool:
    return column in {c["name"] for c in inspector.get_columns(table)}


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    for name, col_type in _NEW_COLUMNS:
        if not _has_column(inspector, "execution_runs", name):
            op.add_column("execution_runs", sa.Column(name, col_type, nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    for name, _ in _NEW_COLUMNS:
        if _has_column(inspector, "execution_runs", name):
            op.drop_column("execution_runs", name)
