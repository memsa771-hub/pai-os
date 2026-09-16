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
  2. claim a batch (FOR UPDATE SKIP LOCKED)
  3. run each handler, commit or fail+backoff per job
  4. sleep only when there was nothing to do

One job's failure never touches its neighbours — each gets its own
try/except and its own transaction outcome.
"""

import asyncio
import logging
import os
import signal

from app.database import SessionLocal
from app.jobs.service import BackgroundJobService, _worker_id, job_handlers, run_job

logger = logging.getLogger(__name__)

POLL_INTERVAL_SECONDS = float(os.environ.get("JOB_WORKER_POLL_SECONDS", "2"))
BATCH_SIZE = int(os.environ.get("JOB_WORKER_BATCH", "5"))
# Reclaim sweeps are cheap but pointless every tick.
RECLAIM_EVERY_TICKS = 30

_shutdown = asyncio.Event()


async def _process_one(job_id: str, worker: str) -> bool:
    """Run a single claimed job in its own session and transaction.

    A fresh session per job means a handler that poisons its transaction
    cannot take down the rest of the batch.
    """
    db = SessionLocal()
    try:
        service = BackgroundJobService(db)
        job = service.get(job_id)
        if job is None or job.status != "running":
            return False
        try:
            result = await run_job(job, db)
            service.complete(job, result if isinstance(result, dict) else {"value": result})
            db.commit()
            logger.info("job succeeded id=%s type=%s worker=%s", job.id, job.job_type, worker)
            return True
        except Exception as exc:
            db.rollback()
            # Re-fetch: the rollback detached whatever we had.
            job = service.get(job_id)
            if job is not None:
                service.fail(job, f"{type(exc).__name__}: {exc}")
                db.commit()
            logger.exception("job failed id=%s type=%s", job_id, getattr(job, "job_type", "?"))
            return False
    finally:
        db.close()


async def run_worker_loop(
    poll_interval: float = POLL_INTERVAL_SECONDS,
    batch_size: int = BATCH_SIZE,
    max_iterations: int | None = None,
) -> None:
    """The worker loop. `max_iterations` bounds it for tests."""
    worker = _worker_id()
    logger.info(
        "job worker starting id=%s handlers=%s", worker, list(job_handlers.registered())
    )
    tick = 0
    while not _shutdown.is_set():
        if max_iterations is not None and tick >= max_iterations:
            break
        tick += 1

        claimed: list[str] = []
        db = SessionLocal()
        try:
            service = BackgroundJobService(db)
            if tick % RECLAIM_EVERY_TICKS == 1:
                reclaimed = service.reclaim_stale()
                if reclaimed:
                    logger.warning("reclaimed %d stale job(s)", reclaimed)
            claimed = [j.id for j in service.claim(limit=batch_size, worker_id=worker)]
        except Exception:
            logger.exception("job claim failed")
        finally:
            db.close()

        for job_id in claimed:
            if _shutdown.is_set():
                break
            await _process_one(job_id, worker)

        if not claimed:
            try:
                await asyncio.wait_for(_shutdown.wait(), timeout=poll_interval)
            except asyncio.TimeoutError:
                pass

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
