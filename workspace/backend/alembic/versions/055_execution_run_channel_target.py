# -*- coding: utf-8 -*-
"""Add execution_runs.channel_target — where to auto-post the result.

Revision ID: 055
Revises: 054

PAI Operator previously had no record of which chat thread an objective was
delegated from, so a finished run had nowhere to report back to except a
status row the student had to ask about. This column carries the same
event target (`channel/<thread-id>`) PAI Counselor's own replies use (see
`app.services.cloud_agent._post_response`), so `operator._execute` can post
the finished result into the same thread automatically, the way any other
cloud-agent reply already does.

Create Date: 2026-09-16
"""

import sqlalchemy as sa
from alembic import op

revision = "055"
down_revision = "054"
branch_labels = None
depends_on = None


def _has_column(inspector, table, column) -> bool:
    return column in {c["name"] for c in inspector.get_columns(table)}


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if not _has_column(inspector, "execution_runs", "channel_target"):
        op.add_column("execution_runs", sa.Column("channel_target", sa.Text(), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if _has_column(inspector, "execution_runs", "channel_target"):
        op.drop_column("execution_runs", "channel_target")
