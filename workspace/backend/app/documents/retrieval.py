# -*- coding: utf-8 -*-
"""Derived document search — "what grade did I get in Machine Learning?"

Those details belong in neither the Vault nor semantic memory. A transcript
has fifty courses; promoting each to a canonical fact would bury the profile,
and storing the whole document as memories would flood retrieval with text
nobody asked for. They live here instead: chunks derived from the stored
artifact, rebuildable, and deleted when the source file is purged.

Reuses `app.memory.index` rather than standing up a second vector stack, so
workspace isolation, dimension checks and hybrid ranking are the ones already
tested. Chunks carry `kind="document_chunk"`, which the default memory search
deliberately excludes — document text must not leak into every context block.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

from .content import Segment

logger = logging.getLogger(__name__)

#: Target characters per chunk. Small enough to rank precisely, large enough
#: that a course row keeps its surrounding context.
CHUNK_CHARS = 1200
CHUNK_OVERLAP = 120

KIND_DOCUMENT_CHUNK = "document_chunk"


@dataclass(frozen=True)
class DocumentChunk:
    """One indexed slice, addressed back to its source locator."""

    chunk_id: str
    file_id: str
    locator: str
    text: str
    document_type: str


def chunk_id_for(file_id: str, locator: str, ordinal: int) -> str:
    """Deterministic id — reindexing updates in place instead of duplicating."""
    return f"{file_id}:{locator}:{ordinal}"


def build_chunks(
    file_id: str, segments: list[Segment], document_type: str = "",
) -> list[DocumentChunk]:
    """Split segments into indexable chunks, never crossing a locator.

    A chunk that spanned two pages could not name one honest source, so long
    segments are split within themselves and short ones stay whole.
    """
    chunks: list[DocumentChunk] = []
    for segment in segments:
        text = segment.text.strip()
        if not text:
            continue
        if len(text) <= CHUNK_CHARS:
            chunks.append(DocumentChunk(
                chunk_id=chunk_id_for(file_id, segment.locator, 0),
                file_id=file_id, locator=segment.locator, text=text,
                document_type=document_type,
            ))
            continue
        start = 0
        ordinal = 0
        while start < len(text):
            piece = text[start:start + CHUNK_CHARS]
            chunks.append(DocumentChunk(
                chunk_id=chunk_id_for(file_id, segment.locator, ordinal),
                file_id=file_id, locator=segment.locator, text=piece,
                document_type=document_type,
            ))
            ordinal += 1
            if start + CHUNK_CHARS >= len(text):
                break
            start += CHUNK_CHARS - CHUNK_OVERLAP
    return chunks


async def index_document_chunks(
    workspace_id: str, file_id: str, document_type: str, segments: list[Segment],
) -> int:
    """Write a document's chunks into the shared retrieval index."""
    from app.memory.index import MemoryRecord, get_memory_index

    chunks = build_chunks(file_id, segments, document_type)
    if not chunks:
        return 0

    records = [
        MemoryRecord(
            id=chunk.chunk_id,
            workspace_id=workspace_id,
            kind=KIND_DOCUMENT_CHUNK,
            text=chunk.text,
            filters={
                "file_id": chunk.file_id,
                "locator": chunk.locator,
                "document_type": chunk.document_type,
                "status": "active",
            },
        )
        for chunk in chunks
    ]
    return await get_memory_index().index(records)


async def delete_document_chunks(workspace_id: str, file_ids: list[str]) -> int:
    """Remove every chunk belonging to these files.

    Called on permanent purge. Correctness of *canonical* data does not depend
    on this landing — but purged document text staying searchable would be a
    real disclosure, so this is its own retried job.
    """
    from app.memory.index import get_memory_index

    index = get_memory_index()
    deleter = getattr(index, "delete_by_filter", None)
    if deleter is not None:
        return await deleter(
            workspace_id, {"file_id": file_ids, "kind": KIND_DOCUMENT_CHUNK},
        )

    # NullMemoryIndex and any backend without filtered delete: nothing was
    # indexed, so nothing can be stale.
    return 0


async def search_documents(
    workspace_id: str, query: str, limit: int = 8,
    file_id: Optional[str] = None,
) -> list[dict]:
    """Rank document chunks for a question, within one workspace."""
    from app.memory.index import get_memory_index

    filters: dict = {"status": "active"}
    if file_id:
        filters["file_id"] = file_id

    hits = await get_memory_index().search(
        workspace_id=workspace_id, query=query, limit=limit,
        kinds=(KIND_DOCUMENT_CHUNK,), filters=filters,
    )
    return [
        {
            "chunk_id": hit.id,
            "score": hit.score,
            "text": hit.text,
            # file_id/locator are recoverable from the deterministic chunk id,
            # so a backend that returns no payload still yields a citation.
            "file_id": hit.id.split(":", 1)[0] if hit.id else None,
        }
        for hit in hits
    ]
