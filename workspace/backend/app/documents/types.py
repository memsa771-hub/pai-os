# -*- coding: utf-8 -*-
"""What a student document actually IS, decided from bytes.

Phase 1 accepts exactly two formats: PDF and DOCX. The restriction applies to
the student-document intelligence path only — generic workspace file storage
keeps accepting whatever it accepted before (see `app/file_types.py`).

**Extension is a hint, content is the decision.** A renamed binary called
`transcript.pdf` must not reach a parser, an OCR provider or an extraction
prompt, so every check here reads the leading bytes:

    PDF    %PDF-                     (allowed a small leading offset)
    DOCX   PK zip container holding `word/` parts

Legacy `.doc` is the trap worth naming: it is an OLE2 compound file
(``D0CF11E0``), shares a stem with `.docx`, and is explicitly rejected rather
than handed to a DOCX parser that would fail deep inside a zip reader.
"""

from __future__ import annotations

import io
import zipfile
from typing import Optional

#: Extensions the student-document path accepts. Nothing else is processed.
DOCUMENT_EXTENSIONS = ("pdf", "docx")

#: Canonical content types, keyed by the detected document type.
DOCUMENT_CONTENT_TYPES = {
    "pdf": "application/pdf",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}

_PDF_MAGIC = b"%PDF-"
#: Some producers emit junk before the header; the spec tolerates a small
#: offset and real-world files do use it. Anything further in is not a PDF.
_PDF_MAGIC_SEARCH_WINDOW = 1024

_ZIP_MAGIC = (b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08")
#: OLE2 compound file — legacy .doc/.xls/.ppt. Rejected explicitly.
_OLE2_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"

#: A DOCX zip must carry the WordprocessingML main part. An XLSX or a plain
#: zip renamed to .docx is a zip too, so the container alone proves nothing.
_DOCX_REQUIRED_ENTRY = "word/document.xml"


class DocumentTypeError(ValueError):
    """The bytes are not a supported student document."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        #: Stable machine-readable reason, safe to return to a client.
        self.code = code
        self.message = message


def _extension(filename: str) -> str:
    name = (filename or "").rsplit("/", 1)[-1]
    _, dot, ext = name.rpartition(".")
    return ext.lower() if dot else ""


def _looks_like_pdf(data: bytes) -> bool:
    return data[:_PDF_MAGIC_SEARCH_WINDOW].find(_PDF_MAGIC) != -1


def _looks_like_zip(data: bytes) -> bool:
    return data.startswith(_ZIP_MAGIC)


def _is_docx_container(data: bytes) -> bool:
    """True only for a readable zip that holds WordprocessingML.

    Reads the central directory rather than trusting the PK header: that is
    what separates a real DOCX from an XLSX, an ODT or an arbitrary archive
    someone renamed.
    """
    if not _looks_like_zip(data):
        return False
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            names = set(archive.namelist())
    except (zipfile.BadZipFile, OSError, ValueError):
        return False
    return _DOCX_REQUIRED_ENTRY in names


def sniff_content_type(data: bytes) -> Optional[str]:
    """Detected canonical content type, or None when unrecognised."""
    if _looks_like_pdf(data):
        return DOCUMENT_CONTENT_TYPES["pdf"]
    if _is_docx_container(data):
        return DOCUMENT_CONTENT_TYPES["docx"]
    return None


def is_student_document_candidate(filename: str, content_type: str = "") -> bool:
    """Cheap pre-check: could this file be a student document at all?

    Name/type only — deliberately does NOT read bytes, because callers use it
    to decide whether byte-level validation is even worth doing. A true result
    means "worth validating", never "valid".
    """
    if _extension(filename) in DOCUMENT_EXTENSIONS:
        return True
    return (content_type or "").split(";")[0].strip().lower() in set(
        DOCUMENT_CONTENT_TYPES.values()
    )


def detect_document_type(
    data: bytes, filename: str = "", declared_content_type: str = "",
) -> tuple[str, str]:
    """Decide the document type from CONTENT, cross-checked against the name.

    Returns ``(document_type, content_type)`` where `document_type` is "pdf"
    or "docx".

    Raises `DocumentTypeError` with a stable `code`:

        unsupported_type   not a PDF or DOCX at all (includes legacy .doc)
        content_mismatch   real document, but not the type the name claims

    The browser's declared MIME is accepted as a hint and never as proof: it
    is attacker-controlled on an API call and frequently wrong even from real
    browsers (`application/octet-stream` for a genuine PDF).
    """
    if not data:
        raise DocumentTypeError("unsupported_type", "The file is empty.")

    extension = _extension(filename)

    if extension == "doc" or data.startswith(_OLE2_MAGIC):
        raise DocumentTypeError(
            "unsupported_type",
            "Legacy .doc files are not supported. Save the document as PDF or "
            ".docx and upload it again.",
        )

    detected: Optional[str] = None
    if _looks_like_pdf(data):
        detected = "pdf"
    elif _is_docx_container(data):
        detected = "docx"

    if detected is None:
        raise DocumentTypeError(
            "unsupported_type",
            "Only PDF and Word (.docx) documents can be processed.",
        )

    # A name that claims the other supported type is a mismatch worth
    # reporting; a name with no/unknown extension simply defers to content.
    if extension in DOCUMENT_EXTENSIONS and extension != detected:
        raise DocumentTypeError(
            "content_mismatch",
            f"This file is named .{extension} but its contents are a "
            f"{detected.upper()} document.",
        )

    return detected, DOCUMENT_CONTENT_TYPES[detected]
