"""Parse Counselor prose and its internal structured state."""

import json


def parse_turn(raw: str) -> tuple[str, dict]:
    text = (raw or "").strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
    try:
        parsed = json.loads(text)
    except (TypeError, ValueError):
        return raw, {}
    if not isinstance(parsed, dict) or not isinstance(parsed.get("response"), str):
        return raw, {}
    state = parsed.get("counselor_state")
    if not isinstance(state, dict):
        state = {}
    delta = state.get("student_understanding_delta")
    if not isinstance(delta, dict):
        delta = {}
    state["student_understanding_delta"] = {
        key: value if isinstance(value := delta.get(key), list) else []
        for key in ("facts", "records", "memories", "conflicts", "unknowns")
    }
    if not isinstance(state.get("next_move"), dict):
        state["next_move"] = {}
    return parsed["response"], state
