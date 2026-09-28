from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class JourneyState:
    journey_id: str
    active_goal: Any
    current_stage: str | None
    current_objective: str | None
    blockers: tuple[Any, ...]
    next_milestone: Any
    next_recommended_action: Any
