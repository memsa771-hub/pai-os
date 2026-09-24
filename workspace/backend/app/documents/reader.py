# -*- coding: utf-8 -*-
"""Agent-facing document reading.

`files.read` must return the PARSED content of a PDF/DOCX, never its bytes.
Handing a model `response.text` of a PDF gives it mojibake that it will
cheerfully "interpret", inventing content that was never in the document.

Every non-ready state is reported explicitly — `processing`, `failed`,
`unsupported` — because "I could not read it yet" and "here is nothing" must
be distinguishable to an agent deciding whether to answer or wait.

Bounded by construction: `max_chars`, and page/section ranges, so a 200-page
document cannot flood a model's context.
"""

from __future__ import annotations

import logging
from typing import Optional

from .content import segments_from_content
from .service import DocumentArtifactService

logger = logging.getLogger(__name__)

#: Default ceiling for one read. Generous enough for a transcript, far short
#: of a prospectus.
DEFAULT_MAX_CHARS = 20_000
HARD_MAX_CHARS = 100_000


def _parse_range(spec: Optional[str]) -> Optional[tuple[int, int]]:
    """Parse "3" or "2-5" into an inclusive 1-based range."""
    if not spec:
        return None
    text = str(spec).strip()
    if not text:
        return None
    if "-" in text:
        start_text, _, end_text = text.partition("-")
    else:
        start_text = end_text = text
    try:
        start = int(start_text)
        end = int(end_text)
    except ValueError:
        return None
    if start < 1 or end < start:
        return None
    return start, end


def read_document(
    db, workspace_id: str, file_id: str, *,
    max_chars: int = DEFAULT_MAX_CHARS,
    pages: Optional[str] = None,
) -> dict:
    """Return parsed document content, or an explicit not-ready state.

    The response always carries `status`, so a caller can branch without
    guessing from the presence of `content`.
    """
    service = DocumentArtifactService(db)
    artifact = service.get(workspace_id, file_id)

    if artifact is None:
        return {
            "ok": False,
            "status": "unsupported",
            "reason": "not_a_document",
            "message": (
                "This file is not a PDF or Word document, so it has no parsed "
                "text. Use the raw file download if you need its bytes."
            ),
        }

    if artifact.status == "unsupported":
        return {
            "ok": False,
            "status": "unsupported",
            "reason": artifact.error_code or "unsupported_type",
            "message": artifact.error_message or "This document type is not supported.",
        }

    if artifact.status in ("queued", "processing"):
        # The current-turn race: the student uploaded a file and immediately
        # asked about it. PAI must know the document exists and that it has
        # NOT been read, so it cannot invent findings.
        return {
            "ok": False,
            "status": artifact.status,
            "reason": "processing",
            "message": (
                "This document is still being processed. Its contents are not "
                "available yet — do not guess what it says."
            ),
        }

    if artifact.status == "failed":
        return {
            "ok": False,
            "status": "failed",
            "reason": artifact.error_code or "parse_failed",
            "message": artifact.error_message or "This document could not be read.",
        }

    segments = segments_from_content(artifact.content)
    if not segments:
        return {
            "ok": False,
            "status": artifact.status,
            "reason": "no_content",
            "message": "No readable text was found in this document.",
        }

    window = _parse_range(pages)
    if window is not None:
        start, end = window
        segments = segments[start - 1:end]

    budget = max(1, min(int(max_chars or DEFAULT_MAX_CHARS), HARD_MAX_CHARS))
    parts: list[str] = []
    used = 0
    truncated = False
    included: list[str] = []
    for segment in segments:
        text = segment.text.strip()
        if not text:
            continue
        chunk = f"[{segment.locator}]\n{text}"
        if used + len(chunk) > budget and parts:
            truncated = True
            break
        parts.append(chunk)
        included.append(segment.locator)
        used += len(chunk)

    return {
        "ok": True,
        "status": artifact.status,
        "document_type": artifact.document_type,
        "classification": artifact.classification,
        "authority": artifact.authority,
        "page_count": artifact.page_count,
        "ocr_used": bool(artifact.ocr_used),
        # `partial` is surfaced rather than smoothed over: a caller should
        # know some pages were unreadable before drawing conclusions.
        "partial": artifact.status == "partial",
        "truncated": truncated or bool((artifact.content or {}).get("truncated")),
        "locators": included,
        "content": "\n\n".join(parts),
    }


def document_status_payload(db, workspace_id: str, file_id: str) -> dict:
    """Compact status for attachment metadata and clients."""
    from .progress import document_stage

    artifact = DocumentArtifactService(db).get(workspace_id, file_id)
    if artifact is None:
        return {"processing_status": "unsupported", "document_stage": "unsupported"}
    return {
        "processing_status": artifact.status,
        # What a PERSON cares about: reading -> understanding -> done. The
        # storage status alone says "ready" a second after upload, while the
        # profile update is still running.
        "document_stage": document_stage(artifact),
        "document_type": artifact.document_type,
        "classification": artifact.classification,
        "page_count": artifact.page_count,
        "error_code": artifact.error_code,
    }
