# -*- coding: utf-8 -*-
"""VaultService — canonical structured student state.

Two rules this module exists to enforce:

1. **History, not overwrite.** Superseding a fact marks the old row
   `superseded` with a `valid_until` and inserts a new one. Provenance
   survives, so "who told us their CGPA was 8.1, and when?" stays answerable
   after the value changes.

2. **Validated writes only.** Every write goes through the field definition
   (`field_definitions.py`). There is no path that writes an unvalidated value
   and no branch on any particular field key.

**Cardinality invariant — exactly one active row per (workspace, field_key).**

This holds for `single` AND `multi` fields. A list-valued field such as
`preferences.countries` stores the whole list inside one row's `value`
(`{"value": ["Germany", "Canada"]}`); it does NOT store one row per country.

`cardinality` therefore describes the SHAPE OF THE VALUE, not the number of
rows:

    single   value is a scalar or object    {"value": 8.1}
    multi    value is a list                {"value": ["Germany", "Canada"]}

Rationale: one row per member would make "the student's preferences are now
exactly [X, Y]" a multi-row diff with no atomic point of truth, would break
ordering (these lists are ranked), and would give a single logical fact
several provenance records instead of one. The set is what the student stated,
so the set is what gets a row.

Reading code may rely on this: `snapshot()` returns `value` directly for every
key, with no per-cardinality branch.

`apply_*` is intentionally not public API for agents. Agents produce
candidates; only `MemoryReconciler` calls these. See reconciler.py.
"""

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional

from sqlalchemy import select

from app.models import ProfileIssue, VaultFact
from .field_definitions import VaultFieldDefinitionService, VaultFieldError

logger = logging.getLogger(__name__)


class VaultOutcome(str, Enum):
    """What `apply_fact` actually did.

    Distinguishing these matters because two of them mean "canonical state did
    NOT change". Collapsing them into a boolean is how a student ends up told
    "saved" for a correction that was silently discarded.
    """

    CREATED = "created"            # no prior value; a new fact was recorded
    SUPERSEDED = "superseded"      # replaced a prior value, which is retained
    RETAINED = "retained"          # prior value won the conflict; nothing written
    NEEDS_REVIEW = "needs_review"  # policy requires explicit confirmation
    # A document independently states the value already on record. Value
    # unchanged, no duplicate row, no conflict — the evidence is appended to
    # the existing fact so "how do we know?" can name both sources.
    CORROBORATED = "corroborated"

#: How many independent corroborations one fact keeps. Bounded so a student
#: re-uploading the same transcript cannot grow a row without limit.
MAX_CORROBORATIONS = 10


@dataclass(frozen=True)
class VaultWriteResult:
    outcome: VaultOutcome
    fact: Optional[VaultFact]

    @property
    def changed(self) -> bool:
        """True only when canonical state actually moved."""
        return self.outcome in (VaultOutcome.CREATED, VaultOutcome.SUPERSEDED)

# Ranked trust. A value the student stated outright should not be silently
# replaced by something an LLM inferred from chat.
SOURCE_TRUST = {
    "user_explicit": 100,
    "document": 80,
    "agent": 50,
    "conversation": 40,
    "system": 30,
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _unwrap(value: Any) -> Any:
    """Vault values are stored wrapped as {"value": ...}.

    JSONB columns cannot hold a bare scalar in every backend consistently, and
    wrapping leaves room to add per-value metadata later without a migration.
    """
    if isinstance(value, dict) and set(value) == {"value"}:
        return value["value"]
    return value


class VaultService:
    """Read and (via the reconciler) write canonical Vault facts."""

    def __init__(self, db):
        self.db = db
        self.fields = VaultFieldDefinitionService(db)

    # -- reads ------------------------------------------------------------

    def get_fact(self, workspace_id: str, field_key: str) -> Optional[VaultFact]:
        return self.db.execute(
            select(VaultFact)
            .where(
                VaultFact.workspace_id == workspace_id,
                VaultFact.field_key == field_key,
                VaultFact.status == "active",
            )
            .order_by(VaultFact.valid_from.desc())
            .limit(1)
        ).scalar_one_or_none()

    def get_facts(self, workspace_id: str, field_key: str) -> list[VaultFact]:
        """All active facts for a key — the `multi` cardinality read."""
        return list(self.db.execute(
            select(VaultFact)
            .where(
                VaultFact.workspace_id == workspace_id,
                VaultFact.field_key == field_key,
                VaultFact.status == "active",
            )
            .order_by(VaultFact.valid_from.desc())
        ).scalars().all())

    def snapshot(
        self, workspace_id: str, include_sensitive: bool = False,
        allowed_sensitive_keys: Optional[set[str]] = None,
    ) -> dict[str, Any]:
        """All active facts as `{field_key: value}`.

        Structured retrieval, deliberately: the Vault is queried by filter, not
        by embedding. `include_sensitive=False` withholds fields the definition
        marks sensitive, so prompt context does not carry a student's finances
        unless something explicitly asked for them.
        """
        facts = self.db.execute(
            select(VaultFact).where(
                VaultFact.workspace_id == workspace_id,
                VaultFact.status == "active",
            ).order_by(VaultFact.valid_from.desc())
        ).scalars().all()

        sensitive: set[str] = set()
        if not include_sensitive:
            sensitive = {
                d.key for d in self.fields.list_definitions()
                if d.sensitivity == "restricted" or
                (d.sensitivity == "sensitive" and d.key not in (allowed_sensitive_keys or set()))
            }

        out: dict[str, Any] = {}
        for fact in facts:
            if fact.field_key in sensitive:
                continue
            # One active row per key (the cardinality invariant), so the stored
            # value IS the value — including for list-valued fields, where it is
            # already a list. Wrapping it again here is what produced
            # `[["Canada", "Germany"]]`.
            out.setdefault(fact.field_key, _unwrap(fact.value))
        return out

    def history(self, workspace_id: str, field_key: str) -> list[VaultFact]:
        """Every version of a field, newest first — the provenance trail."""
        return list(self.db.execute(
            select(VaultFact)
            .where(
                VaultFact.workspace_id == workspace_id,
                VaultFact.field_key == field_key,
            )
            .order_by(VaultFact.valid_from.desc())
        ).scalars().all())

    # -- writes (reconciler-only) -----------------------------------------

    def apply_fact(
        self,
        workspace_id: str,
        field_key: str,
        value: Any,
        source_type: str,
        confidence: float = 1.0,
        source_event_id: Optional[str] = None,
        evidence: Optional[dict] = None,
        subject_user_id: Optional[str] = None,
        claim_origin: Optional[str] = None,
        capture_method: Optional[str] = None,
        candidate_id: Optional[str] = None,
    ) -> "VaultWriteResult":
        """Validate, resolve against any existing fact, and record.

        Returns a `VaultWriteResult` naming what actually happened — CREATED,
        SUPERSEDED, RETAINED or NEEDS_REVIEW. The caller must not assume a
        write occurred: RETAINED and NEEDS_REVIEW both mean canonical state is
        unchanged, and reporting either as success would tell a student their
        correction was saved when it was not.

        Raises VaultFieldError if the value fails its definition — an invalid
        value never reaches the table.
        """
        definition = self.fields.validate(field_key, value)
        policy = definition.conflict_policy or "latest_wins"
        current = self.get_fact(workspace_id, field_key)

        # A document agreeing with what is already canonical is corroboration,
        # not a new value. Without this branch the existing policy either
        # superseded the fact with an identical duplicate row, or — against a
        # student-stated value — reported the agreement as a LOST CONFLICT.
        # Scoped to documents: conversation restatements keep their existing
        # behaviour.
        if (current is not None and source_type == "document"
                and _unwrap(current.value) == value):
            self._corroborate(current, evidence)
            return VaultWriteResult(VaultOutcome.CORROBORATED, current)

        # An institutional document disagreeing with self-report is evidence
        # of a conflict, not authority to silently replace the student's claim.
        quote = str((evidence or {}).get("quote") or "").casefold()
        explicit_correction = any(marker in quote for marker in
                                  ("actually", "correction", "correct that", "i changed", "now ", "instead"))
        safe_latest_wins = (
            policy == "latest_wins"
            and definition.category in ("preferences", "career", "mobility")
        )
        if (current is not None and _unwrap(current.value) != value
                and source_type != "user_explicit" and not safe_latest_wins and (
                    source_type in ("document", "agent", "system") or
                    (source_type == "conversation" and not explicit_correction))):
            self.db.add(ProfileIssue(
                workspace_id=workspace_id, subject_user_id=subject_user_id,
                candidate_id=candidate_id,
                issue_type="conflicting_fact",
                severity="blocking", affected_type="vault_fact", affected_id=current.id,
                summary=f"Conflicting evidence for {field_key}",
                clarification_question=f"What is the correct value for {field_key}?",
                evidence={"field_key": field_key, "current_fact_id": current.id,
                          "current_value": _unwrap(current.value),
                          "current_source_type": current.source_type,
                          "proposed_value": value, "proposed_source_type": source_type,
                          "current_evidence": current.evidence,
                          "proposed_evidence": evidence},
            ))
            self.db.flush()
            return VaultWriteResult(VaultOutcome.NEEDS_REVIEW, current)

        # manual_review parks anything not stated first-hand, whether or not a
        # value already exists. Checked before the existence branch because a
        # FIRST value for a review-gated field must be parked too — otherwise
        # the policy only applies from the second write onward, which is
        # exactly backwards for a field marked as needing review.
        if policy == "manual_review" and source_type != "user_explicit":
            self.db.add(ProfileIssue(
                workspace_id=workspace_id, subject_user_id=subject_user_id,
                candidate_id=candidate_id, issue_type="conflicting_fact",
                severity="blocking", affected_type="vault_fact",
                affected_id=current.id if current else None,
                summary=f"Confirmation required for {field_key}",
                clarification_question=f"What is the correct value for {field_key}?",
                evidence={
                    "field_key": field_key,
                    "current_fact_id": current.id if current else None,
                    "current_value": _unwrap(current.value) if current else None,
                    "current_source_type": current.source_type if current else None,
                    "proposed_value": value,
                    "proposed_source_type": source_type,
                    "current_evidence": current.evidence if current else None,
                    "proposed_evidence": evidence,
                },
            ))
            self.db.flush()
            logger.info(
                "vault fact needs review key=%s workspace=%s source=%s",
                field_key, workspace_id, source_type,
            )
            return VaultWriteResult(VaultOutcome.NEEDS_REVIEW, current)

        superseded = False
        if definition.cardinality == "multi":
            # List-valued fields keep ONE active row holding the whole list —
            # see the module docstring. Retire any prior row for this key.
            for existing in self.get_facts(workspace_id, field_key):
                existing.status = "superseded"
                existing.valid_until = _now()
                superseded = True
        elif current is not None:
            if not self._should_supersede(current, definition, source_type, confidence):
                logger.info(
                    "vault fact retained key=%s workspace=%s policy=%s",
                    field_key, workspace_id, policy,
                )
                return VaultWriteResult(VaultOutcome.RETAINED, current)
            current.status = "superseded"
            current.valid_until = _now()
            superseded = True

        # Release the partial unique slot before inserting the new active row.
        # This ordering matters under PostgreSQL's immediate unique checks.
        if superseded:
            self.db.flush()

        fact = VaultFact(
            workspace_id=workspace_id,
            subject_user_id=subject_user_id,
            field_key=field_key,
            field_version=definition.version,
            value={"value": value},
            confidence=confidence,
            source_type=source_type,
            claim_origin=claim_origin or ("institution_document" if source_type == "document" else "student" if source_type in ("conversation", "user_explicit") else "agent_inference"),
            capture_method=capture_method or ("document_extraction" if source_type == "document" else "conversation_extraction" if source_type == "conversation" else "explicit_correction" if source_type == "user_explicit" else "agent_proposal"),
            source_event_id=source_event_id,
            evidence=evidence,
            status="active",
        )
        self.db.add(fact)
        self.db.flush()
        return VaultWriteResult(
            VaultOutcome.SUPERSEDED if superseded else VaultOutcome.CREATED, fact
        )

    def _corroborate(self, fact: VaultFact, evidence: Optional[dict]) -> None:
        """Append independent supporting evidence to an existing fact.

        The fact's own evidence (who first told us) is left intact; this only
        adds "and a document says so too". Deduplicated by file+locator so a
        reprocessed document does not count twice.
        """
        if not evidence:
            return
        entry = {k: evidence[k] for k in (
            "file_id", "locator", "quote", "authority", "document_type",
        ) if evidence.get(k) is not None}
        if not entry:
            return
        existing = dict(fact.evidence or {})
        corroborations = list(existing.get("corroborations") or [])
        key = (entry.get("file_id"), entry.get("locator"))
        if any((c.get("file_id"), c.get("locator")) == key for c in corroborations):
            return
        corroborations.append(entry)
        existing["corroborations"] = corroborations[-MAX_CORROBORATIONS:]
        # Reassigned, not mutated in place, so the JSONB change is detected.
        fact.evidence = existing
        self.db.flush()

    def retract_fact(self, workspace_id: str, field_key: str) -> int:
        """Mark a field's active facts retracted ("that's wrong, drop it").

        Retract rather than delete: the row stays for audit, and reads filter
        on `status='active'` so a retracted fact is invisible to every reader.
        """
        facts = self.get_facts(workspace_id, field_key)
        for fact in facts:
            fact.status = "retracted"
            fact.valid_until = _now()
        if facts:
            self.db.flush()
        return len(facts)

    # -- conflict policy ---------------------------------------------------

    @staticmethod
    def _should_supersede(
        current: VaultFact, definition, source_type: str, confidence: float,
    ) -> bool:
        """Resolve new-vs-existing using the field's declared policy.

        Data-driven: the policy is a column, so a new policy is a new branch
        here and a config change per field — never a per-field branch.
        """
        policy = definition.conflict_policy or "latest_wins"

        if policy == "manual_review":
            # Only a first-hand statement may auto-apply; anything inferred
            # waits for an explicit user confirmation.
            return source_type == "user_explicit"

        if policy == "highest_confidence":
            new_trust = SOURCE_TRUST.get(source_type, 0)
            cur_trust = SOURCE_TRUST.get(current.source_type, 0)
            if new_trust != cur_trust:
                return new_trust > cur_trust
            return confidence >= (current.confidence or 0)

        # latest_wins — but never let weak inference overwrite a fact the
        # student stated themselves.
        if (
            current.source_type == "user_explicit"
            and SOURCE_TRUST.get(source_type, 0) < SOURCE_TRUST["user_explicit"]
        ):
            return False
        return True
