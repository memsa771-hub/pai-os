# -*- coding: utf-8 -*-
"""LLM extraction of memory candidates from one conversational turn.

Produces *proposals* and nothing else. It cannot write canonical state, cannot
choose how far it is trusted, and cannot name its own `source_type` — the
caller sets that. Everything it emits still goes through the deterministic
reconciler.

Two failure modes are handled differently on purpose:

    malformed output      -> ExtractionError, the job fails and retries. We do
                             not half-parse a broken response into candidates.
    one bad candidate     -> dropped with a logged reason; its valid siblings
                             survive. One hallucinated field must not discard a
                             correctly extracted CGPA from the same turn.
"""

import json
import logging
import re
from dataclasses import dataclass
from typing import Any, Optional

from app.config import config
from app.services import pai
from app.services.cloud_providers import chat_completion

logger = logging.getLogger(__name__)

CANDIDATE_TYPES = ("vault_fact", "semantic_memory", "episode")
OPERATIONS = ("upsert",)          # extraction may only ADD proposals
MEMORY_TYPES = ("preference", "goal", "constraint", "interest", "context")
MAX_CANDIDATES = 8                 # a single turn yielding more is a runaway


class ExtractionError(RuntimeError):
    """The model's output could not be trusted. Retryable."""


@dataclass(frozen=True)
class ExtractedCandidate:
    """One validated proposal. Note the absence of `source_type` — the server
    assigns it, so there is no field here for a model to populate."""

    candidate_type: str
    operation: str
    key: Optional[str]
    proposed_value: Any
    content: Optional[str]
    entities: dict
    confidence: float
    evidence: dict


SYSTEM_PROMPT = """\
You extract durable facts about a student from one turn of conversation with \
their counsellor, for a long-term student profile.

You are CONSERVATIVE. Most turns contain nothing worth storing. Returning an \
empty list is the correct answer far more often than not, and a wrong memory \
is much worse than a missing one — it will be repeated back to the student as \
fact for months.

STORE only durable information that will still matter weeks from now:
- a concrete profile fact the student states about themselves
- a stable preference, goal or constraint they express
- a decision they made, or an action they took, with a reason

NEVER store:
- greetings, small talk, thanks, acknowledgements
- anything the ASSISTANT claimed, suggested or recommended, unless the student
  explicitly adopted it in their own words
- possibilities the student is only considering ("maybe", "I might", "what about")
- your own inferences about the student's personality or ability
- anything already present in the existing profile or memories below, unless
  the student is CORRECTING it
- transient logistics ("let me check", "one moment")

EVIDENCE RULE: only the student's own message is evidence. The assistant's \
reply is provided solely to help you resolve references like "that country" or \
"the same budget". If only the assistant said something, it is not a fact.

CORRECTIONS: when the student corrects an existing value, propose the NEW value. \
Do not try to delete or edit the old one.

Return ONLY a JSON object, no prose and no markdown fences:

{"candidates": [ ... ]}

Each candidate is one of:

  {"candidate_type": "vault_fact", "operation": "upsert",
   "key": "<copy one key EXACTLY from the VAULT FIELDS list in the user message;
           never invent or abbreviate one>",
   "proposed_value": <value matching that field's declared type>,
   "confidence": 0.0-1.0,
   "quote": "<the student's exact words that state this>"}

  {"candidate_type": "semantic_memory", "operation": "upsert",
   "content": "<one short third-person sentence about the student>",
   "memory_type": "preference|goal|constraint|interest|context",
   "entities": {...}, "confidence": 0.0-1.0,
   "quote": "<the student's exact words>"}

  {"candidate_type": "episode", "operation": "upsert",
   "content": "<what the student decided or did, and why>",
   "event_type": "<short_snake_case_label>",
   "entities": {...}, "confidence": 0.0-1.0,
   "quote": "<the student's exact words>"}

`quote` MUST be copied from the student's message. If you cannot quote the \
student for a candidate, do not emit that candidate.

If nothing is worth storing, return {"candidates": []}.
"""


def _model_config() -> tuple[str, str, str, Optional[str]]:
    """(api_key, provider, model, base_url) for extraction.

    Falls back to PAI Counselor's own configuration so this works out of the
    box, while `MEMORY_EXTRACTOR_*` lets extraction move to a cheaper/faster
    model later without touching Counselor.
    """
    api_key = getattr(config, "MEMORY_EXTRACTOR_API_KEY", "") or config.PAI_API_KEY
    provider = getattr(config, "MEMORY_EXTRACTOR_PROVIDER", "") or pai.PAI_PROVIDER
    model = getattr(config, "MEMORY_EXTRACTOR_MODEL", "") or config.PAI_MODEL
    base_url = (
        getattr(config, "MEMORY_EXTRACTOR_BASE_URL", "")
        or config.PAI_BASE_URL
        or None
    )
    return api_key, provider, model, base_url


def _render_field_specs(field_specs) -> str:
    """Render the Vault fields the model is allowed to propose.

    Without this the model is asked for "<one of the allowed vault field keys>"
    and never told what they are, so it guesses (`cgpa`, `ielts`) and every
    proposal is dropped by `_validate` as an unknown key — the Vault then never
    populates from conversation at all. The key must be exact, so it has to be
    listed; the type has to come with it, because a key the model gets right
    with a value of the wrong shape is rejected one layer later instead.
    """
    lines = []
    for spec in field_specs:
        key = spec.get("key")
        if not key:
            continue
        bits = [f"- {key}"]
        if spec.get("data_type"):
            bits.append(f"({spec['data_type']})")
        if spec.get("description"):
            bits.append(f"— {spec['description']}")
        schema = spec.get("validation_schema") or {}
        # Objects/arrays are the ones a model reliably gets wrong; a bare
        # "(object)" does not say which properties are required.
        if spec.get("data_type") in ("object", "array") and schema:
            bits.append(f"shape: {json.dumps(schema, sort_keys=True)}")
        lines.append(" ".join(bits))
    if not lines:
        return ""
    return (
        "VAULT FIELDS YOU MAY PROPOSE (`key` must match one of these EXACTLY; "
        "if nothing fits, use a semantic_memory instead):\n" + "\n".join(lines)
    )


def build_user_prompt(turn, field_specs=None) -> str:
    """Render a TurnContext into the extractor's user message."""
    parts: list[str] = []

    if field_specs:
        parts.append(_render_field_specs(field_specs))

    if turn.vault:
        parts.append(
            "EXISTING PROFILE (do not re-propose these unless corrected):\n"
            + json.dumps(turn.vault, ensure_ascii=False, sort_keys=True)
        )
    if turn.existing_memories:
        parts.append(
            "EXISTING MEMORIES (do not repeat these):\n"
            + "\n".join(f"- {m}" for m in turn.existing_memories)
        )
    if turn.recent:
        parts.append(
            "EARLIER IN THIS CONVERSATION (context only, not evidence):\n"
            + "\n".join(f"{m['role']}: {m['text']}" for m in turn.recent)
        )

    parts.append(f"STUDENT MESSAGE (the only evidence):\n{turn.user_text}")
    if turn.assistant_text:
        parts.append(
            "ASSISTANT REPLY (for reference resolution only — never evidence):\n"
            + turn.assistant_text
        )
    parts.append("Extract now. Return only the JSON object.")
    return "\n\n".join(parts)


def _parse_response(raw: str) -> list[dict]:
    """Parse the model's JSON. Raises ExtractionError on anything unusable."""
    if not (raw or "").strip():
        raise ExtractionError("empty extraction response")

    text = raw.strip()
    fence = re.match(r"^```(?:json)?\s*(.*?)\s*```$", text, re.DOTALL)
    if fence:
        text = fence.group(1).strip()

    try:
        parsed = json.loads(text)
    except (json.JSONDecodeError, ValueError) as exc:
        raise ExtractionError(f"extraction output was not valid JSON: {exc}") from exc

    if not isinstance(parsed, dict):
        raise ExtractionError("extraction output was not a JSON object")
    candidates = parsed.get("candidates")
    if candidates is None:
        raise ExtractionError("extraction output has no 'candidates' key")
    if not isinstance(candidates, list):
        raise ExtractionError("'candidates' was not a list")
    return candidates


def _validate(raw: dict, turn, allowed_vault_keys: set[str]) -> Optional[ExtractedCandidate]:
    """Validate one proposal. None (with a reason logged) if unusable.

    Dropping the individual candidate rather than raising is what lets a good
    CGPA survive a bad sibling from the same turn.
    """
    def drop(reason: str) -> None:
        logger.info("memory: dropped candidate — %s", reason)
        return None

    if not isinstance(raw, dict):
        return drop("not an object")

    candidate_type = raw.get("candidate_type")
    if candidate_type not in CANDIDATE_TYPES:
        return drop(f"unknown candidate_type {candidate_type!r}")

    operation = raw.get("operation", "upsert")
    if operation not in OPERATIONS:
        return drop(f"extraction may not propose operation {operation!r}")

    # The evidence rule, enforced rather than requested: the quote must
    # actually appear in the student's message. This is what stops the
    # assistant's own words becoming student truth (requirement 7).
    quote = (raw.get("quote") or "").strip()
    if not quote:
        return drop("no quote")
    if _normalize(quote) not in _normalize(turn.user_text):
        return drop("quote not found in the student's message")

    try:
        confidence = float(raw.get("confidence", 0.5))
    except (TypeError, ValueError):
        return drop("confidence not a number")
    confidence = max(0.0, min(1.0, confidence))

    entities = raw.get("entities")
    if not isinstance(entities, dict):
        entities = {}

    evidence = {"quote": quote[:500], "user_event_id": turn.user_event_id}

    if candidate_type == "vault_fact":
        key = raw.get("key")
        if not key or key not in allowed_vault_keys:
            return drop(f"unknown vault key {key!r}")
        if "proposed_value" not in raw:
            return drop("vault_fact without proposed_value")
        return ExtractedCandidate(
            candidate_type="vault_fact", operation="upsert", key=key,
            proposed_value=raw["proposed_value"], content=None,
            entities=entities, confidence=confidence, evidence=evidence,
        )

    content = (raw.get("content") or "").strip()
    if not content:
        return drop(f"{candidate_type} without content")

    if candidate_type == "semantic_memory":
        memory_type = raw.get("memory_type", "context")
        if memory_type not in MEMORY_TYPES:
            memory_type = "context"
        entities = {**entities, "memory_type": memory_type}
    else:
        event_type = raw.get("event_type") or "decision_made"
        entities = {**entities, "event_type": str(event_type)[:80]}

    return ExtractedCandidate(
        candidate_type=candidate_type, operation="upsert", key=None,
        proposed_value=None, content=content[:2000],
        entities=entities, confidence=confidence, evidence=evidence,
    )


def _normalize(text: str) -> str:
    """Casefold + collapse whitespace, for quote matching."""
    return re.sub(r"\s+", " ", (text or "")).strip().casefold()


async def extract_candidates(
    turn, allowed_vault_keys: set[str], field_specs=None,
) -> list[ExtractedCandidate]:
    """Run extraction for one turn. Raises ExtractionError on bad output.

    `allowed_vault_keys` is the authorization boundary — `_validate` drops
    anything outside it regardless of what the model returns. `field_specs`
    (key/type/description) is the same set rendered INTO the prompt so the
    model can hit those keys exactly; callers that pass only keys still get
    them listed, just without type hints.
    """
    if turn.is_empty():
        return []

    api_key, provider, model, base_url = _model_config()
    if not api_key:
        raise ExtractionError("no extraction API key configured")

    specs = field_specs or [{"key": key} for key in sorted(allowed_vault_keys)]
    raw = await chat_completion(
        api_key=api_key, provider=provider, model=model,
        messages=[{"role": "user", "content": build_user_prompt(turn, specs)}],
        system_prompt=SYSTEM_PROMPT, max_tokens=1200, base_url=base_url,
    )

    proposals = _parse_response(raw)
    if len(proposals) > MAX_CANDIDATES:
        logger.warning(
            "memory: extraction returned %d candidates, truncating to %d",
            len(proposals), MAX_CANDIDATES,
        )
        proposals = proposals[:MAX_CANDIDATES]

    validated = [
        candidate for candidate in (
            _validate(p, turn, allowed_vault_keys) for p in proposals
        ) if candidate is not None
    ]
    return validated
