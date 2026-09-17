# -*- coding: utf-8 -*-
"""Provider-neutral embedding boundary.

`MemoryIndex` and the memory services never see a provider. They ask for
vectors; this module decides who produces them.

Two deliberate choices:

**No blind credential fallback.** A chat-model key/endpoint does not
necessarily serve an embeddings route — a gateway that proxies
`/chat/completions` may 404 on `/embeddings`. Falling back to `PAI_API_KEY`
unconditionally would turn a config mistake into a failing job on every
indexed memory. We fall back only when the PAI endpoint is explicitly
OpenAI-compatible, and degrade to `NullEmbeddingProvider` otherwise.

**Versioned metadata.** Every provider reports `model_id` and `dimensions`,
stored with each indexed point. A model change is then detectable rather than
silently mixing incompatible vector spaces, and `memory.reindex` can rebuild.
"""

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional

from app.config import config

logger = logging.getLogger(__name__)

# Endpoints known to expose an OpenAI-compatible /embeddings route. Used only
# to decide whether reusing PAI credentials is safe.
_OPENAI_COMPATIBLE_HOSTS = ("api.openai.com",)


@dataclass(frozen=True)
class EmbeddingResult:
    """Dense vectors plus the metadata needed to detect a model change."""

    vectors: list[list[float]]
    model_id: str
    dimensions: int


class EmbeddingProvider(ABC):
    """Turns text into dense vectors."""

    @property
    @abstractmethod
    def model_id(self) -> str:
        """Stable identifier stored with indexed points, e.g. 'openai:model'."""

    @property
    @abstractmethod
    def dimensions(self) -> int:
        ...

    @property
    def available(self) -> bool:
        """False when unconfigured — callers degrade instead of failing."""
        return True

    @abstractmethod
    async def embed_documents(self, texts: list[str]) -> EmbeddingResult:
        ...

    @abstractmethod
    async def embed_query(self, text: str) -> list[float]:
        ...


class NullEmbeddingProvider(EmbeddingProvider):
    """No embeddings configured. Indexing no-ops; retrieval falls back."""

    @property
    def model_id(self) -> str:
        return "null"

    @property
    def dimensions(self) -> int:
        return 0

    @property
    def available(self) -> bool:
        return False

    async def embed_documents(self, texts: list[str]) -> EmbeddingResult:
        return EmbeddingResult(vectors=[], model_id="null", dimensions=0)

    async def embed_query(self, text: str) -> list[float]:
        return []


class OpenAIEmbeddingProvider(EmbeddingProvider):
    """OpenAI-compatible `/embeddings`. Works with any compatible gateway."""

    def __init__(self, api_key: str, model: str, dimensions: int, base_url: Optional[str] = None):
        self._api_key = api_key
        self._model = model
        self._dimensions = dimensions
        self._base_url = base_url

    @property
    def model_id(self) -> str:
        return f"openai:{self._model}"

    @property
    def dimensions(self) -> int:
        return self._dimensions

    @property
    def available(self) -> bool:
        return bool(self._api_key)

    def _client(self):
        from openai import AsyncOpenAI

        kwargs = {"api_key": self._api_key}
        if self._base_url:
            kwargs["base_url"] = self._base_url
        return AsyncOpenAI(**kwargs)

    async def embed_documents(self, texts: list[str]) -> EmbeddingResult:
        """Embed texts, closing the client before returning.

        The client is created per call and closed in a `finally`, so it never
        outlives the event loop that owns it. Foreground retrieval runs inside
        short-lived `asyncio.run()` loops, and an AsyncOpenAI client left open
        there would have its async HTTP resources torn down with the loop
        rather than released cleanly.

        Deliberately NOT cached on the instance: this provider is a
        process-wide singleton, so a cached client would be shared across
        independent foreground loops — the same cross-loop ownership bug
        already fixed for the Qdrant client.
        """
        if not texts:
            return EmbeddingResult([], self.model_id, self._dimensions)

        client = self._client()
        try:
            response = await client.embeddings.create(model=self._model, input=texts)
        finally:
            # Closed on the error path too: a provider outage must not leak a
            # client per failed indexing job.
            await client.close()

        vectors = [item.embedding for item in response.data]
        actual = len(vectors[0]) if vectors else self._dimensions
        if vectors and actual != self._dimensions:
            # Surfaced rather than silently accepted: a mismatch means the
            # collection was created for a different vector space and every
            # upsert would be rejected (or worse, silently wrong).
            logger.warning(
                "embeddings: model %s returned dim=%d, configured dim=%d",
                self._model, actual, self._dimensions,
            )
        return EmbeddingResult(vectors, self.model_id, actual)

    async def embed_query(self, text: str) -> list[float]:
        result = await self.embed_documents([text])
        return result.vectors[0] if result.vectors else []


def _is_openai_compatible(base_url: str) -> bool:
    if not base_url:
        return True          # no base_url == the real OpenAI API
    return any(host in base_url for host in _OPENAI_COMPATIBLE_HOSTS)


def resolve_config() -> tuple[str, str, str, str, int]:
    """(provider, api_key, model, base_url, dimensions) for embeddings.

    Prefers the dedicated `MEMORY_EMBEDDING_*` settings. Reuses PAI credentials
    ONLY when the PAI endpoint is a known OpenAI-compatible one — otherwise
    returns an empty key so the caller degrades to Null rather than calling an
    endpoint that may not serve embeddings.
    """
    provider = (config.MEMORY_EMBEDDING_PROVIDER or "openai").lower()
    model = config.MEMORY_EMBEDDING_MODEL
    dimensions = config.MEMORY_EMBEDDING_DIM

    api_key = config.MEMORY_EMBEDDING_API_KEY
    base_url = config.MEMORY_EMBEDDING_BASE_URL

    if not api_key:
        pai_base = config.PAI_BASE_URL or ""
        if _is_openai_compatible(pai_base):
            api_key = config.PAI_API_KEY
            base_url = base_url or pai_base
        else:
            logger.info(
                "embeddings: no MEMORY_EMBEDDING_API_KEY and PAI endpoint is not "
                "known OpenAI-compatible — embeddings disabled"
            )
    return provider, api_key, model, base_url, dimensions


_provider: Optional[EmbeddingProvider] = None


def get_embedding_provider() -> EmbeddingProvider:
    global _provider
    if _provider is None:
        provider, api_key, model, base_url, dimensions = resolve_config()
        if provider == "openai" and api_key:
            _provider = OpenAIEmbeddingProvider(api_key, model, dimensions, base_url or None)
        else:
            _provider = NullEmbeddingProvider()
    return _provider


def set_embedding_provider(provider: Optional[EmbeddingProvider]) -> None:
    """Install a provider (tests, and future runtime reconfiguration)."""
    global _provider
    _provider = provider
