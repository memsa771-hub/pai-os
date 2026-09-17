# -*- coding: utf-8 -*-
"""Exact-fingerprint dedupe for proposed memories.

Turn N and turn N+3 will often produce the same sentence. Without this, "wants
to study in Germany" accumulates a row per mention and floods the context
budget with one repeated fact.

Strategy: normalize (casefold, strip punctuation, collapse whitespace) and
compare. Purely structural — no keyword lists, nothing domain-specific, so it
never needs updating as the product's vocabulary grows.

This catches restatements, not paraphrases: "prefers Germany" and "wants to
study in Germany" are different fingerprints. Semantic near-duplicate detection
needs embeddings and belongs to the retrieval phase; this is the layer that
works correctly without them, and it stays useful afterwards as the cheap
first pass.
"""

import hashlib
import logging
import re
import unicodedata

logger = logging.getLogger(__name__)

_PUNCT = re.compile(r"[^\w\s]", re.UNICODE)
_SPACE = re.compile(r"\s+")


def normalize_text(text: str) -> str:
    """Casefold, strip accents and punctuation, collapse whitespace."""
    if not text:
        return ""
    decomposed = unicodedata.normalize("NFKD", text)
    stripped = "".join(c for c in decomposed if not unicodedata.combining(c))
    return _SPACE.sub(" ", _PUNCT.sub(" ", stripped)).strip().casefold()


def fingerprint(*parts: str) -> str:
    """Stable hash of the normalized parts. Order matters; empties are kept so
    ("a", "") and ("", "a") differ."""
    joined = "\x1f".join(normalize_text(p) for p in parts)
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()[:32]


def memory_fingerprint(memory_type: str, content: str) -> str:
    return fingerprint(memory_type or "", content or "")


def episode_fingerprint(event_type: str, summary: str) -> str:
    return fingerprint(event_type or "", summary or "")


def is_duplicate_memory(db, workspace_id: str, memory_type: str, content: str) -> bool:
    """True if an active memory with this fingerprint already exists.

    Compares in Python rather than SQL because the fingerprint is not stored:
    adding a column would need a migration, and the active-memory set per
    workspace is small (tens, budget-capped at read time). Revisit if that
    stops being true.
    """
    from .semantic import MemoryService

    target = memory_fingerprint(memory_type, content)
    for existing in MemoryService(db).list_memories(workspace_id, limit=500):
        if memory_fingerprint(existing.memory_type, existing.content) == target:
            return True
    return False


def is_duplicate_episode(db, workspace_id: str, event_type: str, summary: str) -> bool:
    """True if an active episode with this fingerprint already exists."""
    from .episodic import EpisodicMemoryService

    target = episode_fingerprint(event_type, summary)
    for existing in EpisodicMemoryService(db).recent(workspace_id, limit=500):
        if episode_fingerprint(existing.event_type, existing.summary) == target:
            return True
    return False
