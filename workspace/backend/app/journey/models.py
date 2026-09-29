"""Public journey value objects. ORM storage remains private to the service."""

from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any


@dataclass(frozen=True)
class JourneyView:
    id: str
    workspace_id: str
    journey_type: str
    title: str
    status: str
    is_primary: bool
    active_goal: Any = None
    goals: list[dict] = field(default_factory=list)
    current_focus_goal_id: str | None = None
    current_stage: str | None = None
    current_objective: str | None = None
    target_outcome: Any = None
    target_date: datetime | None = None
    milestones: list[dict] = field(default_factory=list)
    decisions: list[dict] = field(default_factory=list)
    unresolved_decisions: list[dict] = field(default_factory=list)
    blockers: list[dict] = field(default_factory=list)
    next_milestone: Any = None
    next_recommended_action: Any = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    completed_at: datetime | None = None

    def to_dict(self) -> dict:
        return asdict(self)
