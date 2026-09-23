"""Versioned, data-driven requirements for personalized counseling."""

from typing import Any

from sqlalchemy import select

from app.models import ProfileRequirement
from .education_journey import GROUP_ORDER, education_group
from .student_snapshot import StudentSnapshot


TIERS = ("critical", "important", "enrichment")
SOURCE_TYPES = ("vault_fact", "record_presence", "record_field", "journey_gap")
SELECTORS = ("any", "current_or_highest")


def is_filled(value: Any) -> bool:
    """False and zero are answers; only null and empty containers are absent."""
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, tuple, dict, set)):
        return bool(value)
    return True


def value_at(value: Any, path: str | None) -> Any:
    for part in (path or "").split("."):
        if not part:
            continue
        if not isinstance(value, dict):
            return None
        value = value.get(part)
    return value


class ProfileRequirementRegistry:
    def __init__(self, db):
        self.db = db

    def active(self) -> list[ProfileRequirement]:
        rows = self.db.execute(
            select(ProfileRequirement).where(ProfileRequirement.enabled.is_(True))
            .order_by(ProfileRequirement.key, ProfileRequirement.version.desc())
        ).scalars().all()
        latest: dict[str, ProfileRequirement] = {}
        for row in rows:
            latest.setdefault(row.key, row)
        return list(latest.values())

    def get(self, key: str) -> ProfileRequirement | None:
        return next((row for row in self.active() if row.key == key), None)

    def is_applicable(self, requirement: ProfileRequirement, snapshot: StudentSnapshot, journey: dict) -> bool:
        checks = requirement.applicability or {}
        if not isinstance(checks, dict):
            raise ValueError(f"Invalid applicability for requirement {requirement.key}")
        for selector, expected in checks.items():
            actual = self._applicability_value(selector, snapshot, journey)
            if isinstance(actual, list):
                if expected not in actual:
                    return False
            elif actual != expected:
                return False
        return True

    def evaluate(self, requirement: ProfileRequirement, snapshot: StudentSnapshot, journey: dict) -> tuple[bool, bool]:
        if requirement.tier not in TIERS or requirement.source_type not in SOURCE_TYPES:
            raise ValueError(f"Invalid profile requirement {requirement.key}")
        if requirement.selector not in SELECTORS:
            raise ValueError(f"Invalid selector for requirement {requirement.key}")
        if not self.is_applicable(requirement, snapshot, journey):
            return False, False

        if requirement.source_type == "vault_fact":
            return True, is_filled(snapshot.fact_value(requirement.source_key))
        if requirement.source_type == "record_presence":
            return True, bool(snapshot.records.get(requirement.source_key))
        if requirement.source_type == "record_field":
            rows = self.select_records(requirement, snapshot)
            return True, any(is_filled(value_at(row, requirement.source_path)) for row in rows)
        gaps = {gap["key"] for gap in journey.get("gaps", [])}
        if requirement.source_key not in gaps:
            return False, False
        return True, False

    def select_records(self, requirement: ProfileRequirement, snapshot: StudentSnapshot) -> list[dict]:
        rows = list(snapshot.records.get(requirement.source_key, []))
        if requirement.selector != "current_or_highest" or not rows:
            return rows
        return [max(rows, key=self._education_rank)]

    @staticmethod
    def _education_rank(row: dict) -> tuple:
        group = education_group(row.get("canonical_level"))
        status_rank = {"current": 3, "completed": 2, "incomplete": 1, "planned": 0}
        date = row.get("end_date") or str(row.get("graduation_year") or "") or row.get("start_date") or ""
        return (status_rank.get(row.get("academic_status"), 1), GROUP_ORDER.get(group, -1), date, row.get("id") or "")

    @staticmethod
    def _applicability_value(selector: str, snapshot: StudentSnapshot, journey: dict) -> Any:
        if selector.startswith("fact:"):
            return snapshot.fact_value(selector.removeprefix("fact:"))
        if selector.startswith("record:"):
            target = selector.removeprefix("record:")
            kind, _, path = target.partition(".")
            return [value_at(row, path) for row in snapshot.records.get(kind, [])]
        if selector.startswith("journey_gap:"):
            key = selector.removeprefix("journey_gap:")
            return any(gap.get("key") == key for gap in journey.get("gaps", []))
        raise ValueError(f"Unsupported applicability selector: {selector}")
