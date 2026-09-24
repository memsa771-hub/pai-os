# -*- coding: utf-8 -*-
"""Phase C — agent reading, processing states, attachment normalization."""

import asyncio
from types import SimpleNamespace
from unittest.mock import patch

from app.documents.attachments import (
    attachment_prompt_block, describe_attachments, normalize_attachments,
)
from app.documents.reader import read_document
from app.documents.service import DocumentArtifactService
from app.tools.builtin.files import read_file
from tests.documents.conftest import make_file
from tests.documents.fixtures import (
    DOCX_CONTENT_TYPE, TRANSCRIPT_PAGES, make_docx, make_fake_pdf, make_pdf,
)
from tests.documents.test_parse_job import _StubStore, _run


def _parsed(db, workspace_id, filename="transcript.pdf", data=None,
            content_type="application/pdf"):
    data = data if data is not None else make_pdf(TRANSCRIPT_PAGES)
    record = make_file(db, workspace_id, filename, data, content_type)
    DocumentArtifactService(db).register_and_enqueue(
        workspace_id, record.id, data, filename, content_type,
    )
    store = _StubStore({record.storage_key: data})
    with patch("app.documents.handlers.get_file_store", return_value=store):
        _run(db, workspace_id, record.id)
    return record


class TestReadDocument:
    def test_pdf_returns_parsed_text_with_locators(self, db, workspace_id):
        record = _parsed(db, workspace_id)

        result = read_document(db, workspace_id, record.id)

        assert result["ok"] is True
        assert result["status"] == "ready"
        assert "BS Computer Science" in result["content"]
        assert "[p1]" in result["content"]

    def test_docx_returns_parsed_text(self, db, workspace_id):
        record = _parsed(
            db, workspace_id, "sop.docx",
            make_docx(["Statement of Purpose", "I want to study AI."]),
            DOCX_CONTENT_TYPE,
        )

        result = read_document(db, workspace_id, record.id)

        assert result["ok"] is True
        assert "study AI" in result["content"]

    def test_never_returns_binary_garbage(self, db, workspace_id):
        # A renamed binary is unsupported, and says so — it does NOT come
        # back as decoded bytes.
        data = make_fake_pdf()
        record = make_file(db, workspace_id, "transcript.pdf", data)
        DocumentArtifactService(db).register_and_enqueue(
            workspace_id, record.id, data, "transcript.pdf", "application/pdf",
        )

        result = read_document(db, workspace_id, record.id)

        assert result["ok"] is False
        assert result["status"] == "unsupported"
        assert "content" not in result

    def test_processing_state_is_explicit(self, db, workspace_id):
        # The current-turn race: uploaded, asked about immediately.
        data = make_pdf(TRANSCRIPT_PAGES)
        record = make_file(db, workspace_id, "transcript.pdf", data)
        DocumentArtifactService(db).register_and_enqueue(
            workspace_id, record.id, data, "transcript.pdf", "application/pdf",
        )

        result = read_document(db, workspace_id, record.id)

        assert result["ok"] is False
        assert result["status"] == "queued"
        assert result["reason"] == "processing"
        assert "do not guess" in result["message"].lower()

    def test_failed_state_is_explicit(self, db, workspace_id):
        record = _parsed(db, workspace_id)
        artifact = DocumentArtifactService(db).get(workspace_id, record.id)
        DocumentArtifactService(db).mark_failed(
            artifact, "parse_failed", "This document could not be read.")

        result = read_document(db, workspace_id, record.id)

        assert result["ok"] is False
        assert result["status"] == "failed"

    def test_non_document_reports_not_a_document(self, db, workspace_id):
        record = make_file(db, workspace_id, "notes.txt", b"plain text", "text/plain")

        result = read_document(db, workspace_id, record.id)

        assert result["ok"] is False
        assert result["reason"] == "not_a_document"

    def test_reading_is_bounded(self, db, workspace_id):
        record = _parsed(db, workspace_id)

        result = read_document(db, workspace_id, record.id, max_chars=40)

        assert result["ok"] is True
        assert len(result["content"]) <= 200
        assert result["truncated"] is True

    def test_page_range_selects_pages(self, db, workspace_id):
        record = _parsed(db, workspace_id)

        result = read_document(db, workspace_id, record.id, pages="2")

        assert result["locators"] == ["p2"]
        assert "Machine Learning" in result["content"]

    def test_cross_workspace_read_is_impossible(self, db, workspace_id):
        record = _parsed(db, workspace_id)

        result = read_document(db, db.info["other"], record.id)

        assert result["ok"] is False
        assert result["reason"] == "not_a_document"


class TestFilesReadTool:
    def _ctx(self, db, workspace_id, payload):
        class _Api:
            def __init__(self):
                self.text_calls = []

            async def get(self, path, **params):
                return payload

            async def get_text(self, path, max_chars=50000):
                self.text_calls.append(path)
                return {"ok": True, "content": "RAW", "content_type": "text/plain"}

        api = _Api()
        return SimpleNamespace(api=api, workspace_id=workspace_id), api

    def test_parsed_document_is_returned(self, db, workspace_id):
        ctx, api = self._ctx(db, workspace_id, {
            "data": {"ok": True, "status": "ready", "content": "[p1]\nCGPA: 3.41"},
        })

        result = asyncio.run(read_file(ctx, {"file_id": "f1"}))

        assert result["content"] == "[p1]\nCGPA: 3.41"
        # Crucially: the raw-bytes path was never used.
        assert api.text_calls == []

    def test_processing_state_is_surfaced_not_swallowed(self, db, workspace_id):
        ctx, api = self._ctx(db, workspace_id, {
            "data": {"ok": False, "status": "processing", "reason": "processing"},
        })

        result = asyncio.run(read_file(ctx, {"file_id": "f1"}))

        assert result["status"] == "processing"
        assert api.text_calls == []

    def test_plain_text_still_reads_raw(self, db, workspace_id):
        # A .txt is not a student document; the tool stays a general reader.
        ctx, api = self._ctx(db, workspace_id, {
            "data": {"ok": False, "reason": "not_a_document"},
        })

        result = asyncio.run(read_file(ctx, {"file_id": "f1"}))

        assert result["content"] == "RAW"
        assert api.text_calls == ["/v1/files/f1"]


class TestAttachmentNormalization:
    def test_camel_and_snake_both_normalize(self):
        normalized = normalize_attachments([
            {"fileId": "a", "filename": "t.pdf", "contentType": "application/pdf"},
            {"file_id": "b", "filename": "s.docx", "content_type": DOCX_CONTENT_TYPE},
        ])

        assert [a["file_id"] for a in normalized] == ["a", "b"]
        assert normalized[0]["content_type"] == "application/pdf"

    def test_entries_without_a_file_id_are_dropped(self):
        assert normalize_attachments([{"filename": "x.pdf"}, "nonsense", None]) == []

    def test_processing_status_comes_from_the_server(self, db, workspace_id):
        record = _parsed(db, workspace_id)
        # A client claiming "ready" must not be believed; the server decides.
        described = describe_attachments(db, workspace_id, [
            {"file_id": record.id, "filename": "transcript.pdf",
             "content_type": "application/pdf", "processing_status": "lies"},
        ])

        assert described[0]["processing_status"] == "ready"

    def test_counselor_is_told_a_file_exists_but_not_its_contents(self, db, workspace_id):
        record = _parsed(db, workspace_id)
        described = describe_attachments(db, workspace_id, [
            {"file_id": record.id, "filename": "transcript.pdf",
             "content_type": "application/pdf"},
        ])

        block = attachment_prompt_block(described)

        assert record.id in block
        assert "transcript.pdf" in block
        assert "files.read" in block
        # The document's actual text must NOT be in conversation context.
        assert "BS Computer Science" not in block
        assert "3.41" not in block

    def test_unprocessed_attachment_warns_against_guessing(self, db, workspace_id):
        data = make_pdf(TRANSCRIPT_PAGES)
        record = make_file(db, workspace_id, "transcript.pdf", data)
        DocumentArtifactService(db).register_and_enqueue(
            workspace_id, record.id, data, "transcript.pdf", "application/pdf",
        )
        described = describe_attachments(db, workspace_id, [
            {"file_id": record.id, "filename": "transcript.pdf",
             "content_type": "application/pdf"},
        ])

        block = attachment_prompt_block(described)

        assert "never guess" in block.lower()
