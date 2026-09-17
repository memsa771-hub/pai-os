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

    An indexed lookup on (workspace_id, status, fingerprint), not a scan: the
    previous implementation fingerprinted up to 500 rows in Python for every
    proposed candidate, which grew with the student's history.
    """
    from sqlalchemy import select

    from app.models import PaiMemory

    return db.execute(
        select(PaiMemory.id).where(
            PaiMemory.workspace_id == workspace_id,
            PaiMemory.status == "active",
            PaiMemory.fingerprint == memory_fingerprint(memory_type, content),
        ).limit(1)
    ).scalar_one_or_none() is not None


def is_duplicate_episode(db, workspace_id: str, event_type: str, summary: str) -> bool:
    """True if an active episode with this fingerprint already exists."""
    from sqlalchemy import select

    from app.models import PaiEpisode

    return db.execute(
        select(PaiEpisode.id).where(
            PaiEpisode.workspace_id == workspace_id,
            PaiEpisode.status == "active",
            PaiEpisode.fingerprint == episode_fingerprint(event_type, summary),
        ).limit(1)
    ).scalar_one_or_none() is not None
