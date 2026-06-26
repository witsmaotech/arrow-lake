# Query Layer — 6 Bridges + DuckDB Session Manager

> Back-reference: [../SKILL.md](../SKILL.md) · parent: [architecture.md](architecture.md). Verified v1.7.0.

Arrow Lake answers every query shape from **one Lance dataset** through six isolated Bridge classes that share a single `DuckDBSessionManager`. There is no second OLAP engine and no per-mode dataset.

## The Six Bridges

| Bridge | SDK method | Index / engine | Use when |
|---|---|---|---|
| VectorSearch | `search()` | IVF_PQ / IVF_FLAT / IVF_HNSW_PQ | semantic / nearest-neighbor |
| FullTextSearch | `text_search()` | Tantivy (BM25), jieba CJK | keyword / exact-term |
| HybridSearch | `hybrid_search()` | RRF(vector, FTS) | best of both (needs vec **and** text) |
| FacetedSearch | `faceted_search()` | metadata columns | multi-dim filtering / counts |
| EnsembleSearch | `ensemble_search()` | cross-column RRF | multiple embedding columns |
| OlapQuery | `olap_query()` | DuckDB SQL | aggregations, JOINs, windows |

All six are **sync**; all six acquire connections from the same session manager.

## Vector Search

```python
lake.create_vector_index("docs", vector_column="text_embedding",
                         index_type="IVF_PQ", metric="cosine",
                         num_lists=100, num_bits=8)           # build once
result = lake.search("docs", query_vector, top_k=10,
                     metric="cosine",
                     vector_column="text_embedding",
                     where="category = 'ml'")                 # optional SQL filter
```

**Index types** (trade-off table):

| Index | Speed | Precision | Memory | When |
|---|---|---|---|---|
| `IVF_PQ` | fast | good | low | default, large datasets |
| `IVF_FLAT` | medium | high | high | small datasets, max recall |
| `IVF_HNSW_PQ` | fastest | good | highest | latency-critical, RAM-rich |

Manage with `list_vector_indexes`, `get_vector_index_info`, `rebuild_vector_index`, `delete_vector_index`. Rebuild after significant data growth or embedding-model change (prevents drift).

## Full-Text Search

```python
lake.create_fts_index("docs", fts_column="text")             # once per column
res = lake.text_search("docs", "机器学习 深度学习",
                       top_k=10, fts_column="text",
                       where=None, version=None)
```
Tantivy engine: BM25 ranking, English stemming, stop-word removal, **jieba** for CJK. Manage via `get_fts_index_info`, `delete_fts_index`.

## Hybrid Search (RRF)

```python
# REQUIRES both a vector and a text query
res = lake.hybrid_search("docs", query_vector, "机器学习",
                         top_k=10,
                         vector_column="text_embedding",
                         fts_column="text")
```

Reciprocal Rank Fusion:
```
score(d) = Σ_modes  w_mode / (k + rank_mode(d))
```
Default `k≈60`. `hybrid_search` is **not** a text-only call — omitting `query_vector` defeats the fusion. For text-only, use `text_search`.

## OLAP (DuckDB)

```python
# NO params= argument. Extra in-memory tables go via tables=
res = lake.olap_query("docs",
    "SELECT category, COUNT(*) AS c FROM docs GROUP BY category",
    max_rows=None, tables=None)
res.table.to_pandas()
```

Supports: aggregations, window functions, JOINs (pass extra Arrow tables via `tables={"t": tbl}`), streaming over large results. `sql_query()` is a semantic alias. For DuckLake materialized views: `materialize(...)` / `cleanup_materialized(ttl_days=None)`.

**SQL injection defense:** the layer applies a dangerous-keyword regex and identifier validation. Never bypass it with f-strings; never interpolate user input into `sql`.

## DuckDB Session Manager (unified pool)

All bridges go through `DuckDBSessionManager` (`query/session_manager.py`, ADR-08). One pool, one concurrency guard.

```python
manager = lake.get_session_manager()     # lazily created, cached on Lake
```

| Capability | Detail |
|---|---|
| Concurrency | semaphore (`max_concurrent_queries`) |
| Idle pool | health checks + timeout eviction |
| Zombie eviction | `max_session_lifetime_seconds=3600` |
| Per-conn governance | `memory_limit`, `statement_timeout`, `threads` |
| Retry | 1 attempt on `duckdb.Error` |
| Warmup | `OlapConfig.warmup_enabled` pre-loads datasets on cold start |
| Metrics | 8 Prometheus counters (pool active/evicted/health-checks) |

**Do not** create ad-hoc `duckdb.connect()` in bridge code — always acquire via the manager so the semaphore and zombie reaping apply. Migrating a new query mode = acquire from `get_session_manager()` like the others.

## Common Mistakes

- **Wrong method name**: `search` not `search_vector`; `text_search` not `search_fts`; `hybrid_search` not `search_hybrid`.
- **`hybrid_search` without the vector**: returns near-pure FTS; pass both.
- **`olap_query(..., params=...)`**: there is no such arg — use `tables=` or inline-safe SQL.
- **Per-query `duckdb.connect()`**: bypasses concurrency control; use the manager.
- **Forgetting to (re)build the index**: `search` over an unindexed column degrades to brute force.
