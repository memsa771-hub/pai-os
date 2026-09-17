# -*- coding: utf-8 -*-
"""Memory job handlers and the MemoryIndex seam."""

import pytest

from app.jobs.service import BackgroundJobService, run_job
from app.memory.handlers import JOB_EMBED, JOB_EXTRACT, JOB_RECONCILE
from app.memory.index import MemoryIndex, MemoryRecord, SearchHit, set_memory_index
from app.memory.semantic import MemoryService
from app.memory.vault import VaultService
from app.models import BackgroundJob, MemoryCandidate, PaiMemory, VaultFact


class _RecordingIndex(MemoryIndex):
    """Captures what would be indexed, without any provider."""

    def __init__(self):
        self.indexed: list[MemoryRecord] = []
        self.deleted: list[str] = []

    async def index(self, records):
        self.indexed.extend(records)
        return len(records)

    async def search(self, workspace_id, query, limit=10, kinds=None, filters=None):
        return [SearchHit(id=r.id, kind=r.kind, score=1.0, text=r.text)
                for r in self.indexed if r.workspace_id == workspace_id][:limit]

    async def delete(self, workspace_id, ids):
        self.deleted.extend(ids)
        return len(ids)


@pytest.fixture
def recording_index():
    index = _RecordingIndex()
    set_memory_index(index)
    yield index
    from app.memory.index import NullMemoryIndex
    set_memory_index(NullMemoryIndex())


@pytest.mark.asyncio
async def test_extract_only_creates_candidates(db_session, workspace, seed_fields):
    """Extraction must not be able to write canonical state.

    Even handed a Vault proposal, the extract handler produces a candidate and
    nothing else — the reconciler is a separate step.
    """
    service = BackgroundJobService(db_session)
    job = service.enqueue(JOB_EXTRACT, {
        "candidates": [{
            "candidate_type": "vault_fact", "key": "education.cgpa",
            "proposed_value": 8.2, "confidence": 0.9, "source_type": "conversation",
        }],
    }, workspace_id=workspace.id)
    db_session.commit()

    result = await run_job(job, db_session)
    db_session.commit()

    assert result["candidates_proposed"] == 1
    assert db_session.query(MemoryCandidate).count() == 1
    assert db_session.query(VaultFact).count() == 0        # nothing canonical yet


@pytest.mark.asyncio
async def test_extract_chains_a_reconcile_job(db_session, workspace, seed_fields):
    """Reconciliation is its own durable job, so it retries independently."""
    service = BackgroundJobService(db_session)
    job = service.enqueue(JOB_EXTRACT, {
        "candidates": [{"candidate_type": "semantic_memory", "content": "Likes Berlin",
                        "confidence": 0.9}],
    }, workspace_id=workspace.id)
    db_session.commit()

    await run_job(job, db_session)
    db_session.commit()

    chained = db_session.query(BackgroundJob).filter(
        BackgroundJob.job_type == JOB_RECONCILE
    ).all()
    assert len(chained) == 1
    assert chained[0].workspace_id == workspace.id


@pytest.mark.asyncio
async def test_full_pipeline_extract_then_reconcile(db_session, workspace, seed_fields):
    """End to end: a proposal becomes canonical only after reconciliation."""
    service = BackgroundJobService(db_session)
    extract = service.enqueue(JOB_EXTRACT, {
        "candidates": [{
            "candidate_type": "vault_fact", "key": "education.cgpa",
            "proposed_value": 8.2, "confidence": 1.0,
        }],
    }, workspace_id=workspace.id)
    db_session.commit()

    await run_job(extract, db_session)
    db_session.commit()

    reconcile = db_session.query(BackgroundJob).filter(
        BackgroundJob.job_type == JOB_RECONCILE
    ).one()
    result = await run_job(reconcile, db_session)
    db_session.commit()

    assert result["accepted"] == 1
    assert VaultService(db_session).get_fact(workspace.id, "education.cgpa").value["value"] == 8.2


@pytest.mark.asyncio
async def test_reconcile_reports_rejections(db_session, workspace, seed_fields):
    """A bad proposal is counted as rejected, not silently dropped."""
    service = BackgroundJobService(db_session)
    extract = service.enqueue(JOB_EXTRACT, {
        "candidates": [{
            "candidate_type": "vault_fact", "key": "education.cgpa",
            "proposed_value": 99, "confidence": 1.0,
        }],
    }, workspace_id=workspace.id)
    db_session.commit()
    await run_job(extract, db_session)
    db_session.commit()

    reconcile = db_session.query(BackgroundJob).filter(
        BackgroundJob.job_type == JOB_RECONCILE
    ).one()
    result = await run_job(reconcile, db_session)
    db_session.commit()

    assert result["accepted"] == 0 and result["rejected"] == 1
    assert db_session.query(VaultFact).count() == 0


@pytest.mark.asyncio
async def test_reconcile_enqueues_embedding_instead_of_indexing_inline(
    db_session, workspace, recording_index,
):
    """Reconciliation must not call the index itself.

    Indexing is a separate durable job so an embedding-provider outage retries
    on its own schedule instead of failing reconciliation.
    """
    service = BackgroundJobService(db_session)
    extract = service.enqueue(JOB_EXTRACT, {
        "candidates": [{
            "candidate_type": "semantic_memory",
            "content": "Prefers research-focused universities",
            "confidence": 0.9,
        }],
    }, workspace_id=workspace.id)
    db_session.commit()
    await run_job(extract, db_session)
    db_session.commit()

    reconcile = db_session.query(BackgroundJob).filter(
        BackgroundJob.job_type == JOB_RECONCILE
    ).one()
    result = await run_job(reconcile, db_session)
    db_session.commit()

    assert result["accepted"] == 1
    assert result["embed_enqueued"] == 1
    # Nothing was indexed synchronously...
    assert recording_index.indexed == []
    # ...but a durable embed job now exists to do it.
    embed = db_session.query(BackgroundJob).filter(
        BackgroundJob.job_type == JOB_EMBED
    ).one()
    assert embed.status == "pending"
    assert len(embed.payload["memory_ids"]) == 1

    # And running it does the actual indexing.
    embed.status = "running"
    await run_job(embed, db_session)
    assert len(recording_index.indexed) == 1
    assert "research-focused" in recording_index.indexed[0].text


@pytest.mark.asyncio
async def test_embed_job_is_idempotent_across_reconcile_retries(
    db_session, workspace, recording_index,
):
    """A retried reconcile must not queue the same embedding twice."""
    service = BackgroundJobService(db_session)
    extract = service.enqueue(JOB_EXTRACT, {
        "candidates": [{"candidate_type": "semantic_memory",
                        "content": "Wants Berlin", "confidence": 0.9}],
    }, workspace_id=workspace.id)
    db_session.commit()
    await run_job(extract, db_session)
    db_session.commit()

    reconcile = db_session.query(BackgroundJob).filter(
        BackgroundJob.job_type == JOB_RECONCILE
    ).one()
    await run_job(reconcile, db_session)
    db_session.commit()

    # Re-run the same reconcile job (what a retry after a crash looks like).
    await run_job(reconcile, db_session)
    db_session.commit()

    assert db_session.query(BackgroundJob).filter(
        BackgroundJob.job_type == JOB_EMBED
    ).count() == 1


@pytest.mark.asyncio
async def test_index_failure_does_not_roll_back_canonical_memory(db_session, workspace):
    """An embedding outage must not undo successful reconciliation.

    The memory is canonical in PostgreSQL the moment reconciliation commits;
    only the separate embed job fails, and only it retries.
    """

    class _BrokenIndex(MemoryIndex):
        async def index(self, records):
            raise RuntimeError("vector store down")

        async def search(self, workspace_id, query, limit=10, kinds=None, filters=None):
            return []

        async def delete(self, workspace_id, ids):
            return 0

    set_memory_index(_BrokenIndex())
    try:
        service = BackgroundJobService(db_session)
        extract = service.enqueue(JOB_EXTRACT, {
            "candidates": [{"candidate_type": "semantic_memory",
                            "content": "Wants Germany", "confidence": 0.9}],
        }, workspace_id=workspace.id)
        db_session.commit()
        await run_job(extract, db_session)
        db_session.commit()

        reconcile = db_session.query(BackgroundJob).filter(
            BackgroundJob.job_type == JOB_RECONCILE
        ).one()
        # Reconciliation SUCCEEDS despite the index being down, because it
        # never touches the index.
        result = await run_job(reconcile, db_session)
        db_session.commit()
        assert result["accepted"] == 1
        assert db_session.query(PaiMemory).count() == 1

        # Only the embed job fails, and it is the only thing that retries.
        embed = db_session.query(BackgroundJob).filter(
            BackgroundJob.job_type == JOB_EMBED
        ).one()
        embed.status = "running"
        with pytest.raises(RuntimeError):
            await run_job(embed, db_session)
        db_session.rollback()

        # Canonical memory survived the index outage.
        assert db_session.query(PaiMemory).count() == 1
        assert MemoryService(db_session).list_memories(workspace.id)
    finally:
        from app.memory.index import NullMemoryIndex
        set_memory_index(NullMemoryIndex())


@pytest.mark.asyncio
async def test_extract_without_a_workspace_fails_loudly(db_session):
    service = BackgroundJobService(db_session)
    job = service.enqueue(JOB_EXTRACT, {"candidates": []}, workspace_id=None)
    db_session.commit()

    with pytest.raises(ValueError, match="requires a workspace_id"):
        await run_job(job, db_session)


@pytest.mark.asyncio
async def test_null_index_is_a_safe_default(db_session, workspace):
    """No index configured must not break memory formation."""
    service = BackgroundJobService(db_session)
    extract = service.enqueue(JOB_EXTRACT, {
        "candidates": [{"candidate_type": "semantic_memory",
                        "content": "Likes Munich", "confidence": 0.9}],
    }, workspace_id=workspace.id)
    db_session.commit()
    await run_job(extract, db_session)
    db_session.commit()

    reconcile = db_session.query(BackgroundJob).filter(
        BackgroundJob.job_type == JOB_RECONCILE
    ).one()
    result = await run_job(reconcile, db_session)
    db_session.commit()

    assert result["accepted"] == 1
    assert MemoryService(db_session).list_memories(workspace.id)
