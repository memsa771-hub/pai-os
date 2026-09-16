# -*- coding: utf-8 -*-
"""Memory tool handlers.

Capability declarations live in `app/tools/builtin/__init__.py` alongside the
other registrations; this module is just the handlers.

The split that matters:

  * read handlers        -> vault.read / memory.read   (Counselor + Operator)
  * remember / forget    -> vault.manage / memory.manage (Counselor only)

`remember` and `forget` are the explicit-user-command path from requirement #3,
so they commit synchronously before PAI confirms — the student is told "saved",
and it is saved. They still go through candidate + reconciler, so even the
explicit path never lets a model write canonical state directly; it only gets
to skip the *queue*, not the *gate*.
"""

import logging

from app.database import SessionLocal
from app.memory.candidates import MemoryCandidateService
from app.memory.context import MemoryContextService
from app.memory.reconciler import MemoryReconciler
from app.memory.vault import VaultService

logger = logging.getLogger(__name__)


def _session():
    """Own session per tool call.

    Tool handlers are async and run inside a threadpool request; borrowing the
    request's Session would hold a pooled connection across an await.
    """
    return SessionLocal()


# -- reads (Counselor + Operator) -----------------------------------------


async def get_context(context, args: dict) -> dict:
    """Assemble compact student context. The main read path."""
    db = _session()
    try:
        service = MemoryContextService(db)
        student = service.build_student_context(
            workspace_id=context.workspace_id,
            query=args.get("query"),
            context_refs=args.get("context_refs"),
            caller=context.agent_name,
            include_sensitive=False,
        )
        return {"ok": True, "data": student.to_dict()}
    finally:
        db.close()


async def vault_get(context, args: dict) -> dict:
    """Read Vault facts — the whole snapshot, or one field with provenance."""
    db = _session()
    try:
        vault = VaultService(db)
        field_key = args.get("field_key")
        if field_key:
            fact = vault.get_fact(context.workspace_id, field_key)
            if fact is None:
                return {"ok": True, "data": {"field_key": field_key, "value": None}}
            return {"ok": True, "data": {
                "field_key": field_key,
                "value": (fact.value or {}).get("value"),
                "confidence": fact.confidence,
                "source_type": fact.source_type,
                "valid_from": fact.valid_from.isoformat() if fact.valid_from else None,
            }}
        return {"ok": True, "data": {"fields": vault.snapshot(context.workspace_id)}}
    finally:
        db.close()


async def memory_search(context, args: dict) -> dict:
    """Search semantic memories."""
    from app.memory.semantic import MemoryService

    db = _session()
    try:
        service = MemoryService(db)
        found = service.search(
            context.workspace_id,
            args.get("query", ""),
            limit=min(int(args.get("limit", 10)), 50),
            memory_type=args.get("memory_type"),
        )
        return {"ok": True, "data": {"memories": [MemoryService.to_dict(m) for m in found]}}
    finally:
        db.close()


async def episodes_recent(context, args: dict) -> dict:
    """Recent episodes — what has happened lately."""
    from app.memory.episodic import EpisodicMemoryService

    db = _session()
    try:
        service = EpisodicMemoryService(db)
        found = service.recent(
            context.workspace_id,
            limit=min(int(args.get("limit", 10)), 50),
            event_type=args.get("event_type"),
        )
        return {"ok": True, "data": {
            "episodes": [EpisodicMemoryService.to_dict(e) for e in found]
        }}
    finally:
        db.close()


# -- writes (Counselor only) ----------------------------------------------


async def remember(context, args: dict) -> dict:
    """Explicit "remember that ..." — durably committed before confirming.

    Proposed as a candidate and reconciled inline. `source_type` is
    `user_explicit`, which is what lets it bypass the confidence floor and
    outrank previously inferred values.
    """
    content = (args.get("content") or "").strip()
    if not content:
        return {"ok": False, "error": {"code": "invalid_arguments", "message": "content is required"}}

    db = _session()
    try:
        candidates = MemoryCandidateService(db)
        field_key = args.get("field_key")
        if field_key:
            candidate = candidates.propose(
                workspace_id=context.workspace_id,
                candidate_type="vault_fact",
                operation="upsert",
                key=field_key,
                proposed_value=args.get("value"),
                confidence=1.0,
                source_type="user_explicit", allow_user_explicit=True,
                evidence={"quote": content},
            )
        else:
            candidate = candidates.propose(
                workspace_id=context.workspace_id,
                candidate_type="semantic_memory",
                operation="upsert",
                content=content,
                entities={"memory_type": args.get("memory_type", "preference")},
                confidence=1.0,
                source_type="user_explicit", allow_user_explicit=True,
            )

        result = MemoryReconciler(db).reconcile(candidate)
        if not result.accepted:
            db.commit()  # keep the rejection record
            return {"ok": False, "error": {
                "code": "memory_rejected", "message": result.reason or "rejected"
            }}
        db.commit()
        return {"ok": True, "data": {"remembered": True, "id": result.result_id}}
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


async def forget(context, args: dict) -> dict:
    """Explicit "forget ..." — durably committed before confirming.

    Marks matching memories `forgotten`; they are excluded from every read
    thereafter. Vault fields are retracted rather than deleted.
    """
    target = (args.get("query") or "").strip()
    field_key = args.get("field_key")
    if not target and not field_key:
        return {"ok": False, "error": {
            "code": "invalid_arguments", "message": "query or field_key is required"
        }}

    db = _session()
    try:
        candidates = MemoryCandidateService(db)
        if field_key:
            candidate = candidates.propose(
                workspace_id=context.workspace_id,
                candidate_type="vault_fact",
                operation="retract",
                key=field_key,
                confidence=1.0,
                source_type="user_explicit", allow_user_explicit=True,
            )
        else:
            candidate = candidates.propose(
                workspace_id=context.workspace_id,
                candidate_type="semantic_memory",
                operation="forget",
                content=target,
                confidence=1.0,
                source_type="user_explicit", allow_user_explicit=True,
            )

        result = MemoryReconciler(db).reconcile(candidate)
        db.commit()
        if not result.accepted:
            return {"ok": False, "error": {
                "code": "memory_not_found", "message": result.reason or "nothing to forget"
            }}
        return {"ok": True, "data": {"forgotten": True}}
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
