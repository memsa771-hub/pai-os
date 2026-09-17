# -*- coding: utf-8 -*-
"""Database-level uniqueness for exact memory fingerprints.

Revision ID: 059
Revises: 058

Migration 058 made dedupe an O(1) lookup, but the shape was still
check-then-insert:

    SELECT ... WHERE fingerprint = ?      -- neither sees the other
    INSERT ...

Two workers extracting from concurrent turns could both find nothing and both
insert, producing identical active memories. The application check is a fast
path; this index is the actual invariant.

PARTIAL on `status='active' AND fingerprint IS NOT NULL`:
  - superseded/forgotten rows must stay for audit, and several forgotten rows
    may legitimately share a fingerprint
  - a NULL fingerprint (backfill could not compute one) must not collide

The fingerprint already folds in memory_type/event_type, so the index does not
repeat them.

Duplicates are collapsed before the index is created, keeping the newest row
active — an existing duplicate pair would otherwise fail the migration.

Create Date: 2026-09-17
"""

import sqlalchemy as sa
from alembic import op

revision = "059"
down_revision = "058"
branch_labels = None
depends_on = None

_SPECS = (
    ("pai_memories", "uq_pai_memories_ws_fingerprint_active"),
    ("pai_episodes", "uq_pai_episodes_ws_fingerprint_active"),
)


def _has_table(inspector, name: str) -> bool:
    return name in inspector.get_table_names()


def _collapse_existing_duplicates(bind, table: str) -> int:
    """Retire older rows that share a fingerprint, newest wins."""
    rows = bind.execute(sa.text(
        f"SELECT workspace_id, fingerprint FROM {table} "
        "WHERE status = 'active' AND fingerprint IS NOT NULL "
        "GROUP BY workspace_id, fingerprint HAVING COUNT(*) > 1"
    )).fetchall()

    collapsed = 0
    for workspace_id, fingerprint in rows:
        ids = [r[0] for r in bind.execute(sa.text(
            f"SELECT id FROM {table} WHERE workspace_id = :ws "
            "AND fingerprint = :fp AND status = 'active' "
            "ORDER BY created_at DESC, id DESC"
        ), {"ws": workspace_id, "fp": fingerprint}).fetchall()]
        for stale_id in ids[1:]:
            bind.execute(
                sa.text(f"UPDATE {table} SET status = 'superseded' WHERE id = :id"),
                {"id": stale_id},
            )
            collapsed += 1
    return collapsed


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    dialect = bind.dialect.name

    for table, index_name in _SPECS:
        if not _has_table(inspector, table):
            continue
        if index_name in {i["name"] for i in inspector.get_indexes(table)}:
            continue

        _collapse_existing_duplicates(bind, table)

        kwargs = {}
        condition = "status = 'active' AND fingerprint IS NOT NULL"
        if dialect == "postgresql":
            kwargs["postgresql_where"] = sa.text(condition)
        elif dialect == "sqlite":
            kwargs["sqlite_where"] = sa.text(condition)

        op.create_index(
            index_name, table, ["workspace_id", "fingerprint"],
            unique=True, **kwargs,
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    for table, index_name in _SPECS:
        if not _has_table(inspector, table):
            continue
        if index_name in {i["name"] for i in inspector.get_indexes(table)}:
            op.drop_index(index_name, table_name=table)
