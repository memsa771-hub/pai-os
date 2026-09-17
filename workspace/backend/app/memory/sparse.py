# -*- coding: utf-8 -*-
"""Sparse (lexical) encoding boundary.

Exact terms carry most of the signal in this product: "IELTS 7.5", "TU Munich",
a program name, an application id, a date. Dense embeddings blur exactly those
— two different band scores embed almost identically — so sparse retrieval is
not an optimisation here, it is the half that gets identifiers right.

Uses FastEmbed's `Qdrant/bm25`, which emits term-frequency vectors that Qdrant
scores as real BM25 server-side when the collection's sparse vector is
configured with `Modifier.IDF`. The IDF component lives in Qdrant, which is why
we do not compute weights ourselves: an approximation here would disagree with
the corpus statistics Qdrant actually has.

Behind an interface so SPLADE (learned term expansion) can replace it when
retrieval evaluation justifies the extra dependency.
"""

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional

from app.config import config

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SparseVector:
    """Term ids and weights, in Qdrant's sparse-vector shape."""

    indices: list[int]
    values: list[float]

    def is_empty(self) -> bool:
        return not self.indices


class SparseEncoder(ABC):
    @property
    @abstractmethod
    def model_id(self) -> str:
        ...

    @property
    def available(self) -> bool:
        return True

    @abstractmethod
    def encode_documents(self, texts: list[str]) -> list[SparseVector]:
        ...

    @abstractmethod
    def encode_query(self, text: str) -> SparseVector:
        ...


class NullSparseEncoder(SparseEncoder):
    """No sparse encoding — hybrid degrades to dense-only."""

    @property
    def model_id(self) -> str:
        return "null"

    @property
    def available(self) -> bool:
        return False

    def encode_documents(self, texts: list[str]) -> list[SparseVector]:
        return [SparseVector([], []) for _ in texts]

    def encode_query(self, text: str) -> SparseVector:
        return SparseVector([], [])


class Bm25SparseEncoder(SparseEncoder):
    """FastEmbed Qdrant/bm25. Qdrant applies IDF at query time.

    `query_embed` differs from `embed` in BM25: the query side must not carry
    document term frequencies. Using the wrong one quietly degrades ranking,
    so the two paths are kept separate here rather than sharing a helper.
    """

    def __init__(self, model_name: str):
        self._model_name = model_name
        self._model = None

    @property
    def model_id(self) -> str:
        return self._model_name

    def _load(self):
        if self._model is None:
            from fastembed import SparseTextEmbedding

            self._model = SparseTextEmbedding(model_name=self._model_name)
        return self._model

    @property
    def available(self) -> bool:
        try:
            self._load()
            return True
        except Exception:
            logger.warning(
                "sparse: %s unavailable — hybrid degrades to dense-only",
                self._model_name, exc_info=True,
            )
            return False

    def encode_documents(self, texts: list[str]) -> list[SparseVector]:
        if not texts:
            return []
        return [
            SparseVector(list(map(int, e.indices)), list(map(float, e.values)))
            for e in self._load().embed(texts)
        ]

    def encode_query(self, text: str) -> SparseVector:
        for embedding in self._load().query_embed(text):
            return SparseVector(
                list(map(int, embedding.indices)), list(map(float, embedding.values))
            )
        return SparseVector([], [])


_encoder: Optional[SparseEncoder] = None


def get_sparse_encoder() -> SparseEncoder:
    global _encoder
    if _encoder is None:
        model = config.MEMORY_SPARSE_MODEL
        _encoder = Bm25SparseEncoder(model) if model else NullSparseEncoder()
    return _encoder


def set_sparse_encoder(encoder: Optional[SparseEncoder]) -> None:
    global _encoder
    _encoder = encoder
