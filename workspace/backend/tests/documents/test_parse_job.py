# -*- coding: utf-8 -*-
"""Phase B — the durable parse job: state, OCR fallback, idempotency."""

import asyncio
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from sqlalchemy import select

from app.documents.handlers import parse_document_job
from app.documents.ocr import OcrPage
from app.documents.service import (
    JOB_DOCUMENT_EXTRACT, DocumentArtifactService,
)
from app.models import BackgroundJob
from tests.documents.conftest import make_file
from tests.documents.fixtures import (
    DOCX_CONTENT_TYPE, TRANSCRIPT_PAGES, make_docx, make_image_only_pdf, make_pdf,
)


class _StubStore:
    """Workspace storage that serves bytes from a dict."""

    def __init__(self, by_key: dict):
        self.by_key = by_key

    def read(self, storage_key: str) -> bytes:
        if storage_key not in self.by_key:
            raise FileNotFoundError(storage_key)
        return self.by_key[storage_key]


class _StubOcr:
    name = "stub_ocr"

    def __init__(self, text: str = "SCANNED TRANSCRIPT\nCGPA: 3.41"):
        self.text = text
        self.calls = 0

    async def transcribe(self, images):
        self.calls += 1
        return [OcrPage(page_number=number, text=self.text) for number, _ in images]


def _run(db, workspace_id, file_id):
    job = SimpleNamespace(
        id="job-1", workspace_id=workspace_id,
        payload={"file_id": file_id}, job_type="document.parse",
    )
    return asyncio.run(parse_document_job(job, db))


def _setup(db, workspace_id, filename, data, content_type="application/pdf"):
    record = make_file(db, workspace_id, filename, data, content_type)
    DocumentArtifactService(db).register_and_enqueue(
        workspace_id, record.id, data, filename, content_type,
    )
    return record, _StubStore({record.storage_key: data})


class TestParseJob:
    def test_pdf_becomes_ready_with_content(self, db, workspace_id):
        data = make_pdf(TRANSCRIPT_PAGES)
        record, store = _setup(db, workspace_id, "transcript.pdf", data)

        with patch("app.documents.handlers.get_file_store", return_value=store):
            result = _run(db, workspace_id, record.id)

        assert result["parsed"] is True
        artifact = DocumentArtifactService(db).get(workspace_id, record.id)
        assert artifact.status == "ready"
        assert artifact.parser == "pypdf"
        assert artifact.page_count == 2
        assert artifact.char_count > 0
        assert "BS Computer Science" in str(artifact.content)

    def test_docx_becomes_ready(self, db, workspace_id):
        data = make_docx(["Statement of Purpose", "I want to study AI."])
        record, store = _setup(db, workspace_id, "sop.docx", data, DOCX_CONTENT_TYPE)

        with patch("app.documents.handlers.get_file_store", return_value=store):
            result = _run(db, workspace_id, record.id)

        assert result["parsed"] is True
        artifact = DocumentArtifactService(db).get(workspace_id, record.id)
        assert artifact.status == "ready"
        assert artifact.parser == "python-docx"

    def test_parsing_enqueues_extraction(self, db, workspace_id):
        data = make_pdf(TRANSCRIPT_PAGES)
        record, store = _setup(db, workspace_id, "transcript.pdf", data)

        with patch("app.documents.handlers.get_file_store", return_value=store):
            _run(db, workspace_id, record.id)

        jobs = list(db.execute(
            select(BackgroundJob).where(BackgroundJob.job_type == JOB_DOCUMENT_EXTRACT)
        ).scalars().all())
        assert len(jobs) == 1

    def test_missing_bytes_fail_safely(self, db, workspace_id):
        data = make_pdf(TRANSCRIPT_PAGES)
        record, _ = _setup(db, workspace_id, "transcript.pdf", data)

        with patch("app.documents.handlers.get_file_store", return_value=_StubStore({})):
            result = _run(db, workspace_id, record.id)

        assert result["parsed"] is False
        artifact = DocumentArtifactService(db).get(workspace_id, record.id)
        assert artifact.status == "failed"
        assert artifact.error_code == "file_unavailable"

    def test_parse_failure_does_not_corrupt_profile_state(self, db, workspace_id):
        # A file that passed validation but cannot be parsed must record a
        # failure, never a half-extraction.
        data = make_pdf(TRANSCRIPT_PAGES)
        record, store = _setup(db, workspace_id, "transcript.pdf", data)

        from app.documents.parsers import DocumentParseError

        with patch("app.documents.handlers.get_file_store", return_value=store), \
             patch("app.documents.handlers.parse_document",
                   side_effect=DocumentParseError("parse_failed", "unreadable")):
            result = _run(db, workspace_id, record.id)

        assert result["parsed"] is False
        artifact = DocumentArtifactService(db).get(workspace_id, record.id)
        assert artifact.status == "failed"
        assert artifact.content is None


class TestIdempotency:
    def test_rerunning_a_parsed_document_is_a_noop(self, db, workspace_id):
        data = make_pdf(TRANSCRIPT_PAGES)
        record, store = _setup(db, workspace_id, "transcript.pdf", data)

        with patch("app.documents.handlers.get_file_store", return_value=store):
            first = _run(db, workspace_id, record.id)
            second = _run(db, workspace_id, record.id)

        assert first["parsed"] is True
        assert second["parsed"] is False
        assert second["reason"] == "already_parsed"

    def test_retry_does_not_duplicate_extraction_jobs(self, db, workspace_id):
        data = make_pdf(TRANSCRIPT_PAGES)
        record, store = _setup(db, workspace_id, "transcript.pdf", data)

        with patch("app.documents.handlers.get_file_store", return_value=store):
            _run(db, workspace_id, record.id)
            _run(db, workspace_id, record.id)

        jobs = list(db.execute(
            select(BackgroundJob).where(BackgroundJob.job_type == JOB_DOCUMENT_EXTRACT)
        ).scalars().all())
        assert len(jobs) == 1

    def test_unsupported_document_is_never_parsed(self, db, workspace_id):
        from tests.documents.fixtures import make_fake_pdf

        data = make_fake_pdf()
        record, store = _setup(db, workspace_id, "transcript.pdf", data)

        with patch("app.documents.handlers.get_file_store", return_value=store):
            result = _run(db, workspace_id, record.id)

        assert result["reason"] == "unsupported"


class TestOcrFallback:
    def test_scanned_pdf_uses_ocr(self, db, workspace_id):
        data = make_image_only_pdf(page_count=1)
        record, store = _setup(db, workspace_id, "scan.pdf", data)
        ocr = _StubOcr()

        with patch("app.documents.handlers.get_file_store", return_value=store), \
             patch("app.documents.ocr.get_ocr_provider", return_value=ocr):
            result = _run(db, workspace_id, record.id)

        assert result["parsed"] is True
        assert ocr.calls == 1
        artifact = DocumentArtifactService(db).get(workspace_id, record.id)
        assert artifact.ocr_used is True
        assert artifact.ocr_provider == "stub_ocr"
        assert "CGPA: 3.41" in str(artifact.content)

    def test_digital_pdf_never_calls_ocr(self, db, workspace_id):
        data = make_pdf(TRANSCRIPT_PAGES)
        record, store = _setup(db, workspace_id, "transcript.pdf", data)
        ocr = _StubOcr()

        with patch("app.documents.handlers.get_file_store", return_value=store), \
             patch("app.documents.ocr.get_ocr_provider", return_value=ocr):
            _run(db, workspace_id, record.id)

        assert ocr.calls == 0
        artifact = DocumentArtifactService(db).get(workspace_id, record.id)
        assert artifact.ocr_used is False

    def test_missing_ocr_provider_yields_partial_not_failure(self, db, workspace_id):
        data = make_image_only_pdf(page_count=1)
        record, store = _setup(db, workspace_id, "scan.pdf", data)

        with patch("app.documents.handlers.get_file_store", return_value=store), \
             patch("app.documents.ocr.get_ocr_provider", return_value=None):
            result = _run(db, workspace_id, record.id)

        # The upload still lands and can be reprocessed later.
        assert result["parsed"] is True
        artifact = DocumentArtifactService(db).get(workspace_id, record.id)
        assert artifact.status == "partial"

    def test_ocr_outage_yields_partial_not_failure(self, db, workspace_id):
        class _Broken:
            name = "broken"

            async def transcribe(self, images):
                raise RuntimeError("provider down")

        data = make_image_only_pdf(page_count=1)
        record, store = _setup(db, workspace_id, "scan.pdf", data)

        with patch("app.documents.handlers.get_file_store", return_value=store), \
             patch("app.documents.ocr.get_ocr_provider", return_value=_Broken()):
            result = _run(db, workspace_id, record.id)

        assert result["parsed"] is True
        artifact = DocumentArtifactService(db).get(workspace_id, record.id)
        assert artifact.status == "partial"
