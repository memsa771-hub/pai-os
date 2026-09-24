# -*- coding: utf-8 -*-
"""Phase B — real parsing: digital PDF, DOCX, and the scanned-PDF trigger."""

import pytest

from app.documents.content import render_tables, segments_from_content, truncate_segments, Segment
from app.documents.parsers import (
    DocumentParseError, needs_ocr, pages_needing_ocr, parse_docx, parse_document, parse_pdf,
)
from tests.documents.fixtures import (
    TRANSCRIPT_PAGES, make_docx, make_fake_pdf, make_image_only_pdf, make_pdf,
)


class TestPdfParsing:
    def test_digital_pdf_text_is_extracted(self):
        parsed = parse_pdf(make_pdf(TRANSCRIPT_PAGES))

        assert parsed.parser == "pypdf"
        assert parsed.page_count == 2
        assert "BS Computer Science" in parsed.normalized_text
        assert "3.41" in parsed.normalized_text

    def test_pages_become_locators(self):
        parsed = parse_pdf(make_pdf(TRANSCRIPT_PAGES))

        assert [s.locator for s in parsed.segments] == ["p1", "p2"]
        # Page 2's content must be addressable at p2, not merged into p1.
        assert "Machine Learning" in parsed.segments[1].text

    def test_never_returns_binary_as_text(self):
        # The guarantee behind "files.read must not interpret bytes as text".
        with pytest.raises(DocumentParseError):
            parse_document(make_fake_pdf(), "pdf")

    def test_image_only_pdf_has_no_text(self):
        parsed = parse_pdf(make_image_only_pdf(page_count=2))

        assert parsed.page_count == 2
        assert parsed.char_count < 32

    def test_image_only_pdf_triggers_ocr(self):
        parsed = parse_pdf(make_image_only_pdf(page_count=2))

        assert needs_ocr(parsed) is True
        assert pages_needing_ocr(parsed) == [1, 2]

    def test_digital_pdf_does_not_trigger_ocr(self):
        parsed = parse_pdf(make_pdf(TRANSCRIPT_PAGES))

        assert needs_ocr(parsed) is False
        assert pages_needing_ocr(parsed) == []


class TestDocxParsing:
    def test_paragraphs_are_extracted(self):
        parsed = parse_docx(make_docx([
            "Statement of Purpose",
            "I want to study artificial intelligence.",
        ]))

        assert parsed.parser == "python-docx"
        assert "artificial intelligence" in parsed.normalized_text
        assert [s.locator for s in parsed.segments] == ["para1", "para2"]

    def test_tables_are_extracted_with_structure(self):
        parsed = parse_docx(make_docx(
            ["Semester 5"],
            tables=[[["Course", "Grade"], ["Machine Learning", "A-"]]],
        ))

        tables = [s for s in parsed.segments if s.kind == "table"]
        assert len(tables) == 1
        assert tables[0].locator == "table1"
        assert tables[0].tables == [[["Course", "Grade"], ["Machine Learning", "A-"]]]
        # Flattened text keeps the row association readable.
        assert "Machine Learning | A-" in tables[0].text

    def test_document_order_is_preserved(self):
        # A heading followed by its table is what makes the table meaningful.
        parsed = parse_docx(make_docx(
            ["Results"], tables=[[["Course", "Grade"]]],
        ))

        assert [s.kind for s in parsed.segments] == ["text", "table"]

    def test_empty_document_is_rejected(self):
        with pytest.raises(DocumentParseError) as caught:
            parse_docx(make_docx([]))
        assert caught.value.code == "empty_document"

    def test_docx_has_no_page_count(self):
        parsed = parse_docx(make_docx(["text"]))
        assert parsed.page_count == 0


class TestNormalizedContent:
    def test_content_roundtrips_through_storage_shape(self):
        parsed = parse_pdf(make_pdf(TRANSCRIPT_PAGES))
        restored = segments_from_content(parsed.to_content())

        assert [s.locator for s in restored] == [s.locator for s in parsed.segments]
        assert [s.text for s in restored] == [s.text for s in parsed.segments]

    def test_truncation_keeps_whole_segments(self):
        segments = [Segment(locator=f"p{i}", text="x" * 100) for i in range(1, 6)]

        kept, truncated = truncate_segments(segments, max_chars=250)

        assert truncated is True
        # A locator must address the text it really holds, so no partial page.
        assert all(len(s.text) == 100 for s in kept)
        assert len(kept) == 2

    def test_render_tables_is_readable(self):
        rendered = render_tables([[["Course", "Grade"], ["Databases", "B+"]]])
        assert "Course | Grade" in rendered
        assert "Databases | B+" in rendered


class TestDispatch:
    def test_unsupported_type_is_rejected(self):
        with pytest.raises(DocumentParseError) as caught:
            parse_document(b"whatever", "txt")
        assert caught.value.code == "unsupported_type"
