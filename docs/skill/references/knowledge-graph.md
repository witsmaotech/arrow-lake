# Knowledge Graph — HugeGraph + Vermeer, Gremlin, GraphRAG

> Back-reference: [../SKILL.md](../SKILL.md) · parent: [architecture.md](architecture.md). Verified v1.7.0.

All KG entry points on `Lake` are **`async`** (`_LakeKGMixin`). Two engines back it:
- **`HugeGraphClient`** — query / traverse / Gremlin
- **`VermeerClient`** — bulk graph construction

## Building a graph

`kg_build` is **fire-and-forget** since v1.6.1 — it returns a `task_id` immediately and never blocks the event loop.

```python
import asyncio
from arrow_lake import Lake

async def main():
    lake = Lake("./data")
    try:
        task_id = await lake.kg_build("docs")     # non-blocking → str task_id
        # ... do other work ...
        status = await lake.kg_build_status(task_id)   # poll: dict | None
        # status carries entity_count, relation_count once done (synced to Redis v1.6.2)
    finally:
        lake.shutdown()

asyncio.run(main())
```

Internals (`_lake_kg.py`): `prepare_build()` loads the Lance dataset and normalizes Arrow columns in a thread executor (so the uvicorn loop never blocks), then `execute_build()` extracts entities/relations via LLM and inserts them into HugeGraph. HugeGraph defaults tuned in v1.6.1: `build_concurrency` 1→3, `build_batch_delay` 3.0s→0.5s.

**Multi-worker note (v1.6.2):** final status (`entity_count`, `relation_count`) is dual-written to a Redis HASH (`RedisTaskStore`), so `kg_build_status` is correct regardless of which uvicorn worker ran the build.

## doc_type routing & hyper-extract backend (v1.7.0)

v1.7.0 layers a pluggable extraction backend and document-type-aware template routing on top of `kg_build`.

**Extraction backend** — `HugeGraphConfig.extractor_backend`:
- `"graphrag"` (default) — original GraphRAG-style extraction.
- `"he"` — hyper-extract backend (`he_extractor.py`); drives hyper-extract templates via langchain `ChatOpenAI` for higher-precision triples. Needs the `he` extra: `pip install "arrow-lake[he]"`.

**doc_type three-layer routing** (`doc_type_router.py`) — picks the hyper-extract template per document:
1. **config override** — explicit `doc_type` wins.
2. **TemplateGallery metadata match** — scans every preset's `tags` / `category` / `name` / `description`; new templates are picked up automatically (zero code change).
3. **default fallback** — nothing matched.

Plus `normalize_doc_type()` alias folding (论文 / `research_paper` → `paper`, …); `DocTypeClassifier` LLM content inference (missing `doc_type` → inferred from content, **once per document**, shared across chunks — saves LLM calls); `KNOWN_DOC_TYPES` + `validate_taxonomy()` single source of truth (CI-guarded).

**Entity dual-write (A scheme)** — every entity writes a generic `entity` vertex **plus** a fine-grained label (`person` / `organization` / `concept` / …). Relation routing: synonyms → fine-grained edge; no match → `related_to` fallback. Original type preserved on the `relation_type` property.

**PD cluster mode** — `deploy/docker-compose.prod.yml` runs HugeGraph in PD mode (`hg-pd` + `hg-store` + `hg-server`, hstore backend) instead of standalone rocksdb, enabling **runtime multi-graph creation** (one isolated KG per document). Startup order PD→Store→Server via healthcheck dependencies.

**doc_type plumbing** — `ingest_documents(doc_type=...)` flows upload API → `Lake` facade → Ingestor → chunk table → KG builder. Note: CLI `kg build` has **no** `--doc-type` flag — doc_type is set at ingest time.

See cookbook [example 44](../../cookbook/examples/44_kg_doctype_he.py) (routing, offline-runnable) / [45](../../cookbook/examples/45_kg_doctype_api.py) (REST API build flow).

## Querying (Gremlin)

```python
res = await lake.kg_query("g.V().hasLabel('Person').limit(10)", traversal_depth=None)
# → list[dict]
```

Traversal & analysis (all async):
- `kg_get_neighbors(...)`, `kg_stats()`, `kg_graph_exists()`, `kg_ensure_graph()`, `kg_delete_graph()`
- Shortest paths: `kg_all_shortest_paths`, `kg_weighted_shortest_path`, `kg_single_source_shortest_path`, `kg_multi_node_shortest_path`
- Topology: `kg_rays`, `kg_rings`

## GraphRAG

GraphRAG = RAG pipeline wired to the KG retriever (entities + relations) instead of pure vector retrieval. See [rag-pipeline.md](rag-pipeline.md). It uses the same `rag_query` surface with the graph strategy; KG enriches retrieved context with subgraph facts.

## HugeGraph Gremlin fix (v1.6.3) — know this before debugging `g.V()` failures

HugeGraph 1.7 all-in-one's `gremlin-server.yaml` registered `graphs: {}` such that the `g` TraversalSource was unbound — `g.V()` raised "No such property: g". **Fixed in v1.6.3** via an entrypoint wrapper (`deploy/scripts/entrypoint-hugegraph.sh`) that patches the Groovy init script to call `HugeFactory.open(...)` and bind `g` before the server starts. `export_graph()` additionally falls back to the REST API (`GET /graphs/{name}/graph/vertices|edges`) when Gremlin still throws.

If `g.V()` fails in an older deploy: apply `deploy/scripts/fix-hugegraph-gremlin.sh`, or ensure the compose uses the wrapper overlay (`docker-compose.hugegraph.yml`). Full detail: `issue-hugegraph-gremlin-bindings` memory (RESOLVED, v1.6.3).

## Common Mistakes

- **Treating `kg_build` as blocking**: it returns a `task_id` (v1.6.1). Poll `kg_build_status(task_id)`; don't `await` expecting the finished graph.
- **Missing `await`**: every `kg_*` method is a coroutine.
- **Querying before the build finishes**: `kg_build_status` returns `None` or `{"state": ...}` until complete; check before `kg_query`.
- **Gremlin on an unfixed HugeGraph**: if `g.V()` errors, you're on a pre-v1.6.3 deploy — apply the entrypoint fix.
- **Cross-worker status**: in a multi-worker deploy, only the Redis-backed status is globally correct (v1.6.2) — ensure `RedisTaskStore` is initialized (app lifespan does this automatically).
