# -*- coding: utf-8 -*-
"""The normalized shape every parser produces, and every reader consumes.

One representation serves four jobs, which is why it is a list of located
segments rather than a blob of text:

    extraction      needs the text
    evidence        needs a LOCATOR for every quote ("p3", "para12", "table2")
    Operator review needs readable, ordered content
    retrieval       needs chunks that can point back at a source

`normalized_text` is a convenience join, never the source of truth — a quote
must be traceable to the segment that contains it, so the segments are what
get stored.

Deliberately NOT a layout model. Coordinates, fonts and reading-order
reconstruction are a different problem; the requirement here is that every
extracted fact can name real evidence.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

#: Cap on stored derived content. A 500-page prospectus should not put a
#: multi-megabyte JSONB row behind every file listing; beyond this the parse
#: is marked `partial` and keeps what it read.
MAX_CONTENT_CHARS = 400_000


@dataclass
class Segment:
    """One located piece of a document.

    `locator` is the stable evidence address. It is what an extracted fact
    quotes against, so it must survive re-reads of the same artifact:

        PDF     "p1", "p2"            page numbers, 1-based
        DOCX    "para3", "table1"     ordinal within the document body
    """

    locator: str
    text: str
    #: Rows of cells, when this segment is a table. Kept structured because a
    #: transcript's marks are a grid, and flattening it to prose loses the
    #: course-to-grade association that makes it worth extracting.
    tables: list[list[list[str]]] = field(default_factory=list)
    kind: str = "text"          # text | table | heading
    ocr: bool = False           # this segment's text came from OCR

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"locator": self.locator, "text": self.text, "kind": self.kind}
        if self.tables:
            out["tables"] = self.tables
        if self.ocr:
            out["ocr"] = True
        return out


@dataclass
class ParsedDocument:
    """Parser output. Derived, rebuildable, never canonical."""

    document_type: str                  # pdf | docx
    parser: str                         # pypdf | python-docx
    segments: list[Segment]
    page_count: int = 0
    ocr_used: bool = False
    ocr_provider: Optional[str] = None
    #: True when some content could not be read (a failed page, OCR partly
    #: unavailable). The document is still usable; the caller records
    #: `partial` rather than pretending it is complete.
    partial: bool = False
    truncated: bool = False

    @property
    def normalized_text(self) -> str:
        return "\n\n".join(s.text for s in self.segments if s.text.strip())

    @property
    def char_count(self) -> int:
        return sum(len(s.text) for s in self.segments)

    def to_content(self) -> dict[str, Any]:
        """The JSONB payload stored on `DocumentArtifact.content`."""
        return {
            "document_type": self.document_type,
            "parser": self.parser,
            "segments": [s.to_dict() for s in self.segments],
            "page_count": self.page_count,
            "ocr_used": self.ocr_used,
            "truncated": self.truncated,
        }


def segments_from_content(content: Optional[dict]) -> list[Segment]:
    """Rebuild segments from a stored artifact. Tolerant of older shapes."""
    if not isinstance(content, dict):
        return []
    out: list[Segment] = []
    for raw in content.get("segments") or []:
        if not isinstance(raw, dict):
            continue
        out.append(Segment(
            locator=str(raw.get("locator") or ""),
            text=str(raw.get("text") or ""),
            tables=raw.get("tables") or [],
            kind=str(raw.get("kind") or "text"),
            ocr=bool(raw.get("ocr")),
        ))
    return out


def render_tables(tables: list[list[list[str]]]) -> str:
    """Flatten tables to pipe-delimited rows for prompt/tool rendering.

    Keeps the grid legible to a model without inventing Markdown alignment
    that a parser would then have to strip back out.
    """
    lines: list[str] = []
    for table in tables:
        for row in table:
            cells = [" ".join(str(cell or "").split()) for cell in row]
            if any(cells):
                lines.append(" | ".join(cells))
        lines.append("")
    return "\n".join(lines).strip()


def truncate_segments(
    segments: list[Segment], max_chars: int = MAX_CONTENT_CHARS,
) -> tuple[list[Segment], bool]:
    """Keep whole segments up to a character budget.

    Whole segments rather than a hard character cut: a locator must address
    the text it actually holds, and half a page under the name "p7" makes the
    evidence trail lie.
    """
    kept: list[Segment] = []
    used = 0
    for segment in segments:
        length = len(segment.text)
        if used + length > max_chars and kept:
            return kept, True
        kept.append(segment)
        used += length
    return kept, False
