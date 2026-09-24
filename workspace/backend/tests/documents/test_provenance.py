# -*- coding: utf-8 -*-
"""Authority-driven provenance and corroboration — found by the E2E run.

The end-to-end transcript run showed three things the unit tests had missed:
records were stamped `institution_document` purely because source_type was
"document", a registrar-backed record stayed `extracted`, and a document
restating a Vault value was reported as a LOST CONFLICT. These pin the fixes.
"""

from sqlalchemy import select

from app.memory.candidates import MemoryCandidateService
from app.memory.reconciler import MemoryReconciler, provenance_for
from app.memory.student_records import StudentRecordService
from app.memory.vault import VaultOutcome, VaultService
from app.models import EducationRecord, ProfileIssue, VaultFact


def _propose(db, workspace_id, *, candidate_type="student_record", key="education",
             value=None, authority="institution_issued", source_type="document",
             quote="BS Computer Science", locator="p1", file_id="file-1"):
    return MemoryCandidateService(db).propose(
        workspace_id=workspace_id, candidate_type=candidate_type, key=key,
        proposed_value=value, confidence=0.9, source_type=source_type,
        evidence={"quote": quote, "locator": locator, "file_id": file_id,
                  "authority": authority, "document_type": "transcript"},
    )


class TestAuthorityProvenance:
    def test_transcript_is_document_supported(self, db, workspace_id):
        candidate = _propose(db, workspace_id,
                             value={"qualification_name": "BS Computer Science"})
        MemoryReconciler(db).reconcile(candidate)

        row = db.execute(select(EducationRecord)).scalars().one()
        assert row.claim_origin == "institution_document"
        assert row.verification_status == "document_supported"

    def test_cv_is_not_recorded_as_institution_issued(self, db, workspace_id):
        # The spec's explicit requirement: file present != institution document.
        candidate = _propose(db, workspace_id, authority="student_authored",
                             value={"qualification_name": "BS Computer Science"})
        MemoryReconciler(db).reconcile(candidate)

        row = db.execute(select(EducationRecord)).scalars().one()
        assert row.claim_origin == "student_document"
        assert row.verification_status == "self_reported"

    def test_missing_authority_is_unknown_never_official(self, db, workspace_id):
        candidate = _propose(db, workspace_id, authority=None,
                             value={"qualification_name": "BS Computer Science"})
        origin, method, verification = provenance_for(candidate)

        assert origin == "unknown_document"
        assert verification == "extracted"
        assert method == "document_extraction"

    def test_conversation_provenance_is_unchanged(self, db, workspace_id):
        candidate = MemoryCandidateService(db).propose(
            workspace_id=workspace_id, candidate_type="student_record",
            key="education", proposed_value={"qualification_name": "BS CS"},
            confidence=0.9, source_type="conversation", evidence={"quote": "BS CS"},
        )
        assert provenance_for(candidate) == ("student", "conversation_extraction", None)


class TestVaultCorroboration:
    def _seed(self, db, workspace_id, source_type="user_explicit"):
        # A free-text field outside preferences/career/mobility, so the normal
        # document-vs-student conflict policy applies. (education.cgpa is
        # entity-backed — CGPA conflicts are record conflicts, covered in
        # test_reconciliation.py.)
        key = "location.current_country"
        VaultService(db).fields.upsert_definition({
            "key": key, "category": "location", "data_type": "string",
            "validation_schema": {"type": "string"},
        })
        value = "Germany"
        VaultService(db).apply_fact(workspace_id, key, value, source_type=source_type,
                                    evidence={"quote": "I live in Germany"})
        db.flush()
        return key, value

    def test_agreeing_document_corroborates_instead_of_losing(self, db, workspace_id):
        key, value = self._seed(db, workspace_id)
        candidate = _propose(db, workspace_id, candidate_type="vault_fact",
                             key=key, value=value, quote="Germany")

        result = MemoryReconciler(db).reconcile(candidate)

        # Previously: rejected as "retained existing value (conflict lost)".
        assert result.accepted is True
        assert result.outcome == VaultOutcome.CORROBORATED.value
        assert db.execute(select(ProfileIssue)).scalars().all() == []

    def test_corroboration_creates_no_duplicate_fact(self, db, workspace_id):
        key, value = self._seed(db, workspace_id, source_type="conversation")
        candidate = _propose(db, workspace_id, candidate_type="vault_fact",
                             key=key, value=value, quote="Germany")
        MemoryReconciler(db).reconcile(candidate)

        facts = db.execute(select(VaultFact).where(VaultFact.field_key == key)).scalars().all()
        # Previously latest_wins superseded it with an identical second row.
        assert len(facts) == 1
        assert facts[0].status == "active"

    def test_corroboration_keeps_original_provenance_and_adds_the_document(self, db, workspace_id):
        key, value = self._seed(db, workspace_id)
        candidate = _propose(db, workspace_id, candidate_type="vault_fact",
                             key=key, value=value, quote="Germany")
        MemoryReconciler(db).reconcile(candidate)

        fact = db.execute(select(VaultFact).where(VaultFact.field_key == key)).scalars().one()
        assert fact.source_type == "user_explicit"          # who first told us
        assert fact.evidence["quote"] == "I live in Germany"
        assert fact.evidence["corroborations"][0]["file_id"] == "file-1"

    def test_reprocessing_does_not_double_count(self, db, workspace_id):
        key, value = self._seed(db, workspace_id)
        for _ in range(2):
            candidate = _propose(db, workspace_id, candidate_type="vault_fact",
                                 key=key, value=value, quote="Germany")
            MemoryReconciler(db).reconcile(candidate)

        fact = db.execute(select(VaultFact).where(VaultFact.field_key == key)).scalars().one()
        assert len(fact.evidence["corroborations"]) == 1

    def test_disagreeing_document_still_conflicts(self, db, workspace_id):
        key, _ = self._seed(db, workspace_id)
        candidate = _propose(db, workspace_id, candidate_type="vault_fact",
                             key=key, value="Canada", quote="Canada")

        result = MemoryReconciler(db).reconcile(candidate)

        assert result.accepted is False
        assert len(db.execute(select(ProfileIssue)).scalars().all()) == 1


class TestRecordCorroboration:
    def test_confirming_document_keeps_original_evidence(self, db, workspace_id):
        StudentRecordService(db).apply(
            workspace_id, "education", {"qualification_name": "BS Computer Science"},
            source_type="conversation", claim_origin="student",
            capture_method="conversation_extraction",
            evidence={"quote": "I did a BS in Computer Science"},
        )
        db.flush()
        candidate = _propose(db, workspace_id,
                             value={"qualification_name": "BS Computer Science"})

        MemoryReconciler(db).reconcile(candidate)

        row = db.execute(select(EducationRecord)).scalars().one()
        # Previously the document's evidence OVERWROTE the student's.
        assert row.evidence["quote"] == "I did a BS in Computer Science"
        assert row.evidence["corroborations"][0]["file_id"] == "file-1"

    def test_transcript_confirmation_raises_verification(self, db, workspace_id):
        StudentRecordService(db).apply(
            workspace_id, "education", {"qualification_name": "BS Computer Science"},
            source_type="conversation", claim_origin="student",
            capture_method="conversation_extraction",
        )
        db.flush()
        candidate = _propose(db, workspace_id,
                             value={"qualification_name": "BS Computer Science"})

        MemoryReconciler(db).reconcile(candidate)

        row = db.execute(select(EducationRecord)).scalars().one()
        assert row.verification_status == "document_supported"

    def test_weaker_corroboration_never_demotes(self, db, workspace_id):
        first = _propose(db, workspace_id,
                         value={"qualification_name": "BS Computer Science"})
        MemoryReconciler(db).reconcile(first)
        # A self-authored CV later agrees with the transcript-backed record.
        cv = _propose(db, workspace_id, authority="student_authored", file_id="cv-1",
                      value={"qualification_name": "BS Computer Science"})
        MemoryReconciler(db).reconcile(cv)

        row = db.execute(select(EducationRecord)).scalars().one()
        assert row.verification_status == "document_supported"
