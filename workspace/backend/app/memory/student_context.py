"""Intent-shaped student profile view for Counselor and Operator."""

from .context import MemoryContextService
from .readiness import ReadinessService
from .student_records import StudentRecordService

INTENT_RECORDS = {
    "discovery": ("education", "goal"),
    "academic_planning": ("education", "course", "test_attempt", "goal"),
    "study_abroad_matching": ("education", "test_attempt", "language_proficiency", "goal", "financial_sponsor"),
    "eligibility_analysis": ("education", "course", "test_attempt", "goal"),
    "career_exploration": ("education", "work_experience", "project", "skill", "certification", "research", "achievement", "goal"),
    "scholarship_planning": ("education", "test_attempt", "achievement", "financial_sponsor", "scholarship_application", "goal", "application", "document"),
    "application_preparation": ("education", "course", "test_attempt", "goal", "application", "document"),
    "application_execution": ("education", "test_attempt", "goal", "application", "document"),
    "visa_preparation": ("goal", "application", "document", "financial_sponsor", "visa"),
    "enrollment": ("education", "goal", "application", "document"),
    "document_review": ("education", "course", "test_attempt", "work_experience", "application", "document"),
}
INTENT_STAGE = {
    "discovery": "discovery",
    "academic_planning": "counseling",
    "study_abroad_matching": "matching",
    "eligibility_analysis": "eligibility",
    "career_exploration": "career",
    "scholarship_planning": "scholarship",
    "application_preparation": "application",
    "application_execution": "application",
    "visa_preparation": "visa",
    "enrollment": "enrollment",
    "document_review": "application",
}

INTENT_SIGNALS = (
    ("visa_preparation", ("visa", "passport", "embassy")),
    ("scholarship_planning", ("scholarship", "funding", "financial aid")),
    ("document_review", ("transcript", "cv", "document", "certificate", "sop")),
    ("application_preparation", ("application", "apply", "deadline", "admission")),
    ("eligibility_analysis", ("eligible", "eligibility", "prerequisite", "requirements")),
    ("study_abroad_matching", ("country", "university", "study abroad", "masters abroad")),
    ("career_exploration", ("career", "job", "internship", "profession", "skill")),
    ("academic_planning", ("course", "subject", "degree", "study plan", "gpa")),
    ("enrollment", ("enroll", "enrol", "offer letter")),
)
INTENT_SENSITIVE_FIELDS = {
    "visa_preparation": {"identity.nationality", "finance.funding_status", "finance.budget"},
    "scholarship_planning": {"identity.nationality", "finance.funding_status", "finance.scholarship_interest", "finance.budget"},
    "study_abroad_matching": {"identity.nationality", "finance.funding_status", "finance.budget"},
    "application_preparation": {"identity.full_name"},
    "application_execution": {"identity.full_name"},
}


def classify_intent(query: str | None) -> str:
    text = (query or "").casefold()
    scored = [(sum(1 for signal in signals if signal in text), intent)
              for intent, signals in INTENT_SIGNALS]
    score, intent = max(scored, default=(0, "discovery"))
    return intent if score else "discovery"


class StudentContextBuilder:
    def __init__(self, db):
        self.memory = MemoryContextService(db)
        self.records = StudentRecordService(db)
        self.readiness = ReadinessService(db)

    def build(self, student_id: str, intent: str, query: str | None = None,
              caller: str = "counselor") -> dict:
        if intent not in INTENT_RECORDS:
            intent = "discovery"
        context = self.memory.build_student_context(
            workspace_id=student_id, query=query, caller=caller,
            include_sensitive=False,
        )
        # The context service owns permission and sensitivity checks. Never
        # add raw records after it has deliberately withheld profile access.
        if "vault" not in context.resolved_refs:
            return {"profile": context.to_dict(), "records": {}, "readiness": {}, "issues": []}
        return {
            "profile": context.to_dict(),
            "records": {kind: context.records.get(kind, []) for kind in INTENT_RECORDS[intent]},
            "readiness": self.readiness.evaluate(student_id, INTENT_STAGE[intent]),
            "issues": context.issues,
        }

    def build_context(self, student_id: str, query: str | None = None,
                      caller: str = "counselor", intent: str | None = None):
        """Authoritative live context used by Counselor and profile-aware tools."""
        intent = intent if intent in INTENT_RECORDS else classify_intent(query)
        context = self.memory.build_student_context(
            workspace_id=student_id, query=query, caller=caller,
            include_sensitive=False,
        )
        return self._shape(context, student_id, intent)

    async def build_context_async(self, student_id: str, query: str | None = None,
                                  caller: str = "counselor", intent: str | None = None):
        intent = intent if intent in INTENT_RECORDS else classify_intent(query)
        context = await self.memory.build_student_context_async(
            workspace_id=student_id, query=query, caller=caller,
            include_sensitive=False,
        )
        return self._shape(context, student_id, intent)

    def _shape(self, context, student_id, intent):
        if "vault" not in context.resolved_refs:
            context.records = {}
            context.issues = []
            return context
        allowed = INTENT_RECORDS[intent]
        permitted_sensitive = INTENT_SENSITIVE_FIELDS.get(intent, set())
        if permitted_sensitive:
            selected = self.memory.vault.snapshot(
                student_id, include_sensitive=False,
                allowed_sensitive_keys=permitted_sensitive)
            context.vault.update({key: value for key, value in selected.items()
                                  if key in permitted_sensitive})
        context.records = {kind: rows for kind, rows in context.records.items()
                           if kind in allowed}
        context.readiness = self.readiness.evaluate(student_id, INTENT_STAGE[intent])
        # Only decision-relevant open issues enter the prompt.
        record_types = set(allowed)
        context.issues = [issue for issue in context.issues
                          if not issue.get("record_type") or issue.get("record_type") in record_types]
        return context
