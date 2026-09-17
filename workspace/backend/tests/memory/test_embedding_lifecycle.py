# -*- coding: utf-8 -*-
"""AsyncOpenAI client lifecycle in the embedding provider.

Foreground retrieval runs inside short-lived `asyncio.run()` loops, so any
async network client created there must be closed before that loop ends. A
client left open has its HTTP resources torn down with the loop instead of
released, and a per-call leak compounds across indexing jobs.
"""

import pytest

from app.memory.embeddings import EmbeddingResult, OpenAIEmbeddingProvider


class _FakeEmbeddings:
    def __init__(self, client, vectors=None, error=None):
        self._client = client
        self._vectors = vectors
        self._error = error

    async def create(self, model, input):
        self._client.calls.append((model, tuple(input)))
        if self._error is not None:
            raise self._error

        class _Item:
            def __init__(self, embedding):
                self.embedding = embedding

        class _Response:
            def __init__(self, data):
                self.data = data

        vectors = self._vectors or [[0.1, 0.2, 0.3] for _ in input]
        return _Response([_Item(v) for v in vectors])


class FakeAsyncClient:
    """Records close() calls so ownership can be asserted."""

    def __init__(self, vectors=None, error=None):
        self.closed = 0
        self.calls: list = []
        self.embeddings = _FakeEmbeddings(self, vectors, error)

    async def close(self):
        self.closed += 1


def _provider(monkeypatch, client):
    provider = OpenAIEmbeddingProvider(
        api_key="test-key", model="test-model", dimensions=3,
    )
    monkeypatch.setattr(provider, "_client", lambda: client)
    return provider


@pytest.mark.asyncio
async def test_client_is_closed_after_a_successful_embedding(monkeypatch):
    client = FakeAsyncClient()
    provider = _provider(monkeypatch, client)

    result = await provider.embed_documents(["hello", "world"])

    assert isinstance(result, EmbeddingResult)
    assert len(result.vectors) == 2
    assert client.closed == 1, "client was not closed after a successful call"


@pytest.mark.asyncio
async def test_client_is_closed_when_the_provider_raises(monkeypatch):
    """A provider outage must not leak a client per failed job."""
    client = FakeAsyncClient(error=RuntimeError("provider is down"))
    provider = _provider(monkeypatch, client)

    with pytest.raises(RuntimeError, match="provider is down"):
        await provider.embed_documents(["hello"])

    assert client.closed == 1, "client was not closed on the error path"


@pytest.mark.asyncio
async def test_each_call_creates_and_closes_its_own_client(monkeypatch):
    """No client is cached across calls — that is the cross-loop bug."""
    clients = [FakeAsyncClient() for _ in range(3)]
    provider = OpenAIEmbeddingProvider(
        api_key="test-key", model="test-model", dimensions=3,
    )
    monkeypatch.setattr(provider, "_client", lambda: clients.pop(0))

    for _ in range(3):
        await provider.embed_documents(["text"])

    assert clients == [], "a client was reused rather than created per call"


@pytest.mark.asyncio
async def test_embed_query_also_closes_its_client(monkeypatch):
    client = FakeAsyncClient(vectors=[[0.4, 0.5, 0.6]])
    provider = _provider(monkeypatch, client)

    vector = await provider.embed_query("a question")

    assert vector == [0.4, 0.5, 0.6]
    assert client.closed == 1


@pytest.mark.asyncio
async def test_empty_input_creates_no_client(monkeypatch):
    """Nothing to embed means nothing to open or close."""
    created = []

    provider = OpenAIEmbeddingProvider(
        api_key="test-key", model="test-model", dimensions=3,
    )

    def _track():
        client = FakeAsyncClient()
        created.append(client)
        return client

    monkeypatch.setattr(provider, "_client", _track)
    result = await provider.embed_documents([])

    assert result.vectors == []
    assert created == [], "a client was created for an empty request"


@pytest.mark.asyncio
async def test_client_does_not_outlive_its_event_loop(monkeypatch):
    """The property the fix exists for.

    Each short-lived loop must leave no open client behind.
    """
    import asyncio

    clients: list = []

    def _run_once():
        provider = OpenAIEmbeddingProvider(
            api_key="test-key", model="test-model", dimensions=3,
        )
        client = FakeAsyncClient()
        clients.append(client)
        provider._client = lambda: client
        return asyncio.run(provider.embed_documents(["text"]))

    for _ in range(3):
        await asyncio.to_thread(_run_once)

    assert len(clients) == 3
    assert all(c.closed == 1 for c in clients), (
        "a client survived its owning event loop"
    )
