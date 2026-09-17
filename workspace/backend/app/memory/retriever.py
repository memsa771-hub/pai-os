# -*- coding: utf-8 -*-
"""MemoryRetriever — hybrid retrieval with PostgreSQL as the authority.

    query
      -> hybrid search in Qdrant (dense + BM25, RRF-fused, workspace-filtered)
      -> rerank the small candidate set
      -> re-read canonical rows from PostgreSQL
      -> drop anything missing or inactive
      -> typed results

The last two steps are the point. Qdrant returns *hints*: ids it believes are
relevant. PostgreSQL decides whether each one still exists and is still active.
A forgotten memory whose point has not been deleted yet is therefore invisible
anyway — index deletion is an optimisation, not a correctness requirement.

Degrades rather than fails: no Qdrant, no embeddings, or a Qdrant outage all
fall back to the existing lexical/structured search. A retrieval outage must
never look like "the student has no memories".
"""

import logging
import time
from dataclasses import dataclass, field
from typing import Optional

from app.config import config

from .episodic import EpisodicMemoryService
from .rerank import RerankCandidate, get_reranker
from .semantic import MemoryService

logger = logging.getLogger(__name__)

KIND_SEMANTIC = "semantic_memory"
KIND_EPISODE = "episode"


@dataclass
class RetrievalResult:
    """Canonical rows, plus how they were found."""

    memories: list = field(default_factory=list)      # PaiMemory
    episodes: list = field(default_factory=list)      # PaiEpisode
    # "hybrid" | "lexical_fallback" | "empty"
    mode: str = "hybrid"
    candidates_considered: int = 0
    dropped_stale: int = 0

    def is_empty(self) -> bool:
        return not (self.memories or self.episodes)


class MemoryRetriever:
    def __init__(self, db, index=None, reranker=None):
        self.db = db
        self._index = index
        self._reranker = reranker
        self.memories = MemoryService(db)
        self.episodes = EpisodicMemoryService(db)

    @property
    def index(self):
        if self._index is None:
            from .index import get_memory_index

            self._index = get_memory_index()
        return self._index

    @property
    def reranker(self):
        if self._reranker is None:
            self._reranker = get_reranker()
        return self._reranker

    async def retrieve(
        self,
        workspace_id: str,
        query: str,
        kinds: Optional[tuple[str, ...]] = None,
        memory_types: Optional[tuple[str, ...]] = None,
        limit: Optional[int] = None,
        candidate_limit: Optional[int] = None,
    ) -> RetrievalResult:
        limit = limit or config.MEMORY_RETRIEVAL_LIMIT
        candidate_limit = candidate_limit or config.MEMORY_RETRIEVAL_CANDIDATES
        kinds = kinds or (KIND_SEMANTIC, KIND_EPISODE)

        if not (query or "").strip():
            return self._fallback(workspace_id, "", kinds, memory_types, limit)

        started = time.monotonic()
        filters = {"memory_type": list(memory_types)} if memory_types else None

        try:
            hits = await self.index.search(
                workspace_id=workspace_id, query=query,
                limit=candidate_limit, kinds=kinds, filters=filters,
            )
        except Exception:
            # Includes QdrantUnavailable. An index outage is not a data
            # outage — fall back to lexical search over canonical rows.
            logger.warning(
                "retrieval: index unavailable for workspace=%s — lexical fallback",
                workspace_id, exc_info=True,
            )
            return self._fallback(workspace_id, query, kinds, memory_types, limit)

        if not hits:
            return self._fallback(workspace_id, query, kinds, memory_types, limit)

        reranked = await self.reranker.rerank(
            query,
            [RerankCandidate(
                id=h.id, kind=h.kind, text=h.text or "", score=h.score,
            ) for h in hits if h.id],
            limit,
        )

        # PostgreSQL is the authority. Everything above produced ids.
        result = RetrievalResult(mode="hybrid", candidates_considered=len(hits))
        for candidate in reranked:
            row = self._load_active(workspace_id, candidate)
            if row is None:
                result.dropped_stale += 1
                continue
            (result.episodes if candidate.kind == KIND_EPISODE
             else result.memories).append(row)

        logger.info(
            "retrieval: workspace=%s mode=hybrid candidates=%d returned=%d "
            "dropped_stale=%d elapsed_ms=%d",
            workspace_id, len(hits),
            len(result.memories) + len(result.episodes), result.dropped_stale,
            int((time.monotonic() - started) * 1000),
        )
        if result.is_empty():
            # Every hit was stale — the index is behind, so answer from
            # canonical data rather than returning nothing.
            return self._fallback(workspace_id, query, kinds, memory_types, limit)
        return result

    # -- helpers -----------------------------------------------------------

    def _load_active(self, workspace_id: str, candidate: RerankCandidate):
        """Canonical row if it exists and is active, else None."""
        if candidate.kind == KIND_EPISODE:
            row = self.episodes.get(workspace_id, candidate.id)
        else:
            row = self.memories.get(workspace_id, candidate.id)
        if row is None or row.status != "active":
            return None
        return row

    def _fallback(
        self, workspace_id: str, query: str, kinds, memory_types, limit: int,
    ) -> RetrievalResult:
        """Existing lexical/structured behaviour. Always available."""
        memory_type = memory_types[0] if memory_types and len(memory_types) == 1 else None
        result = RetrievalResult(mode="lexical_fallback" if query else "empty")

        if KIND_SEMANTIC in kinds:
            result.memories = (
                self.memories.search(workspace_id, query, limit=limit, memory_type=memory_type)
                if query else
                self.memories.list_memories(workspace_id, memory_type=memory_type, limit=limit)
            )
        if KIND_EPISODE in kinds:
            result.episodes = (
                self.episodes.search(workspace_id, query, limit=limit)
                if query else
                self.episodes.recent(workspace_id, limit=limit)
            )

        logger.info(
            "retrieval: workspace=%s mode=%s returned=%d",
            workspace_id, result.mode, len(result.memories) + len(result.episodes),
        )
        return result
