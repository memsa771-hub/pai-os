"""Journey-specific presence checks derived from field metadata and records."""

from .field_definitions import VaultFieldDefinitionService
from .student_records import StudentRecordService
from .vault import VaultService

STAGES = (
    "discovery", "counseling", "matching", "eligibility", "application",
    "scholarship", "visa", "enrollment", "career",
)
STAGE_REQUIREMENTS = {
    "discovery": {"entities": ("education", "goal")},
    "counseling": {"entities": ("education", "goal")},
    "matching": {"facts": ("preferences.target_countries", "finance.budget"), "entities": ("education", "goal")},
    "eligibility": {"entities": ("education", "goal")},
    "application": {"entities": ("education", "goal", "document")},
    "scholarship": {"facts": ("finance.funding_status",), "entities": ("education", "goal")},
    "visa": {"facts": ("identity.nationality",), "entities": ("application", "document")},
    "enrollment": {"entities": ("application", "document")},
    "career": {"entities": ("education", "goal")},
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
        required = STAGE_REQUIREMENTS[stage]
        required_fact_keys = set(required.get("facts", ()))
        for definition in self.fields.list_definitions():
            if stage not in (definition.required_for or []):
                continue
            required_fact_keys.add(definition.key)
        for key in sorted(required_fact_keys):
            (filled if facts.get(key) not in (None, "", []) else missing).append(key)
        for kind in required.get("entities", ()):
            (filled if records.get(kind) else missing).append(f"records.{kind}")
        issues = self.records.issues(workspace_id)
        conflicts = [issue.id for issue in issues
                     if issue.issue_type in ("conflicting_fact", "conflicting_record")]
        expired = [f"records.{kind}:{row['id']}" for kind, rows in records.items()
                   for row in rows if row.get("verification_status") == "expired"]
        status = ("blocked" if conflicts else "insufficient_information" if not filled
                  else "partially_ready" if missing or expired else "ready")
        return {
            "stage": stage, "status": status, "filled": filled, "missing": missing,
            "conflicts": conflicts, "expired_evidence": expired,
            "blocking_issues": conflicts,
            "next_useful_gap": missing[0] if missing else None,
        }
