# Knowledge Graph & GraphRAG

> Version: 1.10.4

Arrow Lake includes a built-in Knowledge Graph (KG) subsystem that transforms unstructured text
into a structured entity-relationship graph through LLM-based entity extraction, writing results
into HugeGraph. When `hugegraph.enabled=True`, the RAG pipeline automatically upgrades to GraphRAG,
merging graph neighbor context alongside vector retrieval to significantly improve answer quality
for multi-hop reasoning questions.

> Prerequisites: Install dependencies with `pip install arrow-lake[kg]`, deploy a HugeGraph service,
> and enable `hugegraph.enabled = True` in your configuration.

> **Running dataset.** We build on the **AIGC industry report** (`aigc_report`): the `datas/reports/aigc_industry_report.pdf` ingested in [08](./08-rag-pipeline.md) is now mined for entities and relationships, turning the same corpus into a knowledge graph for GraphRAG.

***

## 0. Why GraphRAG — what pure vector RAG gets wrong

Before diving into KG usage, understand **why** GraphRAG exists.

**The limit of pure vector RAG**: it retrieves by embedding similarity — returning *passages* lexically/semantically near the query. Great for single-hop "what is X" facts. It struggles with multi-hop, relation-dense questions:

> Q: *"GPT-4 from OpenAI uses RLHF for alignment — what problem does that method solve, and which upstream technologies does it depend on?"*
>
> - **Pure vector RAG**: retrieves a few passages mentioning "GPT-4" or "RLHF", stitches a generic answer, likely missing the causal chain *RLHF solves alignment → it depends on human feedback + reward models → reward models build on Transformer*.
> - **GraphRAG**: locates the "GPT-4" vertex, traverses typed edges (`uses` / `based_on`) to "RLHF", "reward model", and "Transformer" vertices, injects those **structured neighbors** with `relation_type` into the LLM → a **traceable, relation-complete** answer.

**Three differentiators of Arrow Lake GraphRAG**:

1. **`relation_type` enrichment** — edges aren't just "A connects B" but "A *uses* B", "A *based_on* B"; the LLM sees *how* entities connect, not just that they co-occur.
2. **Template-driven extraction** — domain YAML templates (e.g. `project_concept_graph`: 22 types + 14 relations) yield strongly-typed entity-relations, not a bag of untyped triples.
3. **Measurable quality** — the template-quality harness quantifies orphan rate / relation-type coverage / avg degree before shipping, turning "is this graph any good?" from a guess into a metric.

> Want to jump straight to GraphRAG Q&A? See the GraphRAG section of [08 RAG](./08-rag-pipeline.md) and §9 below. First, let's build a knowledge graph that GraphRAG can use.

## 1. Building the Knowledge Graph

`Lake.kg_build()` reads text chunks from a specified dataset, calls an LLM to extract entities and
relationships, and batch-writes them into HugeGraph. The build process runs asynchronously and
returns a `task_id` for progress tracking.

```python
import asyncio
from arrow_lake import Lake
from arrow_lake.config import ArrowLakeConfig

# Enable the knowledge graph
config = ArrowLakeConfig()
config.hugegraph.enabled = True
config.hugegraph.host = "localhost"
config.hugegraph.port = 8091            # code default 8091; prod compose often rewrites to 8089
config.hugegraph.graph_name = "hugegraph"  # base name; actual graph derived per dataset as kg_{dataset} (per-dataset isolation)

lake = Lake(base_uri="./data", config=config)

# Trigger an async build — performs entity extraction on the "aigc_report" dataset
task_id = asyncio.run(lake.kg_build("aigc_report"))
print(f"Build task submitted: {task_id}")
```

### 1.1 Polling Build Status

```python
import asyncio

status = asyncio.run(lake.kg_build_status(task_id))
if status:
    print(f"Status: {status['status']}")
    print(f"Dataset: {status['dataset_name']}")
    print(f"Total chunks: {status['total_chunks']}")
    print(f"Processed: {status['processed_chunks']}")
    print(f"Entities: {status['entity_count']}")
    print(f"Relations: {status['relation_count']}")
```

`kg_build_status()` returns a dictionary with the following fields:

| Field              | Type          | Description                                    |
| ------------------ | ------------- | ---------------------------------------------- |
| `task_id`          | `str`         | Unique task identifier                         |
| `status`           | `str`         | `pending` / `running` / `completed` / `failed` |
| `dataset_name`     | `str`         | Source dataset name                            |
| `total_chunks`     | `int`         | Total text chunks to process                   |
| `processed_chunks` | `int`         | Number of chunks already processed             |
| `entity_count`     | `int`         | Number of extracted entities                   |
| `relation_count`   | `int`         | Number of extracted relations                  |
| `error`            | `str \| None` | Error message (only in `failed` status)        |

***

## 2. Graph Statistics

`Lake.kg_stats()` returns vertex and edge counts, giving you a quick overview of the graph's scale.

```python
import asyncio

stats = asyncio.run(lake.kg_stats())
print(f"Vertices: {stats.get('vertex_count', 0)}")
print(f"Edges: {stats.get('edge_count', 0)}")
```

Under the hood, this calls `HugeGraphClient.get_stats()`, which hits the HugeGraph REST API
endpoint `/graphs/{graph_name}/stats`.

***

## 3. Gremlin Queries

`Lake.kg_query()` executes raw Gremlin query strings directly against HugeGraph. This is ideal
for scenarios that require flexible, ad-hoc query patterns.

```python
import asyncio

# List all entity labels
labels = asyncio.run(
    lake.kg_query("g.V().label().dedup()")
)
print(f"Entity labels: {labels}")

# Retrieve the first 10 entity vertices
entities = asyncio.run(
    lake.kg_query("g.V().hasLabel('entity').limit(10)")
)
for entity in entities:
    print(f"  {entity.get('id')}: {entity.get('name', '')}")

# Look up a specific entity by name
results = asyncio.run(
    lake.kg_query("g.V().has('entity', 'name', 'OpenAI').valueMap()")
)
print(results)
```

> Note: Write operations in Gremlin queries (e.g., `addV()`, `addE()`) are intercepted by the
> REST API endpoint to prevent accidental mutations. They only execute when calling `kg_query()`
> directly through the Python SDK.

***

## 4. Neighbor Traversal

`Lake.kg_get_neighbors()` performs K-hop neighbor traversal starting from a given entity,
returning all reachable neighbor vertices. This is the foundation of the GraphRAG capability.

```python
import asyncio

# Get 1st-degree neighbors (outgoing edges only)
neighbors_out = asyncio.run(
    lake.kg_get_neighbors(
        entity_id="arrow_lake:entity:42",
        direction="out",
        depth=1,
    )
)
print(f"Outgoing neighbors: {len(neighbors_out)}")
for n in neighbors_out:
    print(f"  [{n.get('label')}] {n.get('name', n.get('id'))}")

# Get 2nd-degree neighbors in both directions, limited to 200 results
neighbors_2 = asyncio.run(
    lake.kg_get_neighbors(
        entity_id="arrow_lake:entity:42",
        direction="both",
        depth=2,
        limit=200,
    )
)
print(f"2nd-degree neighbors: {len(neighbors_2)}")
```

Parameter details:

* `entity_id` — Starting vertex ID as a string
* `direction` — Edge direction: `"out"`, `"in"`, or `"both"` (default: `"both"`)
* `depth` — Number of traversal hops (default: 1, max governed by `max_traversal_depth`, default 5)
* `limit` — Maximum number of neighbor vertices to return (default: 100)

Under the hood, this calls `HugeGraphClient.traverser_kneighbor()`, which uses HugeGraph's
`/graphs/{name}/traversers/kneighbor` endpoint.

***

## 5. Shortest Path Traversal

Arrow Lake provides several shortest-path algorithms for finding routes between entities.

### All Shortest Paths

`Lake.kg_all_shortest_paths()` finds all shortest paths between two vertices:

```python
import asyncio

paths = asyncio.run(
    lake.kg_all_shortest_paths(
        source="arrow_lake:entity:1",
        target="arrow_lake:entity:42",
    )
)
for path in paths.get("paths", []):
    print(" -> ".join(str(v) for v in path))
```

### Weighted Shortest Path

`Lake.kg_weighted_shortest_path()` computes the shortest path considering edge weights:

```python
result = asyncio.run(
    lake.kg_weighted_shortest_path(
        source="arrow_lake:entity:1",
        target="arrow_lake:entity:42",
    )
)
print(f"Path: {result.get('path')}")
print(f"Weight: {result.get('weight')}")
```

### Single-Source Shortest Path

`Lake.kg_single_source_shortest_path()` computes shortest paths from one source to all
reachable vertices:

```python
result = asyncio.run(
    lake.kg_single_source_shortest_path(
        source="arrow_lake:entity:1",
    )
)
for target, info in result.get("paths", {}).items():
    print(f"  -> {target}: distance={info.get('distance')}")
```

***

## 6. Ray & Ring Traversal

### Rays

`Lake.kg_rays()` emits all paths (rays) from a source vertex in the specified direction:

```python
import asyncio

rays = asyncio.run(
    lake.kg_rays(
        source="arrow_lake:entity:1",
        direction="out",
        max_depth=3,
    )
)
for ray in rays.get("rays", []):
    print(" -> ".join(str(v) for v in ray))
```

### Rings

`Lake.kg_rings()` detects cyclic paths (rings) starting from a source vertex:

```python
rings = asyncio.run(
    lake.kg_rings(
        source="arrow_lake:entity:1",
        direction="out",
        max_depth=3,
    )
)
for ring in rings.get("rings", []):
    print("Cycle: " + " -> ".join(str(v) for v in ring))
```

### Crosspoints

`Lake.kg_crosspoints()` finds the intersection points between paths from two vertices:

```python
crosspoints = asyncio.run(
    lake.kg_crosspoints(
        source="arrow_lake:entity:1",
        target="arrow_lake:entity:42",
    )
)
print(f"Crosspoints: {crosspoints.get('vertices', [])}")
```

***

## 7. Graph Analytics

Arrow Lake exposes a suite of graph analytics algorithms for measuring centrality,
detecting communities, and analyzing graph structure.

### PageRank

```python
import asyncio

pr = asyncio.run(lake.kg_pagerank(iterations=20, damping=0.85))
for vertex, score in sorted(pr.get("scores", {}).items(), key=lambda x: -x[1])[:10]:
    print(f"  {vertex}: {score:.4f}")
```

### Community Detection (Louvain)

```python
communities = asyncio.run(lake.kg_louvain(resolution=1.0))
print(f"Communities detected: {communities.get('community_count', 0)}")
```

### Centrality Measures

```python
# Degree centrality — number of direct connections per vertex
degree = asyncio.run(lake.kg_degree_centrality())

# Closeness centrality — average shortest-path distance to all others
closeness = asyncio.run(lake.kg_closeness_centrality())

# Betweenness centrality — how often a vertex lies on shortest paths
betweenness = asyncio.run(lake.kg_betweenness_centrality())
```

### Structural Analysis

```python
# Weakly Connected Components
wcc = asyncio.run(lake.kg_wcc())
print(f"Connected components: {wcc.get('component_count', 0)}")

# Triangle Count
triangles = asyncio.run(lake.kg_triangle_count())
print(f"Triangles: {triangles.get('triangle_count', 0)}")

# K-Core Decomposition
kcore = asyncio.run(lake.kg_k_core(k=3))
print(f"Vertices in 3-core: {kcore.get('vertex_count', 0)}")
```

Analytics API summary:

| Method                       | Description                                 |
| ---------------------------- | ------------------------------------------- |
| `kg_pagerank(iterations, damping)` | PageRank ranking algorithm           |
| `kg_louvain(resolution)`     | Louvain community detection                |
| `kg_degree_centrality()`     | Degree centrality per vertex               |
| `kg_closeness_centrality()`  | Closeness centrality per vertex            |
| `kg_betweenness_centrality()`| Betweenness centrality per vertex          |
| `kg_wcc()`                   | Weakly connected components                |
| `kg_triangle_count()`        | Triangle counting                          |
| `kg_k_core(k)`               | K-core decomposition                       |

***

## 8. Import & Export

### Exporting the Graph

`Lake.kg_export_graph()` exports all vertices and edges as a dictionary, optionally
including vertex/edge properties:

```python
import asyncio

data = asyncio.run(lake.kg_export_graph(with_properties=True))
print(f"Exported {len(data.get('vertices', []))} vertices, {len(data.get('edges', []))} edges")

# Save to JSON for backup
import json
with open("graph_backup.json", "w") as f:
    json.dump(data, f, indent=2)
```

### Importing the Graph

`Lake.kg_import_graph()` restores a graph from a previously exported dictionary:

```python
import json

with open("graph_backup.json") as f:
    data = json.load(f)

result = asyncio.run(lake.kg_import_graph(data))
print(f"Imported: {result}")
```

***

## 9. GraphRAG-Enhanced Q\&A

When `hugegraph.enabled=True`, `Lake.rag_query()` automatically creates a `GraphRAGPipeline`
instead of the basic `RAGPipeline`. The GraphRAG pipeline extracts key entities from the user's
question, performs neighbor traversal in the knowledge graph to gather structured context, and
injects this alongside vector retrieval results into the LLM prompt.

```python
import asyncio
from arrow_lake import Lake
from arrow_lake.config import ArrowLakeConfig

# Configuration: enable knowledge graph
config = ArrowLakeConfig()
config.hugegraph.enabled = True
config.hugegraph.host = "localhost"
config.hugegraph.port = 8089
config.llm.provider = "openai"

lake = Lake(base_uri="./data", config=config)

# GraphRAG is enabled automatically — no extra code needed
response = asyncio.run(
    lake.rag_query(
        question="Which layers does the AIGC industry chain have, and who are the representative companies in each layer?",
        dataset_name="aigc_report",
        top_k=5,
    )
)

print(response.answer)
print(f"Documents cited: {response.retrieval_count}")
for citation in response.citations:
    print(f"  - {citation.document_name} (score={citation.score:.3f})")
```

The GraphRAG pipeline follows this workflow:

1. **Entity Extraction** — Extracts key entity names from the user's question
2. **Graph Retrieval** — Runs neighbor traversal for each entity, collecting related entities and relationships
3. **Vector Retrieval** — Retrieves relevant documents using the standard strategy (vector/fts/hybrid)
4. **Context Fusion** — Merges graph context and document snippets into an augmented prompt
5. **LLM Generation** — Generates the final answer based on the fused context

If HugeGraph is unavailable or the connection fails, the pipeline automatically falls back to
standard RAG mode without interrupting service.

***

## 10. Graph Cleanup

`Lake.kg_delete_graph()` removes all vertices and edges (including the schema) from the graph.
This operation is irreversible — use with caution.

```python
import asyncio

# Confirm and execute cleanup
asyncio.run(lake.kg_delete_graph())
print("Graph cleared")

# Verify cleanup results
stats = asyncio.run(lake.kg_stats())
print(f"Vertices: {stats.get('vertex_count', 0)}")
print(f"Edges: {stats.get('edge_count', 0)}")
```

Under the hood, this calls `HugeGraphClient.clear()`, which executes the following steps in order:

1. Clear all vertex data
2. Clear all edge data
3. Clear the schema (vertex label and edge label definitions)

***

## 11. Full Workflow Example

Here is a complete end-to-end flow from data ingestion through GraphRAG question answering:

```python
import asyncio
from arrow_lake import Lake
from arrow_lake.config import ArrowLakeConfig

async def main():
    # 1. Configuration
    config = ArrowLakeConfig()
    config.hugegraph.enabled = True
    config.hugegraph.host = "localhost"
    config.hugegraph.port = 8089
    config.hugegraph.build_batch_size = 100
    config.hugegraph.default_traversal_depth = 2
    config.llm.provider = "openai"

    lake = Lake(base_uri="./data", config=config)

    # 2. Ingest the full text of the AIGC industry report (parse -> chunk -> embed -> index)
    report = lake.ingest_documents_and_index("aigc_report", [
        "datas/reports/aigc_industry_report.pdf",
    ])
    print(f"Ingested: {report.total_rows} chunks")

    # 3. Build the knowledge graph
    task_id = await lake.kg_build("aigc_report")
    print(f"Build task: {task_id}")

    # 4. Wait for build completion
    status = await lake.kg_build_status(task_id)
    while status and status["status"] not in ("completed", "failed"):
        await asyncio.sleep(2)
        status = await lake.kg_build_status(task_id)
        if status:
            print(f"  Progress: {status['processed_chunks']}/{status['total_chunks']}")

    # 5. View statistics
    stats = await lake.kg_stats()
    print(f"Graph: {stats.get('vertex_count', 0)} vertices, {stats.get('edge_count', 0)} edges")

    # 6. GraphRAG Q&A
    response = await lake.rag_query(
        question="What are the core technologies and representative companies in the AIGC report, and how are they related?",
        dataset_name="aigc_report",
    )
    print(f"Answer: {response.answer[:200]}...")

    # 7. Cleanup
    await lake.kg_delete_graph()
    lake.shutdown()

asyncio.run(main())
```

***

## 12. Configuration Reference

Key configuration options for `HugeGraphConfig` (v1.9.6):

| Option                    | Type    | Default           | Description                                 |
| ------------------------- | ------- | ----------------- | ------------------------------------------- |
| `enabled`                 | `bool`  | `False`           | Whether to enable the knowledge graph       |
| `host` / `port`           | `str`/`int` | `localhost`/`8091` | HugeGraph REST endpoint (prod often rewrites to 8089) |
| `graph_name`              | `str`   | `"hugegraph"`     | Base graph name; actual graph derived per dataset as `kg_{dataset}` (per-dataset isolation) |
| `backend`                 | `str`   | `"rocksdb"`       | Storage backend (rocksdb single-node multi-graph / hstore PD cluster) |
| `build_concurrency` / `write_concurrency` | `int` | `3`/`2` | LLM extraction concurrency / HugeGraph write concurrency (write is the bottleneck, lower by default) |
| `extractor_backend`       | `str`   | `"he"`            | Extraction backend: `"he"` (hyper-extract, default) / `"legacy"` |
| `he_default_template`     | `str`   | `"entity_graph"`  | Default extraction template (generic entity+relation, strict enum) |
| `he_doc_type_templates`   | `dict`  | see code          | doc_type→template mapping (paper/report→entity_graph, medicine→medical_concept_graph, project→project_concept_graph, etc.) |
| `he_kg_granularity`       | `str`   | `"map_reduce"`    | Extraction granularity: `map_reduce` (default since v1.9.12, concurrent extract + global merge) / `dataset` / `chunk` / `auto` (auto picks map_reduce when N>500 chunks) |
| `he_strict_definition`    | `bool`  | `False`           | v1.9.6: drop entities with empty definition (noise reduction) |
| `he_extract_llm` / `he_qa_llm` | `LLMConfig\|None` | `None` | Two-stage independent LLMs (extraction/QA; None falls back to global llm) |
| `he_ka_max_versions`      | `int`   | `5`               | Number of KA versions retained per dataset (excess pruned; supports rollback) |
| `he_ka_base_dir`          | `str`   | `"/data/ka"`      | KA dump local root (must be a local path, not a bucket) |
| `default_traversal_depth` / `max_traversal_depth` | `int` | `2`/`5` | Default/max traversal hops (1-10) |

Configuration constraints:

* `max_traversal_depth` must be between 1 and 10
* `build_batch_size` must be at least 1
* `timeout_seconds` must be at least 1.0

***

## 📚 Appendix: v1.7–v1.10 KG Evolution (version history, optional)

> This section is the version evolution log of the KG subsystem (extraction backend / doc_type routing / per-dataset / incremental / quality / template management). **New users can skip it** — core usage is in §1–§11 above; full changes in [CHANGELOG](../../CHANGELOG.md). Kept as an appendix for contributors and existing users to understand the design background.

### v1.7–v1.10 evolution overview

v1.7.0 added a pluggable extraction backend and document-type-aware template routing to `kg_build`,
significantly improving triple precision for domain-specific documents (research articles, contracts, financial
reports, medical records, etc.).

### Switching to the Hyper-Extract (`he`) Backend

Set `HugeGraphConfig.extractor_backend` to route extraction through hyper-extract templates:

```python
from arrow_lake.config import ArrowLakeConfig

config = ArrowLakeConfig()
config.hugegraph.enabled = True
config.hugegraph.extractor_backend = "he"            # "legacy" | "he" (default he, hyper-extract)
config.hugegraph.he_model = "qwen3:30b-a3b"          # any OpenAI-compatible model (or use he_extract_llm/he_qa_llm two-stage)
config.hugegraph.he_default_template = "entity_graph"  # default entity_graph (generic entity+relation, strict enum + required definition); concept_graph reserved for taxonomy scenarios; domain templates (project_concept_graph, etc.) see he_doc_type_templates.
```

Requires the `he` extra: `pip install "arrow-lake[he]"`. The `he` backend drives hyper-extract
templates via langchain `ChatOpenAI`, producing higher-precision triples than the legacy generic
extractor.

### Doc-Type Three-Layer Routing

When `extractor_backend="he"`, each document's `doc_type` selects a hyper-extract template through
three layers (`doc_type_router.py`) — first match wins:

1. **Config override** — `HugeGraphConfig.he_doc_type_templates` explicit mapping.
2. **TemplateGallery metadata match** — scans every preset's `tags` / `category` / `name` /
   `description`; newly added templates are picked up automatically with no code change.
3. **Default fallback** — `HugeGraphConfig.he_default_template`.

```python
from arrow_lake.knowledge_graph.doc_type_router import (
    DocTypeRouter, TemplateGallery, normalize_doc_type, validate_taxonomy,
)

# Alias folding: 论文 / research_paper / 白皮书 → canonical "paper"
print(normalize_doc_type("论文"))              # "paper"

# Gallery indexes every hyper-extract preset by metadata (auto-discovers new templates)
gallery = TemplateGallery.build()
hit = gallery.match("paper")                  # → matched preset (path/tags) or None
print(hit.path if hit else "default")

# Three-layer router: override > gallery > default
router = DocTypeRouter(
    doc_type_templates={"paper": "general/concept_graph"},   # layer 1: explicit override
    default_template="general/concept_graph",                # layer 3: fallback
)
path, source = router.resolve_with_source("论文")
print(path, source)                           # general/concept_graph 'override'
```

If `doc_type` is missing entirely, `DocTypeClassifier` infers it from document content via LLM
**once per document** — all chunks then share the matched template, saving LLM calls.

### Passing doc_type at Ingest Time

`doc_type` is an ingest-time property; it flows upload API → `Lake` facade → Ingestor → chunk
table → KG builder:

```python
# Python SDK
lake.ingest_documents("aigc_report", ["datas/reports/aigc_industry_report.pdf"], doc_type="report")
```

> CLI `kg build` has **no** `--doc-type` flag — set `doc_type` when ingesting. To override the *template*
> (not the doc_type) for a single build, use `kg build <ds> --template <name>` (v1.10.0); see
> [13-cli-reference](./13-cli-reference.md).

### A-Scheme Entity Dual-Write

Every entity is written as a generic `entity` vertex **plus** a fine-grained label
(`person` / `organization` / `concept` / …). Relations route to a fine-grained edge when the
endpoint types have a matching synonym, otherwise fall back to a generic `related_to` edge. The
original type is preserved on the `relation_type` property, so both generic and type-specific
queries work:

```python
# Generic — all entities
await lake.kg_query("g.V().hasLabel('entity').limit(10)")
# Type-specific — people only
await lake.kg_query("g.V().hasLabel('person').limit(10)")
```

### HugeGraph PD Cluster Mode

The production compose (`deploy/docker-compose.prod.yml`) runs HugeGraph in PD mode
(`hg-pd` + `hg-store` + `hg-server`, hstore backend) instead of standalone rocksdb, enabling
**runtime multi-graph creation** — each document can get its own isolated KG. Services start in
order PD → Store → Server, gated by healthchecks.

### v1.8.8 Per-Dataset Dynamic Graph + Incremental KA + Versioning

- **Per-dataset isolation**: each dataset gets its own graph `kg_{dataset}` (not a single global graph), with true isolation on the rocksdb backend.
- **`kg_build(incremental=True)`**: incremental build — only feeds new chunks (`fed_chunks` sidecar), falls back on template mismatch, and KG reuses `PRIMARY_KEY` for idempotent upsert. CLI: `kg build --incremental`. REST: `POST /api/v1/kg/build` body adds `"incremental": true`.
- **KA versioning**: before each `kg_build`, the pre-build dump is archived to `<base>/<ds>/ka/versions/v{ts}/`; `he_ka_max_versions` (default 5) prunes the oldest beyond the cap. SDK: `lake.kg_list_ka_versions(ds)` / `kg_rollback_ka(ds, version)` / `kg_prune_ka_versions(ds)`; REST: `/api/v1/kg/ka-versions/{dataset}`, `/ka-rollback`, `/ka-prune`.
- **Template discovery endpoints**: `GET /api/v1/kg/doc-types`, `/templates`, `/templates/{template_path}` (lists canonical doc_types + aliases + templates; `is_high_risk` flags hypergraph presets).

### v1.9.4 Quality: MERGE_FIELD + Domain Strict Templates

- **MERGE_FIELD merge** (replaces BALANCED): at `dataset` granularity, cross-chunk field merging now uses the non-LLM MERGE_FIELD strategy (`he_extractor._create_ka`), eliminating the memory blowups of BALANCED grouped merges and staying stable at any scale; `build_index` is decoupled so KG ingestion is reliable. The grouped tier has been removed.
- **Domain strict templates**: `project_concept_graph` (22 types + 14 relations, for project proposals), `medical_concept_graph`, `legal_concept_graph`, `finance_concept_graph` — tight enums + required definition, avoiding the 0% definition coverage + 80+ free-typed noise of `general/concept_graph`.

### v1.9.6 Performance & Noise Reduction

- **Snap edit-distance normalization**: noisy types ("architecture component" → "component") are snapped to the nearest enum value.
- **Strict filtering**: `he_strict_definition=true` drops entities with empty definition (noise reduction).
- **GraphRAG three-way parallelism**: `_graphrag_retrieve` uses `asyncio.gather` to run vector / search_ka / neighbor in parallel, cutting latency by 40–50%; `QuestionEntityCache` uses a monotonic clock to prevent bulk TTL expiry; the KA LRU cache invalidates by dump mtime.
- **Traverser OOM fix**: JVM taking the last of duplicate `-Xmx` flags once left the heap at only 2g → traversal OOM. Fixed via `HG_SERVER_MEMORY_LIMIT=12288M` + `JAVA_OPTS -Xmx8g` (see [12-deployment](./12-deployment.md)).

> See also: cookbook [example 44](examples/44_kg_doctype_he.py) (routing, offline-runnable) and
> [example 45](examples/45_kg_doctype_api.py) (REST API build flow).

### v1.9.9 Resilient Relations + Orphan Linking

- **Relation soft-degrade** (`he_kg_type_pair=True`, default): when the LLM emits a relation whose
  endpoint type-pair is not in the legal verb table, the relation is **downgraded to `related_to`**
  (weight 0.4) instead of being dropped — endpoints stay connected. This fixes the paradox where the
  type-pair filter itself created orphans. Empirically recovered ~5000 `related_to` edges on a 12k-entity
  graph, cutting the orphan rate from 0.44 → 0.03.
- **Heuristic orphan linking** (`he_orphan_linking=auto`, default on, zero LLM): combines chunk
  co-occurrence + embedding cosine ≥ `he_orphan_threshold` (0.75) + a legal type-pair verb to connect
  isolated vertices into the existing connected component. Capped by `he_orphan_max_partners=3` and
  `he_orphan_max_links=500`. Effective only for `project_concept_graph` (other templates have different
  type systems → no-op). Quantifiable via `GET /api/v1/kg/quality` (`orphan_rate` / `avg_degree` /
  `relation_type_coverage`).

### v1.9.12 map_reduce Default Granularity

`he_kg_granularity` now defaults to **`map_reduce`** (concurrent per-chunk extraction + a global merge
pass), unified across the code default, prod_minimal compose, and dev override. `auto` is retained and
picks `map_reduce` when a dataset has > 500 chunks, else `dataset`. `chunk` granularity (no cross-chunk
merge) remains available but is rarely the right choice for connected graphs.

### v1.10.0 Extraction-Template Management ⚑

v1.10.0 adds a full **dynamic template lifecycle** — no rebuild or restart needed to pick up a new
preset (`reset_gallery_cache` re-scans). All template state lives in the system_db (libSQL).

- **Template registry YAML schema** (`template_registry.py`): each template declares `name` /
  `category` / `type` / `output.entities` + `output.relations` / `guideline`. `category` is required and
  must exist in the doc-type category dictionary.
- **CRUD + binding**: `POST/PUT/DELETE/GET /api/v1/admin/extraction-templates`; bind a dataset with
  `/bindings/{dataset}`. `POST /api/v1/kg/build` auto-resolves the template: explicit `req.template` →
  dataset binding → doc_type routing (three-layer).
- **AI generate + dry-run**: `POST /generate` produces a template from a doc sample + doc_type;
  `POST /dry-run` validates extraction without persisting.
- **Quality-validation harness**: `POST /{name}/quality/doc` generates a ~2000-word sample doc →
  `POST /{name}/quality/build` ingests it + builds the KG + renders the graph + runs RAG →
  `DELETE /quality/{temp_dataset}` cleans up the throwaway dataset. Run history is queryable via
  `GET /{name}/quality/history`.

```bash
# Generate a template from a sample, then quality-validate it end-to-end
curl -X POST http://localhost:8000/api/v1/admin/extraction-templates/generate \
  -H "Authorization: Bearer $TOKEN" -H "X-API-Key: $KEY" \
  -H "Content-Type: application/json" -d '{"doc_type": "project", "sample": "...proposal text..."}'

curl -X POST http://localhost:8000/api/v1/admin/extraction-templates/project_concept_graph/quality/build \
  -H "Authorization: Bearer $TOKEN" -H "X-API-Key: $KEY"
```

### v1.10.0 Category ↔ Doc-Type Dynamic Dictionary

`GET /api/v1/kg/doc-types` is now dynamic. `DOC_TYPE_ALIASES` ships 10 canonical keys
(paper/report/manual/biography/finance/legal/medicine/industry/tcm/general); `project` and custom keys
are added through `POST /api/v1/admin/doc-type-categories` (admin-only; `GET` lists, `DELETE/{name}`
removes). A template's `category` must be present in this dictionary or registration is rejected.

***

## 13. Frequently Asked Questions

**Q: What should I do if the build task is stuck in `pending` status?**

Check whether the HugeGraph service is reachable. If `lake.kg_stats()` throws a connection error,
HugeGraph is either not running or the network configuration is incorrect.

**Q: How can I improve entity extraction quality?**

Configure a stronger LLM model (e.g., GPT-4, Claude), or specify domain-specific keywords in the
prompt. The `EntityExtractor` supports switching the underlying model via the `llm` configuration
option.

**Q: What is the performance impact of GraphRAG?**

GraphRAG's additional graph traversal typically adds 50-200ms of latency, but it significantly
improves answer quality for multi-hop reasoning and entity-association questions. If latency is
a concern, set `hugegraph.enabled=False` to fall back to standard RAG.
