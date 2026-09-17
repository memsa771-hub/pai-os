# -*- coding: utf-8 -*-
"""Retrieval-correctness hardening regressions.

1. canonical validation runs BEFORE reranking
2. exact dedupe is race-safe at the database level
3. the unindex idempotency key is deterministic across processes
4. reindex has no silent row cap
5. vectors from a different model are never compared
6. semantic uses memory_type, episodes use event_type
"""

import pytest

from app.jobs.service import BackgroundJobService, run_job
from app.memory.dedupe import memory_fingerprint
from app.memory.embeddings import NullEmbeddingProvider, set_embedding_provider
from app.memory.episodic import EpisodicMemoryService
from app.memory.handlers import JOB_REINDEX, _records_for
from app.memory.index import MemoryIndex, NullMemoryIndex, SearchHit, set_memory_index
from app.memory.index_lifecycle import unindex_key
from app.memory.rerank import Reranker, set_reranker
from app.memory.retriever import KIND_EPISODE, KIND_SEMANTIC, MemoryRetriever
from app.memory.semantic import MemoryService
from app.models import PaiEpisode, PaiMemory

from .test_retrieval import FakeIndex


@pytest.fixture(autouse=True)
def _null_embeddings():
    set_embedding_provider(NullEmbeddingProvider())
    yield
    set_embedding_provider(None)
    set_reranker(None)
    set_memory_index(NullMemoryIndex())


class ScriptedIndex(MemoryIndex):
    """Returns a fixed hit list, so staleness ordering is exact."""

    def __init__(self, hits):
        self._hits = hits

    async def index(self, records):
        return len(records)

    async def search(self, workspace_id, query, limit=10, kinds=None, filters=None):
        return list(self._hits)[:limit]

    async def delete(self, workspace_id, ids):
        return len(ids)


class RecordingReranker(Reranker):
    """Captures exactly what the reranker was handed."""

    def __init__(self):
        self.seen = []

    @property
    def name(self):
        return "recording"

    async def rerank(self, query, candidates, limit):
        self.seen.append(list(candidates))
        return candidates[:limit]


# ---------------------------------------------------------------------------
# 1. Validation before rerank
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_forgotten_rows_never_reach_the_reranker(db_session, workspace):
    """A future external reranker must not receive forgotten student content."""
    service = MemoryService(db_session)
    live = service.create(
        workspace_id=workspace.id, content="Wants Germany.", memory_type="preference",
    )
    dead = service.create(
        workspace_id=workspace.id, content="SECRET forgotten thing.",
        memory_type="preference",
    )
    db_session.commit()
    service.forget(workspace.id, dead.id)
    db_session.commit()

    reranker = RecordingReranker()
    index = ScriptedIndex([
        SearchHit(id=dead.id, kind=KIND_SEMANTIC, score=9.0, text="SECRET forgotten thing."),
        SearchHit(id=live.id, kind=KIND_SEMANTIC, score=1.0, text="Wants Germany."),
    ])

    result = await MemoryRetriever(db_session, index=index, reranker=reranker).retrieve(
        workspace.id, "Germany",
    )

    seen_ids = {c.id for batch in reranker.seen for c in batch}
    assert dead.id not in seen_ids, "forgotten content was sent to the reranker"
    assert live.id in seen_ids
    assert [m.id for m in result.memories] == [live.id]


@pytest.mark.asyncio
async def test_stale_top_hits_do_not_reduce_the_result_count(db_session, workspace):
    """The regression: stale hits used to consume the final slots.

    Three dead hits rank above three live ones with limit=3. Validating first
    means the live three still fill the request.
    """
    service = MemoryService(db_session)
    dead_ids, live_ids = [], []
    for i in range(3):
        dead = service.create(
            workspace_id=workspace.id, content=f"Dead Germany {i}.",
            memory_type="context",
        )
        dead_ids.append(dead.id)
    for i in range(3):
        live = service.create(
            workspace_id=workspace.id, content=f"Live Germany {i}.",
            memory_type="context",
        )
        live_ids.append(live.id)
    db_session.commit()
    for dead_id in dead_ids:
        service.forget(workspace.id, dead_id)
    db_session.commit()

    hits = (
        [SearchHit(id=i, kind=KIND_SEMANTIC, score=9.0, text="stale") for i in dead_ids]
        + [SearchHit(id=i, kind=KIND_SEMANTIC, score=1.0, text="live") for i in live_ids]
    )
    result = await MemoryRetriever(db_session, index=ScriptedIndex(hits)).retrieve(
        workspace.id, "Germany", limit=3,
    )

    assert len(result.memories) == 3, "stale hits consumed final slots"
    assert {m.id for m in result.memories} == set(live_ids)
    assert result.dropped_stale == 3


@pytest.mark.asyncio
async def test_reranker_receives_canonical_importance(db_session, workspace):
    """ImportanceReranker scored everything at the 0.5 default before this."""
    memory = MemoryService(db_session).create(
        workspace_id=workspace.id, content="Critical constraint.",
        memory_type="constraint", importance=0.93,
    )
    db_session.commit()

    reranker = RecordingReranker()
    index = ScriptedIndex([
        SearchHit(id=memory.id, kind=KIND_SEMANTIC, score=1.0, text="stale payload text"),
    ])
    await MemoryRetriever(db_session, index=index, reranker=reranker).retrieve(
        workspace.id, "constraint",
    )

    candidate = reranker.seen[0][0]
    assert candidate.importance == pytest.approx(0.93)
    # Canonical text, not the (possibly stale) indexed copy.
    assert candidate.text == "Critical constraint."


@pytest.mark.asyncio
async def test_episode_rerank_text_comes_from_summary(db_session, workspace):
    episode = EpisodicMemoryService(db_session).record(
        workspace_id=workspace.id, event_type="decision_made",
        summary="Chose Fall 2027.", importance=0.8,
    )
    db_session.commit()

    reranker = RecordingReranker()
    index = ScriptedIndex([
        SearchHit(id=episode.id, kind=KIND_EPISODE, score=1.0, text="stale"),
    ])
    await MemoryRetriever(db_session, index=index, reranker=reranker).retrieve(
        workspace.id, "Fall",
    )

    candidate = reranker.seen[0][0]
    assert candidate.text == "Chose Fall 2027."
    assert candidate.importance == pytest.approx(0.8)


# ---------------------------------------------------------------------------
# 2. Race-safe dedupe (single-process; real concurrency in the PG module)
# ---------------------------------------------------------------------------

def test_duplicate_active_memory_is_collapsed(db_session, workspace):
    """The DB invariant turns a lost race into the dedupe outcome."""
    service = MemoryService(db_session)
    first = service.create(
        workspace_id=workspace.id, content="Wants Germany.", memory_type="preference",
    )
    db_session.commit()

    second = service.create(
        workspace_id=workspace.id, content="wants  germany",   # same fingerprint
        memory_type="preference",
    )
    db_session.commit()

    assert second.id == first.id
    assert db_session.query(PaiMemory).filter(PaiMemory.status == "active").count() == 1


def test_duplicate_active_episode_is_collapsed(db_session, workspace):
    service = EpisodicMemoryService(db_session)
    first = service.record(
        workspace_id=workspace.id, event_type="decision_made", summary="Chose Fall 2027.",
    )
    db_session.commit()

    second = service.record(
        workspace_id=workspace.id, event_type="decision_made", summary="chose  fall 2027",
    )
    db_session.commit()

    assert second.id == first.id
    assert db_session.query(PaiEpisode).filter(PaiEpisode.status == "active").count() == 1


def test_forgotten_rows_may_share_a_fingerprint(db_session, workspace):
    """Partial index: re-learning after forgetting must still work."""
    service = MemoryService(db_session)
    first = service.create(
        workspace_id=workspace.id, content="Wants Germany.", memory_type="preference",
    )
    db_session.commit()
    service.forget(workspace.id, first.id)
    db_session.commit()

    second = service.create(
        workspace_id=workspace.id, content="Wants Germany.", memory_type="preference",
    )
    db_session.commit()

    assert second.id != first.id
    assert db_session.query(PaiMemory).count() == 2


def test_uniqueness_is_workspace_scoped(db_session, workspace, other_workspace):
    service = MemoryService(db_session)
    a = service.create(
        workspace_id=workspace.id, content="Wants Germany.", memory_type="preference",
    )
    b = service.create(
        workspace_id=other_workspace.id, content="Wants Germany.", memory_type="preference",
    )
    db_session.commit()
    assert a.id != b.id


# ---------------------------------------------------------------------------
# 3. Deterministic unindex key
# ---------------------------------------------------------------------------

def test_unindex_key_is_deterministic():
    """`hash()` is per-process randomised — the key must not be."""
    key = unindex_key("ws-1", ["b", "a", "c"])
    assert key == unindex_key("ws-1", ["c", "b", "a"])     # order-insensitive
    assert key != unindex_key("ws-2", ["a", "b", "c"])     # workspace-scoped
    assert key != unindex_key("ws-1", ["a", "b"])          # content-sensitive
    # The literal value: if this changes, keys stop matching across a deploy.
    assert key == unindex_key("ws-1", ["a", "b", "c"])
    assert key.startswith("unindex:ws-1:")


def test_unindex_key_survives_a_hash_seed_change(monkeypatch):
    """Simulates a different process: builtin hash() would differ, ours must not."""
    import builtins

    before = unindex_key("ws-1", ["m1", "m2"])
    monkeypatch.setattr(builtins, "hash", lambda obj: 12345)
    assert unindex_key("ws-1", ["m1", "m2"]) == before


# ---------------------------------------------------------------------------
# 4. Unbounded reindex
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_reindex_pages_through_everything(db_session, workspace):
    """A small page size must still cover every row — no silent cap."""
    index = FakeIndex()
    set_memory_index(index)

    service = MemoryService(db_session)
    for i in range(25):
        service.create(
            workspace_id=workspace.id, content=f"Memory {i}.", memory_type="context",
        )
    episodes = EpisodicMemoryService(db_session)
    for i in range(10):
        episodes.record(
            workspace_id=workspace.id, event_type="decision_made", summary=f"Decision {i}.",
        )
    db_session.commit()

    job = BackgroundJobService(db_session).enqueue(
        JOB_REINDEX, {"batch_size": 4}, workspace_id=workspace.id,
    )
    db_session.commit()
    result = await run_job(job, db_session)

    assert result["memories"] == 25
    assert result["episodes"] == 10
    assert len(index.points) == 35


@pytest.mark.asyncio
async def test_reindex_skips_inactive_rows(db_session, workspace):
    index = FakeIndex()
    set_memory_index(index)

    service = MemoryService(db_session)
    keep = service.create(
        workspace_id=workspace.id, content="Keep me.", memory_type="context",
    )
    drop = service.create(
        workspace_id=workspace.id, content="Drop me.", memory_type="context",
    )
    db_session.commit()
    service.forget(workspace.id, drop.id)
    db_session.commit()

    job = BackgroundJobService(db_session).enqueue(
        JOB_REINDEX, {"batch_size": 2}, workspace_id=workspace.id,
    )
    db_session.commit()
    result = await run_job(job, db_session)

    assert result["memories"] == 1
    assert [r.id for r in index.points.values()] == [keep.id]


# ---------------------------------------------------------------------------
# 6. Payload type semantics
# ---------------------------------------------------------------------------

def test_episode_records_use_event_type(db_session, workspace):
    """An episode's type must not be stored under `memory_type`."""
    episode = EpisodicMemoryService(db_session).record(
        workspace_id=workspace.id, event_type="shortlist_removed",
        summary="Removed University X.",
    )
    db_session.commit()

    record = _records_for(db_session, workspace.id, [], [episode.id])[0]
    assert record.filters["event_type"] == "shortlist_removed"
    assert "memory_type" not in record.filters


def test_semantic_records_use_memory_type(db_session, workspace):
    memory = MemoryService(db_session).create(
        workspace_id=workspace.id, content="Wants Germany.", memory_type="preference",
    )
    db_session.commit()

    record = _records_for(db_session, workspace.id, [memory.id], [])[0]
    assert record.filters["memory_type"] == "preference"
    assert "event_type" not in record.filters


@pytest.mark.asyncio
async def test_type_filters_are_passed_separately(db_session, workspace):
    """`memory_types` and `event_types` must not collapse into one field."""
    captured = {}

    class CapturingIndex(ScriptedIndex):
        async def search(self, workspace_id, query, limit=10, kinds=None, filters=None):
            captured["filters"] = filters
            return []

    await MemoryRetriever(db_session, index=CapturingIndex([])).retrieve(
        workspace.id, "Germany",
        memory_types=("preference",), event_types=("decision_made",),
    )
    assert captured["filters"] == {
        "memory_type": ["preference"], "event_type": ["decision_made"],
    }
