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


# ---------------------------------------------------------------------------
# Leases — a live long-running job must not be reclaimed
# ---------------------------------------------------------------------------

def test_heartbeat_renews_the_lease(db_session, workspace):
    """A job that keeps saying it is alive is never stolen.

    Regression: reclaim used to treat "running longer than N" as dead, which
    silently ran long jobs twice.
    """
    service = BackgroundJobService(db_session)
    service.enqueue("memory.extract", {}, workspace_id=workspace.id)
    db_session.commit()

    job = service.claim(limit=1, worker_id="worker-a")[0]
    job_id = job.id

    # Simulate a long run: the lease is about to expire...
    job.locked_at = _now() - timedelta(minutes=4, seconds=50)
    db_session.commit()

    # ...but the worker renews it.
    assert service.heartbeat(job_id, "worker-a") is True

    assert service.reclaim_stale() == 0
    db_session.refresh(job)
    assert job.status == "running"
    assert job.locked_by == "worker-a"


def test_expired_lease_is_reclaimed(db_session, workspace):
    """A worker that stopped renewing loses the job."""
    service = BackgroundJobService(db_session)
    service.enqueue("memory.extract", {}, workspace_id=workspace.id)
    db_session.commit()

    job = service.claim(limit=1, worker_id="worker-a")[0]
    job.locked_at = _now() - timedelta(minutes=10)
    db_session.commit()

    assert service.reclaim_stale() == 1
    db_session.refresh(job)
    assert job.status == "pending"
    assert job.locked_by is None


def test_heartbeat_fails_after_the_job_was_reclaimed(db_session, workspace):
    """The losing worker must find out, so it discards its result.

    Without this the original worker finishes and overwrites the outcome of
    whichever worker legitimately took the job over.
    """
    service = BackgroundJobService(db_session)
    service.enqueue("memory.extract", {}, workspace_id=workspace.id)
    db_session.commit()

    job = service.claim(limit=1, worker_id="worker-a")[0]
    job_id = job.id
    job.locked_at = _now() - timedelta(minutes=10)
    db_session.commit()

    service.reclaim_stale()
    taken = service.claim(limit=1, worker_id="worker-b")
    assert len(taken) == 1 and taken[0].id == job_id

    # worker-a no longer owns it and cannot renew.
    assert service.heartbeat(job_id, "worker-a") is False
    # worker-b does.
    assert service.heartbeat(job_id, "worker-b") is True


def test_heartbeat_on_a_finished_job_returns_false(db_session, workspace):
    service = BackgroundJobService(db_session)
    service.enqueue("memory.extract", {}, workspace_id=workspace.id)
    db_session.commit()

    job = service.claim(limit=1, worker_id="worker-a")[0]
    service.complete(job, {})
    db_session.commit()

    assert service.heartbeat(job.id, "worker-a") is False


def test_reclaim_does_not_load_every_running_job(db_session, workspace):
    """Reclaim is set-based SQL; it must not scale with the running set.

    Asserted behaviourally: many live jobs, none reclaimed, and the one dead
    job is found regardless.
    """
    service = BackgroundJobService(db_session)
    for i in range(25):
        service.enqueue("memory.embed", {"n": i}, workspace_id=workspace.id)
    db_session.commit()

    claimed = service.claim(limit=25, worker_id="worker-a")
    assert len(claimed) == 25

    dead = claimed[7]
    dead.locked_at = _now() - timedelta(minutes=10)
    db_session.commit()

    assert service.reclaim_stale() == 1
    db_session.refresh(dead)
    assert dead.status == "pending"
    still_running = [j for j in claimed if j.id != dead.id]
    for job in still_running:
        db_session.refresh(job)
        assert job.status == "running"


# ---------------------------------------------------------------------------
# Enqueue must not damage the caller's transaction
# ---------------------------------------------------------------------------

def test_idempotency_conflict_does_not_roll_back_the_caller(db_session, workspace):
    """A duplicate enqueue must not discard the caller's own uncommitted work.

    Regression: `enqueue` called `self.db.rollback()` on conflict, which threw
    away canonical memory rows written earlier in the SAME transaction —
    exactly what reconciliation does before enqueueing its embed job.
    """
    from app.memory.semantic import MemoryService

    service = BackgroundJobService(db_session)
    service.enqueue(
        "memory.embed", {"v": 1}, workspace_id=workspace.id,
        idempotency_key="dup-key",
    )
    db_session.commit()

    # Caller writes something important, THEN hits a duplicate enqueue.
    memory = MemoryService(db_session).create(
        workspace_id=workspace.id, content="Must survive the conflict",
        memory_type="preference",
    )
    again = service.enqueue(
        "memory.embed", {"v": 2}, workspace_id=workspace.id,
        idempotency_key="dup-key",
    )
    db_session.commit()

    # The duplicate resolved to the existing job...
    assert again.idempotency_key == "dup-key"
    assert db_session.query(BackgroundJob).filter(
        BackgroundJob.idempotency_key == "dup-key"
    ).count() == 1
    # ...and the caller's work survived.
    assert MemoryService(db_session).get(workspace.id, memory.id) is not None
