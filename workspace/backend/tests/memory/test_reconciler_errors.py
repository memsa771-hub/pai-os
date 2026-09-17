# -*- coding: utf-8 -*-
"""Data errors reject; system errors propagate.

A rejection is a permanent verdict on a student's data. It must only ever be
caused by the data. A NameError or a dead database says nothing about the
candidate — swallowing it records a false verdict, loses the fact, and hides
the failure from the durable job that should have retried.
"""

import pytest
from sqlalchemy.exc import OperationalError

from app.jobs.service import BackgroundJobService, run_job
from app.memory.candidates import MemoryCandidateService
from app.memory.errors import MemoryDataError
from app.memory.field_definitions import VaultFieldError
from app.memory.handlers import JOB_RECONCILE
from app.memory.reconciler import MemoryReconciler
from app.models import BackgroundJob, MemoryCandidate, VaultFact


def _candidate(db, workspace_id, **overrides):
    spec = dict(
        workspace_id=workspace_id, candidate_type="vault_fact", operation="upsert",
        key="education.cgpa", proposed_value=8.0, confidence=1.0,
        source_type="user_explicit", allow_user_explicit=True,
    )
    spec.update(overrides)
    return MemoryCandidateService(db).propose(**spec)


# ---------------------------------------------------------------------------
# Data errors -> rejection
# ---------------------------------------------------------------------------

def test_vault_field_error_is_a_data_error():
    """The taxonomy itself: schema violations are data, not system, failures."""
    assert issubclass(VaultFieldError, MemoryDataError)


def test_invalid_value_is_rejected_not_raised(db_session, workspace, seed_fields):
    candidate = _candidate(db_session, workspace.id, proposed_value=42)
    result = MemoryReconciler(db_session).reconcile(candidate)
    db_session.commit()

    assert not result.accepted
    assert candidate.status == "rejected"
    assert "must be <= 10" in candidate.rejection_reason
    assert db_session.query(VaultFact).count() == 0


def test_unknown_field_is_rejected_not_raised(db_session, workspace, seed_fields):
    candidate = _candidate(db_session, workspace.id, key="not.a.field")
    result = MemoryReconciler(db_session).reconcile(candidate)
    db_session.commit()

    assert not result.accepted
    assert "Unknown vault field" in candidate.rejection_reason


# ---------------------------------------------------------------------------
# System errors -> propagate
# ---------------------------------------------------------------------------

def test_programming_error_propagates(db_session, workspace, seed_fields, monkeypatch):
    """A NameError must NOT become `rejection_reason="NameError: ..."`.

    Regression: a real NameError in `_reconcile_vault` was silently recorded
    as a candidate rejection during development. Tests caught it; production
    would have shown it as the student's data being refused.
    """
    reconciler = MemoryReconciler(db_session)

    def _boom(candidate):
        raise NameError("name 'VaultOutcome' is not defined")

    monkeypatch.setattr(reconciler, "_reconcile_vault", _boom)
    candidate = _candidate(db_session, workspace.id)

    with pytest.raises(NameError):
        reconciler.reconcile(candidate)

    # And the candidate is left alone for the retry — not falsely rejected.
    assert candidate.status == "pending"
    assert candidate.rejection_reason is None


def test_infrastructure_error_propagates(db_session, workspace, seed_fields, monkeypatch):
    """A dead database is not a verdict on the candidate."""
    reconciler = MemoryReconciler(db_session)

    def _boom(*args, **kwargs):
        raise OperationalError("SELECT 1", {}, Exception("connection refused"))

    monkeypatch.setattr(reconciler.vault, "apply_fact", _boom)
    candidate = _candidate(db_session, workspace.id)

    with pytest.raises(OperationalError):
        reconciler.reconcile(candidate)
    assert candidate.status == "pending"


@pytest.mark.parametrize("exc", [TypeError("bad"), AttributeError("nope"), KeyError("k")])
def test_assorted_programmer_errors_propagate(
    db_session, workspace, seed_fields, monkeypatch, exc,
):
    reconciler = MemoryReconciler(db_session)
    monkeypatch.setattr(
        reconciler, "_reconcile_semantic",
        lambda candidate: (_ for _ in ()).throw(exc),
    )
    candidate = _candidate(
        db_session, workspace.id, candidate_type="semantic_memory",
        key=None, proposed_value=None, content="something",
    )
    with pytest.raises(type(exc)):
        reconciler.reconcile(candidate)


# ---------------------------------------------------------------------------
# The durable job must fail and retry, not succeed
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_reconcile_job_fails_when_reconciler_raises(
    db_session, workspace, seed_fields, monkeypatch,
):
    """The whole point: an unexpected failure reaches the job, which retries.

    If the reconciler swallowed it, the job would report success and the
    candidate would be permanently mis-marked.
    """
    _candidate(db_session, workspace.id)
    job = BackgroundJobService(db_session).enqueue(
        JOB_RECONCILE, {}, workspace_id=workspace.id,
    )
    db_session.commit()

    def _boom(self, candidate):
        raise RuntimeError("infrastructure is down")

    monkeypatch.setattr(MemoryReconciler, "_reconcile_vault", _boom)

    with pytest.raises(RuntimeError, match="infrastructure is down"):
        await run_job(job, db_session)


@pytest.mark.asyncio
async def test_failed_reconcile_job_is_retried(db_session, workspace, seed_fields, monkeypatch):
    """A raised failure goes back to `pending` with a backoff, not `succeeded`."""
    _candidate(db_session, workspace.id)
    service = BackgroundJobService(db_session)
    service.enqueue(JOB_RECONCILE, {}, workspace_id=workspace.id)
    db_session.commit()

    job = service.claim(limit=1)[0]

    def _boom(self, candidate):
        raise RuntimeError("transient outage")

    monkeypatch.setattr(MemoryReconciler, "_reconcile_vault", _boom)

    with pytest.raises(RuntimeError):
        await run_job(job, db_session)

    db_session.rollback()
    job = service.get(job.id)
    service.fail(job, "RuntimeError: transient outage")
    db_session.commit()

    assert job.status == "pending"        # queued for retry, not failed-forever
    assert job.attempts == 1
    assert "transient outage" in job.last_error


@pytest.mark.asyncio
async def test_a_data_rejection_still_lets_the_job_succeed(
    db_session, workspace, seed_fields,
):
    """Bad data is a normal outcome — the job must NOT retry it forever."""
    _candidate(db_session, workspace.id, proposed_value=99)
    job = BackgroundJobService(db_session).enqueue(
        JOB_RECONCILE, {}, workspace_id=workspace.id,
    )
    db_session.commit()

    result = await run_job(job, db_session)
    db_session.commit()

    assert result["rejected"] == 1 and result["accepted"] == 0
    assert db_session.query(MemoryCandidate).one().status == "rejected"
