# -*- coding: utf-8 -*-
"""Small, generated PDF/DOCX fixtures.

Built byte-by-byte rather than committed as binaries: no real student document
ever enters the repository, the content is visible in review, and each test can
state exactly what its document says.
"""

from __future__ import annotations

import io
import zipfile

DOCX_CONTENT_TYPE = (
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
)

_W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


def make_pdf(pages: list[str]) -> bytes:
    """A minimal but genuinely valid multi-page PDF with extractable text.

    Uses uncompressed content streams and the standard Helvetica font so real
    parsers read the text back without any font embedding.
    """
    objects: list[bytes] = []

    def add(body: bytes) -> int:
        objects.append(body)
        return len(objects)

    font_id = add(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")

    page_ids: list[int] = []
    content_ids: list[int] = []
    for text in pages:
        lines = text.split("\n")
        parts = [b"BT", b"/F1 12 Tf", b"72 720 Td", b"14 TL"]
        for line in lines:
            escaped = (
                line.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")
            ).encode("latin-1", "replace")
            parts.append(b"(" + escaped + b") Tj")
            parts.append(b"T*")
        parts.append(b"ET")
        stream = b"\n".join(parts)
        content_ids.append(
            add(b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n"
                + stream + b"\nendstream")
        )

    pages_id = len(objects) + len(pages) + 1
    for content_id in content_ids:
        page_ids.append(add(
            b"<< /Type /Page /Parent " + str(pages_id).encode()
            + b" 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 "
            + str(font_id).encode() + b" 0 R >> >> /Contents "
            + str(content_id).encode() + b" 0 R >>"
        ))

    kids = b" ".join(str(pid).encode() + b" 0 R" for pid in page_ids)
    actual_pages_id = add(
        b"<< /Type /Pages /Kids [" + kids + b"] /Count "
        + str(len(page_ids)).encode() + b" >>"
    )
    catalog_id = add(
        b"<< /Type /Catalog /Pages " + str(actual_pages_id).encode() + b" 0 R >>"
    )

    out = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for index, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += str(index).encode() + b" 0 obj\n" + body + b"\nendobj\n"

    xref_at = len(out)
    out += b"xref\n0 " + str(len(objects) + 1).encode() + b"\n"
    out += b"0000000000 65535 f \n"
    for offset in offsets[1:]:
        out += f"{offset:010d} 00000 n \n".encode()
    out += (
        b"trailer\n<< /Size " + str(len(objects) + 1).encode()
        + b" /Root " + str(catalog_id).encode() + b" 0 R >>\nstartxref\n"
        + str(xref_at).encode() + b"\n%%EOF\n"
    )
    return bytes(out)


def make_image_only_pdf(page_count: int = 1) -> bytes:
    """A PDF whose pages carry no text — the scanned-document shape.

    Real scans hold a raster image; what matters for the OCR fallback is that
    deterministic extraction yields (almost) nothing, which this reproduces
    without embedding an actual image.
    """
    objects: list[bytes] = []

    def add(body: bytes) -> int:
        objects.append(body)
        return len(objects)

    content_ids = []
    for _ in range(page_count):
        stream = b"0.5 0.5 0.5 rg\n72 600 400 120 re\nf"
        content_ids.append(add(
            b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n"
            + stream + b"\nendstream"
        ))

    pages_id = len(objects) + page_count + 1
    page_ids = []
    for content_id in content_ids:
        page_ids.append(add(
            b"<< /Type /Page /Parent " + str(pages_id).encode()
            + b" 0 R /MediaBox [0 0 612 792] /Resources << >> /Contents "
            + str(content_id).encode() + b" 0 R >>"
        ))

    kids = b" ".join(str(pid).encode() + b" 0 R" for pid in page_ids)
    actual_pages_id = add(
        b"<< /Type /Pages /Kids [" + kids + b"] /Count "
        + str(len(page_ids)).encode() + b" >>"
    )
    catalog_id = add(
        b"<< /Type /Catalog /Pages " + str(actual_pages_id).encode() + b" 0 R >>"
    )

    out = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for index, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += str(index).encode() + b" 0 obj\n" + body + b"\nendobj\n"
    xref_at = len(out)
    out += b"xref\n0 " + str(len(objects) + 1).encode() + b"\n"
    out += b"0000000000 65535 f \n"
    for offset in offsets[1:]:
        out += f"{offset:010d} 00000 n \n".encode()
    out += (
        b"trailer\n<< /Size " + str(len(objects) + 1).encode()
        + b" /Root " + str(catalog_id).encode() + b" 0 R >>\nstartxref\n"
        + str(xref_at).encode() + b"\n%%EOF\n"
    )
    return bytes(out)


def _p(text: str) -> str:
    safe = (
        text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    )
    return f"<w:p><w:r><w:t xml:space=\"preserve\">{safe}</w:t></w:r></w:p>"


def _table(rows: list[list[str]]) -> str:
    cells = []
    for row in rows:
        tcs = "".join(
            f"<w:tc><w:tcPr/><w:p><w:r><w:t xml:space=\"preserve\">"
            f"{c.replace('&', '&amp;').replace('<', '&lt;')}</w:t></w:r></w:p></w:tc>"
            for c in row
        )
        cells.append(f"<w:tr>{tcs}</w:tr>")
    return f"<w:tbl>{''.join(cells)}</w:tbl>"


def make_docx(paragraphs: list[str], tables: list[list[list[str]]] | None = None) -> bytes:
    """A real DOCX: a zip container with WordprocessingML parts."""
    body = "".join(_p(text) for text in paragraphs)
    for rows in tables or []:
        body += _table(rows)

    document_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<w:document xmlns:w="{_W_NS}"><w:body>{body}'
        "<w:sectPr/></w:body></w:document>"
    )
    content_types = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
        "</Types>"
    )
    rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>'
        "</Relationships>"
    )

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", content_types)
        archive.writestr("_rels/.rels", rels)
        archive.writestr("word/document.xml", document_xml)
    return buffer.getvalue()


def make_legacy_doc() -> bytes:
    """An OLE2 compound-file header — what a real legacy .doc starts with."""
    return b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 512


def make_fake_pdf() -> bytes:
    """A binary that is NOT a PDF, for tests that rename it to .pdf."""
    return b"\x7fELF\x02\x01\x01\x00" + b"\x00" * 256


def make_fake_docx() -> bytes:
    """A zip that is not WordprocessingML — e.g. a renamed archive."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("notes.txt", "not a word document")
    return buffer.getvalue()


TRANSCRIPT_PAGES = [
    "UNIVERSITY OF EXAMPLE\n"
    "OFFICIAL ACADEMIC TRANSCRIPT\n"
    "Student: Test Student\n"
    "Programme: BS Computer Science\n"
    "Period: 2022 - 2026\n"
    "CGPA: 3.41 / 4.00",
    "Semester 5 Results\n"
    "Machine Learning  A-\n"
    "Databases  B+\n"
    "Operating Systems  A",
]
