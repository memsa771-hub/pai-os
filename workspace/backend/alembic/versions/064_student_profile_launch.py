"""Student record revision history and counseling context metadata.

Revision ID: 064
Revises: 063
"""
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID
from alembic import op

revision = "064"
down_revision = "063"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("pai_student_record_revisions",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("workspace_id", UUID(as_uuid=False), sa.ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False),
        sa.Column("record_type", sa.Text(), nullable=False), sa.Column("record_id", sa.Text(), nullable=False),
        sa.Column("before", JSONB()), sa.Column("after", JSONB(), nullable=False),
        sa.Column("source_type", sa.Text(), nullable=False), sa.Column("claim_origin", sa.Text(), nullable=False),
        sa.Column("capture_method", sa.Text(), nullable=False), sa.Column("evidence", JSONB()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()")))
    op.create_index("idx_pai_record_revision_ws", "pai_student_record_revisions", ["workspace_id", "record_type", "record_id"])
    op.execute("""UPDATE pai_vault_field_definitions
        SET context_tags = '["counseling", "matching", "career"]'::jsonb,
            profile_priority = 100
        WHERE key IN ('finance.budget', 'finance.funding_status', 'finance.scholarship_interest')
          AND enabled = true""")
    op.execute("""UPDATE pai_vault_field_definitions
        SET required_for = '["matching"]'::jsonb, profile_priority = 90
        WHERE key = 'preferences.countries' AND enabled = true""")


def downgrade():
    op.drop_table("pai_student_record_revisions")
    op.execute("""UPDATE pai_vault_field_definitions SET context_tags = NULL
        WHERE key IN ('finance.budget', 'finance.funding_status', 'finance.scholarship_interest')
          AND enabled = true""")
