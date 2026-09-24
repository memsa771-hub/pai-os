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


async def extract_document_job(job, db) -> dict:
    """Understand a parsed document and propose candidates.

    This function may ONLY write `pai_memory_candidates`. It has no access to
    VaultService or StudentRecordService, so even a compromised extraction
    prompt cannot change canonical state — the reconciler decides, exactly as
    it does for conversation.
    """
    from app.memory.candidates import MemoryCandidateService
    from app.memory.field_definitions import (
        ENTITY_BACKED_LEGACY_FIELDS, VaultFieldDefinitionService,
    )
    from app.memory.student_snapshot import StudentSnapshotService
    from .classify import authority_for, is_journey_document
    from .extractor import EXTRACTOR_VERSION, DocumentExtractionError, understand_document

    workspace_id = job.workspace_id
    file_id = (job.payload or {}).get("file_id")
    if not workspace_id or not file_id:
        raise ValueError("document.extract requires workspace_id and file_id")

    service = DocumentArtifactService(db)
    artifact = service.get(workspace_id, file_id)
    if artifact is None:
        return {"extracted": False, "reason": "artifact_missing"}
    if artifact.status not in ("ready", "partial"):
        return {"extracted": False, "reason": f"not_readable:{artifact.status}"}
    if artifact.extractor_version == EXTRACTOR_VERSION and artifact.classification:
        # Already understood at this version — a retry must not propose the
        # same candidates twice.
        return {"extracted": False, "reason": "already_extracted"}

    segments = segments_from_content(artifact.content)
    if not segments:
        return {"extracted": False, "reason": "no_content"}

    # Existing state comes from the shared snapshot — never a second view of
    # "what we know", which would drift from the canonical one.
    snapshot = StudentSnapshotService(db).build(workspace_id)
    definitions = [
        d for d in VaultFieldDefinitionService(db).list_definitions()
        if d.key not in ENTITY_BACKED_LEGACY_FIELDS
    ]
    allowed_keys = {d.key for d in definitions}
    field_specs = [
        {"key": d.key, "data_type": d.data_type, "description": d.description}
        for d in definitions
    ]

    from sqlalchemy import select

    from app.models import FileRecord

    record = db.execute(
        select(FileRecord).where(FileRecord.id == file_id)
    ).scalar_one_or_none()
    filename = (record.filename if record else "").rsplit("/", 1)[-1]
    uploaded_by = record.uploaded_by if record else ""

    try:
        understanding = await understand_document(
            segments, filename=filename,
            document_type=artifact.document_type or "",
            allowed_vault_keys=allowed_keys, field_specs=field_specs,
            existing_records=snapshot.records,
        )
    except DocumentExtractionError:
        # Malformed output fails the job so the durable worker retries, rather
        # than writing half-trusted rows.
        logger.warning("document.extract: bad model output file=%s", file_id)
        raise

    authority = authority_for(understanding.classification, uploaded_by)
    service.record_classification(
        artifact,
        classification=understanding.classification,
        confidence=understanding.classification_confidence,
        authority=authority,
        extractor_version=EXTRACTOR_VERSION,
        summary={"findings": len(understanding.findings)},
    )

    candidates = MemoryCandidateService(db)
    proposed: list[str] = []

    # A StudentDocument record: "this file is a meaningful journey document".
    # The file_id is SERVER-owned — taken from the job, never from model
    # output — so a model cannot name a file it was not given.
    if is_journey_document(understanding.classification) and record is not None:
        existing_documents = {
            row.get("file_id") for row in snapshot.records.get("document", [])
        }
        if file_id not in existing_documents:
            candidate = candidates.propose(
                workspace_id=workspace_id,
                candidate_type="student_record",
                key="document",
                proposed_value={
                    "file_id": file_id,
                    "document_type": understanding.classification,
                    **({"title": understanding.title} if understanding.title else {}),
                },
                confidence=max(0.5, understanding.classification_confidence),
                source_type="document",
                evidence={
                    "file_id": file_id, "filename": filename,
                    "authority": authority,
                    "extractor_version": EXTRACTOR_VERSION,
                },
            )
            proposed.append(candidate.id)

    for finding in understanding.findings:
        candidate = candidates.propose(
            workspace_id=workspace_id,
            candidate_type=finding.candidate_type,
            operation="upsert",
            key=finding.key,
            proposed_value=finding.proposed_value,
            content=finding.content,
            entities=finding.entities or None,
            confidence=finding.confidence,
            # Server-assigned, from the channel this job read. The extractor
            # has no field for it and cannot reach `user_explicit`.
            source_type="document",
            evidence={
                **finding.evidence,
                "file_id": file_id,
                "filename": filename,
                "document_type": understanding.classification,
                "authority": authority,
                "parser": artifact.parser,
                "parser_version": artifact.parser_version,
                "extractor_version": EXTRACTOR_VERSION,
                **({"ocr": True} if artifact.ocr_used else {}),
            },
        )
        proposed.append(candidate.id)

    logger.info(
        "document.extract: workspace=%s file=%s class=%s authority=%s "
        "findings=%d proposed=%d types=%s",
        workspace_id, file_id, understanding.classification, authority,
        len(understanding.findings), len(proposed),
        sorted({f.candidate_type for f in understanding.findings}) or None,
    )

    from app.jobs.service import BackgroundJobService

    jobs = BackgroundJobService(db)
    if proposed:
        # Reconciliation is the EXISTING durable job — documents converge on
        # the same path as conversation rather than getting their own.
        from app.memory.handlers import JOB_RECONCILE

        jobs.enqueue(
            job_type=JOB_RECONCILE,
            workspace_id=workspace_id,
            payload={"candidate_ids": proposed},
            idempotency_key=f"reconcile:document:{file_id}:{EXTRACTOR_VERSION}",
        )

    # Indexing is enqueued separately and AFTER extraction, so an index
    # failure can never roll back canonical writes.
    jobs.enqueue(
        job_type=JOB_DOCUMENT_INDEX,
        workspace_id=workspace_id,
        payload={"file_id": file_id},
        idempotency_key=f"document.index:{file_id}:{artifact.content_sha256}",
    )

    return {
        "extracted": True,
        "classification": understanding.classification,
        "authority": authority,
        "candidates_proposed": len(proposed),
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
job_handlers.register(JOB_DOCUMENT_EXTRACT, extract_document_job)
job_handlers.register(JOB_DOCUMENT_INDEX, index_document_job)
job_handlers.register(JOB_DOCUMENT_UNINDEX, unindex_document_job)
