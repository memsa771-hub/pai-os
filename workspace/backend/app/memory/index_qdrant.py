# -*- coding: utf-8 -*-
"""QdrantMemoryIndex — the derived hybrid retrieval index.

Qdrant holds NO canonical data. Every point is a pointer to a PostgreSQL row
plus whatever is needed to rank and filter it. Dropping the collection costs a
`memory.reindex`, never student memory.

Three invariants this module is responsible for:

**Workspace isolation is structural.** Every query carries a mandatory
`workspace_id` filter applied *before* ranking. There is no code path that
searches without it — `_workspace_filter` is not optional anywhere.

**Point ids are deterministic.** A point id is derived from the canonical row
id, so indexing is an idempotent UPSERT: a retried job overwrites rather than
duplicating.

**Qdrant is a hint, never an authority.** Hits are ids; the caller re-reads
PostgreSQL and drops anything inactive or missing. A stale point for a
forgotten memory therefore cannot surface it, even before deletion lands.
"""

import logging
import time
import uuid
from typing import Any, Optional

from app.config import config

from .index import MemoryIndex, MemoryRecord, SearchHit

logger = logging.getLogger(__name__)

DENSE_VECTOR = "dense"
SPARSE_VECTOR = "sparse"

# Stable namespace so a canonical row always maps to the same point id.
_POINT_NAMESPACE = uuid.UUID("6f1d8f6e-1f5e-4f0e-9a1f-2b7c9d3e4f50")


def point_id_for(workspace_id: str, kind: str, record_id: str) -> str:
    """Deterministic point id. Same row -> same point -> UPSERT, not duplicate.

    Includes the workspace so a (theoretically impossible) id collision across
    workspaces still lands on different points.
    """
    return str(uuid.uuid5(_POINT_NAMESPACE, f"{workspace_id}:{kind}:{record_id}"))


class QdrantUnavailable(RuntimeError):
    """Qdrant could not be reached. Retryable; never a data decision."""


class QdrantMemoryIndex(MemoryIndex):
    """Hybrid (dense + BM25 sparse) index over semantic memories and episodes."""

    def __init__(
        self,
        url: str,
        collection: str,
        api_key: Optional[str] = None,
        embedding_provider=None,
        sparse_encoder=None,
    ):
        self._url = url
        self._collection = collection
        self._api_key = api_key
        self._client = None
        self._ensured = False
        self._embeddings = embedding_provider
        self._sparse = sparse_encoder

    # -- wiring ------------------------------------------------------------

    @property
    def embeddings(self):
        if self._embeddings is None:
            from .embeddings import get_embedding_provider

            self._embeddings = get_embedding_provider()
        return self._embeddings

    @property
    def sparse(self):
        if self._sparse is None:
            from .sparse import get_sparse_encoder

            self._sparse = get_sparse_encoder()
        return self._sparse

    def _get_client(self):
        if self._client is None:
            try:
                from qdrant_client import AsyncQdrantClient
            except ImportError as exc:  # pragma: no cover
                raise QdrantUnavailable("qdrant-client is not installed") from exc
            self._client = AsyncQdrantClient(url=self._url, api_key=self._api_key)
        return self._client

    async def ensure_collection(self) -> None:
        """Create the collection if absent. Idempotent.

        The sparse vector uses `Modifier.IDF` so Qdrant computes real BM25
        server-side against actual corpus statistics — which is precisely what
        we cannot do correctly ourselves in the encoder.
        """
        if self._ensured:
            return
        from qdrant_client import models

        client = self._get_client()
        try:
            exists = await client.collection_exists(self._collection)
            if exists:
                await self._assert_compatible_dimensions(client)
            else:
                await client.create_collection(
                    collection_name=self._collection,
                    vectors_config={
                        DENSE_VECTOR: models.VectorParams(
                            size=self.embeddings.dimensions or config.MEMORY_EMBEDDING_DIM,
                            distance=models.Distance.COSINE,
                        )
                    },
                    sparse_vectors_config={
                        SPARSE_VECTOR: models.SparseVectorParams(
                            modifier=models.Modifier.IDF,
                        )
                    },
                )
                # Payload indexes: workspace_id is on the hot path of every
                # single query, so it must be indexed rather than scanned.
                for field, schema in (
                    ("workspace_id", models.PayloadSchemaType.KEYWORD),
                    ("kind", models.PayloadSchemaType.KEYWORD),
                    ("status", models.PayloadSchemaType.KEYWORD),
                    ("memory_type", models.PayloadSchemaType.KEYWORD),
                    ("event_type", models.PayloadSchemaType.KEYWORD),
                    # On every dense/sparse prefetch filter — must be indexed.
                    ("embedding_model", models.PayloadSchemaType.KEYWORD),
                    ("embedding_dim", models.PayloadSchemaType.INTEGER),
                    ("sparse_model", models.PayloadSchemaType.KEYWORD),
                ):
                    await client.create_payload_index(
                        collection_name=self._collection,
                        field_name=field, field_schema=schema,
                    )
                logger.info("qdrant: created collection %s", self._collection)
            self._ensured = True
        except Exception as exc:
            raise QdrantUnavailable(f"could not ensure collection: {exc}") from exc

    async def _assert_compatible_dimensions(self, client) -> None:
        """Refuse to use a collection built for a different vector size.

        Qdrant rejects a wrongly-sized upsert anyway, but the error is opaque
        and arrives once per job. Failing here says exactly what happened and
        what to do about it.
        """
        expected = int(self.embeddings.dimensions or config.MEMORY_EMBEDDING_DIM)
        if not expected:
            return
        try:
            info = await client.get_collection(self._collection)
            vectors = info.config.params.vectors
            actual = (
                vectors.get(DENSE_VECTOR).size
                if isinstance(vectors, dict) else getattr(vectors, "size", None)
            )
        except Exception:
            logger.debug("qdrant: could not read collection config", exc_info=True)
            return

        if actual is not None and int(actual) != expected:
            raise QdrantUnavailable(
                f"collection '{self._collection}' has dense dimension {actual} but the "
                f"configured embedding model ({self.embeddings.model_id}) produces "
                f"{expected}. Recreate the collection and run memory.reindex "
                f"(or point MEMORY_EMBEDDING_* back at the original model)."
            )

    # -- filters -----------------------------------------------------------

    @staticmethod
    def _workspace_filter(workspace_id: str, kinds=None, filters=None, extra=None):
        """Mandatory pre-ranking filter. Workspace is never optional.

        `filters` with a list value becomes MatchAny, so per-kind type filters
        (`memory_type` for memories, `event_type` for episodes) must be OR'd
        across kinds by the caller rather than AND'd here — an episode has no
        `memory_type` and would be excluded by a bare AND.
        """
        from qdrant_client import models

        must = [models.FieldCondition(
            key="workspace_id", match=models.MatchValue(value=workspace_id),
        )]
        if kinds:
            must.append(models.FieldCondition(
                key="kind", match=models.MatchAny(any=list(kinds)),
            ))
        for key, value in (filters or {}).items():
            if value is None:
                continue
            match = (
                models.MatchAny(any=list(value))
                if isinstance(value, (list, tuple, set))
                else models.MatchValue(value=value)
            )
            must.append(models.FieldCondition(key=key, match=match))
        for condition in (extra or []):
            must.append(condition)
        return models.Filter(must=must)

    def _dense_version_conditions(self):
        """Restrict dense search to points from the CURRENT embedding model.

        Cosine distance between vectors from two different models is
        meaningless — the spaces are unrelated. Without this, a model change
        silently degrades every ranking until a reindex happens to finish,
        with no error anywhere. Filtering means a half-reindexed collection
        returns fewer results rather than wrong ones.
        """
        from qdrant_client import models

        return [
            models.FieldCondition(
                key="embedding_model",
                match=models.MatchValue(value=self.embeddings.model_id),
            ),
            models.FieldCondition(
                key="embedding_dim",
                match=models.MatchValue(value=int(self.embeddings.dimensions)),
            ),
        ]

    def _sparse_version_conditions(self):
        """Same, for the sparse encoder's term-id space."""
        from qdrant_client import models

        return [models.FieldCondition(
            key="sparse_model", match=models.MatchValue(value=self.sparse.model_id),
        )]

    # -- write -------------------------------------------------------------

    async def index(self, records: list[MemoryRecord]) -> int:
        if not records:
            return 0
        if not self.embeddings.available:
            logger.info("qdrant: embeddings unavailable — skipping %d record(s)", len(records))
            return 0

        started = time.monotonic()
        await self.ensure_collection()

        from qdrant_client import models

        texts = [r.text for r in records]
        dense = await self.embeddings.embed_documents(texts)
        if not dense.vectors:
            return 0

        sparse_vectors = []
        if self.sparse.available:
            try:
                sparse_vectors = self.sparse.encode_documents(texts)
            except Exception:
                logger.warning("qdrant: sparse encoding failed — indexing dense only",
                               exc_info=True)

        points = []
        for i, record in enumerate(records):
            vector: dict[str, Any] = {DENSE_VECTOR: dense.vectors[i]}
            if i < len(sparse_vectors) and not sparse_vectors[i].is_empty():
                vector[SPARSE_VECTOR] = models.SparseVector(
                    indices=sparse_vectors[i].indices, values=sparse_vectors[i].values,
                )
            points.append(models.PointStruct(
                id=point_id_for(record.workspace_id, record.kind, record.id),
                vector=vector,
                payload={
                    "workspace_id": record.workspace_id,
                    "memory_id": record.id,
                    "kind": record.kind,
                    "text": record.text,
                    # Versioned so a model change is detectable and reindexable
                    # rather than silently mixing vector spaces.
                    "embedding_model": dense.model_id,
                    "embedding_dim": dense.dimensions,
                    "sparse_model": self.sparse.model_id,
                    **(record.filters or {}),
                },
            ))

        try:
            await self._get_client().upsert(
                collection_name=self._collection, points=points, wait=True,
            )
        except Exception as exc:
            raise QdrantUnavailable(f"upsert failed: {exc}") from exc

        logger.info(
            "qdrant: indexed count=%d workspace=%s elapsed_ms=%d",
            len(points), records[0].workspace_id, int((time.monotonic() - started) * 1000),
        )
        return len(points)

    async def delete(self, workspace_id: str, ids: list[str]) -> int:
        """Remove points for canonical ids. Both kinds, since a caller
        deleting an id does not necessarily know which table it came from."""
        if not ids:
            return 0
        from qdrant_client import models

        await self.ensure_collection()
        point_ids = [
            point_id_for(workspace_id, kind, record_id)
            for record_id in ids
            for kind in ("semantic_memory", "episode")
        ]
        try:
            await self._get_client().delete(
                collection_name=self._collection,
                points_selector=models.PointIdsList(points=point_ids),
                wait=True,
            )
        except Exception as exc:
            raise QdrantUnavailable(f"delete failed: {exc}") from exc

        logger.info("qdrant: deleted count=%d workspace=%s", len(ids), workspace_id)
        return len(ids)

    async def drop_workspace(self, workspace_id: str) -> None:
        """Remove every point for one workspace (used by reindex)."""
        from qdrant_client import models

        await self.ensure_collection()
        try:
            await self._get_client().delete(
                collection_name=self._collection,
                points_selector=models.FilterSelector(
                    filter=self._workspace_filter(workspace_id),
                ),
                wait=True,
            )
        except Exception as exc:
            raise QdrantUnavailable(f"workspace drop failed: {exc}") from exc

    # -- read --------------------------------------------------------------

    async def search(
        self,
        workspace_id: str,
        query: str,
        limit: int = 10,
        kinds: Optional[tuple[str, ...]] = None,
        filters: Optional[dict[str, Any]] = None,
    ) -> list[SearchHit]:
        """Hybrid dense + sparse retrieval, fused by Qdrant's native RRF.

        RRF rather than weighted score addition: dense cosine and BM25 live on
        different, un-normalised scales, so any fixed weighting would be an
        unevaluated guess. Rank-based fusion needs no calibration.
        """
        if not (query or "").strip():
            return []

        started = time.monotonic()
        await self.ensure_collection()

        from qdrant_client import models

        # Base filter (no version conditions): used for the fusion stage, which
        # only reorders what the arms already returned.
        query_filter = self._workspace_filter(workspace_id, kinds, filters)
        prefetch: list = []
        stages: list[str] = []

        if self.embeddings.available:
            dense_vector = await self.embeddings.embed_query(query)
            if dense_vector:
                prefetch.append(models.Prefetch(
                    query=dense_vector, using=DENSE_VECTOR,
                    # Each arm additionally restricts to ITS model version.
                    filter=self._workspace_filter(
                        workspace_id, kinds, filters,
                        extra=self._dense_version_conditions(),
                    ),
                    limit=limit,
                ))
                stages.append("dense")

        if self.sparse.available:
            try:
                sparse_vector = self.sparse.encode_query(query)
                if not sparse_vector.is_empty():
                    prefetch.append(models.Prefetch(
                        query=models.SparseVector(
                            indices=sparse_vector.indices, values=sparse_vector.values,
                        ),
                        using=SPARSE_VECTOR,
                        filter=self._workspace_filter(
                            workspace_id, kinds, filters,
                            extra=self._sparse_version_conditions(),
                        ),
                        limit=limit,
                    ))
                    stages.append("sparse")
            except Exception:
                logger.warning("qdrant: sparse query failed — dense only", exc_info=True)

        if not prefetch:
            logger.info("qdrant: no retrieval arm available for workspace=%s", workspace_id)
            return []

        try:
            response = await self._get_client().query_points(
                collection_name=self._collection,
                prefetch=prefetch,
                query=models.FusionQuery(fusion=models.Fusion.RRF),
                # Redundant with each prefetch's filter, deliberately: the
                # fusion stage must not be able to surface anything the arms
                # filtered out.
                query_filter=query_filter,
                limit=limit,
                with_payload=True,
            )
        except Exception as exc:
            raise QdrantUnavailable(f"query failed: {exc}") from exc

        hits = []
        for point in response.points:
            payload = point.payload or {}
            if payload.get("workspace_id") != workspace_id:
                # Defence in depth: never return a point whose payload
                # disagrees with the requested workspace.
                logger.error("qdrant: workspace mismatch in result — dropping point")
                continue
            hits.append(SearchHit(
                id=payload.get("memory_id", ""),
                kind=payload.get("kind", "semantic_memory"),
                score=float(point.score or 0.0),
                text=payload.get("text"),
                retrieval_source="hybrid" if len(stages) > 1 else (stages[0] if stages else "hybrid"),
            ))

        logger.info(
            "qdrant: query workspace=%s stages=%s candidates=%d elapsed_ms=%d",
            workspace_id, "+".join(stages), len(hits),
            int((time.monotonic() - started) * 1000),
        )
        return hits
