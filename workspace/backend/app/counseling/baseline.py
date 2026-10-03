"""Confirmation metadata; canonical profile data remains in Vault and records."""

from __future__ import annotations

from datetime import datetime, timezone

from .understanding import StudentUnderstandingBuilder

_KEY = "student_understanding_baseline"


def metadata(workspace) -> dict:
    return dict((getattr(workspace, "settings", None) or {}).get(_KEY) or {})


def confirmed(turn_semantics: dict | None) -> bool:
    return isinstance(turn_semantics, dict) and turn_semantics.get("mirror_confirmation") is True


def changed_domains(view: dict, baseline: dict) -> list[str]:
    if baseline.get("status") not in {"confirmed", "mirror_review"}:
        return []
    previous = baseline.get("domain_hashes") or {}
    current = StudentUnderstandingBuilder.domain_hashes(view)
    return [domain for domain, digest in current.items() if previous.get(domain) != digest]


def save(workspace, *, status: str, view: dict,
         affected_domains: list[str] | None = None) -> dict:
    previous = metadata(workspace)
    data = {"status": status, "version": int(previous.get("version") or 0),
            "domain_hashes": StudentUnderstandingBuilder.domain_hashes(view)}
    if status != "confirmed":
        data["affected_domains"] = (affected_domains if affected_domains is not None
                                    else previous.get("affected_domains") or [])
    if status == "confirmed":
        data["version"] += 1
        data["confirmed_at"] = datetime.now(timezone.utc).isoformat()
    else:
        data["confirmed_at"] = previous.get("confirmed_at")
    settings = dict(workspace.settings or {})
    settings[_KEY] = data
    workspace.settings = settings
    return data


def invalidate(workspace, *, affected_domains: list[str] | None = None) -> dict:
    """Revoke approval without claiming an unseen revision has been displayed."""
    data = metadata(workspace)
    data["status"] = "mirror_review"
    data["affected_domains"] = (affected_domains if affected_domains is not None
                                else data.get("affected_domains") or [])
    data.pop("mirror_event_id", None)
    settings = dict(workspace.settings or {})
    settings[_KEY] = data
    workspace.settings = settings
    return data


def record_mirror(workspace, *, view: dict, event_id: str, channel: str,
                  affected_domains: list[str] | None = None) -> dict:
    """Only call after the exact safe mirror has been successfully posted."""
    data = save(workspace, status="mirror_review", view=view,
                affected_domains=affected_domains)
    data.update(mirror_event_id=str(event_id), channel=channel,
                owner_user_id=str(workspace.owner_user_id))
    settings = dict(workspace.settings or {})
    settings[_KEY] = data
    workspace.settings = settings
    return data


def can_confirm(db, workspace, baseline: dict, view: dict, source_event,
                *, turn_semantics: dict | None = None) -> bool:
    """Approval belongs to the owner and the latest displayed revision/thread."""
    from sqlalchemy import select
    from app.models import EventRecord
    from app.services.pai import PAI_AGENT_NAME

    if (source_event is None or baseline.get("status") != "mirror_review"
            or source_event.network_id != workspace.id
            or source_event.source != f"human:{workspace.owner_user_id}"
            or not confirmed(turn_semantics)
            or (source_event.payload or {}).get("attachments")
            or view.get("open_conflicts")
            or baseline.get("owner_user_id") != str(workspace.owner_user_id)
            or baseline.get("channel") != source_event.target
            or baseline.get("domain_hashes") != StudentUnderstandingBuilder.domain_hashes(view)
            or not baseline.get("mirror_event_id")):
        return False
    mirror = db.get(EventRecord, baseline["mirror_event_id"])
    if (mirror is None or mirror.network_id != workspace.id
            or mirror.target != source_event.target
            or mirror.source != f"openagents:{PAI_AGENT_NAME}"
            or mirror.timestamp >= source_event.timestamp):
        return False
    # A yes after a different question must not approve an older profile mirror.
    intervening = db.execute(select(EventRecord.id).where(
        EventRecord.network_id == workspace.id,
        EventRecord.target == source_event.target,
        EventRecord.source.in_((f"openagents:{PAI_AGENT_NAME}", f"human:{workspace.owner_user_id}")),
        EventRecord.timestamp > mirror.timestamp,
        EventRecord.timestamp < source_event.timestamp,
    ).limit(1)).first()
    return intervening is None
