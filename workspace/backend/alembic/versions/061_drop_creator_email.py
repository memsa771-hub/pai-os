# -*- coding: utf-8 -*-
"""Drop workspaces.creator_email — the last of the claim-era identity surface.

Revision ID: 061
Revises: 060

`creator_email` was how a workspace remembered which human made it, back when
one could be created anonymously and claimed by email afterwards. Ownership is
`owner_user_id` now, set at creation time and never NULL for a student
workspace, so the column was write-only: set on provisioning, never read for a
decision.

It was not merely redundant. `GET /v1/workspaces?creator_email=` filtered on it
with no authentication at all, and the web client derived "is this mine?" from
`creatorEmail === user.email`. Both are gone; keeping the column would leave the
same mistake available to the next caller.

The owner's address is still reachable — `workspaces.owner_user_id` -> `users.email`
— which is where it should have come from all along.
"""

import sqlalchemy as sa
from alembic import op

revision = "061"
down_revision = "060"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_column("workspaces", "creator_email")


def downgrade() -> None:
    # Recreated empty. The values are not restorable, and they were only ever a
    # denormalized copy of the owner's email: backfill from users if you need it.
    op.add_column("workspaces", sa.Column("creator_email", sa.Text(), nullable=True))
