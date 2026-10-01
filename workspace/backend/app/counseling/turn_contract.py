"""Parse Counselor prose and its internal structured state."""

import json

_RETRY_RESPONSE = "I couldn't finish that reply. Please retry your last message so I can pick up from here."
_DELTA_BUCKETS = ("facts", "records", "memories", "conflicts", "unknowns")


def parse_turn(raw: str) -> tuple[str, dict]:
    text = (raw or "").strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
    try:
        parsed = json.loads(text)
    except (TypeError, ValueError):
        # A truncated internal envelope must never be displayed as conversation.
        # Plain prose remains supported for older models and harmless replies.
        if text.startswith(("{", "[")) and any(
                f'"{key}"' in text for key in ("response", "counselor_state", "student_understanding_delta")):
            return _RETRY_RESPONSE, {}
        return raw, {}
    if not isinstance(parsed, dict) or not isinstance(parsed.get("response"), str):
        return _RETRY_RESPONSE, {}
    state = parsed.get("counselor_state")
    if not isinstance(state, dict):
        state = {}
    delta = state.get("student_understanding_delta")
    if not isinstance(delta, dict):
        delta = {}
    state["student_understanding_delta"] = {
        key: [item for item in value[:20] if isinstance(item, dict)]
        if isinstance(value := delta.get(key), list) else []
        for key in _DELTA_BUCKETS
    }
    if not isinstance(state.get("next_move"), dict):
        state["next_move"] = {}
    # Persistence still validates every proposal against the durable owner event;
    # successful parsing conveys no authority to change canonical state.
    return parsed["response"].strip() or _RETRY_RESPONSE, state
