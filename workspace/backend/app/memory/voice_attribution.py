"""Conservative attribution checks for student intent in quoted proposals."""

import re


_OUTSIDE = re.compile(
    r"\b(?:my\s+(?:father|dad|mother|mom|mum|parents?|family|siblings?|brother|sister|friends?|teachers?|mentor)"
    r"|(?:his|her|their)\s+(?:father|mother|parents?|friends?)"
    r"|everyone|people|society|social\s+media|tiktok|instagram|youtube)\b", re.I)
_OWN_CHOICE = re.compile(
    r"\bi\s+(?:(?:also|really|personally|still|genuinely|actually)\s+)*"
    r"(?:want|prefer|choose|chose|would\s+(?:still\s+)?choose|would\s+(?:still\s+)?prefer|am\s+considering|"
    r"am\s+interested\s+in|like|love)\b([^.!?;]*)", re.I)
_OWN_INTEREST = re.compile(r"\bi\s+think\s+(.{1,80}?)\s+(?:is|sounds)\s+(?:interesting|cool|appealing)\b", re.I)
_UNCERTAIN = re.compile(r"\bi\s+(?:do\s+not|don't)\s+know\b|\bi(?:'m|\s+am)\s+(?:not\s+sure|unsure)\b", re.I)
_PRESSURE_ONLY = re.compile(r"\bi\s+only\s+want\b.{0,100}\bbecause\b.{0,100}\b(?:family|parents?|father|mother)\b", re.I)


def _normalized(value: str) -> str:
    return " ".join(str(value or "").casefold().replace("’", "'").split())


def _direction_terms(direction: str) -> set[str]:
    value = _normalized(direction)
    terms = {value}
    aliases = {
        "computer science": {"cs", "computer science"},
        "business administration": {"bba", "business administration"},
        "artificial intelligence": {"ai", "artificial intelligence"},
        "software engineering": {"software engineering", "se"},
    }
    for key, values in aliases.items():
        if key in value or any(re.search(r"\b" + re.escape(alias) + r"\b", value)
                               for alias in values):
            terms.update(values)
    return {term for term in terms if term}


def mentions_direction(text: str, direction: str) -> bool:
    content = _normalized(text)
    return any(re.search(r"\b" + re.escape(term) + r"\b", content)
               for term in _direction_terms(direction))


def personal_direction_supported(user_text: str, quote: str, direction: str,
                                 *, interest: bool = False) -> bool:
    """Require an attributable own clause when outside influence is present."""
    text = _normalized(user_text)
    excerpt = _normalized(quote)
    if _PRESSURE_ONLY.search(excerpt) or _PRESSURE_ONLY.search(text):
        return False
    if not _OUTSIDE.search(text) and not _UNCERTAIN.search(text):
        return True
    if not mentions_direction(excerpt, direction):
        return False
    for match in _OWN_CHOICE.finditer(text):
        if mentions_direction(match.group(1), direction):
            return True
    if interest:
        for match in _OWN_INTEREST.finditer(text):
            if mentions_direction(match.group(1), direction):
                return True
        # "AI sounds cool" is an own curiosity only when attributed to nobody else.
        for clause in re.split(r"[.!?;]|\bbut\b|\band\b", text):
            if (not _OUTSIDE.search(clause) and mentions_direction(clause, direction)
                    and re.search(r"\b(?:sounds cool|sounds interesting|is interesting)\b", clause)):
                return True
    return False


def external_source_supported(quote: str, source_label: str, direction: str) -> bool:
    aliases = {"father": ("father", "dad"), "mother": ("mother", "mom", "mum"),
               "social_media": ("social media", "tiktok", "instagram", "youtube"),
               "social expectation": ("everyone", "people", "society")}
    labels = aliases.get(_normalized(source_label), (_normalized(source_label),))
    return bool(_OUTSIDE.search(quote) and
                any(re.search(r"\b" + re.escape(label) + r"\b", _normalized(quote))
                    for label in labels if label) and mentions_direction(quote, direction))


def alignment_supported(quote: str, alignment: str) -> bool:
    text = _normalized(quote)
    markers = {
        "aligned": (r"\bi\s+(?:(?:also|really|personally|genuinely)\s+)+(?:want|prefer|like)\b",),
        "partially_aligned": (r"\bi\s+(?:partly|partially|somewhat)\b",),
        "uncertain": (r"\bi\s+(?:do\s+not|don't)\s+know\b", r"\bi(?:'m|\s+am)\s+(?:not\s+sure|unsure)\b"),
        "not_aligned": (r"\bi\s+(?:do\s+not|don't)\s+want\b", r"\bi\s+(?:disagree|would\s+not\s+choose)\b"),
    }
    return any(re.search(pattern, text) for pattern in markers.get(alignment, ()))
