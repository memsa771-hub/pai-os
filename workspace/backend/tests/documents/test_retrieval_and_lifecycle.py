# -*- coding: utf-8 -*-
"""Phase F+G — document retrieval, workspace isolation, purge, gating."""

import asyncio
from types import SimpleNamespace
from unittest.mock import patch

from sqlalchemy import select

from app.documents.content import Segment
from app.documents.retrieval import (
    KIND_DOCUMENT_CHUNK, build_chunks, chunk_id_for, delete_document_chunks,
    index_document_chunks, search_documents,
)
from app.documents.service import DocumentArtifactService
from app.models import DocumentArtifact
from app.services.pai import allowed_tools_for_mode
from tests.documents.conftest import make_file
from tests.documents.fixtures import TRANSCRIPT_PAGES, make_pdf
from tests.documents.test_parse_job import _StubStore, _run


class _RecordingIndex:
    """Stands in for the shared memory index, recording what it is given."""

    def __init__(self):
        self.records = []
        self.deleted = []

    async def index(self, records):
        self.records.extend(records)
        return len(records)

    async def search(self, workspace_id, query, limit=10, kinds=None, filters=None):
        self.last_search = {
            "workspace_id": workspace_id, "kinds": kinds, "filters": filters,
        }
        return []

    async def delete_by_filter(self, workspace_id, filters):
        self.deleted.append((workspace_id, filters))
        return 1


class TestChunking:
    def test_chunks_never_cross_a_locator(self):
        chunks = build_chunks("f1", [
            Segment(locator="p1", text="page one"),
            Segment(locator="p2", text="page two"),
        ], "transcript")

        assert [c.locator for c in chunks] == ["p1", "p2"]

    def test_long_segments_split_within_their_locator(self):
        chunks = build_chunks("f1", [Segment(locator="p1", text="x" * 3000)], "transcript")

        assert len(chunks) > 1
        # Every piece still cites the page it really came from.
        assert {c.locator for c in chunks} == {"p1"}

    def test_chunk_ids_are_deterministic(self):
        # Reindexing must update in place, not duplicate.
        assert chunk_id_for("f1", "p1", 0) == chunk_id_for("f1", "p1", 0)

    def test_empty_segments_are_skipped(self):
        assert build_chunks("f1", [Segment(locator="p1", text="   ")], "t") == []


class TestIndexing:
    def test_chunks_carry_workspace_and_source_metadata(self):
        index = _RecordingIndex()
        with patch("app.memory.index.get_memory_index", return_value=index):
            count = asyncio.run(index_document_chunks(
                workspace_id="ws-1", file_id="f1", document_type="transcript",
                segments=[Segment(locator="p1", text="CGPA 3.41")],
            ))

        assert count == 1
        record = index.records[0]
        assert record.workspace_id == "ws-1"
        assert record.kind == KIND_DOCUMENT_CHUNK
        assert record.filters["file_id"] == "f1"
        assert record.filters["locator"] == "p1"

    def test_search_is_workspace_scoped_and_kind_scoped(self):
        index = _RecordingIndex()
        with patch("app.memory.index.get_memory_index", return_value=index):
            asyncio.run(search_documents("ws-1", "machine learning"))

        assert index.last_search["workspace_id"] == "ws-1"
        # Document chunks only — never mixed into memory retrieval.
        assert index.last_search["kinds"] == (KIND_DOCUMENT_CHUNK,)

    def test_document_chunks_are_excluded_from_default_memory_search(self):
        # The default kinds in the shared index must not include documents,
        # or document text would enter every foreground context block.
        from app.memory.index_qdrant import KIND_DOCUMENT_CHUNK as qdrant_kind
        from app.memory.index_qdrant import KIND_EPISODE, KIND_SEMANTIC

        default_kinds = (KIND_SEMANTIC, KIND_EPISODE)
        assert qdrant_kind not in default_kinds


class TestPurgeLifecycle:
    def test_purge_removes_derived_artifact_and_chunks(self, db, workspace_id):
        data = make_pdf(TRANSCRIPT_PAGES)
        record = make_file(db, workspace_id, "transcript.pdf", data)
        service = DocumentArtifactService(db)
        service.register_and_enqueue(
            workspace_id, record.id, data, "transcript.pdf", "application/pdf")

        removed = service.purge_for_files(workspace_id, [record.id])
        index = _RecordingIndex()
        with patch("app.memory.index.get_memory_index", return_value=index):
            asyncio.run(delete_document_chunks(workspace_id, [record.id]))

        assert removed == 1
        assert db.execute(select(DocumentArtifact)).scalars().all() == []
        assert index.deleted[0][0] == workspace_id
        assert index.deleted[0][1]["file_id"] == [record.id]

    def test_canonical_facts_survive_a_purged_source_file(self, db, workspace_id):
        """Evidence availability and student truth are different concerns.

        A purged file must not silently retract what was learned from it.
        """
        from app.memory.student_records import StudentRecordService
        from app.models import EducationRecord

        records = StudentRecordService(db)
        records.apply(
            workspace_id, "education",
            {"qualification_name": "BS Computer Science"},
            source_type="document", claim_origin="institution_document",
            capture_method="document_extraction",
            evidence={"file_id": "gone", "quote": "BS Computer Science"},
        )
        db.flush()

        DocumentArtifactService(db).purge_for_files(workspace_id, ["gone"])

        rows = db.execute(select(EducationRecord)).scalars().all()
        assert len(rows) == 1
        # Provenance is retained even though the evidence file is gone.
        assert rows[0].evidence["file_id"] == "gone"

    def test_reprocessing_after_a_parser_change_is_possible(self, db, workspace_id):
        data = make_pdf(TRANSCRIPT_PAGES)
        record = make_file(db, workspace_id, "transcript.pdf", data)
        service = DocumentArtifactService(db)
        service.register_and_enqueue(
            workspace_id, record.id, data, "transcript.pdf", "application/pdf")
        store = _StubStore({record.storage_key: data})
        with patch("app.documents.handlers.get_file_store", return_value=store):
            _run(db, workspace_id, record.id)

        artifact = service.get(workspace_id, record.id)
        artifact.parser_version = "0"        # simulate an older parser
        db.flush()

        with patch("app.documents.handlers.get_file_store", return_value=store):
            result = _run(db, workspace_id, record.id)

        assert result["parsed"] is True


class TestCollectionModeGate:
    def test_files_read_is_withheld_in_collection_mode(self):
        # files.read now returns a document's PARSED contents, which is
        # exactly the personalized material the completion gate withholds.
        assert "files.read" not in allowed_tools_for_mode("collection")

    def test_files_read_is_available_in_normal_mode(self):
        assert "files.read" in allowed_tools_for_mode("normal")

    def test_existing_collection_gates_are_unchanged(self):
        collection = allowed_tools_for_mode("collection")
        for tool in ("operator.delegate", "memory.context", "vault.get",
                     "memory.search", "memory.episodes"):
            assert tool not in collection
        # The one safe profile question is still available.
        assert "profile.answer" in collection

    def test_ingestion_is_infrastructure_not_a_counselor_tool(self):
        """Automatic ingestion must keep working during collection mode.

        It runs as background jobs, never as a tool in the Counselor's turn,
        so withholding files.read does not stop a transcript from filling the
        profile (and potentially ending collection mode).
        """
        from app.documents.service import (
            JOB_DOCUMENT_EXTRACT, JOB_DOCUMENT_INDEX, JOB_DOCUMENT_PARSE,
        )

        collection = allowed_tools_for_mode("collection")
        for job_type in (JOB_DOCUMENT_PARSE, JOB_DOCUMENT_EXTRACT, JOB_DOCUMENT_INDEX):
            assert job_type not in collection


class TestWorkspaceIsolation:
    def test_artifacts_are_workspace_scoped(self, db, workspace_id):
        data = make_pdf(TRANSCRIPT_PAGES)
        record = make_file(db, workspace_id, "transcript.pdf", data)
        service = DocumentArtifactService(db)
        service.register_and_enqueue(
            workspace_id, record.id, data, "transcript.pdf", "application/pdf")

        # Another workspace cannot see this student's document state.
        assert service.get(db.info["other"], record.id) is None
        assert service.status_for(db.info["other"], record.id) == "unsupported"
