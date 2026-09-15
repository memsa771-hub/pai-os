#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Backfill the built-in PAI Counselor assistant into existing (active) workspaces.

New workspaces get PAI Counselor automatically at creation (see
routers/workspaces.py). This one-off script adds PAI Counselor to workspaces that
already existed before the feature shipped.

Idempotent: skips workspaces that already have a live PAI Counselor. Safe to re-run.
Requires the same env as the backend (DATABASE_URL, PAI_API_KEY, ...). PAI Counselor is
only added when config.should_provision() is True (i.e. enabled + key set).

Usage (from workspace/backend/):
    python scripts/backfill_pai.py            # apply
    python scripts/backfill_pai.py --dry-run  # report only
"""

import os
import sys

# Make `app` importable when run as `python scripts/backfill_pai.py`.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import select  # noqa: E402

from app.database import SessionLocal  # noqa: E402
from app.models import Workspace  # noqa: E402
from app.services.pai import ensure_primary_conversation, provision_pai, should_provision  # noqa: E402


def main() -> int:
    dry_run = "--dry-run" in sys.argv

    if not should_provision():
        print(
            "PAI Counselor provisioning is disabled or no PAI_API_KEY is configured — "
            "nothing to do. Set PAI_ENABLED=true and PAI_API_KEY."
        )
        return 1

    db = SessionLocal()
    added = 0
    skipped = 0
    try:
        workspaces = db.execute(
            select(Workspace).where(Workspace.status != "deleted")
        ).scalars().all()
        print(f"Found {len(workspaces)} active workspace(s).")

        for ws in workspaces:
            if dry_run:
                # provision_pai mutates + returns whether it *would* add; roll
                # back so a dry run changes nothing.
                would = provision_pai(db, ws)
                conversation = ensure_primary_conversation(db, ws)
                db.rollback()
                changed = would or conversation
                print(f"  [{ws.slug}] {'would update' if changed else 'already ready'} PAI Counselor")
                added += 1 if changed else 0
                skipped += 0 if changed else 1
                continue

            provisioned = provision_pai(db, ws)
            conversation = ensure_primary_conversation(db, ws)
            if provisioned or conversation:
                db.commit()
                added += 1
                print(f"  [{ws.slug}] added PAI Counselor")
            else:
                skipped += 1
                print(f"  [{ws.slug}] already has PAI Counselor — skipped")
    finally:
        db.close()

    verb = "would add" if dry_run else "added"
    print(f"\nDone. {verb} PAI Counselor to {added} workspace(s), {skipped} already had it.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
