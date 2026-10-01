"""Conservative server authorization for owner-authored memory commands.

Models may propose an operation, but cannot grant themselves command trust.
Ambiguous, quoted, conditional and negated language stays ordinary extraction.
"""
import re


def _normal(text):
    return re.sub(r"\s+", " ", str(text or "")).strip().casefold()


def command_target(text, operation):
    """Return the target of a narrow imperative, never a substring command."""
    if "\n" in str(text or ""):
        return None
    text = _normal(text).rstrip(".")
    if any(token in text for token in ("?", "\n", '"', "“", "”", ";")):
        return None
    if re.search(r"\b(?:not|never|don't|dont|except|unless|if|keep|but|nahi|nahin|mat|agar)\b", text):
        return None
    prefix = r"^(?:actually[, ]+)?(?:please\s+)?"
    if operation == "upsert":
        match = re.fullmatch(prefix + r"(?:correct|change|update)\s+(?:my\s+)?(.+?)\s+to\s+(.+)", text)
    else:
        match = re.fullmatch(prefix + r"(?:forget|retract|remove)\s+(?:my\s+|about\s+)?(.+)", text)
    if not match and operation == "upsert":
        match = re.fullmatch(r"(?:please\s+)?(?:mera|meri|mere)\s+(.+?)\s+(?:ko\s+)?(.+?)\s+(?:kar do|kardo|kar dein|kardein)", text)
    elif not match:
        match = re.fullmatch(r"(?:please\s+)?(?:mera|meri|mere)\s+(.+?)\s+(?:bhool jao|bhool jaen|bhool jaein|hata do|hata dein)", text)
    if not match:
        return None
    target = match.group(1).strip()
    # Do not let one model-selected target consume a compound command.
    if re.search(r"\b(?:and|or)\b", target):
        return None
    return target


def authorizes_fact(text, operation, key, current_value=None, proposed_value=None):
    target = command_target(text, operation)
    if not target or not isinstance(key, str):
        return False
    if operation == "upsert" and isinstance(proposed_value, dict):
        # An old amount mentioned before "to" is not the replacement amount.
        from .extractor import _money_is_evidenced
        normalized = _normal(text)
        replacement = normalized.split(" to ", 1)[-1]
        if " to " not in normalized:
            match = re.fullmatch(r"(?:please\s+)?(?:mera|meri|mere)\s+(.+?)\s+(?:ko\s+)?(.+?)\s+(?:kar do|kardo|kar dein|kardein)", normalized.rstrip("."))
            replacement = match.group(2) if match else ""
        if any(not _money_is_evidenced(proposed_value.get(field), replacement)
               for field in ("amount", "value", "tuition", "cost")):
            return False
    label = key.rsplit(".", 1)[-1].replace("_", " ")
    aliases = {key.casefold(), label.casefold()}
    if key == "preferences.target_countries":
        aliases.update({"countries", "country preference", "country preferences", "target countries"})
        # A single named preference can retract the field only if this cannot
        # also remove other, unmentioned destinations.
        if operation != "upsert" and isinstance(current_value, list) and len(current_value) == 1:
            country = _normal(current_value[0])
            aliases.update({country, country + " preference"})
    return target in aliases


def authorizes_memory_forget(text, content):
    target = command_target(text, "forget")
    query = _normal(content)
    if any(char in query for char in "%_"):
        return False
    if not target or not query or len(query) < 3 or query in {"all", "everything", "memory", "memories"}:
        return False
    # Preserve the user's exact named search scope. Never accept a model's
    # broader subset ("my Canada scholarship" -> "Canada").
    return target == query or target == query + " preference"
