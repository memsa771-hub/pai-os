"""Intent-shaped student profile view for Counselor and Operator."""

from .context import MemoryContextService
from .readiness import ReadinessService
from .student_records import StudentRecordService

INTENT_RECORDS = {
    "discovery": ("education", "goal"),
    "study_abroad_matching": ("education", "test_attempt", "goal"),
    "career_exploration": ("education", "work_experience", "project", "skill", "certification", "goal"),
    "application_execution": ("education", "test_attempt", "goal", "application", "document"),
}
INTENT_STAGE = {
    "discovery": "discovery",
    "study_abroad_matching": "matching",
    "career_exploration": "career",
    "application_execution": "application",
}


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
