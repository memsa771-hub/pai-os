"""Canonicalize profile fields and preserve legacy data.

Revision ID: 065
Revises: 064
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "065"
down_revision = "064"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("pai_vault_facts", sa.Column("claim_origin", sa.Text()))
    op.add_column("pai_vault_facts", sa.Column("capture_method", sa.Text()))
    op.add_column("pai_profile_issues", sa.Column("severity", sa.Text(), nullable=False, server_default="warning"))
    op.add_column("pai_profile_issues", sa.Column("affected_type", sa.Text()))
    op.add_column("pai_profile_issues", sa.Column("affected_id", sa.Text()))
    op.add_column("pai_profile_issues", sa.Column("clarification_question", sa.Text()))
    op.add_column("pai_profile_issues", sa.Column("resolution", JSONB()))
    def shared():
        return [sa.Column("id", sa.Text(), primary_key=True),
            sa.Column("workspace_id", UUID(as_uuid=False), sa.ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False),
            sa.Column("subject_user_id", sa.Text()), sa.Column("source_type", sa.Text(), nullable=False),
            sa.Column("claim_origin", sa.Text(), nullable=False), sa.Column("capture_method", sa.Text(), nullable=False),
            sa.Column("verification_status", sa.Text(), nullable=False, server_default="self_reported"),
            sa.Column("evidence", JSONB()), sa.Column("status", sa.Text(), nullable=False, server_default="active"),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()")),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()"))]
    tables = (
        ("pai_language_proficiencies", [sa.Column("language", sa.Text(), nullable=False), sa.Column("proficiency", sa.Text()), sa.Column("evidence_type", sa.Text()), sa.Column("details", JSONB())]),
        ("pai_research_records", [sa.Column("title", sa.Text(), nullable=False), sa.Column("organization", sa.Text()), sa.Column("role", sa.Text()), sa.Column("start_date", sa.Text()), sa.Column("end_date", sa.Text()), sa.Column("details", JSONB())]),
        ("pai_achievement_records", [sa.Column("title", sa.Text(), nullable=False), sa.Column("achievement_type", sa.Text()), sa.Column("issuer", sa.Text()), sa.Column("achieved_on", sa.Text()), sa.Column("details", JSONB())]),
        ("pai_financial_sponsors", [sa.Column("sponsor_type", sa.Text(), nullable=False), sa.Column("name", sa.Text()), sa.Column("commitment_status", sa.Text()), sa.Column("details", JSONB())]),
        ("pai_scholarship_applications", [sa.Column("scholarship_name", sa.Text(), nullable=False), sa.Column("provider", sa.Text()), sa.Column("application_status", sa.Text()), sa.Column("deadline", sa.Text()), sa.Column("details", JSONB())]),
        ("pai_visa_records", [sa.Column("country", sa.Text(), nullable=False), sa.Column("visa_type", sa.Text()), sa.Column("application_status", sa.Text()), sa.Column("expiry_date", sa.Text()), sa.Column("details", JSONB())]),
    )
    for table, columns in tables:
        op.create_table(table, *shared(), *columns)
        op.create_index(f"idx_{table}_ws", table, ["workspace_id", "status"])
    # The v4 canonical country key is preferences.target_countries. Move old
    # values only when the canonical key has no active value; otherwise retain
    # the old row as history instead of overwriting either claim.
    op.execute("""
      UPDATE pai_vault_facts old SET field_key = 'preferences.target_countries'
      WHERE old.field_key = 'preferences.countries' AND old.status = 'active'
        AND NOT EXISTS (
          SELECT 1 FROM pai_vault_facts current
          WHERE current.workspace_id = old.workspace_id
            AND current.field_key = 'preferences.target_countries'
            AND current.status = 'active'
        )
    """)
    op.execute("""
      UPDATE pai_vault_facts SET status = 'superseded', valid_until = NOW()
      WHERE field_key = 'preferences.countries' AND status = 'active'
    """)
    op.execute("""
      UPDATE pai_vault_field_definitions SET enabled = false
      WHERE key IN ('preferences.countries', 'education.cgpa',
                    'education.backlogs', 'tests.ielts.score')
    """)


def downgrade():
    for table in ("pai_visa_records", "pai_scholarship_applications", "pai_financial_sponsors", "pai_achievement_records", "pai_research_records", "pai_language_proficiencies"):
        op.drop_table(table)
    op.execute("""
      UPDATE pai_vault_field_definitions SET enabled = true
      WHERE key IN ('preferences.countries', 'education.cgpa',
                    'education.backlogs', 'tests.ielts.score')
    """)
    for column in ("resolution", "clarification_question", "affected_id", "affected_type", "severity"):
        op.drop_column("pai_profile_issues", column)
    op.drop_column("pai_vault_facts", "capture_method")
    op.drop_column("pai_vault_facts", "claim_origin")
