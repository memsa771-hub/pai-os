"""Model-owned multilingual meaning, reduced to validated product operations."""

import json
import logging

from app.config import config
from app.inference.client import chat_completion
from app.memory.student_context import INTENT_RECORDS
from .decision_sufficiency import validated_decision_intent
from .discovery import DISCOVERY_FOCI, DISCOVERY_STATUSES
from .understanding import EDUCATION_LEVELS

logger = logging.getLogger(__name__)

_PROMPT = """Classify the student's current turn by meaning in any language or
mixture of languages. Return only JSON. Do not infer a stronger request than the
student made. The previous assistant message is context, not student evidence.
{
 "general_information": false,
 "mirror_confirmation": false,
 "mirror_request": false,
 "profile_correction": false,
 "requested_work": false,
 "requested_roadmap": false,
 "decision_intent": null,
 "context_intent": null,
 "topic_focus": null,
 "education_claim": null,
 "discovery_statuses": [],
 "explicit_commands": []
}
decision_intent, when confidently present, has {"type": one of
choose_bachelor_direction, compare_fields, choose_subjects,
study_abroad_direction, career_direction, exploration_next_step,
"candidates": [open-ended normalized direction labels named or clearly
referenced by the student]}. Leave it null when uncertain. A mirror confirmation
means the student approves the immediately preceding student mirror, not merely
that they agree with a different question. requested_work means a concrete
research/execution request. context_intent is one of the registered product
intents or null. topic_focus is one of the registered discovery foci or null.
education_claim, when the student clearly states their own present or completed
qualification, is {"canonical_level": one of the supplied education levels,
"academic_status": "current|completed", "correction": true|false}. Otherwise null.
discovery_statuses contains only explicit student statements of unknown,
declined, deferred, or not-applicable discovery topics. Each item has focus,
status, and an exact quote from the student's current message. Never infer a
status from a question or hypothetical statement.
explicit_commands contains only unambiguous direct owner requests to update a
specific canonical fact, retract it, or forget a narrowly named memory. Each
item has operation (upsert|retract|forget), exact quote, and canonical key for
fact operations or exact content_scope for forget. Conditional, quoted,
hypothetical, or negated requests yield no command.
Preserve original meaning; never require English phrasing."""


def validate_turn_semantics(raw: object, *, message: str = "") -> dict:
    data = raw if isinstance(raw, dict) else {}
    result = {key: data.get(key) is True for key in (
        "general_information", "mirror_confirmation", "mirror_request",
        "profile_correction", "requested_work", "requested_roadmap")}
    result["decision_intent"] = validated_decision_intent(data.get("decision_intent"))
    context_intent = data.get("context_intent")
    topic_focus = data.get("topic_focus")
    result["context_intent"] = context_intent if isinstance(context_intent, str) and context_intent in INTENT_RECORDS else None
    result["topic_focus"] = topic_focus if isinstance(topic_focus, str) and topic_focus in DISCOVERY_FOCI else None
    claim = data.get("education_claim")
    result["education_claim"] = ({
        "canonical_level": claim["canonical_level"],
        "academic_status": claim["academic_status"],
        "correction": claim.get("correction") is True,
    } if isinstance(claim, dict) and isinstance(claim.get("canonical_level"), str)
       and claim.get("canonical_level") in EDUCATION_LEVELS
       and isinstance(claim.get("academic_status"), str)
       and claim.get("academic_status") in {"current", "completed"} else None)
    from app.memory.voice_attribution import contained
    result["discovery_statuses"] = [
        {"focus": item["focus"], "status": item["status"], "quote": item["quote"]}
        for item in (data.get("discovery_statuses") or [])[:12]
        if isinstance(item, dict) and isinstance(item.get("focus"), str)
        and item.get("focus") in DISCOVERY_FOCI
        and isinstance(item.get("status"), str) and item.get("status") in DISCOVERY_STATUSES
        and contained(item.get("quote"), message)
    ] if isinstance(data.get("discovery_statuses"), list) else []
    from app.memory.explicit_commands import validated_commands
    result["explicit_commands"] = validated_commands(data.get("explicit_commands"), message)
    return result


async def classify_turn(message: str, *, previous_assistant: str = "",
                        fact_keys: tuple[str, ...] = ()) -> dict:
    """Fail closed to unknown intent when classification is unavailable."""
    try:
        raw = await chat_completion(
            api_key=config.PAI_API_KEY, model=config.PAI_MODEL,
            messages=[{"role": "user", "content": json.dumps({
                "student_message": message,
                "previous_assistant_message": previous_assistant[-2000:],
                "supported_context_intents": sorted(INTENT_RECORDS),
                "supported_topic_foci": sorted(DISCOVERY_FOCI),
                "supported_education_levels": EDUCATION_LEVELS,
                "supported_fact_keys": fact_keys,
            }, ensure_ascii=False)}],
            system_prompt=_PROMPT, max_tokens=500,
            base_url=config.PAI_BASE_URL or None)
        parsed = json.loads(raw.strip().removeprefix("```json").removesuffix("```").strip())
        return validate_turn_semantics(parsed, message=message)
    except Exception:
        logger.warning("counselor turn classification unavailable", exc_info=True)
        return validate_turn_semantics(None)


async def verifies_no_final_recommendation(response: str, decision_type: str) -> bool:
    """Independent semantic check; uncertainty fails closed for high-stakes turns."""
    try:
        raw = await chat_completion(
            api_key=config.PAI_API_KEY, model=config.PAI_MODEL,
            messages=[{"role": "user", "content": json.dumps({
                "response": response, "decision_type": decision_type,
            }, ensure_ascii=False)}],
            system_prompt=("In any language, decide whether the response makes a strong "
                "personal final choice of degree, field, subjects, destination, or career "
                "for the student. Comparing evidence, stating uncertainty, or suggesting "
                "an experiment is not a final choice. Return only JSON: "
                '{"final_recommendation": true|false}. With ambiguity, return true.'),
            max_tokens=100, base_url=config.PAI_BASE_URL or None)
        parsed = json.loads(raw.strip().removeprefix("```json").removesuffix("```").strip())
        return isinstance(parsed, dict) and parsed.get("final_recommendation") is False
    except Exception:
        logger.warning("final recommendation verification unavailable", exc_info=True)
        return False
