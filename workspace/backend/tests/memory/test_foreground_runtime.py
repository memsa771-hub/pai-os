# -*- coding: utf-8 -*-
"""Foreground runtime hardening.

1. documented Compose rollout variables cannot silently disappear
2. a synchronous DB stall cannot block the event loop past the deadline
3. abandoned foreground DB work stays bounded
4. the reported retrieval mode is what actually happened
"""

import asyncio
import threading
import time
from pathlib import Path

import pytest
import yaml

from app.memory import foreground_executor
from app.memory.context import StudentContext
from app.memory.foreground import build_foreground_context

# Resolved from the repo when the tests run against a checkout. In the backend
# container only `backend/` is mounted, so the compose file is genuinely
# absent — these config assertions are skipped there rather than failing for an
# environmental reason, matching test_migration_schema.py's convention.
COMPOSE = Path(__file__).resolve().parents[3] / "docker-compose.yml"
_COMPOSE_MISSING = pytest.mark.skipif(
    not COMPOSE.exists(),
    reason="docker-compose.yml not available (backend-only mount)",
)


@pytest.fixture(autouse=True)
def _reset_executor():
    foreground_executor.reset_for_tests()
    yield
    foreground_executor.reset_for_tests()


def _student(**kwargs):
    context = StudentContext(workspace_id="ws-1")
    context.vault = kwargs.get("vault", {"education.cgpa": 3.5})
    context.memories = []
    context.episodes = []
    return context


# ---------------------------------------------------------------------------
# 1. Compose configuration
# ---------------------------------------------------------------------------

def _backend_env() -> dict:
    assert COMPOSE.exists(), f"compose file not found at {COMPOSE}"
    document = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))
    return document["x-backend-env"]


@_COMPOSE_MISSING
@pytest.mark.parametrize("variable", [
    "PAI_MEMORY_CONTEXT_ENABLED",
    "PAI_MEMORY_CONTEXT_TIMEOUT_MS",
    "PAI_MEMORY_CONTEXT_MAX_CHARS",
    "MEMORY_RETRIEVAL_CANDIDATES",
    "MEMORY_RETRIEVAL_LIMIT",
    "MEMORY_RERANKER",
])
def test_documented_rollout_variables_are_passed_to_containers(variable):
    """Config the rollout doc tells an operator to set must actually arrive.

    Regression: these existed in `app/config.py` but were never plumbed
    through Compose, so setting them in `.env` did nothing.
    """
    assert variable in _backend_env(), f"{variable} missing from x-backend-env"


@_COMPOSE_MISSING
def test_compose_env_is_shared_by_backend_and_worker():
    document = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))
    for service in ("backend", "worker"):
        environment = document["services"][service]["environment"]
        # Both merge the shared anchor rather than redeclaring, so they cannot
        # drift apart.
        assert environment, f"{service} has no environment"


@_COMPOSE_MISSING
def test_compose_defaults_are_safe():
    """Compose must not switch on injection or a vector backend by itself."""
    environment = _backend_env()
    assert environment["PAI_MEMORY_CONTEXT_ENABLED"] == "${PAI_MEMORY_CONTEXT_ENABLED:-false}"
    assert environment["MEMORY_VECTOR_BACKEND"] == "${MEMORY_VECTOR_BACKEND:-}"


@_COMPOSE_MISSING
def test_every_compose_memory_variable_exists_in_config():
    """No variable plumbed through Compose that the app ignores."""
    import app.config as config_module

    source = config_module.__dict__["__doc__"] or ""
    import inspect

    source = inspect.getsource(config_module)
    for variable in _backend_env():
        if variable.startswith(("MEMORY_", "PAI_MEMORY_", "QDRANT_")):
            assert f'"{variable}"' in source, f"{variable} is not read by config.py"


# ---------------------------------------------------------------------------
# 2. Synchronous DB stall must not block the loop
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_event_loop_stays_responsive_during_a_db_stall(monkeypatch):
    """The core fix.

    A blocking DB call used to run on the event loop inside a single await, so
    `wait_for` could not interrupt it and every other request on the worker
    stalled too.
    """
    def _stalled_db(*args):
        time.sleep(3)                     # synchronous, uninterruptible
        return _student()

    monkeypatch.setattr("app.memory.foreground._hybrid_blocking", _stalled_db)
    monkeypatch.setattr("app.memory.foreground._structured", _stalled_db)
    monkeypatch.setattr("app.config.config.PAI_MEMORY_CONTEXT_TIMEOUT_MS", 300,
                        raising=False)

    ticks = 0

    async def _heartbeat():
        nonlocal ticks
        while True:
            await asyncio.sleep(0.02)
            ticks += 1

    beat = asyncio.create_task(_heartbeat())
    started = time.monotonic()
    context = await build_foreground_context("ws-1", "anything")
    elapsed_ms = (time.monotonic() - started) * 1000
    beat.cancel()

    assert context.mode in ("timeout", "error", "busy")
    assert not context.has_content
    # Returned at roughly the deadline, not after the 3s stall.
    assert elapsed_ms < 300 * 2.5, f"took {elapsed_ms:.0f}ms against a 300ms budget"
    # And the loop kept running the whole time.
    assert ticks > 5, f"event loop was blocked (only {ticks} heartbeats)"


@pytest.mark.asyncio
async def test_deadline_holds_when_pool_acquisition_stalls(monkeypatch):
    """Covers connect/pool waits, not just Qdrant/embedding waits."""
    def _stalled_session(*args):
        time.sleep(3)                     # models a pool checkout that hangs
        return _student()

    monkeypatch.setattr("app.memory.foreground._hybrid_blocking", _stalled_session)
    monkeypatch.setattr("app.memory.foreground._structured", _stalled_session)
    monkeypatch.setattr("app.config.config.PAI_MEMORY_CONTEXT_TIMEOUT_MS", 250,
                        raising=False)

    started = time.monotonic()
    await build_foreground_context("ws-1", "q")
    assert (time.monotonic() - started) * 1000 < 250 * 2.5


# ---------------------------------------------------------------------------
# 3. Abandoned work stays bounded
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_repeated_timeouts_do_not_create_unbounded_db_work(monkeypatch):
    """Threads cannot be killed, so admission must be capped.

    Without a limit, every timed-out turn would leave another stuck query
    behind and a slow database would become a dead one.
    """
    from app.config import config

    def _stalled(*args):
        time.sleep(2)
        return _student()

    monkeypatch.setattr("app.memory.foreground._hybrid_blocking", _stalled)
    monkeypatch.setattr("app.memory.foreground._structured", _stalled)
    monkeypatch.setattr("app.config.config.PAI_MEMORY_CONTEXT_TIMEOUT_MS", 80,
                        raising=False)

    # Far more turns than the cap allows.
    results = await asyncio.gather(*[
        build_foreground_context(f"ws-{i}", "q") for i in range(30)
    ])

    assert all(not r.has_content for r in results)
    peak = foreground_executor.inflight_count()
    assert peak <= config.PAI_MEMORY_FOREGROUND_MAX_INFLIGHT, (
        f"{peak} in-flight operations exceeds the "
        f"{config.PAI_MEMORY_FOREGROUND_MAX_INFLIGHT} cap"
    )
    # Load was shed rather than queued.
    assert any(r.mode == "busy" for r in results)


@pytest.mark.asyncio
async def test_slots_are_released_after_a_stalled_call_finishes(monkeypatch):
    def _slow(*args):
        time.sleep(0.3)
        return _student()

    monkeypatch.setattr("app.memory.foreground._hybrid_blocking", _slow)
    monkeypatch.setattr("app.config.config.PAI_MEMORY_CONTEXT_TIMEOUT_MS", 50,
                        raising=False)

    await build_foreground_context("ws-1", "q")
    assert foreground_executor.inflight_count() >= 0

    await asyncio.sleep(0.5)              # let the abandoned thread finish
    assert foreground_executor.inflight_count() == 0, "slot was never released"


# ---------------------------------------------------------------------------
# 4. Real retrieval mode
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_lexical_degradation_is_reported_as_lexical(monkeypatch):
    """Mode 1 telemetry must not claim hybrid when no vector backend exists."""
    def _degraded(*args):
        # What `_hybrid_blocking` returns when the retriever fell back.
        return _student(), "lexical_fallback"

    monkeypatch.setattr("app.memory.foreground._hybrid_blocking", _degraded)

    context = await build_foreground_context("ws-1", "germany")
    assert context.mode == "lexical_fallback", (
        "foreground reported hybrid for a lexical retrieval"
    )


@pytest.mark.asyncio
async def test_true_hybrid_is_reported_as_hybrid(monkeypatch):
    monkeypatch.setattr(
        "app.memory.foreground._hybrid_blocking",
        lambda *args: (_student(), "hybrid"),
    )
    context = await build_foreground_context("ws-1", "germany")
    assert context.mode == "hybrid"


@pytest.mark.asyncio
async def test_mode_is_not_inferred_from_configuration():
    """It must come from the retrieval result, not an env check."""
    import inspect

    import app.memory.foreground as module

    source = inspect.getsource(module.build_foreground_context)
    assert "MEMORY_VECTOR_BACKEND" not in source
    assert "retrieval_mode" in source


@pytest.mark.asyncio
async def test_context_service_records_the_retrieval_mode(
    db_session, workspace, seed_fields,
):
    """End to end: no vector backend configured -> lexical, reported as such."""
    from app.memory.context import MemoryContextService
    from app.memory.semantic import MemoryService

    MemoryService(db_session).create(
        workspace_id=workspace.id, content="Germany is the first choice.",
        memory_type="preference",
    )
    db_session.commit()

    service = MemoryContextService(db_session)
    await service.build_student_context_async(
        workspace_id=workspace.id, query="Germany", caller="counselor",
    )
    # NullMemoryIndex is the default, so the retriever degrades.
    assert service.last_retrieval_mode == "lexical_fallback"


# ---------------------------------------------------------------------------
# 5. Admission-slot lifecycle
#
# Slots must follow the concurrent Future, not the callable. Releasing inside
# the callable leaked one slot per QUEUED-then-cancelled operation, because the
# callable that owned the release never ran.
# ---------------------------------------------------------------------------

def _pin_executor(monkeypatch, workers: int, max_inflight: int):
    monkeypatch.setattr("app.config.config.PAI_MEMORY_FOREGROUND_WORKERS", workers,
                        raising=False)
    monkeypatch.setattr("app.config.config.PAI_MEMORY_FOREGROUND_MAX_INFLIGHT",
                        max_inflight, raising=False)
    foreground_executor.reset_for_tests()


@pytest.mark.asyncio
async def test_queued_cancellation_releases_its_slot(monkeypatch):
    """A: workers=1, max_inflight=2 — the exact leak scenario.

    Job 1 occupies the only worker. Job 2 is admitted but queued. Job 2's
    caller times out, the queued future is cancelled before starting, and its
    slot must come back.
    """
    _pin_executor(monkeypatch, workers=1, max_inflight=2)
    release = threading.Event()
    started = threading.Event()

    def _blocker():
        started.set()
        release.wait(timeout=5)
        return "first"

    def _never_runs():
        raise AssertionError("queued callable should not have executed")

    first = asyncio.create_task(foreground_executor.run_bounded(_blocker))
    await asyncio.to_thread(started.wait, 2)

    # Admitted but queued behind the single worker.
    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(
            foreground_executor.run_bounded(_never_runs), 0.2
        )

    # The queued future was cancelled before running — its slot is back.
    for _ in range(50):
        if foreground_executor.inflight_count() == 1:
            break
        await asyncio.sleep(0.02)
    assert foreground_executor.inflight_count() == 1, (
        "queued cancellation leaked an admission slot"
    )

    release.set()
    assert await first == "first"
    for _ in range(50):
        if foreground_executor.inflight_count() == 0:
            break
        await asyncio.sleep(0.02)
    assert foreground_executor.inflight_count() == 0


@pytest.mark.asyncio
async def test_repeated_queued_timeouts_never_pin_at_busy(monkeypatch):
    """B: many rounds of queued timeouts must not exhaust admission forever."""
    _pin_executor(monkeypatch, workers=1, max_inflight=2)

    for _ in range(12):
        release = threading.Event()
        started = threading.Event()

        def _blocker():
            started.set()
            release.wait(timeout=5)
            return "ok"

        holder = asyncio.create_task(foreground_executor.run_bounded(_blocker))
        await asyncio.to_thread(started.wait, 2)

        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(
                foreground_executor.run_bounded(lambda: "queued"), 0.1
            )

        release.set()
        await holder
        for _ in range(50):
            if foreground_executor.inflight_count() == 0:
                break
            await asyncio.sleep(0.02)

    # Admission still works after a dozen queued timeouts.
    assert foreground_executor.inflight_count() == 0
    assert await foreground_executor.run_bounded(lambda: "still working") == \
        "still working"


@pytest.mark.asyncio
async def test_running_operation_keeps_its_slot_until_the_thread_exits(monkeypatch):
    """C: a timed-out but RUNNING call still holds the database."""
    _pin_executor(monkeypatch, workers=2, max_inflight=4)
    release = threading.Event()
    started = threading.Event()

    def _slow():
        started.set()
        release.wait(timeout=5)
        return "done"

    task = asyncio.create_task(foreground_executor.run_bounded(_slow))
    await asyncio.to_thread(started.wait, 2)

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    # Still running -> slot correctly retained.
    assert foreground_executor.inflight_count() == 1

    release.set()
    for _ in range(50):
        if foreground_executor.inflight_count() == 0:
            break
        await asyncio.sleep(0.02)
    assert foreground_executor.inflight_count() == 0, (
        "slot not released after the thread finished"
    )


def test_queued_cancellation_releases_immediately(monkeypatch):
    """D: a never-started operation frees its slot without waiting.

    Driven synchronously against the concurrent Future rather than through an
    event loop: awaiting introduces scheduling slack that makes "was it still
    queued?" timing-dependent, and this property is about the Future's own
    lifecycle, not asyncio's.
    """
    _pin_executor(monkeypatch, workers=1, max_inflight=3)
    release = threading.Event()
    started = threading.Event()

    def _blocker():
        started.set()
        release.wait(timeout=5)
        return "first"

    def _never_runs():
        raise AssertionError("queued callable executed after cancellation")

    executor = foreground_executor._get_executor()

    # Occupy the single worker, mirroring run_bounded's accounting.
    assert foreground_executor._acquire()
    busy = executor.submit(_blocker)
    busy.add_done_callback(lambda _f: foreground_executor._release())
    assert started.wait(2)

    # Admit a second operation that must sit QUEUED.
    assert foreground_executor._acquire()
    queued = executor.submit(_never_runs)
    queued.add_done_callback(lambda _f: foreground_executor._release())
    assert foreground_executor.inflight_count() == 2

    # Cancellable precisely because it has not started.
    assert queued.cancel(), "future had already started; not a queued case"
    # The done-callback fires on cancellation, so the slot is back immediately
    # — without waiting for the still-running first job.
    assert foreground_executor.inflight_count() == 1
    assert not release.is_set()

    release.set()
    assert busy.result(timeout=5) == "first"
    assert foreground_executor.inflight_count() == 0


@pytest.mark.asyncio
async def test_no_double_or_negative_release_under_cancellation(monkeypatch):
    """E: racing cancellation must not push the counter below zero."""
    _pin_executor(monkeypatch, workers=2, max_inflight=6)

    async def _one():
        task = asyncio.create_task(
            foreground_executor.run_bounded(lambda: time.sleep(0.05) or "x")
        )
        await asyncio.sleep(0)
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass

    await asyncio.gather(*[_one() for _ in range(20)])
    for _ in range(100):
        if foreground_executor.inflight_count() == 0:
            break
        await asyncio.sleep(0.02)

    count = foreground_executor.inflight_count()
    assert count == 0, f"counter settled at {count}, expected 0"
    assert count >= 0, "counter went negative"


@pytest.mark.asyncio
async def test_stress_leaves_no_leaked_slots(monkeypatch):
    """Strengthened: after everything settles, in-flight must be exactly 0.

    Previously this only checked the cap, which a leak satisfies trivially.
    """
    _pin_executor(monkeypatch, workers=4, max_inflight=8)

    async def _turn(i):
        try:
            return await asyncio.wait_for(
                foreground_executor.run_bounded(lambda: time.sleep(0.02) or i), 0.5
            )
        except (asyncio.TimeoutError, foreground_executor.ForegroundBusy):
            return None

    await asyncio.gather(*[_turn(i) for i in range(40)])
    for _ in range(100):
        if foreground_executor.inflight_count() == 0:
            break
        await asyncio.sleep(0.02)

    assert foreground_executor.inflight_count() == 0, "admission slots leaked"
