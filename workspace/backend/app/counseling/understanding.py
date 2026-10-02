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
    "research": "research", "achievements": "achievement", "activities": "activity",
    "explorations": "exploration_experience",
    "languages": "language_proficiency", "certifications": "certification",
    "documents": "document", "applications": "application",
    "scholarships": "scholarship_application", "visa": "visa",
}
_LIMITS = {"documents": 8, "applications": 8, "scholarships": 8, "visa": 8}


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
    result = {**value, "id": row.get("id"), "provenance": _provenance(row)}
    if kind == "exploration_experience":
        evidence = row.get("evidence") or {}
        for field in ("reflection", "completion"):
            detail = evidence.get(field) or {}
            if detail:
                result[field + "_provenance"] = {
                    "source": detail.get("source_type"),
                    "evidence": {key: detail.get("evidence", {}).get(key)
                                 for key in ("source_event_id", "execution_run_id")
                                 if detail.get("evidence", {}).get(key)},
                }
    return result


def _education_edges(nodes: list[dict]) -> list[dict]:
    """Link recorded qualifications only when chronology or level supports it."""
    known = [node for node in nodes if node.get("id")]
    def order(node):
        level = node.get("canonical_level")
        rank = EDUCATION_LEVELS.index(level) if level in EDUCATION_LEVELS else len(EDUCATION_LEVELS)
        return rank, node.get("start_date") or node.get("end_date") or ""
    ordered = sorted(known, key=order)
    edges = []
    for earlier, later in zip(ordered, ordered[1:]):
        first, second = earlier.get("canonical_level"), later.get("canonical_level")
        dated = bool(earlier.get("end_date") and later.get("start_date")
                     and earlier["end_date"] <= later["start_date"])
        ranked = first in EDUCATION_LEVELS and second in EDUCATION_LEVELS and (
            EDUCATION_LEVELS.index(first) < EDUCATION_LEVELS.index(second))
        contradictory_dates = (earlier.get("end_date") and later.get("start_date")
                               and earlier["end_date"] > later["start_date"])
        if (dated or ranked) and not contradictory_dates:
            edges.append({"from": earlier["id"], "to": later["id"],
                          "relation": "precedes", "basis": "dates" if dated else "level"})
    return edges


def _support_relationships(view: dict) -> tuple[list[dict], int]:
    """References to existing records; no inferred skill or qualification rows."""
    relationships = []
    sources = (
        ("project", view["projects"]["nodes"], "skills", "name"),
        ("activity", view["activities"]["nodes"], "skills", "title"),
        ("work_experience", view["experience"]["nodes"], "skills", "role"),
        ("research", view["research"]["nodes"], "methods", "title"),
        ("course", view["education"]["courses"], None, "name"),
    )
    for skill in view["skills"]["nodes"]:
        name = str(skill.get("name") or "").strip().casefold()
        if not name or not skill.get("id"):
            continue
        skill["supported_by"] = []
        skill["evidence_examples"] = []
        demonstrated = {str(value).strip().casefold()
                        for value in (skill.get("details") or {}).get("demonstrated_by", [])}
        for kind, rows, detail_key, label_key in sources:
            for row in rows:
                if not row.get("id"):
                    continue
                details = row.get("details") or {}
                explicit = {str(value).strip().casefold()
                            for value in details.get(detail_key, [])} if detail_key else set()
                course_match = kind == "course" and (
                    str(row.get("name") or "").strip().casefold() == name or
                    str(row.get("name") or "").strip().casefold() in demonstrated)
                if name not in explicit and not course_match:
                    continue
                ref = {"type": kind, "id": row["id"]}
                skill["supported_by"].append(ref)
                skill["evidence_examples"].append(str(row.get(label_key) or kind)[:120])
                relationships.append({"from": ref, "to": {"type": "skill", "id": skill["id"]},
                                      "relation": "supports"})
    for education in view["education"]["nodes"]:
        field = str(education.get("field_of_study") or "").strip().casefold()
        if not field or not education.get("id"):
            continue
        for goal in view["goals"]["nodes"]:
            target = str((goal.get("details") or {}).get("field_of_study") or "").strip().casefold()
            title = str(goal.get("title") or "").casefold()
            related_cs = (field in {"computer science", "software engineering"} and
                          (target in {"artificial intelligence", "ai", "machine learning",
                                      "data science"} or bool(re.search(
                                          r"\b(?:ai|artificial intelligence|machine learning|data science)\b",
                                          title))))
            if goal.get("id") and (target == field or related_cs):
                relationships.append({"from": {"type": "education", "id": education["id"]},
                                      "to": {"type": "goal", "id": goal["id"]},
                                      "relation": "relevant_to",
                                      "basis": "matching_stated_field" if target == field else "related_field_family"})
    visible = {
        "project": {row.get("id") for row in view["projects"]["nodes"]},
        "work_experience": {row.get("id") for row in view["experience"]["nodes"]},
        "research": {row.get("id") for row in view["research"]["nodes"]},
        "achievement": {row.get("id") for row in view["achievements"]["nodes"]},
        "activity": {row.get("id") for row in view["activities"]["nodes"]},
        "course": {row.get("id") for row in view["education"]["courses"]},
    }
    for experience in view["explorations"]["nodes"]:
        for ref in experience.get("evidence_refs") or []:
            if ref.get("id") in visible.get(ref.get("kind"), set()):
                relationships.append({"from": {"type": "exploration_experience", "id": experience["id"]},
                                      "to": {"type": ref["kind"], "id": ref["id"]},
                                      "relation": "has_evidence", "basis": "canonical_reference"})
    return relationships[:32], len(relationships)


def _domain_key(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(value or "").casefold()).strip("_")


def _live_goals(view: dict) -> list[dict]:
    return [node for node in view["goals"]["nodes"]
            if (node.get("details") or {}).get("direction_status") not in {"rejected", "changed"}]


def _interests_and_exposure(view: dict) -> None:
    """Keep stated interest, attempted exposure and demonstrated skill distinct."""
    stated = []
    interest = view.get("career", {}).get("primary_interest")
    if interest and interest.get("value"):
        stated.append({"domain": _domain_key(interest["value"]),
                       "title": _plain(interest["value"]), "basis": "stated",
                       "provenance": interest["provenance"]})
    for goal in view["goals"]["nodes"]:
        if (goal.get("details") or {}).get("direction_status") in {"rejected", "changed"}:
            continue
        title = (goal.get("details") or {}).get("field_of_study") or (
            goal.get("title") if goal.get("commitment") in {"exploratory", "considering"} else None)
        if title and not any(item["domain"] == _domain_key(title) for item in stated):
            stated.append({"domain": _domain_key(title), "title": _plain(title),
                           "basis": "stated_direction", "commitment": goal.get("commitment"),
                           "provenance": goal.get("provenance")})
    view["interests"] = {"stated": stated[:12], "experienced": []}
    by_domain = {}
    for row in view["explorations"]["nodes"]:
        key = _domain_key(row.get("domain"))
        if not key:
            continue
        by_domain.setdefault(key, []).append(row)
        reflection = row.get("student_reflection") or {}
        if row.get("activity_status") == "completed" and reflection.get("enjoyed") is True:
            view["interests"]["experienced"].append({
                "domain": key, "title": row.get("title"), "basis": "student_reflection",
                "experience_id": row.get("id"), "student_reflection": reflection,
                "provenance": row.get("reflection_provenance") or row.get("provenance"),
            })
    view["exposure"] = {"domains": [
        {"domain": key, "experiences": rows[:8],
         "completed_count": sum(row.get("activity_status") == "completed" for row in rows),
         "reported_level": next((row["exposure_level"] for row in rows
                                 if row.get("exposure_level")), None)}
        for key, rows in list(by_domain.items())[:12]
    ], "unexplored_interests": [
        item["domain"] for item in stated if item["domain"] not in by_domain
    ]}


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
            limit = _LIMITS.get(section, 12)
            view[section] = {"nodes": [_node(row, kind) for row in records.get(kind, [])[:limit]]}
        view["coverage"] = {
            section: {"shown": min(len(records.get(kind, [])), _LIMITS.get(section, 12)),
                      "total": len(records.get(kind, [])),
                      "truncated": len(records.get(kind, [])) > _LIMITS.get(section, 12)}
            for section, kind in _KINDS.items()
        }
        visible_education = {node["id"] for node in view["education"]["nodes"]}
        linked_courses = [row for row in records.get("course", [])
                          if row.get("education_id") in visible_education]
        view["education"]["courses"] = [_node(row, "course") for row in linked_courses[:24]]
        view["coverage"]["courses"] = {
            "shown": min(len(linked_courses), 24), "total": len(records.get("course", [])),
            "truncated": len(linked_courses) > 24 or len(linked_courses) < len(records.get("course", [])),
        }
        for node in view["education"]["nodes"]:
            node["course_ids"] = [course["id"] for course in view["education"]["courses"]
                                  if course.get("education_id") == node["id"]]
        view["education"]["edges"] = _education_edges(view["education"]["nodes"])
        # Tentative interpretation belongs in the current reply, never in the
        # canonical mirror merely because it was retained as semantic memory.
        view["memories"] = []
        view["finance"] = {"facts": view["finance"],
                           "sponsors": [_node(row, "financial_sponsor")
                                        for row in records.get("financial_sponsor", [])[:8]]}
        view["coverage"]["sponsors"] = {
            "shown": min(len(records.get("financial_sponsor", [])), 8),
            "total": len(records.get("financial_sponsor", [])),
            "truncated": len(records.get("financial_sponsor", [])) > 8,
        }
        view["goals"]["nodes"] += [
            {"key": key, "value": _plain(row["value"]), "provenance": _provenance(row)}
            for key, row in facts.items() if key.startswith("goal.") or key.startswith("goals.")
        ]
        view["goals"]["edges"] = []
        _interests_and_exposure(view)
        levels = {n.get("canonical_level") for n in view["education"]["nodes"]}
        gaps = []
        for level in levels:
            predecessor = _PREDECESSOR.get(level)
            if predecessor and predecessor not in levels:
                gaps.append({"level": predecessor, "status": "UNKNOWN",
                             "reason": f"predecessor of {level} is not recorded"})
        view["education"]["gaps"] = sorted(gaps, key=lambda g: EDUCATION_LEVELS.index(g["level"]))
        view["relationships"], relationship_total = _support_relationships(view)
        view["coverage"]["relationships"] = {
            "shown": len(view["relationships"]), "total": relationship_total,
            "truncated": relationship_total > len(view["relationships"]),
        }
        view["open_conflicts"] = []
        for issue in snapshot.issues[:8]:
            evidence = issue.get("evidence") or {}
            key = evidence.get("field_key") or issue.get("field_key")
            # Restricted values can occur in free-text summaries as well as
            # evidence. Omit the entire issue unless its target is safe.
            if key and key not in facts:
                continue
            kind = evidence.get("record_type") or issue.get("record_type")
            if not key and kind not in set(_KINDS.values()) | {"course", "financial_sponsor"}:
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
            domain: "KNOWN" if view[domain]["nodes"] else "UNKNOWN"
            for domain in _KINDS
        }
        view["domain_status"].update({
            domain: "KNOWN" if view[domain] else "UNKNOWN"
            for domain in ("identity", "preferences", "constraints", "career", "location", "mobility")
        })
        view["domain_status"]["interests"] = (
            "KNOWN" if view["interests"]["stated"] or view["interests"]["experienced"] else "UNKNOWN")
        view["domain_status"]["exposure"] = (
            "KNOWN" if view["exposure"]["domains"] else "UNKNOWN")
        view["domain_status"]["finance"] = (
            "KNOWN" if view["finance"]["facts"] or view["finance"]["sponsors"] else "UNKNOWN")
        view["domain_status"]["motivation"] = (
            "KNOWN" if any((node.get("details") or {}).get("motivation")
                           for node in view["goals"]["nodes"]) else "UNKNOWN"
        )
        if (view["domain_status"]["motivation"] == "UNKNOWN"
                and view["discovery"].get("motivation", {}).get("student_stated")):
            view["domain_status"]["motivation"] = view["discovery"]["motivation"]["status"]
        domain_for_kind = {kind: section for section, kind in _KINDS.items()}
        domain_for_kind.update(course="education", financial_sponsor="finance")
        for conflict in view["open_conflicts"]:
            domain = (conflict.get("field_key") or "").partition(".")[0] or domain_for_kind.get(
                conflict.get("record_type"))
            if domain in view["domain_status"]:
                view["domain_status"][domain] = "CONFLICTING"
        focus_domain = {gap["focus"]: gap["domain"] for gap in view["open_gaps"] if gap.get("focus")}
        for focus, item in view["discovery"].items():
            domain = focus_domain.get(focus)
            if (domain in view["domain_status"] and view["domain_status"][domain] == "UNKNOWN"
                    and item["status"] in SUPPRESSED_STATUSES):
                view["domain_status"][domain] = item["status"]
        view["baseline"] = {"status": (baseline or {}).get("status", "discovering"),
                            "version": (baseline or {}).get("version", 0),
                            "confirmed_at": (baseline or {}).get("confirmed_at")}
        return view

    @staticmethod
    def gaps(view: dict) -> list[dict]:
        gaps = []
        goals = _live_goals(view)
        if not view["education"]["nodes"]:
            gaps.append({"domain": "education", "status": "UNKNOWN", "focus": "current_level"})
        if not goals:
            gaps.append({"domain": "goals", "status": "UNKNOWN", "focus": "current_direction"})
        if goals and not any((n.get("details") or {}).get("motivation") for n in goals):
            gaps.append({"domain": "goals", "status": "UNKNOWN", "focus": "motivation"})
        if view["education"]["nodes"] and not any(n.get("result") for n in view["education"]["nodes"]):
            gaps.append({"domain": "education", "status": "UNKNOWN", "focus": "academic_performance"})
        abroad = any(
            str(node.get("goal_type") or "").casefold() in {"study_abroad", "masters_abroad"}
            or bool((node.get("details") or {}).get("target_countries"))
            for node in goals
        )
        if abroad and not (view["finance"]["facts"].get("budget")
                           or view["finance"]["sponsors"]):
            gaps.append({"domain": "finance", "status": "UNKNOWN", "focus": "budget"})
        if abroad and not (view["preferences"].get("target_countries")
                           or any((n.get("details") or {}).get("target_countries")
                                  for n in goals)):
            gaps.append({"domain": "preferences", "status": "UNKNOWN", "focus": "target_location"})
        if abroad and not any(n.get("target_date") or (n.get("details") or {}).get("target_intake")
                              for n in goals):
            gaps.append({"domain": "goals", "status": "UNKNOWN", "focus": "target_timing"})
        career_change = any(
            str(n.get("goal_type") or "").casefold() in {"career_transition", "career_change"}
            or any(term in str(n.get("title") or "").casefold()
                   for term in ("change career", "switch career", "career transition"))
            for n in goals)
        if career_change and not view["experience"]["nodes"]:
            gaps.append({"domain": "experience", "status": "UNKNOWN", "focus": "work_history"})
        if not view.get("career", {}).get("primary_interest") and not any(
                (node.get("details") or {}).get("field_of_study") or
                (node.get("details") or {}).get("career_direction") for node in goals):
            gaps.append({"domain": "career", "status": "UNKNOWN", "focus": "interests"})
        if not any(node.get("supported_by") or (node.get("details") or {}).get("demonstrated_by")
                   for node in view["skills"]["nodes"]) and not view["projects"]["nodes"] and not view["experience"]["nodes"] and not view.get("research", {}).get("nodes"):
            gaps.append({"domain": "skills", "status": "UNKNOWN", "focus": "strengths"})
        if not view["constraints"] and not view.get("mobility") and not any(
                (n.get("details") or {}).get("constraints") for n in goals):
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
                               "career", "location", "mobility", "research", "achievements",
                               "languages", "certifications", "documents", "applications",
                               "scholarships", "visa", "discovery", "explorations",
                               "interests", "exposure", "activities")}



def baseline_sufficient(view: dict) -> bool:
    """Contextual readiness for confirming a partial but useful student mirror."""
    if view["open_conflicts"]:
        return False
    discovery = view.get("discovery") or {}
    def addressed(focus):
        return bool(discovery.get(focus, {}).get("student_stated"))
    education = view["education"]["nodes"]
    goals = _live_goals(view)
    if not education and not (addressed("current_level") and (
            view["experience"]["nodes"] or view["projects"]["nodes"])):
        return False
    if not (goals or view.get("career", {}).get("primary_interest") or addressed("current_direction")):
        return False
    if goals and not (any((goal.get("details") or {}).get("motivation") for goal in goals)
                      or addressed("motivation")):
        return False

    early_student = bool(education) and all(
        node.get("canonical_level") in {"school", "secondary", "upper_secondary"}
        for node in education)
    academic_goal = any(
        str(goal.get("goal_type") or "").casefold() in {
            "study_abroad", "masters_abroad", "academic", "admission"}
        or any(term in str(goal.get("title") or "").casefold()
               for term in ("master", "university", "admission", "degree"))
        for goal in goals)
    abroad = any(
        str(goal.get("goal_type") or "").casefold() in {"study_abroad", "masters_abroad"}
        or bool((goal.get("details") or {}).get("target_countries"))
        for goal in goals)
    career_change = any(
        str(goal.get("goal_type") or "").casefold() in {"career_transition", "career_change"}
        or any(term in str(goal.get("title") or "").casefold()
               for term in ("change career", "switch career", "career transition"))
        for goal in goals)
    if (academic_goal or early_student) and not (
            any(node.get("result") for node in education) or addressed("academic_performance")):
        return False
    if academic_goal and view["education"]["gaps"] and not addressed("education_history"):
        return False
    strengths = (view["skills"]["nodes"] or view["projects"]["nodes"]
                 or view["research"]["nodes"] or view["experience"]["nodes"]
                 or view["achievements"]["nodes"])
    if (career_change or (academic_goal and not early_student)) and not (
            strengths or addressed("strengths")):
        return False
    if career_change and not early_student and not (
            view["experience"]["nodes"] or addressed("work_history")):
        return False
    if abroad and not (view["finance"]["facts"].get("budget")
                       or view["finance"]["sponsors"] or addressed("budget")):
        return False
    if abroad and not (view["preferences"].get("target_countries")
                       or any((goal.get("details") or {}).get("target_countries")
                              for goal in goals) or addressed("target_location")):
        return False
    if career_change and not (
            view["constraints"] or any((goal.get("details") or {}).get("constraints")
                                      for goal in goals) or addressed("practical_constraints")):
        return False
    return True


_LABELS = {
    "identity": "About you", "education": "Education so far", "experience": "Experience",
    "projects": "Projects", "skills": "Skills and supporting examples", "tests": "Tests",
    "languages": "Languages", "certifications": "Certifications",
    "goals": "Directions considered and decisions", "career": "Interests",
    "preferences": "Preferences", "constraints": "Practical constraints",
    "finance": "Funding context", "location": "Where you are", "mobility": "Relocation",
    "research": "Research", "achievements": "Achievements", "discovery": "Still open",
    "activities": "Activities", "explorations": "Exploration experiences",
    "interests": "Interests and their evidence", "exposure": "What you have tried",
    "documents": "Documents", "applications": "Applications",
    "scholarships": "Scholarships", "visa": "Visa history",
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
    "work_history": "work history", "target_location": "destination",
    "target_timing": "target timing",
    "exposure_level": "exposure so far", "activity_status": "activity status",
    "student_reflection": "your reflection", "wants_more_exposure": "want to explore further",
}

def _readable(value) -> str:
    if isinstance(value, dict):
        return ", ".join(f"{_FIELD_LABELS.get(k, k.replace('_', ' '))}: {_readable(v)}"
                         for k, v in value.items() if v not in (None, {}, []) and k not in {
                             "id", "file_id", "source_event_id", "record_id", "student_stated", "provenance",
                             "supported_by", "course_ids", "related_record_id", "evidence_refs",
                             "goal_type", "key", "reflection_provenance", "completion_provenance"})
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
    if verification in {"verified", "externally_verified"}:
        return "verified"
    if provenance.get("source") in {"conversation", "user_explicit"} or verification == "self_reported":
        return "you told me"
    return "source not yet confirmed"

def _mirror_domain(view: dict, domain: str) -> str:
    data = view.get(domain) or {}
    if domain == "interests":
        stated = [f"{item['title']} (you mentioned it)" for item in data.get("stated", [])]
        experienced = [f"{item['domain'].replace('_', ' ')}: {item['title']} "
                       f"(completed experience and your reflection)"
                       for item in data.get("experienced", [])]
        return "; ".join((stated + experienced)[:16])
    if domain == "exposure":
        parts = [f"{item['domain'].replace('_', ' ')}: "
                 f"{item['completed_count']} completed experience(s)"
                 + (f", reported exposure {item['reported_level']}" if item.get("reported_level") else "")
                 for item in data.get("domains", [])]
        parts.extend(f"{key.replace('_', ' ')}: no experience recorded yet"
                     for key in data.get("unexplored_interests", []))
        return "; ".join(parts[:16])
    if domain == "explorations":
        lines = []
        for node in data.get("nodes", []):
            status = str(node.get("activity_status") or "planned").replace("_", " ")
            completion = node.get("completion_provenance") or {}
            if status == "completed" and completion.get("evidence"):
                status += " (recorded from execution evidence)"
            reflection = node.get("student_reflection") or {}
            line = f"{node.get('domain', 'field').replace('_', ' ')}: {node.get('title', 'activity')} — {status}"
            if reflection:
                line += f"; your reflection: {_readable(reflection)}"
            lines.append(line)
        coverage = view.get("coverage", {}).get(domain) or {}
        if coverage.get("truncated"):
            lines.append(f"Showing {coverage['shown']} of {coverage['total']} recorded items")
        return "; ".join(lines)
    if domain == "discovery":
        statuses = {"UNKNOWN": "not yet known", "DECLINED": "you prefer not to share",
                    "DEFERRED": "you want to return to this later", "NOT_APPLICABLE": "you said this does not apply"}
        return "; ".join(f"{_FIELD_LABELS.get(focus, focus.replace('_', ' '))}: "
                         f"{statuses.get(item['status'], 'not yet known')}"
                         for focus, item in data.items())
    if domain == "finance":
        facts = "; ".join(f"{_FIELD_LABELS.get(name, name.replace('_', ' '))}: "
                          f"{_readable(item.get('value'))} ({_source(item)})"
                          for name, item in data.get("facts", {}).items())
        sponsors = "; ".join(f"{_readable(node)} ({_source(node)})"
                             for node in data.get("sponsors", []))
        return "; ".join(part for part in (facts, sponsors) if part)
    if "nodes" in data:
        lines = []
        courses = {node["id"]: [] for node in data["nodes"] if node.get("id")} if domain == "education" else {}
        for course in data.get("courses", []):
            if course.get("education_id") in courses:
                courses[course["education_id"]].append(str(course.get("name") or "course")[:120])
        for node in data["nodes"]:
            content = _readable(node)
            if content:
                course_names = courses.get(node.get("id")) or []
                suffix = f"; courses: {', '.join(course_names[:8])}" if course_names else ""
                lines.append(f"{content}{suffix} ({_source(node)})")
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


def same_turn_education_conflict(view: dict, message: str,
                                 recent_conversation: list[dict] | None = None) -> dict | None:
    """Clarify high-confidence incompatible current or completed education claims."""
    text = message.casefold()
    if any(marker in text for marker in (
            "correction", "actually", "i changed", "i finished", "since then",
            "to clarify", "i meant", "i was wrong")):
        return None
    patterns = (("doctorate", r"\b(?:phd|doctorate)\b"),
                ("master", r"\b(?:master'?s|msc|ms degree)\b"),
                ("bachelor", r"\b(?:bachelor'?s|bs degree|undergraduate)\b"))
    def levels(clause):
        clause = re.split(r"\b(?:and|but|then|want|hope|plan|would|will)\b",
                          clause, maxsplit=1)[0]
        found = set()
        for level, pattern in patterns:
            if re.search(pattern, clause):
                found.add(level)
        return found
    current_claims = re.findall(r"\b(?:i am currently|i'm currently|i am studying|i'm studying)\s+([^.;!?]+)", text)
    completed_claims = re.findall(
        r"\b(?:i already completed|i have completed|i completed|i graduated with)\s+([^.;!?]+)", text)
    mentioned = set().union(*(levels(claim) for claim in current_claims)) if current_claims else set()
    completed = set().union(*(levels(claim) for claim in completed_claims)) if completed_claims else set()
    current = [n for n in view["education"]["nodes"] if n.get("academic_status") == "current"]
    if not current:
        for turn in reversed((recent_conversation or [])[-8:]):
            if turn.get("role") != "user":
                continue
            prior = str(turn.get("content") or "").casefold()
            prior_claims = re.findall(r"\b(?:i am currently|i'm currently|i am studying|i'm studying)\s+([^.;!?]+)", prior)
            prior_levels = set().union(*(levels(claim) for claim in prior_claims)) if prior_claims else set()
            if len(prior_levels) == 1:
                current = [{"canonical_level": next(iter(prior_levels)),
                            "qualification_name": next(iter(prior_levels)).title()}]
                break
    if not current:
        return None
    known_levels = {n.get("canonical_level") for n in current}
    if len(mentioned) == 1 and not known_levels.intersection(mentioned):
        level = next(iter(mentioned))
    elif len(completed) == 1 and any(
            EDUCATION_LEVELS.index(level) >= EDUCATION_LEVELS.index(known)
            for level in completed for known in known_levels
            if known in EDUCATION_LEVELS):
        level = next(iter(completed))
    else:
        return None
    known = current[0].get("qualification_name") or current[0].get("canonical_level")
    return {"summary": f"Current education on record is {known}; new claim says {level}.",
            "question": f"I have {known} as your current qualification. Has that changed?"}
