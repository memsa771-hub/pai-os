# -*- coding: utf-8 -*-
"""Enqueue, claim, complete and retry durable jobs.

The claim is the interesting part. On PostgreSQL:

    SELECT ... WHERE status='pending' AND available_at <= NOW()
    ORDER BY priority DESC, available_at
    LIMIT n
    FOR UPDATE SKIP LOCKED

`SKIP LOCKED` is what makes N workers safe without a broker: a row another
transaction has locked is passed over rather than waited on, so two workers
never return the same job and neither blocks. SQLite (tests) has no such
clause and serialises writers anyway, so it degrades to a plain SELECT — the
same code path, minus the concurrency it cannot exhibit.
"""

import logging
import socket
import os
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Optional

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.models import BackgroundJob

logger = logging.getLogger(__name__)

# A job whose worker died holds `locked_at` forever. Anything running longer
# than this is presumed dead and may be reclaimed. Generous enough that a slow
# LLM extraction is not stolen from a live worker.
STALE_LOCK_SECONDS = 15 * 60

# Retry backoff, indexed by attempt number; the last entry repeats.
RETRY_BACKOFF_SECONDS = (10, 60, 300, 1800)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _worker_id() -> str:
    return f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:8]}"


def _as_aware(value: Optional[datetime]) -> Optional[datetime]:
    """SQLite returns naive datetimes; PostgreSQL returns aware ones.

    Comparing the two raises TypeError, so normalise before any comparison.
    """
    if value is not None and value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


class JobHandlerRegistry:
    """Maps `job_type` -> coroutine handler.

    Handlers register themselves at import time, so adding a job type never
    means editing the worker.
    """

    def __init__(self) -> None:
        self._handlers: dict[str, Callable] = {}

    def register(self, job_type: str, handler: Callable) -> Callable:
        if job_type in self._handlers:
            raise ValueError(f"Duplicate job handler: {job_type}")
        self._handlers[job_type] = handler
        return handler

    def handler_for(self, job_type: str) -> Optional[Callable]:
        return self._handlers.get(job_type)

    def registered(self) -> tuple[str, ...]:
        return tuple(sorted(self._handlers))


job_handlers = JobHandlerRegistry()


class BackgroundJobService:
    """Transactional API over the `background_jobs` table.

    Never commits on its own except where noted (`claim`, which must commit to
    publish the lock). The caller owns the transaction, matching how the rest
    of this backend treats its Session.
    """

    def __init__(self, db):
        self.db = db

    # -- enqueue ----------------------------------------------------------

    def enqueue(
        self,
        job_type: str,
        payload: Optional[dict] = None,
        workspace_id: Optional[str] = None,
        idempotency_key: Optional[str] = None,
        priority: int = 0,
        available_at: Optional[datetime] = None,
        max_attempts: int = 5,
    ) -> BackgroundJob:
        """Insert a job, or return the existing one for the same key.

        Idempotency is enforced by a UNIQUE constraint rather than a prior
        SELECT: two concurrent enqueues of the same logical work race, one
        loses on the constraint, and the loser returns the winner's row. A
        check-then-insert would let both through.
        """
        if idempotency_key:
            existing = self.db.execute(
                select(BackgroundJob).where(
                    BackgroundJob.idempotency_key == idempotency_key
                )
            ).scalar_one_or_none()
            if existing is not None:
                return existing

        job = BackgroundJob(
            workspace_id=workspace_id,
            job_type=job_type,
            payload=payload or {},
            status="pending",
            priority=priority,
            attempts=0,
            max_attempts=max_attempts,
            available_at=available_at or _now(),
            idempotency_key=idempotency_key,
        )
        self.db.add(job)
        try:
            self.db.flush()
        except IntegrityError:
            # Lost the race on the unique index — adopt the winner's row.
            self.db.rollback()
            existing = self.db.execute(
                select(BackgroundJob).where(
                    BackgroundJob.idempotency_key == idempotency_key
                )
            ).scalar_one_or_none()
            if existing is None:
                raise
            return existing
        return job

    # -- claim ------------------------------------------------------------

    def claim(self, limit: int = 1, worker_id: Optional[str] = None) -> list[BackgroundJob]:
        """Atomically take up to `limit` runnable jobs.

        Commits before returning: until the lock is visible to other
        transactions it does not exclude anyone.
        """
        worker = worker_id or _worker_id()
        now = _now()

        stmt = (
            select(BackgroundJob)
            .where(
                BackgroundJob.status == "pending",
                BackgroundJob.available_at <= now,
            )
            .order_by(BackgroundJob.priority.desc(), BackgroundJob.available_at)
            .limit(limit)
        )
        if self.db.bind is not None and self.db.bind.dialect.name == "postgresql":
            stmt = stmt.with_for_update(skip_locked=True)

        jobs = list(self.db.execute(stmt).scalars().all())
        for job in jobs:
            job.status = "running"
            job.attempts = (job.attempts or 0) + 1
            job.locked_at = now
            job.locked_by = worker
        if jobs:
            self.db.commit()
            for job in jobs:
                self.db.refresh(job)
        return jobs

    def reclaim_stale(self, older_than_seconds: int = STALE_LOCK_SECONDS) -> int:
        """Return jobs whose worker died to the pending pool.

        Without this a crash mid-job strands that row in `running` forever.
        Attempts are NOT incremented here — the attempt was already counted at
        claim time, so a crash-looping job still exhausts `max_attempts`.
        """
        cutoff = _now() - timedelta(seconds=older_than_seconds)
        stale = list(self.db.execute(
            select(BackgroundJob).where(BackgroundJob.status == "running")
        ).scalars().all())

        count = 0
        for job in stale:
            locked_at = _as_aware(job.locked_at)
            if locked_at is not None and locked_at > cutoff:
                continue
            if (job.attempts or 0) >= (job.max_attempts or 5):
                job.status = "failed"
                job.last_error = "worker lock expired; max attempts exhausted"
                job.completed_at = _now()
            else:
                job.status = "pending"
                job.locked_at = None
                job.locked_by = None
                job.last_error = "worker lock expired; requeued"
            count += 1
        if count:
            self.db.commit()
        return count

    # -- completion -------------------------------------------------------

    def complete(self, job: BackgroundJob, result: Optional[dict] = None) -> BackgroundJob:
        job.status = "succeeded"
        job.result = result or {}
        job.locked_at = None
        job.locked_by = None
        job.last_error = None
        job.completed_at = _now()
        self.db.flush()
        return job

    def fail(self, job: BackgroundJob, error: str, retry: bool = True) -> BackgroundJob:
        """Record a failure and either schedule a retry or give up.

        Retry pushes `available_at` forward; the row goes back to `pending` and
        is picked up by whichever worker gets there first — the retry does not
        belong to the worker that failed it.
        """
        job.last_error = (error or "")[:2000]
        job.locked_at = None
        job.locked_by = None

        attempts = job.attempts or 0
        if retry and attempts < (job.max_attempts or 5):
            idx = min(attempts - 1, len(RETRY_BACKOFF_SECONDS) - 1)
            delay = RETRY_BACKOFF_SECONDS[max(idx, 0)]
            job.status = "pending"
            job.available_at = _now() + timedelta(seconds=delay)
        else:
            job.status = "failed"
            job.completed_at = _now()
        self.db.flush()
        return job

    # -- reads ------------------------------------------------------------

    def get(self, job_id: str) -> Optional[BackgroundJob]:
        return self.db.get(BackgroundJob, job_id)

    def pending_count(self, workspace_id: Optional[str] = None) -> int:
        stmt = select(BackgroundJob).where(BackgroundJob.status == "pending")
        if workspace_id:
            stmt = stmt.where(BackgroundJob.workspace_id == workspace_id)
        return len(list(self.db.execute(stmt).scalars().all()))


async def run_job(job: BackgroundJob, db, registry: JobHandlerRegistry = job_handlers) -> Any:
    """Dispatch one claimed job to its handler.

    Raises if the handler raises; the worker loop turns that into `fail()`.
    """
    handler = registry.handler_for(job.job_type)
    if handler is None:
        raise LookupError(f"No handler registered for job type: {job.job_type}")
    return await handler(job, db)
