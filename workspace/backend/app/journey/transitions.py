"""Normalized journey stages and the legal state-machine transitions."""

from enum import Enum


class JourneyStage(str, Enum):
    ORIENTING = "ORIENTING"
    UNDERSTANDING = "UNDERSTANDING"
    ALIGNING = "ALIGNING"
    PLANNING = "PLANNING"
    ACTING = "ACTING"
    REVIEWING = "REVIEWING"
    COMPLETED = "COMPLETED"


_ORDER = tuple(JourneyStage)


def normalize_stage(value: str | JourneyStage | None) -> str | None:
    if value is None:
        return None
    try:
        return JourneyStage(str(getattr(value, "value", value)).strip().upper()).value
    except ValueError as exc:
        raise ValueError(f"invalid journey stage: {value}") from exc


def can_transition(current: str | None, target: str) -> bool:
    """Allow forward progress, review loops, and explicit completion.

    Backward jumps would make the event history misleading. REVIEWING is a
    loop point and can return to any active phase after reviewing a plan.
    """
    normalized_target = normalize_stage(target)
    if normalized_target is None:
        raise ValueError("target journey stage is required")
    target = normalized_target
    current = normalize_stage(current)
    if current is None or current == target:
        return True
    if current == JourneyStage.COMPLETED.value:
        return False
    if target == JourneyStage.COMPLETED.value:
        return True
    if current == JourneyStage.REVIEWING.value:
        return target != JourneyStage.ORIENTING.value
    current_index = _ORDER.index(JourneyStage(current))
    target_index = _ORDER.index(JourneyStage(target))
    return target_index >= current_index or target == JourneyStage.REVIEWING.value


def require_transition(current: str | None, target: str) -> str:
    normalized = normalize_stage(target)
    if normalized is None:
        raise ValueError("target journey stage is required")
    if not can_transition(current, normalized):
        raise ValueError(f"invalid journey stage transition: {current} -> {normalized}")
    return normalized
