# -*- coding: utf-8 -*-
"""Vault field definitions — the Vault's schema, stored as data.

Adding `tests.pte.score` must be an INSERT, never an edit to reconciliation or
retrieval. So every rule that varies per field lives in a row:

    validation_schema   what a legal value looks like (JSON Schema fragment)
    cardinality         single (one active fact) | multi (a set)
    conflict_policy     how a new value resolves against an existing one
    sensitivity         whether it may go into prompt context freely

The seed set below is a *starting* set, not a hardcoded one — it is data
inserted by a migration, and nothing in this package branches on any of these
keys. Deleting them all would leave a Vault with no fields, not broken code.
"""

import logging
from typing import Any, Optional

from sqlalchemy import select

from app.models import VaultFieldDefinition
from app.tools.executor import _validate

from .errors import MemoryDataError

logger = logging.getLogger(__name__)

# These values now live in independently addressable typed records. Existing
# rows remain readable as history, but new extraction must never create a
# second canonical truth beside EducationRecord/TestAttempt.
ENTITY_BACKED_LEGACY_FIELDS = frozenset({
    "education.cgpa", "education.backlogs", "tests.ielts.score",
})
FIELD_ALIASES = {"preferences.countries": "preferences.target_countries"}


class VaultFieldError(MemoryDataError):
    """A proposed value does not satisfy its field definition."""


def usable_in_counseling(definition) -> bool:
    """Restricted identifiers never enter routine conversation prompts."""
    return definition is not None and (
        definition.sensitivity == "normal" or
        (definition.sensitivity == "sensitive" and
         "counseling" in (definition.context_tags or []))
    )


# Seeded by migration 055. Illustrative coverage of the shapes the validator
# must handle (number with range, enum, string, array, boolean), NOT an
# attempt to model the domain — Phase 2 adds fields as data.
SEED_FIELD_DEFINITIONS: tuple[dict, ...] = (
    {
        "key": "education.cgpa",
        "category": "education",
        "data_type": "number",
        "validation_schema": {"type": "number", "minimum": 0, "maximum": 10},
        "cardinality": "single",
        "conflict_policy": "latest_wins",
        "description": "Cumulative grade point average on the student's stated scale.",
    },
    {
        "key": "education.backlogs",
        "category": "education",
        "data_type": "integer",
        "validation_schema": {"type": "integer", "minimum": 0, "maximum": 100},
        "cardinality": "single",
        "conflict_policy": "latest_wins",
        "description": "Number of outstanding failed subjects.",
    },
    {
        "key": "tests.ielts.score",
        "category": "tests",
        "data_type": "number",
        "validation_schema": {"type": "number", "minimum": 0, "maximum": 9},
        "cardinality": "single",
        "conflict_policy": "highest_confidence",
        "description": "Overall IELTS band score.",
    },
    {
        "key": "finance.budget",
        "category": "finance",
        "data_type": "object",
        "validation_schema": {
            "type": "object",
            "properties": {
                "amount": {"type": "number", "minimum": 0},
                "currency": {"type": "string"},
                "period": {"type": "string", "enum": ["total", "per_year"]},
            },
            "required": ["amount", "currency"],
            "additionalProperties": False,
        },
        "cardinality": "single",
        "conflict_policy": "latest_wins",
        "sensitivity": "sensitive",
        "description": "Budget the student can fund, with currency and period.",
    },
    {
        "key": "preferences.target_countries",
        "category": "preferences",
        "data_type": "array",
        "validation_schema": {"type": "array", "items": {"type": "string"}},
        "cardinality": "multi",
        "conflict_policy": "latest_wins",
        "searchable": True,
        "description": "Preferred destination countries, most preferred first.",
    },
    {
        "key": "preferences.study_mode",
        "category": "preferences",
        "data_type": "string",
        "validation_schema": {
            "type": "string",
            "enum": ["on_campus", "online", "hybrid"],
        },
        "cardinality": "single",
        "conflict_policy": "latest_wins",
        "description": "How the student wants to study.",
    },
)


class VaultFieldDefinitionService:
    """Reads and validates against the field-definition table."""

    def __init__(self, db):
        self.db = db

    def get(self, key: str) -> Optional[VaultFieldDefinition]:
        """The highest enabled version of a field definition."""
        return self.db.execute(
            select(VaultFieldDefinition)
            .where(
                VaultFieldDefinition.key == key,
                VaultFieldDefinition.enabled.is_(True),
            )
            .order_by(VaultFieldDefinition.version.desc())
            .limit(1)
        ).scalar_one_or_none()

    def list_definitions(self, category: Optional[str] = None) -> list[VaultFieldDefinition]:
        stmt = select(VaultFieldDefinition).where(
            VaultFieldDefinition.enabled.is_(True)
        )
        if category:
            stmt = stmt.where(VaultFieldDefinition.category == category)
        return list(self.db.execute(stmt.order_by(VaultFieldDefinition.key)).scalars().all())

    def keys(self) -> tuple[str, ...]:
        return tuple(d.key for d in self.list_definitions())

    def validate(self, key: str, value: Any) -> VaultFieldDefinition:
        """Check `value` against `key`'s definition. Raises VaultFieldError.

        Reuses the tool runtime's `_validate` rather than adding a second
        JSON-Schema implementation — one validator, one set of bugs, and a
        Vault field schema behaves exactly like a tool argument schema.
        """
        definition = self.get(key)
        if definition is None:
            raise VaultFieldError(f"Unknown vault field: {key}")
        if not definition.enabled:
            raise VaultFieldError(f"Vault field is disabled: {key}")

        schema = definition.validation_schema
        if schema:
            try:
                _validate(schema, value, path=key)
            except ValueError as exc:
                raise VaultFieldError(str(exc)) from exc
        return definition

    def upsert_definition(self, spec: dict) -> VaultFieldDefinition:
        """Create a definition, or supersede it with a new version.

        Versioning rather than mutation: an existing fact stays interpretable
        against the definition it was accepted under.
        """
        existing = self.get(spec["key"])
        version = 1 if existing is None else (existing.version or 1) + 1
        if existing is not None:
            existing.enabled = False

        definition = VaultFieldDefinition(
            key=spec["key"],
            category=spec.get("category", "general"),
            data_type=spec.get("data_type", "string"),
            validation_schema=spec.get("validation_schema"),
            cardinality=spec.get("cardinality", "single"),
            conflict_policy=spec.get("conflict_policy", "latest_wins"),
            sensitivity=spec.get("sensitivity", "normal"),
            searchable=bool(spec.get("searchable", False)),
            context_tags=spec.get("context_tags"),
            required_for=spec.get("required_for"),
            profile_priority=spec.get("profile_priority", 50),
            extractable_from=spec.get("extractable_from"),
            verification_policy=spec.get("verification_policy"),
            enabled=True,
            version=version,
            description=spec.get("description"),
        )
        self.db.add(definition)
        self.db.flush()
        return definition
