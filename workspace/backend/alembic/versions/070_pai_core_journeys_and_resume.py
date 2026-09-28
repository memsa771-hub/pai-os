"""Add Student Journey and resumable Operator state.

Revision ID: 070
Revises: 069
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "070"
down_revision = "069"
branch_labels = None
depends_on = None


def _has_table(inspector, name):
    return name in inspector.get_table_names()


def _has_column(inspector, table, name):
    return any(col["name"] == name for col in inspector.get_columns(table))


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    json_type = postgresql.JSONB(astext_type=sa.Text())
    for name in ("pending_action", "resume_input"):
        if not _has_column(inspector, "execution_runs", name):
            op.add_column("execution_runs", sa.Column(name, json_type, nullable=True))
            inspector = sa.inspect(bind)

    if not _has_table(inspector, "pai_student_journeys"):
        op.create_table(
            "pai_student_journeys",
            sa.Column("id", sa.Text(), primary_key=True),
            sa.Column("workspace_id", postgresql.UUID(as_uuid=False), sa.ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False),
            sa.Column("subject_user_id", sa.Text(), nullable=True),
            sa.Column("journey_type", sa.Text(), nullable=False),
            sa.Column("title", sa.Text(), nullable=False),
            sa.Column("status", sa.Text(), nullable=False, server_default="active"),
            sa.Column("is_primary", sa.Boolean(), nullable=False, server_default=sa.text("false")),
            sa.Column("active_goal", json_type),
            sa.Column("current_stage", sa.Text()),
            sa.Column("current_objective", sa.Text()),
            sa.Column("target_outcome", json_type),
            sa.Column("target_date", sa.DateTime(timezone=True)),
            sa.Column("milestones", json_type, nullable=False, server_default=sa.text("'[]'::jsonb")),
            sa.Column("decisions", json_type, nullable=False, server_default=sa.text("'[]'::jsonb")),
            sa.Column("unresolved_decisions", json_type, nullable=False, server_default=sa.text("'[]'::jsonb")),
            sa.Column("blockers", json_type, nullable=False, server_default=sa.text("'[]'::jsonb")),
            sa.Column("next_milestone", json_type),
            sa.Column("next_recommended_action", json_type),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()")),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()")),
            sa.Column("completed_at", sa.DateTime(timezone=True)),
            sa.CheckConstraint("status IN ('active', 'paused', 'completed', 'abandoned')", name="ck_student_journey_status"),
        )
        op.create_index("idx_student_journeys_workspace", "pai_student_journeys", ["workspace_id"])
        op.create_index("idx_student_journeys_workspace_status", "pai_student_journeys", ["workspace_id", "status"])
        op.create_index("uq_student_journey_primary_active", "pai_student_journeys", ["workspace_id"], unique=True,
                        postgresql_where=sa.text("is_primary = true AND status = 'active'"))

    inspector = sa.inspect(bind)
    if not _has_table(inspector, "pai_student_journey_events"):
        op.create_table(
            "pai_student_journey_events",
            sa.Column("id", sa.Text(), primary_key=True),
            sa.Column("journey_id", sa.Text(), sa.ForeignKey("pai_student_journeys.id", ondelete="CASCADE"), nullable=False),
            sa.Column("workspace_id", postgresql.UUID(as_uuid=False), sa.ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False),
            sa.Column("event_type", sa.Text(), nullable=False),
            sa.Column("payload", json_type, nullable=False, server_default=sa.text("'{}'::jsonb")),
            sa.Column("actor", sa.Text(), nullable=False, server_default="system"),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()")),
        )
        op.create_index("idx_student_journey_events_journey", "pai_student_journey_events", ["journey_id", "created_at"])
        op.create_index("idx_student_journey_events_workspace", "pai_student_journey_events", ["workspace_id"])


def downgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if _has_table(inspector, "pai_student_journey_events"):
        op.drop_table("pai_student_journey_events")
    inspector = sa.inspect(bind)
    if _has_table(inspector, "pai_student_journeys"):
        op.drop_table("pai_student_journeys")
    inspector = sa.inspect(bind)
    for name in ("resume_input", "pending_action"):
        if _has_column(inspector, "execution_runs", name):
            op.drop_column("execution_runs", name)
