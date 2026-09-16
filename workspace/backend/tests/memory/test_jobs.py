# -*- coding: utf-8 -*-
"""Durable background jobs: idempotency, claiming, retry, recovery.

SQLite serialises writers, so it cannot demonstrate two workers racing. These
tests cover the logic; `tests/test_memory_jobs_postgres.py` exercises genuine
concurrent claiming against a real PostgreSQL server.
"""

from datetime import datetime, timedelta, timezone

import pytest

from app.jobs.service import BackgroundJobService, JobHandlerRegistry, run_job
from app.models import BackgroundJob


def _now():
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Idempotency
# ---------------------------------------------------------------------------

def test_same_idempotency_key_does_not_duplicate_work(db_session, workspace):
    """Enqueueing the same logical job twice yields one row.

    The realistic trigger: a message is processed, the pod restarts before the
    job completes, and the same extraction is enqueued again.
    """
    service = BackgroundJobService(db_session)
    first = service.enqueue(
        "memory.extract", {"event_id": "evt-1"},
        workspace_id=workspace.id, idempotency_key="extract:evt-1",
    )
    second = service.enqueue(
        "memory.extract", {"event_id": "evt-1"},
        workspace_id=workspace.id, idempotency_key="extract:evt-1",
    )
    db_session.commit()

    assert first.id == second.id
    assert db_session.query(BackgroundJob).count() == 1


def test_jobs_without_idempotency_key_are_independent(db_session, workspace):
    """A NULL key must not collide — several NULLs are allowed by the index."""
    service = BackgroundJobService(db_session)
    service.enqueue("memory.embed", {"n": 1}, workspace_id=workspace.id)
    service.enqueue("memory.embed", {"n": 2}, workspace_id=workspace.id)
    db_session.commit()
    assert db_session.query(BackgroundJob).count() == 2


# ---------------------------------------------------------------------------
# Claiming
# ---------------------------------------------------------------------------

def test_claim_marks_running_and_counts_the_attempt(db_session, workspace):
    service = BackgroundJobService(db_session)
    service.enqueue("memory.extract", {}, workspace_id=workspace.id)
    db_session.commit()

    claimed = service.claim(limit=5)
    assert len(claimed) == 1
    assert claimed[0].status == "running"
    assert claimed[0].attempts == 1
    assert claimed[0].locked_by and claimed[0].locked_at


def test_a_claimed_job_is_not_claimed_again(db_session, workspace):
    """The second claim must find nothing — no double delivery."""
    service = BackgroundJobService(db_session)
    service.enqueue("memory.extract", {}, workspace_id=workspace.id)
    db_session.commit()

    assert len(service.claim(limit=5)) == 1
    assert service.claim(limit=5) == []


def test_future_jobs_are_not_claimed_early(db_session, workspace):
    """`available_at` is a visibility timestamp; a future job stays invisible."""
    service = BackgroundJobService(db_session)
    service.enqueue(
        "memory.extract", {}, workspace_id=workspace.id,
        available_at=_now() + timedelta(hours=1),
    )
    db_session.commit()
    assert service.claim(limit=5) == []


def test_claim_respects_priority(db_session, workspace):
    service = BackgroundJobService(db_session)
    service.enqueue("memory.embed", {"tag": "low"}, workspace_id=workspace.id, priority=0)
    service.enqueue("memory.extract", {"tag": "high"}, workspace_id=workspace.id, priority=10)
    db_session.commit()

    claimed = service.claim(limit=1)
    assert claimed[0].payload["tag"] == "high"


# ---------------------------------------------------------------------------
# Failure and retry
# ---------------------------------------------------------------------------

def test_failed_job_is_requeued_with_backoff(db_session, workspace):
    service = BackgroundJobService(db_session)
    service.enqueue("memory.extract", {}, workspace_id=workspace.id)
    db_session.commit()

    job = service.claim(limit=1)[0]
    service.fail(job, "boom")
    db_session.commit()

    assert job.status == "pending"
    assert job.last_error == "boom"
    assert job.locked_by is None          # released, so any worker may retake it
    # Not immediately runnable — the backoff pushed it into the future.
    assert service.claim(limit=5) == []


def test_retry_is_claimable_once_the_backoff_elapses(db_session, workspace):
    service = BackgroundJobService(db_session)
    service.enqueue("memory.extract", {}, workspace_id=workspace.id)
    db_session.commit()

    job = service.claim(limit=1)[0]
    service.fail(job, "transient")
    job.available_at = _now() - timedelta(seconds=1)   # simulate elapsed backoff
    db_session.commit()

    reclaimed = service.claim(limit=1)
    assert len(reclaimed) == 1
    assert reclaimed[0].attempts == 2     # the earlier attempt still counts


def test_job_fails_permanently_after_max_attempts(db_session, workspace):
    """A job that always fails must stop, not retry forever."""
    service = BackgroundJobService(db_session)
    service.enqueue("memory.extract", {}, workspace_id=workspace.id, max_attempts=2)
    db_session.commit()

    for _ in range(2):
        job = service.claim(limit=1)[0]
        service.fail(job, "always fails")
        job.available_at = _now() - timedelta(seconds=1)
        db_session.commit()

    assert job.status == "failed"
    assert job.completed_at is not None
    assert service.claim(limit=5) == []


def test_completion_records_the_result(db_session, workspace):
    service = BackgroundJobService(db_session)
    service.enqueue("memory.extract", {}, workspace_id=workspace.id)
    db_session.commit()

    job = service.claim(limit=1)[0]
    service.complete(job, {"candidates_proposed": 3})
    db_session.commit()

    assert job.status == "succeeded"
    assert job.result == {"candidates_proposed": 3}
    assert job.locked_by is None and job.completed_at is not None


# ---------------------------------------------------------------------------
# Crash recovery
# ---------------------------------------------------------------------------

def test_stale_lock_is_reclaimed(db_session, workspace):
    """A job whose worker died must not be stranded in `running` forever."""
    service = BackgroundJobService(db_session)
    service.enqueue("memory.extract", {}, workspace_id=workspace.id)
    db_session.commit()

    job = service.claim(limit=1)[0]
    job.locked_at = _now() - timedelta(hours=2)        # worker died long ago
    db_session.commit()

    assert service.reclaim_stale() == 1
    assert job.status == "pending"
    assert job.locked_by is None
    assert len(service.claim(limit=1)) == 1


def test_live_lock_is_not_stolen(db_session, workspace):
    """A job a healthy worker is still running must be left alone."""
    service = BackgroundJobService(db_session)
    service.enqueue("memory.extract", {}, workspace_id=workspace.id)
    db_session.commit()

    job = service.claim(limit=1)[0]
    db_session.commit()

    assert service.reclaim_stale() == 0
    assert job.status == "running"


def test_stale_job_past_max_attempts_is_failed_not_requeued(db_session, workspace):
    """A crash-looping job must eventually stop rather than cycle forever."""
    service = BackgroundJobService(db_session)
    service.enqueue("memory.extract", {}, workspace_id=workspace.id, max_attempts=1)
    db_session.commit()

    job = service.claim(limit=1)[0]
    job.locked_at = _now() - timedelta(hours=2)
    db_session.commit()

    assert service.reclaim_stale() == 1
    assert job.status == "failed"


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_unknown_job_type_raises(db_session, workspace):
    service = BackgroundJobService(db_session)
    job = service.enqueue("nope.not.real", {}, workspace_id=workspace.id)
    db_session.commit()

    with pytest.raises(LookupError, match="No handler registered"):
        await run_job(job, db_session, registry=JobHandlerRegistry())


def test_duplicate_handler_registration_is_rejected():
    """Two handlers for one job type is a bug worth failing loudly on."""
    registry = JobHandlerRegistry()

    async def handler(job, db):
        return {}

    registry.register("x.y", handler)
    with pytest.raises(ValueError, match="Duplicate job handler"):
        registry.register("x.y", handler)
