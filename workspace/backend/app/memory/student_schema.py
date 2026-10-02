"""Shared record schemas for extraction, validation and counseling context.

Only stated information belongs here. Missing values remain absent, including
grading scales and test expiry dates; normalization must not invent them.
"""

from copy import deepcopy
from datetime import date, datetime, timezone
import math
import re

from .errors import MemoryDataError
from app.tools.executor import _validate


def string():
    return {"type": "string", "minLength": 1, "maxLength": 1000}


def number(minimum=0):
    return {"type": "number", "minimum": minimum}


def strings():
    return {"type": "array", "items": string(), "maxItems": 30}


def obj(properties, required=()):
    return {"type": "object", "properties": properties,
            "required": list(required), "additionalProperties": False}


RESULT_SCHEMA = obj({
    "gpa": number(), "gpa_scale": number(), "percentage": {**number(), "maximum": 100},
    "marks_obtained": number(), "marks_total": number(), "grade": string(),
    "grading_system": string(), "backlogs": {"type": "integer", "minimum": 0},
    "class_or_division": string(),
})

# Each entry owns required fields, the permitted nested data, identity hints,
# and fields safe/useful in routine counseling. Prompts render this registry.
RECORD_SPECS = {
    "education": {
        "required": ("qualification_name",),
        "identity": ("qualification_name", "institution_name"),
        "discriminators": ("start_date", "graduation_year"),
        "properties": {
            "qualification_name": string(), "institution_name": string(),
            "canonical_level": {"type": "string", "enum": ["school", "secondary", "upper_secondary", "diploma", "associate", "bachelor", "master", "mphil", "doctorate", "professional", "other"]},
            "field_of_study": string(), "start_date": string(), "end_date": string(),
            "graduation_year": {"type": "integer", "minimum": 1900, "maximum": 2200},
            "academic_status": {"type": "string", "enum": ["current", "completed", "incomplete", "planned"]},
            "result": RESULT_SCHEMA,
            "details": obj({"institution_country": string(), "institution_city": string(),
                "framework": string(), "major": string(), "minor": string(),
                "specialization": string(), "study_mode": string(), "thesis_title": string(),
                "prerequisites": strings(), "distinctions": strings()}),
        },
    },
    "course": {
        "required": ("education_id", "name"), "identity": ("education_id", "name"),
        "discriminators": (),
        "properties": {"education_id": string(), "name": string(), "normalized_name": string(),
            "grade": string(), "score": RESULT_SCHEMA, "credits": number(),
            "details": obj({"semester": string(), "academic_year": string(), "credit_system": string()})},
    },
    "test_attempt": {
        "required": ("test_type",), "identity": ("test_type",),
        "discriminators": ("test_date", "attempt_number"),
        "properties": {"test_type": string(), "original_name": string(),
            "attempt_number": {"type": "integer", "minimum": 1}, "test_date": string(),
            "expiry_date": string(), "overall_score": string(),
            "section_scores": {"type": "object"},
            "details": obj({"status": string(), "test_variant": string()})},
    },
    "work_experience": {
        "required": ("organization", "role"), "identity": ("organization", "role"),
        "discriminators": ("start_date",),
        "properties": {"organization": string(), "role": string(), "experience_type": string(),
            "start_date": string(), "end_date": string(),
            "details": obj({"responsibilities": strings(), "achievements": strings(),
                            "skills": strings(), "country": string(), "current": {"type": "boolean"}})},
    },
    "project": {
        "required": ("name",), "identity": ("name",), "discriminators": ("start_date",),
        "properties": {"name": string(), "role": string(), "start_date": string(), "end_date": string(),
            "details": obj({"description": string(), "skills": strings(), "technologies": strings(),
                            "outcomes": strings(), "url": string()})},
    },
    "activity": {
        "required": ("title", "activity_type"),
        "identity": ("title", "activity_type", "organization"),
        "discriminators": ("start_date",),
        "properties": {
            "title": string(),
            "activity_type": {"type": "string", "enum": ["club", "competition", "volunteering", "extracurricular", "leadership", "other"]},
            "organization": string(), "role": string(),
            "start_date": string(), "end_date": string(),
            "details": obj({"description": string(), "responsibilities": strings(),
                            "outcomes": strings(), "skills": strings(),
                            "leadership_role": string()}),
        },
    },
    "exploration_experience": {
        "required": ("domain", "activity_type", "title"),
        "identity": ("domain", "activity_type", "title"),
        "discriminators": ("started_at",),
        "properties": {
            "domain": string(), "activity_type": string(), "title": string(),
            "activity_status": {"type": "string", "enum": ["planned", "in_progress", "completed", "stopped"]},
            "exposure_level": {"type": "string", "enum": ["none", "introductory", "practical", "sustained"]},
            "started_at": string(), "completed_at": string(),
            "student_reflection": obj({
                "enjoyed": {"type": "boolean"}, "what_enjoyed": string(),
                "what_disliked": string(), "wants_more_exposure": {"type": "boolean"},
                "notes": string(),
            }),
            "evidence_refs": {"type": "array", "maxItems": 20, "items": obj({
                "kind": {"type": "string", "enum": ["project", "work_experience", "research", "achievement", "activity", "course"]},
                "id": string(),
            }, ("kind", "id"))},
        },
    },
    "goal": {
        "required": ("goal_type", "title"), "identity": ("goal_type", "title"), "discriminators": (),
        "properties": {"goal_type": string(), "title": string(),
            "commitment": {"type": "string", "enum": ["exploratory", "considering", "committed"]},
            "target_date": string(),
            "details": obj({"motivation": string(), "success_criteria": string(), "degree_level": string(),
                "field_of_study": string(), "target_countries": strings(), "target_intake": string(),
                "career_direction": string(), "constraints": strings(),
                "direction_status": {"type": "string", "enum": ["exploring", "considering", "committed", "changed", "rejected"]},
                "decision_rationale": string()})},
    },
    "skill": {
        "required": ("name",), "identity": ("name",), "discriminators": (),
        "properties": {"name": string(), "proficiency": string(),
            "details": obj({"demonstrated_by": strings(), "learning_goal": string()})},
    },
    "certification": {
        "required": ("name",), "identity": ("name", "issuer"), "discriminators": ("issued_on",),
        "properties": {"name": string(), "issuer": string(), "issued_on": string(), "expires_on": string(),
            "details": obj({"skills": strings(), "credential_url": string()})},
    },
    "application": {
        "required": ("institution_name",), "identity": ("institution_name", "program_name", "intake"),
        "discriminators": (),
        "properties": {"institution_name": string(), "program_name": string(), "intake": string(),
            "application_status": string(), "deadline": string(),
            "details": obj({"missing_documents": strings(), "next_action": string()})},
    },
    "document": {
        "required": ("file_id", "document_type"), "identity": ("file_id",), "discriminators": (),
        "properties": {"file_id": string(), "document_type": string(), "title": string(),
            "details": obj({"related_record_type": string(), "related_record_id": string()})},
    },
    "language_proficiency": {
        "required": ("language",), "identity": ("language",), "discriminators": ("evidence_type",),
        "properties": {"language": string(), "proficiency": string(), "evidence_type": string(),
            "details": obj({"native": {"type": "boolean"}, "notes": string()})},
    },
    "research": {
        "required": ("title",), "identity": ("title", "organization"), "discriminators": ("start_date",),
        "properties": {"title": string(), "organization": string(), "role": string(),
            "start_date": string(), "end_date": string(),
            "details": obj({"abstract": string(), "methods": strings(), "outcomes": strings(),
                            "publication_title": string(), "publication_url": string()})},
    },
    "achievement": {
        "required": ("title",), "identity": ("title", "issuer"), "discriminators": ("achieved_on",),
        "properties": {"title": string(), "achievement_type": string(), "issuer": string(),
            "achieved_on": string(), "details": obj({"description": string(), "level": string(),
                                                       "leadership_role": string(), "activity_type": string()})},
    },
    "financial_sponsor": {
        "required": ("sponsor_type",), "identity": ("sponsor_type", "name"), "discriminators": (),
        "properties": {"sponsor_type": string(), "name": string(), "commitment_status": string(),
            "details": obj({"relationship": string(), "amount": number(), "currency": string(),
                            "evidence_available": {"type": "boolean"}})},
    },
    "scholarship_application": {
        "required": ("scholarship_name",), "identity": ("scholarship_name", "provider"), "discriminators": ("deadline",),
        "properties": {"scholarship_name": string(), "provider": string(), "application_status": string(),
            "deadline": string(), "details": obj({"award_amount": number(), "currency": string(),
                                                    "missing_requirements": strings(), "next_action": string()})},
    },
    "visa": {
        "required": ("country",), "identity": ("country", "visa_type"), "discriminators": ("expiry_date",),
        "properties": {"country": string(), "visa_type": string(), "application_status": string(),
            "expiry_date": string(), "details": obj({"history_type": string(), "decision_date": string(),
                                                       "refusal_reason": string(), "next_action": string()})},
    },
}


def validate_record(kind: str, values: dict, *, partial: bool = False) -> dict:
    """Normalize unambiguous transport shapes, then validate all supplied data."""
    if kind not in RECORD_SPECS or not isinstance(values, dict):
        raise MemoryDataError("Unknown student record type or invalid record")
    value = deepcopy(values)
    spec = RECORD_SPECS[kind]
    try:
        _finite(value)
    except ValueError as exc:
        raise MemoryDataError(str(exc)) from exc
    # LLMs commonly emit a numeric band even though tests also have textual grades.
    if kind == "test_attempt" and isinstance(value.get("overall_score"), (int, float)) and not isinstance(value["overall_score"], bool):
        value["overall_score"] = str(value["overall_score"])
    try:
        _validate(obj(spec["properties"], () if partial else spec["required"]), value, kind)
        _finite(value)
        for key in ("start_date", "end_date", "test_date", "expiry_date", "issued_on", "expires_on", "deadline", "target_date", "achieved_on"):
            if key in value:
                text = value[key]
                if not re.fullmatch(r"\d{4}(?:-\d{2})?(?:-\d{2})?", text):
                    raise ValueError(f"{key} must be YYYY, YYYY-MM or YYYY-MM-DD")
                date.fromisoformat(text + ("-01-01" if len(text) == 4 else "-01" if len(text) == 7 else ""))
        for key in ("started_at", "completed_at"):
            if key in value:
                stamp = value[key]
                if re.fullmatch(r"\d{4}-\d{2}-\d{2}", stamp):
                    date.fromisoformat(stamp)
                else:
                    parsed = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
                    if parsed.tzinfo is None:
                        raise ValueError(f"{key} must be a date or timezone-aware ISO timestamp")
        if "started_at" in value and "completed_at" in value:
            def instant(text):
                if len(text) == 10:
                    return datetime.fromisoformat(text).replace(tzinfo=timezone.utc)
                return datetime.fromisoformat(text.replace("Z", "+00:00")).astimezone(timezone.utc)
            if instant(value["started_at"]) > instant(value["completed_at"]):
                raise ValueError("completed_at must not precede started_at")
        for start, end in (("start_date", "end_date"), ("test_date", "expiry_date"), ("issued_on", "expires_on")):
            if start in value and end in value:
                precision = min(len(value[start]), len(value[end]))
                if value[start][:precision] > value[end][:precision]:
                    raise ValueError(f"{end} must not precede {start}")
        result = value.get("result") or {}
        for actual, maximum in (("gpa", "gpa_scale"), ("marks_obtained", "marks_total")):
            if actual in result and maximum in result and (result[maximum] <= 0 or result[actual] > result[maximum]):
                raise ValueError(f"{actual} exceeds its stated scale")
        if kind == "test_attempt":
            sections = value.get("section_scores", {})
            if len(sections) > 20 or any(not isinstance(k, str) or len(k) > 80 or
                    isinstance(v, bool) or not isinstance(v, (str, int, float)) for k, v in sections.items()):
                raise ValueError("Invalid section scores")
        if kind == "exploration_experience":
            if value.get("completed_at") and value.get("activity_status") != "completed":
                raise ValueError("completed_at requires completed activity_status")
            if value.get("student_reflection") == {}:
                raise ValueError("student_reflection cannot be empty")
    except (ValueError, TypeError) as exc:
        raise MemoryDataError(str(exc)) from exc
    return value


def _finite(value):
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("Non-finite numbers are not student facts")
    if isinstance(value, dict):
        for child in value.values():
            _finite(child)
    elif isinstance(value, list):
        for child in value:
            _finite(child)


def extraction_specs():
    return {kind: obj(spec["properties"], spec["required"])
            for kind, spec in RECORD_SPECS.items() if kind not in ("course", "document")}
