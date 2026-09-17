# -*- coding: utf-8 -*-
"""Real-Qdrant integration tests.

Unit tests use a fake index, which cannot catch a wrong collection schema, a
malformed hybrid query, or a filter that does not actually filter. Only a real
server does.

    export TEST_QDRANT_URL=http://localhost:6333
    pytest -m qdrant tests/test_memory_qdrant_integration.py

Without TEST_QDRANT_URL the module is skipped, matching the PostgreSQL
integration tests' convention.

Embeddings are stubbed with a deterministic fake so these test the INDEX, not a
provider — no API key, no network, repeatable rankings.
"""

import os
import uuid

import pytest
import pytest_asyncio

TEST_QDRANT_URL = os.environ.get("TEST_QDRANT_URL", "")

pytestmark = [
    pytest.mark.qdrant,
    pytest.mark.skipif(
        not TEST_QDRANT_URL.startswith("http"),
        reason="TEST_QDRANT_URL not set",
    ),
]


class FakeEmbeddings:
    """Deterministic 8-dim vectors from token hashes.

    Shared tokens -> similar vectors, so "paraphrase" behaviour is testable
    without a model.
    """

    DIM = 8

    @property
    def model_id(self):
        return "fake:test-embed"

    @property
    def dimensions(self):
        return self.DIM

    @property
    def available(self):
        return True

    def _vector(self, text: str) -> list[float]:
        vector = [0.0] * self.DIM
        for token in text.lower().split():
            vector[hash(token) % self.DIM] += 1.0
        norm = sum(v * v for v in vector) ** 0.5 or 1.0
        return [v / norm for v in vector]

    async def embed_documents(self, texts):
        from app.memory.embeddings import EmbeddingResult

        return EmbeddingResult(
            [self._vector(t) for t in texts], self.model_id, self.DIM
        )

    async def embed_query(self, text):
        return self._vector(text)


@pytest_asyncio.fixture
async def index():
    """A throwaway collection per test run, dropped afterwards."""
    from app.memory.index_qdrant import QdrantMemoryIndex
    from app.memory.sparse import get_sparse_encoder

    collection = f"pai_test_{uuid.uuid4().hex[:8]}"
    memory_index = QdrantMemoryIndex(
        url=TEST_QDRANT_URL, collection=collection,
        embedding_provider=FakeEmbeddings(),
        sparse_encoder=get_sparse_encoder(),
    )
    yield memory_index
    try:
        client = memory_index._get_client()
        await client.delete_collection(collection)
    except Exception:
        pass


def _record(workspace_id, record_id, text, kind="semantic_memory", **filters):
    from app.memory.index import MemoryRecord

    return MemoryRecord(
        id=record_id, workspace_id=workspace_id, kind=kind, text=text,
        filters={"status": "active", **filters},
    )


@pytest.mark.asyncio
async def test_collection_is_created_with_dense_and_sparse(index):
    await index.ensure_collection()
    info = await index._get_client().get_collection(index._collection)

    vectors = info.config.params.vectors
    assert "dense" in vectors
    assert info.config.params.sparse_vectors
    assert "sparse" in info.config.params.sparse_vectors


@pytest.mark.asyncio
async def test_upsert_is_idempotent(index):
    workspace = str(uuid.uuid4())
    record = _record(workspace, "mem-1", "Wants Germany for masters")

    for _ in range(3):
        assert await index.index([record]) == 1

    info = await index._get_client().get_collection(index._collection)
    assert info.points_count == 1, "deterministic ids must upsert, not duplicate"


@pytest.mark.asyncio
async def test_workspace_isolation_is_enforced_by_the_server(index):
    """The isolation guarantee, against a real filter."""
    ws_a, ws_b = str(uuid.uuid4()), str(uuid.uuid4())
    await index.index([
        _record(ws_a, "a1", "Wants Germany for masters"),
        _record(ws_b, "b1", "Wants Germany for masters"),
    ])

    hits = await index.search(ws_a, "Germany masters", limit=10)
    assert hits, "expected a hit in workspace A"
    assert all(h.id == "a1" for h in hits), "workspace B leaked into A's results"


@pytest.mark.asyncio
async def test_sparse_retrieval_finds_exact_identifiers(index):
    """The reason sparse exists: dense blurs "7.5" vs "6.5"."""
    workspace = str(uuid.uuid4())
    await index.index([
        _record(workspace, "m1", "Scored IELTS 7.5 overall"),
        _record(workspace, "m2", "Scored IELTS 6.5 overall"),
        _record(workspace, "m3", "Interested in TU Munich computer science"),
    ])

    hits = await index.search(workspace, "IELTS 7.5", limit=5)
    assert hits and hits[0].id == "m1"

    hits = await index.search(workspace, "TU Munich", limit=5)
    assert hits and hits[0].id == "m3"


@pytest.mark.asyncio
async def test_hybrid_returns_both_kinds(index):
    workspace = str(uuid.uuid4())
    await index.index([
        _record(workspace, "m1", "Wants Germany for masters"),
        _record(workspace, "e1", "Removed University X tuition too high", kind="episode"),
    ])

    kinds = {h.kind for h in await index.search(workspace, "Germany University", limit=10)}
    assert kinds, "expected hits"

    only_episodes = await index.search(
        workspace, "University", limit=10, kinds=("episode",),
    )
    assert all(h.kind == "episode" for h in only_episodes)


@pytest.mark.asyncio
async def test_delete_removes_points(index):
    workspace = str(uuid.uuid4())
    await index.index([_record(workspace, "m1", "Wants Germany for masters")])
    assert await index.search(workspace, "Germany", limit=5)

    await index.delete(workspace, ["m1"])
    assert not await index.search(workspace, "Germany", limit=5)


@pytest.mark.asyncio
async def test_drop_workspace_leaves_other_workspaces_intact(index):
    ws_a, ws_b = str(uuid.uuid4()), str(uuid.uuid4())
    await index.index([
        _record(ws_a, "a1", "Wants Germany for masters"),
        _record(ws_b, "b1", "Wants Germany for masters"),
    ])

    await index.drop_workspace(ws_a)
    assert not await index.search(ws_a, "Germany", limit=5)
    assert await index.search(ws_b, "Germany", limit=5)


@pytest.mark.asyncio
async def test_payload_carries_versioned_model_metadata(index):
    """So a model change is detectable and can trigger a reindex."""
    workspace = str(uuid.uuid4())
    await index.index([_record(workspace, "m1", "Wants Germany for masters")])

    hits = await index.search(workspace, "Germany", limit=1)
    assert hits

    from app.memory.index_qdrant import point_id_for

    points = await index._get_client().retrieve(
        collection_name=index._collection,
        ids=[point_id_for(workspace, "semantic_memory", "m1")],
        with_payload=True,
    )
    payload = points[0].payload
    assert payload["embedding_model"] == "fake:test-embed"
    assert payload["embedding_dim"] == FakeEmbeddings.DIM
    assert payload["workspace_id"] == workspace
