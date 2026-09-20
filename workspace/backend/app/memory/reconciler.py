# -*- coding: utf-8 -*-
"""MemoryReconciler — the only thing that may change canonical memory.

Deterministic on purpose. No LLM call happens in this module: given the same
candidate and the same existing state it always reaches the same decision, so
the behaviour is testable and explainable to a student asking "why does it
think my budget is £20,000?"

    candidate --> [validate] --> [authorise] --> [conflict policy] --> canonical
                      |               |                  |
                   rejected        rejected         retained/superseded

Every outcome is recorded on the candidate row, including rejections.
"""

import logging
from dataclasses import dataclass
from typing import Optional

from app.models import MemoryCandidate
from .candidates import MemoryCandidateService
from .episodic import EpisodicMemoryService
from .errors import MemoryDataError
from .semantic import MemoryService
from .student_records import RecordNeedsReview, StudentRecordService
from .vault import VaultOutcome, VaultService

logger = logging.getLogger(__name__)

# Below this, an inferred candidate is not worth acting on. An explicit user
# instruction bypasses it — see `_min_confidence_for`.
DEFAULT_MIN_CONFIDENCE = 0.35


@dataclass(frozen=True)
class ReconcileResult:
    accepted: bool
    candidate_id: str
    result_id: Optional[str] = None
    reason: Optional[str] = None
    # What happened to canonical state, when the candidate was a vault_fact.
    # `accepted` alone is not enough: a candidate that lost a conflict is
    # neither accepted nor an error, and the caller needs to tell them apart.
    outcome: Optional[str] = None


class MemoryReconciler:
    """Promotes candidates into Vault / semantic / episodic memory."""

    def __init__(self, db):
        self.db = db
        self.candidates = MemoryCandidateService(db)
        self.vault = VaultService(db)
        self.memories = MemoryService(db)
        self.episodes = EpisodicMemoryService(db)
        self.records = StudentRecordService(db)

    # -- entry points ------------------------------------------------------

    def reconcile(self, candidate: MemoryCandidate) -> ReconcileResult:
        """Decide one candidate.

        Rejects bad *data*. Propagates bad *code* and broken infrastructure.

        Only `MemoryDataError` (currently `VaultFieldError`) becomes a
        rejection: those are statements about the candidate, decided
        deterministically. A NameError or an OperationalError says nothing
        about the candidate — swallowing it records a permanent "rejected"
        verdict caused by a bug or an outage, loses the data, and hides the
        failure from the durable job that should have retried it.
        """
        self.db.refresh(candidate, with_for_update=True)
        if candidate.status != "pending":
            return ReconcileResult(False, candidate.id, reason="not_pending")

        try:
            if candidate.candidate_type == "vault_fact":
                return self._reconcile_vault(candidate)
            if candidate.candidate_type == "semantic_memory":
                return self._reconcile_semantic(candidate)
            if candidate.candidate_type == "episode":
                return self._reconcile_episode(candidate)
            if candidate.candidate_type == "student_record":
                return self._reconcile_student_record(candidate)
            return self._reject(candidate, f"unknown candidate_type: {candidate.candidate_type}")
        except RecordNeedsReview as exc:
            candidate.status = "needs_review"
            candidate.rejection_reason = str(exc)
            self.db.flush()
            return ReconcileResult(False, candidate.id, reason="needs_review", outcome="needs_review")
        except MemoryDataError as exc:
            # A statement about the candidate — the expected rejection path.
            return self._reject(candidate, str(exc))

    def reconcile_pending(self, workspace_id: str, limit: int = 100) -> list[ReconcileResult]:
        return [self.reconcile(c) for c in self.candidates.pending(workspace_id, limit)]

    # -- per-type handling -------------------------------------------------

    def _reconcile_student_record(self, candidate: MemoryCandidate) -> ReconcileResult:
        if candidate.operation != "upsert" or not self._confident_enough(candidate):
            return self._reject(candidate, "invalid operation or insufficient confidence")
        values = self._unwrap(candidate.proposed_value)
        if not isinstance(values, dict):
            return self._reject(candidate, "student record requires an object")
        record = self.records.apply(
            candidate.workspace_id, candidate.key or "", values,
            source_type=candidate.source_type,
            claim_origin=("institution_document" if candidate.source_type == "document" else
                          "student" if candidate.source_type in ("conversation", "user_explicit") else
                          "agent_inference"),
            capture_method=("document_extraction" if candidate.source_type == "document" else
                            "conversation_extraction" if candidate.source_type == "conversation" else
                            "explicit_correction" if candidate.source_type == "user_explicit" else
                            "agent_proposal"),
            evidence=candidate.evidence, subject_user_id=candidate.subject_user_id,
            record_id=(candidate.entities or {}).get("record_id"),
            supersedes_record_id=(candidate.entities or {}).get("supersedes_record_id"),
        )
        self.candidates.mark_accepted(candidate, record.id)
        return ReconcileResult(True, candidate.id, result_id=record.id)

    def _reconcile_vault(self, candidate: MemoryCandidate) -> ReconcileResult:
        if not candidate.key:
            return self._reject(candidate, "vault_fact candidate requires a key")
        from .field_definitions import ENTITY_BACKED_LEGACY_FIELDS
        if candidate.key in ENTITY_BACKED_LEGACY_FIELDS:
            return self._reject(candidate, f"{candidate.key} is represented by a typed student record")

        if candidate.operation == "retract":
            count = self.vault.retract_fact(candidate.workspace_id, candidate.key)
            if count == 0:
                return self._reject(candidate, f"no active fact to retract: {candidate.key}")
            self.candidates.mark_accepted(candidate, None)
            return ReconcileResult(True, candidate.id)

        if candidate.operation != "upsert":
            return self._reject(candidate, f"unsupported vault operation: {candidate.operation}")

        if not self._confident_enough(candidate):
            return self._reject(
                candidate,
                f"confidence {candidate.confidence} below threshold for source "
                f"{candidate.source_type}",
            )

        value = self._unwrap(candidate.proposed_value)
        if value is None:
            return self._reject(candidate, "vault_fact upsert requires a proposed_value")

        # Validates against the field definition; raises VaultFieldError, which
        # `reconcile` converts into a recorded rejection.
        result = self.vault.apply_fact(
            workspace_id=candidate.workspace_id,
            field_key=candidate.key,
            value=value,
            source_type=candidate.source_type,
            confidence=candidate.confidence or 1.0,
            source_event_id=(candidate.source_event_ids or [None])[0],
            evidence=candidate.evidence,
            subject_user_id=candidate.subject_user_id,
        )

        # A candidate that lost its conflict, or that needs human confirmation,
        # did NOT become canonical — marking it accepted would claim a write
        # that never happened and hide the losing proposal from review.
        if result.outcome is VaultOutcome.RETAINED:
            self.candidates.mark_rejected(
                candidate,
                f"retained existing value (policy: conflict lost for {candidate.key})",
            )
            return ReconcileResult(
                False, candidate.id, reason="retained",
                outcome=VaultOutcome.RETAINED.value,
            )

        if result.outcome is VaultOutcome.NEEDS_REVIEW:
            # Left `pending` on purpose: this is deferred, not refused. A human
            # or an explicit user confirmation can still promote it later.
            candidate.status = "needs_review"
            candidate.rejection_reason = (
                f"{candidate.key} requires explicit confirmation "
                f"(source: {candidate.source_type})"
            )
            self.db.flush()
            return ReconcileResult(
                False, candidate.id, reason="needs_review",
                outcome=VaultOutcome.NEEDS_REVIEW.value,
            )

        self.candidates.mark_accepted(candidate, result.fact.id)
        return ReconcileResult(
            True, candidate.id, result_id=result.fact.id,
            outcome=result.outcome.value,
        )

    def _reconcile_semantic(self, candidate: MemoryCandidate) -> ReconcileResult:
        if candidate.operation == "forget":
            target = candidate.content or candidate.key or ""
            if not target.strip():
                return self._reject(candidate, "forget requires content or key to match")
            forgotten = self.memories.forget_matching(candidate.workspace_id, target)
            if not forgotten:
                return self._reject(candidate, f"no active memory matching: {target}")
            self.candidates.mark_accepted(candidate, forgotten[0].id)
            return ReconcileResult(True, candidate.id, result_id=forgotten[0].id)

        if candidate.operation != "upsert":
            return self._reject(candidate, f"unsupported memory operation: {candidate.operation}")
        if not (candidate.content or "").strip():
            return self._reject(candidate, "semantic_memory candidate requires content")
        if not self._confident_enough(candidate):
            return self._reject(candidate, f"confidence {candidate.confidence} below threshold")

        memory_type = (candidate.entities or {}).get("memory_type", "context")
        memory = self.memories.create(
            workspace_id=candidate.workspace_id,
            content=candidate.content,
            memory_type=memory_type if memory_type in ("preference", "goal", "constraint", "interest", "context") else "context",
            entities=candidate.entities,
            confidence=candidate.confidence or 1.0,
            source_type=candidate.source_type,
            source_event_ids=candidate.source_event_ids,
            subject_user_id=candidate.subject_user_id,
        )
        self.candidates.mark_accepted(candidate, memory.id)
        return ReconcileResult(True, candidate.id, result_id=memory.id)

    def _reconcile_episode(self, candidate: MemoryCandidate) -> ReconcileResult:
        if candidate.operation != "upsert":
            return self._reject(candidate, f"unsupported episode operation: {candidate.operation}")
        if not (candidate.content or "").strip():
            return self._reject(candidate, "episode candidate requires content")
        # Same gate as Vault and semantic memory. Episodes were the one type
        # without it, so a low-confidence guess at "what the student decided"
        # became canonical history while an equally weak preference was
        # rejected. `_min_confidence_for` still exempts `user_explicit`.
        if not self._confident_enough(candidate):
            return self._reject(
                candidate,
                f"confidence {candidate.confidence} below threshold for source "
                f"{candidate.source_type}",
            )

        event_type = (candidate.entities or {}).get("event_type") or candidate.key or "note"
        episode = self.episodes.record(
            workspace_id=candidate.workspace_id,
            event_type=event_type,
            summary=candidate.content,
            entities=candidate.entities,
            source_event_ids=candidate.source_event_ids,
            subject_user_id=candidate.subject_user_id,
        )
        self.candidates.mark_accepted(candidate, episode.id)
        return ReconcileResult(True, candidate.id, result_id=episode.id)

    # -- helpers -----------------------------------------------------------

    def _confident_enough(self, candidate: MemoryCandidate) -> bool:
        return (candidate.confidence or 0) >= self._min_confidence_for(candidate.source_type)

    @staticmethod
    def _min_confidence_for(source_type: str) -> float:
        """An explicit user statement is not subject to a confidence floor.

        "Remember Germany is my first preference" is not a guess we might be
        35% sure of — the student said it.
        """
        if source_type == "user_explicit":
            return 0.0
        return DEFAULT_MIN_CONFIDENCE

    @staticmethod
    def _unwrap(value):
        if isinstance(value, dict) and set(value) == {"value"}:
            return value["value"]
        return value

    def _reject(self, candidate: MemoryCandidate, reason: str) -> ReconcileResult:
        self.candidates.mark_rejected(candidate, reason)
        logger.info("candidate rejected id=%s reason=%s", candidate.id, reason)
        return ReconcileResult(False, candidate.id, reason=reason)
