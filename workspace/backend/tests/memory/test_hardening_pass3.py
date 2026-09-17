# -*- coding: utf-8 -*-
"""Regressions for the third hardening pass.

1. `_post_response` must run every post-commit hook before returning.
2. Episodes must obey the same confidence gate as Vault and semantic memory.
3. Canonical provenance must point at the student, never the assistant.
"""

import inspect
import json

import pytest

from app.jobs.service import BackgroundJobService, run_job
from app.memory.candidates import MemoryCandidateService
from app.memory.handlers import JOB_EXTRACT
from app.memory.reconciler import DEFAULT_MIN_CONFIDENCE, MemoryReconciler
from app.models import EventRecord, MemoryCandidate, PaiEpisode, PaiMemory
from app.services import cloud_agent
from app.services.pai import PAI_AGENT_NAME

CHANNEL = "channel/pai-counselor"


# ---------------------------------------------------------------------------
# 1. _post_response lifecycle
# ---------------------------------------------------------------------------

def test_no_unreachable_code_after_return_in_post_response():
    """The regression itself: `return` sat above three post-commit hooks.

    Compiling the function and checking the return is the LAST statement is
    what actually catches a stray early return; reading the source for hook
    names would still pass with them orphaned.
    """
    source = inspect.getsource(cloud_agent._post_response)
    body = inspect.cleandoc(source)

    return_index = body.rindex("return event.id")
    after = body[return_index + len("return event.id"):].strip()
    assert after == "", f"unreachable code after return:\n{after[:400]}"


def test_post_response_keeps_every_post_commit_hook_in_order():
    """commit -> push -> redis -> workflow -> relay -> return."""
    body = inspect.getsource(cloud_agent._post_response)

    order = [
        "db.commit()",
        "fanout_for_event",
        "publish_event",
        "advance_workflow",
        "relay_for_event",
        "return event.id",
    ]
    positions = []
    for marker in order:
        assert marker in body, f"{marker} missing from _post_response"
        positions.append(body.index(marker))
    assert positions == sorted(positions), f"hooks out of order: {order}"


@pytest.mark.asyncio
async def test_post_response_runs_hooks_and_returns_the_event_id(
    db_session, workspace, monkeypatch,
):
    """Behavioural proof: returning the id does not skip the hooks."""
    called: list[str] = []

    monkeypatch.setattr(
        "app.services.push.fanout_for_event",
        lambda ws, ev: called.append("push"),
    )
    monkeypatch.setattr(
        "app.cache.publish_event",
        lambda channel, data: called.append("redis"),
    )
    monkeypatch.setattr(
        "app.services.workflow.advance_workflow",
        lambda ws, ev: called.append("workflow"),
    )
    monkeypatch.setattr(
        "app.services.integrations.relay_for_event",
        lambda ws, ev: called.append("relay"),
    )

    event_id = await cloud_agent._post_response(
        db_session, workspace.id, CHANNEL, PAI_AGENT_NAME, "Hello.", depth=0,
    )

    assert event_id, "the persisted event id must still be returned"
    # push and redis are awaited/inline; workflow and relay are scheduled on an
    # executor, so only assert the two that complete synchronously here.
    assert "push" in called
    assert "redis" in called

    stored = db_session.get(EventRecord, event_id)
    assert stored is not None and stored.network_id == workspace.id


@pytest.mark.asyncio
async def test_rejected_post_returns_none(db_session, workspace, monkeypatch):
    """EventRejected / missing workspace still return None."""
    assert await cloud_agent._post_response(
        db_session, "00000000-0000-0000-0000-000000000000",
        CHANNEL, PAI_AGENT_NAME, "Hello.", depth=0,
    ) is None


# ---------------------------------------------------------------------------
# 2. Episode confidence gate
# ---------------------------------------------------------------------------

def _episode_candidate(db, workspace_id, confidence, source_type="conversation", **kw):
    return MemoryCandidateService(db).propose(
        workspace_id=workspace_id, candidate_type="episode", operation="upsert",
        content="Decided to target Fall 2027.",
        entities={"event_type": "decision_made"},
        confidence=confidence, source_type=source_type, **kw,
    )


def test_low_confidence_episode_is_rejected(db_session, workspace):
    """A weak guess at "what the student decided" must not become history."""
    candidate = _episode_candidate(db_session, workspace.id, confidence=0.1)
    result = MemoryReconciler(db_session).reconcile(candidate)
    db_session.commit()

    assert not result.accepted
    assert "below threshold" in candidate.rejection_reason
    assert db_session.query(PaiEpisode).count() == 0


def test_confident_episode_is_accepted(db_session, workspace):
    candidate = _episode_candidate(
        db_session, workspace.id, confidence=DEFAULT_MIN_CONFIDENCE + 0.4,
    )
    result = MemoryReconciler(db_session).reconcile(candidate)
    db_session.commit()

    assert result.accepted
    assert db_session.query(PaiEpisode).count() == 1


def test_explicit_user_episode_bypasses_the_floor(db_session, workspace):
    """Matches Vault/semantic: the student saying it is not a guess."""
    candidate = _episode_candidate(
        db_session, workspace.id, confidence=0.05,
        source_type="user_explicit", allow_user_explicit=True,
    )
    result = MemoryReconciler(db_session).reconcile(candidate)
    db_session.commit()

    assert result.accepted
    assert db_session.query(PaiEpisode).count() == 1


def test_episode_gate_matches_the_other_types(db_session, workspace, seed_fields):
    """All three types must agree on the threshold — no type is a soft spot."""
    weak = 0.1
    reconciler = MemoryReconciler(db_session)
    candidates = MemoryCandidateService(db_session)

    episode = _episode_candidate(db_session, workspace.id, confidence=weak)
    semantic = candidates.propose(
        workspace_id=workspace.id, candidate_type="semantic_memory",
        operation="upsert", content="Vaguely likes cold weather.",
        confidence=weak, source_type="conversation",
    )
    vault = candidates.propose(
        workspace_id=workspace.id, candidate_type="vault_fact", operation="upsert",
        key="education.cgpa", proposed_value=3.5,
        confidence=weak, source_type="conversation",
    )

    for candidate in (episode, semantic, vault):
        assert not reconciler.reconcile(candidate).accepted
    db_session.commit()


# ---------------------------------------------------------------------------
# 3. Provenance
# ---------------------------------------------------------------------------

def _turn(db, workspace_id, student_text, assistant_text="Noted."):
    import uuid

    user = EventRecord(
        id=str(uuid.uuid4()), network_id=workspace_id,
        type="workspace.message.posted", source="human:student@example.com",
        target=CHANNEL, payload={"content": student_text, "message_type": "chat"},
        metadata_={}, timestamp=1000, visibility="channel",
    )
    assistant = EventRecord(
        id=str(uuid.uuid4()), network_id=workspace_id,
        type="workspace.message.posted", source=f"openagents:{PAI_AGENT_NAME}",
        target=CHANNEL, payload={"content": assistant_text, "message_type": "chat"},
        metadata_={}, timestamp=1001, visibility="channel",
    )
    db.add_all([user, assistant])
    db.commit()
    return user, assistant


async def _extract(db, workspace_id, user, assistant, monkeypatch, candidates):
    async def _fake(**kwargs):
        return json.dumps({"candidates": candidates})

    monkeypatch.setattr("app.memory.extractor.chat_completion", _fake)
    monkeypatch.setattr("app.config.config.PAI_API_KEY", "test-key", raising=False)

    job = BackgroundJobService(db).enqueue(JOB_EXTRACT, {
        "source_type": "conversation", "channel": CHANNEL,
        "user_event_id": user.id, "assistant_event_id": assistant.id,
    }, workspace_id=workspace_id)
    db.commit()
    await run_job(job, db)
    db.commit()


@pytest.mark.asyncio
async def test_semantic_provenance_is_the_student_event(
    db_session, workspace, seed_fields, monkeypatch,
):
    user, assistant = _turn(db_session, workspace.id, "I want Germany.")
    await _extract(db_session, workspace.id, user, assistant, monkeypatch, [{
        "candidate_type": "semantic_memory", "operation": "upsert",
        "content": "Wants Germany.", "memory_type": "preference",
        "confidence": 0.9, "quote": "I want Germany.",
    }])

    candidate = db_session.query(MemoryCandidate).one()
    assert candidate.source_event_ids == [user.id]
    assert assistant.id not in candidate.source_event_ids
    # Still reachable for debugging, but as context — not as evidence.
    assert candidate.evidence["context_assistant_event_id"] == assistant.id
    assert candidate.evidence["user_event_id"] == user.id

    MemoryReconciler(db_session).reconcile(candidate)
    db_session.commit()
    memory = db_session.query(PaiMemory).one()
    assert memory.source_event_ids == [user.id]


@pytest.mark.asyncio
async def test_episode_provenance_is_the_student_event(
    db_session, workspace, seed_fields, monkeypatch,
):
    user, assistant = _turn(db_session, workspace.id, "I removed University X.")
    await _extract(db_session, workspace.id, user, assistant, monkeypatch, [{
        "candidate_type": "episode", "operation": "upsert",
        "content": "Removed University X.", "event_type": "shortlist_removed",
        "confidence": 0.9, "quote": "I removed University X.",
    }])

    candidate = db_session.query(MemoryCandidate).one()
    assert candidate.source_event_ids == [user.id]

    MemoryReconciler(db_session).reconcile(candidate)
    db_session.commit()
    episode = db_session.query(PaiEpisode).one()
    assert episode.source_event_ids == [user.id]


@pytest.mark.asyncio
async def test_vault_provenance_is_the_student_event(
    db_session, workspace, seed_fields, monkeypatch,
):
    """`source_event_id` must be the student's message, not PAI's reply."""
    user, assistant = _turn(db_session, workspace.id, "My CGPA is 3.52.")
    await _extract(db_session, workspace.id, user, assistant, monkeypatch, [{
        "candidate_type": "vault_fact", "operation": "upsert",
        "key": "education.cgpa", "proposed_value": 3.52,
        "confidence": 0.95, "quote": "My CGPA is 3.52.",
    }])

    candidate = db_session.query(MemoryCandidate).one()
    result = MemoryReconciler(db_session).reconcile(candidate)
    db_session.commit()

    from app.memory.vault import VaultService

    fact = VaultService(db_session).get_fact(workspace.id, "education.cgpa")
    assert result.accepted
    assert fact.source_event_id == user.id
    assert fact.source_event_id != assistant.id
