# -*- coding: utf-8 -*-
"""MemoryCandidateService — the quarantine between an LLM and the truth.

Extraction proposes; it never commits. Everything an LLM believes about the
student enters as a row here with `status='pending'`, and only the
deterministic reconciler can promote it. The value of the seam is that a
hallucinated CGPA is a rejected candidate row you can inspect, rather than a
corrupted Vault you have to notice.
"""

import logging
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import select

from app.models import MemoryCandidate

logger = logging.getLogger(__name__)

CANDIDATE_TYPES = ("vault_fact", "semantic_memory", "episode")
OPERATIONS = ("upsert", "retract", "forget")


def _now() -> datetime:
    return datetime.now(timezone.utc)


class MemoryCandidateService:
    def __init__(self, db):
        self.db = db

    def propose(
        self,
        workspace_id: str,
        candidate_type: str,
        operation: str = "upsert",
        key: Optional[str] = None,
        proposed_value: Any = None,
        content: Optional[str] = None,
        entities: Optional[dict] = None,
        confidence: float = 0.5,
        source_type: str = "conversation",
        source_event_ids: Optional[list] = None,
        evidence: Optional[dict] = None,
        subject_user_id: Optional[str] = None,
    ) -> MemoryCandidate:
        """Record a proposal. Deliberately cheap and always safe to call.

        Note what this does NOT do: it does not validate against the field
        definition. Validation is the reconciler's job, so an invalid proposal
        is preserved with its rejection reason instead of vanishing — that
        record is how you find a misbehaving extraction prompt.
        """
        if candidate_type not in CANDIDATE_TYPES:
            raise ValueError(f"Unknown candidate_type: {candidate_type}")
        if operation not in OPERATIONS:
            raise ValueError(f"Unknown operation: {operation}")

        candidate = MemoryCandidate(
            workspace_id=workspace_id,
            subject_user_id=subject_user_id,
            candidate_type=candidate_type,
            operation=operation,
            key=key,
            # Wrapped to match VaultFact.value, so the reconciler unwraps once.
            proposed_value=None if proposed_value is None else {"value": proposed_value},
            content=content,
            entities=entities,
            confidence=confidence,
            source_type=source_type,
            source_event_ids=source_event_ids,
            evidence=evidence,
            status="pending",
        )
        self.db.add(candidate)
        self.db.flush()
        return candidate

    def get(self, workspace_id: str, candidate_id: str) -> Optional[MemoryCandidate]:
        return self.db.execute(
            select(MemoryCandidate).where(
                MemoryCandidate.id == candidate_id,
                MemoryCandidate.workspace_id == workspace_id,
            )
        ).scalar_one_or_none()

    def pending(self, workspace_id: str, limit: int = 100) -> list[MemoryCandidate]:
        return list(self.db.execute(
            select(MemoryCandidate)
            .where(
                MemoryCandidate.workspace_id == workspace_id,
                MemoryCandidate.status == "pending",
            )
            .order_by(MemoryCandidate.created_at)
            .limit(limit)
        ).scalars().all())

    def mark_accepted(self, candidate: MemoryCandidate, result_id: Optional[str]) -> MemoryCandidate:
        candidate.status = "accepted"
        candidate.result_id = result_id
        candidate.reconciled_at = _now()
        self.db.flush()
        return candidate

    def mark_rejected(self, candidate: MemoryCandidate, reason: str) -> MemoryCandidate:
        candidate.status = "rejected"
        candidate.rejection_reason = (reason or "")[:1000]
        candidate.reconciled_at = _now()
        self.db.flush()
        return candidate
