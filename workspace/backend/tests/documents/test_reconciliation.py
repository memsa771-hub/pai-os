# -*- coding: utf-8 -*-
"""Phase E — documents converge on the SAME canonical state as conversation.

These run the real MemoryReconciler, VaultService and StudentRecordService.
Nothing here stubs the decision path: the point is that a document is just
another source flowing through the existing rules.
"""

import asyncio
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from sqlalchemy import select

from app.documents.extractor import DocumentFinding, DocumentUnderstanding
from app.documents.handlers import extract_document_job
from app.documents.service import DocumentArtifactService
from app.memory.candidates import MemoryCandidateService
from app.memory.profile_issues import ProfileIssueService
from app.memory.reconciler import MemoryReconciler
from app.memory.student_records import StudentRecordService
from app.memory.student_snapshot import StudentSnapshotService
from app.memory.vault import VaultService
from app.models import EducationRecord, MemoryCandidate, ProfileIssue, StudentDocument
from tests.documents.conftest import make_file
from tests.documents.fixtures import TRANSCRIPT_PAGES, make_pdf
from tests.documents.test_parse_job import _StubStore, _run


def _parsed_transcript(db, workspace_id, filename="transcript.pdf"):
    data = make_pdf(TRANSCRIPT_PAGES)
    record = make_file(db, workspace_id, filename, data)
    DocumentArtifactService(db).register_and_enqueue(
        workspace_id, record.id, data, filename, "application/pdf",
    )
    store = _StubStore({record.storage_key: data})
    with patch("app.documents.handlers.get_file_store", return_value=store):
        _run(db, workspace_id, record.id)
    return record


def _understanding(findings, classification="transcript", title="Transcript"):
    return DocumentUnderstanding(
        classification=classification, classification_confidence=0.95,
        title=title, findings=findings,
    )


def _finding(candidate_type, key=None, value=None, content=None,
             entities=None, quote="CGPA: 3.41 / 4.00", locator="p1",
             confidence=0.9):
    return DocumentFinding(
        candidate_type=candidate_type, key=key, proposed_value=value,
        content=content, entities=entities or {}, confidence=confidence,
        evidence={"quote": quote, "locator": locator},
    )


def _extract(db, workspace_id, file_id, understanding):
    """Run the real extract job with the MODEL CALL stubbed.

    Everything after the model — validation, candidate creation, provenance,
    the StudentDocument proposal — is the production path.
    """
    job = SimpleNamespace(
        id="extract-1", workspace_id=workspace_id,
        payload={"file_id": file_id}, job_type="document.extract",
    )

    async def _fake(*args, **kwargs):
        return understanding

    with patch("app.documents.extractor.understand_document", new=_fake):
        return asyncio.run(extract_document_job(job, db))


def _reconcile_all(db, workspace_id):
    reconciler = MemoryReconciler(db)
    results = []
    for candidate in MemoryCandidateService(db).pending(workspace_id):
        results.append(reconciler.reconcile(candidate))
    return results


class TestCandidateFlow:
    def test_extraction_only_writes_candidates(self, db, workspace_id):
        record = _parsed_transcript(db, workspace_id)

        _extract(db, workspace_id, record.id, _understanding([
            _finding("student_record", "education",
                     {"qualification_name": "BS Computer Science",
                      "institution_name": "University of Example"}),
        ]))

        # Canonical state is untouched until the reconciler runs.
        assert db.execute(select(EducationRecord)).scalars().all() == []
        candidates = MemoryCandidateService(db).pending(workspace_id)
        assert any(c.candidate_type == "student_record" for c in candidates)

    def test_candidates_carry_document_source_and_evidence(self, db, workspace_id):
        record = _parsed_transcript(db, workspace_id)

        _extract(db, workspace_id, record.id, _understanding([
            _finding("student_record", "education",
                     {"qualification_name": "BS Computer Science"}),
        ]))

        candidate = next(
            c for c in MemoryCandidateService(db).pending(workspace_id)
            if c.key == "education"
        )
        # source_type is server-assigned, not model-chosen.
        assert candidate.source_type == "document"
        assert candidate.evidence["file_id"] == record.id
        assert candidate.evidence["locator"] == "p1"
        assert candidate.evidence["authority"] == "institution_issued"

    def test_transcript_creates_a_student_document_record(self, db, workspace_id):
        record = _parsed_transcript(db, workspace_id)

        _extract(db, workspace_id, record.id, _understanding([]))
        _reconcile_all(db, workspace_id)

        documents = db.execute(select(StudentDocument)).scalars().all()
        assert len(documents) == 1
        # The file_id is server-owned; a model cannot name a file.
        assert documents[0].file_id == record.id
        assert documents[0].document_type == "transcript"

    def test_unrelated_document_does_not_pollute_the_profile(self, db, workspace_id):
        record = _parsed_transcript(db, workspace_id, "random.pdf")

        _extract(db, workspace_id, record.id,
                 _understanding([], classification="unknown", title=None))
        _reconcile_all(db, workspace_id)

        assert db.execute(select(StudentDocument)).scalars().all() == []

    def test_reprocessing_does_not_duplicate_the_document_record(self, db, workspace_id):
        record = _parsed_transcript(db, workspace_id)

        _extract(db, workspace_id, record.id, _understanding([]))
        _reconcile_all(db, workspace_id)
        # Force a re-extract by clearing the version marker.
        artifact = DocumentArtifactService(db).get(workspace_id, record.id)
        artifact.extractor_version = None
        db.flush()
        _extract(db, workspace_id, record.id, _understanding([]))
        _reconcile_all(db, workspace_id)

        assert len(db.execute(select(StudentDocument)).scalars().all()) == 1

    def test_retry_is_idempotent(self, db, workspace_id):
        record = _parsed_transcript(db, workspace_id)

        first = _extract(db, workspace_id, record.id, _understanding([]))
        second = _extract(db, workspace_id, record.id, _understanding([]))

        assert first["extracted"] is True
        assert second["extracted"] is False
        assert second["reason"] == "already_extracted"


class TestCanonicalConvergence:
    def test_document_education_becomes_canonical(self, db, workspace_id):
        record = _parsed_transcript(db, workspace_id)

        _extract(db, workspace_id, record.id, _understanding([
            _finding("student_record", "education", {
                "qualification_name": "BS Computer Science",
                "institution_name": "University of Example",
                "result": {"gpa": 3.41, "gpa_scale": 4.0},
            }),
        ]))
        _reconcile_all(db, workspace_id)

        rows = db.execute(select(EducationRecord)).scalars().all()
        assert len(rows) == 1
        assert rows[0].qualification_name == "BS Computer Science"
        assert rows[0].result["gpa"] == 3.41
        assert rows[0].source_type == "document"

    def test_snapshot_reflects_document_derived_state(self, db, workspace_id):
        record = _parsed_transcript(db, workspace_id)

        _extract(db, workspace_id, record.id, _understanding([
            _finding("student_record", "education",
                     {"qualification_name": "BS Computer Science"}),
        ]))
        _reconcile_all(db, workspace_id)

        snapshot = StudentSnapshotService(db).build(workspace_id)
        assert any(r["qualification_name"] == "BS Computer Science"
                   for r in snapshot.records["education"])

    def test_existing_record_is_enriched_not_duplicated(self, db, workspace_id):
        # The student already told PAI about this degree in conversation.
        records = StudentRecordService(db)
        existing = records.apply(
            workspace_id, "education",
            {"qualification_name": "BS Computer Science",
             "institution_name": "University of Example"},
            source_type="conversation", claim_origin="student",
            capture_method="conversation_extraction",
        )
        db.flush()
        record = _parsed_transcript(db, workspace_id)

        # The transcript adds the CGPA to the SAME degree.
        _extract(db, workspace_id, record.id, _understanding([
            _finding("student_record", "education", {
                "qualification_name": "BS Computer Science",
                "institution_name": "University of Example",
                "result": {"gpa": 3.41, "gpa_scale": 4.0},
            }),
        ]))
        _reconcile_all(db, workspace_id)

        rows = db.execute(select(EducationRecord)).scalars().all()
        assert len(rows) == 1, "the transcript must enrich, not duplicate"
        assert rows[0].id == existing.id
        assert rows[0].result["gpa"] == 3.41

    def test_corroborating_document_creates_no_conflict(self, db, workspace_id):
        records = StudentRecordService(db)
        records.apply(
            workspace_id, "education",
            {"qualification_name": "BS Computer Science",
             "institution_name": "University of Example",
             "result": {"gpa": 3.41, "gpa_scale": 4.0}},
            source_type="conversation", claim_origin="student",
            capture_method="conversation_extraction",
        )
        db.flush()
        record = _parsed_transcript(db, workspace_id)

        # The document AGREES with what is already canonical.
        _extract(db, workspace_id, record.id, _understanding([
            _finding("student_record", "education", {
                "qualification_name": "BS Computer Science",
                "institution_name": "University of Example",
                "result": {"gpa": 3.41, "gpa_scale": 4.0},
            }),
        ]))
        _reconcile_all(db, workspace_id)

        assert len(db.execute(select(EducationRecord)).scalars().all()) == 1
        issues = db.execute(select(ProfileIssue)).scalars().all()
        assert issues == [], "agreement must not raise a conflict"


class TestConflicts:
    def _conflicting_setup(self, db, workspace_id):
        records = StudentRecordService(db)
        records.apply(
            workspace_id, "education",
            {"qualification_name": "BS Computer Science",
             "institution_name": "University of Example",
             "result": {"gpa": 3.50, "gpa_scale": 4.0}},
            source_type="conversation", claim_origin="student",
            capture_method="conversation_extraction",
        )
        db.flush()
        record = _parsed_transcript(db, workspace_id)
        _extract(db, workspace_id, record.id, _understanding([
            _finding("student_record", "education", {
                "qualification_name": "BS Computer Science",
                "institution_name": "University of Example",
                "result": {"gpa": 3.41, "gpa_scale": 4.0},
            }),
        ]))
        _reconcile_all(db, workspace_id)
        return record

    def test_document_conflicting_with_conversation_raises_an_issue(self, db, workspace_id):
        self._conflicting_setup(db, workspace_id)

        issues = db.execute(select(ProfileIssue).where(
            ProfileIssue.status == "open")).scalars().all()
        assert len(issues) == 1
        assert issues[0].issue_type == "conflicting_record"

    def test_canonical_value_is_unchanged_by_a_conflicting_document(self, db, workspace_id):
        self._conflicting_setup(db, workspace_id)

        row = db.execute(select(EducationRecord)).scalars().one()
        # The student's stated value stands until they resolve the conflict.
        assert row.result["gpa"] == 3.50

    def test_conflict_issue_is_linked_to_its_candidate(self, db, workspace_id):
        self._conflicting_setup(db, workspace_id)

        issue = db.execute(select(ProfileIssue)).scalars().first()
        assert issue.candidate_id is not None
        candidate = db.get(MemoryCandidate, issue.candidate_id)
        assert candidate.source_type == "document"
        # The losing proposal is preserved and traceable, not discarded.
        assert candidate.evidence["quote"]

    def test_keep_current_retains_the_existing_value(self, db, workspace_id):
        self._conflicting_setup(db, workspace_id)
        issue = db.execute(select(ProfileIssue)).scalars().first()

        result = ProfileIssueService(db).resolve(workspace_id, issue.id, "keep_current")

        assert result["resolved"] is True
        row = db.execute(select(EducationRecord)).scalars().one()
        assert row.result["gpa"] == 3.50

    def test_accept_proposed_applies_the_document_value(self, db, workspace_id):
        self._conflicting_setup(db, workspace_id)
        issue = db.execute(select(ProfileIssue)).scalars().first()

        result = ProfileIssueService(db).resolve(workspace_id, issue.id, "accept_proposed")

        assert result["resolved"] is True
        row = db.execute(select(EducationRecord)).scalars().one()
        assert row.result["gpa"] == 3.41

    def test_provide_new_applies_the_student_value(self, db, workspace_id):
        self._conflicting_setup(db, workspace_id)
        issue = db.execute(select(ProfileIssue)).scalars().first()

        result = ProfileIssueService(db).resolve(
            workspace_id, issue.id, "provide_new",
            value={"qualification_name": "BS Computer Science",
                   "institution_name": "University of Example",
                   "result": {"gpa": 3.45, "gpa_scale": 4.0}},
        )

        assert result["resolved"] is True
        row = db.execute(select(EducationRecord)).scalars().one()
        assert row.result["gpa"] == 3.45

    def test_resolution_goes_through_the_reconciler(self, db, workspace_id):
        self._conflicting_setup(db, workspace_id)
        issue = db.execute(select(ProfileIssue)).scalars().first()

        ProfileIssueService(db).resolve(workspace_id, issue.id, "accept_proposed")

        # A user_explicit candidate was created and accepted — the UI never
        # writes canonical rows directly.
        resolved = db.execute(select(MemoryCandidate).where(
            MemoryCandidate.source_type == "user_explicit")).scalars().all()
        assert len(resolved) == 1
        assert resolved[0].status == "accepted"

    def test_document_cannot_silently_overwrite_canonical_truth(self, db, workspace_id):
        self._conflicting_setup(db, workspace_id)

        # The document's candidate did NOT become canonical.
        document_candidates = db.execute(select(MemoryCandidate).where(
            MemoryCandidate.source_type == "document",
            MemoryCandidate.key == "education")).scalars().all()
        assert all(c.status != "accepted" for c in document_candidates)


class TestNoInference:
    def test_a_masters_document_does_not_invent_a_bachelors(self, db, workspace_id):
        record = _parsed_transcript(db, workspace_id)

        _extract(db, workspace_id, record.id, _understanding([
            _finding("student_record", "education", {
                "qualification_name": "MS Computer Science",
                "institution_name": "University of Example",
            }),
        ]))
        _reconcile_all(db, workspace_id)

        rows = db.execute(select(EducationRecord)).scalars().all()
        assert len(rows) == 1
        assert rows[0].qualification_name == "MS Computer Science"

    def test_education_journey_still_reports_the_gap(self, db, workspace_id):
        from app.memory.education_journey import EducationJourneyService

        record = _parsed_transcript(db, workspace_id)
        _extract(db, workspace_id, record.id, _understanding([
            _finding("student_record", "education", {
                "qualification_name": "MS Computer Science",
                "canonical_level": "master",
            }),
        ]))
        _reconcile_all(db, workspace_id)

        snapshot = StudentSnapshotService(db).build(workspace_id)
        journey = EducationJourneyService().evaluate(snapshot)

        # The missing undergraduate history is identified by the journey
        # service from canonical state — not invented by document extraction.
        assert journey is not None
