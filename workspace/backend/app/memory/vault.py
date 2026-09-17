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

from app.models import VaultFact
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
                d.key for d in self.fields.list_definitions() if d.sensitivity == "sensitive"
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

        # manual_review parks anything not stated first-hand, whether or not a
        # value already exists. Checked before the existence branch because a
        # FIRST value for a review-gated field must be parked too — otherwise
        # the policy only applies from the second write onward, which is
        # exactly backwards for a field marked as needing review.
        if policy == "manual_review" and source_type != "user_explicit":
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

        fact = VaultFact(
            workspace_id=workspace_id,
            subject_user_id=subject_user_id,
            field_key=field_key,
            field_version=definition.version,
            value={"value": value},
            confidence=confidence,
            source_type=source_type,
            source_event_id=source_event_id,
            evidence=evidence,
            status="active",
        )
        self.db.add(fact)
        self.db.flush()
        return VaultWriteResult(
            VaultOutcome.SUPERSEDED if superseded else VaultOutcome.CREATED, fact
        )

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
