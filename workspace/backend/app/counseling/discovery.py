"""Bounded discovery metadata, separate from canonical student facts."""

DISCOVERY_KEY = "student_discovery"
DISCOVERY_FOCI = frozenset({
    "current_level", "current_direction", "motivation", "academic_performance",
    "budget", "interests", "strengths", "practical_constraints", "education_history",
})
DISCOVERY_STATUSES = frozenset({"UNKNOWN", "DECLINED", "DEFERRED", "NOT_APPLICABLE"})
SUPPRESSED_STATUSES = frozenset({"DECLINED", "DEFERRED", "NOT_APPLICABLE"})


def discovery_metadata(workspace) -> dict:
    raw = (getattr(workspace, "settings", None) or {}).get(DISCOVERY_KEY) or {}
    if not isinstance(raw, dict):
        return {}
    return {focus: {"status": item["status"], "source_event_id": item["source_event_id"]}
            for focus, item in raw.items()
            if focus in DISCOVERY_FOCI and isinstance(item, dict)
            and item.get("status") in DISCOVERY_STATUSES and item.get("source_event_id")}


def is_general_information_request(message: str) -> bool:
    """Narrow general explanations should not be blocked by profile review."""
    import re
    text = message.strip().casefold()
    return bool(re.match(r"(?:what (?:is|are|does)|explain|define|how does|meaning of)\b", text)) and not bool(
        re.search(r"\b(?:my|me|fit|best|should i|eligible|qualify)\b", text)
    )
