# -*- coding: utf-8 -*-
"""Drop workspaces.creator_email — the last of the claim-era identity surface.

Revision ID: 061
Revises: 060

`creator_email` was how a workspace remembered which human made it, back when
one could be created anonymously and claimed by email afterwards. Ownership is
`owner_user_id` now, set at creation time, so the column is no longer read for
any decision. It was not merely redundant: `GET /v1/workspaces?creator_email=`
filtered on it with no authentication, and the web client derived "is this
mine?" from `creatorEmail === user.email`.

DATA SAFETY. Dropping it outright would be silent data loss. Migration 053
backfilled `owner_user_id` for ONE workspace per user (`ROW_NUMBER() ... rn = 1`)
and deliberately left every other candidate ownerless while keeping its
`creator_email` — so rows matching

    status = 'active' AND owner_user_id IS NULL AND creator_email IS NOT NULL

demonstrably exist, and for them `creator_email` is the only remaining link to
a human. This migration therefore:

  1. BACKFILLS the unambiguous case — an active, ownerless workspace whose
     `creator_email` matches a `users` row, where that user owns no other
     active workspace. The `uq_workspace_owner_active` partial unique index is
     why the second condition is required: a user may hold exactly one active
     owned workspace, so a blanket backfill would either fail the index or
     silently pick a winner. Anything ambiguous is left for a human.

  2. FAILS LOUDLY if any active ownerless row with a `creator_email` remains,
     naming each workspace id and address. Those are the rows 053 orphaned on
     purpose (a user's 2nd..Nth legacy workspace) or ones whose creator never
     registered. The operator decides — reassign, archive or delete — and
     re-runs. Nothing is destroyed on our initiative.

Rows with `status != 'active'` are tombstones and do lose the field. That is a
stated consequence, not an accident: they are already unreachable, and keeping
a soft-deleted row's creator address is not worth blocking the migration for.

The owner's address remains reachable for every surviving workspace via
`workspaces.owner_user_id` -> `users.email`, which is where it should have come
from all along.
"""

import sqlalchemy as sa
from alembic import op

revision = "061"
down_revision = "060"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()

    # 1. Recover what can be recovered, without violating the one-active-
    #    workspace-per-user index.
    backfilled = bind.execute(sa.text("""
        UPDATE workspaces
        SET owner_user_id = (
            SELECT u.id FROM users u
            WHERE lower(u.email) = lower(workspaces.creator_email)
        )
        WHERE workspaces.status = 'active'
          AND workspaces.owner_user_id IS NULL
          AND workspaces.creator_email IS NOT NULL
          AND EXISTS (
              SELECT 1 FROM users u
              WHERE lower(u.email) = lower(workspaces.creator_email)
          )
          AND NOT EXISTS (
              SELECT 1 FROM workspaces other
              WHERE other.status = 'active'
                AND other.owner_user_id = (
                    SELECT u.id FROM users u
                    WHERE lower(u.email) = lower(workspaces.creator_email)
                )
          )
    """)).rowcount
    if backfilled:
        print(f"061: backfilled owner_user_id for {backfilled} legacy workspace(s) "
              f"from creator_email")

    # 2. Refuse to destroy what is left.
    orphans = bind.execute(sa.text("""
        SELECT id, creator_email
        FROM workspaces
        WHERE status = 'active'
          AND owner_user_id IS NULL
          AND creator_email IS NOT NULL
        ORDER BY creator_email, id
    """)).fetchall()

    if orphans:
        listing = "\n".join(f"    {ws_id}  creator_email={email}" for ws_id, email in orphans)
        raise RuntimeError(
            f"Migration 061 refuses to drop workspaces.creator_email: "
            f"{len(orphans)} active workspace(s) have no owner_user_id, and "
            f"creator_email is the only record of who made them. Dropping the "
            f"column would lose that permanently.\n\n"
            f"{listing}\n\n"
            f"These are typically a user's 2nd..Nth legacy workspace (migration "
            f"053 kept only one per user as canonical) or a workspace whose "
            f"creator never registered an account. Resolve each one, then re-run:\n"
            f"  - assign it:  UPDATE workspaces SET owner_user_id = '<user-id>' "
            f"WHERE id = '<workspace-id>';  (the user must not already own an "
            f"active workspace)\n"
            f"  - retire it:  UPDATE workspaces SET status = 'deleted' WHERE id = "
            f"'<workspace-id>';\n"
            f"  - or clear the field deliberately: UPDATE workspaces SET "
            f"creator_email = NULL WHERE id = '<workspace-id>';"
        )

    op.drop_column("workspaces", "creator_email")


def downgrade() -> None:
    # Recreated empty. The values are not restorable — by the time upgrade()
    # dropped the column, every active workspace either had an owner or the
    # migration had already refused to run. Backfill from users if you need it:
    #   UPDATE workspaces SET creator_email = u.email
    #   FROM users u WHERE u.id = workspaces.owner_user_id;
    op.add_column("workspaces", sa.Column("creator_email", sa.Text(), nullable=True))
