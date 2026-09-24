# -*- coding: utf-8 -*-
"""Where a document is up to, and what was learned from it — for people.

Two things read this, and both exist because the student was left guessing:

1. The Counselor's per-turn context. Document status used to be visible ONLY
   on the turn a file was attached. On any later turn the Counselor had no
   status and no file_id (chat history keeps message text, not attachment
   metadata), so asked "what did you extract from my CV?" it repeated its own
   earlier "still reading" — false, minutes after the CV was done.

2. The completion message posted into the chat when processing finishes, so
   a student who navigated away comes back to an answer instead of a thread
   that looks stalled.

Everything here is server-owned state and counts. No document text, ever:
that stays behind `files.read` and its policy gates.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import select

from app.models import DocumentArtifact, FileRecord, MemoryCandidate

# Stages a person can act on, collapsed from the storage status.
STAGE_READING = "reading"              # queued/processing: not readable yet
STAGE_UNDERSTANDING = "understanding"  # readable; profile update in progress
STAGE_DONE = "done"
STAGE_FAILED = "failed"
STAGE_UNSUPPORTED = "unsupported"

#: Human labels for what a document taught us. Keys are record kinds and
#: candidate types; Vault facts group under one label on purpose — a student
#: does not think in `location.current_city`.
_LABELS = {
    "education": "education", "test_attempt": "test scores",
    "work_experience": "work experience", "project": "projects",
    "goal": "goals", "skill": "skills", "certification": "certifications",
    "application": "applications", "language_proficiency": "languages",
    "research": "research", "achievement": "awards",
    "financial_sponsor": "funding", "scholarship_application": "scholarships",
    "visa": "visas",
    "vault_fact": "profile details", "semantic_memory": "notes",
    "episode": "milestones",
}


def document_stage(artifact: Optional[DocumentArtifact]) -> str:
    from .extractor import EXTRACTOR_VERSION

    if artifact is None:
        return STAGE_UNSUPPORTED
    if artifact.status == "unsupported":
        return STAGE_UNSUPPORTED
    if artifact.status == "failed":
        return STAGE_FAILED
    if artifact.status in ("queued", "processing"):
        return STAGE_READING
    if artifact.classification and artifact.extractor_version == EXTRACTOR_VERSION:
        return STAGE_DONE
    return STAGE_UNDERSTANDING


def learned_summary(db, artifact: DocumentArtifact) -> dict:
    """What the document's candidates became: learned, needs review.

    Keyed on the candidate ids the extract job recorded, so it reports THIS
    document's effect rather than guessing from timestamps.
    """
    ids = list((artifact.extraction_summary or {}).get("candidate_ids") or [])
    learned: dict[str, int] = {}
    needs_review = 0
    if ids:
        rows = db.execute(
            select(MemoryCandidate).where(
                MemoryCandidate.workspace_id == artifact.workspace_id,
                MemoryCandidate.id.in_(ids),
            )
        ).scalars().all()
        for row in rows:
            if row.candidate_type == "student_record" and row.key == "document":
                continue  # the document itself, not something learned from it
            if row.status == "needs_review":
                needs_review += 1
            elif row.status == "accepted":
                label_key = row.key if row.candidate_type == "student_record" else row.candidate_type
                label = _LABELS.get(label_key or "", "profile details")
                learned[label] = learned.get(label, 0) + 1
    return {"learned": learned, "needs_review": needs_review}


def _describe_learned(summary: dict) -> str:
    learned = summary.get("learned") or {}
    if not learned:
        return "nothing new for the profile"
    return ", ".join(f"{label} ({count})" for label, count in sorted(learned.items()))


def recent_documents_block(
    db, workspace_id: str, limit: int = 5, days: int = 14, can_read: bool = True,
) -> str:
    """Per-turn context: recent documents and their REAL status.

    Deliberately includes file_ids, because the Counselor cannot call
    `files.read` on a document it cannot name — and after the upload turn,
    nothing else gives it one.
    """
    since = datetime.now(timezone.utc) - timedelta(days=days)
    rows = db.execute(
        select(DocumentArtifact, FileRecord)
        .join(FileRecord, FileRecord.id == DocumentArtifact.file_id)
        .where(
            DocumentArtifact.workspace_id == workspace_id,
            FileRecord.status == "active",
            DocumentArtifact.created_at >= since,
        )
        .order_by(DocumentArtifact.created_at.desc())
        .limit(limit)
    ).all()
    if not rows:
        return ""

    lines = []
    for artifact, record in rows:
        name = record.filename.rsplit("/", 1)[-1]
        stage = document_stage(artifact)
        kind = artifact.classification or artifact.document_type or "document"
        if stage == STAGE_DONE:
            summary = learned_summary(db, artifact)
            detail = f"finished — added to the profile: {_describe_learned(summary)}"
            if summary["needs_review"]:
                detail += f"; {summary['needs_review']} item(s) await the student's confirmation"
        elif stage == STAGE_UNDERSTANDING:
            detail = (
                "readable now with files.read; profile update still in progress"
                if can_read else "being added to the profile (usually under a minute)"
            )
        elif stage == STAGE_READING:
            detail = "still being read — contents NOT available yet"
        elif stage == STAGE_FAILED:
            detail = "could not be read — ask the student to re-upload it"
        else:
            detail = "not a supported document (only PDF and .docx can be read)"
        lines.append(f"- {name} (file_id: {artifact.file_id}, {kind}): {detail}")

    return (
        "## Student documents\n\n"
        "Current, server-verified status of documents the student uploaded. "
        "This is authoritative: it overrides anything said earlier in the "
        "conversation about these files.\n"
        + "\n".join(lines)
        + "\n\nIf the student asks what was learned from a document, the "
        "finished items above are now part of their profile."
    )


def completion_message(filename: str, classification: str, summary: dict) -> str:
    """The chat message posted when a document finishes processing.

    Deterministic, not model-written: it reports what the reconciler
    actually did, so it cannot overstate what was saved.
    """
    name = filename.rsplit("/", 1)[-1]
    kind = (classification or "document").replace("_", " ")
    if kind == "cv resume":
        kind = "CV"
    learned = summary.get("learned") or {}
    needs_review = summary.get("needs_review") or 0

    if learned:
        text = f"I've finished reading your {kind}, **{name}**. I added {_describe_learned(summary)} to your profile."
    else:
        text = (
            f"I've finished reading **{name}**. It didn't add anything new to your "
            "profile — what it says is either already there or not something I "
            "keep on file."
        )
    if needs_review:
        text += (
            f" {needs_review} detail{'s' if needs_review != 1 else ''} "
            f"{'differ' if needs_review != 1 else 'differs'} from what I had or "
            "need your confirmation — you can review "
            f"{'them' if needs_review != 1 else 'it'} in your profile."
        )
    return text
