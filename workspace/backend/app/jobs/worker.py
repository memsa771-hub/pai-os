# -*- coding: utf-8 -*-
"""Standalone durable-job worker.

    python -m app.jobs.worker

Runs as its OWN process, not inside the web process. That is deliberate: a
memory extraction that calls an LLM can take tens of seconds, and the web
process caps its threadpool to match the DB pool (see `app/main.py` lifespan)
precisely so request handling cannot exhaust connections. Doing slow work
there would compete with request latency, which requirement #3 forbids.

Each iteration:
  1. reclaim jobs whose worker died
  2. claim only as many jobs as there are FREE execution slots
  3. start each claimed job immediately, concurrently, bounded by those slots
  4. sleep only when there was nothing to do

**Why bounded concurrency rather than a sequential batch.** A claimed job's
lease starts ticking the moment it is claimed. If a batch of five were run
one after another, jobs 2-5 would sit unrenewed behind a slow job 1, lose
their leases, be reclaimed by another worker — and then still execute here
when their turn came, because nothing rechecked ownership before the handler
ran. That is a duplicate external side effect (a duplicate LLM call, a
duplicate outbound request) which discarding the final commit does not undo.

So: never claim more than can start now, and start lease renewal before the
handler. `JOB_WORKER_CONCURRENCY` slots are the only jobs in flight, and
`claim(limit=free_slots)` means a job is never claimed with nowhere to run.
"""

import asyncio
import logging
import os
import signal

from app.database import new_session
from app.jobs.service import (
    LEASE_RENEW_SECONDS,
    BackgroundJobService,
    _worker_id,
    job_handlers,
    run_job,
)

logger = logging.getLogger(__name__)

POLL_INTERVAL_SECONDS = float(os.environ.get("JOB_WORKER_POLL_SECONDS", "2"))
# Jobs this worker may execute at once. Also the claim ceiling: we never take
# a job we cannot start immediately.
CONCURRENCY = int(os.environ.get("JOB_WORKER_CONCURRENCY", "5"))
# Reclaim sweeps are cheap but pointless every tick.
RECLAIM_EVERY_TICKS = 30

_shutdown = asyncio.Event()


async def _renew_lease(job_id: str, worker: str) -> None:
    """Hold the lease open for as long as the job is actually running.

    Uses its own session — the handler owns the job's session, and writing the
    heartbeat through that one would interleave with the handler's own
    transaction and could commit its half-finished work.
    """
    while True:
        await asyncio.sleep(LEASE_RENEW_SECONDS)
        db = new_session()
        try:
            if not BackgroundJobService(db).heartbeat(job_id, worker):
                # Lease lost: someone else owns this job now. Stop renewing;
                # `_process_one` will find the job no longer ours and discard
                # its result rather than double-completing it.
                logger.warning("lease lost id=%s worker=%s", job_id, worker)
                return
        except Exception:
            logger.exception("lease renewal failed id=%s", job_id)
        finally:
            db.close()


async def _process_one(job_id: str, worker: str) -> bool:
    """Run a single claimed job in its own session and transaction.

    A fresh session per job means a handler that poisons its transaction
    cannot take down the rest of the batch. Lease renewal starts before the
    handler, and ownership is checked both before running and before
    committing.
    """
    db = new_session()
    lease = asyncio.create_task(_renew_lease(job_id, worker))
    try:
        service = BackgroundJobService(db)
        job = service.get(job_id)
        # Ownership gate BEFORE the handler runs. Between claim and here the
        # lease may have been reclaimed; running anyway would duplicate the
        # job's external side effects, which no later rollback can undo.
        if job is None or job.status != "running" or job.locked_by != worker:
            logger.warning(
                "skipping id=%s — not owned by %s (status=%s locked_by=%s)",
                job_id, worker, getattr(job, "status", None),
                getattr(job, "locked_by", None),
            )
            return False
        try:
            result = await run_job(job, db)
            # Re-check ownership before committing. If the lease expired
            # mid-run and another worker took over, completing here would
            # overwrite that worker's outcome with ours.
            db.refresh(job)
            if job.locked_by != worker or job.status != "running":
                db.rollback()
                logger.warning(
                    "discarding result for id=%s — lease no longer held by %s",
                    job_id, worker,
                )
                return False
            service.complete(job, result if isinstance(result, dict) else {"value": result})
            db.commit()
            logger.info("job succeeded id=%s type=%s worker=%s", job.id, job.job_type, worker)
            return True
        except Exception as exc:
            db.rollback()
            # Re-fetch: the rollback detached whatever we had.
            job = service.get(job_id)
            if job is not None and job.locked_by == worker:
                service.fail(job, f"{type(exc).__name__}: {exc}")
                db.commit()
            logger.exception("job failed id=%s type=%s", job_id, getattr(job, "job_type", "?"))
            return False
    finally:
        lease.cancel()
        try:
            await lease
        except asyncio.CancelledError:
            pass
        db.close()


async def run_worker_loop(
    poll_interval: float = POLL_INTERVAL_SECONDS,
    concurrency: int = CONCURRENCY,
    max_iterations: int | None = None,
) -> None:
    """The worker loop. `max_iterations` bounds it for tests."""
    worker = _worker_id()
    logger.info(
        "job worker starting id=%s concurrency=%d handlers=%s",
        worker, concurrency, list(job_handlers.registered()),
    )
    tick = 0
    in_flight: set[asyncio.Task] = set()

    try:
        while not _shutdown.is_set():
            if max_iterations is not None and tick >= max_iterations:
                break
            tick += 1

            # Drop finished tasks so their slots are free again.
            in_flight = {t for t in in_flight if not t.done()}
            free_slots = concurrency - len(in_flight)

            claimed: list[str] = []
            if free_slots > 0:
                db = new_session()
                try:
                    service = BackgroundJobService(db)
                    if tick % RECLAIM_EVERY_TICKS == 1:
                        reclaimed = service.reclaim_stale()
                        if reclaimed:
                            logger.warning("reclaimed %d stale job(s)", reclaimed)
                    # Claim AT MOST the number we can start right now, so no
                    # job's lease starts ticking while it waits for a slot.
                    claimed = [
                        j.id for j in service.claim(limit=free_slots, worker_id=worker)
                    ]
                except Exception:
                    logger.exception("job claim failed")
                finally:
                    db.close()

            # Start every claimed job immediately — lease renewal begins inside
            # `_process_one`, so nothing sits claimed-but-unrenewed.
            for job_id in claimed:
                in_flight.add(asyncio.create_task(_process_one(job_id, worker)))

            if not claimed:
                # Nothing new. Wake on shutdown, or when a running job frees a
                # slot, whichever comes first — a full worker should not sleep
                # the whole interval before noticing capacity.
                waiters = [asyncio.create_task(_shutdown.wait())]
                waiters.extend(in_flight)
                done, pending = await asyncio.wait(
                    waiters, timeout=poll_interval,
                    return_when=asyncio.FIRST_COMPLETED,
                )
                for task in pending:
                    if task not in in_flight:
                        task.cancel()
                for task in done:
                    if task not in in_flight:
                        task.cancel()
    finally:
        # Let in-flight jobs finish rather than abandoning leases mid-run.
        if in_flight:
            logger.info("waiting for %d in-flight job(s)", len(in_flight))
            await asyncio.gather(*in_flight, return_exceptions=True)

    logger.info("job worker stopped id=%s", worker)


def _install_signal_handlers(loop: asyncio.AbstractEventLoop) -> None:
    def _stop():
        logger.info("shutdown signal received")
        _shutdown.set()

    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, _stop)
        except NotImplementedError:
            # Windows without ProactorEventLoop support — Ctrl+C still works.
            pass


def main() -> None:
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    # Import for side effects: handlers register themselves on import, so the
    # worker must load them before it starts claiming.
    import app.memory.handlers  # noqa: F401

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    _install_signal_handlers(loop)
    try:
        loop.run_until_complete(run_worker_loop())
    finally:
        loop.close()


if __name__ == "__main__":
    main()
