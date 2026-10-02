"""Canonical exploration experiences for direction discovery.

Revision ID: 075
Revises: 074
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "075"
down_revision = "074"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "pai_student_activities",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("workspace_id", sa.UUID(as_uuid=False), sa.ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False),
        sa.Column("subject_user_id", sa.Text()),
        sa.Column("source_type", sa.Text(), nullable=False),
        sa.Column("claim_origin", sa.Text(), nullable=False),
        sa.Column("capture_method", sa.Text(), nullable=False),
        sa.Column("verification_status", sa.Text(), server_default=sa.text("'self_reported'"), nullable=False),
        sa.Column("evidence", postgresql.JSONB()),
        sa.Column("status", sa.Text(), server_default=sa.text("'active'"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()")),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("activity_type", sa.Text(), nullable=False),
        sa.Column("organization", sa.Text()),
        sa.Column("role", sa.Text()),
        sa.Column("start_date", sa.Text()),
        sa.Column("end_date", sa.Text()),
        sa.Column("details", postgresql.JSONB()),
    )
    op.create_index("idx_pai_activities_ws", "pai_student_activities", ["workspace_id", "status"])
    op.create_table(
        "pai_exploration_experiences",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("workspace_id", sa.UUID(as_uuid=False), sa.ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False),
        sa.Column("subject_user_id", sa.Text()),
        sa.Column("source_type", sa.Text(), nullable=False),
        sa.Column("claim_origin", sa.Text(), nullable=False),
        sa.Column("capture_method", sa.Text(), nullable=False),
        sa.Column("verification_status", sa.Text(), server_default=sa.text("'self_reported'"), nullable=False),
        sa.Column("evidence", postgresql.JSONB()),
        sa.Column("status", sa.Text(), server_default=sa.text("'active'"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()")),
        sa.Column("domain", sa.Text(), nullable=False),
        sa.Column("activity_type", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("activity_status", sa.Text(), server_default=sa.text("'planned'"), nullable=False),
        sa.Column("exposure_level", sa.Text()),
        sa.Column("started_at", sa.Text()),
        sa.Column("completed_at", sa.Text()),
        sa.Column("student_reflection", postgresql.JSONB()),
        sa.Column("evidence_refs", postgresql.JSONB()),
    )
    op.create_index("idx_pai_exploration_ws", "pai_exploration_experiences", ["workspace_id", "status", "domain"])


def downgrade() -> None:
    op.drop_index("idx_pai_exploration_ws", table_name="pai_exploration_experiences")
    op.drop_table("pai_exploration_experiences")
    op.drop_index("idx_pai_activities_ws", table_name="pai_student_activities")
    op.drop_table("pai_student_activities")
