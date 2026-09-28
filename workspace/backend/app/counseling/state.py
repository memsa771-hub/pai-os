from dataclasses import dataclass
from enum import Enum


class CounselingPhase(str, Enum):
    ORIENTING = "ORIENTING"
    UNDERSTANDING = "UNDERSTANDING"
    ALIGNING = "ALIGNING"
    PLANNING = "PLANNING"
    ACTING = "ACTING"
    REVIEWING = "REVIEWING"


class CounselingMove(str, Enum):
    ASK = "ASK"
    CLARIFY = "CLARIFY"
    REFLECT = "REFLECT"
    ADVISE = "ADVISE"
    ALIGN = "ALIGN"
    BUILD_ROADMAP = "BUILD_ROADMAP"
    DELEGATE = "DELEGATE"
    REVIEW = "REVIEW"


@dataclass(frozen=True)
class CounselingState:
    phase: CounselingPhase
    discovery_intensity: str
    next_move: CounselingMove
    focus: str | None
    decision_ready: bool
    roadmap_ready: bool
    personalization_level: str
    relevant_unknowns: tuple[str, ...]
    active_conflict: dict | None
    current_objective: str | None
    question_limit: int = 1
