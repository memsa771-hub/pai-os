# -*- coding: utf-8 -*-
"""Durable job handlers for memory formation.

Registered on import. `app/jobs/worker.py` imports this module precisely so the
handlers exist before it starts claiming.

Why these run here and not in the chat path: extraction calls an LLM and
reconciliation touches several tables. Doing that inline would add seconds to
every reply, which requirement #3 forbids. The conversation completes, a job is
enqueued, and memory catches up a moment later.

Phase 1 deliberately ships extraction as a **no-op stub**: the pipeline,
durability and reconciliation are real and tested end to end, but the LLM
extraction prompt is Phase 2. A stub that records "nothing extracted" is honest;
a half-tuned prompt silently writing wrong facts is not.
"""

import logging

from app.jobs.service import job_handlers
from app.memory.candidates import MemoryCandidateService
from app.memory.index import MemoryRecord, get_memory_index
from app.memory.reconciler import MemoryReconciler
from app.memory.semantic import MemoryService

logger = logging.getLogger(__name__)

JOB_EXTRACT = "memory.extract"
JOB_RECONCILE = "memory.reconcile"
JOB_EMBED = "memory.embed"


async def extract_memory(job, db) -> dict:
    """Propose memory candidates from a conversation slice.

    Phase 2 replaces the body with an extraction prompt. The contract is fixed
    now: this function may ONLY write candidates. It has no access to
    VaultService, so even a buggy future prompt cannot mutate canonical state.
    """
    workspace_id = job.workspace_id
    payload = job.payload or {}
    if not workspace_id:
        raise ValueError("memory.extract requires a workspace_id")

    candidates = MemoryCandidateService(db)
    proposed = []

    # Explicit pre-parsed proposals (used by tests, and by any caller that has
    # already decided what to propose). LLM extraction lands here in Phase 2.
    for spec in payload.get("candidates") or []:
        candidate = candidates.propose(
            workspace_id=workspace_id,
            candidate_type=spec.get("candidate_type", "semantic_memory"),
            operation=spec.get("operation", "upsert"),
            key=spec.get("key"),
            proposed_value=spec.get("proposed_value"),
            content=spec.get("content"),
            entities=spec.get("entities"),
            confidence=float(spec.get("confidence", 0.5)),
            source_type=spec.get("source_type", "conversation"),
            source_event_ids=spec.get("source_event_ids"),
            evidence=spec.get("evidence"),
        )
        proposed.append(candidate.id)

    if not proposed:
        logger.info(
            "memory.extract: no extractor configured (Phase 2) workspace=%s", workspace_id
        )

    # Chain reconciliation as its own durable job rather than calling it here:
    # if reconciliation fails it retries on its own schedule without re-running
    # extraction, and each job stays independently observable.
    if proposed:
        from app.jobs.service import BackgroundJobService

        BackgroundJobService(db).enqueue(
            job_type=JOB_RECONCILE,
            workspace_id=workspace_id,
            payload={"candidate_ids": proposed},
            idempotency_key=f"reconcile:{job.id}",
        )

    return {"candidates_proposed": len(proposed)}


async def reconcile_memory(job, db) -> dict:
    """Run the deterministic reconciler over pending candidates."""
    workspace_id = job.workspace_id
    if not workspace_id:
        raise ValueError("memory.reconcile requires a workspace_id")

    reconciler = MemoryReconciler(db)
    payload = job.payload or {}
    candidate_ids = payload.get("candidate_ids")

    results = []
    if candidate_ids:
        service = MemoryCandidateService(db)
        for candidate_id in candidate_ids:
            candidate = service.get(workspace_id, candidate_id)
            if candidate is not None:
                results.append(reconciler.reconcile(candidate))
    else:
        results = reconciler.reconcile_pending(workspace_id)

    accepted = [r for r in results if r.accepted]

    # Index accepted semantic memories for future retrieval. A NullMemoryIndex
    # makes this a no-op until a real index is configured.
    if accepted:
        memories = MemoryService(db)
        records = []
        for result in accepted:
            if not result.result_id:
                continue
            memory = memories.get(workspace_id, result.result_id)
            if memory is not None:
                records.append(MemoryRecord(
                    id=memory.id,
                    workspace_id=workspace_id,
                    kind="semantic_memory",
                    text=memory.content,
                    filters={"memory_type": memory.memory_type},
                ))
        if records:
            await get_memory_index().index(records)

    return {
        "reconciled": len(results),
        "accepted": len(accepted),
        "rejected": len(results) - len(accepted),
    }


async def embed_memory(job, db) -> dict:
    """Push memories into the retrieval index.

    Separate from reconciliation because embedding is the part that calls an
    external provider and therefore fails differently — it deserves its own
    retry schedule rather than dragging a successful reconciliation down with
    it.
    """
    workspace_id = job.workspace_id
    if not workspace_id:
        raise ValueError("memory.embed requires a workspace_id")

    payload = job.payload or {}
    memories = MemoryService(db)
    records = []
    for memory_id in payload.get("memory_ids") or []:
        memory = memories.get(workspace_id, memory_id)
        if memory is not None and memory.status == "active":
            records.append(MemoryRecord(
                id=memory.id,
                workspace_id=workspace_id,
                kind="semantic_memory",
                text=memory.content,
                filters={"memory_type": memory.memory_type},
            ))

    indexed = await get_memory_index().index(records) if records else 0
    return {"indexed": indexed}


job_handlers.register(JOB_EXTRACT, extract_memory)
job_handlers.register(JOB_RECONCILE, reconcile_memory)
job_handlers.register(JOB_EMBED, embed_memory)
