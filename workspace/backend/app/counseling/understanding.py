"""A bounded, read-only projection of canonical student state."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import date, datetime
from typing import Any

from app.memory.student_snapshot import StudentSnapshot, StudentSnapshotService
from app.memory.field_definitions import VaultFieldDefinitionService, usable_in_counseling
from .discovery import discovery_metadata, SUPPRESSED_STATUSES
from app.memory.student_schema import RECORD_SPECS

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
    "research": "research", "achievements": "achievement",
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


def _node(row: dict, kind: str) -> dict:
    properties = RECORD_SPECS[kind]["properties"]
    value = {key: _plain(item) for key, item in row.items()
             if key in properties and item is not None}
    if "details" in value:
        allowed = properties.get("details", {}).get("properties", {})
        value["details"] = {key: item for key, item in value["details"].items()
                            if key in allowed and not key.endswith("url")}
    return {**value, "id": row.get("id"), "provenance": _provenance(row)}


class StudentUnderstandingBuilder:
    def __init__(self, db=None):
        self.db = db

    def build(self, workspace_id: str, *, snapshot: StudentSnapshot | None = None,
              baseline: dict | None = None, discovery: dict | None = None,
              field_definitions: dict | None = None) -> dict:
        snapshot = snapshot or StudentSnapshotService(self.db).build(workspace_id)
        # Fail closed for unknown field definitions. Offline callers may inject
        # the same registry definitions explicitly; raw snapshots are not safe.
        definitions = field_definitions or {}
        if self.db is not None:
            definitions = {d.key: d for d in VaultFieldDefinitionService(self.db).list_definitions()}
            if discovery is None:
                from app.models import Workspace
                discovery = discovery_metadata(self.db.get(Workspace, workspace_id))
        facts = {key: row for key, row in snapshot.facts.items()
                 if usable_in_counseling(definitions.get(key))}
        records = snapshot.records
        view: dict[str, Any] = {}
        for domain in ("identity", "preferences", "constraints", "finance", "career", "location", "mobility"):
            view[domain] = {key.partition(".")[2]: {"value": _plain(row["value"]),
                            "provenance": _provenance(row)}
                            for key, row in facts.items() if key.startswith(domain + ".")}
        for section, kind in _KINDS.items():
            view[section] = {"nodes": [_node(row, kind) for row in records.get(kind, [])[:12]]}
        view["coverage"] = {
            section: {"shown": min(len(records.get(kind, [])), 12),
                      "total": len(records.get(kind, [])),
                      "truncated": len(records.get(kind, [])) > 12}
            for section, kind in _KINDS.items()
        }
        view["documents"] = {"nodes": [_node(row, "document") for row in records.get("document", [])[:8]]}
        # Tentative interpretation belongs in the current reply, never in the
        # canonical mirror merely because it was retained as semantic memory.
        view["memories"] = []
        view["tests"]["nodes"] += [_node(row, "language_proficiency") for row in records.get("language_proficiency", [])[:6]]
        view["skills"]["nodes"] += [_node(row, "certification") for row in records.get("certification", [])[:6]]
        for section, kind in (("tests", "language_proficiency"), ("skills", "certification")):
            view["coverage"][section]["shown"] += min(len(records.get(kind, [])), 6)
            view["coverage"][section]["total"] += len(records.get(kind, []))
            view["coverage"][section]["truncated"] |= len(records.get(kind, [])) > 6
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
            skill["evidence_examples"] = [
                row.get("name") or row.get("role") or "related example"
                for section in ("projects", "experience") for row in view[section]["nodes"]
                if name in [str(s).casefold() for s in (row.get("details") or {}).get("skills", [])]
            ]
        view["open_conflicts"] = []
        for issue in snapshot.issues[:8]:
            evidence = issue.get("evidence") or {}
            key = evidence.get("field_key") or issue.get("field_key")
            # Restricted values can occur in free-text summaries as well as
            # evidence. Omit the entire issue unless its target is safe.
            if key and key not in facts:
                continue
            kind = evidence.get("record_type") or issue.get("record_type")
            if not key and kind not in set(_KINDS.values()) | {"language_proficiency", "certification"}:
                continue
            view["open_conflicts"].append({
                "id": issue.get("id"), "type": issue.get("type"),
                "field_key": key, "record_type": kind,
                "summary": "A stated detail needs clarification",
                "question": "Which version of this detail should I use?",
            })
        view["open_gaps"] = self.gaps(view)
        # A school student may have projects or work; absence is not evidence
        # of inapplicability or inability.
        discovery = discovery or {}
        for gap in view["open_gaps"]:
            item = discovery.get(gap.get("focus")) or {}
            if item.get("source_event_id") and item.get("status") in {"UNKNOWN", *SUPPRESSED_STATUSES}:
                gap["status"] = item["status"]
                gap["student_stated"] = True
        view["discovery"] = {g["focus"]: {"status": g["status"],
                               "student_stated": g.get("student_stated", False)}
                             for g in view["open_gaps"] if g.get("focus")}
        view["domain_status"] = {
            domain: ("CONFLICTING" if view["open_conflicts"] and domain == "education" else
                     "KNOWN" if view[domain]["nodes"] else
                     "UNKNOWN")
            for domain in ("education", "experience", "projects", "skills", "tests", "goals", "research", "achievements")
        }
        view["domain_status"].update({
            domain: "KNOWN" if view[domain] else "UNKNOWN"
            for domain in ("identity", "preferences", "constraints", "finance", "career", "location", "mobility")
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
        if not view.get("career", {}).get("primary_interest") and not any(
                (node.get("details") or {}).get("field_of_study") or
                (node.get("details") or {}).get("career_direction") for node in view["goals"]["nodes"]):
            gaps.append({"domain": "career", "status": "UNKNOWN", "focus": "interests"})
        if not any(node.get("supported_by") or (node.get("details") or {}).get("demonstrated_by")
                   for node in view["skills"]["nodes"]) and not view["projects"]["nodes"] and not view["experience"]["nodes"] and not view.get("research", {}).get("nodes"):
            gaps.append({"domain": "skills", "status": "UNKNOWN", "focus": "strengths"})
        if not view["constraints"] and not view.get("mobility") and not any(
                (n.get("details") or {}).get("constraints") for n in view["goals"]["nodes"]):
            gaps.append({"domain": "constraints", "status": "UNKNOWN", "focus": "practical_constraints"})
        gaps.extend({**gap, "domain": "education", "focus": "education_history"}
                    for gap in view["education"]["gaps"])
        return gaps

    @staticmethod
    def domain_hashes(view: dict) -> dict[str, str]:
        # Approval fingerprints exactly the meaningful content we display,
        # not hidden identifiers, storage timestamps, or an unseen full record.
        return {domain: hashlib.sha256(_mirror_domain(view, domain).encode()).hexdigest()
                for domain in ("identity", "education", "experience", "projects", "skills",
                               "tests", "goals", "preferences", "constraints", "finance",
                               "career", "location", "mobility", "research", "achievements", "discovery")}



def baseline_sufficient(view: dict) -> bool:
    """Enough for an honest partial mirror, not a completed intake checklist."""
    if view["open_conflicts"]:
        return False
    discovery = view.get("discovery") or {}
    def addressed(focus):
        return bool(discovery.get(focus, {}).get("student_stated"))
    education = bool(view["education"]["nodes"]) or addressed("current_level")
    direction = bool(view["goals"]["nodes"]) or bool(view.get("career", {}).get("primary_interest")) or addressed("current_direction")
    return education and direction


_LABELS = {
    "identity": "About you", "education": "Education so far", "experience": "Experience",
    "projects": "Projects", "skills": "Skills and supporting examples", "tests": "Tests and languages",
    "goals": "Directions you are considering", "career": "Interests",
    "preferences": "Preferences", "constraints": "Practical constraints",
    "finance": "Funding context", "location": "Where you are", "mobility": "Relocation",
    "research": "Research", "achievements": "Achievements", "discovery": "Still open",
}
_FIELD_LABELS = {
    "qualification_name": "qualification", "institution_name": "institution",
    "canonical_level": "level", "academic_status": "status", "field_of_study": "subject",
    "gpa": "GPA", "gpa_scale": "GPA scale", "marks_obtained": "marks", "marks_total": "out of",
    "overall_score": "overall score", "test_type": "test", "section_scores": "section scores",
    "goal_type": "kind of goal", "target_date": "target timing", "target_countries": "countries",
    "commitment": "how settled this is", "demonstrated_by": "examples you described",
    "supported_by": "related examples", "evidence_examples": "related examples", "proficiency": "stated level", "primary_interest": "main interest",
    "current_level": "current education", "current_direction": "direction",
    "academic_performance": "academic results", "practical_constraints": "practical circumstances",
    "education_history": "earlier education", "strengths": "strengths and examples",
}

def _readable(value) -> str:
    if isinstance(value, dict):
        return ", ".join(f"{_FIELD_LABELS.get(k, k.replace('_', ' '))}: {_readable(v)}"
                         for k, v in value.items() if v not in (None, {}, []) and k not in {
                             "id", "file_id", "source_event_id", "record_id", "student_stated", "provenance",
                             "supported_by", "goal_type", "key"})
    if isinstance(value, list):
        return "; ".join(_readable(v) for v in value)
    if isinstance(value, bool):
        return "yes" if value else "no"
    return str(value).replace("_", " ")

def _source(value: dict) -> str:
    provenance = value.get("provenance") or {}
    verification = provenance.get("verification")
    if verification == "document_supported":
        return "document-supported"
    if provenance.get("source") in {"conversation", "user_explicit"} or verification == "self_reported":
        return "you told me"
    return "source not yet confirmed"

def _mirror_domain(view: dict, domain: str) -> str:
    data = view.get(domain) or {}
    if domain == "discovery":
        statuses = {"UNKNOWN": "not yet known", "DECLINED": "you prefer not to share",
                    "DEFERRED": "you want to return to this later", "NOT_APPLICABLE": "you said this does not apply"}
        return "; ".join(f"{_FIELD_LABELS.get(focus, focus.replace('_', ' '))}: "
                         f"{statuses.get(item['status'], 'not yet known')}"
                         for focus, item in data.items())
    if "nodes" in data:
        lines = []
        for node in data["nodes"]:
            content = _readable(node)
            if content:
                lines.append(f"{content} ({_source(node)})")
        coverage = view.get("coverage", {}).get(domain) or {}
        if coverage.get("truncated"):
            lines.append(f"Showing {coverage['shown']} of {coverage['total']} recorded items; this is a partial summary")
        return "; ".join(lines)
    return "; ".join(f"{_FIELD_LABELS.get(name, name.replace('_', ' '))}: "
                     f"{_readable(item.get('value'))} ({_source(item)})"
                     for name, item in data.items())

def student_mirror(view: dict, affected_domains: list[str] | None = None) -> str:
    domains = affected_domains or list(StudentUnderstandingBuilder.domain_hashes(view))
    sections = []
    for domain in domains:
        body = _mirror_domain(view, domain)
        if body or affected_domains:
            sections.append(f"{_LABELS.get(domain, domain)}: {body or 'nothing currently recorded'}")
    intro = "Here is the updated part of your mirror:" if affected_domains else "Here is a partial picture of what I understand so far:"
    return intro + "\n" + "\n".join(sections) + (
        "\nThis is open to correction, not a verdict about your ability or personality. "
        "Is this accurate, or would you like to change anything?")


def same_turn_education_conflict(view: dict, message: str) -> dict | None:
    """Only explicit current-study clauses can contradict current education."""
    text = message.casefold()
    if any(marker in text for marker in ("correction", "actually", "i changed", "i finished", "since then")):
        return None
    # Keep future aspirations and historical qualifications outside the claim.
    claims = re.findall(r"\b(?:i am currently|i'm currently)\s+([^.;!?]+)", text)
    mentioned = set()
    for claim in claims:
        claim = re.split(r"\b(?:and|but|then|want|hope|plan|would|will)\b", claim, maxsplit=1)[0]
        for level, pattern in (("doctorate", r"\b(?:phd|doctorate)\b"),
                               ("master", r"\b(?:master'?s|msc|ms degree)\b"),
                               ("bachelor", r"\b(?:bachelor'?s|bs degree|undergraduate)\b")):
            if re.search(pattern, claim):
                mentioned.add(level)
    current = [n for n in view["education"]["nodes"] if n.get("academic_status") == "current"]
    if len(mentioned) != 1 or not current or any(n.get("canonical_level") in mentioned for n in current):
        return None
    level = next(iter(mentioned))
    known = current[0].get("qualification_name") or current[0].get("canonical_level")
    return {"summary": f"Current education on record is {known}; new claim says {level}.",
            "question": f"I have {known} as your current qualification. Has that changed?"}
