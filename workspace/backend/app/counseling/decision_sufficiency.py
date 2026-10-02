"""Decision-specific evidence checks, separate from mirror confirmation."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass


DECISION_TYPES = frozenset({
    "choose_bachelor_direction", "compare_fields", "choose_subjects",
    "study_abroad_direction", "career_direction", "exploration_next_step",
})
_FIELD_NAMES = {
    "computer_science": ("computer science", "cs"),
    "economics": ("economics", "econ"),
    "business": ("business", "bba"),
    "medicine": ("medicine", "medical"),
    "design": ("design",),
    "artificial_intelligence": ("artificial intelligence", "ai"),
}


def _key(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(value or "").casefold()).strip("_")


def _candidate_keys(message: str, view: dict) -> tuple[str, ...]:
    text = message.casefold()
    found = []
    for key, aliases in _FIELD_NAMES.items():
        if any(re.search(r"\b" + re.escape(alias) + r"\b", text) for alias in aliases):
            found.append(key)
    if not found:
        found.extend(item.get("domain") for item in view.get("interests", {}).get("stated", []))
        found.extend(item.get("suggested_direction") for item in view.get("influences", {}).get("nodes", []))
    normalized = []
    for item in found:
        key = _key(item)
        for canonical, aliases in _FIELD_NAMES.items():
            if key in {_key(alias) for alias in aliases}:
                key = canonical
                break
        if key and key not in normalized:
            normalized.append(key)
    return tuple(normalized[:6])


def decision_type_for_message(message: str, view: dict) -> str | None:
    text = " ".join((message or "").casefold().split())
    if re.search(r"\b(?:what|which|where)\b.*\b(?:explore|try|experiment)\b|\bexplore next\b", text):
        return "exploration_next_step"
    if re.search(r"\b(?:abroad|country|countries|overseas)\b", text) and re.search(
            r"\b(?:should|choose|decide|best|where)\b", text):
        return "study_abroad_direction"
    if re.search(r"\b(?:subjects?|a levels|courses?)\b", text) and re.search(
            r"\b(?:choose|pick|take|should|which)\b", text):
        return "choose_subjects"
    if re.search(r"\bcompare\b|\b(?:versus|vs\.?|or)\b", text) and re.search(
            r"\b(?:fields?|degrees?|majors?|cs|economics|business|bba|medicine|design|ai)\b", text):
        return "compare_fields" if "compare" in text else "choose_bachelor_direction"
    if re.search(r"\b(?:career|job|profession|field of work)\b", text) and re.search(
            r"\b(?:choose|should|best|which|recommend|decide)\b", text):
        return "career_direction"
    if re.search(r"\b(?:choose|study|degree|major|bachelor|after school|after college)\b", text) and re.search(
            r"\b(?:should|which|what|best|recommend|choose)\b", text):
        return "choose_bachelor_direction"
    if re.search(r"\b(?:what should i choose|so what should i choose|what do you recommend)\b", text):
        education = view.get("education", {}).get("nodes") or []
        if any(row.get("canonical_level") in {"school", "secondary", "upper_secondary"} for row in education):
            return "choose_bachelor_direction"
    return None


@dataclass(frozen=True)
class DecisionSufficiency:
    decision_type: str
    baseline_confirmed: bool
    evidence: dict[str, str]
    recommendation_ready: bool
    comparison_ready: bool
    research_ready: bool
    missing_evidence: tuple[str, ...]
    next_best_move: str
    candidates: tuple[str, ...]

    def to_dict(self) -> dict:
        return asdict(self)


class DecisionSufficiencyEvaluator:
    def evaluate(self, view: dict, decision_type: str, *,
                 message: str = "") -> DecisionSufficiency:
        if decision_type not in DECISION_TYPES:
            raise ValueError("unsupported decision type")
        baseline = view.get("baseline", {}).get("status") == "confirmed"
        education = view.get("education", {}).get("nodes") or []
        courses = view.get("education", {}).get("courses") or []
        academic = any(node.get("result") for node in education) or any(
            item.get("grade") or item.get("score") for item in courses)
        voice = view.get("student_voice") or {}
        direction = (voice.get("current_direction") or {}).get("status", "unknown")
        interests = view.get("interests", {}).get("stated") or []
        influences = view.get("influences", {}).get("nodes") or []
        candidates = _candidate_keys(message, view)
        exposure = {item.get("domain"): item for item in view.get("exposure", {}).get("domains", [])}
        reflected = {key for key, item in exposure.items() if any(
            row.get("activity_status") == "completed" and row.get("student_reflection")
            for row in item.get("experiences", []))}
        missing_exposure = [key for key in candidates if key not in reflected]
        strengths = bool(view.get("projects", {}).get("nodes") or
                         view.get("activities", {}).get("nodes") or
                         view.get("experience", {}).get("nodes") or
                         any(row.get("supported_by") for row in view.get("skills", {}).get("nodes", [])))
        own_preference = bool(interests or direction in {"exploring", "committed"})
        voice_status = ("known" if direction == "committed" else
                        "partial" if own_preference or direction == "uncertain" else "insufficient")
        evidence = {
            "education": "known" if education else "insufficient",
            "academic_performance": "known" if academic else "insufficient",
            "student_voice": voice_status,
            "interests": "known" if interests else "insufficient",
            "exposure": ("known" if len(reflected) >= 2 and not missing_exposure else
                         "partial" if reflected else "insufficient"),
            "strength_evidence": "known" if strengths else "insufficient",
            "external_influences": "known" if influences else "not_recorded",
        }
        missing = []
        if not baseline:
            missing.append("confirm the current student mirror")
        comparison = baseline and bool(education) and academic and len(candidates) >= 2
        research = baseline and bool(candidates or direction in {"exploring", "committed"})
        if decision_type in {"choose_bachelor_direction", "compare_fields"}:
            if not education:
                missing.append("current education")
            if not academic:
                missing.append("academic performance in relevant subjects")
            if not own_preference or direction == "uncertain":
                missing.append("the student's own preference apart from outside suggestions")
            if len(candidates) < 2:
                missing.append("at least two directions to compare")
            missing.extend(f"practical exposure to {key.replace('_', ' ')}" for key in missing_exposure)
            ready = baseline and bool(education) and academic and own_preference and direction != "uncertain" and len(candidates) >= 2 and not missing_exposure
        elif decision_type == "choose_subjects":
            if not education:
                missing.append("current education")
            if not academic:
                missing.append("academic performance in relevant subjects")
            if not own_preference:
                missing.append("student's own subject interests")
            ready = baseline and bool(education) and academic and own_preference
            comparison = baseline and bool(education) and academic
        elif decision_type == "study_abroad_direction":
            finance = view.get("finance") or {}
            budget = bool(finance.get("facts", {}).get("budget") or finance.get("sponsors"))
            destinations = bool(view.get("preferences", {}).get("target_countries") or any(
                (goal.get("details") or {}).get("target_countries")
                for goal in view.get("goals", {}).get("nodes", [])))
            if not education:
                missing.append("current education")
            if not own_preference:
                missing.append("student's own study direction")
            if not budget:
                missing.append("funding context")
            if not destinations:
                missing.append("destinations under consideration")
            evidence.update(finance="known" if budget else "insufficient",
                            destinations="known" if destinations else "insufficient")
            ready = baseline and bool(education) and own_preference and budget and destinations
            comparison = baseline and bool(education) and destinations
        elif decision_type == "career_direction":
            if not own_preference:
                missing.append("student's own career interests")
            if not strengths:
                missing.append("examples of strengths in practice")
            missing.extend(f"practical exposure to {key.replace('_', ' ')}" for key in missing_exposure)
            ready = baseline and own_preference and strengths and bool(candidates) and not missing_exposure
            comparison = baseline and len(candidates) >= 2 and strengths
        else:  # exploration_next_step is a low-stakes process choice.
            if not (interests or direction == "uncertain" or candidates):
                missing.append("a direction or uncertainty to explore")
            ready = baseline and not missing
            comparison = False
        if ready:
            next_move = "COUNSEL"
        elif missing_exposure and decision_type in {"choose_bachelor_direction", "compare_fields", "career_direction"}:
            next_move = "EXPLORE"
        elif influences and not own_preference:
            next_move = "CLARIFY"
        else:
            next_move = "ASK"
        return DecisionSufficiency(decision_type, baseline, evidence, ready, comparison,
                                   research, tuple(dict.fromkeys(missing)), next_move, candidates)


def guard_premature_verdict(response: str, result: dict | None) -> str:
    """Replace a clear final verdict when the deterministic check says not ready."""
    if not result or result.get("recommendation_ready") or result.get("decision_type") == "exploration_next_step":
        return response
    candidate_terms = set()
    for candidate in result.get("candidates") or ():
        candidate_terms.update(_FIELD_NAMES.get(candidate, (candidate.replace("_", " "),)))
    if not candidate_terms:
        return response
    options = "|".join(re.escape(term) for term in sorted(candidate_terms, key=len, reverse=True))
    directive = re.compile(
        rf"\b(?:you should|i recommend|my recommendation is|the best choice is|choose|go with)\s+(?:the\s+)?(?:{options})\b"
        rf"|\b(?:{options})\s+is\s+(?:the\s+)?best\s+(?:for you|choice)\b", re.I)
    if not directive.search(response):
        return response
    missing = result.get("missing_evidence") or []
    exposure = next((item for item in missing if item.startswith("practical exposure to ")), None)
    next_step = (f"A useful next step is to try a small activity in {exposure.removeprefix('practical exposure to ')} "
                 "and reflect on how you felt doing the work." if exposure else
                 "Let's clarify one of those missing pieces before choosing.")
    return ("I can compare the evidence we have, but I cannot recommend a final choice yet. "
            + next_step)
