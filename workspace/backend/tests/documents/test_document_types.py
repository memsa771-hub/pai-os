# -*- coding: utf-8 -*-
"""Phase A — upload validation decided from bytes, not from the filename."""

import unittest

from app.documents import (
    DocumentTypeError,
    detect_document_type,
    is_student_document_candidate,
    sniff_content_type,
)
from tests.documents.fixtures import (
    DOCX_CONTENT_TYPE,
    TRANSCRIPT_PAGES,
    make_docx,
    make_fake_docx,
    make_fake_pdf,
    make_legacy_doc,
    make_pdf,
)


class DetectDocumentTypeTests(unittest.TestCase):
    def test_valid_pdf_is_detected(self):
        kind, content_type = detect_document_type(
            make_pdf(TRANSCRIPT_PAGES), "transcript.pdf", "application/pdf",
        )
        self.assertEqual(kind, "pdf")
        self.assertEqual(content_type, "application/pdf")

    def test_valid_docx_is_detected(self):
        kind, content_type = detect_document_type(
            make_docx(["Statement of Purpose"]), "sop.docx", DOCX_CONTENT_TYPE,
        )
        self.assertEqual(kind, "docx")
        self.assertEqual(content_type, DOCX_CONTENT_TYPE)

    def test_pdf_is_accepted_when_the_browser_mime_is_wrong(self):
        # Browsers routinely send application/octet-stream for a real PDF.
        kind, _ = detect_document_type(
            make_pdf(["hello"]), "transcript.pdf", "application/octet-stream",
        )
        self.assertEqual(kind, "pdf")

    def test_renamed_binary_claiming_pdf_is_rejected(self):
        with self.assertRaises(DocumentTypeError) as caught:
            detect_document_type(make_fake_pdf(), "transcript.pdf", "application/pdf")
        self.assertEqual(caught.exception.code, "unsupported_type")

    def test_renamed_zip_claiming_docx_is_rejected(self):
        # A zip is not a DOCX: the WordprocessingML part has to be present.
        with self.assertRaises(DocumentTypeError) as caught:
            detect_document_type(make_fake_docx(), "cv.docx", DOCX_CONTENT_TYPE)
        self.assertEqual(caught.exception.code, "unsupported_type")

    def test_legacy_doc_is_rejected_by_content(self):
        with self.assertRaises(DocumentTypeError) as caught:
            detect_document_type(make_legacy_doc(), "transcript.doc", "application/msword")
        self.assertEqual(caught.exception.code, "unsupported_type")

    def test_legacy_doc_is_rejected_even_when_renamed_to_docx(self):
        with self.assertRaises(DocumentTypeError) as caught:
            detect_document_type(make_legacy_doc(), "transcript.docx", DOCX_CONTENT_TYPE)
        self.assertEqual(caught.exception.code, "unsupported_type")

    def test_real_docx_named_pdf_is_a_content_mismatch(self):
        with self.assertRaises(DocumentTypeError) as caught:
            detect_document_type(make_docx(["x"]), "transcript.pdf", "application/pdf")
        self.assertEqual(caught.exception.code, "content_mismatch")

    def test_empty_file_is_rejected(self):
        with self.assertRaises(DocumentTypeError):
            detect_document_type(b"", "transcript.pdf", "application/pdf")

    def test_unsupported_formats_are_rejected(self):
        for name, data in (
            ("notes.txt", b"just text"),
            ("data.csv", b"a,b,c\n1,2,3"),
            ("photo.png", b"\x89PNG\r\n\x1a\n" + b"\x00" * 64),
        ):
            with self.subTest(name=name):
                with self.assertRaises(DocumentTypeError):
                    detect_document_type(data, name, "")


class SniffTests(unittest.TestCase):
    def test_sniff_reports_canonical_types(self):
        self.assertEqual(sniff_content_type(make_pdf(["a"])), "application/pdf")
        self.assertEqual(sniff_content_type(make_docx(["a"])), DOCX_CONTENT_TYPE)

    def test_sniff_returns_none_for_other_bytes(self):
        self.assertIsNone(sniff_content_type(make_fake_pdf()))


class CandidateTests(unittest.TestCase):
    def test_document_names_are_candidates(self):
        self.assertTrue(is_student_document_candidate("transcript.pdf", ""))
        self.assertTrue(is_student_document_candidate("cv.docx", ""))
        self.assertTrue(is_student_document_candidate("x", "application/pdf"))

    def test_unrelated_files_are_not_candidates(self):
        # The guarantee that generic workspace uploads keep working untouched.
        for name in ("photo.png", "main.py", "data.csv", "notes.md", "song.mp3"):
            with self.subTest(name=name):
                self.assertFalse(is_student_document_candidate(name, "image/png"))


if __name__ == "__main__":
    unittest.main()
