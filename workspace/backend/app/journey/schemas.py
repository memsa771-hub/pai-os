"""Validation for JSON values persisted by Student Journey."""

from __future__ import annotations

from datetime import datetime
from typing import Any


GOAL_TYPES = frozenset({"primary", "subgoal"})
GOAL_STATUSES = frozenset({"proposed", "active", "completed", "paused", "abandoned"})
PRIORITIES = frozenset({"high", "medium", "low"})
MILESTONE_STATUSES = frozenset({"pending", "active", "completed", "blocked"})
DECISION_STATUSES = frozenset({"provisional", "confirmed", "changed"})
BLOCKER_TYPES = frozenset({"missing_information", "document", "eligibility", "financial", "decision"})
BLOCKER_STATUSES = frozenset({"open", "resolved"})


def _object(value: Any, name: str) -> dict:
    if not isinstance(value, dict):
        raise ValueError(f"{name} must be an object")
    return dict(value)


def _required_text(item: dict, keys: tuple[str, ...], name: str) -> None:
    for key in keys:
        if not isinstance(item.get(key), str) or not item[key].strip():
            raise ValueError(f"{name}.{key} is required")


def validate_goal(value: Any) -> dict:
    item = _object(value, "goal")
    _required_text(item, ("id", "type", "title", "status", "priority"), "goal")
    if item["type"] not in GOAL_TYPES or item["status"] not in GOAL_STATUSES or item["priority"] not in PRIORITIES:
        raise ValueError("goal contains an invalid type, status, or priority")
    parent = item.get("parent_goal_id")
    if parent is not None and (not isinstance(parent, str) or not parent.strip()):
        raise ValueError("goal.parent_goal_id must be null or a non-empty string")
    if item["type"] == "primary" and parent is not None:
        raise ValueError("a primary goal cannot have a parent")
    if item["type"] == "subgoal" and not parent:
        raise ValueError("a subgoal requires parent_goal_id")
    item.setdefault("parent_goal_id", None)
    dependencies = item.get("depends_on", [])
    if not isinstance(dependencies, list) or any(not isinstance(v, str) or not v for v in dependencies):
        raise ValueError("goal.depends_on must be a list of goal ids")
    item["depends_on"] = list(dict.fromkeys(dependencies))
    return item


def validate_goals(values: Any) -> list[dict]:
    if not isinstance(values, list):
        raise ValueError("goals must be a list")
    goals = [validate_goal(value) for value in values]
    ids = [item["id"] for item in goals]
    if len(ids) != len(set(ids)):
        raise ValueError("goal ids must be unique")
    known = set(ids)
    if sum(item["type"] == "primary" for item in goals) != 1:
        raise ValueError("a journey requires exactly one primary goal")
    for item in goals:
        refs = set(item["depends_on"])
        if item["parent_goal_id"] is not None:
            refs.add(item["parent_goal_id"])
        if item["id"] in refs or not refs.issubset(known):
            raise ValueError("goal references must identify other goals in the journey")
    return goals


def validate_milestone(value: Any) -> dict:
    item = _object(value, "milestone")
    _required_text(item, ("id", "goal_id", "title", "status"), "milestone")
    if item["status"] not in MILESTONE_STATUSES:
        raise ValueError("invalid milestone status")
    completed = item.get("completed_at")
    if completed is not None and not isinstance(completed, (str, datetime)):
        raise ValueError("milestone.completed_at must be null or a timestamp")
    item.setdefault("completed_at", None)
    return item


def validate_decision(value: Any) -> dict:
    item = _object(value, "decision")
    _required_text(item, ("id", "goal_id", "type", "status", "source"), "decision")
    if "value" not in item:
        raise ValueError("decision.value is required")
    if item["status"] not in DECISION_STATUSES:
        raise ValueError("invalid decision status")
    return item


def validate_blocker(value: Any) -> dict:
    item = _object(value, "blocker")
    _required_text(item, ("id", "goal_id", "type", "description", "status"), "blocker")
    if item["type"] not in BLOCKER_TYPES or item["status"] not in BLOCKER_STATUSES:
        raise ValueError("invalid blocker type or status")
    return item
