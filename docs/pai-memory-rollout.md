# PAI Memory v1 — rollout

Foreground memory injection into PAI Counselor is **off by default**. Nothing
below is set in source; every value is deployment configuration.

Background memory formation — extraction, reconciliation, indexing — runs
regardless of these flags. A workspace therefore accumulates canonical memory
before injection is switched on, so enabling it needs no backfill.

PostgreSQL is canonical in every mode. Qdrant is a derived index; losing it
costs a `memory.reindex`, never data.

## Mode 0 — default (current)

No configuration. Memory is formed and stored; the Counselor prompt is
unchanged. This is what ships until a pilot is deliberately started.

## Mode 1 — safe fallback pilot

Injection on, no vector backend. Retrieval uses PostgreSQL structured Vault
reads plus ILIKE lexical search — no embedding provider, no Qdrant, no new
infrastructure and no per-turn embedding cost.

```bash
PAI_MEMORY_CONTEXT_ENABLED=true
# MEMORY_VECTOR_BACKEND deliberately unset
```

Good first pilot: it exercises the whole prompt contract (precedence,
untrusted-data envelope, budget, timeout) with the smallest possible blast
radius. Recall is weaker than hybrid — paraphrases like "which country was I
leaning toward?" will often miss a memory worded differently.

## Mode 2 — hybrid pilot

Adds dense + BM25 retrieval through Qdrant.

```bash
PAI_MEMORY_CONTEXT_ENABLED=true
MEMORY_VECTOR_BACKEND=qdrant
QDRANT_URL=http://qdrant:6333
QDRANT_API_KEY=            # if the deployment requires one
MEMORY_EMBEDDING_MODEL=text-embedding-3-small
MEMORY_EMBEDDING_API_KEY=  # embeddings key; NOT assumed from PAI_API_KEY
MEMORY_EMBEDDING_DIM=1536
```

`MEMORY_EMBEDDING_API_KEY` is separate on purpose: a chat-model key does not
necessarily serve an embeddings route, and silently reusing it turns a config
mistake into a failing job on every indexed memory.

After first enabling, run `memory.reindex` per workspace to populate the
collection from PostgreSQL.

## Tunables

| Variable | Default | Meaning |
|---|---|---|
| `PAI_MEMORY_CONTEXT_ENABLED` | `false` | Foreground injection master switch |
| `PAI_MEMORY_CONTEXT_TIMEOUT_MS` | `1500` | **Total** foreground budget, all tiers |
| `PAI_MEMORY_CONTEXT_MAX_CHARS` | `2500` | Hard cap on the rendered block, delimiters included |
| `PAI_MEMORY_FOREGROUND_WORKERS` | `4` | Threads for foreground memory DB work |
| `PAI_MEMORY_FOREGROUND_MAX_INFLIGHT` | `8` | Cap on concurrent (incl. abandoned) foreground DB operations |
| `MEMORY_RETRIEVAL_CANDIDATES` | `40` | Hybrid candidate pool |
| `MEMORY_RETRIEVAL_LIMIT` | `8` | Results after rerank |
| `MEMORY_RERANKER` | `identity` | `identity` \| `importance` |

All of these are passed through `workspace/docker-compose.yml`, and a test
(`tests/memory/test_foreground_runtime.py`) fails if one is dropped — they were
once documented here without being plumbed through, so setting them did
nothing.

### Why foreground work runs on its own thread pool

Foreground retrieval ends in synchronous SQLAlchemy. `asyncio.wait_for` bounds
awaits, not blocking calls, so a stalled PostgreSQL would freeze the event loop
inside a single await and the deadline would bound nothing. That work therefore
runs on a small dedicated pool (`PAI_MEMORY_FOREGROUND_WORKERS`), and because a
thread cannot be killed, `PAI_MEMORY_FOREGROUND_MAX_INFLIGHT` caps how much
abandoned DB work can accumulate. Past that limit the request sheds
(`mode=busy`) rather than queueing behind stuck threads.

### Reading the telemetry

`ForegroundContext.mode` reports what retrieval actually did, propagated from
the retriever rather than inferred from configuration:

`hybrid` · `lexical_fallback` · `empty` · `timeout` · `busy` · `error` · `none`

In Mode 1 expect `lexical_fallback`. Seeing `hybrid` there would mean a vector
backend is configured when you did not intend one.

## Verifying a pilot

```bash
# retrieval quality (needs Qdrant; --real also needs embeddings)
python -m app.memory.eval_retrieval --real

# Counselor behaviour with injected memory (calls a real LLM, never in CI)
python -m app.memory.eval_behavior --verbose
```

Both print a pass/fail report. Neither contains production student data.

## Rolling back

Set `PAI_MEMORY_CONTEXT_ENABLED=false`. The Counselor prompt reverts to its
previous shape on the next turn. Stored memory is untouched, so re-enabling
later resumes with everything learned in the meantime.
