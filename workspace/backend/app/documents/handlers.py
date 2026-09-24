# -*- coding: utf-8 -*-
"""Durable job handlers for the document pipeline.

    document.parse    bytes  -> normalized segments (+ OCR fallback)
    document.extract  segments -> classification + MemoryCandidates
    document.index    segments -> derived retrieval chunks
    document.unindex  purge    -> remove chunks

Each stage is its own job so they retry independently. The ordering rule that
matters: **indexing failure must never roll back canonical writes.** Indexing
is therefore enqueued after extraction commits, not called inline.

Registered on import, like `app/memory/handlers.py`; the worker imports both.
"""

from __future__ import annotations

import logging

from app.jobs.service import job_handlers
from app.storage import get_file_store
from .content import ParsedDocument, Segment, segments_from_content
from .parsers import (
    DocumentParseError, MAX_CONTENT_CHARS, parse_document, pages_needing_ocr,
    render_pdf_page_png, truncate_segments,
)
from .service import (
    JOB_DOCUMENT_EXTRACT, JOB_DOCUMENT_INDEX, JOB_DOCUMENT_PARSE,
    JOB_DOCUMENT_UNINDEX, PARSER_VERSION, DocumentArtifactService,
)

logger = logging.getLogger(__name__)


def _load_bytes(db, workspace_id: str, file_id: str) -> bytes | None:
    """Read the raw file from workspace storage."""
    from sqlalchemy import select

    from app.models import FileRecord

    record = db.execute(
        select(FileRecord).where(
            FileRecord.id == file_id,
            FileRecord.workspace_id == workspace_id,
        )
    ).scalar_one_or_none()
    if record is None:
        return None
    try:
        return get_file_store().read(record.storage_key)
    except Exception:  # noqa: BLE001 — a missing object is a real, safe outcome
        logger.warning("document bytes unreadable file=%s", file_id, exc_info=True)
        return None


async def _apply_ocr(data: bytes, parsed: ParsedDocument) -> ParsedDocument:
    """Fill in pages that deterministic extraction could not read.

    Returns the document unchanged when OCR is unavailable, marking it
    `partial` — a scanned upload should land and be reprocessable later, not
    fail outright.
    """
    from .ocr import MAX_OCR_PAGES, get_ocr_provider

    blank_pages = pages_needing_ocr(parsed)
    if not blank_pages:
        return parsed

    provider = get_ocr_provider()
    if provider is None:
        logger.info("document ocr unavailable, %d page(s) unread", len(blank_pages))
        parsed.partial = True
        return parsed

    targets = blank_pages[:MAX_OCR_PAGES]
    images: list[tuple[int, bytes]] = []
    for page_number in targets:
        try:
            images.append((page_number, render_pdf_page_png(data, page_number)))
        except Exception:  # noqa: BLE001 — one unrenderable page is not fatal
            logger.warning("document page render failed page=%d", page_number, exc_info=True)

    if not images:
        parsed.partial = True
        return parsed

    try:
        results = await provider.transcribe(images)
    except Exception:  # noqa: BLE001 — OCR outage must not lose the upload
        logger.warning("document ocr failed", exc_info=True)
        parsed.partial = True
        return parsed

    by_page = {page.page_number: page.text for page in results}
    filled = 0
    for index, segment in enumerate(parsed.segments, start=1):
        text = (by_page.get(index) or "").strip()
        if text and len(segment.text) < len(text):
            segment.text = text
            segment.ocr = True
            filled += 1

    parsed.ocr_used = filled > 0
    parsed.ocr_provider = provider.name if filled else None
    # Pages beyond the cap, or that OCR could not read, remain unread.
    parsed.partial = parsed.partial or len(blank_pages) > len(targets) or filled < len(targets)

    kept, truncated = truncate_segments(parsed.segments, MAX_CONTENT_CHARS)
    parsed.segments = kept
    parsed.truncated = parsed.truncated or truncated
    return parsed


async def parse_document_job(job, db) -> dict:
    """Parse one uploaded document into normalized, located segments."""
    workspace_id = job.workspace_id
    payload = job.payload or {}
    file_id = payload.get("file_id")
    if not workspace_id or not file_id:
        raise ValueError("document.parse requires workspace_id and file_id")

    service = DocumentArtifactService(db)
    artifact = service.get(workspace_id, file_id)
    if artifact is None:
        # The file (or its workspace) went away before the job ran. Nothing to
        # parse and nothing to retry.
        logger.info("document.parse: artifact missing workspace=%s file=%s",
                    workspace_id, file_id)
        return {"parsed": False, "reason": "artifact_missing"}

    if artifact.status == "unsupported":
        return {"parsed": False, "reason": "unsupported"}

    # Idempotency: a completed parse at the current parser version is not
    # redone by a retry or a duplicate enqueue.
    if artifact.status in ("ready", "partial") and artifact.parser_version == PARSER_VERSION:
        return {"parsed": False, "reason": "already_parsed"}

    data = _load_bytes(db, workspace_id, file_id)
    if data is None:
        service.mark_failed(
            artifact, "file_unavailable",
            "The uploaded file could not be read from storage.",
        )
        return {"parsed": False, "reason": "file_unavailable"}

    service.mark_processing(artifact)

    try:
        parsed = parse_document(data, artifact.document_type or "")
    except DocumentParseError as exc:
        # A statement about the document, not a transient fault: record it and
        # succeed, so the durable worker does not retry a permanently
        # unparseable file five times.
        logger.info("document.parse failed file=%s code=%s", file_id, exc.code)
        service.mark_failed(artifact, exc.code, exc.message)
        return {"parsed": False, "reason": exc.code}

    if parsed.document_type == "pdf":
        parsed = await _apply_ocr(data, parsed)

    service.mark_parsed(
        artifact,
        content=parsed.to_content(),
        parser=parsed.parser,
        page_count=parsed.page_count,
        char_count=parsed.char_count,
        ocr_used=parsed.ocr_used,
        ocr_provider=parsed.ocr_provider,
        partial=parsed.partial,
    )

    logger.info(
        "document.parse: workspace=%s file=%s type=%s parser=%s pages=%d chars=%d "
        "ocr=%s partial=%s truncated=%s",
        workspace_id, file_id, parsed.document_type, parsed.parser,
        parsed.page_count, parsed.char_count, parsed.ocr_used, parsed.partial,
        parsed.truncated,
    )

    # Extraction is its own durable job: parsing succeeded and must not be
    # redone because an LLM call failed.
    from app.jobs.service import BackgroundJobService

    BackgroundJobService(db).enqueue(
        job_type=JOB_DOCUMENT_EXTRACT,
        workspace_id=workspace_id,
        payload={"file_id": file_id},
        idempotency_key=(
            f"document.extract:{file_id}:{artifact.content_sha256}:{PARSER_VERSION}"
        ),
    )

    return {
        "parsed": True,
        "pages": parsed.page_count,
        "chars": parsed.char_count,
        "ocr_used": parsed.ocr_used,
        "partial": parsed.partial,
    }


async def index_document_job(job, db) -> dict:
    """Index a parsed document's chunks for later retrieval (Phase F)."""
    from .retrieval import index_document_chunks

    workspace_id = job.workspace_id
    file_id = (job.payload or {}).get("file_id")
    if not workspace_id or not file_id:
        raise ValueError("document.index requires workspace_id and file_id")

    artifact = DocumentArtifactService(db).get(workspace_id, file_id)
    if artifact is None or not artifact.content:
        return {"indexed": 0, "reason": "no_content"}

    indexed = await index_document_chunks(
        workspace_id=workspace_id,
        file_id=file_id,
        document_type=artifact.classification or artifact.document_type or "",
        segments=segments_from_content(artifact.content),
    )
    logger.info(
        "document.index: workspace=%s file=%s chunks=%d", workspace_id, file_id, indexed,
    )
    return {"indexed": indexed}


async def unindex_document_job(job, db) -> dict:
    """Remove purged documents from the derived retrieval index."""
    from .retrieval import delete_document_chunks

    workspace_id = job.workspace_id
    file_ids = (job.payload or {}).get("file_ids") or []
    if not workspace_id:
        raise ValueError("document.unindex requires a workspace_id")
    if not file_ids:
        return {"deleted": 0}

    deleted = await delete_document_chunks(workspace_id, file_ids)
    logger.info(
        "document.unindex: workspace=%s files=%d deleted=%d",
        workspace_id, len(file_ids), deleted,
    )
    return {"deleted": deleted}


job_handlers.register(JOB_DOCUMENT_PARSE, parse_document_job)
job_handlers.register(JOB_DOCUMENT_INDEX, index_document_job)
job_handlers.register(JOB_DOCUMENT_UNINDEX, unindex_document_job)
