# -*- coding: utf-8 -*-
"""Phase A — derived processing state, idempotent enqueue, purge lifecycle."""

from sqlalchemy import select

from app.documents.service import (
    JOB_DOCUMENT_PARSE, PARSER_VERSION, DocumentArtifactService, content_sha256,
)
from app.models import BackgroundJob, DocumentArtifact
from tests.documents.conftest import make_file
from tests.documents.fixtures import (
    DOCX_CONTENT_TYPE, TRANSCRIPT_PAGES, make_docx, make_fake_pdf,
    make_legacy_doc, make_pdf,
)


def _jobs(db, job_type=JOB_DOCUMENT_PARSE):
    return list(db.execute(
        select(BackgroundJob).where(BackgroundJob.job_type == job_type)
    ).scalars().all())


class TestRegistration:
    def test_pdf_is_queued_with_hash_and_type(self, db, workspace_id):
        data = make_pdf(TRANSCRIPT_PAGES)
        record = make_file(db, workspace_id, "transcript.pdf", data)

        artifact = DocumentArtifactService(db).register_and_enqueue(
            workspace_id, record.id, data, "transcript.pdf", "application/pdf",
        )

        assert artifact.status == "queued"
        assert artifact.document_type == "pdf"
        assert artifact.content_sha256 == content_sha256(data)
        assert artifact.parser_version == PARSER_VERSION
        assert len(_jobs(db)) == 1

    def test_docx_is_queued(self, db, workspace_id):
        data = make_docx(["Statement of Purpose", "I want to study AI."])
        record = make_file(db, workspace_id, "sop.docx", data, DOCX_CONTENT_TYPE)

        artifact = DocumentArtifactService(db).register_and_enqueue(
            workspace_id, record.id, data, "sop.docx", DOCX_CONTENT_TYPE,
        )

        assert artifact.status == "queued"
        assert artifact.document_type == "docx"

    def test_fake_pdf_is_recorded_unsupported_and_never_queued(self, db, workspace_id):
        data = make_fake_pdf()
        record = make_file(db, workspace_id, "transcript.pdf", data)

        artifact = DocumentArtifactService(db).register_and_enqueue(
            workspace_id, record.id, data, "transcript.pdf", "application/pdf",
        )

        assert artifact.status == "unsupported"
        assert artifact.error_code == "unsupported_type"
        # The point of the test: nothing enters the processing pipeline.
        assert _jobs(db) == []

    def test_legacy_doc_is_unsupported(self, db, workspace_id):
        data = make_legacy_doc()
        record = make_file(db, workspace_id, "old.doc", data, "application/msword")

        artifact = DocumentArtifactService(db).register_and_enqueue(
            workspace_id, record.id, data, "old.doc", "application/msword",
        )

        assert artifact.status == "unsupported"
        assert _jobs(db) == []

    def test_status_for_unknown_file_is_unsupported(self, db, workspace_id):
        assert DocumentArtifactService(db).status_for(workspace_id, "missing") == "unsupported"


class TestIdempotency:
    def test_registering_twice_reuses_one_artifact_row(self, db, workspace_id):
        data = make_pdf(TRANSCRIPT_PAGES)
        record = make_file(db, workspace_id, "transcript.pdf", data)
        service = DocumentArtifactService(db)

        first = service.register_and_enqueue(
            workspace_id, record.id, data, "transcript.pdf", "application/pdf")
        second = service.register_and_enqueue(
            workspace_id, record.id, data, "transcript.pdf", "application/pdf")

        assert first.id == second.id
        rows = list(db.execute(select(DocumentArtifact)).scalars().all())
        assert len(rows) == 1

    def test_retry_does_not_queue_duplicate_work(self, db, workspace_id):
        data = make_pdf(TRANSCRIPT_PAGES)
        record = make_file(db, workspace_id, "transcript.pdf", data)
        service = DocumentArtifactService(db)

        service.register_and_enqueue(
            workspace_id, record.id, data, "transcript.pdf", "application/pdf")
        service.register_and_enqueue(
            workspace_id, record.id, data, "transcript.pdf", "application/pdf")

        # Idempotency key is (file, content hash, parser version).
        assert len(_jobs(db)) == 1

    def test_identical_bytes_in_a_second_file_get_their_own_artifact(self, db, workspace_id):
        data = make_pdf(TRANSCRIPT_PAGES)
        first = make_file(db, workspace_id, "transcript.pdf", data)
        second = make_file(db, workspace_id, "transcript-copy.pdf", data)
        service = DocumentArtifactService(db)

        a = service.register_and_enqueue(
            workspace_id, first.id, data, "transcript.pdf", "application/pdf")
        b = service.register_and_enqueue(
            workspace_id, second.id, data, "transcript-copy.pdf", "application/pdf")

        # Distinct files: distinct derived rows, even for identical content.
        assert a.id != b.id
        assert a.content_sha256 == b.content_sha256


class TestLifecycle:
    def test_purge_removes_derived_state(self, db, workspace_id):
        data = make_pdf(TRANSCRIPT_PAGES)
        record = make_file(db, workspace_id, "transcript.pdf", data)
        service = DocumentArtifactService(db)
        service.register_and_enqueue(
            workspace_id, record.id, data, "transcript.pdf", "application/pdf")

        removed = service.purge_for_files(workspace_id, [record.id])

        assert removed == 1
        assert service.get(workspace_id, record.id) is None

    def test_purge_is_workspace_scoped(self, db, workspace_id):
        data = make_pdf(TRANSCRIPT_PAGES)
        record = make_file(db, workspace_id, "transcript.pdf", data)
        service = DocumentArtifactService(db)
        service.register_and_enqueue(
            workspace_id, record.id, data, "transcript.pdf", "application/pdf")

        # Another workspace must not be able to purge this student's state.
        removed = service.purge_for_files(db.info["other"], [record.id])

        assert removed == 0
        assert service.get(workspace_id, record.id) is not None

    def test_failures_record_a_safe_message(self, db, workspace_id):
        data = make_pdf(TRANSCRIPT_PAGES)
        record = make_file(db, workspace_id, "transcript.pdf", data)
        service = DocumentArtifactService(db)
        artifact = service.register_and_enqueue(
            workspace_id, record.id, data, "transcript.pdf", "application/pdf")

        service.mark_failed(artifact, "parse_failed", "The document could not be read.")

        assert artifact.status == "failed"
        assert artifact.error_code == "parse_failed"
        assert artifact.processed_at is not None
