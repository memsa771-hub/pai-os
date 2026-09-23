"""Profile completion rules and conflict-candidate linkage.

Revision ID: 067
Revises: 066
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "067"
down_revision = "066"
branch_labels = None
depends_on = None


# key, tier, source_type, source_key, source_path, selector, question, priority
REQUIREMENTS = (
    ("education.history", "critical", "record_presence", "education", None, "any",
     "What is your current or highest qualification?", 100),
    ("goal.active", "critical", "record_presence", "goal", None, "any",
     "What education or career goal are you working toward?", 90),
    ("education.undergraduate_history", "important", "journey_gap", "undergraduate_history", "education", "any",
     "What qualification did you complete before postgraduate study?", 100),
    ("education.pre_university_history", "important", "journey_gap", "pre_university_history", "education", "any",
     "What qualification did you complete before undergraduate study?", 90),
    ("education.level", "important", "record_field", "education", "canonical_level", "current_or_highest",
     "What level is your current or highest qualification?", 80),
    ("education.field", "important", "record_field", "education", "field_of_study", "current_or_highest",
     "What subject or field did you study?", 70),
    ("education.status", "important", "record_field", "education", "academic_status", "current_or_highest",
     "Is that qualification current, completed, incomplete, or planned?", 60),
    ("education.result", "important", "record_field", "education", "result", "current_or_highest",
     "What academic result did you receive, including its grading scale if known?", 50),
    ("location.current_country", "important", "vault_fact", "location.current_country", None, "any",
     "Which country do you currently live in?", 40),
    ("finance.budget", "enrichment", "vault_fact", "finance.budget", None, "any",
     "What budget can you realistically fund, and in which currency?", 30),
    ("career.primary_interest", "enrichment", "vault_fact", "career.primary_interest", None, "any",
     "What field or career direction interests you most?", 20),
    ("preferences.target_countries", "enrichment", "vault_fact", "preferences.target_countries", None, "any",
     "Which countries are you considering?", 10),
)


def upgrade():
    op.add_column("pai_profile_issues", sa.Column("candidate_id", sa.Text(), nullable=True))
    op.create_foreign_key(
        "fk_profile_issue_candidate", "pai_profile_issues", "pai_memory_candidates",
        ["candidate_id"], ["id"], ondelete="SET NULL",
    )
    op.create_index("idx_pai_issues_candidate", "pai_profile_issues", ["candidate_id"])

    op.create_table(
        "pai_profile_requirements",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("key", sa.Text(), nullable=False),
        sa.Column("tier", sa.Text(), nullable=False),
        sa.Column("source_type", sa.Text(), nullable=False),
        sa.Column("source_key", sa.Text(), nullable=False),
        sa.Column("source_path", sa.Text()),
        sa.Column("selector", sa.Text(), nullable=False, server_default="any"),
        sa.Column("applicability", JSONB()),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("priority", sa.Integer(), nullable=False, server_default="50"),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()")),
        sa.CheckConstraint(
            "tier IN ('critical', 'important', 'enrichment')",
            name="ck_profile_requirement_tier",
        ),
        sa.CheckConstraint(
            "source_type IN ('vault_fact', 'record_presence', 'record_field', 'journey_gap')",
            name="ck_profile_requirement_source_type",
        ),
        sa.CheckConstraint(
            "selector IN ('any', 'current_or_highest')",
            name="ck_profile_requirement_selector",
        ),
        sa.CheckConstraint("priority >= 0", name="ck_profile_requirement_priority"),
        sa.CheckConstraint("version > 0", name="ck_profile_requirement_version"),
    )
    op.create_index(
        "uq_profile_requirement_key_version", "pai_profile_requirements",
        ["key", "version"], unique=True,
    )
    op.create_index("idx_profile_requirements_enabled", "pai_profile_requirements", ["enabled"])

    requirements = sa.table(
        "pai_profile_requirements",
        sa.column("id", sa.Text()), sa.column("key", sa.Text()),
        sa.column("tier", sa.Text()), sa.column("source_type", sa.Text()),
        sa.column("source_key", sa.Text()), sa.column("source_path", sa.Text()),
        sa.column("selector", sa.Text()), sa.column("applicability", JSONB()),
        sa.column("question", sa.Text()), sa.column("priority", sa.Integer()),
        sa.column("enabled", sa.Boolean()), sa.column("version", sa.Integer()),
    )
    op.bulk_insert(requirements, [{
        "id": f"profile-{key}", "key": key, "tier": tier,
        "source_type": source_type, "source_key": source_key,
        "source_path": source_path, "selector": selector,
        "applicability": None, "question": question, "priority": priority,
        "enabled": True, "version": 1,
    } for key, tier, source_type, source_key, source_path, selector, question, priority
        in REQUIREMENTS])


def downgrade():
    op.drop_table("pai_profile_requirements")
    op.drop_index("idx_pai_issues_candidate", table_name="pai_profile_issues")
    op.drop_constraint("fk_profile_issue_candidate", "pai_profile_issues", type_="foreignkey")
    op.drop_column("pai_profile_issues", "candidate_id")
