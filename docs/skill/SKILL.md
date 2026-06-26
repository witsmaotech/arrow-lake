---
name: arrow-lake
description: Use when working with the Arrow Lake v1.7.x multimodal data lakehouse — the Lake SDK facade, REST API, CLI, or any of its subsystems (vector/full-text/hybrid search, DuckDB OLAP, RAG, knowledge graph, ingestion, data quality, lineage/audit, production deployment). Trigger on SDK method calls, FastAPI router work, dataset ops, async RAG/KG calls, DuckDB session pool, Gravitino governance, Ray/Helm/Docker deploy, or v1.7.x behaviors (doc-type routing, hyper-extract KG, HugeGraph PD cluster, fire-and-forget kg_build, Redis task store, HugeGraph Gremlin).
license: MIT
user-invocable: true
metadata:
  version: 1.0.0
  arrow_lake_version: 1.7.0
  author: Witshine
  domains: [data-lakehouse, vector-search, rag, knowledge-graph, olap]
---

# Arrow Lake Skill

Production-grade multimodal data lakehouse — text, images, audio, vectors, knowledge graphs on one Lance store. **v1.7.0.**

> **One dataset, every query mode. Zero ETL, zero glue code.** PostgreSQL + Elasticsearch + Milvus + Redis + Neo4j + DuckDB + S3 pipelines collapse into one platform.

## When to Use

Use this skill when the task touches Arrow Lake — the `Lake` SDK, the FastAPI layer, the CLI, or any subsystem below. **Before writing any Arrow Lake code, check the real API in [references/architecture.md](references/architecture.md) — v1.6.3 renamed many methods and made RAG/KG async.**

| If the task is… | Read this reference first |
|---|---|
| Facade / mixins / config / protocols / graceful degradation | [references/architecture.md](references/architecture.md) |
| Vector / FTS / hybrid / faceted / ensemble / OLAP / DuckDB session pool | [references/query-layer.md](references/query-layer.md) |
| RAG pipeline, LLM providers, citations, streaming, HyDE, reranking | [references/rag-pipeline.md](references/rag-pipeline.md) |
| Knowledge graph build, Gremlin, GraphRAG, HugeGraph/Vermeer | [references/knowledge-graph.md](references/knowledge-graph.md) |
| Multi-source ingestion, 5 chunking strategies, quality filters, dedup | [references/ingestion-quality.md](references/ingestion-quality.md) |
| Docker Compose, Helm, RBAC, auth, observability, nginx, security | [references/deployment.md](references/deployment.md) |

**Do NOT use Arrow Lake for:** single-database CRUD (use the DB driver directly), pure key-value caching (Redis alone), or batch ETL between heterogeneous stores (Airflow/dbt).

## The Lake Facade — Real API (v1.7.0)

`Lake` composes 9 mixins. **RAG and KG methods are `async` (must `await`)**. `kg_build` is fire-and-forget and returns a `task_id`.

```python
from arrow_lake import Lake

with Lake("./data") as lake:                       # context manager → graceful shutdown
    import pyarrow as pa
    lake.create_dataset("docs", pa.table({...}))   # primary write entry (sync)

    # Search (all sync)
    lake.search("docs", query_vector, top_k=10)            # vector ANN (NOT search_vector)
    lake.text_search("docs", "机器学习", top_k=10)         # Tantivy BM25 (NOT search_fts)
    lake.hybrid_search("docs", vec, "机器学习", top_k=10)  # RRF (NOT search_hybrid; needs vec + text)

    # OLAP (sync) — NO params arg; pass extra tables via `tables=`
    lake.olap_query("docs", "SELECT category, COUNT(*) FROM docs GROUP BY category")

    # Quality (sync)
    lake.quality_filter("docs", "dedup,null_check")        # → QualityReport
    lake.deduplicate("docs", strategy="exact")             # → DedupResult

    # RAG + KG are ASYNC
    import asyncio
    answer = asyncio.run(lake.rag_query("What is RAG?", "docs", top_k=5))
    task_id = asyncio.run(lake.kg_build("docs"))           # returns task_id, non-blocking
```

For the CLI and REST API equivalents, see the Quick Reference below and [references/deployment.md](references/deployment.md).

## Quick Reference

| Operation | SDK (sync unless noted) | CLI | REST |
|---|---|---|---|
| Init | `Lake("./data")` / `Lake.from_yaml("c.yaml")` | `arrow-lake` | — |
| Create dataset | `lake.create_dataset(name, table)` | `arrow-lake ingest files …` | `POST /api/v1/datasets/{name}` |
| Vector search | `lake.search(name, vec, top_k)` | `arrow-lake search vector …` | `POST /api/v1/search/vector` |
| Full-text search | `lake.text_search(name, q)` | `arrow-lake search fts …` | `POST /api/v1/search/fts` |
| Hybrid search | `lake.hybrid_search(name, vec, q)` | `arrow-lake search hybrid …` | `POST /api/v1/search/hybrid` |
| OLAP | `lake.olap_query(name, sql)` | `arrow-lake query sql …` | `POST /api/v1/query/olap` |
| Quality filter | `lake.quality_filter(name, filters)` | `arrow-lake quality …` | `POST /api/v1/quality/...` |
| Dedup | `lake.deduplicate(name, strategy=…)` | `arrow-lake quality dedup …` | `POST /api/v1/quality/dedup` |
| RAG query | `await lake.rag_query(q, ds)` | `arrow-lake rag query …` | `POST /api/v1/rag/query` |
| KG build | `await lake.kg_build(ds)` → task_id | `arrow-lake kg build …` | `POST /api/v1/kg/build` |
| KG status | `await lake.kg_build_status(tid)` | `arrow-lake kg status …` | `GET /api/v1/tasks/{tid}/status` |
| Gremlin | `await lake.kg_query(gremlin)` | `arrow-lake kg query …` | `POST /api/v1/kg/query` |
| Backup | `lake.backup_create(...)` | `arrow-lake backup …` | `POST /api/v1/backup/create` |
| Health | `lake.health()` | `arrow-lake status` | `GET /api/v1/health` |

**CLI command groups (16):** audit, backup, catalog, config, embed, export, index, ingest, kg, lifecycle, lineage, maintenance, quality, query, rag, search.

## Architecture at a Glance

- **Facade + Mixin**: `Lake` = 9 mixins (`_lake_base/_ingest/_search/_query/_admin/_lineage/_audit/_rag/_kg`). Components lazy-load via `_get_component(key, factory)` under a `threading.RLock` (v1.6.1: Lock→RLock fixed a nested-init deadlock).
- **Bridge**: each query mode is an isolated Bridge class behind a unified `DuckDBSessionManager` (semaphore concurrency, idle pool, zombie eviction).
- **Protocol**: `StorageProtocol`, `SearchBridge`, `QualityFilter` — structural typing, swappable, mockable.
- **Graceful degradation**: Ray→local, NeMo→CPU, KG→vector RAG, Gremlin→REST API (`export_graph`, v1.6.3).
- **Config**: 30+ Pydantic sections, 4-layer precedence (defaults < `.env` < env vars `ARROW_LAKE__*` < YAML).

Full detail + every mixin method: [references/architecture.md](references/architecture.md).

## Version Behaviors (v1.6.x → v1.7.0)

- **v1.7.0**: `doc_type` 三层路由（config override → TemplateGallery 元数据匹配 → default 兜底）+ `DocTypeClassifier` LLM 内容推断；hyper-extract (`he`) 抽取后端（`HugeGraphConfig.extractor_backend="he"`，三元组精准度提升）；HugeGraph **PD 集群模式**（`hg-pd`/`hg-store`/`hg-server`，运行时创建多 graph，每文档独立 KG 隔离）；A 方案实体双写（通用 `entity` 顶点 + 细分 label person/organization/concept）；`ingest_documents(doc_type=)` 贯通 上传 API → facade → Ingestor → chunk → KG builder。详见 cookbook [示例 44](../cookbook/examples/44_kg_doctype_he.py) / [45](../cookbook/examples/45_kg_doctype_api.py)。
- **v1.6.1**: `kg_build` no longer blocks — splits into `prepare_build`+`execute_build`, returns `task_id`. `TaskManager` generalized. New async endpoints: `/ingest/async`, `/backup/create/async`, `/restore/async`, `/tasks`, `/tasks/{id}/status`.
- **v1.6.2**: `TaskManager` dual-writes to a Redis HASH (`RedisTaskStore`) so task state is visible across uvicorn workers. `RedisConfig.task_key_prefix`, `task_ttl_seconds`.
- **v1.6.3**: HugeGraph Gremlin bindings fixed (entrypoint wrapper injects `HugeFactory.open()`); `export_graph()` falls back to REST API on Gremlin failure; deploy hardening (redis-exporter, nginx gzip/CSP/proxy, `REDISCLI_AUTH`).

## Common Mistakes

1. **Stale method names** — the SDK uses `search` / `text_search` / `hybrid_search`, NOT `search_vector` / `search_fts` / `search_hybrid`. `olap_query` has no `params=`. Always cross-check [references/architecture.md](references/architecture.md).
2. **Missing `await` on RAG/KG** — `rag_query`, `kg_build`, `kg_query` are coroutines. Calling without `await` returns a coroutine object, not a result.
3. **Treating `kg_build` as synchronous** — it returns a `task_id` immediately (v1.6.1). Poll `kg_build_status(task_id)`.
4. **Leaking the `Lake` instance** — use `with Lake(...) as lake:` so components shut down; otherwise connections leak (`ResourceWarning`).
5. **Duplicating data per query mode** — one Lance dataset serves vector + FTS + OLAP. Don't create `docs_vector` / `docs_fts`.
6. **Embedding drift** — on row updates, re-embed. Use `upsert(on="id")` / `update_rows`; stale vectors silently return wrong results.
7. **`hybrid_search` missing the vector** — it requires BOTH `query_vector` and `query_text`; it is not a text-only call.
8. **SQL injection** — never f-string user input into `olap_query`. The layer has keyword-regex defense; respect it.

## Verification

Run the bundled scripts (no side effects beyond a local `./data` dir):

```bash
# Verify config + env (auth keys present, storage backend resolvable, Redis reachable)
python docs/skill/scripts/verify_config.py

# Smoke-test the Lake facade end-to-end on a tiny synthetic dataset
python docs/skill/scripts/health_check.py ./data
```

A healthy run prints `OK` for create → vector index → search → OLAP → health.

## Extension Points

- **Custom quality filter** — implement the `QualityFilter` protocol, register on `QualityFilterRegistry`.
- **Custom chunking** — extend the chunker (strategies: page / paragraph / recursive / semchunk / chonkie_token).
- **Custom LLM provider** — add to `RAGPipeline` provider registry.
- **Custom storage backend** — implement `StorageProtocol`.
- **Custom reranker** — extend `BaseReranker` (Noop / CrossEncoder / LLM).

## References

- [architecture.md](references/architecture.md) — facade, mixins, protocols, config, degradation, every method signature
- [query-layer.md](references/query-layer.md) — 6 query bridges, DuckDB session manager, index types
- [rag-pipeline.md](references/rag-pipeline.md) — providers, citations, streaming, HyDE/MultiQuery, rerankers
- [knowledge-graph.md](references/knowledge-graph.md) — HugeGraph + Vermeer, Gremlin, GraphRAG, async build
- [ingestion-quality.md](references/ingestion-quality.md) — multi-source ingest, 5 chunkers, quality gates, dedup
- [deployment.md](references/deployment.md) — Docker/Helm, RBAC, auth, observability, nginx, security checklist

---

**Verified against**: `arrow_lake/_version.py` (`1.7.0`) + live method signatures, 2026-06-25.
