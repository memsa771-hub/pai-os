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
