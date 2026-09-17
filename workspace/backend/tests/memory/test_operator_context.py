# -*- coding: utf-8 -*-
"""Operator's `context_refs` resolve to live memory, and stay lightweight.

`ExecutionRun.context_refs` holds references ("vault", "memory:preferences"),
never payloads. They are resolved at run time so a run started an hour ago
still sees the student's current profile — and the row never grows a copy of
the Vault.
"""

from app.memory.episodic import EpisodicMemoryService
from app.memory.semantic import MemoryService
from app.memory.vault import VaultService
from app.services.operator import _resolve_memory_context


def _populate(db, workspace_id):
    VaultService(db).apply_fact(
        workspace_id=workspace_id, field_key="education.cgpa", value=8.1,
        source_type="user_explicit",
    )
    MemoryService(db).create(
        workspace_id=workspace_id, content="Prefers research-focused universities",
        memory_type="preference",
    )
    EpisodicMemoryService(db).record(
        workspace_id=workspace_id, event_type="shortlist_removed",
        summary="Removed University X — tuition exceeded budget",
    )
    db.commit()


def test_refs_resolve_to_a_prompt_block(db_session, workspace, seed_fields):
    _populate(db_session, workspace.id)
    block = _resolve_memory_context(workspace.id, ["vault", "memory", "episodes"])

    assert "education.cgpa: 8.1" in block
    assert "research-focused" in block
    assert "University X" in block


def test_no_refs_yields_no_block(db_session, workspace, seed_fields):
    """A run with no context_refs must not get a stray empty section."""
    _populate(db_session, workspace.id)
    assert _resolve_memory_context(workspace.id, None) == ""
    assert _resolve_memory_context(workspace.id, []) == ""


def test_refs_narrow_what_is_resolved(db_session, workspace, seed_fields):
    _populate(db_session, workspace.id)
    block = _resolve_memory_context(workspace.id, ["vault"])

    assert "education.cgpa: 8.1" in block
    assert "research-focused" not in block


def test_resolution_reflects_current_state_not_a_snapshot(db_session, workspace, seed_fields):
    """The whole point of storing refs rather than payloads."""
    vault = VaultService(db_session)
    vault.apply_fact(
        workspace_id=workspace.id, field_key="education.cgpa", value=7.0,
        source_type="document",
    )
    db_session.commit()
    assert "education.cgpa: 7.0" in _resolve_memory_context(workspace.id, ["vault"])

    vault.apply_fact(
        workspace_id=workspace.id, field_key="education.cgpa", value=8.5,
        source_type="user_explicit",
    )
    db_session.commit()
    # Same refs, updated answer — no re-delegation needed.
    assert "education.cgpa: 8.5" in _resolve_memory_context(workspace.id, ["vault"])


def test_resolution_is_capability_gated_as_operator(db_session, workspace, seed_fields):
    """Operator resolves with ITS grant, so sensitive fields stay withheld."""
    VaultService(db_session).apply_fact(
        workspace_id=workspace.id, field_key="finance.budget",
        value={"amount": 30000, "currency": "EUR"}, source_type="user_explicit",
    )
    db_session.commit()

    block = _resolve_memory_context(workspace.id, ["vault"])
    assert "30000" not in block


def test_resolution_failure_is_not_fatal(db_session, workspace, monkeypatch):
    """A memory outage must not fail an otherwise-valid Operator run."""
    import app.services.operator as operator_module

    class _Boom:
        def __init__(self, db):
            raise RuntimeError("memory down")

    monkeypatch.setattr(
        "app.memory.context.MemoryContextService", _Boom, raising=True
    )
    assert _resolve_memory_context(workspace.id, ["vault"]) == ""


def test_empty_memory_yields_no_block(db_session, workspace, seed_fields):
    """A brand-new student produces no headings, not empty ones."""
    assert _resolve_memory_context(workspace.id, ["vault", "memory"]) == ""


def test_context_refs_stay_lightweight_in_the_db(db_session, workspace, seed_fields):
    """ExecutionRun must store references, never resolved payloads."""
    from app.models import ExecutionRun

    _populate(db_session, workspace.id)
    run = ExecutionRun(
        workspace_id=workspace.id,
        requested_by="openagents:pai",
        objective="Find suitable German universities",
        context_refs=["vault", "memory:preferences", "episodes:recent"],
    )
    db_session.add(run)
    db_session.commit()
    db_session.refresh(run)

    assert run.context_refs == ["vault", "memory:preferences", "episodes:recent"]
    # No student data leaked into the row.
    serialized = str(run.context_refs)
    assert "8.1" not in serialized
    assert "research-focused" not in serialized
