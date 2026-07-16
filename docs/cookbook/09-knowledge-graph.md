# Knowledge Graph & GraphRAG

> Version: 1.7.0

Arrow Lake includes a built-in Knowledge Graph (KG) subsystem that transforms unstructured text
into a structured entity-relationship graph through LLM-based entity extraction, writing results
into HugeGraph. When `hugegraph.enabled=True`, the RAG pipeline automatically upgrades to GraphRAG,
merging graph neighbor context alongside vector retrieval to significantly improve answer quality
for multi-hop reasoning questions.

> Prerequisites: Install dependencies with `pip install arrow-lake[kg]`, deploy a HugeGraph service,
> and enable `hugegraph.enabled = True` in your configuration.

***

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
config.hugegraph.port = 8089
config.hugegraph.graph_name = "arrow_lake_kg"

lake = Lake(base_uri="./data", config=config)

# Trigger an async build — performs entity extraction on the "docs" dataset
task_id = asyncio.run(lake.kg_build("docs"))
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
    lake.kg_query("g.V().has('entity', 'name', 'Arrow Lake').valueMap()")
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
        question="How do Arrow Lake's knowledge graph and vector retrieval work together?",
        dataset_name="docs",
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

    # 2. Ingest documents
    report = lake.ingest("my_docs", ["technical_guide.md"])
    print(f"Ingested: {report.total_files} files, {report.total_rows} rows")

    # 3. Build the knowledge graph
    task_id = await lake.kg_build("my_docs")
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
        question="What are the core components of the system and how are they related?",
        dataset_name="my_docs",
    )
    print(f"Answer: {response.answer[:200]}...")

    # 7. Cleanup
    await lake.kg_delete_graph()
    lake.shutdown()

asyncio.run(main())
```

***

## 12. Configuration Reference

Complete configuration options for `HugeGraphConfig`:

| Option                    | Type    | Default           | Description                                 |
| ------------------------- | ------- | ----------------- | ------------------------------------------- |
| `enabled`                 | `bool`  | `False`           | Whether to enable the knowledge graph       |
| `host`                    | `str`   | `"localhost"`     | HugeGraph server address                    |
| `port`                    | `int`   | `8089`            | HugeGraph REST API port                     |
| `graph_name`              | `str`   | `"arrow_lake_kg"` | Graph database name within HugeGraph        |
| `timeout_seconds`         | `float` | `30.0`            | HTTP request timeout in seconds             |
| `username`                | `str`   | `""`              | Authentication username (empty = no auth)   |
| `password`                | `str`   | `""`              | Authentication password                     |
| `auto_build_on_ingest`    | `bool`  | `False`           | Automatically build graph on ingestion      |
| `build_batch_size`        | `int`   | `50`              | Batch size for inserting vertices and edges |
| `default_traversal_depth` | `int`   | `2`               | Default graph traversal hop count           |
| `max_traversal_depth`     | `int`   | `5`               | Maximum allowed traversal hops (1-10)       |

Configuration constraints:

* `max_traversal_depth` must be between 1 and 10
* `build_batch_size` must be at least 1
* `timeout_seconds` must be at least 1.0

***

## v1.7.0 Update: Doc-Type Routing & Hyper-Extract Backend

v1.7.0 adds a pluggable extraction backend and document-type-aware template routing to `kg_build`,
significantly improving triple precision for domain-specific documents (papers, contracts, financial
reports, medical records, etc.).

### Switching to the Hyper-Extract (`he`) Backend

Set `HugeGraphConfig.extractor_backend` to route extraction through hyper-extract templates:

```python
from arrow_lake.config import ArrowLakeConfig

config = ArrowLakeConfig()
config.hugegraph.enabled = True
config.hugegraph.extractor_backend = "he"            # "graphrag" (default) | "he"
config.hugegraph.he_model = "qwen3:30b-a3b"          # any OpenAI-compatible model
config.hugegraph.he_default_template = "concept_graph"  # v1.8.9 default: project-local strict template (type/relation enum + required definition). Do NOT set "general/concept_graph" — that gallery preset leaves definition optional and yields noisy free-typed entities (0% definition coverage).
```

Requires the `he` extra: `pip install "arrow-lake[he]"`. The `he` backend drives hyper-extract
templates via langchain `ChatOpenAI`, producing higher-precision triples than the default graphrag
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
lake.ingest_documents("papers", ["data/paper.pdf"], doc_type="paper")
```

> CLI `kg build` has **no** `--doc-type` flag — set `doc_type` when ingesting.

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

> See also: cookbook [example 44](examples/44_kg_doctype_he.py) (routing, offline-runnable) and
> [example 45](examples/45_kg_doctype_api.py) (REST API build flow).

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
