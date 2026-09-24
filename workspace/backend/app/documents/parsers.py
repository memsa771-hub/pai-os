# -*- coding: utf-8 -*-
"""Deterministic PDF and DOCX parsing.

No LLM runs here. Given the same bytes these functions return the same
segments, which is what lets an evidence quote be checked against the
document rather than trusted.

The one non-deterministic path — OCR for scanned PDFs — is isolated behind
`app.documents.ocr` and only reached when deterministic extraction comes back
effectively empty.
"""

from __future__ import annotations

import io
import logging
from typing import Optional

from .content import MAX_CONTENT_CHARS, ParsedDocument, Segment, truncate_segments

logger = logging.getLogger(__name__)


class DocumentParseError(RuntimeError):
    """The document could not be parsed. Carries a SAFE, stable code.

    The message is written by us and never interpolates parser output, which
    can quote document text (and therefore student PII) into logs and rows.
    """

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


#: Below this many characters across the whole document, a PDF is treated as
#: image-only. A scanned page still yields a few stray glyphs from headers or
#: an embedded logo's metadata, so zero is the wrong threshold.
_OCR_TRIGGER_CHARS = 32

#: Per-page floor for mixed documents — a scanned insert inside an otherwise
#: digital PDF should still be OCR'd.
_OCR_PAGE_TRIGGER_CHARS = 16


def parse_pdf(data: bytes) -> ParsedDocument:
    """Extract text per page with pypdf. One segment per page.

    Page numbers are the evidence locators, so a page that fails to extract
    still produces its (empty) segment: dropping it would renumber every page
    after it and silently move every later quote.
    """
    try:
        from pypdf import PdfReader
    except ImportError as exc:  # pragma: no cover - dependency is declared
        raise DocumentParseError(
            "parser_unavailable", "The PDF parser is not available.",
        ) from exc

    try:
        reader = PdfReader(io.BytesIO(data))
    except Exception as exc:
        logger.warning("pdf open failed: %s", type(exc).__name__)
        raise DocumentParseError(
            "parse_failed", "This PDF could not be opened.",
        ) from exc

    if getattr(reader, "is_encrypted", False):
        # An empty user password is common for "protected" PDFs and is worth
        # one attempt; a real password is not something we can ask for here.
        try:
            reader.decrypt("")
        except Exception:  # noqa: BLE001
            raise DocumentParseError(
                "password_protected",
                "This PDF is password protected, so its contents cannot be read.",
            ) from None

    segments: list[Segment] = []
    failed_pages = 0
    for index, page in enumerate(reader.pages, start=1):
        try:
            text = page.extract_text() or ""
        except Exception as exc:  # noqa: BLE001 — one bad page must not lose the rest
            logger.warning("pdf page %d extract failed: %s", index, type(exc).__name__)
            text = ""
            failed_pages += 1
        segments.append(Segment(locator=f"p{index}", text=text.strip(), kind="text"))

    if not segments:
        raise DocumentParseError("empty_document", "This PDF has no pages.")

    kept, truncated = truncate_segments(segments)
    return ParsedDocument(
        document_type="pdf",
        parser="pypdf",
        segments=kept,
        page_count=len(segments),
        partial=failed_pages > 0,
        truncated=truncated,
    )


def needs_ocr(parsed: ParsedDocument) -> bool:
    """True when deterministic extraction found essentially no text."""
    return parsed.document_type == "pdf" and parsed.char_count < _OCR_TRIGGER_CHARS


def pages_needing_ocr(parsed: ParsedDocument) -> list[int]:
    """1-based page numbers whose text is too thin to be real content."""
    return [
        index
        for index, segment in enumerate(parsed.segments, start=1)
        if len(segment.text) < _OCR_PAGE_TRIGGER_CHARS
    ]


def render_pdf_page_png(data: bytes, page_number: int, scale: float = 2.0) -> bytes:
    """Render one 1-based page to PNG bytes, for OCR.

    Only used on the scanned-document path; a digital PDF never reaches this.
    """
    try:
        import pypdfium2
    except ImportError as exc:  # pragma: no cover - dependency is declared
        raise DocumentParseError(
            "ocr_unavailable", "Scanned documents cannot be read right now.",
        ) from exc

    document = pypdfium2.PdfDocument(io.BytesIO(data))
    try:
        page = document[page_number - 1]
        bitmap = page.render(scale=scale)
        image = bitmap.to_pil()
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        return buffer.getvalue()
    finally:
        document.close()


def parse_docx(data: bytes) -> ParsedDocument:
    """Extract paragraphs, headings and tables in document order.

    Order matters: "Semester 5" as a heading followed by its results table is
    what makes the table interpretable, so body children are walked in their
    real sequence rather than reading all paragraphs then all tables.
    """
    try:
        import docx
        from docx.table import Table
        from docx.text.paragraph import Paragraph
    except ImportError as exc:  # pragma: no cover - dependency is declared
        raise DocumentParseError(
            "parser_unavailable", "The Word document parser is not available.",
        ) from exc

    try:
        document = docx.Document(io.BytesIO(data))
    except Exception as exc:
        logger.warning("docx open failed: %s", type(exc).__name__)
        raise DocumentParseError(
            "parse_failed", "This Word document could not be opened.",
        ) from exc

    segments: list[Segment] = []
    paragraph_index = 0
    table_index = 0

    body = document.element.body
    for child in body.iterchildren():
        tag = child.tag.rsplit("}", 1)[-1]
        if tag == "p":
            paragraph = Paragraph(child, document)
            text = (paragraph.text or "").strip()
            if not text:
                continue
            paragraph_index += 1
            style = (getattr(paragraph.style, "name", "") or "").lower()
            segments.append(Segment(
                locator=f"para{paragraph_index}",
                text=text,
                kind="heading" if style.startswith("heading") or style == "title" else "text",
            ))
        elif tag == "tbl":
            table = Table(child, document)
            rows: list[list[str]] = []
            for row in table.rows:
                cells = [(cell.text or "").strip() for cell in row.cells]
                if any(cells):
                    rows.append(cells)
            if not rows:
                continue
            table_index += 1
            # The flattened text is what search and extraction read; `tables`
            # keeps the grid for anything that needs the structure.
            flattened = "\n".join(" | ".join(row) for row in rows)
            segments.append(Segment(
                locator=f"table{table_index}",
                text=flattened,
                tables=[rows],
                kind="table",
            ))

    if not segments:
        raise DocumentParseError(
            "empty_document", "This Word document contains no readable text.",
        )

    kept, truncated = truncate_segments(segments)
    return ParsedDocument(
        document_type="docx",
        parser="python-docx",
        segments=kept,
        page_count=0,          # DOCX has no fixed pagination
        truncated=truncated,
    )


def parse_document(data: bytes, document_type: str) -> ParsedDocument:
    """Dispatch to the parser for a validated document type."""
    if document_type == "pdf":
        return parse_pdf(data)
    if document_type == "docx":
        return parse_docx(data)
    raise DocumentParseError(
        "unsupported_type", "Only PDF and Word (.docx) documents can be processed.",
    )
