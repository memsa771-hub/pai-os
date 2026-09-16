# -*- coding: utf-8 -*-
"""MemoryIndex — the retrieval-index seam.

A vector index is a *derived* structure here, never the source of truth: it
holds ids and whatever the provider needs to rank them, and canonical rows stay
in PostgreSQL. Losing the index must cost a reindex, never data. That is why
`MemoryRecord` carries an id, text and structured filter fields — and no
vector, no model name, no provider handle.

Phase 2 adds `PgVectorMemoryIndex` / `QdrantMemoryIndex` behind this interface
and fuses their scores with the lexical search in `semantic.py`. The default
`NullMemoryIndex` makes indexing a no-op so nothing has to special-case
"embeddings are not configured yet".
"""

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Optional

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class MemoryRecord:
    """What gets indexed. Provider-neutral by construction."""

    id: str
    workspace_id: str
    # "semantic_memory" | "episode" — the canonical table this came from.
    kind: str
    text: str
    # Structured fields a backend may use for pre-filtering (type, importance,
    # entities). Filtering before ranking is what keeps hybrid retrieval from
    # returning another workspace's rows.
    filters: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class SearchHit:
    id: str
    kind: str
    score: float
    text: Optional[str] = None


class MemoryIndex(ABC):
    """Index/search/delete over memory records.

    `workspace_id` is a required argument on every method rather than an
    optional filter: workspace isolation should be impossible to forget.
    """

    @abstractmethod
    async def index(self, records: list[MemoryRecord]) -> int:
        """Add or replace records. Returns how many were written."""

    @abstractmethod
    async def search(
        self,
        workspace_id: str,
        query: str,
        limit: int = 10,
        kinds: Optional[tuple[str, ...]] = None,
        filters: Optional[dict[str, Any]] = None,
    ) -> list[SearchHit]:
        """Rank records for a query within one workspace."""

    @abstractmethod
    async def delete(self, workspace_id: str, ids: list[str]) -> int:
        """Remove records. Called when memory is forgotten."""


class NullMemoryIndex(MemoryIndex):
    """Default no-op index.

    Accepts writes and returns no hits, so callers need no "is the index
    configured?" branch. Retrieval falls back to the structured and lexical
    paths, which are real and already wired up.
    """

    async def index(self, records: list[MemoryRecord]) -> int:
        logger.debug("NullMemoryIndex.index dropped %d record(s)", len(records))
        return 0

    async def search(
        self,
        workspace_id: str,
        query: str,
        limit: int = 10,
        kinds: Optional[tuple[str, ...]] = None,
        filters: Optional[dict[str, Any]] = None,
    ) -> list[SearchHit]:
        return []

    async def delete(self, workspace_id: str, ids: list[str]) -> int:
        return 0


_index: Optional[MemoryIndex] = None


def get_memory_index() -> MemoryIndex:
    """The process-wide index. Swapped by configuration in Phase 2."""
    global _index
    if _index is None:
        _index = NullMemoryIndex()
    return _index


def set_memory_index(index: MemoryIndex) -> None:
    """Install an implementation (used by tests and by Phase 2 wiring)."""
    global _index
    _index = index
