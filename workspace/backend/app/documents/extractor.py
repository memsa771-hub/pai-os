# -*- coding: utf-8 -*-
"""LLM understanding of a parsed document. Proposals only.

The same contract as `app/memory/extractor.py`, for the same reason: this is
the one place an LLM reads untrusted content and says what it means, so it
must not be able to write anything. It emits validated proposals; the
deterministic reconciler decides.

Three enforcement points, none of them a prompt instruction:

1. `source_type` is assigned by the CALLER, never read from model output, so
   a document cannot claim to be `user_explicit` and outrank the student.
2. Every quote is checked against the PARSED DOCUMENT. A quote the document
   does not contain drops the candidate — this is what makes "never invent
   quotes" true rather than requested.
3. Every locator is checked against the segments that were actually parsed,
   so page numbers cannot be invented either.

Schema-driven throughout: record shapes come from `student_schema`, Vault
keys from `VaultFieldDefinition`. There is deliberately no second list of
profile fields here.
"""

from __future__ import annotations

import json
import logging
import math
import re
from dataclasses import dataclass
from typing import Any, Optional

from .classify import DOCUMENT_CLASSES
from .content import Segment
from .untrusted import DOCUMENT_RULES, DOCUMENT_RULES_TRAILER, render_untrusted_document

logger = logging.getLogger(__name__)

EXTRACTOR_VERSION = "1"

CANDIDATE_TYPES = ("vault_fact", "student_record", "semantic_memory", "episode")
MAX_CANDIDATES = 40          # a transcript legitimately carries many facts
MAX_PROMPT_CHARS = 60_000


class DocumentExtractionError(RuntimeError):
    """Model output could not be trusted. Retryable."""


@dataclass(frozen=True)
class DocumentFinding:
    """One validated proposal. No `source_type` field, by construction."""

    candidate_type: str
    key: Optional[str]
    proposed_value: Any
    content: Optional[str]
    entities: dict
    confidence: float
    evidence: dict


@dataclass(frozen=True)
class DocumentUnderstanding:
    classification: str
    classification_confidence: float
    title: Optional[str]
    findings: list[DocumentFinding]


SYSTEM_PROMPT = f"""\
You read ONE uploaded student document and report what it states, for a
long-term student profile.

{DOCUMENT_RULES}

Your job has two parts.

1. CLASSIFY the document as exactly one of:
{chr(10).join('   - ' + name for name in DOCUMENT_CLASSES)}

   Use "generic_student_document" for a real student document that fits no
   other class, and "unknown" if it does not appear to relate to this
   student's education or career at all. Do not force a type.

2. EXTRACT what the document explicitly states about the student.

EVIDENCE RULE (enforced, not requested): every finding needs a `quote` copied
EXACTLY from the document text, and the `locator` of the block it came from
(the [p1], [para3], [table2] markers). A finding whose quote does not appear
in the document is discarded automatically. Never paraphrase a quote, never
write your own wording into it, and never invent a locator.

OMIT anything the document does not state. Never write "Unknown", "N/A" or a
guessed institution, date, score or grading scale. A missing value is
recoverable — PAI can ask the student. A fabricated one silently corrupts
their profile.

Do NOT infer educational history that is not evidenced. A Master's transcript
is not evidence of a Bachelor's degree, even though one normally precedes the
other. Propose only what the document itself supports.

Do NOT copy the whole document into memory. A transcript's fifty courses are
not fifty memories. Use:

  student_record  identified qualifications, test attempts, work, projects,
                  certifications, achievements, applications, visas, sponsors
  vault_fact      registered scalar profile fields
  semantic_memory ONLY durable context that matters beyond this document
                  (e.g. a motivation stated in an SOP). Not a summary of the
                  document, and not one entry per sentence.
  episode         a meaningful dated event the document confirms

Use the supplied RECORD SCHEMAS as the authoritative field list, and the
VAULT FIELDS list for scalar keys (copy a key EXACTLY; never invent one).

If an EXISTING RECORD is the same real-world thing, put its exact id in
entities.record_id and propose only the fields the document ADDS or
CONTRADICTS. Do not create a duplicate degree, job or test attempt. Never
invent an id.

Return ONLY a JSON object, no prose and no markdown fences:

{{"classification": "<one class name>",
  "classification_confidence": 0.0-1.0,
  "title": "<short human title for this document>",
  "findings": [ ... ]}}

Each finding is one of:

  {{"candidate_type": "student_record",
    "key": "education|test_attempt|work_experience|project|goal|skill|certification|research|achievement|financial_sponsor|scholarship_application|visa|application|language_proficiency",
    "proposed_value": {{<only fields the document states>}},
    "confidence": 0.0-1.0, "quote": "<exact text>", "locator": "<e.g. p1>"}}

  {{"candidate_type": "vault_fact", "key": "<exact key from VAULT FIELDS>",
    "proposed_value": <value of the declared type>,
    "confidence": 0.0-1.0, "quote": "<exact text>", "locator": "<e.g. p1>"}}

  {{"candidate_type": "semantic_memory",
    "content": "<one short third-person sentence about the student>",
    "memory_type": "preference|goal|constraint|interest|context",
    "confidence": 0.0-1.0, "quote": "<exact text>", "locator": "<e.g. p2>"}}

  {{"candidate_type": "episode", "content": "<what happened>",
    "event_type": "<short_snake_case_label>",
    "confidence": 0.0-1.0, "quote": "<exact text>", "locator": "<e.g. p1>"}}

If the document states nothing about the student, return an empty findings
list. That is a correct answer.

{DOCUMENT_RULES_TRAILER}
"""


def _model_config() -> tuple[str, str, str, Optional[str]]:
    from app.config import config
    from app.services import pai

    api_key = getattr(config, "DOCUMENT_EXTRACTOR_API_KEY", "") or config.PAI_API_KEY
    provider = pai.PAI_PROVIDER
    model = getattr(config, "DOCUMENT_EXTRACTOR_MODEL", "") or config.PAI_MODEL
    base_url = (
        getattr(config, "DOCUMENT_EXTRACTOR_BASE_URL", "")
        or config.PAI_BASE_URL
        or None
    )
    return api_key, provider, model, base_url


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "")).strip().casefold()


def build_user_prompt(
    segments: list[Segment], *, filename: str, document_type: str,
    field_specs: list[dict], existing_records: dict,
) -> str:
    from app.memory.student_schema import extraction_specs

    parts: list[str] = []

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
        lines.append(" ".join(bits))
    if lines:
        parts.append(
            "VAULT FIELDS YOU MAY PROPOSE (`key` must match EXACTLY):\n"
            + "\n".join(lines)
        )

    parts.append(
        "RECORD SCHEMAS (new records require their required fields; a patch to "
        "an existing record may be partial):\n"
        + json.dumps(extraction_specs(), ensure_ascii=False)
    )

    if existing_records:
        parts.append(
            "EXISTING RECORDS (reuse the exact id when this document describes "
            "the same thing; do not duplicate):\n"
            + json.dumps(existing_records, ensure_ascii=False, default=str)
        )

    parts.append(render_untrusted_document(
        segments, filename=filename, document_type=document_type,
        max_chars=MAX_PROMPT_CHARS,
    ))
    parts.append("Classify and extract now. Return only the JSON object.")
    return "\n\n".join(parts)


def _parse_response(raw: str) -> dict:
    if not (raw or "").strip():
        raise DocumentExtractionError("empty extraction response")
    text = raw.strip()
    fence = re.match(r"^```(?:json)?\s*(.*?)\s*```$", text, re.DOTALL)
    if fence:
        text = fence.group(1).strip()
    try:
        parsed = json.loads(text)
    except (json.JSONDecodeError, ValueError) as exc:
        raise DocumentExtractionError(f"output was not valid JSON: {exc}") from exc
    if not isinstance(parsed, dict):
        raise DocumentExtractionError("output was not a JSON object")
    return parsed


_PLACEHOLDERS = frozenset({
    "", "-", "--", "n/a", "na", "none", "null", "nil", "unknown", "unspecified",
    "not specified", "not stated", "not mentioned", "not provided",
    "not applicable", "tbd", "to be determined",
})


def _strip_placeholders(value):
    if isinstance(value, dict):
        cleaned = {}
        for key, child in value.items():
            child = _strip_placeholders(child)
            if child is not None:
                cleaned[key] = child
        return cleaned or None
    if isinstance(value, list):
        items = [v for v in (_strip_placeholders(i) for i in value) if v is not None]
        return items or None
    if isinstance(value, str) and value.strip().casefold() in _PLACEHOLDERS:
        return None
    return value


def _validate_finding(
    raw: dict, *, document_text: str, locators: set[str],
    allowed_vault_keys: set[str], record_ids: dict[str, set[str]],
) -> Optional[DocumentFinding]:
    """Validate one proposal. None (logged) if unusable.

    Dropping the individual finding rather than raising is deliberate: one
    hallucinated field must not discard a correctly extracted CGPA from the
    same transcript.
    """
    def drop(reason: str):
        logger.info("document extraction: dropped finding — %s", reason)
        return None

    if not isinstance(raw, dict):
        return drop("not an object")

    candidate_type = raw.get("candidate_type")
    if candidate_type not in CANDIDATE_TYPES:
        return drop(f"unknown candidate_type {candidate_type!r}")

    quote = raw.get("quote")
    if not isinstance(quote, str) or not quote.strip():
        return drop("missing quote")
    quote = quote.strip()
    # THE evidence rule. A quote the document does not contain is invented.
    if _normalize(quote) not in document_text:
        return drop("quote does not appear in the document")

    locator = raw.get("locator")
    if not isinstance(locator, str) or locator not in locators:
        # An invented page number is as damaging as an invented quote: it
        # makes a fabricated claim look verifiable.
        return drop(f"locator {locator!r} is not a block of this document")

    try:
        confidence = float(raw.get("confidence", 0.5))
    except (TypeError, ValueError):
        return drop("confidence not a number")
    if not math.isfinite(confidence):
        return drop("confidence not finite")
    confidence = max(0.0, min(1.0, confidence))

    evidence = {"quote": quote[:500], "locator": locator}

    if candidate_type == "vault_fact":
        key = raw.get("key")
        if not key or key not in allowed_vault_keys:
            return drop(f"unknown vault key {key!r}")
        if "proposed_value" not in raw:
            return drop("vault_fact without proposed_value")
        value = _strip_placeholders(raw["proposed_value"])
        if value is None:
            return drop(f"vault_fact {key!r} stated no actual value")
        return DocumentFinding(
            candidate_type="vault_fact", key=key, proposed_value=value,
            content=None, entities={}, confidence=confidence, evidence=evidence,
        )

    if candidate_type == "student_record":
        from app.memory.errors import MemoryDataError
        from app.memory.student_records import ENTITY_MODELS
        from app.memory.student_schema import validate_record

        kind = raw.get("key")
        value = raw.get("proposed_value")
        if (not isinstance(kind, str) or kind not in ENTITY_MODELS
                or kind in ("course", "document") or not isinstance(value, dict)):
            return drop("invalid student record proposal")

        entities = raw.get("entities") if isinstance(raw.get("entities"), dict) else {}
        clean_entities = {}
        record_id = entities.get("record_id")
        if record_id is not None:
            if not isinstance(record_id, str) or record_id not in record_ids.get(kind, set()):
                return drop("record_id was not in this student's records")
            clean_entities["record_id"] = record_id

        value = _strip_placeholders(value)
        if not isinstance(value, dict) or not value:
            return drop(f"{kind} record stated no actual values")
        try:
            value = validate_record(kind, value, partial="record_id" in clean_entities)
        except MemoryDataError as exc:
            return drop(str(exc))
        return DocumentFinding(
            candidate_type="student_record", key=kind, proposed_value=value,
            content=None, entities=clean_entities, confidence=confidence,
            evidence=evidence,
        )

    content = raw.get("content")
    if not isinstance(content, str) or not content.strip():
        return drop(f"{candidate_type} without content")

    entities: dict = {}
    if candidate_type == "semantic_memory":
        memory_type = raw.get("memory_type", "context")
        if memory_type not in ("preference", "goal", "constraint", "interest", "context"):
            memory_type = "context"
        entities["memory_type"] = memory_type
    else:
        entities["event_type"] = str(raw.get("event_type") or "document_received")[:80]

    return DocumentFinding(
        candidate_type=candidate_type, key=None, proposed_value=None,
        content=content.strip()[:2000], entities=entities,
        confidence=confidence, evidence=evidence,
    )


async def understand_document(
    segments: list[Segment], *, filename: str, document_type: str,
    allowed_vault_keys: set[str], field_specs: list[dict],
    existing_records: dict,
) -> DocumentUnderstanding:
    """Classify and extract. Raises DocumentExtractionError on bad output."""
    from app.services.cloud_providers import chat_completion

    if not segments:
        return DocumentUnderstanding("unknown", 0.0, None, [])

    api_key, provider, model, base_url = _model_config()
    if not api_key:
        raise DocumentExtractionError("no document extraction API key configured")

    prompt = build_user_prompt(
        segments, filename=filename, document_type=document_type,
        field_specs=field_specs, existing_records=existing_records,
    )
    raw = await chat_completion(
        api_key=api_key, provider=provider, model=model,
        messages=[{"role": "user", "content": prompt}],
        system_prompt=SYSTEM_PROMPT, max_tokens=8000, base_url=base_url,
    )

    parsed = _parse_response(raw)

    classification = parsed.get("classification")
    if classification not in DOCUMENT_CLASSES:
        classification = "unknown"
    try:
        classification_confidence = max(0.0, min(1.0, float(
            parsed.get("classification_confidence", 0.0)
        )))
    except (TypeError, ValueError):
        classification_confidence = 0.0

    title = parsed.get("title")
    title = title.strip()[:200] if isinstance(title, str) and title.strip() else None

    findings_raw = parsed.get("findings")
    if findings_raw is None:
        raise DocumentExtractionError("output has no 'findings' key")
    if not isinstance(findings_raw, list):
        raise DocumentExtractionError("'findings' was not a list")
    if len(findings_raw) > MAX_CANDIDATES:
        logger.warning(
            "document extraction returned %d findings, truncating to %d",
            len(findings_raw), MAX_CANDIDATES,
        )
        findings_raw = findings_raw[:MAX_CANDIDATES]

    # Normalize ONCE: every quote is checked against this.
    document_text = _normalize("\n".join(s.text for s in segments))
    locators = {s.locator for s in segments}
    record_ids = {
        kind: {row.get("id") for row in rows if isinstance(row, dict)}
        for kind, rows in (existing_records or {}).items()
    }

    findings = [
        finding for finding in (
            _validate_finding(
                raw_finding, document_text=document_text, locators=locators,
                allowed_vault_keys=allowed_vault_keys, record_ids=record_ids,
            )
            for raw_finding in findings_raw
        ) if finding is not None
    ]

    return DocumentUnderstanding(
        classification=classification,
        classification_confidence=classification_confidence,
        title=title,
        findings=findings,
    )
