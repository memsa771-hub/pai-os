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
import math
from dataclasses import dataclass
from typing import Any, Optional

from app.config import config
from app.services import pai
from app.services.cloud_providers import chat_completion

logger = logging.getLogger(__name__)

CANDIDATE_TYPES = ("vault_fact", "semantic_memory", "episode", "student_record")
OPERATIONS = ("upsert",)          # extraction may only ADD proposals
MEMORY_TYPES = ("preference", "goal", "constraint", "interest", "context")
MAX_CANDIDATES = 16                # allow a useful multi-fact introduction


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

Capture every clearly stated education/career fact, goal, reason, constraint,
skill and experience that will help future counseling. A student's introduction
can contain several important facts: do not save only their country or degree
and miss the budget, motivation or career direction. Empty output is correct
for a greeting or irrelevant small talk. Be conservative about INFERENCE, not
about remembering what the student actually told PAI.

STORE only durable information that will still matter weeks from now:
- a concrete profile fact the student states about themselves
- a stable preference, goal or constraint they express
- a decision they made, or an action they took, with a reason
- education, test attempts, work, projects, or goals that materially describe
  the student's education or professional journey

NEVER store:
- greetings, small talk, thanks, acknowledgements
- anything the ASSISTANT claimed, suggested or recommended, unless the student
  explicitly adopted it in their own words
- a hypothetical question as a committed personal goal
- your own inferences about the student's personality or ability
- anything already present in the existing profile or memories below, unless
  the student is CORRECTING it
- transient logistics ("let me check", "one moment")
- casual entertainment preferences unrelated to education or career

EVIDENCE RULE: only the student's own message is evidence. The assistant's \
reply is provided solely to help you resolve references like "that country" or \
"the same budget". If only the assistant said something, it is not a fact.

CORRECTIONS: when the student corrects an existing value, propose the NEW value. \
Do not try to delete or edit the old one.

Exploratory personal ambitions are useful: save a seriously considered path
with commitment="exploratory" or "considering", never upgrade it to a decision.
Capture motivations, career direction, target timing and relevant family/cost
constraints in the record's defined details or a concise semantic memory.
Casual movie likes do not belong in the profile; studying film or public
service ambitions do. Understand English, Urdu and Roman Urdu equally.
Short answers such as "Germany", "about €12k", or "AI" can state important
constraints in an ongoing counseling conversation. Resolve their meaning from
the preceding student messages and the question being answered. Preserve the
currency/period only when stated or unambiguously established by that question.
"Cost is important" expresses affordability as a constraint, not an amount.
Do not lose these incremental answers because they are short, and do not turn
a request for one movie into a career interest. A recommendation by PAI remains
context only, including any research result, until the student adopts it.

Use the supplied RECORD SCHEMAS as the authoritative record field list.
If an existing record is being completed or corrected, put its exact id in
entities.record_id and propose only changed/new fields. Do not create a fresh
degree/job every time it is mentioned. Each genuinely new test attempt gets
its own record. When the student explicitly replaces an old goal, put the old
goal id in entities.supersedes_record_id on the NEW goal. Parallel education
and career goals can coexist; do not supersede one simply to add the other.
Never invent ids. Dates can retain YYYY or YYYY-MM precision. Do not invent
January 1, a GPA scale, expiry date or committed intake from vague timing.
Prefer structured records for identified qualifications, attempts, experience
and goals. An isolated GPA whose qualification cannot be identified may still
use a registered scalar field; do not manufacture an education identity.

Return ONLY a JSON object, no prose and no markdown fences:

{"candidates": [ ... ]}

Each candidate is one of:

  {"candidate_type": "student_record", "operation": "upsert",
   "key": "education|test_attempt|language_proficiency|work_experience|project|goal|skill|certification|research|achievement|financial_sponsor|scholarship_application|visa|application",
   "proposed_value": <object with only stated fields>,
   "confidence": 0.0-1.0, "quote": "<the student's exact words>"}

Use fields from the supplied schema. Preserve original qualification wording.
Keep separate records separate. OMIT any field the student did not state:
never write "Unknown", "N/A" or a guessed year, institution, date or score.
The record kind (education, test_attempt, ...) is the "key". "candidate_type"
is ALWAYS one of the four names above. "quote" and "confidence" are candidate
fields at the top level -- never inside "proposed_value".

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
    from .student_schema import extraction_specs
    parts.append("RECORD SCHEMAS (new records require their required fields; existing record patches may be partial):\n"
                 + json.dumps(extraction_specs(), ensure_ascii=False))
    if getattr(turn, "records", None):
        parts.append("EXISTING RECORDS (reuse the exact id when updating; do not duplicate):\n"
                     + json.dumps(turn.records, ensure_ascii=False))

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


# Fillers models emit for a field they were asked to omit. Storing one would
# put the string "Unknown" into a canonical student record.
_PLACEHOLDERS = frozenset({
    "", "-", "--", "n/a", "na", "none", "null", "nil", "unknown", "unspecified",
    "not specified", "not stated", "not mentioned", "not provided", "not applicable",
    "tbd", "to be determined", "unsure", "undecided",
})


def _coerce_proposal(raw: dict) -> dict:
    """Repair the two transport shapes models reliably get wrong.

    The RECORD SCHEMAS block lists record kinds ("education", "visa", ...) as
    top-level JSON keys, which pulls models into naming the kind as
    `candidate_type` and nesting `quote`/`confidence` inside `proposed_value`.
    Both shapes carry correctly extracted data, so normalize them rather than
    discard a good record. Nothing here relaxes a check: the repaired quote is
    still verified against the student's message, and the key is still matched
    against ENTITY_MODELS by the caller.
    """
    from .student_records import ENTITY_MODELS

    kind = raw.get("candidate_type")
    if isinstance(kind, str) and kind not in CANDIDATE_TYPES and kind in ENTITY_MODELS:
        raw = {**raw, "candidate_type": "student_record", "key": raw.get("key") or kind}

    value = raw.get("proposed_value")
    if isinstance(value, dict) and ("quote" in value or "confidence" in value):
        repaired = dict(raw)
        for field in ("quote", "confidence"):
            # A value already stated at the top level wins over the nested copy.
            if field in value and repaired.get(field) is None:
                repaired[field] = value[field]
        repaired["proposed_value"] = {
            k: v for k, v in value.items() if k not in ("quote", "confidence")
        }
        raw = repaired
    return raw


def _strip_placeholders(value):
    """Drop filler values recursively. None means "nothing worth storing"."""
    if isinstance(value, dict):
        cleaned = {}
        for key, child in value.items():
            child = _strip_placeholders(child)
            if child is not None:
                cleaned[key] = child
        return cleaned or None
    if isinstance(value, list):
        items = [item for item in (_strip_placeholders(v) for v in value) if item is not None]
        return items or None
    if isinstance(value, str) and value.strip().casefold() in _PLACEHOLDERS:
        return None
    return value


# Schema fields that assert a point in time. A model that is told "final year"
# will happily compute a graduation year; a counselor that believes the student
# already graduated gives wrong advice for the rest of the relationship.
_YEAR_FIELDS = ("graduation_year",)
_DATE_FIELDS = ("start_date", "end_date", "test_date", "expiry_date", "issued_on",
                "expires_on", "deadline", "target_date", "achieved_on")


def _drop_unevidenced_dates(value: dict, user_text: str) -> dict:
    """Remove a year the student never actually said.

    The module already refuses a quote that is not in the student's message;
    this applies the same evidence rule to the one field type models infer
    most confidently. Omitting a date is recoverable — PAI can ask. A wrong
    one silently poisons every later recommendation.
    """
    stated = set(re.findall(r"(?:19|20)\d{2}", user_text or ""))
    cleaned = dict(value)
    for field in _YEAR_FIELDS + _DATE_FIELDS:
        if field not in cleaned:
            continue
        if str(cleaned[field])[:4] not in stated:
            logger.info("memory: dropped inferred %s from a %s proposal", field, "record")
            cleaned.pop(field)
    return cleaned


# A budget is only comparable if its currency is written one way. Models echo
# whatever the student typed ("EUR", "eur", "€"), and "€" != "EUR" defeats every
# later comparison, conversion and affordability check. Only unambiguous symbols
# are mapped; an ambiguous one is left untouched rather than guessed wrong.
_CURRENCY_SYMBOLS = {
    "€": "EUR", "£": "GBP", "¥": "JPY", "₹": "INR", "₨": "PKR", "₩": "KRW",
    "₪": "ILS", "₺": "TRY", "₽": "RUB", "₴": "UAH", "₫": "VND", "฿": "THB",
    # In international education "$" unqualified means USD often enough that
    # storing the bare symbol is worse than applying the convention.
    "$": "USD", "US$": "USD", "usd": "USD", "eur": "EUR", "euro": "EUR",
    "euros": "EUR", "gbp": "GBP", "pkr": "PKR", "inr": "INR",
}


def _normalize_currencies(value):
    """Rewrite any `currency` field to an ISO-4217-style code, recursively."""
    if isinstance(value, dict):
        out = {}
        for key, child in value.items():
            if key == "currency" and isinstance(child, str):
                text = child.strip()
                mapped = _CURRENCY_SYMBOLS.get(text) or _CURRENCY_SYMBOLS.get(text.casefold())
                if mapped is None and len(text) == 3 and text.isalpha():
                    mapped = text.upper()
                out[key] = mapped or text
            else:
                out[key] = _normalize_currencies(child)
        return out
    if isinstance(value, list):
        return [_normalize_currencies(item) for item in value]
    return value


_MONEY_FIELDS = ("amount", "value", "tuition", "cost")


def _stated_amounts(text: str) -> set:
    """Every figure the student actually wrote, with k/thousand expanded.

    "About EUR 12k per year" yields {12, 12000}, so a proposal of 12000 is
    evidenced and a proposal of 15000 is not.
    """
    found = set()
    for raw, suffix in re.findall(r"(\d[\d,.\s]*)\s*(k|m|thousand|million|lakh|crore)?",
                                  (text or ""), flags=re.IGNORECASE):
        digits = re.sub(r"[,\s]", "", raw).rstrip(".")
        if not digits:
            continue
        try:
            number = float(digits)
        except ValueError:
            continue
        found.add(number)
        multiplier = {"k": 1e3, "thousand": 1e3, "m": 1e6, "million": 1e6,
                      "lakh": 1e5, "crore": 1e7}.get((suffix or "").lower())
        if multiplier:
            found.add(number * multiplier)
        # "12,000" also reads as a bare 12 followed by 000 in sloppy output.
        if "." not in digits:
            found.add(number * 1000)
    return found


def _money_is_evidenced(figure, text: str) -> bool:
    """True when this exact figure appears in the student's own words."""
    if isinstance(figure, bool) or not isinstance(figure, (int, float)):
        return True  # not a number we can check; other rules apply
    return any(abs(figure - candidate) < 0.01 for candidate in _stated_amounts(text))


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

    raw = _coerce_proposal(raw)

    candidate_type = raw.get("candidate_type")
    if candidate_type not in CANDIDATE_TYPES:
        return drop(f"unknown candidate_type {candidate_type!r}")

    operation = raw.get("operation", "upsert")
    if operation not in OPERATIONS:
        return drop(f"extraction may not propose operation {operation!r}")

    # The evidence rule, enforced rather than requested: the quote must
    # actually appear in the student's message. This is what stops the
    # assistant's own words becoming student truth (requirement 7).
    if not isinstance(raw.get("quote"), str):
        return drop("quote not a string")
    quote = raw["quote"].strip()
    if not quote:
        return drop("no quote")
    if _normalize(quote) not in _normalize(turn.user_text):
        return drop("quote not found in the student's message")

    try:
        confidence = float(raw.get("confidence", 0.5))
    except (TypeError, ValueError):
        return drop("confidence not a number")
    if not math.isfinite(confidence):
        return drop("confidence not finite")
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
        proposed = _strip_placeholders(raw["proposed_value"])
        if proposed is None:
            return drop(f"vault_fact {key!r} stated no actual value")
        proposed = _normalize_currencies(proposed)
        # A money field of 0 is a model filling in a blank, not a figure the
        # student gave ("Cost is important" became {"amount": 0} in testing).
        # Storing it as canonical makes every affordability check wrong; no
        # budget at all is recoverable, because PAI can simply ask.
        if isinstance(proposed, dict):
            for money in ("amount", "value", "tuition", "cost"):
                figure = proposed.get(money)
                if isinstance(figure, bool) or figure is None:
                    continue
                if isinstance(figure, (int, float)) and figure <= 0:
                    return drop(f"vault_fact {key!r} proposed a non-positive {money}")
                # A figure the student never said is invented, and a wrong
                # budget is more damaging than a missing one: it looks
                # legitimate and silently misprices every recommendation.
                if not _money_is_evidenced(figure, turn.user_text):
                    return drop(f"vault_fact {key!r} {money}={figure!r} is not in the student's words")
        return ExtractedCandidate(
            candidate_type="vault_fact", operation="upsert", key=key,
            proposed_value=proposed, content=None,
            entities=entities, confidence=confidence, evidence=evidence,
        )

    if candidate_type == "student_record":
        from .student_records import ENTITY_MODELS
        from .student_schema import validate_record
        from .errors import MemoryDataError
        kind = raw.get("key")
        value = raw.get("proposed_value")
        if not isinstance(kind, str) or kind not in ENTITY_MODELS or kind in ("course", "document") or not isinstance(value, dict):
            return drop("invalid student record proposal")
        record_ids = {row["id"] for row in (getattr(turn, "records", {}) or {}).get(kind, [])}
        clean_entities = {}
        for reference in ("record_id", "supersedes_record_id"):
            if reference in entities:
                if not isinstance(entities[reference], str) or entities[reference] not in record_ids:
                    return drop("record reference was not in this student's context")
                clean_entities[reference] = entities[reference]
        if "supersedes_record_id" in clean_entities and kind != "goal":
            return drop("only goals may supersede another goal")
        value = _strip_placeholders(value)
        if not isinstance(value, dict) or not value:
            return drop(f"{kind} record stated no actual values")
        value = _drop_unevidenced_dates(value, turn.user_text)
        value = _normalize_currencies(value)
        if not value:
            return drop(f"{kind} record stated no actual values")
        try:
            value = validate_record(kind, value, partial="record_id" in clean_entities)
        except MemoryDataError as exc:
            return drop(str(exc))
        return ExtractedCandidate(
            candidate_type="student_record", operation="upsert", key=kind,
            proposed_value=value, content=None, entities=clean_entities,
            confidence=confidence, evidence=evidence,
        )

    if not isinstance(raw.get("content"), str):
        return drop("content not a string")
    content = raw["content"].strip()
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
        system_prompt=SYSTEM_PROMPT, max_tokens=4000, base_url=base_url,
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
