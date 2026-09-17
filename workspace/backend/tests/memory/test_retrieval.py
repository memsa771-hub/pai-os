# -*- coding: utf-8 -*-
"""Indexing + hybrid retrieval.

Mocked embedding/index components so ranking behaviour is deterministic. Real
Qdrant is exercised separately in tests/test_memory_qdrant_integration.py.
"""

import pytest

from app.jobs.service import BackgroundJobService, run_job
from app.memory.dedupe import (
    episode_fingerprint,
    is_duplicate_episode,
    is_duplicate_memory,
    memory_fingerprint,
)
from app.memory.embeddings import EmbeddingResult, NullEmbeddingProvider, set_embedding_provider
from app.memory.episodic import EpisodicMemoryService
from app.memory.handlers import JOB_EMBED, JOB_REINDEX, JOB_UNINDEX
from app.memory.index import MemoryIndex, MemoryRecord, NullMemoryIndex, SearchHit, set_memory_index
from app.memory.index_qdrant import point_id_for
from app.memory.rerank import IdentityReranker, ImportanceReranker, RerankCandidate
from app.memory.retriever import KIND_EPISODE, KIND_SEMANTIC, MemoryRetriever
from app.memory.semantic import MemoryService
from app.models import BackgroundJob, PaiEpisode, PaiMemory


# ---------------------------------------------------------------------------
# fakes
# ---------------------------------------------------------------------------

class FakeIndex(MemoryIndex):
    """In-memory stand-in that enforces the same workspace isolation."""

    def __init__(self, fail: bool = False):
        self.points: dict[str, MemoryRecord] = {}
        self.deleted: list[str] = []
        self.fail = fail
        self.queries: list[str] = []

    async def index(self, records):
        if self.fail:
            raise RuntimeError("qdrant down")
        for record in records:
            # Deterministic id -> upsert, never duplicate.
            self.points[point_id_for(record.workspace_id, record.kind, record.id)] = record
        return len(records)

    async def search(self, workspace_id, query, limit=10, kinds=None, filters=None):
        if self.fail:
            raise RuntimeError("qdrant down")
        self.queries.append(query)
        hits = []
        # Normalize like a real lexical stage would, so trailing punctuation
        # ("Germany.") does not defeat matching and mask a real assertion.
        import re as _re

        def tokens(text: str) -> set[str]:
            return {t for t in _re.split(r"[^\w.]+", text.lower()) if t}

        terms = tokens(query)
        for record in self.points.values():
            if record.workspace_id != workspace_id:
                continue          # isolation, enforced in the fake too
            if kinds and record.kind not in kinds:
                continue
            record_terms = tokens(record.text) | {
                t.rstrip(".") for t in tokens(record.text)
            }
            overlap = len(terms & record_terms)
            if overlap:
                hits.append(SearchHit(
                    id=record.id, kind=record.kind, score=float(overlap),
                    text=record.text,
                ))
        hits.sort(key=lambda h: (-h.score, h.id))
        return hits[:limit]

    async def delete(self, workspace_id, ids):
        for record_id in ids:
            for kind in ("semantic_memory", "episode"):
                self.points.pop(point_id_for(workspace_id, kind, record_id), None)
        self.deleted.extend(ids)
        return len(ids)


@pytest.fixture
def fake_index():
    index = FakeIndex()
    set_memory_index(index)
    yield index
    set_memory_index(NullMemoryIndex())


@pytest.fixture(autouse=True)
def _null_embeddings():
    """Never call a real embedding provider from unit tests."""
    set_embedding_provider(NullEmbeddingProvider())
    yield
    set_embedding_provider(None)


# ---------------------------------------------------------------------------
# 1. Fingerprint dedupe is an indexed lookup
# ---------------------------------------------------------------------------

def test_fingerprint_is_stored_on_create(db_session, workspace):
    memory = MemoryService(db_session).create(
        workspace_id=workspace.id, content="Wants Germany.", memory_type="preference",
    )
    db_session.commit()
    assert memory.fingerprint == memory_fingerprint("preference", "Wants Germany.")


def test_episode_fingerprint_is_stored_on_create(db_session, workspace):
    episode = EpisodicMemoryService(db_session).record(
        workspace_id=workspace.id, event_type="decision_made", summary="Chose Fall 2027.",
    )
    db_session.commit()
    assert episode.fingerprint == episode_fingerprint("decision_made", "Chose Fall 2027.")


def test_duplicate_memory_detected_without_scanning(db_session, workspace):
    """Indexed lookup, not an O(n) fingerprint loop over every row."""
    MemoryService(db_session).create(
        workspace_id=workspace.id, content="Wants Germany.", memory_type="preference",
    )
    db_session.commit()

    assert is_duplicate_memory(db_session, workspace.id, "preference", "wants  germany")
    assert not is_duplicate_memory(db_session, workspace.id, "preference", "Wants Canada.")
    # Type is part of identity.
    assert not is_duplicate_memory(db_session, workspace.id, "goal", "Wants Germany.")


def test_duplicate_episode_detected_without_scanning(db_session, workspace):
    EpisodicMemoryService(db_session).record(
        workspace_id=workspace.id, event_type="shortlist_removed",
        summary="Removed University X.",
    )
    db_session.commit()

    assert is_duplicate_episode(
        db_session, workspace.id, "shortlist_removed", "removed  university x"
    )
    assert not is_duplicate_episode(
        db_session, workspace.id, "shortlist_removed", "Removed University Y."
    )


def test_dedupe_lookup_issues_one_query(db_session, workspace):
    """Guards the regression: 500 rows must not mean 500 fingerprints."""
    service = MemoryService(db_session)
    for i in range(50):
        service.create(
            workspace_id=workspace.id, content=f"Memory number {i}.",
            memory_type="context",
        )
    db_session.commit()

    statements: list[str] = []
    from sqlalchemy import event as sa_event

    def _record(conn, cursor, statement, params, context, executemany):
        statements.append(statement)

    sa_event.listen(db_session.bind, "before_cursor_execute", _record)
    try:
        is_duplicate_memory(db_session, workspace.id, "context", "Memory number 7.")
    finally:
        sa_event.remove(db_session.bind, "before_cursor_execute", _record)

    selects = [s for s in statements if s.strip().upper().startswith("SELECT")]
    assert len(selects) == 1, f"expected a single indexed lookup, got {len(selects)}"


def test_forgotten_memory_does_not_block_a_new_one(db_session, workspace):
    """Dedupe is scoped to ACTIVE rows — re-learning after forgetting works."""
    service = MemoryService(db_session)
    memory = service.create(
        workspace_id=workspace.id, content="Wants Germany.", memory_type="preference",
    )
    db_session.commit()
    service.forget(workspace.id, memory.id)
    db_session.commit()

    assert not is_duplicate_memory(db_session, workspace.id, "preference", "Wants Germany.")


def test_dedupe_is_workspace_scoped(db_session, workspace, other_workspace):
    MemoryService(db_session).create(
        workspace_id=workspace.id, content="Wants Germany.", memory_type="preference",
    )
    db_session.commit()

    assert is_duplicate_memory(db_session, workspace.id, "preference", "Wants Germany.")
    assert not is_duplicate_memory(db_session, other_workspace.id, "preference", "Wants Germany.")


# ---------------------------------------------------------------------------
# 2. Indexing lifecycle
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_semantic_memories_are_indexed(db_session, workspace, fake_index):
    memory = MemoryService(db_session).create(
        workspace_id=workspace.id, content="Wants Germany.", memory_type="preference",
    )
    db_session.commit()

    job = BackgroundJobService(db_session).enqueue(
        JOB_EMBED, {"memory_ids": [memory.id]}, workspace_id=workspace.id,
    )
    db_session.commit()
    result = await run_job(job, db_session)

    assert result["indexed"] == 1
    assert len(fake_index.points) == 1


@pytest.mark.asyncio
async def test_episodes_are_indexed(db_session, workspace, fake_index):
    """Episodes were previously never indexed at all."""
    episode = EpisodicMemoryService(db_session).record(
        workspace_id=workspace.id, event_type="shortlist_removed",
        summary="Removed University X.",
    )
    db_session.commit()

    job = BackgroundJobService(db_session).enqueue(
        JOB_EMBED, {"episode_ids": [episode.id]}, workspace_id=workspace.id,
    )
    db_session.commit()
    result = await run_job(job, db_session)

    assert result["indexed"] == 1
    assert next(iter(fake_index.points.values())).kind == KIND_EPISODE


@pytest.mark.asyncio
async def test_indexing_is_idempotent_on_retry(db_session, workspace, fake_index):
    """Deterministic point ids: re-running upserts, never duplicates."""
    memory = MemoryService(db_session).create(
        workspace_id=workspace.id, content="Wants Germany.", memory_type="preference",
    )
    db_session.commit()

    for _ in range(3):
        job = BackgroundJobService(db_session).enqueue(
            JOB_EMBED, {"memory_ids": [memory.id]}, workspace_id=workspace.id,
        )
        db_session.commit()
        await run_job(job, db_session)

    assert len(fake_index.points) == 1


@pytest.mark.asyncio
async def test_inactive_rows_are_not_indexed(db_session, workspace, fake_index):
    service = MemoryService(db_session)
    memory = service.create(
        workspace_id=workspace.id, content="Wants Germany.", memory_type="preference",
    )
    db_session.commit()
    service.forget(workspace.id, memory.id)
    db_session.commit()

    job = BackgroundJobService(db_session).enqueue(
        JOB_EMBED, {"memory_ids": [memory.id]}, workspace_id=workspace.id,
    )
    db_session.commit()
    assert (await run_job(job, db_session))["indexed"] == 0


def test_forgetting_enqueues_an_unindex_job(db_session, workspace):
    service = MemoryService(db_session)
    memory = service.create(
        workspace_id=workspace.id, content="Wants Germany.", memory_type="preference",
    )
    db_session.commit()
    service.forget(workspace.id, memory.id)
    db_session.commit()

    assert db_session.query(BackgroundJob).filter(
        BackgroundJob.job_type == JOB_UNINDEX
    ).count() == 1


@pytest.mark.asyncio
async def test_reindex_rebuilds_from_postgres(db_session, workspace, fake_index):
    """The recovery path after losing Qdrant entirely."""
    memories = MemoryService(db_session)
    episodes = EpisodicMemoryService(db_session)
    for i in range(3):
        memories.create(
            workspace_id=workspace.id, content=f"Preference {i}.", memory_type="preference",
        )
    episodes.record(
        workspace_id=workspace.id, event_type="decision_made", summary="Chose Fall 2027.",
    )
    db_session.commit()

    fake_index.points.clear()          # simulate a lost collection

    job = BackgroundJobService(db_session).enqueue(
        JOB_REINDEX, {}, workspace_id=workspace.id,
    )
    db_session.commit()
    result = await run_job(job, db_session)

    assert result["memories"] == 3 and result["episodes"] == 1
    assert len(fake_index.points) == 4


@pytest.mark.asyncio
async def test_index_outage_does_not_corrupt_canonical_memory(db_session, workspace):
    """A Qdrant outage fails the job; the memory stays safe in PostgreSQL."""
    set_memory_index(FakeIndex(fail=True))
    try:
        memory = MemoryService(db_session).create(
            workspace_id=workspace.id, content="Wants Germany.", memory_type="preference",
        )
        db_session.commit()

        job = BackgroundJobService(db_session).enqueue(
            JOB_EMBED, {"memory_ids": [memory.id]}, workspace_id=workspace.id,
        )
        db_session.commit()
        with pytest.raises(RuntimeError, match="qdrant down"):
            await run_job(job, db_session)
        db_session.rollback()

        assert db_session.query(PaiMemory).filter(PaiMemory.status == "active").count() == 1
    finally:
        set_memory_index(NullMemoryIndex())


# ---------------------------------------------------------------------------
# 3. Retrieval
# ---------------------------------------------------------------------------

async def _index_all(db, workspace_id, index):
    from app.memory.handlers import _records_for

    memory_ids = [m.id for m in MemoryService(db).list_memories(workspace_id, limit=100)]
    episode_ids = [e.id for e in EpisodicMemoryService(db).recent(workspace_id, limit=100)]
    await index.index(_records_for(db, workspace_id, memory_ids, episode_ids))


@pytest.mark.asyncio
async def test_retrieval_returns_canonical_rows(db_session, workspace, fake_index):
    MemoryService(db_session).create(
        workspace_id=workspace.id, content="Wants Germany for masters.",
        memory_type="preference",
    )
    db_session.commit()
    await _index_all(db_session, workspace.id, fake_index)

    result = await MemoryRetriever(db_session, index=fake_index).retrieve(
        workspace.id, "Germany",
    )
    assert result.mode == "hybrid"
    assert len(result.memories) == 1
    assert isinstance(result.memories[0], PaiMemory)


@pytest.mark.asyncio
async def test_retrieval_finds_exact_identifier_terms(db_session, workspace, fake_index):
    """Sparse/lexical is why "IELTS 7.5" is findable at all."""
    MemoryService(db_session).create(
        workspace_id=workspace.id, content="Scored IELTS 7.5 overall.",
        memory_type="context",
    )
    MemoryService(db_session).create(
        workspace_id=workspace.id, content="Interested in TU Munich.",
        memory_type="interest",
    )
    db_session.commit()
    await _index_all(db_session, workspace.id, fake_index)

    retriever = MemoryRetriever(db_session, index=fake_index)
    assert "7.5" in (await retriever.retrieve(workspace.id, "7.5")).memories[0].content
    assert "Munich" in (await retriever.retrieve(workspace.id, "Munich")).memories[0].content


@pytest.mark.asyncio
async def test_retrieval_covers_both_kinds(db_session, workspace, fake_index):
    MemoryService(db_session).create(
        workspace_id=workspace.id, content="Wants Germany.", memory_type="preference",
    )
    EpisodicMemoryService(db_session).record(
        workspace_id=workspace.id, event_type="decision_made",
        summary="Chose Germany over Canada.",
    )
    db_session.commit()
    await _index_all(db_session, workspace.id, fake_index)

    result = await MemoryRetriever(db_session, index=fake_index).retrieve(
        workspace.id, "Germany",
    )
    assert result.memories and result.episodes


@pytest.mark.asyncio
async def test_kinds_filter_is_honoured(db_session, workspace, fake_index):
    MemoryService(db_session).create(
        workspace_id=workspace.id, content="Wants Germany.", memory_type="preference",
    )
    EpisodicMemoryService(db_session).record(
        workspace_id=workspace.id, event_type="decision_made", summary="Chose Germany.",
    )
    db_session.commit()
    await _index_all(db_session, workspace.id, fake_index)

    result = await MemoryRetriever(db_session, index=fake_index).retrieve(
        workspace.id, "Germany", kinds=(KIND_SEMANTIC,),
    )
    assert result.memories and not result.episodes


@pytest.mark.asyncio
async def test_stale_index_hits_are_dropped(db_session, workspace, fake_index):
    """PostgreSQL is the authority.

    A memory forgotten AFTER indexing must not surface, even while its point
    is still in the index.
    """
    service = MemoryService(db_session)
    memory = service.create(
        workspace_id=workspace.id, content="Wants Germany.", memory_type="preference",
    )
    db_session.commit()
    await _index_all(db_session, workspace.id, fake_index)

    # Forget canonically, leaving the index point behind.
    memory.status = "forgotten"
    db_session.commit()
    assert len(fake_index.points) == 1          # index still holds it

    result = await MemoryRetriever(db_session, index=fake_index).retrieve(
        workspace.id, "Germany",
    )
    assert all(m.id != memory.id for m in result.memories)


@pytest.mark.asyncio
async def test_deleted_row_hit_is_dropped(db_session, workspace, fake_index):
    memory = MemoryService(db_session).create(
        workspace_id=workspace.id, content="Wants Germany.", memory_type="preference",
    )
    db_session.commit()
    await _index_all(db_session, workspace.id, fake_index)

    db_session.delete(memory)
    db_session.commit()

    result = await MemoryRetriever(db_session, index=fake_index).retrieve(
        workspace.id, "Germany",
    )
    assert not result.memories


@pytest.mark.asyncio
async def test_retrieval_cannot_cross_workspaces(
    db_session, workspace, other_workspace, fake_index,
):
    MemoryService(db_session).create(
        workspace_id=other_workspace.id, content="Wants Germany secretly.",
        memory_type="preference",
    )
    db_session.commit()
    await _index_all(db_session, other_workspace.id, fake_index)

    result = await MemoryRetriever(db_session, index=fake_index).retrieve(
        workspace.id, "Germany",
    )
    assert result.is_empty() or all(
        m.workspace_id == workspace.id for m in result.memories
    )


@pytest.mark.asyncio
async def test_index_outage_falls_back_to_lexical(db_session, workspace):
    """Retrieval degrades; it must never look like "no memories"."""
    MemoryService(db_session).create(
        workspace_id=workspace.id, content="Wants Germany.", memory_type="preference",
    )
    db_session.commit()

    result = await MemoryRetriever(db_session, index=FakeIndex(fail=True)).retrieve(
        workspace.id, "Germany",
    )
    assert result.mode == "lexical_fallback"
    assert len(result.memories) == 1


@pytest.mark.asyncio
async def test_no_query_uses_the_fallback_path(db_session, workspace, fake_index):
    MemoryService(db_session).create(
        workspace_id=workspace.id, content="Wants Germany.", memory_type="preference",
    )
    db_session.commit()

    result = await MemoryRetriever(db_session, index=fake_index).retrieve(workspace.id, "")
    assert result.mode == "empty"
    assert len(result.memories) == 1
    assert fake_index.queries == []          # index never consulted


# ---------------------------------------------------------------------------
# 4. Fusion / rerank determinism
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_fusion_ordering_is_deterministic(db_session, workspace, fake_index):
    service = MemoryService(db_session)
    for i in range(5):
        service.create(
            workspace_id=workspace.id, content=f"Germany option {i}.",
            memory_type="preference",
        )
    db_session.commit()
    await _index_all(db_session, workspace.id, fake_index)

    retriever = MemoryRetriever(db_session, index=fake_index)
    runs = [
        [m.id for m in (await retriever.retrieve(workspace.id, "Germany")).memories]
        for _ in range(3)
    ]
    assert runs[0] == runs[1] == runs[2]


@pytest.mark.asyncio
async def test_identity_reranker_preserves_order():
    candidates = [
        RerankCandidate(id=str(i), kind=KIND_SEMANTIC, text=f"m{i}", score=1.0)
        for i in range(5)
    ]
    out = await IdentityReranker().rerank("q", candidates, limit=3)
    assert [c.id for c in out] == ["0", "1", "2"]


@pytest.mark.asyncio
async def test_importance_reranker_is_deterministic():
    candidates = [
        RerankCandidate(id="a", kind=KIND_SEMANTIC, text="x", score=1.0, importance=0.1),
        RerankCandidate(id="b", kind=KIND_SEMANTIC, text="y", score=0.9, importance=0.9),
    ]
    reranker = ImportanceReranker()
    first = await reranker.rerank("q", candidates, limit=2)
    second = await reranker.rerank("q", candidates, limit=2)
    assert [c.id for c in first] == [c.id for c in second]


@pytest.mark.asyncio
async def test_candidate_pool_is_wider_than_the_result(db_session, workspace, fake_index):
    """Retrieve many, return few — never rerank the whole database."""
    service = MemoryService(db_session)
    for i in range(30):
        service.create(
            workspace_id=workspace.id, content=f"Germany note {i}.",
            memory_type="context",
        )
    db_session.commit()
    await _index_all(db_session, workspace.id, fake_index)

    result = await MemoryRetriever(db_session, index=fake_index).retrieve(
        workspace.id, "Germany", limit=5, candidate_limit=20,
    )
    assert len(result.memories) <= 5
    assert result.candidates_considered > len(result.memories)


# ---------------------------------------------------------------------------
# 5. Point ids
# ---------------------------------------------------------------------------

def test_point_ids_are_deterministic_and_scoped():
    a = point_id_for("ws-1", KIND_SEMANTIC, "mem-1")
    assert a == point_id_for("ws-1", KIND_SEMANTIC, "mem-1")      # stable
    assert a != point_id_for("ws-2", KIND_SEMANTIC, "mem-1")      # per workspace
    assert a != point_id_for("ws-1", KIND_EPISODE, "mem-1")       # per kind


# ---------------------------------------------------------------------------
# 6. Context service integration
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_context_uses_hybrid_retrieval_when_a_query_is_present(
    db_session, workspace, seed_fields, fake_index,
):
    from app.memory.context import MemoryContextService

    MemoryService(db_session).create(
        workspace_id=workspace.id, content="Wants Germany for masters.",
        memory_type="preference",
    )
    db_session.commit()
    await _index_all(db_session, workspace.id, fake_index)

    context = await MemoryContextService(db_session).build_student_context_async(
        workspace_id=workspace.id, query="Germany", caller="counselor",
    )
    assert fake_index.queries, "hybrid retrieval was not used"
    assert context.memories


@pytest.mark.asyncio
async def test_context_without_a_query_skips_the_index(
    db_session, workspace, seed_fields, fake_index,
):
    from app.memory.context import MemoryContextService

    MemoryService(db_session).create(
        workspace_id=workspace.id, content="Wants Germany.", memory_type="preference",
    )
    db_session.commit()

    context = await MemoryContextService(db_session).build_student_context_async(
        workspace_id=workspace.id, caller="counselor",
    )
    assert fake_index.queries == []
    assert context.memories


@pytest.mark.asyncio
async def test_context_still_gates_on_capabilities(
    db_session, workspace, seed_fields, fake_index, monkeypatch,
):
    """Hybrid retrieval must not become a way around the capability gate."""
    import app.memory.context as context_module

    MemoryService(db_session).create(
        workspace_id=workspace.id, content="Wants Germany.", memory_type="preference",
    )
    db_session.commit()
    await _index_all(db_session, workspace.id, fake_index)

    monkeypatch.setattr(
        context_module, "capabilities_for_agent", lambda name: frozenset()
    )
    context = await context_module.MemoryContextService(db_session).build_student_context_async(
        workspace_id=workspace.id, query="Germany", caller="zero-trust",
    )
    assert context.is_empty()


def test_sync_context_path_still_works(db_session, workspace, seed_fields):
    """Operator's existing integration must be unaffected."""
    from app.memory.context import MemoryContextService

    MemoryService(db_session).create(
        workspace_id=workspace.id, content="Wants Germany.", memory_type="preference",
    )
    db_session.commit()

    context = MemoryContextService(db_session).resolve_refs(
        workspace.id, ["vault", "memory"], caller="operator",
    )
    assert context.memories
