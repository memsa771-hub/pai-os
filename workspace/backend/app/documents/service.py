# -*- coding: utf-8 -*-
"""DocumentArtifactService — derived processing state, and the job that fills it.

The upload request calls `register()` and returns. Everything expensive
(parsing, OCR, extraction, indexing) happens in durable `BackgroundJob`s so a
restart cannot lose a document and a slow OCR cannot slow an upload.

Idempotency is anchored on `(file_id, content_sha256, parser_version)`:

    same file re-queued      -> same artifact row, no duplicate extraction
    identical bytes re-up'd  -> a new FileRecord, but the hash matches, so
                                nothing is re-derived unless asked
    parser upgraded          -> version differs, reprocessing is deliberate

Failures are recorded on the artifact with a SAFE code/message. Raw parser or
model errors can carry document text, so they are logged, never stored.
"""

from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import select

from app.models import DocumentArtifact, FileRecord
from .types import DocumentTypeError, detect_document_type

logger = logging.getLogger(__name__)

#: Bumping this invalidates derived content and allows deliberate reprocessing.
PARSER_VERSION = "1"

JOB_DOCUMENT_PARSE = "document.parse"
JOB_DOCUMENT_EXTRACT = "document.extract"
JOB_DOCUMENT_INDEX = "document.index"
JOB_DOCUMENT_UNINDEX = "document.unindex"
JOB_DOCUMENT_NOTIFY = "document.notify"

STATUSES = ("queued", "processing", "ready", "partial", "failed", "unsupported")

#: Statuses a reader may treat as "there is content to read".
READABLE_STATUSES = ("ready", "partial")


def content_sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _now() -> datetime:
    return datetime.now(timezone.utc)


class DocumentArtifactService:
    """Own the derived row. Never writes canonical student state."""

    def __init__(self, db):
        self.db = db

    # -- reads ------------------------------------------------------------

    def get(self, workspace_id: str, file_id: str) -> Optional[DocumentArtifact]:
        return self.db.execute(
            select(DocumentArtifact).where(
                DocumentArtifact.workspace_id == workspace_id,
                DocumentArtifact.file_id == file_id,
            )
        ).scalar_one_or_none()

    def status_for(self, workspace_id: str, file_id: str) -> str:
        """Processing status for a file, for clients and agent tools.

        A file with no artifact row was never a student document (a PNG, a
        code file) — reported as `unsupported` rather than inventing a queued
        state for something that will never be processed.
        """
        artifact = self.get(workspace_id, file_id)
        return artifact.status if artifact is not None else "unsupported"

    # -- writes (derived only) --------------------------------------------

    def register(
        self, workspace_id: str, file_id: str, data: bytes, filename: str,
        declared_content_type: str = "",
    ) -> Optional[DocumentArtifact]:
        """Create/refresh the derived row for an uploaded file.

        Returns None when the file is not a student-document candidate at all
        — a PNG upload is not an error, it simply has no document pipeline.

        An UNSUPPORTED document (a renamed binary named `.pdf`) DOES get a
        row: the student needs to see why their upload will not be read, and
        a silent absence is indistinguishable from "still queued".
        """
        artifact = self.get(workspace_id, file_id)
        digest = content_sha256(data)

        try:
            document_type, detected_type = detect_document_type(
                data, filename, declared_content_type,
            )
        except DocumentTypeError as exc:
            logger.info(
                "document rejected workspace=%s file=%s code=%s",
                workspace_id, file_id, exc.code,
            )
            return self._record_unsupported(
                artifact, workspace_id, file_id, digest, exc.code, exc.message,
            )

        if artifact is None:
            artifact = DocumentArtifact(workspace_id=workspace_id, file_id=file_id)
            self.db.add(artifact)

        artifact.status = "queued"
        artifact.document_type = document_type
        artifact.detected_content_type = detected_type
        artifact.content_sha256 = digest
        artifact.parser_version = PARSER_VERSION
        artifact.error_code = None
        artifact.error_message = None
        artifact.updated_at = _now()
        self.db.flush()
        return artifact

    def _record_unsupported(
        self, artifact, workspace_id: str, file_id: str, digest: str,
        code: str, message: str,
    ) -> DocumentArtifact:
        if artifact is None:
            artifact = DocumentArtifact(workspace_id=workspace_id, file_id=file_id)
            self.db.add(artifact)
        artifact.status = "unsupported"
        artifact.content_sha256 = digest
        artifact.error_code = code
        artifact.error_message = message[:500]
        artifact.processed_at = _now()
        artifact.updated_at = _now()
        self.db.flush()
        return artifact

    def mark_processing(self, artifact: DocumentArtifact) -> DocumentArtifact:
        artifact.status = "processing"
        artifact.updated_at = _now()
        self.db.flush()
        return artifact

    def mark_parsed(
        self, artifact: DocumentArtifact, *, content: dict, parser: str,
        page_count: int, char_count: int, ocr_used: bool = False,
        ocr_provider: Optional[str] = None, partial: bool = False,
    ) -> DocumentArtifact:
        artifact.status = "partial" if partial else "ready"
        artifact.content = content
        artifact.parser = parser
        artifact.parser_version = PARSER_VERSION
        artifact.page_count = page_count
        artifact.char_count = char_count
        artifact.ocr_used = ocr_used
        artifact.ocr_provider = ocr_provider
        artifact.error_code = None
        artifact.error_message = None
        artifact.processed_at = _now()
        artifact.updated_at = _now()
        self.db.flush()
        return artifact

    def mark_failed(
        self, artifact: DocumentArtifact, code: str, message: str,
    ) -> DocumentArtifact:
        """Record a SAFE failure reason.

        `message` must already be a summary written by our code. Parser and
        model exceptions can quote document text, so callers pass a fixed
        string and log the exception separately.
        """
        artifact.status = "failed"
        artifact.error_code = code
        artifact.error_message = (message or "")[:500]
        artifact.processed_at = _now()
        artifact.updated_at = _now()
        self.db.flush()
        return artifact

    def record_classification(
        self, artifact: DocumentArtifact, *, classification: str,
        confidence: Optional[float], authority: str, extractor_version: str,
        summary: Optional[dict] = None,
    ) -> DocumentArtifact:
        artifact.classification = classification
        artifact.classification_confidence = confidence
        artifact.authority = authority
        artifact.extractor_version = extractor_version
        artifact.extraction_summary = summary
        artifact.updated_at = _now()
        self.db.flush()
        return artifact

    # -- enqueue ----------------------------------------------------------

    def enqueue_parse(self, artifact: DocumentArtifact) -> None:
        """Queue parsing for a registered artifact.

        The idempotency key carries the content hash and parser version, so a
        duplicate upload of identical bytes is a no-op while a parser upgrade
        legitimately re-queues the same file.
        """
        from app.jobs.service import BackgroundJobService

        BackgroundJobService(self.db).enqueue(
            job_type=JOB_DOCUMENT_PARSE,
            workspace_id=artifact.workspace_id,
            payload={"file_id": artifact.file_id, "artifact_id": artifact.id},
            idempotency_key=(
                f"document.parse:{artifact.file_id}:"
                f"{artifact.content_sha256}:{PARSER_VERSION}"
            ),
        )

    def register_and_enqueue(
        self, workspace_id: str, file_id: str, data: bytes, filename: str,
        declared_content_type: str = "",
    ) -> Optional[DocumentArtifact]:
        """The upload path's single entry point. Cheap and non-blocking.

        Validates, records derived state, and queues the work. It does NOT
        parse: an upload request must return before OCR or extraction runs.
        """
        artifact = self.register(
            workspace_id, file_id, data, filename, declared_content_type,
        )
        if artifact is not None and artifact.status == "queued":
            self.enqueue_parse(artifact)
            logger.info(
                "document queued workspace=%s file=%s type=%s bytes=%d",
                workspace_id, file_id, artifact.document_type, len(data),
            )
        return artifact

    # -- lifecycle --------------------------------------------------------

    def purge_for_files(self, workspace_id: str, file_ids: list[str]) -> int:
        """Drop derived state for permanently purged files.

        Canonical facts learned from those documents are deliberately NOT
        touched: evidence availability and student truth are different
        concerns, and a purge is not a retraction. See `docs` in the artifact
        model.
        """
        if not file_ids:
            return 0
        rows = list(self.db.execute(
            select(DocumentArtifact).where(
                DocumentArtifact.workspace_id == workspace_id,
                DocumentArtifact.file_id.in_(file_ids),
            )
        ).scalars().all())
        for row in rows:
            self.db.delete(row)
        if rows:
            self.db.flush()
        return len(rows)
