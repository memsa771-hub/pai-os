# -*- coding: utf-8 -*-
"""Replace Firebase human-auth identity with Supabase; add username.

Revision ID: 051
Revises: 050
Create Date: 2026-09-14

Supabase Auth becomes the sole human-identity provider for web/desktop (Sign
in with Apple, unrelated, is untouched). `users.firebase_uid` is dropped in
favor of `users.supabase_uid`; `users.username` is a new, unique,
case-insensitively-normalized authentication handle (not a profile field —
see app/models.py) used to resolve "sign in with username" to an email
server-side without exposing it to the client.
"""

from alembic import op
import sqlalchemy as sa


revision = "051"
down_revision = "050"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("supabase_uid", sa.Text(), nullable=True))
    op.add_column("users", sa.Column("username", sa.Text(), nullable=True))
    op.drop_column("users", "firebase_uid")
    op.create_index(
        "uq_users_username_lower",
        "users",
        [sa.text("lower(username)")],
        unique=True,
        postgresql_where=sa.text("username IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_users_username_lower", table_name="users")
    op.drop_column("users", "username")
    op.drop_column("users", "supabase_uid")
    op.add_column("users", sa.Column("firebase_uid", sa.Text(), nullable=True))
