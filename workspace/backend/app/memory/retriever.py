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
from typing import Any, Optional

from app.config import config

from .episodic import EpisodicMemoryService
from .rerank import RerankCandidate, get_reranker
from .semantic import MemoryService

logger = logging.getLogger(__name__)

KIND_SEMANTIC = "semantic_memory"
KIND_EPISODE = "episode"


@dataclass(frozen=True)
class RetrievedItem:
    """One result, with its position in the GLOBAL ranking."""

    kind: str          # KIND_SEMANTIC | KIND_EPISODE
    record: Any        # PaiMemory | PaiEpisode
    rank: int          # 0-based position across both kinds
    score: float = 0.0

    @property
    def id(self) -> str:
        return self.record.id

    @property
    def text(self) -> str:
        return (
            self.record.summary if self.kind == KIND_EPISODE else self.record.content
        ) or ""


@dataclass
class RetrievalResult:
    """Canonical rows, plus how they were found.

    `ordered` is the real ranking. `memories`/`episodes` are per-kind views of
    the same rows, kept for existing callers — but splitting by kind DESTROYS
    the global order, so anything rank-sensitive (MRR, Recall@k for k below
    the total) must read `ordered`. Reconstructing order as
    `memories + episodes` silently reports every episode as ranking below
    every memory.
    """

    ordered: list = field(default_factory=list)       # RetrievedItem
    memories: list = field(default_factory=list)      # PaiMemory
    episodes: list = field(default_factory=list)      # PaiEpisode
    # "hybrid" | "lexical_fallback" | "empty"
    mode: str = "hybrid"
    candidates_considered: int = 0
    dropped_stale: int = 0

    def is_empty(self) -> bool:
        return not (self.memories or self.episodes)

    def add(self, kind: str, record, score: float = 0.0) -> None:
        """Append preserving global rank and keeping the per-kind views."""
        self.ordered.append(
            RetrievedItem(kind=kind, record=record, rank=len(self.ordered), score=score)
        )
        (self.episodes if kind == KIND_EPISODE else self.memories).append(record)


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
        event_types: Optional[tuple[str, ...]] = None,
        limit: Optional[int] = None,
        candidate_limit: Optional[int] = None,
    ) -> RetrievalResult:
        limit = limit or config.MEMORY_RETRIEVAL_LIMIT
        candidate_limit = candidate_limit or config.MEMORY_RETRIEVAL_CANDIDATES
        kinds = kinds or (KIND_SEMANTIC, KIND_EPISODE)

        if not (query or "").strip():
            return self._fallback(workspace_id, "", kinds, memory_types, limit, event_types)

        started = time.monotonic()
        # Separate fields per kind: an episode's `event_type` is not a
        # `memory_type` and storing it under that name made the two
        # indistinguishable in a filter.
        filters: dict[str, Any] = {}
        if memory_types:
            filters["memory_type"] = list(memory_types)
        if event_types:
            filters["event_type"] = list(event_types)

        try:
            hits = await self.index.search(
                workspace_id=workspace_id, query=query,
                limit=candidate_limit, kinds=kinds, filters=filters or None,
            )
        except Exception:
            # Includes QdrantUnavailable. An index outage is not a data
            # outage — fall back to lexical search over canonical rows.
            logger.warning(
                "retrieval: index unavailable for workspace=%s — lexical fallback",
                workspace_id, exc_info=True,
            )
            return self._fallback(workspace_id, query, kinds, memory_types, limit, event_types)

        if not hits:
            return self._fallback(workspace_id, query, kinds, memory_types, limit, event_types)

        # VALIDATE FIRST, then rerank. Three reasons the order matters:
        #
        #   1. Reranking before validation lets stale hits consume final slots
        #      — three dead points at the top of a 3-slot request returned
        #      nothing, while valid hits sat just below the cut.
        #   2. A future external reranker would otherwise be sent forgotten
        #      student content over the network.
        #   3. Rerank inputs (text, importance) must come from canonical rows,
        #      not index payload. `ImportanceReranker` was previously scoring
        #      every candidate at the 0.5 default because nothing supplied it.
        result = RetrievalResult(mode="hybrid", candidates_considered=len(hits))
        candidates: list[RerankCandidate] = []
        rows_by_id: dict[tuple[str, str], Any] = {}

        for hit in hits:
            if not hit.id:
                continue
            row = self._load_active(workspace_id, hit.id, hit.kind)
            if row is None:
                result.dropped_stale += 1
                continue
            rows_by_id[(hit.kind, hit.id)] = row
            candidates.append(RerankCandidate(
                id=hit.id, kind=hit.kind,
                # Canonical text/importance, never the indexed copy — payload
                # can lag an edit, and importance is not on the payload at all
                # for older points.
                text=(row.summary if hit.kind == KIND_EPISODE else row.content) or "",
                score=hit.score,
                importance=float(getattr(row, "importance", 0.5) or 0.5),
            ))

        reranked = await self.reranker.rerank(query, candidates, limit)
        for candidate in reranked:
            row = rows_by_id.get((candidate.kind, candidate.id))
            if row is None:
                continue
            # Rank is assigned in reranked order, across both kinds.
            result.add(candidate.kind, row, candidate.score)

        logger.info(
            "retrieval: workspace=%s mode=hybrid candidates=%d validated=%d "
            "returned=%d dropped_stale=%d reranker=%s elapsed_ms=%d",
            workspace_id, len(hits), len(candidates),
            len(result.memories) + len(result.episodes), result.dropped_stale,
            self.reranker.name, int((time.monotonic() - started) * 1000),
        )
        if result.is_empty():
            # Every hit was stale — the index is behind, so answer from
            # canonical data rather than returning nothing.
            return self._fallback(workspace_id, query, kinds, memory_types, limit, event_types)
        return result

    # -- helpers -----------------------------------------------------------

    def _load_active(self, workspace_id: str, record_id: str, kind: str):
        """Canonical row if it exists and is active, else None."""
        row = (
            self.episodes.get(workspace_id, record_id) if kind == KIND_EPISODE
            else self.memories.get(workspace_id, record_id)
        )
        if row is None or row.status != "active":
            return None
        return row

    def _fallback(
        self, workspace_id: str, query: str, kinds, memory_types, limit: int,
        event_types=None,
    ) -> RetrievalResult:
        """Degraded lexical/structured retrieval. Always available.

        Filters identically to the hybrid path. Previously this dropped
        `event_types` entirely and applied only the FIRST of several
        `memory_types`, so the same query silently meant something different
        whenever Qdrant was down — the worst kind of fallback, because the
        results still look plausible.
        """
        result = RetrievalResult(mode="lexical_fallback" if query else "empty")

        if KIND_SEMANTIC in kinds:
            for row in (
                self.memories.search(
                    workspace_id, query, limit=limit, memory_types=memory_types,
                )
                if query else
                self.memories.list_memories(
                    workspace_id, limit=limit, memory_types=memory_types,
                )
            ):
                result.add(KIND_SEMANTIC, row)
        if KIND_EPISODE in kinds:
            for row in (
                self.episodes.search(
                    workspace_id, query, limit=limit, event_types=event_types,
                )
                if query else
                self.episodes.recent(
                    workspace_id, limit=limit, event_types=event_types,
                )
            ):
                result.add(KIND_EPISODE, row)

        logger.info(
            "retrieval: workspace=%s mode=%s returned=%d",
            workspace_id, result.mode, len(result.memories) + len(result.episodes),
        )
        return result
