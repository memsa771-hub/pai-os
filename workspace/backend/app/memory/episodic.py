# -*- coding: utf-8 -*-
"""EpisodicMemoryService — things that happened, in time order.

Separate from semantic memory because the useful query is different. Semantic
memory answers "what is this student like?" and is retrieved by similarity;
episodes answer "what has happened lately?" and are retrieved by recency,
importance and event type. Merging them into one table would force one
retrieval strategy onto both.
"""

import logging
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.models import PaiEpisode

from .dedupe import episode_fingerprint
from .index_lifecycle import enqueue_unindex

logger = logging.getLogger(__name__)


def _now() -> datetime:
    return datetime.now(timezone.utc)


class EpisodicMemoryService:
    def __init__(self, db):
        self.db = db

    def record(
        self,
        workspace_id: str,
        event_type: str,
        summary: str,
        entities: Optional[dict] = None,
        importance: float = 0.5,
        occurred_at: Optional[datetime] = None,
        source_event_ids: Optional[list] = None,
        metadata: Optional[dict] = None,
        subject_user_id: Optional[str] = None,
    ) -> PaiEpisode:
        if not (summary or "").strip():
            raise ValueError("Episode summary cannot be empty")
        episode = PaiEpisode(
            workspace_id=workspace_id,
            subject_user_id=subject_user_id,
            event_type=event_type,
            summary=summary.strip(),
            entities=entities,
            importance=importance,
            occurred_at=occurred_at or _now(),
            source_event_ids=source_event_ids,
            meta=metadata,
            fingerprint=episode_fingerprint(event_type, summary.strip()),
            status="active",
        )
        # Savepoint — see MemoryService.create for why a lost race must not
        # roll back the caller's whole transaction.
        try:
            with self.db.begin_nested():
                self.db.add(episode)
                self.db.flush()
        except IntegrityError:
            existing = self.db.execute(
                select(PaiEpisode).where(
                    PaiEpisode.workspace_id == workspace_id,
                    PaiEpisode.status == "active",
                    PaiEpisode.fingerprint == episode.fingerprint,
                ).limit(1)
            ).scalar_one_or_none()
            if existing is None:
                raise
            logger.info(
                "memory: concurrent duplicate episode collapsed workspace=%s type=%s",
                workspace_id, event_type,
            )
            return existing
        return episode

    def get(self, workspace_id: str, episode_id: str) -> Optional[PaiEpisode]:
        return self.db.execute(
            select(PaiEpisode).where(
                PaiEpisode.id == episode_id,
                PaiEpisode.workspace_id == workspace_id,
            )
        ).scalar_one_or_none()

    def recent(
        self,
        workspace_id: str,
        limit: int = 10,
        event_type: Optional[str] = None,
        since: Optional[datetime] = None,
    ) -> list[PaiEpisode]:
        stmt = select(PaiEpisode).where(
            PaiEpisode.workspace_id == workspace_id,
            PaiEpisode.status == "active",
        )
        if event_type:
            stmt = stmt.where(PaiEpisode.event_type == event_type)
        if since is not None:
            stmt = stmt.where(PaiEpisode.occurred_at >= since)
        return list(self.db.execute(
            stmt.order_by(PaiEpisode.occurred_at.desc()).limit(limit)
        ).scalars().all())

    def search(self, workspace_id: str, query: str, limit: int = 10) -> list[PaiEpisode]:
        stmt = select(PaiEpisode).where(
            PaiEpisode.workspace_id == workspace_id,
            PaiEpisode.status == "active",
        )
        term = (query or "").strip()
        if term:
            stmt = stmt.where(PaiEpisode.summary.ilike(f"%{term}%"))
        return list(self.db.execute(
            stmt.order_by(PaiEpisode.occurred_at.desc()).limit(limit)
        ).scalars().all())

    def forget(self, workspace_id: str, episode_id: str) -> Optional[PaiEpisode]:
        episode = self.get(workspace_id, episode_id)
        if episode is None:
            return None
        episode.status = "forgotten"
        self.db.flush()
        enqueue_unindex(self.db, workspace_id, [episode.id])
        return episode

    @staticmethod
    def to_dict(episode: PaiEpisode) -> dict[str, Any]:
        occurred = episode.occurred_at
        return {
            "id": episode.id,
            "event_type": episode.event_type,
            "summary": episode.summary,
            "entities": episode.entities or {},
            "importance": episode.importance,
            "occurred_at": occurred.isoformat() if occurred else None,
        }
