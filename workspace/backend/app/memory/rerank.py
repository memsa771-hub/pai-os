# -*- coding: utf-8 -*-
"""Reranker boundary.

Reranking is a second, more expensive scoring pass over a SMALL candidate set —
never over the memory database. The retriever fetches ~40 candidates by hybrid
search and the reranker orders them down to ~8.

This phase ships the boundary plus a deterministic default rather than a
cross-encoder: a hosted reranking model is a real dependency and a real latency
cost, and there is nothing to evaluate it against until retrieval is running on
real data. The pipeline is fully wired, so adding one later is a config change.

`IdentityReranker` preserves fusion order (RRF already ranked them sensibly).
`ImportanceReranker` applies a small, explainable nudge toward memories the
system marked important — deterministic, so ordering stays testable.
"""

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional

from app.config import config

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RerankCandidate:
    id: str
    kind: str
    text: str
    score: float
    importance: float = 0.5


class Reranker(ABC):
    @property
    @abstractmethod
    def name(self) -> str:
        ...

    @abstractmethod
    async def rerank(
        self, query: str, candidates: list[RerankCandidate], limit: int,
    ) -> list[RerankCandidate]:
        ...


class IdentityReranker(Reranker):
    """Keep fusion order. The honest default."""

    @property
    def name(self) -> str:
        return "identity"

    async def rerank(self, query, candidates, limit):
        return candidates[:limit]


class ImportanceReranker(Reranker):
    """Blend retrieval rank with stored importance.

    Rank-based, not score-based: fused RRF scores are not calibrated, so
    multiplying them by importance would be meaningless. Using reciprocal rank
    keeps the blend on a defined scale.
    """

    def __init__(self, importance_weight: float = 0.3):
        self._weight = importance_weight

    @property
    def name(self) -> str:
        return "importance"

    async def rerank(self, query, candidates, limit):
        def blended(item: tuple[int, RerankCandidate]) -> float:
            rank, candidate = item
            return (1.0 / (60 + rank + 1)) * (1 - self._weight) + (
                candidate.importance * self._weight * 0.01
            )

        ordered = sorted(enumerate(candidates), key=blended, reverse=True)
        return [candidate for _, candidate in ordered][:limit]


_reranker: Optional[Reranker] = None


def get_reranker() -> Reranker:
    global _reranker
    if _reranker is None:
        name = (config.MEMORY_RERANKER or "").lower()
        _reranker = ImportanceReranker() if name == "importance" else IdentityReranker()
    return _reranker


def set_reranker(reranker: Optional[Reranker]) -> None:
    global _reranker
    _reranker = reranker
