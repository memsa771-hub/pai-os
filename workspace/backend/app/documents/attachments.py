# -*- coding: utf-8 -*-
"""One canonical shape for chat attachments.

Clients send camelCase (`fileId`, `contentType`); server-side producers write
snake_case (`file_id`, `content_type`). Both are in the wild and both must
keep working, so normalization happens once, here, rather than being guessed
at every read site.

What the Counselor is told is deliberately SMALL:

    which file, what type, and whether it has been processed

and never the document's text. Injecting a full transcript into conversation
history would blow past the context budget, bypass the untrusted-data framing
that extraction uses, and — during collection mode — hand over exactly the
personalized material the completion gate is withholding. Analysis goes
through `files.read`, under the existing tool policy.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)


def _first(source: dict, *names: str) -> Optional[Any]:
    for name in names:
        value = source.get(name)
        if value not in (None, ""):
            return value
    return None


def normalize_attachment(raw: Any) -> Optional[dict]:
    """Coerce one attachment payload into the canonical shape."""
    if not isinstance(raw, dict):
        return None
    file_id = _first(raw, "file_id", "fileId", "id")
    if not file_id:
        return None
    return {
        "file_id": str(file_id),
        "filename": str(_first(raw, "filename", "fileName", "name") or ""),
        "content_type": str(_first(raw, "content_type", "contentType", "type") or ""),
        "size": _first(raw, "size", "bytes"),
    }


def normalize_attachments(raw: Any) -> list[dict]:
    if not isinstance(raw, list):
        return []
    out = []
    for item in raw:
        normalized = normalize_attachment(item)
        if normalized is not None:
            out.append(normalized)
    return out


#: Upper bound on how long a chat turn waits for an attachment to parse.
#: Parsing is measured at <1s; the rest is the worker's poll interval. Past
#: this the turn proceeds and honestly reports "still reading".
READABLE_WAIT_SECONDS = 6.0
_READABLE_POLL_SECONDS = 0.5


async def wait_until_readable(
    db, workspace_id: str, file_ids: list[str],
    timeout: float = READABLE_WAIT_SECONDS,
) -> bool:
    """Wait briefly for attached documents to leave queued/processing.

    Bounded, and never an error: the upload request itself never waits, and
    neither does a turn whose file is slow — it just answers truthfully.
    Returns True when every attachment reached a settled state.
    """
    import asyncio
    import time

    from sqlalchemy import select

    from app.models import DocumentArtifact

    if not file_ids:
        return True
    deadline = time.monotonic() + max(0.0, timeout)
    while True:
        # Fresh read each poll: the worker commits in another process.
        db.rollback()
        try:
            pending = db.execute(
                select(DocumentArtifact.id).where(
                    DocumentArtifact.workspace_id == workspace_id,
                    DocumentArtifact.file_id.in_(file_ids),
                    DocumentArtifact.status.in_(("queued", "processing")),
                )
            ).first()
        except Exception:  # noqa: BLE001 — a wait must never break the turn
            logger.exception("attachment readiness check failed workspace=%s", workspace_id)
            db.rollback()
            return False
        if pending is None:
            return True
        if time.monotonic() >= deadline:
            return False
        # Release the connection while sleeping (see the note on
        # idle-in-transaction in the Counselor loop).
        db.rollback()
        await asyncio.sleep(_READABLE_POLL_SECONDS)


def describe_attachments(db, workspace_id: str, attachments: list[dict]) -> list[dict]:
    """Add server-owned processing state to normalized attachments.

    `processing_status` comes from the server, never from the client: a model
    must not be able to be told "ready" by a payload.
    """
    from .reader import document_status_payload

    described = []
    for attachment in attachments:
        described.append({
            **attachment,
            **document_status_payload(db, workspace_id, attachment["file_id"]),
        })
    return described


def attachment_prompt_block(attachments: list[dict], can_read: bool = True) -> str:
    """Tell the Counselor an attachment exists — not what it says.

    The processing state is spelled out so PAI can answer "still reading it"
    truthfully instead of pretending to have read a document that is still
    queued.
    """
    if not attachments:
        return ""

    lines = []
    for attachment in attachments:
        status = attachment.get("processing_status") or "unknown"
        bits = [
            f'- {attachment.get("filename") or "file"}',
            f'(file_id: {attachment["file_id"]}',
        ]
        if attachment.get("document_type"):
            bits.append(f'type: {attachment["document_type"]}')
        bits.append(f"status: {status})")
        lines.append(" ".join(bits))

    guidance = {
        "ready": (
            "Use files.read with the file_id to read a document's contents "
            "when the student asks about it and policy permits."
        ),
        "partial": (
            "Some pages of an attached document could not be read. You may "
            "read what is available with files.read, and should say that it "
            "is incomplete."
        ),
        "queued": (
            "Processing is NOT finished. You do not know what these documents "
            "say. Say that you are still reading them; never guess at their "
            "contents."
        ),
        "processing": (
            "Processing is NOT finished. You do not know what these documents "
            "say. Say that you are still reading them; never guess at their "
            "contents."
        ),
        "failed": (
            "An attached document could not be read. Tell the student and ask "
            "them to re-upload it."
        ),
        "unsupported": (
            "An attached file is not a supported document. Only PDF and Word "
            "(.docx) files can be read."
        ),
    }
    if not can_read:
        # Collection mode withholds files.read; do not point at a tool the
        # Counselor cannot call. The document is still processed in the
        # background and may complete the profile.
        guidance["ready"] = guidance["partial"] = (
            "The document is being added to the student's profile in the "
            "background. Do not discuss its contents in this mode."
        )
    statuses = {a.get("processing_status") for a in attachments}
    notes = [guidance[s] for s in ("queued", "processing", "failed", "unsupported", "partial", "ready")
             if s in statuses and s in guidance]

    return (
        "## Attached files\n\n"
        "The student attached the following to this message:\n"
        + "\n".join(lines)
        + "\n\n" + "\n".join(dict.fromkeys(notes))
    )
