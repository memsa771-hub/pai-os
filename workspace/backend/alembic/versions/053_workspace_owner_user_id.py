# -*- coding: utf-8 -*-
"""Add workspaces.owner_user_id — one student, one canonical personal workspace.

Revision ID: 053
Revises: 052

Placement AI's student-facing product moves from "a user has N workspaces,
picked via membership rows" to "a user owns exactly one active workspace,
identified directly by workspaces.owner_user_id". This migration only adds
the column and backfills it — it does NOT drop workspace_memberships,
workspace_collaborators or invitations (those stay, both as the machine/
legacy-compat path and because their human-facing product surface is removed
in application code, not schema, for this change). A follow-up migration can
drop them once nothing reads them.

Backfill, for every existing user with a determinable owned workspace:
  1. Candidates = workspaces where the user has an 'owner' WorkspaceMembership
     row, UNION workspaces whose creator_email matches the user's email
     (the pre-membership legacy path) — active workspaces only.
  2. Pick exactly one per user, deterministically: most recently active
     (last_activity_at DESC), tie-broken by created_at ASC then id ASC.
  3. Set owner_user_id on that one workspace. Every other candidate is left
     alone — NOT deleted, NOT merged, NOT re-owned. It simply has no
     owner_user_id, so the new owner-based access path can no longer reach it
     through normal product use (its data — channels/events/files/browser
     state — is untouched and still exists in the database).

Users who end up with more than one legacy-owned workspace are logged during
upgrade() so they can be reviewed/consolidated manually later — see the
"multiple legacy workspaces" NOTICE lines in the migration output.
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "053"
down_revision = "052"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("workspaces", sa.Column("owner_user_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.create_foreign_key(
        "fk_workspaces_owner_user_id", "workspaces", "users",
        ["owner_user_id"], ["id"], ondelete="SET NULL",
    )
    op.create_index("idx_workspace_owner", "workspaces", ["owner_user_id"])
    op.create_index(
        "uq_workspace_owner_active", "workspaces", ["owner_user_id"],
        unique=True,
        postgresql_where=sa.text("owner_user_id IS NOT NULL AND status = 'active'"),
        sqlite_where=sa.text("owner_user_id IS NOT NULL AND status = 'active'"),
    )

    bind = op.get_bind()

    # --- Backfill: pick one canonical owned workspace per user ---------------
    bind.execute(sa.text("""
        WITH candidate_owned AS (
            SELECT wm.user_id AS user_id, w.id AS workspace_id,
                   w.last_activity_at AS last_activity_at, w.created_at AS created_at
            FROM workspace_memberships wm
            JOIN workspaces w ON w.id = wm.workspace_id
            WHERE wm.role = 'owner' AND w.status = 'active'
            UNION
            SELECT u.id AS user_id, w.id AS workspace_id,
                   w.last_activity_at AS last_activity_at, w.created_at AS created_at
            FROM workspaces w
            JOIN users u ON lower(u.email) = lower(w.creator_email)
            WHERE w.status = 'active' AND w.creator_email IS NOT NULL
        ),
        ranked AS (
            SELECT user_id, workspace_id,
                   ROW_NUMBER() OVER (
                       PARTITION BY user_id
                       ORDER BY last_activity_at DESC NULLS LAST, created_at ASC, workspace_id ASC
                   ) AS rn
            FROM candidate_owned
        )
        UPDATE workspaces
        SET owner_user_id = ranked.user_id
        FROM ranked
        WHERE workspaces.id = ranked.workspace_id AND ranked.rn = 1
    """))

    # --- Report users who had more than one legacy-owned workspace ----------
    extras = bind.execute(sa.text("""
        SELECT wm.user_id, u.email, COUNT(DISTINCT w.id) AS workspace_count
        FROM workspace_memberships wm
        JOIN workspaces w ON w.id = wm.workspace_id AND w.status = 'active'
        JOIN users u ON u.id = wm.user_id
        WHERE wm.role = 'owner'
        GROUP BY wm.user_id, u.email
        HAVING COUNT(DISTINCT w.id) > 1
    """)).fetchall()
    for user_id, email, count in extras:
        print(f"NOTICE: user {email} ({user_id}) had {count} legacy owned workspaces; "
              f"one was picked as canonical, the rest are kept but no longer owner-linked.")


def downgrade() -> None:
    op.drop_index("uq_workspace_owner_active", table_name="workspaces")
    op.drop_index("idx_workspace_owner", table_name="workspaces")
    op.drop_constraint("fk_workspaces_owner_user_id", "workspaces", type_="foreignkey")
    op.drop_column("workspaces", "owner_user_id")
