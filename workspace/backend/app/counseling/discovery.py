"""Bounded discovery metadata, separate from canonical student facts."""

DISCOVERY_KEY = "student_discovery"
DISCOVERY_FOCI = frozenset({
    "current_level", "current_direction", "motivation", "academic_performance",
    "budget", "interests", "strengths", "practical_constraints", "education_history",
    "work_history", "target_location", "target_timing",
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
