# Knowledge Graph & GraphRAG

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

# Get 1st-degree neighbors
neighbors_1 = asyncio.run(
    lake.kg_get_neighbors(entity_id="arrow_lake:entity:42", depth=1)
)
print(f"1st-degree neighbors: {len(neighbors_1)}")
for n in neighbors_1:
    print(f"  [{n.get('label')}] {n.get('name', n.get('id'))}")

# Get 2nd-degree neighbors — discover more distant associated entities
neighbors_2 = asyncio.run(
    lake.kg_get_neighbors(entity_id="arrow_lake:entity:42", depth=2)
)
print(f"2nd-degree neighbors: {len(neighbors_2)}")
```

Parameter details:

* `entity_id` — Starting vertex ID as a string
* `depth` — Number of traversal hops (default: 1, max governed by `max_traversal_depth`, default 5)

Under the hood, this calls `HugeGraphClient.traverser_kneighbor()`, which uses HugeGraph's
`/graphs/{name}/traversers/kneighbor` endpoint.

***

## 5. GraphRAG-Enhanced Q\&A

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

## 6. Graph Cleanup

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

## 7. Full Workflow Example

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

## 8. Configuration Reference

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

## 9. Frequently Asked Questions

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
