"""Student voice and external influence records.

Revision ID: 076
Revises: 075
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "076"
down_revision = "075"
branch_labels = None
depends_on = None


def _audit_columns():
    return (
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
    )


def upgrade() -> None:
    op.create_table(
        "pai_student_voice_statements", *_audit_columns(),
        sa.Column("voice_type", sa.Text(), nullable=False),
        sa.Column("statement", sa.Text(), nullable=False),
        sa.Column("direction", sa.Text()),
        sa.Column("commitment", sa.Text()),
    )
    op.create_index("idx_pai_voice_ws", "pai_student_voice_statements", ["workspace_id", "status"])
    op.create_table(
        "pai_external_influences", *_audit_columns(),
        sa.Column("influencer_type", sa.Text(), nullable=False),
        sa.Column("source_label", sa.Text(), nullable=False),
        sa.Column("suggested_direction", sa.Text(), nullable=False),
        sa.Column("influence_type", sa.Text(), nullable=False),
        sa.Column("student_alignment", sa.Text()),
        sa.Column("student_response", sa.Text()),
    )
    op.create_index("idx_pai_influences_ws", "pai_external_influences", ["workspace_id", "status"])


def downgrade() -> None:
    op.drop_index("idx_pai_influences_ws", table_name="pai_external_influences")
    op.drop_table("pai_external_influences")
    op.drop_index("idx_pai_voice_ws", table_name="pai_student_voice_statements")
    op.drop_table("pai_student_voice_statements")
