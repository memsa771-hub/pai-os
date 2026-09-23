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

from app.database import new_session
from app.memory.candidates import MemoryCandidateService
from app.memory.context import MemoryContextService
from app.memory.reconciler import MemoryReconciler
from app.memory.vault import VaultService
from app.memory.field_definitions import usable_in_counseling

logger = logging.getLogger(__name__)


def _session():
    """Own session per tool call.

    Tool handlers are async and run inside a threadpool request; borrowing the
    request's Session would hold a pooled connection across an await.
    """
    return new_session()


# -- reads (Counselor + Operator) -----------------------------------------


async def get_context(context, args: dict) -> dict:
    """Assemble compact student context. The main read path."""
    db = _session()
    try:
        from app.memory.student_context import StudentContextBuilder
        student = StudentContextBuilder(db).build_context(
            context.workspace_id, query=args.get("query"), caller=context.agent_name,
            intent=args.get("intent"))
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
            definition = vault.fields.get(field_key)
            if definition is not None and not usable_in_counseling(definition):
                return {"ok": False, "error": {"code": "sensitive_field", "message": "This field is not available in conversational context"}}
            fact = vault.get_fact(context.workspace_id, field_key)
            if fact is None:
                return {"ok": True, "data": {"field_key": field_key, "value": None}}
            return {"ok": True, "data": {
                "field_key": field_key,
                "value": (fact.value or {}).get("value"),
                "confidence": fact.confidence,
                "source_type": fact.source_type,
                "claim_origin": fact.claim_origin,
                "capture_method": fact.capture_method,
                "valid_from": fact.valid_from.isoformat() if fact.valid_from else None,
            }}
        from app.memory.student_records import StudentRecordService
        from app.memory.readiness import ReadinessService
        from app.memory.student_context import StudentContextBuilder
        profile = StudentContextBuilder(db).build_context(
            context.workspace_id, query=args.get("query"), caller=context.agent_name,
            intent=args.get("intent"))
        from app.memory.student_schema import extraction_specs
        return {"ok": True, "data": {
            "fields": profile.vault,
            "records": profile.records,
            "record_schemas": extraction_specs(),
            "issues": [
                {"id": issue.id, "type": issue.issue_type, "summary": issue.summary}
                for issue in StudentRecordService(db).issues(context.workspace_id)
            ],
            "readiness": ReadinessService(db).evaluate(context.workspace_id, "discovery"),
        }}
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


async def propose_profile(context, args: dict) -> dict:
    """Narrow Operator write seam: create proposals, never canonical rows."""
    proposals = args.get("proposals") or []
    if not isinstance(proposals, list) or not proposals or len(proposals) > 50:
        return {"ok": False, "error": {"code": "invalid_arguments", "message": "1-50 proposals are required"}}
    db = _session()
    try:
        service = MemoryCandidateService(db)
        ids = []
        for spec in proposals:
            candidate_type = spec.get("candidate_type")
            if candidate_type not in ("student_record", "vault_fact"):
                raise ValueError("Only student_record and vault_fact proposals are supported")
            source_type = "document" if spec.get("file_id") else "agent"
            if spec.get("file_id"):
                from sqlalchemy import select
                from app.models import FileRecord
                owned_file = db.execute(select(FileRecord.id).where(
                    FileRecord.id == spec["file_id"],
                    FileRecord.workspace_id == context.workspace_id,
                    FileRecord.status == "active",
                )).scalar_one_or_none()
                if owned_file is None:
                    raise ValueError("Evidence file does not belong to this workspace")
            evidence = {"file_id": spec.get("file_id"), "quote": spec.get("quote")}
            evidence = {key: value for key, value in evidence.items() if value}
            candidate = service.propose(
                workspace_id=context.workspace_id, candidate_type=candidate_type,
                key=spec.get("key"), proposed_value=spec.get("value"),
                entities=spec.get("entities") or {}, confidence=float(spec.get("confidence", 0.8)),
                source_type=source_type, evidence=evidence,
            )
            ids.append(candidate.id)
        from app.jobs.service import BackgroundJobService
        job = BackgroundJobService(db).enqueue(
            "memory.reconcile", {"candidate_ids": ids}, context.workspace_id,
            idempotency_key="profile-proposals:" + ":".join(ids),
        )
        db.commit()
        return {"ok": True, "data": {"proposed": len(ids), "job_id": job.id}}
    except (TypeError, ValueError) as exc:
        db.rollback()
        return {"ok": False, "error": {"code": "invalid_proposal", "message": str(exc)}}
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


# -- writes (Counselor only) ----------------------------------------------


def _record_patch(path: str | None, answer):
    if not path:
        return answer
    result = answer
    for part in reversed(path.split(".")):
        result = {part: result}
    return result


async def answer_profile_requirement(context, args: dict) -> dict:
    """Save the one active collection question and recalculate immediately."""
    from app.memory.profile_completion import ProfileCompletionService

    requirement_key = (args.get("requirement_key") or "").strip()
    if not requirement_key or "answer" not in args:
        return {"ok": False, "error": {
            "code": "invalid_arguments", "message": "requirement_key and answer are required",
        }}
    db = _session()
    try:
        completion_service = ProfileCompletionService(db)
        snapshot = completion_service.snapshots.build(context.workspace_id)
        completion = completion_service.evaluate(context.workspace_id, snapshot=snapshot)
        active = completion.get("nextRequirement")
        if not active or active.get("key") != requirement_key:
            return {"ok": False, "error": {
                "code": "requirement_not_active",
                "message": "That requirement is not the active missing profile question",
            }}
        requirement = completion_service.registry.get(requirement_key)
        if requirement is None:
            return {"ok": False, "error": {
                "code": "requirement_not_found", "message": "Profile requirement not found",
            }}

        answer = args["answer"]
        candidate_type = "vault_fact"
        key = requirement.source_key
        entities = {}
        proposed_value = answer
        if requirement.source_type == "record_presence":
            if not isinstance(answer, dict):
                return {"ok": False, "error": {
                    "code": "invalid_answer", "message": "This answer must be a structured record",
                }}
            candidate_type = "student_record"
        elif requirement.source_type == "record_field":
            rows = completion_service.registry.select_records(requirement, snapshot)
            if not rows:
                return {"ok": False, "error": {
                    "code": "record_not_found", "message": "The record to update is missing",
                }}
            candidate_type = "student_record"
            entities = {"record_id": rows[0]["id"]}
            proposed_value = _record_patch(requirement.source_path, answer)
        elif requirement.source_type == "journey_gap":
            if not isinstance(answer, dict) or not requirement.source_path:
                return {"ok": False, "error": {
                    "code": "invalid_answer", "message": "This answer must describe the missing qualification",
                }}
            candidate_type = "student_record"
            key = requirement.source_path
        elif requirement.source_type != "vault_fact":
            return {"ok": False, "error": {
                "code": "unsupported_requirement", "message": "This requirement cannot be answered here",
            }}

        candidate = MemoryCandidateService(db).propose(
            workspace_id=context.workspace_id,
            candidate_type=candidate_type,
            key=key,
            proposed_value=proposed_value,
            entities=entities,
            confidence=1.0,
            source_type="user_explicit",
            allow_user_explicit=True,
            evidence={"profile_requirement_key": requirement_key,
                      "capture": "counselor_collection"},
            subject_user_id=context.user_id,
        )
        result = MemoryReconciler(db).reconcile(candidate)
        if not result.accepted:
            db.commit()
            return {"ok": False, "error": {
                "code": "answer_rejected", "message": result.reason or "Profile answer rejected",
            }}
        recalculated = completion_service.evaluate(context.workspace_id)
        db.commit()
        return {"ok": True, "data": {
            "saved": True, "id": result.result_id, "completion": recalculated,
        }}
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


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
        record_type = args.get("record_type")
        if record_type and field_key:
            return {"ok": False, "error": {"code": "invalid_arguments", "message": "Choose a scalar field or a record"}}
        if record_type:
            candidate = candidates.propose(
                workspace_id=context.workspace_id, candidate_type="student_record", key=record_type,
                proposed_value=args.get("value"),
                entities={"record_id": args["record_id"]} if args.get("record_id") else {},
                confidence=1.0, source_type="user_explicit", allow_user_explicit=True,
                evidence={"quote": content},
            )
        elif field_key:
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
