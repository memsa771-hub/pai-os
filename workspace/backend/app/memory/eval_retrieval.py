# -*- coding: utf-8 -*-
"""Offline retrieval evaluation.

    python -m app.memory.eval_retrieval

Runs the labelled dataset through the REAL `MemoryRetriever` — the same code
path production uses, not a reimplementation — and reports Recall/MRR/HitRate.

Two modes:

  fake   deterministic hash embeddings, no network. Used by CI so the harness
         itself is regression-tested without external dependencies.
  real   the configured MEMORY_EMBEDDING_* provider and Qdrant. Requires
         credentials; exits with instructions when they are absent rather
         than failing.

Metrics are computed here with plain arithmetic — no numpy/sklearn — because
the harness should never be the reason CI needs a heavier dependency.

Never prints student content beyond the synthetic dataset checked in here.
"""

import argparse
import asyncio
import logging
import os
import sys
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Optional

from app.memory.eval_dataset import CASES, EPISODES, MEMORIES, EvalCase

logger = logging.getLogger(__name__)

# Initial engineering gates. NOT universal claims about retrieval quality, and
# deliberately not enforced anywhere in runtime code.
DEFAULT_TARGET_RECALL_AT_5 = 0.90
DEFAULT_TARGET_HITRATE_AT_5 = 0.90


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------

def recall_at_k(retrieved: list[str], relevant: set[str], k: int) -> Optional[float]:
    """Fraction of relevant ids found in the top k. None when nothing is
    relevant (a pure negative case contributes to forbidden checks instead)."""
    if not relevant:
        return None
    return len(set(retrieved[:k]) & relevant) / len(relevant)


def hit_at_k(retrieved: list[str], relevant: set[str], k: int) -> Optional[float]:
    """1.0 if ANY relevant id is in the top k."""
    if not relevant:
        return None
    return 1.0 if set(retrieved[:k]) & relevant else 0.0


def reciprocal_rank(retrieved: list[str], relevant: set[str]) -> Optional[float]:
    if not relevant:
        return None
    for position, record_id in enumerate(retrieved, start=1):
        if record_id in relevant:
            return 1.0 / position
    return 0.0


def _mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


@dataclass
class CaseResult:
    case: EvalCase
    retrieved: list[str]
    mode: str
    recall_3: Optional[float] = None
    recall_5: Optional[float] = None
    hit_3: Optional[float] = None
    hit_5: Optional[float] = None
    mrr: Optional[float] = None
    forbidden_hits: tuple[str, ...] = ()

    @property
    def passed(self) -> bool:
        """A case passes when nothing forbidden appears AND, if it expects
        something, at least one relevant id is in the top 5."""
        if self.forbidden_hits:
            return False
        if not self.case.relevant:
            return True
        return bool(self.hit_5)


@dataclass
class EvalReport:
    results: list[CaseResult] = field(default_factory=list)
    embedding_model: str = "unknown"
    sparse_model: str = "unknown"

    def _collect(self, attribute: str) -> list[float]:
        return [
            getattr(r, attribute) for r in self.results
            if getattr(r, attribute) is not None
        ]

    @property
    def recall_3(self) -> float:
        return _mean(self._collect("recall_3"))

    @property
    def recall_5(self) -> float:
        return _mean(self._collect("recall_5"))

    @property
    def hit_3(self) -> float:
        return _mean(self._collect("hit_3"))

    @property
    def hit_5(self) -> float:
        return _mean(self._collect("hit_5"))

    @property
    def mrr(self) -> float:
        return _mean(self._collect("mrr"))

    @property
    def mode_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for result in self.results:
            counts[result.mode] = counts.get(result.mode, 0) + 1
        return counts

    @property
    def leaks(self) -> list[CaseResult]:
        """Cases returning something forbidden. Any leak is a hard failure."""
        return [r for r in self.results if r.forbidden_hits]

    def by_category(self) -> dict[str, dict]:
        grouped: dict[str, list[CaseResult]] = {}
        for result in self.results:
            grouped.setdefault(result.case.category, []).append(result)
        return {
            category: {
                "cases": len(items),
                "passed": sum(1 for r in items if r.passed),
                "recall_5": _mean([r.recall_5 for r in items if r.recall_5 is not None]),
                "hit_5": _mean([r.hit_5 for r in items if r.hit_5 is not None]),
            }
            for category, items in sorted(grouped.items())
        }

    def meets_gates(
        self,
        target_recall_5: float = DEFAULT_TARGET_RECALL_AT_5,
        target_hit_5: float = DEFAULT_TARGET_HITRATE_AT_5,
    ) -> bool:
        return (
            not self.leaks
            and self.recall_5 >= target_recall_5
            and self.hit_5 >= target_hit_5
        )

    def render(self, target_recall_5: float, target_hit_5: float) -> str:
        lines = [
            "=" * 66,
            "PAI memory retrieval evaluation",
            "=" * 66,
            f"cases            {len(self.results)}",
            f"embedding model  {self.embedding_model}",
            f"sparse model     {self.sparse_model}",
            "",
            f"Recall@3         {self.recall_3:.3f}",
            f"Recall@5         {self.recall_5:.3f}",
            f"HitRate@3        {self.hit_3:.3f}",
            f"HitRate@5        {self.hit_5:.3f}",
            f"MRR              {self.mrr:.3f}",
            "",
            "retrieval modes  " + ", ".join(
                f"{mode}={count}" for mode, count in sorted(self.mode_counts.items())
            ),
            "",
            "by category:",
        ]
        for category, stats in self.by_category().items():
            lines.append(
                f"  {category:<14} {stats['passed']}/{stats['cases']} passed   "
                f"recall@5={stats['recall_5']:.2f}  hit@5={stats['hit_5']:.2f}"
            )

        failures = [r for r in self.results if not r.passed]
        if failures:
            lines.extend(["", f"failing cases ({len(failures)}):"])
            for result in failures:
                reason = (
                    f"LEAKED {list(result.forbidden_hits)}" if result.forbidden_hits
                    else f"missed {list(result.case.relevant)}"
                )
                lines.append(f"  [{result.case.category}] {result.case.name}: {reason}")

        lines.extend([
            "",
            f"gate Recall@5  >= {target_recall_5:.2f}   "
            f"{'PASS' if self.recall_5 >= target_recall_5 else 'FAIL'}",
            f"gate HitRate@5 >= {target_hit_5:.2f}   "
            f"{'PASS' if self.hit_5 >= target_hit_5 else 'FAIL'}",
            f"isolation leaks: {len(self.leaks)}"
            + ("  (must be 0)" if self.leaks else ""),
            "=" * 66,
        ])
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Fake providers (CI mode)
# ---------------------------------------------------------------------------

class HashEmbeddings:
    """Deterministic bag-of-tokens vectors. No network, stable across runs.

    Good enough to exercise the dense ARM and the fusion plumbing; it is not a
    language model, so paraphrase cases lean on sparse/lexical here. Real
    quality numbers come from `--real`.
    """

    DIM = 64

    @property
    def model_id(self) -> str:
        return "hash:eval"

    @property
    def dimensions(self) -> int:
        return self.DIM

    @property
    def available(self) -> bool:
        return True

    def _vector(self, text: str) -> list[float]:
        import hashlib

        vector = [0.0] * self.DIM
        for token in _tokens(text):
            digest = hashlib.sha256(token.encode()).digest()
            vector[digest[0] % self.DIM] += 1.0
        norm = sum(v * v for v in vector) ** 0.5 or 1.0
        return [v / norm for v in vector]

    async def embed_documents(self, texts):
        from app.memory.embeddings import EmbeddingResult

        return EmbeddingResult([self._vector(t) for t in texts], self.model_id, self.DIM)

    async def embed_query(self, text):
        return self._vector(text)


def _tokens(text: str) -> list[str]:
    import re

    return [t for t in re.split(r"[^\w.\-]+", (text or "").lower()) if t]


# ---------------------------------------------------------------------------
# Harness
# ---------------------------------------------------------------------------

async def _seed(db, index) -> None:
    """Write the dataset to PostgreSQL, then index the active rows."""
    from app.memory.episodic import EpisodicMemoryService
    from app.memory.handlers import _records_for
    from app.memory.semantic import MemoryService
    from app.models import PaiEpisode, PaiMemory

    from app.models import Workspace

    # The memory tables carry an FK to `workspaces`, so the evaluation
    # workspaces must exist. Created if absent and left behind — they hold
    # only the synthetic dataset.
    for workspace_id in {s.workspace_id for s in MEMORIES} | {
        s.workspace_id for s in EPISODES
    }:
        if db.get(Workspace, workspace_id) is None:
            db.add(Workspace(
                id=workspace_id,
                name=f"Eval {workspace_id[:8]}",
                slug=f"eval-{workspace_id[:8]}",
                password_hash=uuid.uuid4().hex,
            ))
    db.commit()

    # Clear any previous run's rows: the fingerprint uniqueness index would
    # otherwise collapse the second seeding into the first run's ids.
    workspace_ids = list(
        {s.workspace_id for s in MEMORIES} | {s.workspace_id for s in EPISODES}
    )
    db.query(PaiMemory).filter(PaiMemory.workspace_id.in_(workspace_ids)).delete(
        synchronize_session=False
    )
    db.query(PaiEpisode).filter(PaiEpisode.workspace_id.in_(workspace_ids)).delete(
        synchronize_session=False
    )
    db.commit()

    memories = MemoryService(db)
    episodes = EpisodicMemoryService(db)
    id_map: dict[str, str] = {}

    for spec in MEMORIES:
        row = memories.create(
            workspace_id=spec.workspace_id, content=spec.content,
            memory_type=spec.memory_type, importance=spec.importance,
        )
        id_map[spec.id] = row.id
    for spec in EPISODES:
        row = episodes.record(
            workspace_id=spec.workspace_id, event_type=spec.event_type,
            summary=spec.summary, importance=spec.importance,
            occurred_at=datetime.now(timezone.utc) - timedelta(days=spec.days_ago),
        )
        id_map[spec.id] = row.id
    db.commit()

    # Index everything FIRST, including rows about to be forgotten — that is
    # what makes the forgotten cases a real staleness test rather than a
    # trivial absence.
    for workspace_id in {s.workspace_id for s in MEMORIES} | {s.workspace_id for s in EPISODES}:
        memory_ids = [
            id_map[s.id] for s in MEMORIES if s.workspace_id == workspace_id
        ]
        episode_ids = [
            id_map[s.id] for s in EPISODES if s.workspace_id == workspace_id
        ]
        records = _records_for(db, workspace_id, memory_ids, episode_ids)
        if records:
            await index.index(records)

    for spec in MEMORIES:
        if spec.forgotten:
            row = db.get(PaiMemory, id_map[spec.id])
            row.status = "forgotten"
    for spec in EPISODES:
        if spec.forgotten:
            row = db.get(PaiEpisode, id_map[spec.id])
            row.status = "forgotten"
    db.commit()

    _seed.id_map = id_map


async def run_evaluation(db, index=None, limit: int = 5) -> EvalReport:
    """Evaluate every case through the real retriever."""
    from app.memory.retriever import MemoryRetriever

    if index is None:
        from app.memory.index import get_memory_index

        index = get_memory_index()

    await _seed(db, index)
    id_map = _seed.id_map
    reverse = {v: k for k, v in id_map.items()}

    report = EvalReport(
        embedding_model=getattr(index, "embeddings", None)
        and index.embeddings.model_id or "unknown",
        sparse_model=getattr(index, "sparse", None)
        and index.sparse.model_id or "unknown",
    )

    retriever = MemoryRetriever(db, index=index)
    for case in CASES:
        result = await retriever.retrieve(
            workspace_id=case.workspace_id, query=case.query, limit=limit,
        )
        # `ordered` — the true global ranking. `memories + episodes` would put
        # every episode after every memory regardless of how they actually
        # ranked, quietly corrupting MRR and any Recall@k below the total.
        retrieved = [reverse.get(item.id, item.id) for item in result.ordered]
        relevant = set(case.relevant)
        forbidden = set(case.forbidden)

        report.results.append(CaseResult(
            case=case,
            retrieved=retrieved,
            mode=result.mode,
            recall_3=recall_at_k(retrieved, relevant, 3),
            recall_5=recall_at_k(retrieved, relevant, 5),
            hit_3=hit_at_k(retrieved, relevant, 3),
            hit_5=hit_at_k(retrieved, relevant, 5),
            mrr=reciprocal_rank(retrieved, relevant),
            forbidden_hits=tuple(i for i in retrieved if i in forbidden),
        ))
    return report


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _build_real_index(collection: str):
    from app.config import config
    from app.memory.embeddings import get_embedding_provider
    from app.memory.index_qdrant import QdrantMemoryIndex

    provider = get_embedding_provider()
    if not provider.available:
        print(
            "Real evaluation needs an embedding provider.\n\n"
            "  export MEMORY_EMBEDDING_API_KEY=...\n"
            "  export MEMORY_EMBEDDING_MODEL=text-embedding-3-small\n"
            "  export MEMORY_EMBEDDING_DIM=1536\n\n"
            "Run with --fake for the deterministic offline evaluation.",
            file=sys.stderr,
        )
        return None
    if not config.QDRANT_URL:
        print("Real evaluation needs QDRANT_URL set.", file=sys.stderr)
        return None

    return QdrantMemoryIndex(
        url=config.QDRANT_URL, collection=collection,
        api_key=config.QDRANT_API_KEY or None,
    )


async def _main_async(args) -> int:
    from app.database import new_session

    collection = f"pai_eval_{uuid.uuid4().hex[:8]}"

    if args.real:
        index = _build_real_index(collection)
        if index is None:
            return 2
    else:
        from app.memory.index_qdrant import QdrantMemoryIndex
        from app.memory.sparse import get_sparse_encoder
        from app.config import config

        if not config.QDRANT_URL:
            print(
                "Evaluation needs a Qdrant instance.\n"
                "  export QDRANT_URL=http://localhost:6333",
                file=sys.stderr,
            )
            return 2
        index = QdrantMemoryIndex(
            url=config.QDRANT_URL, collection=collection,
            api_key=config.QDRANT_API_KEY or None,
            embedding_provider=HashEmbeddings(),
            sparse_encoder=get_sparse_encoder(),
        )

    db = new_session()
    try:
        report = await run_evaluation(db, index=index, limit=args.limit)
        print(report.render(args.target_recall, args.target_hitrate))
        return 0 if report.meets_gates(args.target_recall, args.target_hitrate) else 1
    finally:
        db.close()
        try:
            await index._get_client().delete_collection(collection)
        except Exception:
            pass


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate PAI memory retrieval.")
    parser.add_argument("--real", action="store_true",
                        help="use the configured embedding provider instead of fakes")
    parser.add_argument("--fake", action="store_true",
                        help="force deterministic offline embeddings (default)")
    parser.add_argument("--limit", type=int, default=5)
    parser.add_argument("--target-recall", type=float,
                        default=DEFAULT_TARGET_RECALL_AT_5)
    parser.add_argument("--target-hitrate", type=float,
                        default=DEFAULT_TARGET_HITRATE_AT_5)
    args = parser.parse_args()

    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "WARNING"),
        format="%(levelname)s %(name)s %(message)s",
    )
    return asyncio.run(_main_async(args))


if __name__ == "__main__":
    raise SystemExit(main())
