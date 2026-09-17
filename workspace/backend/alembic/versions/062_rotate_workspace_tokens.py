# -*- coding: utf-8 -*-
"""Rotate every workspace machine token — the browser used to hold it.

Revision ID: 062
Revises: 061

`GET /v1/account/workspace` returned `workspaces.password_hash` — the
workspace MACHINE token, the credential PAI Counselor and PAI Operator WRITE
with — to the browser, which stored it in `oa_workspace`: a JS-readable,
30-day, `domain=.openagents.org` cookie, and put it in the query string of
every SSE connection and every `<img src>` file URL.

That token has therefore been readable by any script on any `*.openagents.org`
origin, and has been written into browser history, referrer headers, proxy
access logs and CDN logs, for the life of every workspace created so far. The
code path is closed (app/stream_ticket.py replaces it with a read-only ticket
that expires in minutes), but closing it does not un-leak the tokens already
handed out. Every one of them must be assumed compromised.

So: mint a fresh token for every workspace.

WHAT THIS BREAKS, deliberately. Any agent still holding an old token gets 401
and has to re-join with the new one. That is the point of a rotation — an
old credential that keeps working has not been rotated. Web and desktop
clients are unaffected: they authenticate with the user's bearer, and never
saw a machine token to go stale.

Irreversible by design: downgrade cannot restore secrets it does not have,
and restoring a leaked credential would be the bug, not the fix.
"""

import secrets

import sqlalchemy as sa
from alembic import op

revision = "062"
down_revision = "061"
branch_labels = None
depends_on = None


def upgrade():
    conn = op.get_bind()
    rows = conn.execute(
        sa.text("SELECT id FROM workspaces WHERE password_hash IS NOT NULL")
    ).fetchall()

    for (workspace_id,) in rows:
        conn.execute(
            sa.text("UPDATE workspaces SET password_hash = :tok WHERE id = :id"),
            {"tok": secrets.token_urlsafe(32), "id": workspace_id},
        )

    print(f"062: rotated {len(rows)} workspace token(s); agents must re-join")


def downgrade():
    # The previous tokens are not recorded anywhere — that is intentional.
    # Rolling this back would mean reinstating credentials that leaked into
    # browser storage and proxy logs.
    pass
