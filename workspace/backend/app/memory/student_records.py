"""Validated, versioned updates to repeatable student records."""

from copy import deepcopy
from datetime import date, datetime, timezone
from sqlalchemy import select

from app.models import (
    CourseRecord, EducationRecord, FileRecord, ProfileIssue, StudentGoal, StudentProject,
    TestAttempt, WorkExperience, StudentSkill, StudentCertification,
    StudentApplication, StudentDocument, StudentRecordRevision, Workspace,
    LanguageProficiency, ResearchRecord, AchievementRecord, FinancialSponsor,
    ScholarshipApplication, VisaRecord,
)
from .errors import MemoryDataError
from .student_schema import RECORD_SPECS, validate_record

ENTITY_MODELS = {
    "education": EducationRecord, "course": CourseRecord, "test_attempt": TestAttempt,
    "work_experience": WorkExperience, "project": StudentProject, "goal": StudentGoal,
    "skill": StudentSkill, "certification": StudentCertification,
    "application": StudentApplication, "document": StudentDocument,
    "language_proficiency": LanguageProficiency, "research": ResearchRecord,
    "achievement": AchievementRecord, "financial_sponsor": FinancialSponsor,
    "scholarship_application": ScholarshipApplication, "visa": VisaRecord,
}
REQUIRED = {kind: spec["required"] for kind, spec in RECORD_SPECS.items()}
ALLOWED = {kind: set(spec["properties"]) for kind, spec in RECORD_SPECS.items()}


class RecordNeedsReview(MemoryDataError):
    """A proposal was retained for review without changing canonical values."""


class AmbiguousRecordMatch(RecordNeedsReview):
    def __init__(self, record_ids: list[str]):
        super().__init__("Several existing records match; confirm this is a separate record")
        self.record_ids = record_ids


def _same(left, right):
    if isinstance(left, str) and isinstance(right, str):
        return " ".join(left.casefold().split()) == " ".join(right.casefold().split())
    return left == right


def _merge(previous, patch):
    result = deepcopy(previous)
    for key, value in patch.items():
        result[key] = _merge(result.get(key, {}), value) if isinstance(value, dict) else value
    return result


def _conflicts(previous, patch):
    return any(key in previous and (
        _conflicts(previous[key], value) if isinstance(previous[key], dict) and isinstance(value, dict)
        else not _same(previous[key], value)) for key, value in patch.items())


def _explicit_correction(evidence):
    quote = str((evidence or {}).get("quote") or "").casefold()
    return any(marker in quote for marker in ("actually", "correction", "correct that", "i changed", "now ", "instead"))


class StudentRecordService:
    def __init__(self, db):
        self.db = db

    def list(self, workspace_id: str, kind: str, limit: int | None = None):
        model = ENTITY_MODELS[kind]
        statement = select(model).where(model.workspace_id == workspace_id, model.status == "active").order_by(model.updated_at.desc(), model.id)
        if limit is not None:
            statement = statement.limit(limit)
        return list(self.db.execute(statement).scalars())

    def get(self, workspace_id: str, kind: str, record_id: str):
        model = ENTITY_MODELS[kind]
        return self.db.execute(select(model).where(model.workspace_id == workspace_id,
            model.id == record_id, model.status == "active")).scalar_one_or_none()

    def _values(self, kind, row):
        return {key: deepcopy(getattr(row, key)) for key in ALLOWED[kind] if getattr(row, key) is not None}

    def _match(self, workspace_id, kind, values):
        spec = RECORD_SPECS[kind]
        matches = []
        for row in self.list(workspace_id, kind):
            prior = self._values(kind, row)
            if kind == "test_attempt" and not any(values.get(k) and prior.get(k) == values[k] for k in spec["discriminators"]):
                if prior == values:
                    matches.append(row)
                continue
            # Optional identity fields often arrive in a later turn (the
            # institution after the degree, or issuer after a certificate).
            # Match shared identity evidence, reject contradictions, and let
            # the ambiguity check below prevent guessing between records.
            shared = [k for k in spec["identity"] if values.get(k) and prior.get(k)]
            if not shared or not all(_same(values[k], prior[k]) for k in shared):
                continue
            if any(values.get(k) is not None and prior.get(k) is not None and not _same(values[k], prior[k]) for k in spec["discriminators"]):
                continue
            matches.append(row)
        if len(matches) > 1:
            raise AmbiguousRecordMatch([row.id for row in matches])
        return matches[0] if matches else None

    def _revision(self, workspace_id, kind, row, before, source_type, claim_origin, capture_method, evidence):
        self.db.add(StudentRecordRevision(workspace_id=workspace_id, record_type=kind,
            record_id=row.id, before=before, after={**self._values(kind, row), "status": row.status},
            source_type=source_type, claim_origin=claim_origin, capture_method=capture_method, evidence=evidence))

    def apply(self, workspace_id: str, kind: str, values: dict, *,
              source_type: str, claim_origin: str, capture_method: str,
              evidence: dict | None = None, subject_user_id: str | None = None,
              record_id: str | None = None, supersedes_record_id: str | None = None,
              candidate_id: str | None = None, force_new: bool = False):
        values = validate_record(kind, values, partial=bool(record_id))
        # Serialize even first writes where there is no entity row to lock.
        owner = self.db.execute(select(Workspace).where(Workspace.id == workspace_id).with_for_update()).scalar_one_or_none()
        if owner is None or (subject_user_id is not None and str(owner.owner_user_id) != str(subject_user_id)):
            raise MemoryDataError("Student workspace does not exist or subject does not match")
        try:
            if force_new:
                current = None
            elif record_id:
                current = self.get(workspace_id, kind, record_id)
            else:
                current = self._match(workspace_id, kind, values)
        except AmbiguousRecordMatch as exc:
            self.db.add(ProfileIssue(
                workspace_id=workspace_id, subject_user_id=subject_user_id,
                candidate_id=candidate_id, issue_type="conflicting_record",
                summary=f"Ambiguous {kind.replace('_', ' ')} information",
                severity="blocking", affected_type=kind,
                clarification_question=(
                    f"Should this be added as a separate {kind.replace('_', ' ')} record?"
                ),
                evidence={"record_type": kind, "proposed": values,
                          "matching_record_ids": exc.record_ids,
                          "force_new_on_accept": True,
                          "proposed_source_type": source_type,
                          "proposed_evidence": evidence},
            ))
            self.db.flush()
            raise exc
        if record_id and current is None:
            raise MemoryDataError("Student record does not belong to this workspace or is inactive")
        before = self._values(kind, current) if current else None
        merged = validate_record(kind, _merge(before or {}, values))
        if kind == "course" and self.get(workspace_id, "education", merged["education_id"]) is None:
            raise MemoryDataError("Course education record does not belong to this student")
        if kind == "document":
            file = self.db.execute(select(FileRecord.id).where(FileRecord.id == merged["file_id"],
                FileRecord.workspace_id == workspace_id, FileRecord.status == "active")).scalar_one_or_none()
            if file is None:
                raise MemoryDataError("Document file does not belong to this student")
        old_goal = None
        if supersedes_record_id:
            old_goal = self.get(workspace_id, "goal", supersedes_record_id) if kind == "goal" else None
            if old_goal is None or old_goal.goal_type != merged.get("goal_type") or (current and old_goal.id == current.id):
                raise MemoryDataError("Only an existing goal in the same journey can be superseded")
        changed = current and _conflicts(before, values)
        if changed and source_type != "user_explicit" and (
                source_type in ("document", "agent", "system") or
                current.source_type == "document" or
                current.verification_status in ("document_supported", "externally_verified", "verified") or
                (source_type == "conversation" and not _explicit_correction(evidence))):
            self.db.add(ProfileIssue(workspace_id=workspace_id, subject_user_id=subject_user_id,
                candidate_id=candidate_id,
                issue_type="conflicting_record", summary=f"Conflicting {kind.replace('_', ' ')} information",
                severity="blocking", affected_type=kind, affected_id=current.id,
                clarification_question=f"Which {kind.replace('_', ' ')} information is correct?",
                evidence={"record_type": kind, "record_id": current.id, "current": before,
                          "proposed": values, "current_source_type": current.source_type,
                          "proposed_source_type": source_type,
                          "current_evidence": current.evidence, "proposed_evidence": evidence}))
            self.db.flush()
            raise RecordNeedsReview("The new evidence conflicts with an existing record")
        if current:
            if merged != before:
                for key, value in merged.items():
                    setattr(current, key, value)
                current.updated_at = datetime.now(timezone.utc)
                current.source_type = source_type
                current.claim_origin = claim_origin
                current.capture_method = capture_method
            row = current
        else:
            row = ENTITY_MODELS[kind](workspace_id=workspace_id, subject_user_id=subject_user_id,
                source_type=source_type, claim_origin=claim_origin, capture_method=capture_method,
                status="active", **merged)
            self.db.add(row)
        row.evidence = evidence or row.evidence
        if current is None or merged != before:
            row.verification_status = "extracted" if source_type in ("document", "agent", "system") else "self_reported"
        self.db.flush()
        if before != merged or evidence:
            self._revision(workspace_id, kind, row, before, source_type, claim_origin, capture_method, evidence)
        if old_goal:
            old_before = {**self._values("goal", old_goal), "status": old_goal.status}
            old_goal.status = "superseded"
            self._revision(workspace_id, "goal", old_goal, old_before, source_type, claim_origin, capture_method, evidence)
        self.db.flush()
        return row

    def snapshot(self, workspace_id: str, kinds=None, limit: int | None = None) -> dict:
        return {kind: [{**self._values(kind, record), "id": record.id,
                       "verification_status": self._verification(record)}
                      for record in self.list(workspace_id, kind, limit)]
                for kind in (kinds if kinds is not None else ENTITY_MODELS)}

    @staticmethod
    def _verification(record):
        expiry = getattr(record, "expiry_date", None) or getattr(record, "expires_on", None)
        if expiry and expiry < date.today().isoformat()[:len(expiry)]:
            return "expired"
        return record.verification_status

    def history(self, workspace_id: str, kind: str, record_id: str):
        return list(self.db.execute(select(StudentRecordRevision).where(
            StudentRecordRevision.workspace_id == workspace_id, StudentRecordRevision.record_type == kind,
            StudentRecordRevision.record_id == record_id).order_by(StudentRecordRevision.created_at)).scalars())

    def issues(self, workspace_id: str):
        return list(self.db.execute(select(ProfileIssue).where(ProfileIssue.workspace_id == workspace_id,
            ProfileIssue.status == "open").order_by(ProfileIssue.created_at.desc()).limit(20)).scalars())
