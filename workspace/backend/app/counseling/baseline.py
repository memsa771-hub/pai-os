"""Confirmation metadata; canonical profile data remains in Vault and records."""

from __future__ import annotations

from datetime import datetime, timezone

from .understanding import StudentUnderstandingBuilder

_KEY = "student_understanding_baseline"
_CONFIRMATIONS = {"yes", "yes that's accurate", "yes, that's accurate", "accurate",
                  "confirmed", "i confirm", "that's correct", "that is correct",
                  "looks right", "correct"}


def metadata(workspace) -> dict:
    return dict((getattr(workspace, "settings", None) or {}).get(_KEY) or {})


def confirmed(message: str) -> bool:
    return message.strip().casefold().rstrip(".! ") in _CONFIRMATIONS


def changed_domains(view: dict, baseline: dict) -> list[str]:
    if baseline.get("status") != "confirmed":
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
        data["affected_domains"] = affected_domains or previous.get("affected_domains") or []
    if status == "confirmed":
        data["version"] += 1
        data["confirmed_at"] = datetime.now(timezone.utc).isoformat()
    else:
        data["confirmed_at"] = previous.get("confirmed_at")
    settings = dict(workspace.settings or {})
    settings[_KEY] = data
    workspace.settings = settings
    return data
