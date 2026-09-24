"""Make onboarding identity available to PAI Counselor context.

Revision ID: 069
Revises: 068

Nationality and gender remain sensitive Vault facts. Adding the counseling
context tag lets the existing sensitivity-aware context builder pass them only
to the authorized Counselor, instead of creating a second onboarding context
or weakening restricted-field handling. Date of birth stays restricted and is
available only through explicit, purpose-bound Vault access.
"""

import sqlalchemy as sa
from alembic import op

revision = "069"
down_revision = "068"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(sa.text("""
        UPDATE pai_vault_field_definitions
        SET context_tags = CASE
            WHEN context_tags ? 'counseling' THEN context_tags
            ELSE COALESCE(context_tags, '[]'::jsonb) || '["counseling"]'::jsonb
        END
        WHERE key IN ('identity.nationality', 'identity.gender')
    """))


def downgrade():
    op.execute(sa.text("""
        UPDATE pai_vault_field_definitions
        SET context_tags = COALESCE(
            (SELECT jsonb_agg(value)
             FROM jsonb_array_elements(context_tags) value
             WHERE value <> '"counseling"'::jsonb),
            '[]'::jsonb
        )
        WHERE key IN ('identity.nationality', 'identity.gender')
    """))
