"""PAI OS baseline

This squashes revisions 001 through 074 into the schema required by the
current application. Existing databases that were upgraded through the old
chain remain compatible because the consolidated baseline keeps revision ID
074. A database still stamped below 074 must be upgraded with the old chain
before deploying the squashed history.

Revision ID: 074
Revises: None
Create Date: 2026-09-29 09:53:04.581751
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "074"
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_VAULT_FIELDS = (
    {
        "id": "baseline-finance-budget", "key": "finance.budget", "category": "finance",
        "data_type": "object", "validation_schema": {
            "type": "object", "properties": {
                "amount": {"type": "number", "minimum": 0},
                "currency": {"type": "string"},
                "period": {"type": "string", "enum": ["total", "per_year"]},
            }, "required": ["amount", "currency"], "additionalProperties": False,
        }, "cardinality": "single", "conflict_policy": "latest_wins",
        "sensitivity": "sensitive", "searchable": False,
        "context_tags": ["counseling", "matching", "career"], "required_for": None,
        "profile_priority": 100,
        "description": "Budget the student can fund, with currency and period.",
    },
    {
        "id": "baseline-preferences-study-mode", "key": "preferences.study_mode",
        "category": "preferences", "data_type": "string",
        "validation_schema": {"type": "string", "enum": ["on_campus", "online", "hybrid"]},
        "cardinality": "single", "conflict_policy": "latest_wins",
        "sensitivity": "normal", "searchable": False, "context_tags": None,
        "required_for": None, "profile_priority": 50,
        "description": "How the student wants to study.",
    },
) + tuple(
    {
        "id": f"student-{key}", "key": key, "category": category,
        "data_type": data_type, "validation_schema": schema,
        "cardinality": "multi" if data_type == "array" else "single",
        "conflict_policy": "manual_review" if sensitivity == "restricted" else "latest_wins",
        "sensitivity": sensitivity, "searchable": False,
        "context_tags": context_tags, "required_for": required_for,
        "profile_priority": priority,
        "description": key.replace(".", " ").replace("_", " "),
    }
    for key, category, data_type, sensitivity, context_tags, required_for, priority, schema in (
        ("identity.full_name", "identity", "string", "sensitive", ["application", "visa"], ["application", "visa"], 90, {"type": "string"}),
        ("identity.preferred_name", "identity", "string", "normal", ["discovery"], ["discovery"], 95, {"type": "string"}),
        ("identity.current_status", "identity", "string", "normal", ["discovery", "counseling"], ["discovery", "counseling"], 90, {"type": "string"}),
        ("identity.nationality", "identity", "string", "sensitive", ["visa", "counseling"], ["visa"], 60, {"type": "string"}),
        ("identity.date_of_birth", "identity", "string", "restricted", ["application", "visa"], ["application", "visa"], 40, {"type": "string"}),
        ("identity.passport_number", "identity", "string", "restricted", ["application", "visa"], ["application", "visa"], 20, {"type": "string"}),
        ("contact.email", "contact", "string", "restricted", ["application"], ["application"], 20, {"type": "string"}),
        ("contact.phone", "contact", "string", "restricted", ["application"], ["application"], 20, {"type": "string"}),
        ("location.current_country", "location", "string", "normal", ["discovery", "matching"], ["discovery", "matching"], 90, {"type": "string"}),
        ("location.current_city", "location", "string", "normal", ["counseling"], ["counseling"], 60, {"type": "string"}),
        ("mobility.relocation_willingness", "mobility", "string", "normal", ["matching", "career"], ["matching", "career"], 70, {"type": "string"}),
        ("preferences.target_countries", "preferences", "array", "normal", ["matching"], ["matching"], 85, {"type": "array"}),
        ("preferences.preferred_language", "preferences", "string", "normal", ["counseling"], ["counseling"], 40, {"type": "string"}),
        ("preferences.learning_style", "preferences", "string", "normal", ["counseling"], ["counseling"], 30, {"type": "string"}),
        ("finance.funding_status", "finance", "string", "sensitive", ["counseling", "matching", "career"], ["matching", "application"], 100, {"type": "string"}),
        ("finance.scholarship_interest", "finance", "boolean", "sensitive", ["counseling", "matching", "career"], ["scholarship"], 100, {"type": "boolean"}),
        ("career.primary_interest", "career", "string", "normal", ["career"], ["career"], 80, {"type": "string"}),
        ("accessibility.accommodation_needs", "accessibility", "string", "restricted", ["application"], ["application"], 20, {"type": "string"}),
        ("identity.gender", "identity", "string", "sensitive", ["scholarship", "counseling"], ["scholarship"], 50, {"type": "string", "enum": ["male", "female", "other", "undisclosed"]}),
        ("identity.status_category", "identity", "string", "normal", ["discovery"], ["discovery"], 95, {"type": "string", "enum": ["student", "professional", "other"]}),
    )
)


_PROFILE_REQUIREMENTS = (
    ("education.history", "critical", "record_presence", "education", None, "any", "What is your current or highest qualification?", 100),
    ("goal.active", "critical", "record_presence", "goal", None, "any", "What education or career goal are you working toward?", 90),
    ("education.undergraduate_history", "important", "journey_gap", "undergraduate_history", "education", "any", "What qualification did you complete before postgraduate study?", 100),
    ("education.pre_university_history", "important", "journey_gap", "pre_university_history", "education", "any", "What qualification did you complete before undergraduate study?", 90),
    ("education.level", "important", "record_field", "education", "canonical_level", "current_or_highest", "What level is your current or highest qualification?", 80),
    ("education.field", "important", "record_field", "education", "field_of_study", "current_or_highest", "What subject or field did you study?", 70),
    ("education.status", "important", "record_field", "education", "academic_status", "current_or_highest", "Is that qualification current, completed, incomplete, or planned?", 60),
    ("education.result", "important", "record_field", "education", "result", "current_or_highest", "What academic result did you receive, including its grading scale if known?", 50),
    ("location.current_country", "important", "vault_fact", "location.current_country", None, "any", "Which country do you currently live in?", 40),
    ("finance.budget", "enrichment", "vault_fact", "finance.budget", None, "any", "What budget can you realistically fund, and in which currency?", 30),
    ("career.primary_interest", "enrichment", "vault_fact", "career.primary_interest", None, "any", "What field or career direction interests you most?", 20),
    ("preferences.target_countries", "enrichment", "vault_fact", "preferences.target_countries", None, "any", "Which countries are you considering?", 10),
)


def _seed_reference_data() -> None:
    vault_fields = sa.table(
        "pai_vault_field_definitions",
        sa.column("id", sa.Text()), sa.column("key", sa.Text()),
        sa.column("category", sa.Text()), sa.column("data_type", sa.Text()),
        sa.column("validation_schema", postgresql.JSONB()),
        sa.column("cardinality", sa.Text()), sa.column("conflict_policy", sa.Text()),
        sa.column("sensitivity", sa.Text()), sa.column("searchable", sa.Boolean()),
        sa.column("enabled", sa.Boolean()), sa.column("version", sa.Integer()),
        sa.column("description", sa.Text()), sa.column("context_tags", postgresql.JSONB()),
        sa.column("required_for", postgresql.JSONB()), sa.column("profile_priority", sa.Integer()),
    )
    op.bulk_insert(vault_fields, [{**row, "enabled": True, "version": 1} for row in _VAULT_FIELDS])

    requirements = sa.table(
        "pai_profile_requirements",
        sa.column("id", sa.Text()), sa.column("key", sa.Text()),
        sa.column("tier", sa.Text()), sa.column("source_type", sa.Text()),
        sa.column("source_key", sa.Text()), sa.column("source_path", sa.Text()),
        sa.column("selector", sa.Text()), sa.column("applicability", postgresql.JSONB()),
        sa.column("question", sa.Text()), sa.column("priority", sa.Integer()),
        sa.column("enabled", sa.Boolean()), sa.column("version", sa.Integer()),
    )
    op.bulk_insert(requirements, [
        {
            "id": f"profile-{key}", "key": key, "tier": tier,
            "source_type": source_type, "source_key": source_key,
            "source_path": source_path, "selector": selector,
            "applicability": None, "question": question, "priority": priority,
            "enabled": True, "version": 1,
        }
        for key, tier, source_type, source_key, source_path, selector, question, priority
        in _PROFILE_REQUIREMENTS
    ])


def upgrade() -> None:
    # ### commands auto generated by Alembic - please adjust! ###
    op.create_table('agents',
    sa.Column('agent_name', sa.Text(), nullable=False),
    sa.Column('display_name', sa.Text(), nullable=True),
    sa.Column('agent_type', sa.Text(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('NOW()'), nullable=True),
    sa.PrimaryKeyConstraint('agent_name')
    )
    op.create_table('events',
    sa.Column('id', sa.Text(), nullable=False),
    sa.Column('network_id', sa.UUID(as_uuid=False), nullable=False),
    sa.Column('type', sa.Text(), nullable=False),
    sa.Column('source', sa.Text(), nullable=False),
    sa.Column('target', sa.Text(), nullable=False),
    sa.Column('payload', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('metadata', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('timestamp', sa.BigInteger(), nullable=False),
    sa.Column('visibility', sa.Text(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('NOW()'), nullable=True),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('idx_events_network_target', 'events', ['network_id', 'target'], unique=False)
    op.create_index('idx_events_network_timestamp', 'events', ['network_id', 'timestamp'], unique=False)
    op.create_index('idx_events_network_type', 'events', ['network_id', 'type'], unique=False)
    op.create_index('idx_events_network_type_target_ts', 'events', ['network_id', 'type', 'target', 'timestamp'], unique=False)
    op.create_table('pai_profile_requirements',
    sa.Column('id', sa.Text(), nullable=False),
    sa.Column('key', sa.Text(), nullable=False),
    sa.Column('tier', sa.Text(), nullable=False),
    sa.Column('source_type', sa.Text(), nullable=False),
    sa.Column('source_key', sa.Text(), nullable=False),
    sa.Column('source_path', sa.Text(), nullable=True),
    sa.Column('selector', sa.Text(), server_default=sa.text("'any'"), nullable=False),
    sa.Column('applicability', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('question', sa.Text(), nullable=False),
    sa.Column('priority', sa.Integer(), server_default=sa.text('50'), nullable=False),
    sa.Column('enabled', sa.Boolean(), server_default=sa.text('true'), nullable=False),
    sa.Column('version', sa.Integer(), server_default=sa.text('1'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('NOW()'), nullable=True),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('NOW()'), nullable=True),
    sa.CheckConstraint("selector IN ('any', 'current_or_highest')", name='ck_profile_requirement_selector'),
    sa.CheckConstraint("source_type IN ('vault_fact', 'record_presence', 'record_field', 'journey_gap')", name='ck_profile_requirement_source_type'),
    sa.CheckConstraint("tier IN ('critical', 'important', 'enrichment')", name='ck_profile_requirement_tier'),
    sa.CheckConstraint('priority >= 0', name='ck_profile_requirement_priority'),
    sa.CheckConstraint('version > 0', name='ck_profile_requirement_version'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('idx_profile_requirements_enabled', 'pai_profile_requirements', ['enabled'], unique=False)
    op.create_index('uq_profile_requirement_key_version', 'pai_profile_requirements', ['key', 'version'], unique=True)
    op.create_table('pai_vault_field_definitions',
    sa.Column('id', sa.Text(), nullable=False),
    sa.Column('key', sa.Text(), nullable=False),
    sa.Column('category', sa.Text(), nullable=False),
    sa.Column('data_type', sa.Text(), nullable=False),
    sa.Column('validation_schema', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('cardinality', sa.Text(), server_default=sa.text("'single'"), nullable=False),
    sa.Column('conflict_policy', sa.Text(), server_default=sa.text("'latest_wins'"), nullable=False),
    sa.Column('sensitivity', sa.Text(), server_default=sa.text("'normal'"), nullable=False),
    sa.Column('searchable', sa.Boolean(), server_default=sa.text('false'), nullable=False),
    sa.Column('context_tags', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('required_for', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('profile_priority', sa.Integer(), server_default=sa.text('50'), nullable=False),
    sa.Column('extractable_from', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('verification_policy', sa.Text(), nullable=True),
    sa.Column('enabled', sa.Boolean(), server_default=sa.text('true'), nullable=False),
    sa.Column('version', sa.Integer(), server_default=sa.text('1'), nullable=False),
    sa.Column('description', sa.Text(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('NOW()'), nullable=True),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('NOW()'), nullable=True),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('idx_vault_field_enabled', 'pai_vault_field_definitions', ['enabled'], unique=False)
    op.create_index('uq_vault_field_key_version', 'pai_vault_field_definitions', ['key', 'version'], unique=True)
    op.create_table('users',
    sa.Column('id', sa.UUID(as_uuid=False), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('email', sa.Text(), nullable=False),
    sa.Column('supabase_uid', sa.Text(), nullable=True),
    sa.Column('username', sa.Text(), nullable=True),
    sa.Column('display_name', sa.Text(), nullable=True),
    sa.Column('avatar_url', sa.Text(), nullable=True),
    sa.Column('welcome_seen', sa.Boolean(), server_default=sa.text('FALSE'), nullable=False),
    sa.Column('onboarded_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('NOW()'), nullable=True),
    sa.Column('last_login_at', sa.DateTime(timezone=True), nullable=True),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('email', name='uq_users_email')
    )
    op.create_index('uq_users_username_lower', 'users', [sa.literal_column('lower(username)')], unique=True, postgresql_where=sa.text('username IS NOT NULL'))
    op.create_table('feedback',
    sa.Column('id', sa.UUID(as_uuid=False), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('user_id', sa.UUID(as_uuid=False), nullable=True),
    sa.Column('user_email', sa.Text(), nullable=True),
    sa.Column('workspace_id', sa.Text(), nullable=True),
    sa.Column('kind', sa.Text(), nullable=False),
    sa.Column('message', sa.Text(), nullable=False),
    sa.Column('context', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('status', sa.Text(), server_default=sa.text("'new'"), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('NOW()'), nullable=True),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('workspaces',
    sa.Column('id', sa.UUID(as_uuid=False), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('slug', sa.Text(), nullable=True),
    sa.Column('name', sa.Text(), nullable=False),
    sa.Column('password_hash', sa.Text(), nullable=True),
    sa.Column('owner_user_id', sa.UUID(as_uuid=False), nullable=True),
    sa.Column('require_login', sa.Boolean(), server_default=sa.text('TRUE'), nullable=False),
    sa.Column('settings', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('status', sa.Text(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('NOW()'), nullable=True),
    sa.Column('last_activity_at', sa.DateTime(timezone=True), server_default=sa.text('NOW()'), nullable=True),
    sa.ForeignKeyConstraint(['owner_user_id'], ['users.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('slug')
    )
    op.create_index('idx_workspace_owner', 'workspaces', ['owner_user_id'], unique=False)
    op.create_index('uq_workspace_owner_active', 'workspaces', ['owner_user_id'], unique=True, postgresql_where=sa.text("owner_user_id IS NOT NULL AND status = 'active'"), sqlite_where=sa.text("owner_user_id IS NOT NULL AND status = 'active'"))
    op.create_table('background_jobs',
    sa.Column('id', sa.Text(), nullable=False),
    sa.Column('workspace_id', sa.UUID(as_uuid=False), nullable=True),
    sa.Column('job_type', sa.Text(), nullable=False),
    sa.Column('payload', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('status', sa.Text(), server_default=sa.text("'pending'"), nullable=False),
    sa.Column('priority', sa.Integer(), server_default=sa.text('0'), nullable=False),
    sa.Column('attempts', sa.Integer(), server_default=sa.text('0'), nullable=False),
    sa.Column('max_attempts', sa.Integer(), server_default=sa.text('5'), nullable=False),
    sa.Column('available_at', sa.DateTime(timezone=True), server_default=sa.text('NOW()'), nullable=True),
    sa.Column('locked_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('locked_by', sa.Text(), nullable=True),
    sa.Column('last_error', sa.Text(), nullable=True),
    sa.Column('idempotency_key', sa.Text(), nullable=True),
    sa.Column('result', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('NOW()'), nullable=True),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('NOW()'), nullable=True),
    sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True),
    sa.ForeignKeyConstraint(['workspace_id'], ['workspaces.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('idempotency_key', name='uq_background_jobs_idempotency')
    )
    op.create_index('idx_background_jobs_claim', 'background_jobs', ['status', 'available_at', 'priority'], unique=False)
    op.create_index('idx_background_jobs_workspace', 'background_jobs', ['workspace_id'], unique=False)
    op.create_table('browser_contexts',
    sa.Column('id', sa.Text(), nullable=False),
    sa.Column('workspace_id', sa.UUID(as_uuid=False), nullable=False),
    sa.Column('name', sa.Text(), nullable=False),
    sa.Column('bb_context_id', sa.Text(), nullable=True),
    sa.Column('domain', sa.Text(), nullable=True),
    sa.Column('status', sa.Text(), nullable=False),
    sa.Column('created_by', sa.Text(), nullable=False),
    sa.Column('shared_with', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('NOW()'), nullable=True),
    sa.Column('last_used_at', sa.DateTime(timezone=True), server_default=sa.text('NOW()'), nullable=True),
    sa.ForeignKeyConstraint(['workspace_id'], ['workspaces.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('workspace_id', 'name', name='uq_browser_context_workspace_name')
    )
    op.create_index('idx_browser_contexts_workspace_status', 'browser_contexts', ['workspace_id', 'status'], unique=False)
    op.create_table('browser_usage',
    sa.Column('id', sa.Text(), nullable=False),
    sa.Column('workspace_id', sa.UUID(as_uuid=False), nullable=False),
    sa.Column('tab_id', sa.Text(), nullable=False),
    sa.Column('session_id', sa.Text(), nullable=True),
    sa.Column('opened_by', sa.Text(), nullable=False),
    sa.Column('started_at', sa.DateTime(timezone=True), server_default=sa.text('NOW()'), nullable=False),
    sa.Column('ended_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('duration_seconds', sa.Integer(), nullable=True),
    sa.ForeignKeyConstraint(['workspace_id'], ['workspaces.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('idx_browser_usage_opened_by', 'browser_usage', ['opened_by'], unique=False)
    op.create_index('idx_browser_usage_started', 'browser_usage', ['started_at'], unique=False)
    op.create_index('idx_browser_usage_workspace', 'browser_usage', ['workspace_id'], unique=False)
    op.create_table('channels',
    sa.Column('id', sa.UUID(as_uuid=False), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('workspace_id', sa.UUID(as_uuid=False), nullable=False),
    sa.Column('name', sa.Text(), nullable=False),
    sa.Column('title', sa.Text(), nullable=True),
    sa.Column('title_manually_set', sa.Boolean(), server_default=sa.text('FALSE'), nullable=True),
    sa.Column('created_by', sa.Text(), nullable=True),
    sa.Column('master_agent', sa.Text(), nullable=True),
    sa.Column('resume_from', sa.Text(), nullable=True),
    sa.Column('orchestration_mode', sa.Text(), server_default=sa.text("'dynamic'"), nullable=False),
    sa.Column('orchestration_instruction', sa.Text(), nullable=True),
    sa.Column('workflow_id', sa.Text(), nullable=True),
    sa.Column('status', sa.Text(), nullable=True),
    sa.Column('starred', sa.Boolean(), server_default=sa.text('FALSE'), nullable=True),
    sa.Column('last_event_at', sa.BigInteger(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('NOW()'), nullable=True),
    sa.ForeignKeyConstraint(['workspace_id'], ['workspaces.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('idx_channels_status_last_event', 'channels', ['status', 'last_event_at'], unique=False)
    op.create_index('idx_channels_workspace_status', 'channels', ['workspace_id', 'status'], unique=False)
    op.create_index('uq_channels_ws_name', 'channels', ['workspace_id', 'name'], unique=True)
    op.create_table('execution_runs',
    sa.Column('id', sa.Text(), nullable=False),
    sa.Column('workspace_id', sa.UUID(as_uuid=False), nullable=False),
    sa.Column('requested_by', sa.Text(), nullable=False),
    sa.Column('channel_target', sa.Text(), nullable=True),
    sa.Column('objective', sa.Text(), nullable=False),
    sa.Column('task_type', sa.Text(), nullable=True),
    sa.Column('constraints', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('context_refs', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('status', sa.Text(), server_default='pending', nullable=False),
    sa.Column('current_step', sa.Text(), nullable=True),
    sa.Column('plan', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('completed_steps', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('tool_calls', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('missing', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('approval_required_for', sa.Text(), nullable=True),
    sa.Column('pending_action', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('resume_input', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('error', sa.Text(), nullable=True),
    sa.Column('result', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('verification', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('result_type', sa.Text(), nullable=True),
    sa.Column('result_artifact_id', sa.Text(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('NOW()'), nullable=True),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('NOW()'), nullable=True),
    sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True),
    sa.ForeignKeyConstraint(['workspace_id'], ['workspaces.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('idx_execution_runs_workspace', 'execution_runs', ['workspace_id'], unique=False)
    op.create_index('idx_execution_runs_workspace_status', 'execution_runs', ['workspace_id', 'status'], unique=False)
    op.create_index('idx_execution_runs_workspace_task_type', 'execution_runs', ['workspace_id', 'task_type'], unique=False)
    op.create_table('files',
    sa.Column('id', sa.Text(), nullable=False),
    sa.Column('workspace_id', sa.UUID(as_uuid=False), nullable=False),
    sa.Column('filename', sa.Text(), nullable=False),
    sa.Column('content_type', sa.Text(), nullable=False),
    sa.Column('size', sa.Integer(), nullable=False),
    sa.Column('storage_key', sa.Text(), nullable=False),
    sa.Column('uploaded_by', sa.Text(), nullable=False),
    sa.Column('channel_name', sa.Text(), nullable=True),
    sa.Column('status', sa.Text(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('NOW()'), nullable=True),
    sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('trash_id', sa.Text(), nullable=True),
    sa.Column('trash_path', sa.Text(), nullable=True),
    sa.ForeignKeyConstraint(['workspace_id'], ['workspaces.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('idx_files_trash', 'files', ['workspace_id', 'trash_id'], unique=False)
    op.create_index('idx_files_workspace_status', 'files', ['workspace_id', 'status'], unique=False)
    op.create_table('integration_bindings',
    sa.Column('id', sa.UUID(as_uuid=False), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('workspace_id', sa.UUID(as_uuid=False), nullable=False),
    sa.Column('platform', sa.Text(), nullable=False),
    sa.Column('name', sa.Text(), nullable=True),
    sa.Column('bot_token', sa.Text(), nullable=False),
    sa.Column('signing_secret', sa.Text(), nullable=True),
    sa.Column('webhook_secret', sa.Text(), nullable=True),
    sa.Column('external_team_id', sa.Text(), nullable=True),
    sa.Column('default_agent', sa.Text(), nullable=True),
    sa.Column('config', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('status', sa.Text(), nullable=False),
    sa.Column('last_error', sa.Text(), nullable=True),
    sa.Column('last_event_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_by', sa.Text(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('NOW()'), nullable=True),
    sa.ForeignKeyConstraint(['workspace_id'], ['workspaces.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('idx_integration_bindings_team', 'integration_bindings', ['external_team_id'], unique=False)
    op.create_index('idx_integration_bindings_workspace', 'integration_bindings', ['workspace_id'], unique=False)
    op.create_table('invitations',
    sa.Column('id', sa.UUID(as_uuid=False), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('workspace_id', sa.UUID(as_uuid=False), nullable=False),
    sa.Column('target_agent', sa.Text(), nullable=False),
    sa.Column('invite_token', sa.Text(), nullable=False),
    sa.Column('status', sa.Text(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('NOW()'), nullable=True),
    sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['workspace_id'], ['workspaces.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('invite_token')
    )
    op.create_table('kanban_tasks',
    sa.Column('id', sa.Text(), nullable=False),
    sa.Column('workspace_id', sa.UUID(as_uuid=False), nullable=False),
    sa.Column('title', sa.Text(), nullable=False),
    sa.Column('description', sa.Text(), server_default='', nullable=False),
    sa.Column('status', sa.Text(), server_default='backlog', nullable=False),
    sa.Column('assignee', sa.Text(), nullable=True),
    sa.Column('workflow_id', sa.Text(), nullable=True),
    sa.Column('created_by', sa.Text(), nullable=False),
    sa.Column('channel_name', sa.Text(), nullable=True),
    sa.Column('priority', sa.Text(), server_default='normal', nullable=False),
    sa.Column('position', sa.Integer(), server_default='0', nullable=False),
    sa.Column('knowledge_ids', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('file_ids', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('NOW()'), nullable=True),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('NOW()'), nullable=True),
    sa.ForeignKeyConstraint(['workspace_id'], ['workspaces.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('idx_kanban_workspace_channel', 'kanban_tasks', ['workspace_id', 'channel_name'], unique=False)
    op.create_index('idx_kanban_workspace_status', 'kanban_tasks', ['workspace_id', 'status'], unique=False)
    op.create_table('knowledge_entries',
    sa.Column('id', sa.Text(), nullable=False),
    sa.Column('workspace_id', sa.UUID(as_uuid=False), nullable=False),
    sa.Column('slug', sa.Text(), nullable=False),
    sa.Column('title', sa.Text(), nullable=False),
    sa.Column('description', sa.Text(), nullable=True),
    sa.Column('storage_key', sa.Text(), nullable=True),
    sa.Column('content_size', sa.Integer(), nullable=True),
    sa.Column('created_by', sa.Text(), nullable=False),
    sa.Column('updated_by', sa.Text(), nullable=True),
    sa.Column('status', sa.Text(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('NOW()'), nullable=True),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('NOW()'), nullable=True),
    sa.ForeignKeyConstraint(['workspace_id'], ['workspaces.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('workspace_id', 'slug', name='uq_knowledge_workspace_slug')
    )
    op.create_index('idx_knowledge_workspace_status', 'knowledge_entries', ['workspace_id', 'status'], unique=False)
    op.create_table('model_access',
    sa.Column('id', sa.UUID(as_uuid=False), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('workspace_id', sa.UUID(as_uuid=False), nullable=False),
    sa.Column('label', sa.Text(), nullable=False),
    sa.Column('provider', sa.Text(), nullable=False),
    sa.Column('base_url', sa.Text(), nullable=True),
    sa.Column('api_key', sa.Text(), nullable=False),
    sa.Column('created_by', sa.Text(), nullable=True),
    sa.Column('status', sa.Text(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('NOW()'), nullable=True),
    sa.ForeignKeyConstraint(['workspace_id'], ['workspaces.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('notifications',
    sa.Column('id', sa.Text(), nullable=False),
    sa.Column('workspace_id', sa.UUID(as_uuid=False), nullable=False),
    sa.Column('created_by', sa.Text(), nullable=False),
    sa.Column('title', sa.Text(), nullable=False),
    sa.Column('message', sa.Text(), nullable=False),
    sa.Column('priority', sa.Text(), nullable=False),
    sa.Column('is_read', sa.Boolean(), server_default=sa.text('FALSE'), nullable=True),
    sa.Column('channel_name', sa.Text(), nullable=True),
    sa.Column('thread_id', sa.Text(), nullable=True),
    sa.Column('link_url', sa.Text(), nullable=True),
    sa.Column('status', sa.Text(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('NOW()'), nullable=True),
    sa.Column('read_at', sa.DateTime(timezone=True), nullable=True),
    sa.ForeignKeyConstraint(['workspace_id'], ['workspaces.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('idx_notifications_created_at', 'notifications', ['created_at'], unique=False)
    op.create_index('idx_notifications_workspace_read', 'notifications', ['workspace_id', 'is_read'], unique=False)
    op.create_index('idx_notifications_workspace_status', 'notifications', ['workspace_id', 'status'], unique=False)
    op.create_table('pai_achievement_records',
    sa.Column('title', sa.Text(), nullable=False),
    sa.Column('achievement_type', sa.Text(), nullable=True),
    sa.Column('issuer', sa.Text(), nullable=True),
    sa.Column('achieved_on', sa.Text(), nullable=True),
    sa.Column('details', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('id', sa.Text(), nullable=False),
    sa.Column('workspace_id', sa.UUID(as_uuid=False), nullable=False),
    sa.Column('subject_user_id', sa.Text(), nullable=True),
    sa.Column('source_type', sa.Text(), nullable=False),
    sa.Column('claim_origin', sa.Text(), nullable=False),
    sa.Column('capture_method', sa.Text(), nullable=False),
    sa.Column('verification_status', sa.Text(), server_default=sa.text("'self_reported'"), nullable=False),
    sa.Column('evidence', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('status', sa.Text(), server_default=sa.text("'active'"), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('NOW()'), nullable=True),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('NOW()'), nullable=True),
    sa.ForeignKeyConstraint(['workspace_id'], ['workspaces.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('idx_pai_achievements_ws', 'pai_achievement_records', ['workspace_id', 'status'], unique=False)
    op.create_table('pai_education_records',
    sa.Column('institution_name', sa.Text(), nullable=True),
    sa.Column('qualification_name', sa.Text(), nullable=False),
    sa.Column('canonical_level', sa.Text(), nullable=True),
    sa.Column('field_of_study', sa.Text(), nullable=True),
    sa.Column('start_date', sa.Text(), nullable=True),
    sa.Column('end_date', sa.Text(), nullable=True),
    sa.Column('graduation_year', sa.Integer(), nullable=True),
    sa.Column('academic_status', sa.Text(), nullable=True),
    sa.Column('result', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('details', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('id', sa.Text(), nullable=False),
    sa.Column('workspace_id', sa.UUID(as_uuid=False), nullable=False),
    sa.Column('subject_user_id', sa.Text(), nullable=True),
    sa.Column('source_type', sa.Text(), nullable=False),
    sa.Column('claim_origin', sa.Text(), nullable=False),
    sa.Column('capture_method', sa.Text(), nullable=False),
    sa.Column('verification_status', sa.Text(), server_default=sa.text("'self_reported'"), nullable=False),
    sa.Column('evidence', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('status', sa.Text(), server_default=sa.text("'active'"), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('NOW()'), nullable=True),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('NOW()'), nullable=True),
    sa.ForeignKeyConstraint(['workspace_id'], ['workspaces.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('idx_pai_education_ws', 'pai_education_records', ['workspace_id', 'status'], unique=False)
    op.create_table('pai_episodes',
    sa.Column('id', sa.Text(), nullable=False),
    sa.Column('workspace_id', sa.UUID(as_uuid=False), nullable=False),
    sa.Column('subject_user_id', sa.Text(), nullable=True),
    sa.Column('event_type', sa.Text(), nullable=False),
    sa.Column('summary', sa.Text(), nullable=False),
    sa.Column('entities', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('importance', sa.Float(), server_default=sa.text('0.5'), nullable=False),
    sa.Column('occurred_at', sa.DateTime(timezone=True), server_default=sa.text('NOW()'), nullable=True),
    sa.Column('source_event_ids', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('metadata', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('fingerprint', sa.Text(), nullable=True),
    sa.Column('status', sa.Text(), server_default=sa.text("'active'"), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('NOW()'), nullable=True),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('NOW()'), nullable=True),
    sa.ForeignKeyConstraint(['workspace_id'], ['workspaces.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('idx_pai_episodes_workspace', 'pai_episodes', ['workspace_id'], unique=False)
    op.create_index('idx_pai_episodes_ws_status_fingerprint', 'pai_episodes', ['workspace_id', 'status', 'fingerprint'], unique=False)
    op.create_index('idx_pai_episodes_ws_status_time', 'pai_episodes', ['workspace_id', 'status', 'occurred_at'], unique=False)
    op.create_index('uq_pai_episodes_ws_fingerprint_active', 'pai_episodes', ['workspace_id', 'fingerprint'], unique=True, postgresql_where=sa.text("status = 'active' AND fingerprint IS NOT NULL"), sqlite_where=sa.text("status = 'active' AND fingerprint IS NOT NULL"))
    op.create_table('pai_financial_sponsors',
    sa.Column('sponsor_type', sa.Text(), nullable=False),
    sa.Column('name', sa.Text(), nullable=True),
    sa.Column('commitment_status', sa.Text(), nullable=True),
    sa.Column('details', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('id', sa.Text(), nullable=False),
    sa.Column('workspace_id', sa.UUID(as_uuid=False), nullable=False),
    sa.Column('subject_user_id', sa.Text(), nullable=True),
    sa.Column('source_type', sa.Text(), nullable=False),
    sa.Column('claim_origin', sa.Text(), nullable=False),
    sa.Column('capture_method', sa.Text(), nullable=False),
    sa.Column('verification_status', sa.Text(), server_default=sa.text("'self_reported'"), nullable=False),
    sa.Column('evidence', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('status', sa.Text(), server_default=sa.text("'active'"), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('NOW()'), nullable=True),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('NOW()'), nullable=True),
    sa.ForeignKeyConstraint(['workspace_id'], ['workspaces.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('idx_pai_sponsors_ws', 'pai_financial_sponsors', ['workspace_id', 'status'], unique=False)
    op.create_table('pai_language_proficiencies',
    sa.Column('language', sa.Text(), nullable=False),
    sa.Column('proficiency', sa.Text(), nullable=True),
    sa.Column('evidence_type', sa.Text(), nullable=True),
    sa.Column('details', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('id', sa.Text(), nullable=False),
    sa.Column('workspace_id', sa.UUID(as_uuid=False), nullable=False),
    sa.Column('subject_user_id', sa.Text(), nullable=True),
    sa.Column('source_type', sa.Text(), nullable=False),
    sa.Column('claim_origin', sa.Text(), nullable=False),
    sa.Column('capture_method', sa.Text(), nullable=False),
    sa.Column('verification_status', sa.Text(), server_default=sa.text("'self_reported'"), nullable=False),
    sa.Column('evidence', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('status', sa.Text(), server_default=sa.text("'active'"), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('NOW()'), nullable=True),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('NOW()'), nullable=True),
    sa.ForeignKeyConstraint(['workspace_id'], ['workspaces.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('idx_pai_languages_ws', 'pai_language_proficiencies', ['workspace_id', 'status'], unique=False)
    op.create_table('pai_memories',
    sa.Column('id', sa.Text(), nullable=False),
    sa.Column('workspace_id', sa.UUID(as_uuid=False), nullable=False),
    sa.Column('subject_user_id', sa.Text(), nullable=True),
    sa.Column('memory_type', sa.Text(), nullable=False),
    sa.Column('content', sa.Text(), nullable=False),
    sa.Column('entities', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('importance', sa.Float(), server_default=sa.text('0.5'), nullable=False),
    sa.Column('confidence', sa.Float(), server_default=sa.text('1.0'), nullable=False),
    sa.Column('source_type', sa.Text(), nullable=True),
    sa.Column('source_event_ids', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('metadata', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('valid_from', sa.DateTime(timezone=True), server_default=sa.text('NOW()'), nullable=True),
    sa.Column('valid_until', sa.DateTime(timezone=True), nullable=True),
    sa.Column('fingerprint', sa.Text(), nullable=True),
    sa.Column('status', sa.Text(), server_default=sa.text("'active'"), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('NOW()'), nullable=True),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('NOW()'), nullable=True),
    sa.ForeignKeyConstraint(['workspace_id'], ['workspaces.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('idx_pai_memories_workspace', 'pai_memories', ['workspace_id'], unique=False)
    op.create_index('idx_pai_memories_ws_status_fingerprint', 'pai_memories', ['workspace_id', 'status', 'fingerprint'], unique=False)
    op.create_index('idx_pai_memories_ws_status_type', 'pai_memories', ['workspace_id', 'status', 'memory_type'], unique=False)
    op.create_index('uq_pai_memories_ws_fingerprint_active', 'pai_memories', ['workspace_id', 'fingerprint'], unique=True, postgresql_where=sa.text("status = 'active' AND fingerprint IS NOT NULL"), sqlite_where=sa.text("status = 'active' AND fingerprint IS NOT NULL"))
    op.create_table('pai_memory_candidates',
    sa.Column('id', sa.Text(), nullable=False),
    sa.Column('workspace_id', sa.UUID(as_uuid=False), nullable=False),
    sa.Column('subject_user_id', sa.Text(), nullable=True),
    sa.Column('candidate_type', sa.Text(), nullable=False),
    sa.Column('operation', sa.Text(), nullable=False),
    sa.Column('key', sa.Text(), nullable=True),
    sa.Column('proposed_value', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('content', sa.Text(), nullable=True),
    sa.Column('entities', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('confidence', sa.Float(), server_default=sa.text('0.5'), nullable=False),
    sa.Column('source_type', sa.Text(), server_default=sa.text("'conversation'"), nullable=False),
    sa.Column('source_event_ids', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('evidence', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('status', sa.Text(), server_default=sa.text("'pending'"), nullable=False),
    sa.Column('rejection_reason', sa.Text(), nullable=True),
    sa.Column('reconciled_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('result_id', sa.Text(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('NOW()'), nullable=True),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('NOW()'), nullable=True),
    sa.ForeignKeyConstraint(['workspace_id'], ['workspaces.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('idx_memory_candidates_workspace', 'pai_memory_candidates', ['workspace_id'], unique=False)
    op.create_index('idx_memory_candidates_ws_status', 'pai_memory_candidates', ['workspace_id', 'status'], unique=False)
    op.create_table('pai_research_records',
    sa.Column('title', sa.Text(), nullable=False),
    sa.Column('organization', sa.Text(), nullable=True),
    sa.Column('role', sa.Text(), nullable=True),
    sa.Column('start_date', sa.Text(), nullable=True),
    sa.Column('end_date', sa.Text(), nullable=True),
    sa.Column('details', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('id', sa.Text(), nullable=False),
    sa.Column('workspace_id', sa.UUID(as_uuid=False), nullable=False),
    sa.Column('subject_user_id', sa.Text(), nullable=True),
    sa.Column('source_type', sa.Text(), nullable=False),
    sa.Column('claim_origin', sa.Text(), nullable=False),
    sa.Column('capture_method', sa.Text(), nullable=False),
    sa.Column('verification_status', sa.Text(), server_default=sa.text("'self_reported'"), nullable=False),
    sa.Column('evidence', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('status', sa.Text(), server_default=sa.text("'active'"), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('NOW()'), nullable=True),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('NOW()'), nullable=True),
    sa.ForeignKeyConstraint(['workspace_id'], ['workspaces.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('idx_pai_research_ws', 'pai_research_records', ['workspace_id', 'status'], unique=False)
    op.create_table('pai_scholarship_applications',
    sa.Column('scholarship_name', sa.Text(), nullable=False),
    sa.Column('provider', sa.Text(), nullable=True),
    sa.Column('application_status', sa.Text(), nullable=True),
    sa.Column('deadline', sa.Text(), nullable=True),
    sa.Column('details', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('id', sa.Text(), nullable=False),
    sa.Column('workspace_id', sa.UUID(as_uuid=False), nullable=False),
    sa.Column('subject_user_id', sa.Text(), nullable=True),
    sa.Column('source_type', sa.Text(), nullable=False),
    sa.Column('claim_origin', sa.Text(), nullable=False),
    sa.Column('capture_method', sa.Text(), nullable=False),
    sa.Column('verification_status', sa.Text(), server_default=sa.text("'self_reported'"), nullable=False),
    sa.Column('evidence', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('status', sa.Text(), server_default=sa.text("'active'"), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('NOW()'), nullable=True),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('NOW()'), nullable=True),
    sa.ForeignKeyConstraint(['workspace_id'], ['workspaces.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('idx_pai_scholarships_ws', 'pai_scholarship_applications', ['workspace_id', 'status'], unique=False)
    op.create_table('pai_student_applications',
    sa.Column('institution_name', sa.Text(), nullable=False),
    sa.Column('program_name', sa.Text(), nullable=True),
    sa.Column('intake', sa.Text(), nullable=True),
    sa.Column('application_status', sa.Text(), nullable=True),
    sa.Column('deadline', sa.Text(), nullable=True),
    sa.Column('details', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('id', sa.Text(), nullable=False),
    sa.Column('workspace_id', sa.UUID(as_uuid=False), nullable=False),
    sa.Column('subject_user_id', sa.Text(), nullable=True),
    sa.Column('source_type', sa.Text(), nullable=False),
    sa.Column('claim_origin', sa.Text(), nullable=False),
    sa.Column('capture_method', sa.Text(), nullable=False),
    sa.Column('verification_status', sa.Text(), server_default=sa.text("'self_reported'"), nullable=False),
    sa.Column('evidence', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('status', sa.Text(), server_default=sa.text("'active'"), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('NOW()'), nullable=True),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('NOW()'), nullable=True),
    sa.ForeignKeyConstraint(['workspace_id'], ['workspaces.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('idx_pai_applications_ws', 'pai_student_applications', ['workspace_id', 'status'], unique=False)
    op.create_table('pai_student_certifications',
    sa.Column('name', sa.Text(), nullable=False),
    sa.Column('issuer', sa.Text(), nullable=True),
    sa.Column('issued_on', sa.Text(), nullable=True),
    sa.Column('expires_on', sa.Text(), nullable=True),
    sa.Column('details', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('id', sa.Text(), nullable=False),
    sa.Column('workspace_id', sa.UUID(as_uuid=False), nullable=False),
    sa.Column('subject_user_id', sa.Text(), nullable=True),
    sa.Column('source_type', sa.Text(), nullable=False),
    sa.Column('claim_origin', sa.Text(), nullable=False),
    sa.Column('capture_method', sa.Text(), nullable=False),
    sa.Column('verification_status', sa.Text(), server_default=sa.text("'self_reported'"), nullable=False),
    sa.Column('evidence', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('status', sa.Text(), server_default=sa.text("'active'"), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('NOW()'), nullable=True),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('NOW()'), nullable=True),
    sa.ForeignKeyConstraint(['workspace_id'], ['workspaces.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('idx_pai_certifications_ws', 'pai_student_certifications', ['workspace_id', 'status'], unique=False)
    op.create_table('pai_student_documents',
    sa.Column('file_id', sa.Text(), nullable=False),
    sa.Column('document_type', sa.Text(), nullable=False),
    sa.Column('title', sa.Text(), nullable=True),
    sa.Column('details', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('id', sa.Text(), nullable=False),
    sa.Column('workspace_id', sa.UUID(as_uuid=False), nullable=False),
    sa.Column('subject_user_id', sa.Text(), nullable=True),
    sa.Column('source_type', sa.Text(), nullable=False),
    sa.Column('claim_origin', sa.Text(), nullable=False),
    sa.Column('capture_method', sa.Text(), nullable=False),
    sa.Column('verification_status', sa.Text(), server_default=sa.text("'self_reported'"), nullable=False),
    sa.Column('evidence', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('status', sa.Text(), server_default=sa.text("'active'"), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('NOW()'), nullable=True),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('NOW()'), nullable=True),
    sa.ForeignKeyConstraint(['workspace_id'], ['workspaces.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('idx_pai_documents_ws', 'pai_student_documents', ['workspace_id', 'status'], unique=False)
    op.create_table('pai_student_goals',
    sa.Column('goal_type', sa.Text(), nullable=False),
    sa.Column('title', sa.Text(), nullable=False),
    sa.Column('commitment', sa.Text(), nullable=True),
    sa.Column('target_date', sa.Text(), nullable=True),
    sa.Column('details', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('id', sa.Text(), nullable=False),
    sa.Column('workspace_id', sa.UUID(as_uuid=False), nullable=False),
    sa.Column('subject_user_id', sa.Text(), nullable=True),
    sa.Column('source_type', sa.Text(), nullable=False),
    sa.Column('claim_origin', sa.Text(), nullable=False),
    sa.Column('capture_method', sa.Text(), nullable=False),
    sa.Column('verification_status', sa.Text(), server_default=sa.text("'self_reported'"), nullable=False),
    sa.Column('evidence', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('status', sa.Text(), server_default=sa.text("'active'"), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('NOW()'), nullable=True),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('NOW()'), nullable=True),
    sa.ForeignKeyConstraint(['workspace_id'], ['workspaces.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('idx_pai_goals_ws', 'pai_student_goals', ['workspace_id', 'status'], unique=False)
    op.create_table('pai_student_journeys',
    sa.Column('id', sa.Text(), nullable=False),
    sa.Column('workspace_id', sa.UUID(as_uuid=False), nullable=False),
    sa.Column('subject_user_id', sa.Text(), nullable=True),
    sa.Column('journey_type', sa.Text(), nullable=False),
    sa.Column('title', sa.Text(), nullable=False),
    sa.Column('status', sa.Text(), server_default=sa.text("'active'"), nullable=False),
    sa.Column('is_primary', sa.Boolean(), server_default=sa.text('false'), nullable=False),
    sa.Column('active_goal', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('goals', postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'[]'"), nullable=False),
    sa.Column('current_focus_goal_id', sa.Text(), nullable=True),
    sa.Column('current_stage', sa.Text(), nullable=True),
    sa.Column('current_objective', sa.Text(), nullable=True),
    sa.Column('target_outcome', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('target_date', sa.DateTime(timezone=True), nullable=True),
    sa.Column('milestones', postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'[]'"), nullable=False),
    sa.Column('decisions', postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'[]'"), nullable=False),
    sa.Column('unresolved_decisions', postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'[]'"), nullable=False),
    sa.Column('blockers', postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'[]'"), nullable=False),
    sa.Column('next_milestone', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('next_recommended_action', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('NOW()'), nullable=True),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('NOW()'), nullable=True),
    sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True),
    sa.CheckConstraint("current_stage IS NULL OR current_stage IN ('ORIENTING','UNDERSTANDING','ALIGNING','PLANNING','ACTING','REVIEWING','COMPLETED')", name='ck_student_journey_stage'),
    sa.CheckConstraint("status IN ('active', 'paused', 'completed', 'abandoned')", name='ck_student_journey_status'),
    sa.ForeignKeyConstraint(['workspace_id'], ['workspaces.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('idx_student_journeys_workspace', 'pai_student_journeys', ['workspace_id'], unique=False)
    op.create_index('idx_student_journeys_workspace_status', 'pai_student_journeys', ['workspace_id', 'status'], unique=False)
    op.create_index('uq_student_journey_primary_active', 'pai_student_journeys', ['workspace_id'], unique=True, postgresql_where=sa.text("is_primary = true AND status = 'active'"), sqlite_where=sa.text("is_primary = 1 AND status = 'active'"))
    op.create_table('pai_student_projects',
    sa.Column('name', sa.Text(), nullable=False),
    sa.Column('role', sa.Text(), nullable=True),
    sa.Column('start_date', sa.Text(), nullable=True),
    sa.Column('end_date', sa.Text(), nullable=True),
    sa.Column('details', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('id', sa.Text(), nullable=False),
    sa.Column('workspace_id', sa.UUID(as_uuid=False), nullable=False),
    sa.Column('subject_user_id', sa.Text(), nullable=True),
    sa.Column('source_type', sa.Text(), nullable=False),
    sa.Column('claim_origin', sa.Text(), nullable=False),
    sa.Column('capture_method', sa.Text(), nullable=False),
    sa.Column('verification_status', sa.Text(), server_default=sa.text("'self_reported'"), nullable=False),
    sa.Column('evidence', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('status', sa.Text(), server_default=sa.text("'active'"), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('NOW()'), nullable=True),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('NOW()'), nullable=True),
    sa.ForeignKeyConstraint(['workspace_id'], ['workspaces.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('idx_pai_projects_ws', 'pai_student_projects', ['workspace_id', 'status'], unique=False)
    op.create_table('pai_student_record_revisions',
    sa.Column('id', sa.Text(), nullable=False),
    sa.Column('workspace_id', sa.UUID(as_uuid=False), nullable=False),
    sa.Column('record_type', sa.Text(), nullable=False),
    sa.Column('record_id', sa.Text(), nullable=False),
    sa.Column('before', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('after', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('source_type', sa.Text(), nullable=False),
    sa.Column('claim_origin', sa.Text(), nullable=False),
    sa.Column('capture_method', sa.Text(), nullable=False),
    sa.Column('evidence', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('NOW()'), nullable=True),
    sa.ForeignKeyConstraint(['workspace_id'], ['workspaces.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('idx_pai_record_revision_ws', 'pai_student_record_revisions', ['workspace_id', 'record_type', 'record_id'], unique=False)
    op.create_table('pai_student_skills',
    sa.Column('name', sa.Text(), nullable=False),
    sa.Column('proficiency', sa.Text(), nullable=True),
    sa.Column('details', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('id', sa.Text(), nullable=False),
    sa.Column('workspace_id', sa.UUID(as_uuid=False), nullable=False),
    sa.Column('subject_user_id', sa.Text(), nullable=True),
    sa.Column('source_type', sa.Text(), nullable=False),
    sa.Column('claim_origin', sa.Text(), nullable=False),
    sa.Column('capture_method', sa.Text(), nullable=False),
    sa.Column('verification_status', sa.Text(), server_default=sa.text("'self_reported'"), nullable=False),
    sa.Column('evidence', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('status', sa.Text(), server_default=sa.text("'active'"), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('NOW()'), nullable=True),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('NOW()'), nullable=True),
    sa.ForeignKeyConstraint(['workspace_id'], ['workspaces.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('idx_pai_skills_ws', 'pai_student_skills', ['workspace_id', 'status'], unique=False)
    op.create_table('pai_test_attempts',
    sa.Column('test_type', sa.Text(), nullable=False),
    sa.Column('original_name', sa.Text(), nullable=True),
    sa.Column('attempt_number', sa.Integer(), nullable=True),
    sa.Column('test_date', sa.Text(), nullable=True),
    sa.Column('expiry_date', sa.Text(), nullable=True),
    sa.Column('overall_score', sa.Text(), nullable=True),
    sa.Column('section_scores', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('details', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('id', sa.Text(), nullable=False),
    sa.Column('workspace_id', sa.UUID(as_uuid=False), nullable=False),
    sa.Column('subject_user_id', sa.Text(), nullable=True),
    sa.Column('source_type', sa.Text(), nullable=False),
    sa.Column('claim_origin', sa.Text(), nullable=False),
    sa.Column('capture_method', sa.Text(), nullable=False),
    sa.Column('verification_status', sa.Text(), server_default=sa.text("'self_reported'"), nullable=False),
    sa.Column('evidence', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('status', sa.Text(), server_default=sa.text("'active'"), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('NOW()'), nullable=True),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('NOW()'), nullable=True),
    sa.ForeignKeyConstraint(['workspace_id'], ['workspaces.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('idx_pai_tests_ws', 'pai_test_attempts', ['workspace_id', 'status', 'test_type'], unique=False)
    op.create_table('pai_vault_facts',
    sa.Column('id', sa.Text(), nullable=False),
    sa.Column('workspace_id', sa.UUID(as_uuid=False), nullable=False),
    sa.Column('subject_user_id', sa.Text(), nullable=True),
    sa.Column('field_key', sa.Text(), nullable=False),
    sa.Column('field_version', sa.Integer(), nullable=True),
    sa.Column('value', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('confidence', sa.Float(), server_default=sa.text('1.0'), nullable=False),
    sa.Column('source_type', sa.Text(), nullable=False),
    sa.Column('claim_origin', sa.Text(), nullable=True),
    sa.Column('capture_method', sa.Text(), nullable=True),
    sa.Column('source_event_id', sa.Text(), nullable=True),
    sa.Column('evidence', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('valid_from', sa.DateTime(timezone=True), server_default=sa.text('NOW()'), nullable=True),
    sa.Column('valid_until', sa.DateTime(timezone=True), nullable=True),
    sa.Column('status', sa.Text(), server_default=sa.text("'active'"), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('NOW()'), nullable=True),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('NOW()'), nullable=True),
    sa.ForeignKeyConstraint(['workspace_id'], ['workspaces.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('idx_vault_facts_workspace', 'pai_vault_facts', ['workspace_id'], unique=False)
    op.create_index('idx_vault_facts_ws_status_key', 'pai_vault_facts', ['workspace_id', 'status', 'field_key'], unique=False)
    op.create_index('uq_vault_facts_ws_key_active', 'pai_vault_facts', ['workspace_id', 'field_key'], unique=True, postgresql_where=sa.text("status = 'active'"), sqlite_where=sa.text("status = 'active'"))
    op.create_table('pai_visa_records',
    sa.Column('country', sa.Text(), nullable=False),
    sa.Column('visa_type', sa.Text(), nullable=True),
    sa.Column('application_status', sa.Text(), nullable=True),
    sa.Column('expiry_date', sa.Text(), nullable=True),
    sa.Column('details', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('id', sa.Text(), nullable=False),
    sa.Column('workspace_id', sa.UUID(as_uuid=False), nullable=False),
    sa.Column('subject_user_id', sa.Text(), nullable=True),
    sa.Column('source_type', sa.Text(), nullable=False),
    sa.Column('claim_origin', sa.Text(), nullable=False),
    sa.Column('capture_method', sa.Text(), nullable=False),
    sa.Column('verification_status', sa.Text(), server_default=sa.text("'self_reported'"), nullable=False),
    sa.Column('evidence', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('status', sa.Text(), server_default=sa.text("'active'"), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('NOW()'), nullable=True),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('NOW()'), nullable=True),
    sa.ForeignKeyConstraint(['workspace_id'], ['workspaces.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('idx_pai_visas_ws', 'pai_visa_records', ['workspace_id', 'status'], unique=False)
    op.create_table('pai_work_experiences',
    sa.Column('organization', sa.Text(), nullable=False),
    sa.Column('role', sa.Text(), nullable=False),
    sa.Column('experience_type', sa.Text(), nullable=True),
    sa.Column('start_date', sa.Text(), nullable=True),
    sa.Column('end_date', sa.Text(), nullable=True),
    sa.Column('details', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('id', sa.Text(), nullable=False),
    sa.Column('workspace_id', sa.UUID(as_uuid=False), nullable=False),
    sa.Column('subject_user_id', sa.Text(), nullable=True),
    sa.Column('source_type', sa.Text(), nullable=False),
    sa.Column('claim_origin', sa.Text(), nullable=False),
    sa.Column('capture_method', sa.Text(), nullable=False),
    sa.Column('verification_status', sa.Text(), server_default=sa.text("'self_reported'"), nullable=False),
    sa.Column('evidence', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('status', sa.Text(), server_default=sa.text("'active'"), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('NOW()'), nullable=True),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('NOW()'), nullable=True),
    sa.ForeignKeyConstraint(['workspace_id'], ['workspaces.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('idx_pai_work_ws', 'pai_work_experiences', ['workspace_id', 'status'], unique=False)
    op.create_table('routines',
    sa.Column('id', sa.Text(), nullable=False),
    sa.Column('workspace_id', sa.UUID(as_uuid=False), nullable=False),
    sa.Column('channel_name', sa.Text(), nullable=False),
    sa.Column('thread_id', sa.Text(), nullable=True),
    sa.Column('created_by', sa.Text(), nullable=False),
    sa.Column('name', sa.Text(), nullable=False),
    sa.Column('message', sa.Text(), nullable=False),
    sa.Column('context', sa.Text(), nullable=True),
    sa.Column('schedule_hour', sa.Integer(), nullable=True),
    sa.Column('schedule_minute', sa.Integer(), nullable=True),
    sa.Column('schedule_days', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('schedule_interval_minutes', sa.Integer(), nullable=True),
    sa.Column('timezone', sa.Text(), nullable=True),
    sa.Column('next_fires_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('last_fired_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('status', sa.Text(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('NOW()'), nullable=True),
    sa.ForeignKeyConstraint(['workspace_id'], ['workspaces.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('idx_routines_next_fires_status', 'routines', ['next_fires_at', 'status'], unique=False)
    op.create_index('idx_routines_workspace_channel', 'routines', ['workspace_id', 'channel_name'], unique=False)
    op.create_table('share_snapshots',
    sa.Column('id', sa.Text(), nullable=False),
    sa.Column('workspace_id', sa.UUID(as_uuid=False), nullable=False),
    sa.Column('channel_name', sa.Text(), nullable=False),
    sa.Column('title', sa.Text(), nullable=True),
    sa.Column('created_by', sa.Text(), nullable=False),
    sa.Column('snapshot_data', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('share_token', sa.Text(), nullable=False),
    sa.Column('message_count', sa.Integer(), nullable=False),
    sa.Column('status', sa.Text(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('NOW()'), nullable=True),
    sa.ForeignKeyConstraint(['workspace_id'], ['workspaces.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('share_token')
    )
    op.create_index('idx_share_snapshots_token', 'share_snapshots', ['share_token'], unique=False)
    op.create_index('idx_share_snapshots_workspace', 'share_snapshots', ['workspace_id'], unique=False)
    op.create_table('timers',
    sa.Column('id', sa.Text(), nullable=False),
    sa.Column('workspace_id', sa.UUID(as_uuid=False), nullable=False),
    sa.Column('channel_name', sa.Text(), nullable=False),
    sa.Column('thread_id', sa.Text(), nullable=True),
    sa.Column('created_by', sa.Text(), nullable=False),
    sa.Column('message', sa.Text(), nullable=False),
    sa.Column('delay_seconds', sa.Integer(), nullable=False),
    sa.Column('fires_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('status', sa.Text(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('NOW()'), nullable=True),
    sa.ForeignKeyConstraint(['workspace_id'], ['workspaces.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('idx_timers_fires_at_status', 'timers', ['fires_at', 'status'], unique=False)
    op.create_index('idx_timers_workspace_channel', 'timers', ['workspace_id', 'channel_name'], unique=False)
    op.create_table('todos',
    sa.Column('id', sa.Text(), nullable=False),
    sa.Column('workspace_id', sa.UUID(as_uuid=False), nullable=False),
    sa.Column('channel_name', sa.Text(), nullable=False),
    sa.Column('thread_id', sa.Text(), nullable=True),
    sa.Column('created_by', sa.Text(), nullable=False),
    sa.Column('assignee', sa.Text(), nullable=False),
    sa.Column('content', sa.Text(), nullable=False),
    sa.Column('status', sa.Text(), nullable=False),
    sa.Column('position', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('NOW()'), nullable=True),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('NOW()'), nullable=True),
    sa.ForeignKeyConstraint(['workspace_id'], ['workspaces.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('idx_todos_workspace_channel', 'todos', ['workspace_id', 'channel_name'], unique=False)
    op.create_index('idx_todos_workspace_created_by', 'todos', ['workspace_id', 'created_by'], unique=False)
    op.create_table('workflow_runs',
    sa.Column('id', sa.Text(), nullable=False),
    sa.Column('workspace_id', sa.UUID(as_uuid=False), nullable=False),
    sa.Column('workflow_id', sa.Text(), nullable=True),
    sa.Column('channel_name', sa.Text(), nullable=False),
    sa.Column('snapshot', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('current_step', sa.Text(), nullable=True),
    sa.Column('iterations', sa.Integer(), server_default='0', nullable=False),
    sa.Column('status', sa.Text(), server_default='running', nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('NOW()'), nullable=True),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('NOW()'), nullable=True),
    sa.ForeignKeyConstraint(['workspace_id'], ['workspaces.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('idx_workflow_runs_ws_channel', 'workflow_runs', ['workspace_id', 'channel_name'], unique=False)
    op.create_table('workflows',
    sa.Column('id', sa.Text(), nullable=False),
    sa.Column('workspace_id', sa.UUID(as_uuid=False), nullable=False),
    sa.Column('name', sa.Text(), nullable=False),
    sa.Column('description', sa.Text(), server_default='', nullable=False),
    sa.Column('steps', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('max_iterations', sa.Integer(), server_default='5', nullable=False),
    sa.Column('created_by', sa.Text(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('NOW()'), nullable=True),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('NOW()'), nullable=True),
    sa.ForeignKeyConstraint(['workspace_id'], ['workspaces.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('idx_workflows_workspace', 'workflows', ['workspace_id'], unique=False)
    op.create_table('workspace_members',
    sa.Column('workspace_id', sa.UUID(as_uuid=False), nullable=False),
    sa.Column('agent_name', sa.Text(), nullable=False),
    sa.Column('display_name', sa.Text(), nullable=True),
    sa.Column('role', sa.Text(), nullable=True),
    sa.Column('agent_type', sa.Text(), nullable=True),
    sa.Column('description', sa.Text(), nullable=True),
    sa.Column('status', sa.Text(), nullable=True),
    sa.Column('last_heartbeat', sa.DateTime(timezone=True), nullable=True),
    sa.Column('joined_at', sa.DateTime(timezone=True), server_default=sa.text('NOW()'), nullable=True),
    sa.Column('session_id', sa.Text(), nullable=True),
    sa.Column('session_started_at', sa.DateTime(timezone=True), nullable=True),
    sa.ForeignKeyConstraint(['workspace_id'], ['workspaces.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('workspace_id', 'agent_name')
    )
    op.create_table('browser_tabs',
    sa.Column('id', sa.Text(), nullable=False),
    sa.Column('workspace_id', sa.UUID(as_uuid=False), nullable=False),
    sa.Column('url', sa.Text(), nullable=False),
    sa.Column('title', sa.Text(), nullable=True),
    sa.Column('status', sa.Text(), nullable=False),
    sa.Column('created_by', sa.Text(), nullable=False),
    sa.Column('shared_with', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('context_id', sa.Text(), nullable=True),
    sa.Column('session_id', sa.Text(), nullable=True),
    sa.Column('live_url', sa.Text(), nullable=True),
    sa.Column('bf_key_source', sa.Text(), nullable=True),
    sa.Column('bf_key_fingerprint', sa.Text(), nullable=True),
    sa.Column('session_closed', sa.Boolean(), server_default=sa.text('FALSE'), nullable=False),
    sa.Column('close_status', sa.Text(), server_default=sa.text("'none'"), nullable=False),
    sa.Column('close_attempts', sa.Integer(), server_default=sa.text('0'), nullable=False),
    sa.Column('last_close_attempt_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('last_close_error', sa.Text(), nullable=True),
    sa.Column('last_error', sa.Text(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('NOW()'), nullable=True),
    sa.Column('last_active_at', sa.DateTime(timezone=True), server_default=sa.text('NOW()'), nullable=True),
    sa.ForeignKeyConstraint(['context_id'], ['browser_contexts.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['workspace_id'], ['workspaces.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('idx_browser_tabs_workspace_status', 'browser_tabs', ['workspace_id', 'status'], unique=False)
    op.create_table('channel_human_members',
    sa.Column('channel_id', sa.UUID(as_uuid=False), nullable=False),
    sa.Column('user_email', sa.Text(), nullable=False),
    sa.Column('joined_at', sa.DateTime(timezone=True), server_default=sa.text('NOW()'), nullable=True),
    sa.ForeignKeyConstraint(['channel_id'], ['channels.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('channel_id', 'user_email')
    )
    op.create_index('idx_channel_human_members_email', 'channel_human_members', ['user_email'], unique=False)
    op.create_table('channel_members',
    sa.Column('channel_id', sa.UUID(as_uuid=False), nullable=False),
    sa.Column('agent_name', sa.Text(), nullable=False),
    sa.ForeignKeyConstraint(['channel_id'], ['channels.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('channel_id', 'agent_name')
    )
    op.create_table('pai_course_records',
    sa.Column('education_id', sa.Text(), nullable=False),
    sa.Column('name', sa.Text(), nullable=False),
    sa.Column('normalized_name', sa.Text(), nullable=True),
    sa.Column('grade', sa.Text(), nullable=True),
    sa.Column('score', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('credits', sa.Float(), nullable=True),
    sa.Column('details', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('id', sa.Text(), nullable=False),
    sa.Column('workspace_id', sa.UUID(as_uuid=False), nullable=False),
    sa.Column('subject_user_id', sa.Text(), nullable=True),
    sa.Column('source_type', sa.Text(), nullable=False),
    sa.Column('claim_origin', sa.Text(), nullable=False),
    sa.Column('capture_method', sa.Text(), nullable=False),
    sa.Column('verification_status', sa.Text(), server_default=sa.text("'self_reported'"), nullable=False),
    sa.Column('evidence', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('status', sa.Text(), server_default=sa.text("'active'"), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('NOW()'), nullable=True),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('NOW()'), nullable=True),
    sa.ForeignKeyConstraint(['education_id'], ['pai_education_records.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['workspace_id'], ['workspaces.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('idx_pai_course_education', 'pai_course_records', ['education_id'], unique=False)
    op.create_table('pai_document_artifacts',
    sa.Column('id', sa.Text(), nullable=False),
    sa.Column('workspace_id', sa.UUID(as_uuid=False), nullable=False),
    sa.Column('file_id', sa.Text(), nullable=False),
    sa.Column('status', sa.Text(), server_default=sa.text("'queued'"), nullable=False),
    sa.Column('document_type', sa.Text(), nullable=True),
    sa.Column('detected_content_type', sa.Text(), nullable=True),
    sa.Column('content_sha256', sa.Text(), nullable=True),
    sa.Column('parser', sa.Text(), nullable=True),
    sa.Column('parser_version', sa.Text(), nullable=True),
    sa.Column('ocr_used', sa.Boolean(), server_default=sa.text('false'), nullable=False),
    sa.Column('ocr_provider', sa.Text(), nullable=True),
    sa.Column('page_count', sa.Integer(), nullable=True),
    sa.Column('char_count', sa.Integer(), nullable=True),
    sa.Column('content', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('classification', sa.Text(), nullable=True),
    sa.Column('classification_confidence', sa.Float(), nullable=True),
    sa.Column('authority', sa.Text(), nullable=True),
    sa.Column('extractor_version', sa.Text(), nullable=True),
    sa.Column('extraction_summary', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('error_code', sa.Text(), nullable=True),
    sa.Column('error_message', sa.Text(), nullable=True),
    sa.Column('processed_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('NOW()'), nullable=True),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('NOW()'), nullable=True),
    sa.CheckConstraint("status IN ('queued', 'processing', 'ready', 'partial', 'failed', 'unsupported')", name='ck_document_artifact_status'),
    sa.ForeignKeyConstraint(['file_id'], ['files.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['workspace_id'], ['workspaces.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('file_id', name='uq_document_artifact_file')
    )
    op.create_index('idx_document_artifacts_ws_hash', 'pai_document_artifacts', ['workspace_id', 'content_sha256'], unique=False)
    op.create_index('idx_document_artifacts_ws_status', 'pai_document_artifacts', ['workspace_id', 'status'], unique=False)
    op.create_table('pai_profile_issues',
    sa.Column('id', sa.Text(), nullable=False),
    sa.Column('workspace_id', sa.UUID(as_uuid=False), nullable=False),
    sa.Column('subject_user_id', sa.Text(), nullable=True),
    sa.Column('issue_type', sa.Text(), nullable=False),
    sa.Column('severity', sa.Text(), server_default=sa.text("'warning'"), nullable=False),
    sa.Column('affected_type', sa.Text(), nullable=True),
    sa.Column('affected_id', sa.Text(), nullable=True),
    sa.Column('summary', sa.Text(), nullable=False),
    sa.Column('clarification_question', sa.Text(), nullable=True),
    sa.Column('candidate_id', sa.Text(), nullable=True),
    sa.Column('evidence', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('status', sa.Text(), server_default=sa.text("'open'"), nullable=False),
    sa.Column('resolution', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('NOW()'), nullable=True),
    sa.Column('resolved_at', sa.DateTime(timezone=True), nullable=True),
    sa.ForeignKeyConstraint(['candidate_id'], ['pai_memory_candidates.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['workspace_id'], ['workspaces.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('idx_pai_issues_candidate', 'pai_profile_issues', ['candidate_id'], unique=False)
    op.create_index('idx_pai_issues_ws', 'pai_profile_issues', ['workspace_id', 'status'], unique=False)
    op.create_table('pai_student_journey_events',
    sa.Column('id', sa.Text(), nullable=False),
    sa.Column('journey_id', sa.Text(), nullable=False),
    sa.Column('workspace_id', sa.UUID(as_uuid=False), nullable=False),
    sa.Column('event_type', sa.Text(), nullable=False),
    sa.Column('payload', postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'"), nullable=False),
    sa.Column('actor', sa.Text(), server_default=sa.text("'system'"), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('NOW()'), nullable=True),
    sa.ForeignKeyConstraint(['journey_id'], ['pai_student_journeys.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['workspace_id'], ['workspaces.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('idx_student_journey_events_journey', 'pai_student_journey_events', ['journey_id', 'created_at'], unique=False)
    op.create_index('idx_student_journey_events_workspace', 'pai_student_journey_events', ['workspace_id'], unique=False)
    _seed_reference_data()
    # ### end Alembic commands ###


def downgrade() -> None:
    # ### commands auto generated by Alembic - please adjust! ###
    op.drop_index('idx_student_journey_events_workspace', table_name='pai_student_journey_events')
    op.drop_index('idx_student_journey_events_journey', table_name='pai_student_journey_events')
    op.drop_table('pai_student_journey_events')
    op.drop_index('idx_pai_issues_ws', table_name='pai_profile_issues')
    op.drop_index('idx_pai_issues_candidate', table_name='pai_profile_issues')
    op.drop_table('pai_profile_issues')
    op.drop_index('idx_document_artifacts_ws_status', table_name='pai_document_artifacts')
    op.drop_index('idx_document_artifacts_ws_hash', table_name='pai_document_artifacts')
    op.drop_table('pai_document_artifacts')
    op.drop_index('idx_pai_course_education', table_name='pai_course_records')
    op.drop_table('pai_course_records')
    op.drop_table('channel_members')
    op.drop_index('idx_channel_human_members_email', table_name='channel_human_members')
    op.drop_table('channel_human_members')
    op.drop_index('idx_browser_tabs_workspace_status', table_name='browser_tabs')
    op.drop_table('browser_tabs')
    op.drop_table('workspace_members')
    op.drop_index('idx_workflows_workspace', table_name='workflows')
    op.drop_table('workflows')
    op.drop_index('idx_workflow_runs_ws_channel', table_name='workflow_runs')
    op.drop_table('workflow_runs')
    op.drop_index('idx_todos_workspace_created_by', table_name='todos')
    op.drop_index('idx_todos_workspace_channel', table_name='todos')
    op.drop_table('todos')
    op.drop_index('idx_timers_workspace_channel', table_name='timers')
    op.drop_index('idx_timers_fires_at_status', table_name='timers')
    op.drop_table('timers')
    op.drop_index('idx_share_snapshots_workspace', table_name='share_snapshots')
    op.drop_index('idx_share_snapshots_token', table_name='share_snapshots')
    op.drop_table('share_snapshots')
    op.drop_index('idx_routines_workspace_channel', table_name='routines')
    op.drop_index('idx_routines_next_fires_status', table_name='routines')
    op.drop_table('routines')
    op.drop_index('idx_pai_work_ws', table_name='pai_work_experiences')
    op.drop_table('pai_work_experiences')
    op.drop_index('idx_pai_visas_ws', table_name='pai_visa_records')
    op.drop_table('pai_visa_records')
    op.drop_index('uq_vault_facts_ws_key_active', table_name='pai_vault_facts', postgresql_where=sa.text("status = 'active'"), sqlite_where=sa.text("status = 'active'"))
    op.drop_index('idx_vault_facts_ws_status_key', table_name='pai_vault_facts')
    op.drop_index('idx_vault_facts_workspace', table_name='pai_vault_facts')
    op.drop_table('pai_vault_facts')
    op.drop_index('idx_pai_tests_ws', table_name='pai_test_attempts')
    op.drop_table('pai_test_attempts')
    op.drop_index('idx_pai_skills_ws', table_name='pai_student_skills')
    op.drop_table('pai_student_skills')
    op.drop_index('idx_pai_record_revision_ws', table_name='pai_student_record_revisions')
    op.drop_table('pai_student_record_revisions')
    op.drop_index('idx_pai_projects_ws', table_name='pai_student_projects')
    op.drop_table('pai_student_projects')
    op.drop_index('uq_student_journey_primary_active', table_name='pai_student_journeys', postgresql_where=sa.text("is_primary = true AND status = 'active'"), sqlite_where=sa.text("is_primary = 1 AND status = 'active'"))
    op.drop_index('idx_student_journeys_workspace_status', table_name='pai_student_journeys')
    op.drop_index('idx_student_journeys_workspace', table_name='pai_student_journeys')
    op.drop_table('pai_student_journeys')
    op.drop_index('idx_pai_goals_ws', table_name='pai_student_goals')
    op.drop_table('pai_student_goals')
    op.drop_index('idx_pai_documents_ws', table_name='pai_student_documents')
    op.drop_table('pai_student_documents')
    op.drop_index('idx_pai_certifications_ws', table_name='pai_student_certifications')
    op.drop_table('pai_student_certifications')
    op.drop_index('idx_pai_applications_ws', table_name='pai_student_applications')
    op.drop_table('pai_student_applications')
    op.drop_index('idx_pai_scholarships_ws', table_name='pai_scholarship_applications')
    op.drop_table('pai_scholarship_applications')
    op.drop_index('idx_pai_research_ws', table_name='pai_research_records')
    op.drop_table('pai_research_records')
    op.drop_index('idx_memory_candidates_ws_status', table_name='pai_memory_candidates')
    op.drop_index('idx_memory_candidates_workspace', table_name='pai_memory_candidates')
    op.drop_table('pai_memory_candidates')
    op.drop_index('uq_pai_memories_ws_fingerprint_active', table_name='pai_memories', postgresql_where=sa.text("status = 'active' AND fingerprint IS NOT NULL"), sqlite_where=sa.text("status = 'active' AND fingerprint IS NOT NULL"))
    op.drop_index('idx_pai_memories_ws_status_type', table_name='pai_memories')
    op.drop_index('idx_pai_memories_ws_status_fingerprint', table_name='pai_memories')
    op.drop_index('idx_pai_memories_workspace', table_name='pai_memories')
    op.drop_table('pai_memories')
    op.drop_index('idx_pai_languages_ws', table_name='pai_language_proficiencies')
    op.drop_table('pai_language_proficiencies')
    op.drop_index('idx_pai_sponsors_ws', table_name='pai_financial_sponsors')
    op.drop_table('pai_financial_sponsors')
    op.drop_index('uq_pai_episodes_ws_fingerprint_active', table_name='pai_episodes', postgresql_where=sa.text("status = 'active' AND fingerprint IS NOT NULL"), sqlite_where=sa.text("status = 'active' AND fingerprint IS NOT NULL"))
    op.drop_index('idx_pai_episodes_ws_status_time', table_name='pai_episodes')
    op.drop_index('idx_pai_episodes_ws_status_fingerprint', table_name='pai_episodes')
    op.drop_index('idx_pai_episodes_workspace', table_name='pai_episodes')
    op.drop_table('pai_episodes')
    op.drop_index('idx_pai_education_ws', table_name='pai_education_records')
    op.drop_table('pai_education_records')
    op.drop_index('idx_pai_achievements_ws', table_name='pai_achievement_records')
    op.drop_table('pai_achievement_records')
    op.drop_index('idx_notifications_workspace_status', table_name='notifications')
    op.drop_index('idx_notifications_workspace_read', table_name='notifications')
    op.drop_index('idx_notifications_created_at', table_name='notifications')
    op.drop_table('notifications')
    op.drop_table('model_access')
    op.drop_index('idx_knowledge_workspace_status', table_name='knowledge_entries')
    op.drop_table('knowledge_entries')
    op.drop_index('idx_kanban_workspace_status', table_name='kanban_tasks')
    op.drop_index('idx_kanban_workspace_channel', table_name='kanban_tasks')
    op.drop_table('kanban_tasks')
    op.drop_table('invitations')
    op.drop_index('idx_integration_bindings_workspace', table_name='integration_bindings')
    op.drop_index('idx_integration_bindings_team', table_name='integration_bindings')
    op.drop_table('integration_bindings')
    op.drop_index('idx_files_workspace_status', table_name='files')
    op.drop_index('idx_files_trash', table_name='files')
    op.drop_table('files')
    op.drop_index('idx_execution_runs_workspace_task_type', table_name='execution_runs')
    op.drop_index('idx_execution_runs_workspace_status', table_name='execution_runs')
    op.drop_index('idx_execution_runs_workspace', table_name='execution_runs')
    op.drop_table('execution_runs')
    op.drop_index('uq_channels_ws_name', table_name='channels')
    op.drop_index('idx_channels_workspace_status', table_name='channels')
    op.drop_index('idx_channels_status_last_event', table_name='channels')
    op.drop_table('channels')
    op.drop_index('idx_browser_usage_workspace', table_name='browser_usage')
    op.drop_index('idx_browser_usage_started', table_name='browser_usage')
    op.drop_index('idx_browser_usage_opened_by', table_name='browser_usage')
    op.drop_table('browser_usage')
    op.drop_index('idx_browser_contexts_workspace_status', table_name='browser_contexts')
    op.drop_table('browser_contexts')
    op.drop_index('idx_background_jobs_workspace', table_name='background_jobs')
    op.drop_index('idx_background_jobs_claim', table_name='background_jobs')
    op.drop_table('background_jobs')
    op.drop_index('uq_workspace_owner_active', table_name='workspaces', postgresql_where=sa.text("owner_user_id IS NOT NULL AND status = 'active'"), sqlite_where=sa.text("owner_user_id IS NOT NULL AND status = 'active'"))
    op.drop_index('idx_workspace_owner', table_name='workspaces')
    op.drop_table('workspaces')
    op.drop_table('feedback')
    op.drop_index('uq_users_username_lower', table_name='users', postgresql_where=sa.text('username IS NOT NULL'))
    op.drop_table('users')
    op.drop_index('uq_vault_field_key_version', table_name='pai_vault_field_definitions')
    op.drop_index('idx_vault_field_enabled', table_name='pai_vault_field_definitions')
    op.drop_table('pai_vault_field_definitions')
    op.drop_index('uq_profile_requirement_key_version', table_name='pai_profile_requirements')
    op.drop_index('idx_profile_requirements_enabled', table_name='pai_profile_requirements')
    op.drop_table('pai_profile_requirements')
    op.drop_index('idx_events_network_type_target_ts', table_name='events')
    op.drop_index('idx_events_network_type', table_name='events')
    op.drop_index('idx_events_network_timestamp', table_name='events')
    op.drop_index('idx_events_network_target', table_name='events')
    op.drop_table('events')
    op.drop_table('agents')
    # ### end Alembic commands ###
