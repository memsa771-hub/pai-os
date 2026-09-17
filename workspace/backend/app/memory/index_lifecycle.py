# -*- coding: utf-8 -*-
"""Helpers that keep the derived index in step with canonical changes.

Separate module so `semantic.py` / `episodic.py` can enqueue index work without
importing `handlers.py`, which imports them back.

None of these are required for correctness. `MemoryRetriever` re-reads
PostgreSQL and drops anything inactive, so a failed or delayed unindex cannot
resurface a forgotten memory — these only keep the index from accumulating
garbage. They therefore never raise into their caller: a student's "forget
Canada" must succeed even if the queue is unavailable.
"""

import hashlib
import logging
from typing import Optional

logger = logging.getLogger(__name__)


def unindex_key(workspace_id: str, ids: list[str]) -> str:
    """Stable idempotency key for an unindex job.

    hashlib, not `hash()`: Python randomises string hashing per process
    (PYTHONHASHSEED), so the previous key differed between the web process and
    the worker, and between restarts — the exact situations idempotency is for.
    """
    digest = hashlib.sha256(
        "\x1f".join([workspace_id, *sorted(ids)]).encode("utf-8")
    ).hexdigest()[:32]
    return f"unindex:{workspace_id}:{digest}"


def enqueue_unindex(db, workspace_id: str, ids: list[str]) -> Optional[str]:
    """Queue removal of canonical ids from the retrieval index."""
    if not ids:
        return None
    try:
        from app.jobs.service import BackgroundJobService
        from app.memory.handlers import JOB_UNINDEX

        job = BackgroundJobService(db).enqueue(
            job_type=JOB_UNINDEX,
            workspace_id=workspace_id,
            payload={"ids": sorted(ids)},
            idempotency_key=unindex_key(workspace_id, ids),
        )
        return job.id
    except Exception:
        logger.warning(
            "memory: failed to queue unindex for workspace=%s (%d ids) — "
            "retrieval still filters on canonical status",
            workspace_id, len(ids), exc_info=True,
        )
        return None


def enqueue_reindex(db, workspace_id: str, purge_first: bool = False) -> Optional[str]:
    """Queue a full rebuild of one workspace's index from PostgreSQL."""
    try:
        from app.jobs.service import BackgroundJobService
        from app.memory.handlers import JOB_REINDEX

        job = BackgroundJobService(db).enqueue(
            job_type=JOB_REINDEX,
            workspace_id=workspace_id,
            payload={"purge_first": purge_first},
        )
        return job.id
    except Exception:
        logger.exception("memory: failed to queue reindex for workspace=%s", workspace_id)
        return None
