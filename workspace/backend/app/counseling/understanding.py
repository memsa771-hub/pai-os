"""A bounded, read-only projection of canonical student state."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import date, datetime
from typing import Any

from app.memory.student_snapshot import StudentSnapshot, StudentSnapshotService

EDUCATION_LEVELS = (
    "school", "secondary", "upper_secondary", "diploma", "associate",
    "bachelor", "professional", "master", "mphil", "doctorate", "other", "unknown",
)
_PREDECESSOR = {
    "secondary": "school", "upper_secondary": "secondary", "associate": "upper_secondary",
    "diploma": "upper_secondary", "bachelor": "upper_secondary",
    "professional": "upper_secondary", "master": "bachelor", "mphil": "master",
    "doctorate": "master",
}
_KINDS = {
    "education": "education", "experience": "work_experience", "projects": "project",
    "skills": "skill", "tests": "test_attempt", "goals": "goal",
}


def _plain(value: Any) -> Any:
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, str):
        return value[:400]
    if isinstance(value, dict):
        return {str(k): _plain(v) for k, v in list(value.items())[:15]}
    if isinstance(value, (list, tuple)):
        return [_plain(v) for v in value[:12]]
    return value


def _provenance(row: dict) -> dict:
    source = row.get("source_type") or "unknown"
    verification = row.get("verification_status")
    if not verification:
        verification = "document_supported" if source == "document" else "self_reported" if source in {"user_explicit", "conversation"} else "unknown"
    evidence = row.get("evidence") or {}
    return {"source": source, "verification": verification,
            **({"file_id": evidence["file_id"]} if evidence.get("file_id") else {})}


def _node(row: dict) -> dict:
    omitted = {"source_type", "claim_origin", "capture_method", "evidence",
               "created_at", "updated_at", "verification_status", "subject_user_id"}
    return {**{key: _plain(value) for key, value in row.items()
               if key not in omitted and value is not None}, "provenance": _provenance(row)}


class StudentUnderstandingBuilder:
    def __init__(self, db=None):
        self.db = db

    def build(self, workspace_id: str, *, snapshot: StudentSnapshot | None = None,
              baseline: dict | None = None) -> dict:
        snapshot = snapshot or StudentSnapshotService(self.db).build(workspace_id)
        facts = snapshot.facts
        records = snapshot.records
        view: dict[str, Any] = {}
        for domain in ("identity", "preferences", "constraints", "finance"):
            view[domain] = {key.partition(".")[2]: {"value": _plain(row["value"]),
                            "provenance": _provenance(row)}
                            for key, row in facts.items() if key.startswith(domain + ".")}
        for section, kind in _KINDS.items():
            view[section] = {"nodes": [_node(row) for row in records.get(kind, [])[:12]]}
        view["documents"] = {"nodes": [_node(row) for row in records.get("document", [])[:8]]}
        if self.db is not None:
            from app.memory.semantic import MemoryService
            view["memories"] = [{"content": _plain(row.content), "type": row.memory_type,
                                  "provenance": {"source": row.source_type or "unknown"}}
                                for row in MemoryService(self.db).list_memories(workspace_id, limit=6)]
        else:
            view["memories"] = []
        view["tests"]["nodes"] += [_node(row) for row in records.get("language_proficiency", [])[:6]]
        view["skills"]["nodes"] += [_node(row) for row in records.get("certification", [])[:6]]
        view["goals"]["nodes"] += [
            {"key": key, "value": _plain(row["value"]), "provenance": _provenance(row)}
            for key, row in facts.items() if key.startswith("goal.") or key.startswith("goals.")
        ]
        levels = {n.get("canonical_level") for n in view["education"]["nodes"]}
        gaps = []
        for level in levels:
            predecessor = _PREDECESSOR.get(level)
            if predecessor and predecessor not in levels:
                gaps.append({"level": predecessor, "status": "UNKNOWN",
                             "reason": f"predecessor of {level} is not recorded"})
        view["education"]["gaps"] = sorted(gaps, key=lambda g: EDUCATION_LEVELS.index(g["level"]))
        # Relationships are references to canonical records, never extra facts.
        for skill in view["skills"]["nodes"]:
            name = str(skill.get("name") or "").casefold()
            if not name:
                continue
            skill["supported_by"] = [
                {"type": kind, "id": row["id"]}
                for kind, section in (("project", "projects"), ("work_experience", "experience"))
                for row in view[section]["nodes"]
                if name in [str(s).casefold() for s in (row.get("details") or {}).get("skills", [])]
            ]
        view["open_conflicts"] = [_plain(issue) for issue in snapshot.issues[:8]]
        view["open_gaps"] = self.gaps(view)
        early_student = bool(view["education"]["nodes"]) and all(
            node.get("canonical_level") in {"school", "secondary", "upper_secondary"}
            for node in view["education"]["nodes"]
        )
        view["domain_status"] = {
            domain: ("CONFLICTING" if view["open_conflicts"] and domain == "education" else
                     "KNOWN" if view[domain]["nodes"] else
                     "NOT_APPLICABLE" if early_student and domain in {"experience", "projects"} else
                     "UNKNOWN")
            for domain in ("education", "experience", "projects", "skills", "tests", "goals")
        }
        view["domain_status"].update({
            domain: "KNOWN" if view[domain] else "UNKNOWN"
            for domain in ("identity", "preferences", "constraints", "finance")
        })
        view["domain_status"]["motivation"] = (
            "KNOWN" if any((node.get("details") or {}).get("motivation")
                           for node in view["goals"]["nodes"]) else "UNKNOWN"
        )
        view["baseline"] = {"status": (baseline or {}).get("status", "discovering"),
                            "version": (baseline or {}).get("version", 0),
                            "confirmed_at": (baseline or {}).get("confirmed_at")}
        return view

    @staticmethod
    def gaps(view: dict) -> list[dict]:
        gaps = []
        if not view["education"]["nodes"]:
            gaps.append({"domain": "education", "status": "UNKNOWN", "focus": "current_level"})
        if not view["goals"]["nodes"]:
            gaps.append({"domain": "goals", "status": "UNKNOWN", "focus": "current_direction"})
        if not any((n.get("details") or {}).get("motivation") for n in view["goals"]["nodes"]):
            gaps.append({"domain": "goals", "status": "UNKNOWN", "focus": "motivation"})
        if view["education"]["nodes"] and not any(n.get("result") for n in view["education"]["nodes"]):
            gaps.append({"domain": "education", "status": "UNKNOWN", "focus": "academic_performance"})
        abroad = any(
            str(node.get("goal_type") or "").casefold() in {"study_abroad", "masters_abroad"}
            or bool((node.get("details") or {}).get("target_countries"))
            for node in view["goals"]["nodes"]
        )
        if abroad and not view["finance"].get("budget"):
            gaps.append({"domain": "finance", "status": "UNKNOWN", "focus": "budget"})
        gaps.extend(view["education"]["gaps"])
        return gaps

    @staticmethod
    def domain_hashes(view: dict) -> dict[str, str]:
        return {domain: hashlib.sha256(json.dumps(view[domain], sort_keys=True,
                                      default=str).encode()).hexdigest()
                for domain in ("identity", "education", "experience", "projects", "skills",
                               "tests", "goals", "preferences", "constraints", "finance")}


def baseline_sufficient(view: dict) -> bool:
    if view["open_conflicts"] or not view["education"]["nodes"] or not view["goals"]["nodes"]:
        return False
    critical = {"current_level", "current_direction", "motivation", "academic_performance", "budget"}
    return not any(gap.get("focus") in critical for gap in view["open_gaps"])


def student_mirror(view: dict, affected_domains: list[str] | None = None) -> str:
    affected = set(affected_domains or ())
    show_all = not affected
    sections = []
    education = []
    for node in view["education"]["nodes"][:5]:
        parts = [str(node.get("qualification_name") or node.get("canonical_level") or "qualification")]
        if node.get("institution_name"):
            parts.append(str(node["institution_name"]))
        result = node.get("result") or {}
        if result.get("gpa") is not None:
            parts.append(f"GPA {result['gpa']}" +
                         (f"/{result['gpa_scale']}" if result.get("gpa_scale") else ""))
        education.append(", ".join(parts) +
                         f" ({node['provenance']['verification']})")
    if education and (show_all or "education" in affected):
        sections.append("Education: " + "; ".join(education))
    for heading, key, label in (("Experience", "experience", "role"),
                                ("Projects", "projects", "name"),
                                ("Skills", "skills", "name"),
                                ("Current direction", "goals", "title")):
        values = [str(n.get(label) or n.get("value")) for n in view[key]["nodes"] if n.get(label) or n.get("value")]
        if values and (show_all or key in affected):
            sections.append(heading + ": " + "; ".join(values[:5]))
    motivations = [(n.get("details") or {}).get("motivation") for n in view["goals"]["nodes"]]
    if any(motivations) and (show_all or "goals" in affected):
        sections.append("Motivation: " + "; ".join(str(item) for item in motivations if item))
    for heading, key in (("Preferences", "preferences"), ("Constraints", "constraints"),
                         ("Finance", "finance")):
        if view[key] and (show_all or key in affected):
            sections.append(heading + ": " + "; ".join(
                f"{name}: {item['value']} ({item['provenance']['verification']})"
                for name, item in list(view[key].items())[:5]))
    if view["open_conflicts"] and show_all:
        sections.append("Needs clarification: " + "; ".join(
            issue.get("question") or issue.get("summary") or issue.get("type", "conflict")
            for issue in view["open_conflicts"][:3]))
    if view["open_gaps"] and show_all:
        sections.append("Still to confirm: " + "; ".join(
            g.get("focus") or g.get("level") or g.get("reason", "unknown") for g in view["open_gaps"][:5]))
    intro = ("Please confirm the updated " + ", ".join(sorted(affected)) + " understanding:\n"
             if affected else "This is how I currently understand you:\n")
    return intro + "\n".join(sections) + "\nIs this accurate, or is anything important wrong or missing?"


def same_turn_education_conflict(view: dict, message: str) -> dict | None:
    """Catch a narrow, explicit current-level contradiction before proposing it."""
    text = message.casefold()
    if any(marker in text for marker in ("correction", "actually", "i changed", "i finished", "since then")):
        return None
    mentioned = None
    for level, pattern in (("doctorate", r"\b(?:phd|doctorate)\b"),
                           ("master", r"\b(?:master'?s|msc|ms degree)\b"),
                           ("bachelor", r"\b(?:bachelor'?s|bs degree|undergraduate)\b")):
        if re.search(pattern, text):
            mentioned = level
            break
    if not mentioned or not re.search(r"\b(?:i am currently|i'm currently|i already completed)\b", text):
        return None
    current = [n for n in view["education"]["nodes"] if n.get("academic_status") == "current"]
    if not current or any(n.get("canonical_level") == mentioned for n in current):
        return None
    known = current[0].get("qualification_name") or current[0].get("canonical_level")
    return {"summary": f"Current education on record is {known}; new claim says {mentioned}.",
            "clarification_question": f"I have you currently studying {known}. Has that changed, or are you describing another qualification?"}
