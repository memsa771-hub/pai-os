"""Repeatable student records and active scalar fact invariant.

Revision ID: 063
Revises: 062
"""

import json
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID
from alembic import op

revision = "063"
down_revision = "062"
branch_labels = None
depends_on = None


def _shared():
    return [
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("workspace_id", UUID(as_uuid=False), sa.ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False),
        sa.Column("subject_user_id", sa.Text()),
        sa.Column("source_type", sa.Text(), nullable=False),
        sa.Column("claim_origin", sa.Text(), nullable=False),
        sa.Column("capture_method", sa.Text(), nullable=False),
        sa.Column("verification_status", sa.Text(), nullable=False, server_default="self_reported"),
        sa.Column("evidence", JSONB()),
        sa.Column("status", sa.Text(), nullable=False, server_default="active"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()")),
    ]


def upgrade():
    op.add_column("pai_vault_field_definitions", sa.Column("context_tags", JSONB()))
    op.add_column("pai_vault_field_definitions", sa.Column("required_for", JSONB()))
    op.add_column("pai_vault_field_definitions", sa.Column("profile_priority", sa.Integer(), nullable=False, server_default="50"))
    op.add_column("pai_vault_field_definitions", sa.Column("extractable_from", JSONB()))
    op.add_column("pai_vault_field_definitions", sa.Column("verification_policy", sa.Text()))
    # Scalar-only identity, constraints and preferences. Repeatable histories
    # live in their own tables below rather than JSON arrays in VaultFact.
    scalar_fields = (
        ("identity.full_name", "identity", "string", "sensitive", ["application", "visa"], 90),
        ("identity.preferred_name", "identity", "string", "normal", ["discovery"], 95),
        ("identity.current_status", "identity", "string", "normal", ["discovery", "counseling"], 90),
        ("identity.nationality", "identity", "string", "sensitive", ["visa"], 60),
        ("identity.date_of_birth", "identity", "string", "restricted", ["application", "visa"], 40),
        ("identity.passport_number", "identity", "string", "restricted", ["application", "visa"], 20),
        ("contact.email", "contact", "string", "restricted", ["application"], 20),
        ("contact.phone", "contact", "string", "restricted", ["application"], 20),
        ("location.current_country", "location", "string", "normal", ["discovery", "matching"], 90),
        ("location.current_city", "location", "string", "normal", ["counseling"], 60),
        ("mobility.relocation_willingness", "mobility", "string", "normal", ["matching", "career"], 70),
        ("preferences.target_countries", "preferences", "array", "normal", ["matching"], 85),
        ("preferences.preferred_language", "preferences", "string", "normal", ["counseling"], 40),
        ("preferences.learning_style", "preferences", "string", "normal", ["counseling"], 30),
        ("finance.funding_status", "finance", "string", "sensitive", ["matching", "application"], 80),
        ("finance.scholarship_interest", "finance", "boolean", "sensitive", ["scholarship"], 70),
        ("career.primary_interest", "career", "string", "normal", ["career"], 80),
        ("accessibility.accommodation_needs", "accessibility", "string", "restricted", ["application"], 20),
    )
    connection = op.get_bind()
    for key, category, value_type, sensitivity, required_for, priority in scalar_fields:
        connection.execute(sa.text("""
            INSERT INTO pai_vault_field_definitions
              (id, key, category, data_type, validation_schema, cardinality,
               conflict_policy, sensitivity, searchable, enabled, version,
               description, context_tags, required_for, profile_priority)
            SELECT :id, :key, :category, :value_type, CAST(:schema AS jsonb),
                   :cardinality, :policy, :sensitivity, false, true, 1,
                   :description, CAST(:tags AS jsonb), CAST(:required_for AS jsonb), :priority
            WHERE NOT EXISTS (SELECT 1 FROM pai_vault_field_definitions WHERE key = :key)
        """), {
            "id": f"student-{key}", "key": key, "category": category,
            "value_type": value_type,
            "schema": json.dumps({"type": value_type}),
            "cardinality": "multi" if value_type == "array" else "single",
            "policy": "manual_review" if sensitivity == "restricted" else "latest_wins",
            "sensitivity": sensitivity,
            "description": key.replace(".", " ").replace("_", " "),
            "tags": json.dumps(required_for), "required_for": json.dumps(required_for),
            "priority": priority,
        })
    # A partial unique index is the concurrency boundary for all scalar facts.
    # Existing duplicates must be retired deterministically before creating it.
    op.execute("""
        WITH ranked AS (
          SELECT id, row_number() OVER (
            PARTITION BY workspace_id, field_key
            ORDER BY valid_from DESC NULLS LAST, created_at DESC NULLS LAST, id DESC
          ) AS rank
          FROM pai_vault_facts WHERE status = 'active'
        )
        UPDATE pai_vault_facts SET status = 'superseded', valid_until = NOW()
        WHERE id IN (SELECT id FROM ranked WHERE rank > 1)
    """)
    op.create_index("uq_vault_facts_ws_key_active", "pai_vault_facts", ["workspace_id", "field_key"], unique=True, postgresql_where=sa.text("status = 'active'"))

    op.create_table("pai_education_records", *_shared(),
        sa.Column("institution_name", sa.Text()), sa.Column("qualification_name", sa.Text(), nullable=False),
        sa.Column("canonical_level", sa.Text()), sa.Column("field_of_study", sa.Text()),
        sa.Column("start_date", sa.Text()), sa.Column("end_date", sa.Text()),
        sa.Column("graduation_year", sa.Integer()), sa.Column("academic_status", sa.Text()),
        sa.Column("result", JSONB()), sa.Column("details", JSONB()))
    op.create_table("pai_course_records", *_shared(),
        sa.Column("education_id", sa.Text(), sa.ForeignKey("pai_education_records.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.Text(), nullable=False), sa.Column("normalized_name", sa.Text()),
        sa.Column("grade", sa.Text()), sa.Column("score", JSONB()), sa.Column("credits", sa.Float()),
        sa.Column("details", JSONB()))
    op.create_table("pai_test_attempts", *_shared(),
        sa.Column("test_type", sa.Text(), nullable=False), sa.Column("original_name", sa.Text()),
        sa.Column("attempt_number", sa.Integer()), sa.Column("test_date", sa.Text()),
        sa.Column("expiry_date", sa.Text()), sa.Column("overall_score", sa.Text()),
        sa.Column("section_scores", JSONB()), sa.Column("details", JSONB()))
    op.create_table("pai_work_experiences", *_shared(),
        sa.Column("organization", sa.Text(), nullable=False), sa.Column("role", sa.Text(), nullable=False),
        sa.Column("experience_type", sa.Text()), sa.Column("start_date", sa.Text()),
        sa.Column("end_date", sa.Text()), sa.Column("details", JSONB()))
    op.create_table("pai_student_projects", *_shared(),
        sa.Column("name", sa.Text(), nullable=False), sa.Column("role", sa.Text()),
        sa.Column("start_date", sa.Text()), sa.Column("end_date", sa.Text()), sa.Column("details", JSONB()))
    op.create_table("pai_student_goals", *_shared(),
        sa.Column("goal_type", sa.Text(), nullable=False), sa.Column("title", sa.Text(), nullable=False),
        sa.Column("commitment", sa.Text()), sa.Column("target_date", sa.Text()), sa.Column("details", JSONB()))
    op.create_table("pai_student_skills", *_shared(),
        sa.Column("name", sa.Text(), nullable=False), sa.Column("proficiency", sa.Text()),
        sa.Column("details", JSONB()))
    op.create_table("pai_student_certifications", *_shared(),
        sa.Column("name", sa.Text(), nullable=False), sa.Column("issuer", sa.Text()),
        sa.Column("issued_on", sa.Text()), sa.Column("expires_on", sa.Text()),
        sa.Column("details", JSONB()))
    op.create_table("pai_student_applications", *_shared(),
        sa.Column("institution_name", sa.Text(), nullable=False), sa.Column("program_name", sa.Text()),
        sa.Column("intake", sa.Text()), sa.Column("application_status", sa.Text()),
        sa.Column("deadline", sa.Text()), sa.Column("details", JSONB()))
    op.create_table("pai_student_documents", *_shared(),
        sa.Column("file_id", sa.Text(), nullable=False), sa.Column("document_type", sa.Text(), nullable=False),
        sa.Column("title", sa.Text()), sa.Column("details", JSONB()))
    op.create_table("pai_profile_issues",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("workspace_id", UUID(as_uuid=False), sa.ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False),
        sa.Column("subject_user_id", sa.Text()), sa.Column("issue_type", sa.Text(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False), sa.Column("evidence", JSONB()),
        sa.Column("status", sa.Text(), nullable=False, server_default="open"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()")),
        sa.Column("resolved_at", sa.DateTime(timezone=True)))
    for table, index in (
        ("pai_education_records", "idx_pai_education_ws"),
        ("pai_work_experiences", "idx_pai_work_ws"),
        ("pai_student_projects", "idx_pai_projects_ws"),
        ("pai_student_goals", "idx_pai_goals_ws"),
        ("pai_student_skills", "idx_pai_skills_ws"),
        ("pai_student_certifications", "idx_pai_certifications_ws"),
        ("pai_student_applications", "idx_pai_applications_ws"),
        ("pai_student_documents", "idx_pai_documents_ws"),
        ("pai_profile_issues", "idx_pai_issues_ws"),
    ):
        op.create_index(index, table, ["workspace_id", "status"])
    op.create_index("idx_pai_course_education", "pai_course_records", ["education_id"])
    op.create_index("idx_pai_tests_ws", "pai_test_attempts", ["workspace_id", "status", "test_type"])


def downgrade():
    op.execute("DELETE FROM pai_vault_field_definitions WHERE id LIKE 'student-%'")
    for table in ("pai_profile_issues", "pai_student_documents", "pai_student_applications", "pai_student_certifications", "pai_student_skills", "pai_student_goals", "pai_student_projects", "pai_work_experiences", "pai_test_attempts", "pai_course_records", "pai_education_records"):
        op.drop_table(table)
    op.drop_index("uq_vault_facts_ws_key_active", table_name="pai_vault_facts")
    for column in ("verification_policy", "extractable_from", "profile_priority", "required_for", "context_tags"):
        op.drop_column("pai_vault_field_definitions", column)
