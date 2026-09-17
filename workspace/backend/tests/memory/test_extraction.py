# -*- coding: utf-8 -*-
"""Automatic conversation memory formation.

Deterministic mocked LLM responses throughout — these test the extraction
*pipeline* and its guardrails, not a model's judgement.
"""

import json

import pytest

from app.jobs.service import BackgroundJobService, run_job
from app.memory.dedupe import memory_fingerprint, normalize_text
from app.memory.extraction_context import build_turn_context
from app.memory.extractor import (
    ExtractionError,
    _parse_response,
    _validate,
    build_user_prompt,
)
from app.memory.handlers import JOB_EXTRACT, JOB_RECONCILE
from app.memory.reconciler import MemoryReconciler
from app.memory.semantic import MemoryService
from app.memory.turn_hook import enqueue_turn_extraction
from app.memory.vault import VaultService
from app.models import BackgroundJob, EventRecord, MemoryCandidate, PaiEpisode, PaiMemory
from app.services.pai import PAI_AGENT_NAME

CHANNEL = "channel/pai-counselor"


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _event(db, workspace_id, source, content, ts, event_id=None, target=CHANNEL):
    import uuid

    record = EventRecord(
        id=event_id or str(uuid.uuid4()),
        network_id=workspace_id,
        type="workspace.message.posted",
        source=source,
        target=target,
        payload={"content": content, "message_type": "chat"},
        metadata_={},
        timestamp=ts,
        visibility="channel",
    )
    db.add(record)
    db.flush()
    return record


def _turn(db, workspace_id, student_text, assistant_text="Noted.", base_ts=1000):
    user = _event(db, workspace_id, "human:student@example.com", student_text, base_ts)
    assistant = _event(
        db, workspace_id, f"openagents:{PAI_AGENT_NAME}", assistant_text, base_ts + 1
    )
    db.commit()
    return user, assistant


def _mock_llm(monkeypatch, payload):
    """Pin the extractor's LLM call to a fixed response."""
    raw = payload if isinstance(payload, str) else json.dumps(payload)

    async def _fake(**kwargs):
        return raw

    monkeypatch.setattr("app.memory.extractor.chat_completion", _fake)
    monkeypatch.setattr("app.config.config.PAI_API_KEY", "test-key", raising=False)
    return raw


async def _run_extract(db, workspace_id, user_event, assistant_event):
    job = BackgroundJobService(db).enqueue(
        JOB_EXTRACT,
        {
            "source_type": "conversation",
            "channel": CHANNEL,
            "user_event_id": user_event.id,
            "assistant_event_id": assistant_event.id,
        },
        workspace_id=workspace_id,
    )
    db.commit()
    result = await run_job(job, db)
    db.commit()
    return result


# ---------------------------------------------------------------------------
# 1. The hook: one job per turn, PAI only
# ---------------------------------------------------------------------------

def test_completed_turn_enqueues_exactly_one_job(db_session, workspace):
    user, assistant = _turn(db_session, workspace.id, "My CGPA is 3.52.")
    job_id = enqueue_turn_extraction(
        db=db_session, workspace_id=workspace.id, channel_target=CHANNEL,
        user_event_id=user.id, assistant_event_id=assistant.id,
        agent_name=PAI_AGENT_NAME,
    )

    assert job_id
    jobs = db_session.query(BackgroundJob).filter(
        BackgroundJob.job_type == JOB_EXTRACT
    ).all()
    assert len(jobs) == 1
    # Only identifiers — no conversation blob in the row.
    assert jobs[0].payload["user_event_id"] == user.id
    assert "content" not in jobs[0].payload


def test_duplicate_hook_does_not_create_a_second_job(db_session, workspace):
    """A retried invocation or a doubled hook must be a no-op."""
    user, assistant = _turn(db_session, workspace.id, "My CGPA is 3.52.")
    args = dict(
        db=db_session, workspace_id=workspace.id, channel_target=CHANNEL,
        user_event_id=user.id, assistant_event_id=assistant.id,
        agent_name=PAI_AGENT_NAME,
    )
    first = enqueue_turn_extraction(**args)
    second = enqueue_turn_extraction(**args)

    assert first == second
    assert db_session.query(BackgroundJob).filter(
        BackgroundJob.job_type == JOB_EXTRACT
    ).count() == 1


def test_turn_without_a_user_event_is_skipped(db_session, workspace):
    """No student message means no evidence — nothing to extract."""
    assert enqueue_turn_extraction(
        db=db_session, workspace_id=workspace.id, channel_target=CHANNEL,
        user_event_id=None, assistant_event_id="evt-x", agent_name=PAI_AGENT_NAME,
    ) is None
    assert db_session.query(BackgroundJob).count() == 0


def test_hook_failure_never_raises(db_session, workspace, monkeypatch):
    """Memory formation must not surface as an error on a delivered reply."""
    monkeypatch.setattr(
        "app.memory.turn_hook.BackgroundJobService",
        lambda db: (_ for _ in ()).throw(RuntimeError("db down")),
    )
    assert enqueue_turn_extraction(
        db=db_session, workspace_id=workspace.id, channel_target=CHANNEL,
        user_event_id="u1", assistant_event_id="a1", agent_name=PAI_AGENT_NAME,
    ) is None


def test_only_pai_counselor_is_hooked():
    """Ordinary cloud agents must not feed the student's memory.

    The guard lives at the call site in `_invoke_assistant_agent`; this asserts
    the condition it uses.
    """
    import inspect

    from app.services import cloud_agent

    source = inspect.getsource(cloud_agent._invoke_assistant_agent)
    assert "enqueue_turn_extraction" in source
    assert "agent_name == pai.PAI_AGENT_NAME" in source


def test_chat_path_does_not_extract_synchronously():
    """Extraction must not be reachable from the response path.

    `_invoke_assistant_agent` may enqueue, but must never call the extractor
    or the reconciler itself.
    """
    import inspect

    from app.services import cloud_agent

    source = inspect.getsource(cloud_agent)
    assert "extract_candidates" not in source
    assert "MemoryReconciler" not in source
    # And the enqueue happens after the reply is posted.
    assistant = inspect.getsource(cloud_agent._invoke_assistant_agent)
    assert assistant.index("_post_response") < assistant.index("enqueue_turn_extraction")


# ---------------------------------------------------------------------------
# 2. Source events are loaded durably
# ---------------------------------------------------------------------------

def test_context_is_loaded_from_postgres_by_id(db_session, workspace, seed_fields):
    user, assistant = _turn(
        db_session, workspace.id, "My CGPA is 3.52.", "Got it, 3.52.",
    )
    turn = build_turn_context(
        db_session, workspace.id, user.id, assistant.id, channel=CHANNEL,
    )
    assert turn.user_text == "My CGPA is 3.52."
    assert turn.assistant_text == "Got it, 3.52."
    assert turn.user_event_id == user.id


def test_missing_source_event_yields_no_context(db_session, workspace):
    assert build_turn_context(db_session, workspace.id, "does-not-exist") is None


def test_context_cannot_cross_workspaces(db_session, workspace, other_workspace):
    """An event id from another student must not resolve."""
    user, _ = _turn(db_session, workspace.id, "My CGPA is 3.52.")
    assert build_turn_context(db_session, other_workspace.id, user.id) is None


def test_recent_window_is_bounded(db_session, workspace, seed_fields):
    """Not the Counselor's 100-message window."""
    from app.memory.extraction_context import RECENT_TURN_COUNT

    for i in range(20):
        _event(db_session, workspace.id, "human:student@example.com", f"msg {i}", 100 + i)
    user, assistant = _turn(db_session, workspace.id, "Latest.", base_ts=500)

    turn = build_turn_context(db_session, workspace.id, user.id, assistant.id, CHANNEL)
    assert len(turn.recent) <= RECENT_TURN_COUNT


@pytest.mark.asyncio
async def test_extract_job_reads_events_not_payload(db_session, workspace, seed_fields, monkeypatch):
    """The job row carries IDs; the worker fetches the text."""
    user, assistant = _turn(db_session, workspace.id, "I want Germany as my first preference.")
    captured = {}

    async def _fake(**kwargs):
        captured["prompt"] = kwargs["messages"][0]["content"]
        return json.dumps({"candidates": []})

    monkeypatch.setattr("app.memory.extractor.chat_completion", _fake)
    monkeypatch.setattr("app.config.config.PAI_API_KEY", "test-key", raising=False)

    await _run_extract(db_session, workspace.id, user, assistant)
    assert "I want Germany as my first preference." in captured["prompt"]


@pytest.mark.asyncio
async def test_prompt_lists_the_exact_vault_keys_the_filter_allows(
    db_session, workspace, seed_fields, monkeypatch,
):
    """The allow-list must be SHOWN to the model, not only enforced after it.

    `_validate` drops any vault_fact whose key is not in the allowed set. If
    the prompt never names those keys, the model guesses (`cgpa`, `ielts`),
    every proposal is dropped, and the Vault silently never populates from
    conversation — the failure is invisible because the job still succeeds.
    """
    from app.memory.field_definitions import VaultFieldDefinitionService

    user, assistant = _turn(db_session, workspace.id, "My CGPA is 3.52 and IELTS is 7.5.")
    captured = {}

    async def _fake(**kwargs):
        captured["prompt"] = kwargs["messages"][0]["content"]
        return json.dumps({"candidates": []})

    monkeypatch.setattr("app.memory.extractor.chat_completion", _fake)
    monkeypatch.setattr("app.config.config.PAI_API_KEY", "test-key", raising=False)

    await _run_extract(db_session, workspace.id, user, assistant)

    allowed = VaultFieldDefinitionService(db_session).keys()
    assert allowed, "fixture should seed at least one vault field"
    for key in allowed:
        assert key in captured["prompt"], f"{key} is accepted but never shown to the model"


@pytest.mark.asyncio
async def test_prompt_declares_value_shape_for_structured_fields(
    db_session, workspace, seed_fields, monkeypatch,
):
    """A right key with a wrong-shaped value is rejected one layer later, so
    the declared type travels with the key."""
    from app.memory.field_definitions import VaultFieldDefinitionService

    user, assistant = _turn(db_session, workspace.id, "My budget is 20000 EUR per year.")
    captured = {}

    async def _fake(**kwargs):
        captured["prompt"] = kwargs["messages"][0]["content"]
        return json.dumps({"candidates": []})

    monkeypatch.setattr("app.memory.extractor.chat_completion", _fake)
    monkeypatch.setattr("app.config.config.PAI_API_KEY", "test-key", raising=False)

    await _run_extract(db_session, workspace.id, user, assistant)

    for definition in VaultFieldDefinitionService(db_session).list_definitions():
        if definition.data_type:
            assert definition.data_type in captured["prompt"]
            break


# ---------------------------------------------------------------------------
# 3. What gets extracted — and what must not
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_greeting_creates_no_candidate(db_session, workspace, seed_fields, monkeypatch):
    user, assistant = _turn(db_session, workspace.id, "hi there, thanks!", "Hello!")
    _mock_llm(monkeypatch, {"candidates": []})

    result = await _run_extract(db_session, workspace.id, user, assistant)
    assert result["candidates_proposed"] == 0
    assert db_session.query(MemoryCandidate).count() == 0


@pytest.mark.asyncio
async def test_explicit_correction_creates_a_vault_candidate(
    db_session, workspace, seed_fields, monkeypatch,
):
    """"Actually my final CGPA is 3.52" -> a Vault candidate for 3.52."""
    VaultService(db_session).apply_fact(
        workspace_id=workspace.id, field_key="education.cgpa", value=3.41,
        source_type="conversation", confidence=0.8,
    )
    db_session.commit()

    user, assistant = _turn(
        db_session, workspace.id, "Actually my final CGPA is 3.52.", "Updated.",
    )
    _mock_llm(monkeypatch, {"candidates": [{
        "candidate_type": "vault_fact", "operation": "upsert",
        "key": "education.cgpa", "proposed_value": 3.52, "confidence": 0.95,
        "quote": "Actually my final CGPA is 3.52.",
    }]})

    await _run_extract(db_session, workspace.id, user, assistant)

    candidate = db_session.query(MemoryCandidate).one()
    assert candidate.candidate_type == "vault_fact"
    assert candidate.proposed_value == {"value": 3.52}
    assert candidate.source_type == "conversation"
    # Evidence points at the user event, not the assistant's.
    assert candidate.evidence["user_event_id"] == user.id

    # The deterministic reconciler — not the LLM — applies the replacement.
    MemoryReconciler(db_session).reconcile(candidate)
    db_session.commit()
    assert VaultService(db_session).get_fact(
        workspace.id, "education.cgpa"
    ).value["value"] == 3.52


@pytest.mark.asyncio
async def test_assistant_claim_does_not_become_student_truth(
    db_session, workspace, seed_fields, monkeypatch,
):
    """Requirement 7, enforced rather than requested.

    Student hedges; PAI overstates. A candidate quoting the ASSISTANT is
    dropped because the quote is not in the student's message.
    """
    user, assistant = _turn(
        db_session, workspace.id,
        "Maybe Germany would work.",
        "Germany is definitely your top choice.",
    )
    _mock_llm(monkeypatch, {"candidates": [{
        "candidate_type": "semantic_memory", "operation": "upsert",
        "content": "Germany is definitely the student's top choice.",
        "memory_type": "preference", "confidence": 0.9,
        "quote": "Germany is definitely your top choice.",   # assistant's words
    }]})

    result = await _run_extract(db_session, workspace.id, user, assistant)
    assert result["candidates_proposed"] == 0
    assert db_session.query(MemoryCandidate).count() == 0


@pytest.mark.asyncio
async def test_preference_creates_a_semantic_candidate(
    db_session, workspace, seed_fields, monkeypatch,
):
    user, assistant = _turn(
        db_session, workspace.id, "I want Germany as my first preference.",
    )
    _mock_llm(monkeypatch, {"candidates": [{
        "candidate_type": "semantic_memory", "operation": "upsert",
        "content": "Wants Germany as first-preference destination.",
        "memory_type": "preference", "confidence": 0.9,
        "quote": "I want Germany as my first preference.",
    }]})

    await _run_extract(db_session, workspace.id, user, assistant)
    candidate = db_session.query(MemoryCandidate).one()
    assert candidate.candidate_type == "semantic_memory"
    assert candidate.entities["memory_type"] == "preference"


@pytest.mark.asyncio
async def test_decision_creates_an_episode_candidate(
    db_session, workspace, seed_fields, monkeypatch,
):
    user, assistant = _turn(
        db_session, workspace.id,
        "I removed University X because the tuition was too high.",
    )
    _mock_llm(monkeypatch, {"candidates": [{
        "candidate_type": "episode", "operation": "upsert",
        "content": "Removed University X — tuition exceeded budget.",
        "event_type": "shortlist_removed", "confidence": 0.9,
        "quote": "I removed University X because the tuition was too high.",
    }]})

    await _run_extract(db_session, workspace.id, user, assistant)
    candidate = db_session.query(MemoryCandidate).one()
    assert candidate.candidate_type == "episode"
    assert candidate.entities["event_type"] == "shortlist_removed"


@pytest.mark.asyncio
async def test_valid_siblings_survive_an_invalid_candidate(
    db_session, workspace, seed_fields, monkeypatch,
):
    """One bad proposal must not discard a good one from the same turn."""
    user, assistant = _turn(
        db_session, workspace.id, "My CGPA is 3.52 and I want Germany.",
    )
    _mock_llm(monkeypatch, {"candidates": [
        {"candidate_type": "vault_fact", "operation": "upsert",
         "key": "not.a.real.field", "proposed_value": 1, "confidence": 0.9,
         "quote": "My CGPA is 3.52 and I want Germany."},
        {"candidate_type": "semantic_memory", "operation": "upsert",
         "content": "Wants to study in Germany.", "memory_type": "preference",
         "confidence": 0.9, "quote": "My CGPA is 3.52 and I want Germany."},
    ]})

    result = await _run_extract(db_session, workspace.id, user, assistant)
    assert result["candidates_proposed"] == 1
    assert db_session.query(MemoryCandidate).one().candidate_type == "semantic_memory"


# ---------------------------------------------------------------------------
# 4. Trust boundary
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_model_supplied_source_type_is_ignored(
    db_session, workspace, seed_fields, monkeypatch,
):
    """The model cannot promote itself to `user_explicit`."""
    user, assistant = _turn(db_session, workspace.id, "My CGPA is 3.52.")
    _mock_llm(monkeypatch, {"candidates": [{
        "candidate_type": "vault_fact", "operation": "upsert",
        "key": "education.cgpa", "proposed_value": 3.52, "confidence": 1.0,
        "source_type": "user_explicit",          # forged
        "quote": "My CGPA is 3.52.",
    }]})

    await _run_extract(db_session, workspace.id, user, assistant)
    assert db_session.query(MemoryCandidate).one().source_type == "conversation"


def test_extracted_candidate_has_no_source_type_field():
    """Structural: there is no field for a model to populate."""
    from dataclasses import fields

    from app.memory.extractor import ExtractedCandidate

    assert "source_type" not in {f.name for f in fields(ExtractedCandidate)}


@pytest.mark.asyncio
async def test_extraction_cannot_propose_deletions(
    db_session, workspace, seed_fields, monkeypatch,
):
    """Only the explicit user path may forget or retract."""
    user, assistant = _turn(db_session, workspace.id, "Forget about Canada.")
    _mock_llm(monkeypatch, {"candidates": [{
        "candidate_type": "semantic_memory", "operation": "forget",
        "content": "Canada", "confidence": 0.9, "quote": "Forget about Canada.",
    }]})

    result = await _run_extract(db_session, workspace.id, user, assistant)
    assert result["candidates_proposed"] == 0


# ---------------------------------------------------------------------------
# 5. Malformed output
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("bad", ["", "not json at all", "[]", '{"nope": 1}', '{"candidates": "x"}'])
def test_malformed_output_raises(bad):
    with pytest.raises(ExtractionError):
        _parse_response(bad)


def test_fenced_json_is_accepted():
    """Models wrap JSON in fences despite instructions."""
    assert _parse_response('```json\n{"candidates": []}\n```') == []


@pytest.mark.asyncio
async def test_malformed_output_fails_the_job_for_retry(
    db_session, workspace, seed_fields, monkeypatch,
):
    """Broken output must retry, not write half-trusted rows."""
    user, assistant = _turn(db_session, workspace.id, "My CGPA is 3.52.")
    _mock_llm(monkeypatch, "I think the student said something about grades")

    with pytest.raises(ExtractionError):
        await _run_extract(db_session, workspace.id, user, assistant)
    db_session.rollback()
    assert db_session.query(MemoryCandidate).count() == 0


@pytest.mark.asyncio
async def test_llm_failure_propagates_for_retry(
    db_session, workspace, seed_fields, monkeypatch,
):
    async def _boom(**kwargs):
        raise RuntimeError("provider timeout")

    monkeypatch.setattr("app.memory.extractor.chat_completion", _boom)
    monkeypatch.setattr("app.config.config.PAI_API_KEY", "test-key", raising=False)
    user, assistant = _turn(db_session, workspace.id, "My CGPA is 3.52.")

    with pytest.raises(RuntimeError, match="provider timeout"):
        await _run_extract(db_session, workspace.id, user, assistant)


# ---------------------------------------------------------------------------
# 6. Deduplication
# ---------------------------------------------------------------------------

def test_normalization_ignores_case_punctuation_and_spacing():
    assert normalize_text("Wants  Germany!") == normalize_text("wants germany")
    assert memory_fingerprint("preference", "Wants Germany.") == \
        memory_fingerprint("preference", "wants  germany")


def test_different_statements_have_different_fingerprints():
    assert memory_fingerprint("preference", "Wants Germany") != \
        memory_fingerprint("preference", "Wants Canada")
    # Type is part of the identity.
    assert memory_fingerprint("goal", "Wants Germany") != \
        memory_fingerprint("preference", "Wants Germany")


@pytest.mark.asyncio
async def test_repeated_statement_does_not_duplicate_memory(
    db_session, workspace, seed_fields, monkeypatch,
):
    """The same fact across two turns yields one canonical memory."""
    proposal = {"candidates": [{
        "candidate_type": "semantic_memory", "operation": "upsert",
        "content": "Wants to study in Germany.", "memory_type": "preference",
        "confidence": 0.9, "quote": "I want to study in Germany.",
    }]}

    for i in range(2):
        user, assistant = _turn(
            db_session, workspace.id, "I want to study in Germany.",
            base_ts=1000 + i * 10,
        )
        _mock_llm(monkeypatch, proposal)
        await _run_extract(db_session, workspace.id, user, assistant)

        for job in db_session.query(BackgroundJob).filter(
            BackgroundJob.job_type == JOB_RECONCILE,
            BackgroundJob.status == "pending",
        ).all():
            await run_job(job, db_session)
            job.status = "succeeded"
        db_session.commit()

    assert db_session.query(PaiMemory).filter(PaiMemory.status == "active").count() == 1


@pytest.mark.asyncio
async def test_duplicate_episode_is_skipped(db_session, workspace, seed_fields, monkeypatch):
    from app.memory.episodic import EpisodicMemoryService

    EpisodicMemoryService(db_session).record(
        workspace_id=workspace.id, event_type="shortlist_removed",
        summary="Removed University X.",
    )
    db_session.commit()

    user, assistant = _turn(db_session, workspace.id, "I removed University X.")
    _mock_llm(monkeypatch, {"candidates": [{
        "candidate_type": "episode", "operation": "upsert",
        "content": "Removed University X.", "event_type": "shortlist_removed",
        "confidence": 0.9, "quote": "I removed University X.",
    }]})

    result = await _run_extract(db_session, workspace.id, user, assistant)
    assert result["candidates_proposed"] == 0
    assert db_session.query(PaiEpisode).count() == 1


def test_dedupe_is_workspace_scoped(db_session, workspace, other_workspace):
    """One student's memory must not suppress another's identical one."""
    from app.memory.dedupe import is_duplicate_memory

    MemoryService(db_session).create(
        workspace_id=workspace.id, content="Wants Germany.", memory_type="preference",
    )
    db_session.commit()

    assert is_duplicate_memory(db_session, workspace.id, "preference", "Wants Germany.")
    assert not is_duplicate_memory(
        db_session, other_workspace.id, "preference", "Wants Germany."
    )


# ---------------------------------------------------------------------------
# 7. Isolation and prompt hygiene
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_extraction_is_workspace_isolated(
    db_session, workspace, other_workspace, seed_fields, monkeypatch,
):
    MemoryService(db_session).create(
        workspace_id=other_workspace.id,
        content="OTHER STUDENT SECRET", memory_type="preference",
    )
    db_session.commit()

    user, assistant = _turn(db_session, workspace.id, "My CGPA is 3.52.")
    captured = {}

    async def _fake(**kwargs):
        captured["prompt"] = kwargs["messages"][0]["content"]
        return json.dumps({"candidates": []})

    monkeypatch.setattr("app.memory.extractor.chat_completion", _fake)
    monkeypatch.setattr("app.config.config.PAI_API_KEY", "test-key", raising=False)

    await _run_extract(db_session, workspace.id, user, assistant)
    assert "OTHER STUDENT SECRET" not in captured["prompt"]


def test_prompt_marks_the_assistant_reply_as_non_evidence(db_session, workspace, seed_fields):
    user, assistant = _turn(
        db_session, workspace.id, "My CGPA is 3.52.", "You are a strong candidate.",
    )
    turn = build_turn_context(db_session, workspace.id, user.id, assistant.id, CHANNEL)
    prompt = build_user_prompt(turn)

    assert "the only evidence" in prompt
    assert "never evidence" in prompt


def test_extractor_model_is_configurable(monkeypatch):
    """Extraction can move off Counselor's model without code changes."""
    from app.memory.extractor import _model_config

    monkeypatch.setattr("app.config.config.PAI_API_KEY", "pai-key", raising=False)
    monkeypatch.setattr("app.config.config.PAI_MODEL", "pai-model", raising=False)
    monkeypatch.setattr("app.config.config.MEMORY_EXTRACTOR_MODEL", "", raising=False)
    assert _model_config()[2] == "pai-model"      # falls back

    monkeypatch.setattr(
        "app.config.config.MEMORY_EXTRACTOR_MODEL", "cheap-model", raising=False
    )
    assert _model_config()[2] == "cheap-model"    # overrides
