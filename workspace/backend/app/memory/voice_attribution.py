"""Language-neutral ownership checks for model-extracted student claims.

The model interprets speech. This module only checks its structured output and
that any separately attributed student clause is present in the owner turn.
"""

CLAIM_OWNERS = frozenset({"student", "external", "mixed", "uncertain"})
STUDENT_OWNED = frozenset({"goal", "student_voice_statement", "career.primary_interest"})


def contained(excerpt: str, message: str) -> bool:
    return bool(isinstance(excerpt, str) and excerpt.strip() and
                " ".join(excerpt.casefold().split()) in
                " ".join(str(message or "").casefold().split()))


def validated_attribution(raw: object, *, kind: str, quote: str,
                          message: str, voice_type: str | None = None) -> dict | None:
    """Return safe attribution or reject a missing/ambiguous ownership claim."""
    if (not isinstance(raw, dict) or not isinstance(raw.get("claim_owner"), str)
            or raw["claim_owner"] not in CLAIM_OWNERS):
        return None
    owner = raw["claim_owner"]
    if kind == "external_influence":
        if owner not in {"external", "mixed"}:
            return None
    elif kind in STUDENT_OWNED:
        if owner == "student":
            pass
        elif owner == "mixed":
            clause = raw.get("student_clause_quote")
            if not contained(clause, quote) or not contained(clause, message):
                return None
        elif kind == "student_voice_statement" and owner == "uncertain" and voice_type == "uncertainty":
            pass
        else:
            return None
    result = {"claim_owner": owner}
    if owner == "mixed":
        clause = raw.get("student_clause_quote")
        if isinstance(clause, str) and contained(clause, message):
            result["student_clause_quote"] = clause
    alignment_quote = raw.get("alignment_quote")
    if kind == "external_influence" and isinstance(alignment_quote, str) and contained(alignment_quote, quote):
        result["alignment_quote"] = alignment_quote
    return result
