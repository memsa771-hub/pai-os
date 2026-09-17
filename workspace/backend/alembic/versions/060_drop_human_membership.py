# -*- coding: utf-8 -*-
"""Drop the multi-human workspace authorization tables.

Revision ID: 060
Revises: 059

Placement AI is one student, one personal workspace. A workspace never has a
second human, so the tables that modelled a roster of them have no rows worth
keeping and no code left reading them:

  workspace_memberships    user -> workspace with owner/admin/member/viewer
  workspace_collaborators  email-keyed ACL that predated accounts

Human authorization is now exactly `user.id == workspace.owner_user_id`, and
`workspaces.owner_user_id` plus `uq_workspace_owner_active` (migration 053)
carry the whole model. `workspace_members` is NOT touched — that table is
agents, not humans.

Ordering matters: every code reference was removed before this migration, so
an upgrade cannot leave the app querying a table that is already gone. A
downgrade recreates both tables empty; the rows are not recoverable, which is
why the data is read out into owner_user_id by 053 first rather than here.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "060"
down_revision = "059"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Indexes and FK constraints go with the table; dropping the table is
    # enough on both PostgreSQL and SQLite.
    op.drop_table("workspace_memberships")
    op.drop_table("workspace_collaborators")


def downgrade() -> None:
    op.create_table(
        "workspace_collaborators",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False),
        sa.Column("email", sa.Text(), nullable=False),
        sa.Column("role", sa.Text(), server_default="editor"),
        sa.Column("added_by", sa.Text(), nullable=True),
        sa.Column("added_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()")),
        sa.Column("display_name", sa.Text(), nullable=True),
        sa.UniqueConstraint("workspace_id", "email", name="uq_collaborator_workspace_email"),
    )
    op.create_index("idx_collaborators_workspace", "workspace_collaborators", ["workspace_id"])
    op.create_index("idx_collaborators_email", "workspace_collaborators", ["email"])

    op.create_table(
        "workspace_memberships",
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("role", sa.Text(), nullable=False, server_default=sa.text("'member'")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()")),
        sa.PrimaryKeyConstraint("workspace_id", "user_id"),
    )
    op.create_index("idx_memberships_user", "workspace_memberships", ["user_id"])
