# -*- coding: utf-8 -*-
"""MemoryContextService — compact assembly, ref resolution, read gating."""

from app.memory.context import MAX_SEMANTIC_MEMORIES, MemoryContextService
from app.memory.episodic import EpisodicMemoryService
from app.memory.semantic import MemoryService
from app.memory.vault import VaultService


def _populate(db, workspace_id):
    VaultService(db).apply_fact(
        workspace_id=workspace_id, field_key="education.cgpa", value=8.1,
        source_type="user_explicit",
    )
    VaultService(db).apply_fact(
        workspace_id=workspace_id, field_key="finance.budget",
        value={"amount": 30000, "currency": "EUR"}, source_type="user_explicit",
    )
    MemoryService(db).create(
        workspace_id=workspace_id, content="Prefers research-focused universities",
        memory_type="preference",
    )
    EpisodicMemoryService(db).record(
        workspace_id=workspace_id, event_type="shortlist_removed",
        summary="Removed University X — tuition exceeded budget",
    )
    db.flush()


def test_default_context_includes_all_three_memory_kinds(db_session, workspace, seed_fields):
    _populate(db_session, workspace.id)
    context = MemoryContextService(db_session).build_student_context(
        workspace_id=workspace.id, caller="counselor",
    )
    assert context.vault["education.cgpa"] == 8.1
    assert len(context.memories) == 1
    assert len(context.episodes) == 1


def test_sensitive_vault_fields_stay_out_of_context(db_session, workspace, seed_fields):
    """Budget is sensitive, so it must not ride along into every prompt."""
    _populate(db_session, workspace.id)
    context = MemoryContextService(db_session).build_student_context(
        workspace_id=workspace.id, caller="counselor",
    )
    assert "finance.budget" not in context.vault


def test_context_refs_narrow_what_is_returned(db_session, workspace, seed_fields):
    """Operator asks for only what its objective needs."""
    _populate(db_session, workspace.id)
    context = MemoryContextService(db_session).build_student_context(
        workspace_id=workspace.id, context_refs=["vault"], caller="operator",
    )
    assert context.vault
    assert context.memories == []
    assert context.episodes == []
    assert context.resolved_refs == ["vault"]


def test_unknown_refs_are_ignored_not_fatal(db_session, workspace, seed_fields):
    _populate(db_session, workspace.id)
    context = MemoryContextService(db_session).build_student_context(
        workspace_id=workspace.id,
        context_refs=["vault", "nonsense:whatever"], caller="counselor",
    )
    assert context.vault
    assert "nonsense:whatever" not in context.resolved_refs


def test_typed_memory_ref_filters_by_type(db_session, workspace):
    memories = MemoryService(db_session)
    memories.create(workspace_id=workspace.id, content="Wants a research career", memory_type="goal")
    memories.create(workspace_id=workspace.id, content="Prefers small cohorts", memory_type="preference")
    db_session.flush()

    context = MemoryContextService(db_session).build_student_context(
        workspace_id=workspace.id, context_refs=["memory:goal"], caller="counselor",
    )
    assert len(context.memories) == 1
    assert context.memories[0]["type"] == "goal"


def test_context_is_budgeted(db_session, workspace):
    """Never dump the whole database into a prompt."""
    memories = MemoryService(db_session)
    for i in range(MAX_SEMANTIC_MEMORIES + 15):
        memories.create(
            workspace_id=workspace.id, content=f"Preference number {i}",
            memory_type="preference",
        )
    db_session.flush()

    context = MemoryContextService(db_session).build_student_context(
        workspace_id=workspace.id, caller="counselor",
    )
    assert len(context.memories) == MAX_SEMANTIC_MEMORIES


def test_forgotten_memories_never_reach_context(db_session, workspace):
    """The end-to-end guarantee behind "forget X"."""
    memories = MemoryService(db_session)
    memories.create(workspace_id=workspace.id, content="Interested in Canada", memory_type="preference")
    kept = memories.create(workspace_id=workspace.id, content="Interested in Germany", memory_type="preference")
    memories.forget_matching(workspace.id, "Canada")
    db_session.flush()

    context = MemoryContextService(db_session).build_student_context(
        workspace_id=workspace.id, caller="counselor",
    )
    contents = [m["content"] for m in context.memories]
    assert contents == [kept.content]


def test_retracted_vault_facts_never_reach_context(db_session, workspace, seed_fields):
    vault = VaultService(db_session)
    vault.apply_fact(
        workspace_id=workspace.id, field_key="education.cgpa", value=8.1,
        source_type="user_explicit",
    )
    vault.retract_fact(workspace.id, "education.cgpa")
    db_session.flush()

    context = MemoryContextService(db_session).build_student_context(
        workspace_id=workspace.id, caller="counselor",
    )
    assert "education.cgpa" not in context.vault


def test_context_is_workspace_isolated(db_session, workspace, other_workspace, seed_fields):
    _populate(db_session, workspace.id)
    context = MemoryContextService(db_session).build_student_context(
        workspace_id=other_workspace.id, caller="counselor",
    )
    assert context.is_empty()


def test_prompt_block_is_empty_when_nothing_is_known(db_session, workspace):
    """A new student should not get a block of empty headings."""
    context = MemoryContextService(db_session).build_student_context(
        workspace_id=workspace.id, caller="counselor",
    )
    assert context.to_prompt_block() == ""


def test_prompt_block_renders_known_facts(db_session, workspace, seed_fields):
    _populate(db_session, workspace.id)
    block = MemoryContextService(db_session).build_student_context(
        workspace_id=workspace.id, caller="counselor",
    ).to_prompt_block()

    assert "education.cgpa: 8.1" in block
    assert "research-focused" in block
    assert "University X" in block
    assert "30000" not in block           # sensitive, withheld


def test_resolve_refs_returns_fresh_data(db_session, workspace, seed_fields):
    """ExecutionRun stores refs, not payloads — so a run sees current state."""
    service = MemoryContextService(db_session)
    vault = VaultService(db_session)
    vault.apply_fact(
        workspace_id=workspace.id, field_key="education.cgpa", value=7.0,
        source_type="document",
    )
    db_session.flush()
    assert service.resolve_refs(workspace.id, ["vault"]).vault["education.cgpa"] == 7.0

    vault.apply_fact(
        workspace_id=workspace.id, field_key="education.cgpa", value=8.5,
        source_type="user_explicit",
    )
    db_session.flush()
    # Same refs, updated answer.
    assert service.resolve_refs(workspace.id, ["vault"]).vault["education.cgpa"] == 8.5


def test_reads_are_capability_gated(db_session, workspace, seed_fields, monkeypatch):
    """A caller holding no capabilities gets no memory at all.

    Reads are gated as well as writes, so a future low-trust agent cannot pull
    the student's whole profile by calling the context service directly.
    """
    import app.memory.context as context_module

    monkeypatch.setattr(
        context_module, "capabilities_for_agent", lambda agent_name: frozenset()
    )
    _populate(db_session, workspace.id)

    context = MemoryContextService(db_session).build_student_context(
        workspace_id=workspace.id, caller="zero-trust-agent",
    )
    assert context.is_empty()
    assert context.resolved_refs == []


def test_unknown_agents_can_still_read(db_session, workspace, seed_fields):
    """The documented default: unknown agents get read-only, not no access."""
    _populate(db_session, workspace.id)
    context = MemoryContextService(db_session).build_student_context(
        workspace_id=workspace.id, caller="some-future-agent",
    )
    assert context.vault["education.cgpa"] == 8.1
    assert context.memories
