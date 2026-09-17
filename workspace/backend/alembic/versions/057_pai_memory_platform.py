# -*- coding: utf-8 -*-
"""PAI Memory Platform — Vault, semantic memory, episodes, candidates, jobs.

Revision ID: 057
Revises: 056

Creates the foundation for PAI's memory (see app/memory/):

  pai_vault_field_definitions  the Vault's schema, stored as data so adding a
                               field is an INSERT rather than a code change
  pai_vault_facts              canonical structured student state, versioned
                               with provenance rather than overwritten
  pai_memories                 semantic memory (preferences, goals)
  pai_episodes                 episodic memory (what happened, when)
  pai_memory_candidates        LLM proposals awaiting deterministic
                               reconciliation — the quarantine that keeps a
                               model from writing canonical state
  background_jobs              durable, resumable work queue (deliberately
                               generic; memory is its first consumer)

Also seeds an initial set of Vault field definitions. They are seed *data*,
not hardcoded fields — no code branches on any of these keys, and deleting
them leaves an empty Vault rather than a broken system.

Create Date: 2026-09-16
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "057"
down_revision = "056"
branch_labels = None
depends_on = None


def _has_table(inspector, name: str) -> bool:
    return name in inspector.get_table_names()


# Seeded Vault fields. Chosen to exercise each validator shape (bounded
# number, integer, enum string, object with required keys, array), not to
# model the domain — Phase 2 adds real fields as data.
SEED_FIELDS = [
    {
        "key": "education.cgpa", "category": "education", "data_type": "number",
        "validation_schema": {"type": "number", "minimum": 0, "maximum": 10},
        "cardinality": "single", "conflict_policy": "latest_wins",
        "sensitivity": "normal", "searchable": False,
        "description": "Cumulative grade point average on the student's stated scale.",
    },
    {
        "key": "education.backlogs", "category": "education", "data_type": "integer",
        "validation_schema": {"type": "integer", "minimum": 0, "maximum": 100},
        "cardinality": "single", "conflict_policy": "latest_wins",
        "sensitivity": "normal", "searchable": False,
        "description": "Number of outstanding failed subjects.",
    },
    {
        "key": "tests.ielts.score", "category": "tests", "data_type": "number",
        "validation_schema": {"type": "number", "minimum": 0, "maximum": 9},
        "cardinality": "single", "conflict_policy": "highest_confidence",
        "sensitivity": "normal", "searchable": False,
        "description": "Overall IELTS band score.",
    },
    {
        "key": "finance.budget", "category": "finance", "data_type": "object",
        "validation_schema": {
            "type": "object",
            "properties": {
                "amount": {"type": "number", "minimum": 0},
                "currency": {"type": "string"},
                "period": {"type": "string", "enum": ["total", "per_year"]},
            },
            "required": ["amount", "currency"],
            "additionalProperties": False,
        },
        "cardinality": "single", "conflict_policy": "latest_wins",
        "sensitivity": "sensitive", "searchable": False,
        "description": "Budget the student can fund, with currency and period.",
    },
    {
        "key": "preferences.countries", "category": "preferences", "data_type": "array",
        "validation_schema": {"type": "array", "items": {"type": "string"}},
        "cardinality": "multi", "conflict_policy": "latest_wins",
        "sensitivity": "normal", "searchable": True,
        "description": "Preferred destination countries, most preferred first.",
    },
    {
        "key": "preferences.study_mode", "category": "preferences", "data_type": "string",
        "validation_schema": {"type": "string", "enum": ["on_campus", "online", "hybrid"]},
        "cardinality": "single", "conflict_policy": "latest_wins",
        "sensitivity": "normal", "searchable": False,
        "description": "How the student wants to study.",
    },
]


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if not _has_table(inspector, "pai_vault_field_definitions"):
        op.create_table(
            "pai_vault_field_definitions",
            sa.Column("id", sa.Text(), primary_key=True),
            sa.Column("key", sa.Text(), nullable=False),
            sa.Column("category", sa.Text(), nullable=False),
            sa.Column("data_type", sa.Text(), nullable=False),
            sa.Column("validation_schema", JSONB(), nullable=True),
            sa.Column("cardinality", sa.Text(), nullable=False, server_default="single"),
            sa.Column("conflict_policy", sa.Text(), nullable=False, server_default="latest_wins"),
            sa.Column("sensitivity", sa.Text(), nullable=False, server_default="normal"),
            sa.Column("searchable", sa.Boolean(), nullable=False, server_default=sa.text("false")),
            sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
            sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
            sa.Column("description", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()")),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()")),
        )
        op.create_index(
            "uq_vault_field_key_version", "pai_vault_field_definitions",
            ["key", "version"], unique=True,
        )
        op.create_index("idx_vault_field_enabled", "pai_vault_field_definitions", ["enabled"])

    if not _has_table(inspector, "pai_vault_facts"):
        op.create_table(
            "pai_vault_facts",
            sa.Column("id", sa.Text(), primary_key=True),
            sa.Column("workspace_id", UUID(as_uuid=False), sa.ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False),
            sa.Column("subject_user_id", sa.Text(), nullable=True),
            sa.Column("field_key", sa.Text(), nullable=False),
            sa.Column("field_version", sa.Integer(), nullable=True),
            sa.Column("value", JSONB(), nullable=False),
            sa.Column("confidence", sa.Float(), nullable=False, server_default="1.0"),
            sa.Column("source_type", sa.Text(), nullable=False),
            sa.Column("source_event_id", sa.Text(), nullable=True),
            sa.Column("evidence", JSONB(), nullable=True),
            sa.Column("valid_from", sa.DateTime(timezone=True), server_default=sa.text("NOW()")),
            sa.Column("valid_until", sa.DateTime(timezone=True), nullable=True),
            sa.Column("status", sa.Text(), nullable=False, server_default="active"),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()")),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()")),
        )
        op.create_index("idx_vault_facts_workspace", "pai_vault_facts", ["workspace_id"])
        op.create_index(
            "idx_vault_facts_ws_status_key", "pai_vault_facts",
            ["workspace_id", "status", "field_key"],
        )

    if not _has_table(inspector, "pai_memories"):
        op.create_table(
            "pai_memories",
            sa.Column("id", sa.Text(), primary_key=True),
            sa.Column("workspace_id", UUID(as_uuid=False), sa.ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False),
            sa.Column("subject_user_id", sa.Text(), nullable=True),
            sa.Column("memory_type", sa.Text(), nullable=False),
            sa.Column("content", sa.Text(), nullable=False),
            sa.Column("entities", JSONB(), nullable=True),
            sa.Column("importance", sa.Float(), nullable=False, server_default="0.5"),
            sa.Column("confidence", sa.Float(), nullable=False, server_default="1.0"),
            sa.Column("source_type", sa.Text(), nullable=True),
            sa.Column("source_event_ids", JSONB(), nullable=True),
            sa.Column("metadata", JSONB(), nullable=True),
            sa.Column("valid_from", sa.DateTime(timezone=True), server_default=sa.text("NOW()")),
            sa.Column("valid_until", sa.DateTime(timezone=True), nullable=True),
            sa.Column("status", sa.Text(), nullable=False, server_default="active"),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()")),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()")),
        )
        op.create_index("idx_pai_memories_workspace", "pai_memories", ["workspace_id"])
        op.create_index(
            "idx_pai_memories_ws_status_type", "pai_memories",
            ["workspace_id", "status", "memory_type"],
        )

    if not _has_table(inspector, "pai_episodes"):
        op.create_table(
            "pai_episodes",
            sa.Column("id", sa.Text(), primary_key=True),
            sa.Column("workspace_id", UUID(as_uuid=False), sa.ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False),
            sa.Column("subject_user_id", sa.Text(), nullable=True),
            sa.Column("event_type", sa.Text(), nullable=False),
            sa.Column("summary", sa.Text(), nullable=False),
            sa.Column("entities", JSONB(), nullable=True),
            sa.Column("importance", sa.Float(), nullable=False, server_default="0.5"),
            sa.Column("occurred_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()")),
            sa.Column("source_event_ids", JSONB(), nullable=True),
            sa.Column("metadata", JSONB(), nullable=True),
            sa.Column("status", sa.Text(), nullable=False, server_default="active"),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()")),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()")),
        )
        op.create_index("idx_pai_episodes_workspace", "pai_episodes", ["workspace_id"])
        op.create_index(
            "idx_pai_episodes_ws_status_time", "pai_episodes",
            ["workspace_id", "status", "occurred_at"],
        )

    if not _has_table(inspector, "pai_memory_candidates"):
        op.create_table(
            "pai_memory_candidates",
            sa.Column("id", sa.Text(), primary_key=True),
            sa.Column("workspace_id", UUID(as_uuid=False), sa.ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False),
            sa.Column("subject_user_id", sa.Text(), nullable=True),
            sa.Column("candidate_type", sa.Text(), nullable=False),
            sa.Column("operation", sa.Text(), nullable=False),
            sa.Column("key", sa.Text(), nullable=True),
            sa.Column("proposed_value", JSONB(), nullable=True),
            sa.Column("content", sa.Text(), nullable=True),
            sa.Column("entities", JSONB(), nullable=True),
            sa.Column("confidence", sa.Float(), nullable=False, server_default="0.5"),
            sa.Column("source_type", sa.Text(), nullable=False, server_default="conversation"),
            sa.Column("source_event_ids", JSONB(), nullable=True),
            sa.Column("evidence", JSONB(), nullable=True),
            sa.Column("status", sa.Text(), nullable=False, server_default="pending"),
            sa.Column("rejection_reason", sa.Text(), nullable=True),
            sa.Column("reconciled_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("result_id", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()")),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()")),
        )
        op.create_index("idx_memory_candidates_workspace", "pai_memory_candidates", ["workspace_id"])
        op.create_index(
            "idx_memory_candidates_ws_status", "pai_memory_candidates",
            ["workspace_id", "status"],
        )

    if not _has_table(inspector, "background_jobs"):
        op.create_table(
            "background_jobs",
            sa.Column("id", sa.Text(), primary_key=True),
            sa.Column("workspace_id", UUID(as_uuid=False), sa.ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=True),
            sa.Column("job_type", sa.Text(), nullable=False),
            sa.Column("payload", JSONB(), nullable=True),
            sa.Column("status", sa.Text(), nullable=False, server_default="pending"),
            sa.Column("priority", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="5"),
            sa.Column("available_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()")),
            sa.Column("locked_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("locked_by", sa.Text(), nullable=True),
            sa.Column("last_error", sa.Text(), nullable=True),
            sa.Column("idempotency_key", sa.Text(), nullable=True),
            sa.Column("result", JSONB(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()")),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()")),
            sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
            sa.UniqueConstraint("idempotency_key", name="uq_background_jobs_idempotency"),
        )
        op.create_index(
            "idx_background_jobs_claim", "background_jobs",
            ["status", "available_at", "priority"],
        )
        op.create_index("idx_background_jobs_workspace", "background_jobs", ["workspace_id"])

    _seed_field_definitions()


def _seed_field_definitions() -> None:
    """Insert the starter field definitions, skipping any that exist.

    Idempotent so re-running against a partially seeded database is safe.
    """
    import json
    import uuid

    bind = op.get_bind()
    existing = {
        row[0] for row in bind.execute(
            sa.text("SELECT key FROM pai_vault_field_definitions")
        )
    }

    for spec in SEED_FIELDS:
        if spec["key"] in existing:
            continue
        bind.execute(
            sa.text(
                "INSERT INTO pai_vault_field_definitions "
                "(id, key, category, data_type, validation_schema, cardinality, "
                " conflict_policy, sensitivity, searchable, enabled, version, description) "
                "VALUES (:id, :key, :category, :data_type, :validation_schema, :cardinality, "
                " :conflict_policy, :sensitivity, :searchable, :enabled, :version, :description)"
            ).bindparams(
                sa.bindparam("validation_schema", type_=sa.Text()),
            ),
            {
                "id": str(uuid.uuid4()),
                "key": spec["key"],
                "category": spec["category"],
                "data_type": spec["data_type"],
                "validation_schema": json.dumps(spec["validation_schema"]),
                "cardinality": spec["cardinality"],
                "conflict_policy": spec["conflict_policy"],
                "sensitivity": spec["sensitivity"],
                "searchable": spec["searchable"],
                "enabled": True,
                "version": 1,
                "description": spec["description"],
            },
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    for table, indexes in (
        ("background_jobs", ["idx_background_jobs_workspace", "idx_background_jobs_claim"]),
        ("pai_memory_candidates", ["idx_memory_candidates_ws_status", "idx_memory_candidates_workspace"]),
        ("pai_episodes", ["idx_pai_episodes_ws_status_time", "idx_pai_episodes_workspace"]),
        ("pai_memories", ["idx_pai_memories_ws_status_type", "idx_pai_memories_workspace"]),
        ("pai_vault_facts", ["idx_vault_facts_ws_status_key", "idx_vault_facts_workspace"]),
        ("pai_vault_field_definitions", ["idx_vault_field_enabled", "uq_vault_field_key_version"]),
    ):
        if _has_table(inspector, table):
            for index in indexes:
                op.drop_index(index, table_name=table)
            op.drop_table(table)
