# -*- coding: utf-8 -*-
"""Bounded execution boundary for foreground memory retrieval.

`asyncio.wait_for` only bounds *awaits*. Foreground retrieval ultimately calls
synchronous SQLAlchemy — pool acquisition, connect, query — and a stalled
PostgreSQL blocks the event loop inside a single `await` that never yields. The
deadline then bounds nothing: the Counselor hangs, and so does every other
request on that worker.

So all foreground DB work runs on a **dedicated, bounded thread pool**:

* the event loop never executes the blocking call itself, so a stall degrades
  one turn instead of the process
* `wait_for` around the future returns at the deadline regardless of whether
  the thread is still stuck
* the pool is small and has a hard admission limit, so repeated timeouts
  cannot spawn unbounded orphaned DB work — that is the failure mode that
  turns a slow database into a dead one

Separate from the default `asyncio.to_thread` executor on purpose: that one is
shared with every other `to_thread` caller in the process, and filling it with
stuck memory queries would starve unrelated work.

Only Counselor foreground retrieval uses this. Operator and the tool paths stay
synchronous — they are not on the chat-response critical path.
"""

import asyncio
import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, Optional

from app.config import config

logger = logging.getLogger(__name__)

_executor: Optional[ThreadPoolExecutor] = None
_lock = threading.Lock()

# Admission counter. Threads that have timed out keep running (a thread cannot
# be killed), so this — not the pool size — is what bounds how much abandoned
# DB work can exist at once.
_inflight = 0
_inflight_lock = threading.Lock()


class ForegroundBusy(RuntimeError):
    """Too much foreground DB work already in flight. Degrade, do not queue."""


def _get_executor() -> ThreadPoolExecutor:
    global _executor
    if _executor is None:
        with _lock:
            if _executor is None:
                _executor = ThreadPoolExecutor(
                    max_workers=config.PAI_MEMORY_FOREGROUND_WORKERS,
                    thread_name_prefix="pai-memory-fg",
                )
    return _executor


def inflight_count() -> int:
    """Current foreground DB operations, including abandoned ones."""
    with _inflight_lock:
        return _inflight


def _acquire() -> bool:
    global _inflight
    with _inflight_lock:
        if _inflight >= config.PAI_MEMORY_FOREGROUND_MAX_INFLIGHT:
            return False
        _inflight += 1
        return True


def _release() -> None:
    global _inflight
    with _inflight_lock:
        _inflight = max(0, _inflight - 1)


async def run_bounded(fn: Callable[..., Any], *args) -> Any:
    """Run a blocking callable on the foreground pool.

    Raises `ForegroundBusy` immediately when the in-flight limit is reached —
    shedding load rather than queueing behind stuck threads, since a queued
    call would only time out later having added more pressure.

    **Slot ownership follows the concurrent Future, not the callable.**
    Releasing inside the callable's `finally` leaked: when every worker was
    busy, an admitted call could sit QUEUED, the awaiting side time out, the
    queued future be cancelled before starting — and the callable that owned
    the release would never run. Slots leaked one per queued timeout until
    `_inflight` pinned at the cap and every later request returned `busy`
    forever.

    A single done-callback on the concurrent Future covers all three endings —
    completed, raised, cancelled-before-start — and `Future.add_done_callback`
    fires exactly once, so double-release is not possible.

    A timed-out but still RUNNING operation keeps its slot until the thread
    actually exits, which is correct: the database is still holding it.
    """
    if not _acquire():
        raise ForegroundBusy(
            f"{inflight_count()} foreground memory operations already in flight"
        )

    released = False

    def _release_once(_future) -> None:
        # Guarded as well as single-registered: cheap, and makes the invariant
        # hold even if a future implementation invokes callbacks differently.
        nonlocal released
        if not released:
            released = True
            _release()

    try:
        future = _get_executor().submit(fn, *args)
    except Exception:
        # Submission itself failed (e.g. pool shut down) — no future exists to
        # carry the release.
        _release()
        raise

    future.add_done_callback(_release_once)

    # `wrap_future` bridges the concurrent Future to this loop. Cancelling the
    # awaiting side propagates to the concurrent Future: if it has not started
    # it is cancelled (callback fires, slot freed immediately); if it is
    # already running the cancel is refused and the slot stays held until the
    # thread finishes.
    return await asyncio.wrap_future(future)


def reset_for_tests() -> None:
    """Drop the pool and counter between tests."""
    global _executor, _inflight
    with _lock:
        if _executor is not None:
            _executor.shutdown(wait=False)
        _executor = None
    with _inflight_lock:
        _inflight = 0
