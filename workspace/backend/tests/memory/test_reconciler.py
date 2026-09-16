# -*- coding: utf-8 -*-
"""The candidate -> reconciler -> canonical boundary.

The property under test: proposing something never changes canonical state.
Only the deterministic reconciler does, and only when the proposal survives
validation, confidence and conflict policy.
"""

from app.memory.candidates import MemoryCandidateService
from app.memory.reconciler import MemoryReconciler
from app.memory.semantic import MemoryService
from app.memory.vault import VaultService
from app.models import MemoryCandidate, PaiEpisode, PaiMemory, VaultFact


# ---------------------------------------------------------------------------
# Proposing is not writing
# ---------------------------------------------------------------------------

def test_proposing_does_not_touch_the_vault(db_session, workspace, seed_fields):
    """The core safety property: an LLM proposal alone changes nothing."""
    candidates = MemoryCandidateService(db_session)
    candidates.propose(
        workspace_id=workspace.id, candidate_type="vault_fact", operation="upsert",
        key="education.cgpa", proposed_value=9.9, confidence=0.9,
        source_type="conversation",
    )
    db_session.commit()

    assert db_session.query(MemoryCandidate).count() == 1
    assert db_session.query(VaultFact).count() == 0
    assert VaultService(db_session).get_fact(workspace.id, "education.cgpa") is None


def test_proposing_does_not_create_memories_or_episodes(db_session, workspace):
    candidates = MemoryCandidateService(db_session)
    candidates.propose(
        workspace_id=workspace.id, candidate_type="semantic_memory",
        content="Prefers research universities", confidence=0.9,
    )
    candidates.propose(
        workspace_id=workspace.id, candidate_type="episode",
        content="Removed University X", confidence=0.9,
    )
    db_session.commit()

    assert db_session.query(PaiMemory).count() == 0
    assert db_session.query(PaiEpisode).count() == 0


# ---------------------------------------------------------------------------
# Acceptance
# ---------------------------------------------------------------------------

def test_reconciler_applies_an_explicit_user_correction(db_session, workspace, seed_fields):
    """The headline flow: "actually my CGPA is 8.4" corrects an earlier value."""
    vault = VaultService(db_session)
    vault.apply_fact(
        workspace_id=workspace.id, field_key="education.cgpa", value=7.2,
        source_type="document",
    )

    candidate = MemoryCandidateService(db_session).propose(
        workspace_id=workspace.id, candidate_type="vault_fact", operation="upsert",
        key="education.cgpa", proposed_value=8.4, confidence=1.0,
        source_type="user_explicit", evidence={"quote": "actually my CGPA is 8.4"},
    )
    result = MemoryReconciler(db_session).reconcile(candidate)
    db_session.commit()

    assert result.accepted
    assert vault.get_fact(workspace.id, "education.cgpa").value["value"] == 8.4
    assert candidate.status == "accepted"
    assert candidate.result_id == result.result_id
    # The superseded value is still on record.
    assert len(vault.history(workspace.id, "education.cgpa")) == 2


def test_accepted_semantic_candidate_becomes_a_memory(db_session, workspace):
    candidate = MemoryCandidateService(db_session).propose(
        workspace_id=workspace.id, candidate_type="semantic_memory",
        content="Prefers research-focused universities",
        entities={"memory_type": "preference"},
        confidence=0.9, source_type="conversation",
    )
    result = MemoryReconciler(db_session).reconcile(candidate)
    db_session.commit()

    assert result.accepted
    memory = MemoryService(db_session).get(workspace.id, result.result_id)
    assert memory.content == "Prefers research-focused universities"
    assert memory.memory_type == "preference"


def test_accepted_episode_candidate_becomes_an_episode(db_session, workspace):
    candidate = MemoryCandidateService(db_session).propose(
        workspace_id=workspace.id, candidate_type="episode",
        content="Removed University X — tuition exceeded budget",
        entities={"event_type": "shortlist_removed"},
        confidence=0.9,
    )
    result = MemoryReconciler(db_session).reconcile(candidate)
    db_session.commit()

    assert result.accepted
    episode = db_session.get(PaiEpisode, result.result_id)
    assert episode.event_type == "shortlist_removed"


# ---------------------------------------------------------------------------
# Rejection — the hallucination guard
# ---------------------------------------------------------------------------

def test_invalid_value_is_rejected_with_a_recorded_reason(db_session, workspace, seed_fields):
    """A hallucinated out-of-range CGPA must be a rejection, not a write."""
    candidate = MemoryCandidateService(db_session).propose(
        workspace_id=workspace.id, candidate_type="vault_fact", operation="upsert",
        key="education.cgpa", proposed_value=42, confidence=1.0,
        source_type="user_explicit",
    )
    result = MemoryReconciler(db_session).reconcile(candidate)
    db_session.commit()

    assert not result.accepted
    assert candidate.status == "rejected"
    assert "must be <= 10" in candidate.rejection_reason
    assert db_session.query(VaultFact).count() == 0


def test_low_confidence_inference_is_rejected(db_session, workspace, seed_fields):
    candidate = MemoryCandidateService(db_session).propose(
        workspace_id=workspace.id, candidate_type="vault_fact", operation="upsert",
        key="education.cgpa", proposed_value=8.0, confidence=0.1,
        source_type="conversation",
    )
    result = MemoryReconciler(db_session).reconcile(candidate)
    db_session.commit()

    assert not result.accepted
    assert "below threshold" in candidate.rejection_reason
    assert db_session.query(VaultFact).count() == 0


def test_explicit_user_statement_bypasses_the_confidence_floor(db_session, workspace, seed_fields):
    """The student saying it is not a guess we might be 10% sure of."""
    candidate = MemoryCandidateService(db_session).propose(
        workspace_id=workspace.id, candidate_type="vault_fact", operation="upsert",
        key="education.cgpa", proposed_value=8.0, confidence=0.1,
        source_type="user_explicit",
    )
    result = MemoryReconciler(db_session).reconcile(candidate)
    db_session.commit()
    assert result.accepted


def test_unknown_field_candidate_is_rejected(db_session, workspace, seed_fields):
    candidate = MemoryCandidateService(db_session).propose(
        workspace_id=workspace.id, candidate_type="vault_fact", operation="upsert",
        key="invented.by.the.model", proposed_value=1, confidence=1.0,
        source_type="user_explicit",
    )
    result = MemoryReconciler(db_session).reconcile(candidate)
    db_session.commit()

    assert not result.accepted
    assert "Unknown vault field" in candidate.rejection_reason


def test_a_candidate_is_only_reconciled_once(db_session, workspace, seed_fields):
    """Re-running the reconciler must not apply the same fact twice."""
    candidate = MemoryCandidateService(db_session).propose(
        workspace_id=workspace.id, candidate_type="vault_fact", operation="upsert",
        key="education.cgpa", proposed_value=8.0, confidence=1.0,
        source_type="user_explicit",
    )
    reconciler = MemoryReconciler(db_session)
    assert reconciler.reconcile(candidate).accepted
    second = reconciler.reconcile(candidate)
    db_session.commit()

    assert not second.accepted
    assert second.reason == "not_pending"
    assert db_session.query(VaultFact).filter_by(status="active").count() == 1


# ---------------------------------------------------------------------------
# Forgetting
# ---------------------------------------------------------------------------

def test_forgotten_memory_is_excluded_from_future_reads(db_session, workspace):
    """"Forget Canada" must actually stop Canada showing up."""
    memories = MemoryService(db_session)
    memories.create(
        workspace_id=workspace.id, content="Interested in Canada for study",
        memory_type="preference",
    )
    memories.create(
        workspace_id=workspace.id, content="Interested in Germany for study",
        memory_type="preference",
    )
    db_session.commit()

    candidate = MemoryCandidateService(db_session).propose(
        workspace_id=workspace.id, candidate_type="semantic_memory",
        operation="forget", content="Canada", confidence=1.0,
        source_type="user_explicit",
    )
    assert MemoryReconciler(db_session).reconcile(candidate).accepted
    db_session.commit()

    remaining = memories.list_memories(workspace.id)
    assert len(remaining) == 1
    assert "Germany" in remaining[0].content
    assert memories.search(workspace.id, "Canada") == []
    # Row retained for audit, just no longer active.
    assert db_session.query(PaiMemory).count() == 2


def test_forgetting_something_absent_is_rejected_not_silently_ok(db_session, workspace):
    """PAI should not claim it forgot something it never knew."""
    candidate = MemoryCandidateService(db_session).propose(
        workspace_id=workspace.id, candidate_type="semantic_memory",
        operation="forget", content="Antarctica", confidence=1.0,
        source_type="user_explicit",
    )
    result = MemoryReconciler(db_session).reconcile(candidate)
    db_session.commit()

    assert not result.accepted
    assert "no active memory matching" in candidate.rejection_reason


def test_retract_removes_a_vault_fact_from_reads(db_session, workspace, seed_fields):
    vault = VaultService(db_session)
    vault.apply_fact(
        workspace_id=workspace.id, field_key="education.cgpa", value=8.0,
        source_type="user_explicit",
    )
    db_session.commit()

    candidate = MemoryCandidateService(db_session).propose(
        workspace_id=workspace.id, candidate_type="vault_fact", operation="retract",
        key="education.cgpa", confidence=1.0, source_type="user_explicit",
    )
    assert MemoryReconciler(db_session).reconcile(candidate).accepted
    db_session.commit()

    assert vault.get_fact(workspace.id, "education.cgpa") is None


# ---------------------------------------------------------------------------
# Isolation
# ---------------------------------------------------------------------------

def test_reconciling_one_workspace_does_not_touch_another(
    db_session, workspace, other_workspace, seed_fields,
):
    candidates = MemoryCandidateService(db_session)
    candidates.propose(
        workspace_id=workspace.id, candidate_type="vault_fact", operation="upsert",
        key="education.cgpa", proposed_value=8.0, confidence=1.0,
        source_type="user_explicit",
    )
    candidates.propose(
        workspace_id=other_workspace.id, candidate_type="vault_fact", operation="upsert",
        key="education.cgpa", proposed_value=5.0, confidence=1.0,
        source_type="user_explicit",
    )
    db_session.commit()

    # Reconcile only the first workspace's pending candidates.
    MemoryReconciler(db_session).reconcile_pending(workspace.id)
    db_session.commit()

    vault = VaultService(db_session)
    assert vault.get_fact(workspace.id, "education.cgpa").value["value"] == 8.0
    assert vault.get_fact(other_workspace.id, "education.cgpa") is None


def test_candidate_lookup_is_workspace_scoped(db_session, workspace, other_workspace):
    """An id from another workspace must not resolve."""
    candidate = MemoryCandidateService(db_session).propose(
        workspace_id=workspace.id, candidate_type="semantic_memory",
        content="Something private", confidence=0.9,
    )
    db_session.commit()

    service = MemoryCandidateService(db_session)
    assert service.get(workspace.id, candidate.id) is not None
    assert service.get(other_workspace.id, candidate.id) is None
