"""Persist explicit discovery statuses from structured semantic classification."""

from app.models import EventRecord, Workspace
from app.counseling.discovery import DISCOVERY_KEY, DISCOVERY_FOCI, DISCOVERY_STATUSES
from app.memory.voice_attribution import contained


def consume_discovery_statuses(db, workspace_id, turn, items,
                               *, semantic_statuses: list[dict] | None = None):
    if not isinstance(items, list) or not isinstance(semantic_statuses, list):
        return
    workspace = db.get(Workspace, workspace_id)
    source_event = db.get(EventRecord, turn.user_event_id)
    if (workspace is None or source_event is None or source_event.network_id != workspace_id
            or source_event.source != f"human:{workspace.owner_user_id}"):
        return
    source_text = (source_event.payload or {}).get("content")
    if not isinstance(source_text, str):
        return
    approved = {(item.get("focus"), item.get("status"), item.get("quote"))
                for item in semantic_statuses[:20] if isinstance(item, dict)
                and isinstance(item.get("focus"), str) and item.get("focus") in DISCOVERY_FOCI
                and isinstance(item.get("status"), str) and item.get("status") in DISCOVERY_STATUSES
                and contained(item.get("quote"), source_text)}
    settings = dict(workspace.settings or {})
    current = settings.get(DISCOVERY_KEY)
    states = dict(current) if isinstance(current, dict) else {}
    for item in items[:20]:
        if not isinstance(item, dict):
            continue
        focus, status = item.get("focus"), item.get("status")
        evidence = item.get("evidence")
        quote = evidence.get("quote") if isinstance(evidence, dict) else None
        if (not isinstance(focus, str) or not isinstance(status, str)
                or not isinstance(quote, str) or (focus, status, quote) not in approved
                or not contained(quote, source_text)):
            continue
        states[focus] = {"status": status, "source_event_id": turn.user_event_id}
    if states != current and states:
        settings[DISCOVERY_KEY] = states
        workspace.settings = settings
