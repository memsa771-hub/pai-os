"""Only database boundary for Student Journey state."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select

from app.models import StudentJourney, StudentJourneyEvent
from .events import JOURNEY_CREATED, JOURNEY_PRIMARY_CHANGED, JOURNEY_STATUS_CHANGED, JOURNEY_UPDATED
from .models import JourneyView

VALID_STATUSES = frozenset({"active", "paused", "completed", "abandoned"})
UPDATABLE = frozenset({
    "journey_type", "title", "active_goal", "current_stage", "current_objective",
    "target_outcome", "target_date", "milestones", "decisions",
    "unresolved_decisions", "blockers", "next_milestone", "next_recommended_action",
})


class JourneyError(ValueError):
    pass


class JourneyService:
    def __init__(self, db):
        self.db = db

    def create(self, workspace_id: str, journey_type: str, title: str, *,
               primary: bool = False, actor: str = "system", **values: Any) -> JourneyView:
        if not journey_type.strip() or not title.strip():
            raise JourneyError("journey_type and title are required")
        if primary:
            self._clear_primary(workspace_id)
        row = StudentJourney(
            workspace_id=workspace_id, journey_type=journey_type.strip(), title=title.strip(),
            is_primary=primary, **{k: v for k, v in values.items() if k in UPDATABLE},
        )
        self.db.add(row)
        self.db.flush()
        self._event(row, JOURNEY_CREATED, {"title": row.title}, actor)
        return self._view(row)

    def get(self, workspace_id: str, journey_id: str) -> JourneyView | None:
        row = self._row(workspace_id, journey_id)
        return self._view(row) if row else None

    def list(self, workspace_id: str, status: str | None = None) -> list[JourneyView]:
        query = select(StudentJourney).where(StudentJourney.workspace_id == workspace_id)
        if status:
            query = query.where(StudentJourney.status == status)
        rows = self.db.execute(query.order_by(StudentJourney.created_at.desc())).scalars().all()
        return [self._view(row) for row in rows]

    def resolve_primary(self, workspace_id: str) -> JourneyView | None:
        row = self.db.execute(select(StudentJourney).where(
            StudentJourney.workspace_id == workspace_id,
            StudentJourney.status == "active",
        ).order_by(StudentJourney.is_primary.desc(), StudentJourney.updated_at.desc()).limit(1)).scalar_one_or_none()
        return self._view(row) if row else None

    def update(self, workspace_id: str, journey_id: str, *, actor: str = "system", **changes: Any) -> JourneyView:
        row = self._require(workspace_id, journey_id)
        invalid = set(changes) - UPDATABLE
        if invalid:
            raise JourneyError(f"unsupported journey fields: {', '.join(sorted(invalid))}")
        for key, value in changes.items():
            setattr(row, key, value)
        row.updated_at = datetime.now(timezone.utc)
        self._event(row, JOURNEY_UPDATED, {"changed": sorted(changes)}, actor)
        return self._view(row)

    def set_status(self, workspace_id: str, journey_id: str, status: str, *, actor: str = "system") -> JourneyView:
        if status not in VALID_STATUSES:
            raise JourneyError("invalid journey status")
        row = self._require(workspace_id, journey_id)
        previous = row.status
        row.status = status
        row.completed_at = datetime.now(timezone.utc) if status == "completed" else None
        if status != "active":
            row.is_primary = False
        self._event(row, JOURNEY_STATUS_CHANGED, {"from": previous, "to": status}, actor)
        return self._view(row)

    def set_primary(self, workspace_id: str, journey_id: str, *, actor: str = "system") -> JourneyView:
        row = self._require(workspace_id, journey_id)
        if row.status != "active":
            raise JourneyError("only an active journey can be primary")
        self._clear_primary(workspace_id)
        row.is_primary = True
        self._event(row, JOURNEY_PRIMARY_CHANGED, {}, actor)
        return self._view(row)

    def history(self, workspace_id: str, journey_id: str) -> list[dict]:
        self._require(workspace_id, journey_id)
        rows = self.db.execute(select(StudentJourneyEvent).where(
            StudentJourneyEvent.workspace_id == workspace_id,
            StudentJourneyEvent.journey_id == journey_id,
        ).order_by(StudentJourneyEvent.created_at)).scalars().all()
        return [{"id": r.id, "event_type": r.event_type, "payload": r.payload,
                 "actor": r.actor, "created_at": r.created_at} for r in rows]

    def _clear_primary(self, workspace_id: str) -> None:
        for row in self.db.execute(select(StudentJourney).where(
            StudentJourney.workspace_id == workspace_id, StudentJourney.is_primary.is_(True),
        )).scalars():
            row.is_primary = False

    def _row(self, workspace_id: str, journey_id: str):
        return self.db.execute(select(StudentJourney).where(
            StudentJourney.id == journey_id, StudentJourney.workspace_id == workspace_id,
        )).scalar_one_or_none()

    def _require(self, workspace_id: str, journey_id: str):
        row = self._row(workspace_id, journey_id)
        if row is None:
            raise JourneyError("journey not found")
        return row

    def _event(self, row, event_type: str, payload: dict, actor: str) -> None:
        self.db.add(StudentJourneyEvent(journey_id=row.id, workspace_id=row.workspace_id,
                                        event_type=event_type, payload=payload, actor=actor))
        self.db.flush()

    @staticmethod
    def _view(row) -> JourneyView:
        return JourneyView(
            id=row.id, workspace_id=str(row.workspace_id), journey_type=row.journey_type,
            title=row.title, status=row.status, is_primary=row.is_primary,
            active_goal=row.active_goal, current_stage=row.current_stage,
            current_objective=row.current_objective, target_outcome=row.target_outcome,
            target_date=row.target_date, milestones=list(row.milestones or []),
            decisions=list(row.decisions or []), unresolved_decisions=list(row.unresolved_decisions or []),
            blockers=list(row.blockers or []), next_milestone=row.next_milestone,
            next_recommended_action=row.next_recommended_action,
            created_at=row.created_at, updated_at=row.updated_at, completed_at=row.completed_at,
        )
