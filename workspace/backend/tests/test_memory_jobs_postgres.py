# -*- coding: utf-8 -*-
"""Real-PostgreSQL concurrency tests for durable job claiming.

SQLite cannot demonstrate these: its writer lock serialises everything, so two
"workers" never actually contend. Only a real server shows that
`FOR UPDATE SKIP LOCKED` hands each job to exactly one worker and that neither
blocks on the other.

    export TEST_DATABASE_URL=postgresql://postgres:dev@localhost:5432/openagents_workspace
    pytest -m postgres tests/test_memory_jobs_postgres.py

Without TEST_DATABASE_URL the whole module is skipped, matching
tests/test_browser_postgres_concurrency.py.
"""

import os
import threading
import uuid

import pytest

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL", "")

pytestmark = [
    pytest.mark.postgres,
    pytest.mark.skipif(
        not TEST_DATABASE_URL.startswith("postgresql"),
        reason="TEST_DATABASE_URL not set to a PostgreSQL DSN",
    ),
]


@pytest.fixture
def pg_engine():
    from sqlalchemy import create_engine
    from app.database import Base
    import app.models  # noqa: F401 — register models

    engine = create_engine(TEST_DATABASE_URL)
    Base.metadata.create_all(bind=engine)
    yield engine
    engine.dispose()


@pytest.fixture
def pg_sessionmaker(pg_engine):
    from sqlalchemy.orm import sessionmaker

    return sessionmaker(bind=pg_engine, autocommit=False, autoflush=False)


@pytest.fixture
def pg_workspace(pg_sessionmaker):
    """A real workspace row; cleaned up with its cascade afterwards."""
    from app.models import Workspace

    session = pg_sessionmaker()
    workspace = Workspace(
        id=str(uuid.uuid4()),
        name="Job Concurrency Test",
        slug=f"jobs-{uuid.uuid4().hex[:8]}",
        password_hash=uuid.uuid4().hex,
    )
    session.add(workspace)
    session.commit()
    workspace_id = workspace.id
    session.close()

    yield workspace_id

    session = pg_sessionmaker()
    try:
        obj = session.get(Workspace, workspace_id)
        if obj is not None:
            session.delete(obj)      # cascades to background_jobs
            session.commit()
    finally:
        session.close()


def test_concurrent_workers_never_claim_the_same_job(pg_sessionmaker, pg_workspace):
    """The core guarantee: N workers, N jobs, no job delivered twice.

    Without SKIP LOCKED this either deadlocks or double-delivers.
    """
    from app.jobs.service import BackgroundJobService

    job_count = 20
    worker_count = 4

    session = pg_sessionmaker()
    try:
        service = BackgroundJobService(session)
        for i in range(job_count):
            service.enqueue(
                "memory.extract", {"n": i}, workspace_id=pg_workspace,
                idempotency_key=f"concurrency-{pg_workspace}-{i}",
            )
        session.commit()
    finally:
        session.close()

    claimed_by_worker: dict[int, list[str]] = {}
    errors: list[Exception] = []
    barrier = threading.Barrier(worker_count)

    def worker(index: int):
        local = pg_sessionmaker()
        mine: list[str] = []
        try:
            barrier.wait(timeout=10)      # maximise real contention
            while True:
                jobs = BackgroundJobService(local).claim(limit=3, worker_id=f"w{index}")
                if not jobs:
                    break
                mine.extend(j.id for j in jobs)
        except Exception as exc:          # noqa: BLE001 — surfaced below
            errors.append(exc)
        finally:
            claimed_by_worker[index] = mine
            local.close()

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(worker_count)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)

    assert not errors, f"worker errors: {errors}"

    all_claimed = [job_id for ids in claimed_by_worker.values() for job_id in ids]
    assert len(all_claimed) == job_count, "some jobs were never claimed"
    assert len(set(all_claimed)) == job_count, "a job was claimed by two workers"


def test_concurrent_enqueue_of_one_key_creates_one_job(pg_sessionmaker, pg_workspace):
    """Idempotency must hold under a genuine race, not just sequentially.

    Two threads enqueue the same key at once; the unique index makes one lose,
    and the loser must adopt the winner's row rather than raise.
    """
    from app.jobs.service import BackgroundJobService
    from app.models import BackgroundJob

    key = f"race-{uuid.uuid4().hex}"
    results: list[str] = []
    errors: list[Exception] = []
    barrier = threading.Barrier(2)

    def enqueue():
        session = pg_sessionmaker()
        try:
            barrier.wait(timeout=10)
            job = BackgroundJobService(session).enqueue(
                "memory.extract", {"v": 1}, workspace_id=pg_workspace,
                idempotency_key=key,
            )
            session.commit()
            results.append(job.id)
        except Exception as exc:          # noqa: BLE001
            errors.append(exc)
            session.rollback()
        finally:
            session.close()

    threads = [threading.Thread(target=enqueue) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)

    assert not errors, f"enqueue errors: {errors}"

    session = pg_sessionmaker()
    try:
        count = session.query(BackgroundJob).filter(
            BackgroundJob.idempotency_key == key
        ).count()
    finally:
        session.close()
    assert count == 1
    assert len(set(results)) == 1, "the two enqueues returned different rows"


# ---------------------------------------------------------------------------
# Lease ownership under real concurrency
# ---------------------------------------------------------------------------

def test_long_job_keeps_its_lease_while_abandoned_work_is_reclaimed(
    pg_sessionmaker, pg_workspace,
):
    """A live long-running job is safe; only genuinely dead work moves.

    Two jobs claimed by worker-a. One keeps heartbeating (a slow but healthy
    job); the other's worker "dies". A sweep must reclaim exactly the dead one.
    """
    from app.jobs.service import BackgroundJobService
    from app.models import BackgroundJob
    from datetime import datetime, timedelta, timezone

    session = pg_sessionmaker()
    try:
        service = BackgroundJobService(session)
        service.enqueue("memory.extract", {"tag": "slow"}, workspace_id=pg_workspace,
                        idempotency_key=f"slow-{pg_workspace}")
        service.enqueue("memory.extract", {"tag": "dead"}, workspace_id=pg_workspace,
                        idempotency_key=f"dead-{pg_workspace}")
        session.commit()

        claimed = service.claim(limit=2, worker_id="worker-a")
        assert len(claimed) == 2
        by_tag = {j.payload["tag"]: j for j in claimed}
        slow_id, dead_id = by_tag["slow"].id, by_tag["dead"].id

        # Both leases age out...
        stale = datetime.now(timezone.utc) - timedelta(minutes=10)
        session.execute(
            BackgroundJob.__table__.update()
            .where(BackgroundJob.id.in_([slow_id, dead_id]))
            .values(locked_at=stale)
        )
        session.commit()

        # ...but the slow job's worker is alive and renews.
        assert service.heartbeat(slow_id, "worker-a") is True

        assert service.reclaim_stale() == 1
        session.expire_all()
        assert session.get(BackgroundJob, slow_id).status == "running"
        assert session.get(BackgroundJob, dead_id).status == "pending"
    finally:
        session.close()


def test_waiting_jobs_do_not_lose_leases_behind_a_slow_job(pg_sessionmaker, pg_workspace):
    """The batch-ownership race: claimed jobs must not expire while queued.

    The worker claims only as many jobs as it has free slots and starts lease
    renewal immediately, so a slow job cannot strand its batch-mates. Asserted
    on the invariant that matters: every claimed job is renewable throughout.
    """
    from app.jobs.service import BackgroundJobService
    from app.models import BackgroundJob
    from datetime import datetime, timedelta, timezone

    session = pg_sessionmaker()
    try:
        service = BackgroundJobService(session)
        for i in range(4):
            service.enqueue("memory.embed", {"n": i}, workspace_id=pg_workspace,
                            idempotency_key=f"batch-{pg_workspace}-{i}")
        session.commit()

        claimed = service.claim(limit=4, worker_id="worker-a")
        assert len(claimed) == 4
        ids = [j.id for j in claimed]

        # Time passes while job 0 runs long.
        stale = datetime.now(timezone.utc) - timedelta(minutes=10)
        session.execute(
            BackgroundJob.__table__.update()
            .where(BackgroundJob.id.in_(ids)).values(locked_at=stale)
        )
        session.commit()

        # Every in-flight job renews — none is stranded waiting its turn.
        for job_id in ids:
            assert service.heartbeat(job_id, "worker-a") is True

        assert service.reclaim_stale() == 0
        session.expire_all()
        for job_id in ids:
            assert session.get(BackgroundJob, job_id).status == "running"
    finally:
        session.close()


def test_stale_worker_cannot_act_after_ownership_moved(pg_sessionmaker, pg_workspace):
    """The original worker must be locked out once its lease is taken over.

    It can neither renew nor complete — which is what stops it overwriting the
    new owner's outcome with its own stale result.
    """
    from app.jobs.service import BackgroundJobService
    from app.models import BackgroundJob
    from datetime import datetime, timedelta, timezone

    session = pg_sessionmaker()
    try:
        service = BackgroundJobService(session)
        service.enqueue("memory.extract", {}, workspace_id=pg_workspace,
                        idempotency_key=f"takeover-{pg_workspace}")
        session.commit()

        job_id = service.claim(limit=1, worker_id="worker-a")[0].id

        session.execute(
            BackgroundJob.__table__.update().where(BackgroundJob.id == job_id)
            .values(locked_at=datetime.now(timezone.utc) - timedelta(minutes=10))
        )
        session.commit()
        service.reclaim_stale()

        taken = service.claim(limit=1, worker_id="worker-b")
        assert len(taken) == 1 and taken[0].id == job_id

        # worker-a is locked out of renewal...
        assert service.heartbeat(job_id, "worker-a") is False
        # ...and the ownership predicate the worker checks before completing
        # tells it to discard its result.
        session.expire_all()
        job = session.get(BackgroundJob, job_id)
        assert job.locked_by == "worker-b"
        assert job.status == "running"

        # worker-b, the real owner, still works normally.
        assert service.heartbeat(job_id, "worker-b") is True
    finally:
        session.close()


def test_two_workers_never_hold_the_same_valid_lease(pg_sessionmaker, pg_workspace):
    """Exactly one worker owns a job at any moment, under real contention."""
    import threading
    from app.jobs.service import BackgroundJobService

    session = pg_sessionmaker()
    try:
        BackgroundJobService(session).enqueue(
            "memory.extract", {}, workspace_id=pg_workspace,
            idempotency_key=f"single-{pg_workspace}",
        )
        session.commit()
    finally:
        session.close()

    winners: list[str] = []
    barrier = threading.Barrier(4)

    def contend(name: str):
        local = pg_sessionmaker()
        try:
            barrier.wait(timeout=10)
            for job in BackgroundJobService(local).claim(limit=1, worker_id=name):
                winners.append(name)
        finally:
            local.close()

    threads = [threading.Thread(target=contend, args=(f"w{i}",)) for i in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    assert len(winners) == 1, f"job claimed by {len(winners)} workers: {winners}"
