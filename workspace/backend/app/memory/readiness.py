"""Journey-specific presence checks derived from field metadata and records."""

from .field_definitions import VaultFieldDefinitionService
from .student_records import StudentRecordService
from .vault import VaultService

STAGES = (
    "discovery", "counseling", "matching", "eligibility", "application",
    "scholarship", "visa", "enrollment", "career",
)
ENTITY_REQUIREMENTS = {
    "matching": ("education", "goal"),
    "eligibility": ("education", "test_attempt"),
    "application": ("education", "goal", "document"),
    "scholarship": ("education",),
    "career": ("education",),
}


class ReadinessService:
    def __init__(self, db):
        self.fields = VaultFieldDefinitionService(db)
        self.vault = VaultService(db)
        self.records = StudentRecordService(db)

    def evaluate(self, workspace_id: str, stage: str) -> dict:
        if stage not in STAGES:
            raise ValueError("Unknown student journey stage")
        facts = self.vault.snapshot(workspace_id, include_sensitive=True)
        records = self.records.snapshot(workspace_id)
        filled, missing = [], []
        for definition in self.fields.list_definitions():
            if stage not in (definition.required_for or []):
                continue
            (filled if facts.get(definition.key) not in (None, "", []) else missing).append(definition.key)
        for kind in ENTITY_REQUIREMENTS.get(stage, ()):
            (filled if records.get(kind) else missing).append(f"records.{kind}")
        issues = self.records.issues(workspace_id)
        return {
            "stage": stage, "filled": filled, "missing": missing,
            "conflicts": [issue.id for issue in issues
                          if issue.issue_type in ("conflicting_fact", "conflicting_record")],
            "next_useful_gap": missing[0] if missing else None,
        }
