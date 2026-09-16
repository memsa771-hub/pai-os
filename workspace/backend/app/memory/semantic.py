# -*- coding: utf-8 -*-
"""MemoryService — semantic (long-term preference/goal/constraint) memory.

Canonical text lives in PostgreSQL. Retrieval today is structured + lexical
(`search`); the hybrid dense/sparse/rerank stack described in the plan plugs in
behind `MemoryIndex` (see index.py) without this module changing, because
nothing here knows what an embedding is.

"Forgetting" sets `status='forgotten'` rather than deleting. The student asked
us to stop using it, not to destroy the audit trail — and every read filters on
`status='active'`, so a forgotten memory is invisible either way.
"""

import logging
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import select

from app.models import PaiMemory

logger = logging.getLogger(__name__)

MEMORY_TYPES = ("preference", "goal", "constraint", "interest", "context")


def _now() -> datetime:
    return datetime.now(timezone.utc)


class MemoryService:
    def __init__(self, db):
        self.db = db

    def create(
        self,
        workspace_id: str,
        content: str,
        memory_type: str = "context",
        entities: Optional[dict] = None,
        importance: float = 0.5,
        confidence: float = 1.0,
        source_type: Optional[str] = None,
        source_event_ids: Optional[list] = None,
        metadata: Optional[dict] = None,
        subject_user_id: Optional[str] = None,
    ) -> PaiMemory:
        if memory_type not in MEMORY_TYPES:
            raise ValueError(f"Unknown memory_type: {memory_type}")
        if not (content or "").strip():
            raise ValueError("Memory content cannot be empty")

        memory = PaiMemory(
            workspace_id=workspace_id,
            subject_user_id=subject_user_id,
            memory_type=memory_type,
            content=content.strip(),
            entities=entities,
            importance=importance,
            confidence=confidence,
            source_type=source_type,
            source_event_ids=source_event_ids,
            meta=metadata,
            status="active",
        )
        self.db.add(memory)
        self.db.flush()
        return memory

    def get(self, workspace_id: str, memory_id: str) -> Optional[PaiMemory]:
        """Fetch by id, scoped to the workspace.

        The workspace predicate is not redundant: it is what stops an id
        leaked from one workspace reading a row in another.
        """
        return self.db.execute(
            select(PaiMemory).where(
                PaiMemory.id == memory_id,
                PaiMemory.workspace_id == workspace_id,
            )
        ).scalar_one_or_none()

    def list_memories(
        self,
        workspace_id: str,
        memory_type: Optional[str] = None,
        limit: int = 50,
    ) -> list[PaiMemory]:
        stmt = select(PaiMemory).where(
            PaiMemory.workspace_id == workspace_id,
            PaiMemory.status == "active",
        )
        if memory_type:
            stmt = stmt.where(PaiMemory.memory_type == memory_type)
        return list(self.db.execute(
            stmt.order_by(PaiMemory.importance.desc(), PaiMemory.created_at.desc())
            .limit(limit)
        ).scalars().all())

    def search(
        self, workspace_id: str, query: str, limit: int = 10,
        memory_type: Optional[str] = None,
    ) -> list[PaiMemory]:
        """Lexical search — the sparse half of the eventual hybrid retrieval.

        Deliberately simple and honest about it: a LIKE scan, not a pretend
        semantic search. The dense half arrives via MemoryIndex in Phase 2 and
        fuses with this, rather than replacing it.
        """
        stmt = select(PaiMemory).where(
            PaiMemory.workspace_id == workspace_id,
            PaiMemory.status == "active",
        )
        if memory_type:
            stmt = stmt.where(PaiMemory.memory_type == memory_type)
        term = (query or "").strip()
        if term:
            stmt = stmt.where(PaiMemory.content.ilike(f"%{term}%"))
        return list(self.db.execute(
            stmt.order_by(PaiMemory.importance.desc(), PaiMemory.created_at.desc())
            .limit(limit)
        ).scalars().all())

    def forget(self, workspace_id: str, memory_id: str) -> Optional[PaiMemory]:
        memory = self.get(workspace_id, memory_id)
        if memory is None:
            return None
        memory.status = "forgotten"
        memory.valid_until = _now()
        self.db.flush()
        return memory

    def forget_matching(
        self, workspace_id: str, query: str, memory_type: Optional[str] = None,
    ) -> list[PaiMemory]:
        """Forget every active memory matching a term.

        Backs "forget Canada as a preference", where the student names a topic
        rather than an id.
        """
        matches = self.search(workspace_id, query, limit=100, memory_type=memory_type)
        for memory in matches:
            memory.status = "forgotten"
            memory.valid_until = _now()
        if matches:
            self.db.flush()
        return matches

    @staticmethod
    def to_dict(memory: PaiMemory) -> dict[str, Any]:
        return {
            "id": memory.id,
            "type": memory.memory_type,
            "content": memory.content,
            "entities": memory.entities or {},
            "importance": memory.importance,
            "confidence": memory.confidence,
        }
