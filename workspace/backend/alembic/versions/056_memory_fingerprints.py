# -*- coding: utf-8 -*-
"""Stored dedupe fingerprints for semantic memories and episodes.

Revision ID: 056
Revises: 055

`app/memory/dedupe.py` computed a fingerprint per row in Python, loading up to
500 rows for every candidate. Correct, but O(n) per proposal and it grows with
the student's history. The fingerprint is deterministic, so it belongs in the
row with an index on it.

EXACT normalized dedupe only — this catches restatements ("Wants Germany." vs
"wants  germany"), never paraphrases. Semantic near-duplicate detection is the
vector index's job, not this column's.

Backfill computes fingerprints for existing rows using the same
`dedupe.memory_fingerprint` / `episode_fingerprint` helpers the application
uses, so old and new rows are directly comparable. The column is nullable:
a row whose fingerprint could not be computed stays usable, it just misses the
fast dedupe path.

Create Date: 2026-09-17
"""

import sqlalchemy as sa
from alembic import op

revision = "056"
down_revision = "055"
branch_labels = None
depends_on = None


def _has_table(inspector, name: str) -> bool:
    return name in inspector.get_table_names()


def _has_column(inspector, table: str, column: str) -> bool:
    return column in {c["name"] for c in inspector.get_columns(table)}


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if _has_table(inspector, "pai_memories") and not _has_column(
        inspector, "pai_memories", "fingerprint"
    ):
        op.add_column("pai_memories", sa.Column("fingerprint", sa.Text(), nullable=True))
        # Partial-shaped composite: the dedupe query is always
        # "this workspace, active, this fingerprint".
        op.create_index(
            "idx_pai_memories_ws_status_fingerprint", "pai_memories",
            ["workspace_id", "status", "fingerprint"],
        )

    if _has_table(inspector, "pai_episodes") and not _has_column(
        inspector, "pai_episodes", "fingerprint"
    ):
        op.add_column("pai_episodes", sa.Column("fingerprint", sa.Text(), nullable=True))
        op.create_index(
            "idx_pai_episodes_ws_status_fingerprint", "pai_episodes",
            ["workspace_id", "status", "fingerprint"],
        )

    _backfill(bind)


def _backfill(bind) -> None:
    """Fill fingerprints for existing rows, in batches.

    Uses the application's own helpers so backfilled values are byte-identical
    to what new rows get — a backfill that computed them differently would be
    worse than no backfill, since it would silently stop matching.
    """
    try:
        from app.memory.dedupe import episode_fingerprint, memory_fingerprint
    except Exception:  # pragma: no cover - app not importable in some contexts
        return

    batch = 500

    rows = bind.execute(sa.text(
        "SELECT id, memory_type, content FROM pai_memories WHERE fingerprint IS NULL"
    )).fetchall()
    for start in range(0, len(rows), batch):
        for row in rows[start:start + batch]:
            bind.execute(
                sa.text("UPDATE pai_memories SET fingerprint = :fp WHERE id = :id"),
                {"fp": memory_fingerprint(row[1] or "", row[2] or ""), "id": row[0]},
            )

    rows = bind.execute(sa.text(
        "SELECT id, event_type, summary FROM pai_episodes WHERE fingerprint IS NULL"
    )).fetchall()
    for start in range(0, len(rows), batch):
        for row in rows[start:start + batch]:
            bind.execute(
                sa.text("UPDATE pai_episodes SET fingerprint = :fp WHERE id = :id"),
                {"fp": episode_fingerprint(row[1] or "", row[2] or ""), "id": row[0]},
            )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if _has_table(inspector, "pai_episodes") and _has_column(
        inspector, "pai_episodes", "fingerprint"
    ):
        op.drop_index("idx_pai_episodes_ws_status_fingerprint", table_name="pai_episodes")
        op.drop_column("pai_episodes", "fingerprint")

    if _has_table(inspector, "pai_memories") and _has_column(
        inspector, "pai_memories", "fingerprint"
    ):
        op.drop_index("idx_pai_memories_ws_status_fingerprint", table_name="pai_memories")
        op.drop_column("pai_memories", "fingerprint")
