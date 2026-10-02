"""Deterministic contract for a discovery journey; execution remains in PAI OS."""

from __future__ import annotations

import re

from app.memory.student_records import StudentRecordService
from .service import JourneyError, JourneyService, new_id


def _domain(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", value.casefold()).strip("_")


class DirectionDiscoveryService:
    def __init__(self, db):
        self.db = db
        self.journeys = JourneyService(db)

    def start(self, workspace_id: str, directions: list[str] | None = None, *,
              actor: str = "student"):
        """Open a journey after the student chooses exploration, without choosing for them."""
        if directions is None:
            directions = []
        if not isinstance(directions, list) or len(directions) > 6 or any(
                not isinstance(item, str) or not item.strip() or len(item) > 80
                for item in directions):
            raise JourneyError("directions must be a short list of student-chosen fields")
        chosen = list({_domain(item): item.strip() for item in directions}.items())
        if any(not key for key, _ in chosen):
            raise JourneyError("direction names must contain letters or numbers")
        existing = next((j for j in self.journeys.list_active(workspace_id)
                         if j.journey_type == "direction_discovery"), None)
        if existing:
            journey = existing
        else:
            journey = self.journeys.create(
                workspace_id, "direction_discovery", "Discover a study direction",
                primary=not bool(self.journeys.list_active(workspace_id)), actor=actor,
                current_stage="UNDERSTANDING",
                current_objective="Choose fields to explore through small experiences",
                target_outcome="Choose an evidence-backed direction",
                next_recommended_action={"type": "choose_exploration", "status": "pending"},
            )
        primary = next(goal for goal in journey.goals if goal["type"] == "primary")
        for key, name in chosen:
            goal = next((item for item in journey.goals if item.get("domain") == key), None)
            if goal is None:
                goal_id = new_id("goal")
                journey = self.journeys.add_goal(workspace_id, journey.id, {
                    "id": goal_id, "parent_goal_id": primary["id"], "type": "subgoal",
                    "title": f"Explore {name.strip()}", "status": "active",
                    "priority": "medium", "depends_on": [], "domain": key,
                }, actor=actor)
            else:
                goal_id = goal["id"]
            for step, title in (("activity", "Try a small exploration activity"),
                                ("reflection", "Reflect on that experience")):
                milestone_id = f"{goal_id}:{step}"
                if not any(item["id"] == milestone_id for item in journey.milestones):
                    journey = self.journeys.upsert_milestone(workspace_id, journey.id, {
                        "id": milestone_id, "goal_id": goal_id, "title": title,
                        "status": "pending", "step": step,
                    }, actor=actor)
        if chosen:
            journey = self.journeys.update(workspace_id, journey.id, actor=actor,
                current_objective="Complete and reflect on small exploration activities",
                next_recommended_action={"type": "arrange_exploration", "status": "pending",
                                         "domain": chosen[0][0]})
        return journey

    def observe_exploration(self, workspace_id: str, experience_id: str, *, actor: str = "system"):
        """Advance only from accepted canonical completion and student reflection."""
        record = StudentRecordService(self.db).get(workspace_id, "exploration_experience", experience_id)
        if record is None:
            raise JourneyError("exploration experience not found")
        if record.activity_status != "completed":
            return []
        changed = []
        for journey in self.journeys.list_active(workspace_id):
            if journey.journey_type != "direction_discovery":
                continue
            goal = next((item for item in journey.goals
                         if item.get("domain") == _domain(record.domain)), None)
            if goal is None:
                continue
            changed_for_journey = False
            for step, allowed in (("activity", True),
                                  ("reflection", bool(record.student_reflection))):
                milestone = next((item for item in journey.milestones
                                  if item.get("id") == f"{goal['id']}:{step}"), None)
                if allowed and milestone and milestone["status"] != "completed":
                    journey = self.journeys.upsert_milestone(workspace_id, journey.id,
                        {**milestone, "status": "completed", "experience_id": record.id}, actor=actor)
                    changed.append(milestone["id"])
                    changed_for_journey = True
            if changed_for_journey:
                own_steps = [item for item in journey.milestones if item.get("goal_id") == goal["id"]]
                if (own_steps and all(item["status"] == "completed" for item in own_steps)
                        and goal["status"] != "completed"):
                    journey = self.journeys.update_goal(workspace_id, journey.id, goal["id"],
                        status="completed", actor=actor)
                pending = next((item for item in journey.milestones
                                if item["status"] != "completed"), None)
                if pending is None and journey.current_stage != "REVIEWING":
                    journey = self.journeys.set_stage(workspace_id, journey.id, "REVIEWING", actor=actor)
                action = ({"type": "arrange_exploration", "status": "pending",
                           "goal_id": pending["goal_id"], "step": pending.get("step")}
                          if pending else {"type": "compare_directions", "status": "pending"})
                self.journeys.update(workspace_id, journey.id, actor=actor,
                    next_recommended_action=action,
                    current_objective=("Compare experiences with the student" if not pending
                                       else "Continue exploration and reflection"))
        return changed
