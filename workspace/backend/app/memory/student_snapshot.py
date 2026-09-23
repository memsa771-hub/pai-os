"""Request-scoped internal view of canonical student state."""

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select

from app.models import VaultFact
from .student_records import ENTITY_MODELS, StudentRecordService
from .vault import _unwrap


@dataclass(frozen=True)
class StudentSnapshot:
    workspace_id: str
    facts: dict[str, dict[str, Any]]
    records: dict[str, list[dict[str, Any]]]
    issues: tuple[dict[str, Any], ...]
    generated_at: datetime

    def fact_value(self, key: str) -> Any:
        fact = self.facts.get(key)
        return None if fact is None else fact.get("value")


class StudentSnapshotService:
    """Build one shared read model without creating another source of truth."""

    def __init__(self, db):
        self.db = db
        self.record_service = StudentRecordService(db)

    def build(self, workspace_id: str) -> StudentSnapshot:
        fact_rows = self.db.execute(
            select(VaultFact).where(
                VaultFact.workspace_id == workspace_id,
                VaultFact.status == "active",
            ).order_by(VaultFact.field_key, VaultFact.valid_from.desc())
        ).scalars().all()
        facts: dict[str, dict[str, Any]] = {}
        for row in fact_rows:
            facts.setdefault(row.field_key, {
                "id": row.id,
                "value": _unwrap(row.value),
                "field_version": row.field_version,
                "confidence": row.confidence,
                "source_type": row.source_type,
                "claim_origin": row.claim_origin,
                "capture_method": row.capture_method,
                "source_event_id": row.source_event_id,
                "evidence": row.evidence,
                "valid_from": row.valid_from,
            })

        records: dict[str, list[dict[str, Any]]] = {}
        for kind in ENTITY_MODELS:
            rows = []
            for row in self.record_service.list(workspace_id, kind):
                rows.append({
                    **self.record_service._values(kind, row),
                    "id": row.id,
                    "verification_status": self.record_service._verification(row),
                    "source_type": row.source_type,
                    "claim_origin": row.claim_origin,
                    "capture_method": row.capture_method,
                    "evidence": row.evidence,
                    "created_at": row.created_at,
                    "updated_at": row.updated_at,
                })
            records[kind] = rows

        issues = tuple({
            "id": issue.id,
            "candidate_id": issue.candidate_id,
            "type": issue.issue_type,
            "severity": issue.severity,
            "affected_type": issue.affected_type,
            "affected_id": issue.affected_id,
            "question": issue.clarification_question,
            "evidence": issue.evidence,
        } for issue in self.record_service.issues(workspace_id))

        return StudentSnapshot(
            workspace_id=workspace_id,
            facts=facts,
            records=records,
            issues=issues,
            generated_at=datetime.now(timezone.utc),
        )
