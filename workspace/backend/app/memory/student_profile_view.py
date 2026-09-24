# -*- coding: utf-8 -*-
"""The student-facing Profile projection.

The Profile is a VIEW, never a second source of truth. Everything here is read
from the canonical pair — Vault scalar facts and the typed student records —
and shaped for one surface. Nothing in this module writes; profile edits go
through MemoryCandidateService -> MemoryReconciler like every other claim, so
provenance and revision history survive an edit made by hand.

Two rules this module exists to enforce:

1. **Restricted facts never reach the Profile.** `VaultService.snapshot`
   already withholds `restricted` fields; this module additionally names the
   small set of `sensitive` fields the student's own profile page may show
   (their nationality, their funding situation), so adding a sensitive field to
   the Vault does not silently publish it. Passport number, date of birth,
   stored contact details and accessibility needs stay out by construction.

2. **Issue evidence is sanitized.** A `conflicting_fact` issue carries the
   proposed value inline, and that value can be the very identifier rule 1
   withholds. Values are echoed back only for fields the projection would have
   shown anyway.
"""

from datetime import datetime, timezone
from typing import Any, Optional

from .field_definitions import VaultFieldDefinitionService
from .readiness import ReadinessService, STAGES
from .student_records import ENTITY_MODELS, StudentRecordService
from .vault import VaultService

# Sensitive Vault fields the student's OWN profile page may render. High-level
# planning context (where they are a citizen of, what they can fund) is useful
# on the page and safe to show back to its subject; anything not listed stays
# withheld, as does every `restricted` field, which never appears here at all.
PROFILE_SAFE_SENSITIVE_FIELDS = frozenset({
    "identity.full_name",
    "identity.nationality",
    "identity.gender",
    "finance.funding_status",
    "finance.scholarship_interest",
    "finance.budget",
})

# Section id -> the record kinds it renders, in display order. Section ids are
# the Profile's own vocabulary; record kinds stay the canonical ones.
PROFILE_SECTIONS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("education", ("education", "course")),
    ("tests", ("test_attempt", "language_proficiency")),
    ("experience", ("work_experience",)),
    ("projects", ("project",)),
    ("skills", ("skill",)),
    ("certifications", ("certification",)),
    ("research", ("research", "achievement")),
    ("goals", ("goal",)),
    ("finance", ("financial_sponsor", "scholarship_application")),
    ("applications", ("application", "visa")),
    ("documents", ("document",)),
)

# Scalar facts the Profile groups into its About, Preferences and Finance
# blocks. Keys absent from the Vault are simply absent here — the page hides a
# missing field rather than printing a placeholder for it.
FACT_GROUPS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("about", ("identity.full_name", "identity.preferred_name", "identity.current_status",
               "identity.status_category", "identity.nationality", "identity.gender",
               "location.current_country", "location.current_city",
               "career.primary_interest")),
    ("preferences", ("preferences.target_countries", "preferences.preferred_language",
                     "preferences.learning_style", "mobility.relocation_willingness")),
    ("finance", ("finance.funding_status", "finance.scholarship_interest", "finance.budget")),
)

# The stage whose readiness the Profile leads with. The rest still ship, so the
# journey strip can show the whole arc.
PRIMARY_STAGE = "discovery"


def _text(value: Any) -> Optional[str]:
    """A non-empty display string, or None so the caller can omit the field."""
    if value is None or isinstance(value, bool):
        return None
    text = str(value).strip()
    return text or None


def _category(value: Any) -> Optional[str]:
    """The onboarding status category, capitalized for display."""
    text = _text(value)
    return text.capitalize() if text else None


def _joined(*parts: Any) -> Optional[str]:
    return ", ".join(p for p in (_text(part) for part in parts) if p) or None


class StudentProfileView:
    """Compose the Profile projection from canonical state."""

    def __init__(self, db):
        self.db = db
        self.vault = VaultService(db)
        self.records = StudentRecordService(db)
        self.readiness = ReadinessService(db)
        self.fields = VaultFieldDefinitionService(db)

    # -- the projection ----------------------------------------------------

    def build(self, workspace_id: str, account: Optional[dict] = None) -> dict:
        facts = self.safe_facts(workspace_id)
        records = self.records.snapshot(workspace_id)
        issues = [self._issue(issue) for issue in self.records.issues(workspace_id)]
        record_count = sum(len(rows) for rows in records.values())
        return {
            "header": self._header(facts, records, account or {}),
            "facts": facts,
            "factGroups": {group: [key for key in keys if key in facts]
                           for group, keys in FACT_GROUPS},
            "sections": {section: {kind: records.get(kind, []) for kind in kinds}
                         for section, kinds in PROFILE_SECTIONS},
            "readiness": {
                "primary": self.readiness.evaluate(workspace_id, PRIMARY_STAGE),
                "stages": {stage: self.readiness.evaluate(workspace_id, stage)
                           for stage in STAGES},
            },
            "issues": issues,
            "meta": {
                "recordCount": record_count,
                "factCount": len(facts),
                # "Empty" drives the onboarding state, so account-only identity
                # does not count: a name from the login provider is not a
                # profile PAI has learned anything from.
                "isEmpty": record_count == 0 and not facts,
                "openIssueCount": len(issues),
                "generatedAt": datetime.now(timezone.utc).isoformat(),
            },
        }

    def safe_facts(self, workspace_id: str) -> dict[str, Any]:
        """Active Vault facts minus everything the Profile must not show."""
        return self.vault.snapshot(
            workspace_id, include_sensitive=False,
            allowed_sensitive_keys=set(PROFILE_SAFE_SENSITIVE_FIELDS),
        )

    # -- header ------------------------------------------------------------

    def _header(self, facts: dict, records: dict, account: dict) -> dict:
        """Canonical preferred name first, account identity as fallback."""
        education = records.get("education", [])
        header = {
            "displayName": (_text(facts.get("identity.preferred_name"))
                            or _text(account.get("displayName"))
                            or _text(facts.get("identity.full_name"))),
            "avatarUrl": _text(account.get("avatarUrl")),
            "email": _text(account.get("email")),
            "preferredName": _text(facts.get("identity.preferred_name")),
            # Richest first: a stated headline, then what their education
            # implies, and only then the coarse onboarding category — so a
            # brand-new account still reads as "Student" instead of blank.
            "status": (_text(facts.get("identity.current_status"))
                       or self._status_from_education(education)
                       or _category(facts.get("identity.status_category"))),
            "location": _joined(facts.get("location.current_city"),
                                facts.get("location.current_country")),
            "headline": (_text(facts.get("career.primary_interest"))
                         or self._headline_from_goals(records.get("goal", []))),
        }
        return {key: value for key, value in header.items() if value is not None}

    @staticmethod
    def _status_from_education(education: list[dict]) -> Optional[str]:
        """e.g. "BS Computer Science student at COMSATS" — from stated data only."""
        current = next((row for row in education
                        if row.get("academic_status") == "current"), None)
        row = current or next((row for row in education
                               if row.get("academic_status") == "completed"), None)
        if row is None:
            return None
        qualification = _text(row.get("qualification_name"))
        if not qualification:
            return None
        institution = _text(row.get("institution_name"))
        status = (f"{qualification} student" if current
                  else f"Completed {qualification}")
        return f"{status} at {institution}" if institution else status

    @staticmethod
    def _headline_from_goals(goals: list[dict]) -> Optional[str]:
        """The goal the student is most committed to, used as their headline."""
        ranking = {"committed": 0, "considering": 1, "exploratory": 2}
        ranked = sorted(goals, key=lambda row: ranking.get(row.get("commitment"), 3))
        for goal in ranked:
            title = _text(goal.get("title"))
            if title:
                return title
        return None

    # -- issues ------------------------------------------------------------

    def _issue(self, issue) -> dict:
        """An open issue, with its evidence reduced to what is safe to echo."""
        evidence = issue.evidence or {}
        return {
            "id": issue.id,
            "type": issue.issue_type,
            "severity": issue.severity,
            "summary": issue.summary,
            "clarificationQuestion": issue.clarification_question,
            "recordType": evidence.get("record_type"),
            "recordId": evidence.get("record_id"),
            "fieldKey": self._safe_field_key(evidence.get("field_key")),
            "values": self._safe_values(evidence),
            "createdAt": issue.created_at.isoformat() if issue.created_at else None,
        }

    def _safe_field_key(self, field_key: Optional[str]) -> Optional[str]:
        """A field key is only named when its value could be shown anyway."""
        if not field_key:
            return None
        definition = self.fields.get(field_key)
        if definition is None or definition.sensitivity == "restricted":
            return None
        if (definition.sensitivity == "sensitive"
                and field_key not in PROFILE_SAFE_SENSITIVE_FIELDS):
            return None
        return field_key

    def _safe_values(self, evidence: dict) -> Optional[dict]:
        """Current/proposed values, withheld entirely for withheld fields."""
        if evidence.get("field_key"):
            if not self._safe_field_key(evidence["field_key"]):
                return None
            return {"current": evidence.get("current_value"),
                    "proposed": evidence.get("proposed_value")}
        if evidence.get("record_type") in ENTITY_MODELS:
            return {"current": evidence.get("current"), "proposed": evidence.get("proposed")}
        return None
