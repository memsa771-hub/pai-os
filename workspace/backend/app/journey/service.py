"""Only database boundary for Student Journey state."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from sqlalchemy import select

from app.models import StudentJourney, StudentJourneyEvent
from .events import (
    JOURNEY_ABANDONED, JOURNEY_BLOCKER_ADDED, JOURNEY_BLOCKER_RESOLVED,
    JOURNEY_COMPLETED, JOURNEY_CREATED, JOURNEY_DECISION_ADDED,
    JOURNEY_DECISION_CHANGED, JOURNEY_FOCUS_CHANGED, JOURNEY_GOAL_ADDED,
    JOURNEY_GOAL_CHANGED, JOURNEY_GOAL_COMPLETED, JOURNEY_MILESTONE_UPDATED,
    JOURNEY_PAUSED, JOURNEY_PRIMARY_CHANGED, JOURNEY_STAGE_CHANGED,
    JOURNEY_STATUS_CHANGED, JOURNEY_UPDATED,
)
from .models import JourneyView
from .schemas import (
    validate_blocker, validate_decision, validate_goal, validate_goals,
    validate_milestone,
)
from .transitions import JourneyStage, normalize_stage, require_transition

VALID_STATUSES = frozenset({"active", "paused", "completed", "abandoned"})
UPDATABLE = frozenset({
    "journey_type", "title", "active_goal", "goals", "current_focus_goal_id",
    "current_stage", "current_objective",
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
        if not isinstance(journey_type, str) or not isinstance(title, str) or not journey_type.strip() or not title.strip():
            raise JourneyError("journey_type and title are required")
        invalid = set(values) - UPDATABLE
        if invalid:
            raise JourneyError(f"unsupported journey fields: {', '.join(sorted(invalid))}")
        if "current_stage" in values:
            try:
                values["current_stage"] = normalize_stage(values["current_stage"])
            except ValueError as exc:
                raise JourneyError(str(exc)) from exc
        if "goals" in values:
            try:
                values["goals"] = validate_goals(values["goals"])
            except ValueError as exc:
                raise JourneyError(str(exc)) from exc
            focus = values.get("current_focus_goal_id") or values["goals"][0]["id"]
            if focus not in {goal["id"] for goal in values["goals"]}:
                raise JourneyError("current focus must reference a goal in this journey")
            values["current_focus_goal_id"] = focus
            values["active_goal"] = next(goal for goal in values["goals"] if goal["id"] == focus)
        else:
            legacy = values.get("active_goal")
            goal_title = (legacy.get("title") if isinstance(legacy, dict) else legacy) or title.strip()
            goal = {
                "id": new_id("goal"), "parent_goal_id": None, "type": "primary",
                "title": str(goal_title), "status": "active", "priority": "high",
                "depends_on": [],
            }
            values["goals"] = [goal]
            values["current_focus_goal_id"] = goal["id"]
            values["active_goal"] = goal
        if primary:
            self._clear_primary(workspace_id, actor=actor)
        row = StudentJourney(
            workspace_id=workspace_id, journey_type=journey_type.strip(), title=title.strip(),
            is_primary=primary, **values,
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

    def get_primary(self, workspace_id: str) -> JourneyView | None:
        """Return only the explicitly primary active journey."""
        row = self.db.execute(select(StudentJourney).where(
            StudentJourney.workspace_id == workspace_id,
            StudentJourney.status == "active", StudentJourney.is_primary.is_(True),
        ).limit(1)).scalar_one_or_none()
        return self._view(row) if row else None

    def resolve_active(self, workspace_id: str) -> JourneyView | None:
        """Resolve primary, falling back to the most recently updated active journey."""
        row = self.db.execute(select(StudentJourney).where(
            StudentJourney.workspace_id == workspace_id,
            StudentJourney.status == "active",
        ).order_by(StudentJourney.is_primary.desc(), StudentJourney.updated_at.desc()).limit(1)).scalar_one_or_none()
        return self._view(row) if row else None

    def resolve_primary(self, workspace_id: str) -> JourneyView | None:
        """Deprecated exact-primary alias; fallback is explicit in resolve_active."""
        return self.get_primary(workspace_id)

    def list_active(self, workspace_id: str) -> list[JourneyView]:
        rows = self.db.execute(select(StudentJourney).where(
            StudentJourney.workspace_id == workspace_id,
            StudentJourney.status == "active",
        ).order_by(StudentJourney.is_primary.desc(), StudentJourney.updated_at.desc())).scalars().all()
        return [self._view(row) for row in rows]

    def update(self, workspace_id: str, journey_id: str, *, actor: str = "system", **changes: Any) -> JourneyView:
        row = self._require(workspace_id, journey_id)
        invalid = set(changes) - UPDATABLE
        if invalid:
            raise JourneyError(f"unsupported journey fields: {', '.join(sorted(invalid))}")
        if "current_stage" in changes:
            try:
                changes["current_stage"] = normalize_stage(changes["current_stage"])
            except ValueError as exc:
                raise JourneyError(str(exc)) from exc
        if "goals" in changes:
            try:
                changes["goals"] = validate_goals(changes["goals"])
            except ValueError as exc:
                raise JourneyError(str(exc)) from exc
            focus = changes.get("current_focus_goal_id", row.current_focus_goal_id)
            if focus and focus not in {goal["id"] for goal in changes["goals"]}:
                raise JourneyError("current focus must reference a goal in this journey")
        effective = {key: value for key, value in changes.items() if getattr(row, key) != value}
        if not effective:
            return self._view(row)
        for key, value in effective.items():
            setattr(row, key, value)
        row.updated_at = datetime.now(timezone.utc)
        self._event(row, JOURNEY_UPDATED, {"changed": sorted(effective)}, actor)
        return self._view(row)

    def set_status(self, workspace_id: str, journey_id: str, status: str, *, actor: str = "system") -> JourneyView:
        if status not in VALID_STATUSES:
            raise JourneyError("invalid journey status")
        row = self._require(workspace_id, journey_id)
        previous = row.status
        if previous == status:
            return self._view(row)
        if status == "active" and previous != "paused":
            raise JourneyError("only a paused journey can be resumed")
        was_primary = row.is_primary
        row.status = status
        row.completed_at = datetime.now(timezone.utc) if status == "completed" else None
        if status != "active":
            row.is_primary = False
        event = {"paused": JOURNEY_PAUSED, "completed": JOURNEY_COMPLETED,
                 "abandoned": JOURNEY_ABANDONED}.get(status, JOURNEY_STATUS_CHANGED)
        if status == "completed":
            previous_stage = row.current_stage
            row.current_stage = JourneyStage.COMPLETED.value
            if previous_stage != row.current_stage:
                self._event(row, JOURNEY_STAGE_CHANGED,
                            {"from": previous_stage, "to": row.current_stage}, actor)
        terminal_goal_status = {"completed": "completed", "abandoned": "abandoned"}.get(status)
        if terminal_goal_status:
            goals = []
            for goal in (row.goals or []):
                changed = goal.get("status") in {"proposed", "active", "paused"}
                goals.append({**goal, "status": terminal_goal_status} if changed else goal)
                if changed:
                    self._event(row, JOURNEY_GOAL_COMPLETED if status == "completed" else JOURNEY_GOAL_CHANGED,
                                {"goal_id": goal.get("id"), "status": terminal_goal_status}, actor)
            row.goals = goals
            row.active_goal = next((goal for goal in goals
                                    if goal.get("id") == row.current_focus_goal_id), row.active_goal)
        self._event(row, event, {"from": previous, "to": status}, actor)
        if was_primary and status != "active":
            replacement = self.db.execute(select(StudentJourney).where(
                StudentJourney.workspace_id == workspace_id,
                StudentJourney.status == "active", StudentJourney.id != row.id,
            ).order_by(StudentJourney.updated_at.desc()).limit(1)).scalar_one_or_none()
            if replacement is not None:
                replacement.is_primary = True
                self._event(replacement, JOURNEY_PRIMARY_CHANGED,
                            {"primary": True, "replaced_journey_id": row.id}, actor)
        return self._view(row)

    def pause(self, workspace_id: str, journey_id: str, *, actor: str = "system") -> JourneyView:
        return self.set_status(workspace_id, journey_id, "paused", actor=actor)

    def complete(self, workspace_id: str, journey_id: str, *, actor: str = "system") -> JourneyView:
        return self.set_status(workspace_id, journey_id, "completed", actor=actor)

    def abandon(self, workspace_id: str, journey_id: str, *, actor: str = "system") -> JourneyView:
        return self.set_status(workspace_id, journey_id, "abandoned", actor=actor)

    def resume(self, workspace_id: str, journey_id: str, *, actor: str = "system") -> JourneyView:
        return self.set_status(workspace_id, journey_id, "active", actor=actor)

    def set_stage(self, workspace_id: str, journey_id: str, stage: str, *, actor: str = "system") -> JourneyView:
        row = self._require(workspace_id, journey_id)
        try:
            target = require_transition(row.current_stage, stage)
        except ValueError as exc:
            raise JourneyError(str(exc)) from exc
        previous = row.current_stage
        if previous == target:
            return self._view(row)
        row.current_stage = target
        row.updated_at = datetime.now(timezone.utc)
        self._event(row, JOURNEY_STAGE_CHANGED, {"from": previous, "to": target}, actor)
        return self._view(row)

    def add_goal(self, workspace_id: str, journey_id: str, goal: dict, *,
                 focus: bool = False, actor: str = "system") -> JourneyView:
        row = self._require(workspace_id, journey_id)
        try:
            item = validate_goal(goal)
        except ValueError as exc:
            raise JourneyError(str(exc)) from exc
        goals = list(row.goals or [])
        if any(existing.get("id") == item["id"] for existing in goals):
            raise JourneyError("goal id already exists")
        if any(_goal_key(existing) == _goal_key(item) for existing in goals):
            return self._view(row)
        try:
            goals = validate_goals([*goals, item])
        except ValueError as exc:
            raise JourneyError(str(exc)) from exc
        row.goals = goals
        self._event(row, JOURNEY_GOAL_ADDED, {"goal": item}, actor)
        if focus:
            self._set_focus(row, item["id"], actor)
        return self._view(row)

    def update_goal(self, workspace_id: str, journey_id: str, goal_id: str, *,
                    actor: str = "system", **changes: Any) -> JourneyView:
        allowed = {"title", "status", "priority", "depends_on"}
        if set(changes) - allowed:
            raise JourneyError("unsupported goal fields")
        row = self._require(workspace_id, journey_id)
        goals = list(row.goals or [])
        index = next((i for i, item in enumerate(goals) if item.get("id") == goal_id), None)
        if index is None:
            raise JourneyError("goal not found")
        changed = {**goals[index], **changes}
        try:
            goals[index] = validate_goal(changed)
            goals = validate_goals(goals)
        except ValueError as exc:
            raise JourneyError(str(exc)) from exc
        row.goals = goals
        if row.current_focus_goal_id == goal_id:
            row.active_goal = goals[index]
        event = JOURNEY_GOAL_COMPLETED if changes.get("status") == "completed" else JOURNEY_GOAL_CHANGED
        self._event(row, event, {"goal_id": goal_id, "changed": sorted(changes)}, actor)
        return self._view(row)

    def set_focus_goal(self, workspace_id: str, journey_id: str, goal_id: str, *, actor: str = "system") -> JourneyView:
        row = self._require(workspace_id, journey_id)
        self._set_focus(row, goal_id, actor)
        return self._view(row)

    def upsert_milestone(self, workspace_id: str, journey_id: str, milestone: dict, *, actor: str = "system") -> JourneyView:
        row = self._require(workspace_id, journey_id)
        try:
            item = validate_milestone(milestone)
        except ValueError as exc:
            raise JourneyError(str(exc)) from exc
        self._require_goal_ref(row, item["goal_id"])
        values = _upsert(list(row.milestones or []), item)
        row.milestones = values
        row.next_milestone = next((v for v in values if v["status"] != "completed"), None)
        self._event(row, JOURNEY_MILESTONE_UPDATED, {"milestone": item}, actor)
        return self._view(row)

    def add_decision(self, workspace_id: str, journey_id: str, decision: dict, *, actor: str = "system") -> JourneyView:
        row = self._require(workspace_id, journey_id)
        try:
            item = validate_decision(decision)
        except ValueError as exc:
            raise JourneyError(str(exc)) from exc
        self._require_goal_ref(row, item["goal_id"])
        decisions = list(row.decisions or [])
        existing = next((v for v in decisions if v.get("id") == item["id"]), None)
        duplicate = next((v for v in decisions
                          if v.get("type") == item["type"] and v.get("value") == item["value"]
                          and v.get("status") == item["status"]), None)
        if duplicate and existing is None:
            return self._view(row)
        if existing and existing.get("status") == "confirmed" and item["status"] != "confirmed":
            raise JourneyError("a confirmed decision must be changed explicitly")
        superseded = []
        if existing is None and item["status"] == "confirmed":
            for index, value in enumerate(decisions):
                if (value.get("goal_id") == item["goal_id"] and value.get("type") == item["type"]
                        and value.get("status") in {"provisional", "confirmed"}):
                    decisions[index] = {**value, "status": "changed"}
                    superseded.append(value["id"])
        row.decisions = _upsert(decisions, item)
        row.unresolved_decisions = [v for v in row.decisions if v.get("status") == "provisional"]
        if superseded:
            self._event(row, JOURNEY_DECISION_CHANGED, {"decision_ids": superseded}, actor)
        self._event(row, JOURNEY_DECISION_CHANGED if existing else JOURNEY_DECISION_ADDED,
                    {"decision": item}, actor)
        return self._view(row)

    def add_blocker(self, workspace_id: str, journey_id: str, blocker: dict, *, actor: str = "system") -> JourneyView:
        row = self._require(workspace_id, journey_id)
        try:
            item = validate_blocker(blocker)
        except ValueError as exc:
            raise JourneyError(str(exc)) from exc
        self._require_goal_ref(row, item["goal_id"])
        row.blockers = _upsert(list(row.blockers or []), item)
        self._event(row, JOURNEY_BLOCKER_ADDED, {"blocker": item}, actor)
        return self._view(row)

    def resolve_blocker(self, workspace_id: str, journey_id: str, blocker_id: str, *, actor: str = "system") -> JourneyView:
        row = self._require(workspace_id, journey_id)
        values = list(row.blockers or [])
        item = next((value for value in values if value.get("id") == blocker_id), None)
        if item is None:
            raise JourneyError("blocker not found")
        item = {**item, "status": "resolved"}
        row.blockers = _upsert(values, item)
        self._event(row, JOURNEY_BLOCKER_RESOLVED, {"blocker_id": blocker_id}, actor)
        return self._view(row)

    def set_primary(self, workspace_id: str, journey_id: str, *, actor: str = "system") -> JourneyView:
        row = self._require(workspace_id, journey_id)
        if row.status != "active":
            raise JourneyError("only an active journey can be primary")
        if row.is_primary:
            return self._view(row)
        self._clear_primary(workspace_id, actor=actor)
        row.is_primary = True
        self._event(row, JOURNEY_PRIMARY_CHANGED, {"primary": True}, actor)
        return self._view(row)

    def history(self, workspace_id: str, journey_id: str) -> list[dict]:
        self._require(workspace_id, journey_id)
        rows = self.db.execute(select(StudentJourneyEvent).where(
            StudentJourneyEvent.workspace_id == workspace_id,
            StudentJourneyEvent.journey_id == journey_id,
        ).order_by(StudentJourneyEvent.created_at)).scalars().all()
        return [{"id": r.id, "event_type": r.event_type, "payload": r.payload,
                 "actor": r.actor, "created_at": r.created_at} for r in rows]

    def _clear_primary(self, workspace_id: str, *, actor: str) -> None:
        for row in self.db.execute(select(StudentJourney).where(
            StudentJourney.workspace_id == workspace_id, StudentJourney.is_primary.is_(True),
        )).scalars():
            row.is_primary = False
            self._event(row, JOURNEY_PRIMARY_CHANGED, {"primary": False}, actor)

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

    def _set_focus(self, row, goal_id: str, actor: str) -> None:
        goal = next((item for item in (row.goals or []) if item.get("id") == goal_id), None)
        if goal is None:
            raise JourneyError("focus goal not found")
        previous = row.current_focus_goal_id
        if previous == goal_id:
            return
        row.current_focus_goal_id = goal_id
        row.active_goal = goal
        self._event(row, JOURNEY_FOCUS_CHANGED, {"from": previous, "to": goal_id}, actor)

    @staticmethod
    def _require_goal_ref(row, goal_id: str) -> None:
        if not any(goal.get("id") == goal_id for goal in (row.goals or [])):
            raise JourneyError("referenced goal not found")

    @staticmethod
    def _view(row) -> JourneyView:
        return JourneyView(
            id=row.id, workspace_id=str(row.workspace_id), journey_type=row.journey_type,
            title=row.title, status=row.status, is_primary=row.is_primary,
            active_goal=row.active_goal, goals=list(row.goals or []),
            current_focus_goal_id=row.current_focus_goal_id,
            current_stage=row.current_stage,
            current_objective=row.current_objective, target_outcome=row.target_outcome,
            target_date=row.target_date, milestones=list(row.milestones or []),
            decisions=list(row.decisions or []), unresolved_decisions=list(row.unresolved_decisions or []),
            blockers=list(row.blockers or []), next_milestone=row.next_milestone,
            next_recommended_action=row.next_recommended_action,
            created_at=row.created_at, updated_at=row.updated_at, completed_at=row.completed_at,
        )


def new_id(prefix: str) -> str:
    return f"{prefix}-{uuid4().hex[:12]}"


def _goal_key(goal: dict) -> tuple:
    return (str(goal.get("parent_goal_id") or ""), " ".join(str(goal.get("title") or "").casefold().split()))


def _upsert(values: list[dict], item: dict) -> list[dict]:
    for index, value in enumerate(values):
        if value.get("id") == item["id"]:
            return [*values[:index], item, *values[index + 1:]]
    return [*values, item]
