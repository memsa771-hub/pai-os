"""First-run student onboarding: completion stamp and the two fields it adds.

Revision ID: 066
Revises: 065

`identity.status_category` exists SEPARATELY from `identity.current_status`
on purpose. Onboarding asks a coarse question ("student / professional /
other") and answers it as `user_explicit`, and under `latest_wins` a
`user_explicit` fact can never be superseded by conversation extraction (see
VaultService._should_supersede). Writing the dropdown answer into
`current_status` would therefore freeze it at "Student" forever and stop PAI
ever refining it to "Final-year BS Computer Science student". The category is
a stable classification; the status is a living headline. They are different
facts and get different keys.
"""
import json

import sqlalchemy as sa
from alembic import op

revision = "066"
down_revision = "065"
branch_labels = None
depends_on = None

# key, category, data_type, sensitivity, required_for, priority, validation_schema
NEW_FIELDS = (
    (
        "identity.gender", "identity", "string", "sensitive",
        ["scholarship"], 50,
        {"type": "string", "enum": ["male", "female", "other", "undisclosed"]},
    ),
    (
        "identity.status_category", "identity", "string", "normal",
        ["discovery"], 95,
        {"type": "string", "enum": ["student", "professional", "other"]},
    ),
)


def upgrade():
    op.add_column("users", sa.Column("onboarded_at", sa.DateTime(timezone=True), nullable=True))

    connection = op.get_bind()
    for key, category, data_type, sensitivity, required_for, priority, schema in NEW_FIELDS:
        connection.execute(sa.text("""
            INSERT INTO pai_vault_field_definitions
              (id, key, category, data_type, validation_schema, cardinality,
               conflict_policy, sensitivity, searchable, enabled, version,
               description, context_tags, required_for, profile_priority)
            SELECT :id, :key, :category, :data_type, CAST(:schema AS jsonb),
                   'single', :policy, :sensitivity, false, true, 1,
                   :description, CAST(:tags AS jsonb), CAST(:required_for AS jsonb), :priority
            WHERE NOT EXISTS (SELECT 1 FROM pai_vault_field_definitions WHERE key = :key)
        """), {
            "id": f"student-{key}", "key": key, "category": category,
            "data_type": data_type, "schema": json.dumps(schema),
            # Both are stated first-hand at onboarding, so neither needs the
            # review gate that guards identifiers like a passport number.
            "policy": "latest_wins",
            "sensitivity": sensitivity,
            "description": key.replace(".", " ").replace("_", " "),
            "tags": json.dumps(required_for), "required_for": json.dumps(required_for),
            "priority": priority,
        })

    # Anyone already using the product has effectively onboarded: their facts
    # came from conversation instead. Backfilling stops the new gate from
    # interrupting an existing student mid-journey.
    connection.execute(sa.text("""
        UPDATE users SET onboarded_at = NOW()
        WHERE onboarded_at IS NULL
          AND EXISTS (
            SELECT 1 FROM workspaces w
            WHERE w.owner_user_id = users.id AND w.status = 'active'
          )
    """))


def downgrade():
    op.execute(
        "DELETE FROM pai_vault_field_definitions "
        "WHERE key IN ('identity.gender', 'identity.status_category')"
    )
    op.drop_column("users", "onboarded_at")
