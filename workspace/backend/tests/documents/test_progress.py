# -*- coding: utf-8 -*-
"""Progress visibility — the "looks stuck" and "still parsing" reports.

Reproduces what a student saw: a CV finished processing in ~80s, yet on a
later turn PAI said it had not finished parsing, and a student who left the
chat came back to a thread that looked idle.
"""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from sqlalchemy import select

from app.documents.attachments import attachment_prompt_block, wait_until_readable
from app.documents.extractor import (
    EXTRACTOR_VERSION, MAX_EPISODES_PER_DOCUMENT, MAX_SEMANTIC_MEMORIES_PER_DOCUMENT,
    DocumentFinding, DocumentUnderstanding, _cap_unstructured,
)
from app.documents.handlers import notify_document_job
from app.documents.progress import (
    STAGE_DONE, STAGE_READING, STAGE_UNDERSTANDING, completion_message,
    document_stage, learned_summary, recent_documents_block,
)
from app.documents.reader import document_status_payload
from app.documents.service import JOB_DOCUMENT_NOTIFY, DocumentArtifactService
from app.memory.handlers import reconcile_memory
from app.models import BackgroundJob
from tests.documents.conftest import make_file
from tests.documents.fixtures import TRANSCRIPT_PAGES, make_pdf
from tests.documents.test_parse_job import _StubStore, _run
from tests.documents.test_reconciliation import _extract, _finding, _understanding


def _registered(db, workspace_id, channel="thread-1"):
    data = make_pdf(TRANSCRIPT_PAGES)
    record = make_file(db, workspace_id, "uploaded_files/transcript.pdf", data)
    record.channel_name = channel
    DocumentArtifactService(db).register_and_enqueue(
        workspace_id, record.id, data, "transcript.pdf", "application/pdf")
    return record, data


def _parse(db, workspace_id, record, data):
    with patch("app.documents.handlers.get_file_store",
               return_value=_StubStore({record.storage_key: data})):
        _run(db, workspace_id, record.id)


def _education():
    return _understanding([_finding("student_record", "education", {
        "qualification_name": "BS Computer Science",
        "institution_name": "University of Example"})])


class TestStages:
    def test_stages_follow_the_pipeline(self, db, workspace_id):
        record, data = _registered(db, workspace_id)
        service = DocumentArtifactService(db)
        assert document_stage(service.get(workspace_id, record.id)) == STAGE_READING

        _parse(db, workspace_id, record, data)
        # Parsed and readable, but the profile update has not run yet.
        assert document_stage(service.get(workspace_id, record.id)) == STAGE_UNDERSTANDING

        _extract(db, workspace_id, record.id, _education())
        assert document_stage(service.get(workspace_id, record.id)) == STAGE_DONE

    def test_status_payload_exposes_the_person_facing_stage(self, db, workspace_id):
        record, data = _registered(db, workspace_id)
        _parse(db, workspace_id, record, data)

        payload = document_status_payload(db, workspace_id, record.id)

        # Storage says "ready" a second after upload; the person-facing stage
        # must still say work is in progress.
        assert payload["processing_status"] == "ready"
        assert payload["document_stage"] == STAGE_UNDERSTANDING


class TestCounselorSeesRealStatus:
    def test_later_turn_is_told_the_document_finished(self, db, workspace_id):
        """The reported bug: 'what did you extract from my CV?' got 'still parsing'."""
        record, data = _registered(db, workspace_id)
        _parse(db, workspace_id, record, data)
        _extract(db, workspace_id, record.id, _education())
        from app.memory.handlers import reconcile_memory as _  # noqa: F401
        from app.memory.reconciler import MemoryReconciler
        from app.memory.candidates import MemoryCandidateService
        for c in MemoryCandidateService(db).pending(workspace_id):
            MemoryReconciler(db).reconcile(c)

        block = recent_documents_block(db, workspace_id)

        assert "transcript.pdf" in block
        # The file_id is what lets the Counselor call files.read at all.
        assert record.id in block
        assert "finished" in block
        assert "education (1)" in block
        # It must override stale claims earlier in the conversation.
        assert "overrides anything said earlier" in block

    def test_block_never_contains_document_text(self, db, workspace_id):
        record, data = _registered(db, workspace_id)
        _parse(db, workspace_id, record, data)

        block = recent_documents_block(db, workspace_id)

        assert "Machine Learning" not in block
        assert "3.41" not in block

    def test_in_progress_document_is_reported_honestly(self, db, workspace_id):
        _registered(db, workspace_id)

        block = recent_documents_block(db, workspace_id)

        assert "NOT available yet" in block

    def test_collection_mode_is_not_pointed_at_a_withheld_tool(self, db, workspace_id):
        record, data = _registered(db, workspace_id)
        _parse(db, workspace_id, record, data)

        block = recent_documents_block(db, workspace_id, can_read=False)
        attachment = attachment_prompt_block(
            [{"file_id": record.id, "filename": "t.pdf", "processing_status": "ready"}],
            can_read=False,
        )

        assert "files.read" not in block
        assert "files.read" not in attachment

    def test_other_workspaces_documents_are_not_listed(self, db, workspace_id):
        _registered(db, workspace_id)

        assert recent_documents_block(db, db.info["other"]) == ""


class TestWaitUntilReadable:
    # Committed first: in production the upload request commits the artifact
    # before the chat turn that waits on it, and the wait re-reads fresh state.
    def test_returns_once_the_parse_lands(self, db, workspace_id):
        record, data = _registered(db, workspace_id)
        _parse(db, workspace_id, record, data)
        db.commit()

        assert asyncio.run(wait_until_readable(db, workspace_id, [record.id], timeout=1)) is True

    def test_is_bounded_when_the_parse_is_slow(self, db, workspace_id):
        record, _ = _registered(db, workspace_id)
        db.commit()

        # Still queued: gives up after the timeout instead of hanging the turn.
        assert asyncio.run(wait_until_readable(db, workspace_id, [record.id], timeout=0.2)) is False


class TestCompletionMessage:
    def test_reports_what_was_actually_saved(self):
        text = completion_message(
            "uploaded_files/cv.docx", "cv_resume",
            {"learned": {"education": 1, "skills": 4}, "needs_review": 2},
        )
        assert "**cv.docx**" in text
        assert "CV" in text
        assert "education (1)" in text and "skills (4)" in text
        assert "2 details" in text

    def test_nothing_new_is_said_plainly(self):
        text = completion_message("t.pdf", "transcript", {"learned": {}, "needs_review": 0})
        assert "didn't add anything new" in text

    def test_summary_counts_only_this_documents_candidates(self, db, workspace_id):
        record, data = _registered(db, workspace_id)
        _parse(db, workspace_id, record, data)
        _extract(db, workspace_id, record.id, _education())
        from app.memory.reconciler import MemoryReconciler
        from app.memory.candidates import MemoryCandidateService
        for c in MemoryCandidateService(db).pending(workspace_id):
            MemoryReconciler(db).reconcile(c)

        artifact = DocumentArtifactService(db).get(workspace_id, record.id)
        summary = learned_summary(db, artifact)

        # The StudentDocument record itself is not "learned from" the file.
        assert summary["learned"] == {"education": 1}


class TestNotifyChain:
    def test_reconcile_of_document_candidates_queues_the_message(self, db, workspace_id):
        record, data = _registered(db, workspace_id)
        _parse(db, workspace_id, record, data)
        _extract(db, workspace_id, record.id, _education())
        artifact = DocumentArtifactService(db).get(workspace_id, record.id)
        ids = artifact.extraction_summary["candidate_ids"]

        job = SimpleNamespace(id="r1", workspace_id=workspace_id,
                              payload={"candidate_ids": ids, "document_file_id": record.id})
        asyncio.run(reconcile_memory(job, db))

        notify = db.execute(select(BackgroundJob).where(
            BackgroundJob.job_type == JOB_DOCUMENT_NOTIFY)).scalars().all()
        assert len(notify) == 1

    def test_ordinary_conversation_reconcile_does_not_notify(self, db, workspace_id):
        job = SimpleNamespace(id="r2", workspace_id=workspace_id, payload={"candidate_ids": []})
        asyncio.run(reconcile_memory(job, db))

        assert db.execute(select(BackgroundJob).where(
            BackgroundJob.job_type == JOB_DOCUMENT_NOTIFY)).scalars().all() == []

    def test_notify_posts_into_the_documents_chat(self, db, workspace_id):
        record, data = _registered(db, workspace_id, channel="thread-42")
        _parse(db, workspace_id, record, data)
        _extract(db, workspace_id, record.id, _education())

        post = AsyncMock(return_value="event-1")
        with patch("app.services.cloud_agent._post_response", new=post):
            result = asyncio.run(notify_document_job(
                SimpleNamespace(id="n1", workspace_id=workspace_id,
                                payload={"file_id": record.id}), db))

        assert result["posted"] is True
        args, kwargs = post.call_args
        assert args[2] == "channel/thread-42"
        assert kwargs["message_type"] == "document_processed"

    def test_document_outside_a_chat_is_not_posted(self, db, workspace_id):
        record, data = _registered(db, workspace_id, channel=None)
        _parse(db, workspace_id, record, data)

        post = AsyncMock()
        with patch("app.services.cloud_agent._post_response", new=post):
            result = asyncio.run(notify_document_job(
                SimpleNamespace(id="n2", workspace_id=workspace_id,
                                payload={"file_id": record.id}), db))

        assert result == {"posted": False, "reason": "no_chat"}
        post.assert_not_called()


class TestFloodingCaps:
    def _f(self, kind, confidence):
        return DocumentFinding(kind, None, None, "x", {}, confidence, {"quote": "q", "locator": "p1"})

    def test_memories_and_episodes_are_capped_most_confident_first(self):
        findings = [self._f("semantic_memory", c / 10) for c in range(1, 8)]
        findings += [self._f("episode", c / 10) for c in range(1, 6)]
        findings += [self._f("student_record", 0.5) for _ in range(9)]

        kept = _cap_unstructured(findings)

        memories = [f for f in kept if f.candidate_type == "semantic_memory"]
        episodes = [f for f in kept if f.candidate_type == "episode"]
        assert len(memories) == MAX_SEMANTIC_MEMORIES_PER_DOCUMENT
        assert len(episodes) == MAX_EPISODES_PER_DOCUMENT
        assert min(f.confidence for f in memories) == 0.5   # the top three kept
        # Structured records are never capped here.
        assert sum(f.candidate_type == "student_record" for f in kept) == 9
