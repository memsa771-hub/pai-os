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

`apply_*` is intentionally not public API for agents. Agents produce
candidates; only `MemoryReconciler` calls these. See reconciler.py.
"""

import logging
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import select

from app.models import VaultFact
from .field_definitions import VaultFieldDefinitionService, VaultFieldError

logger = logging.getLogger(__name__)

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
            definition = self.fields.get(fact.field_key)
            value = _unwrap(fact.value)
            if definition is not None and definition.cardinality == "multi":
                out.setdefault(fact.field_key, []).append(value)
            else:
                out.setdefault(fact.field_key, value)
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
    ) -> VaultFact:
        """Validate, resolve against any existing fact, and record.

        Raises VaultFieldError if the value fails its definition — an invalid
        value never reaches the table.
        """
        definition = self.fields.validate(field_key, value)

        if definition.cardinality == "multi":
            # A set-valued field: the new value replaces the whole set, so
            # retire the existing members rather than accumulating duplicates.
            for existing in self.get_facts(workspace_id, field_key):
                existing.status = "superseded"
                existing.valid_until = _now()
        else:
            current = self.get_fact(workspace_id, field_key)
            if current is not None:
                if not self._should_supersede(current, definition, source_type, confidence):
                    logger.info(
                        "vault fact retained key=%s workspace=%s policy=%s",
                        field_key, workspace_id, definition.conflict_policy,
                    )
                    return current
                current.status = "superseded"
                current.valid_until = _now()

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
        return fact

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
