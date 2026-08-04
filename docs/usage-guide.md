# Arrow Lake Usage Guide

A practical walkthrough for **Arrow Lake v1.10.0** -- a production-grade multimodal data lakehouse
built on Lance, DuckDB, Daft, and Ray.

---

## Table of Contents

1. [Installation](#1-installation)
2. [Quick Start](#2-quick-start)
3. [Dataset Management](#3-dataset-management)
4. [Data Ingestion](#4-data-ingestion)
5. [Search](#5-search)
6. [SQL Analytics](#6-sql-analytics)
7. [RAG Pipeline](#7-rag-pipeline)
8. [Knowledge Graph](#8-knowledge-graph)
9. [Data Quality](#9-data-quality)
10. [Export](#10-export)
11. [Lineage & Audit](#11-lineage--audit)
12. [Production Deployment](#12-production-deployment)
13. [Configuration Reference](#13-configuration-reference)
14. [CLI Reference](#14-cli-reference)

---

## 1. Installation

### pip (recommended)

```bash
pip install arrow-lake
```

### From source

```bash
git clone https://github.com/wits-sunpw/arrow-lake.git
cd arrow-lake
pip install -e .
```

### Docker

```bash
# Pull the image
docker pull arrowlake/arrow-lake:latest

# Or build from the repo
docker build -f deploy/Dockerfile -t arrow-lake .
```

### Verify

```bash
arrow-lake --version        # CLI
python -c "from arrow_lake import Lake; print(Lake().version())"
```

---

## 2. Quick Start

Go from zero to first query in under five minutes.

```python
from arrow_lake import Lake
import pyarrow as pa

# 1. Initialize (local filesystem by default)
with Lake("./my_lake") as lake:
    # 2. Create a dataset from an Arrow Table
    table = pa.table({
        "id": [1, 2, 3],
        "title": ["Neural Networks", "Transformers", "Diffusion Models"],
        "body": [
            "A foundational architecture for deep learning...",
            "Attention is all you need...",
            "Generating images from noise...",
        ],
        "category": ["ml", "ml", "genai"],
    })
    lake.create_dataset("articles", table)

    # 3. List what you have
    print(lake.list_datasets())  # ['articles']

    # 4. Read data back
    result = lake.read_dataset("articles")
    print(result.num_rows)  # 3

    # 5. Run an OLAP query
    olap = lake.olap_query("articles", """
        SELECT category, COUNT(*) AS cnt
        FROM articles
        GROUP BY category
    """)
    print(olap.table.to_pandas())

lake.shutdown()  # or use 'with' context manager
```

### Config-driven initialization

For production workloads, use a YAML config file instead of defaults:

```python
lake = Lake.from_yaml("configs/prod.yaml", base_uri="./lake_data")
```

---

## 3. Dataset Management

### Create

```python
import pyarrow as pa

table = pa.table({"name": ["Alice", "Bob"], "age": [30, 25]})
lake.create_dataset("users", table)
```

### Read

```python
# Full table
table = lake.read_dataset("users")

# Specific columns only
table = lake.read_dataset("users", columns=["name"])

# Lazy scanner for large datasets
scanner = lake.scan_dataset("users", columns=["name"], filter="age > 25")
for batch in scanner.to_batch_iter():
    print(batch.num_rows)
```

### Update

```python
# Append new rows
new_rows = pa.table({"name": ["Carol"], "age": [28]})
lake.append_dataset("users", new_rows)

# Upsert (insert new, update existing on key column)
lake.upsert("users", pa.table({"id": [1], "name": ["Alice Updated"]}), on="id")

# Add a computed column
lake.add_column("users", "age_group",
    "CASE WHEN age < 30 THEN 'young' ELSE 'senior' END")

# Add pre-computed columns (in-place, no full rewrite)
vec_table = pa.table({
    "embedding": pa.array([[0.1, 0.2, 0.3]], type=pa.list_(pa.float32(), 3))
})
lake.add_columns_table("users", vec_table)

# Update rows matching a filter
lake.update_rows("users", where="age > 30", values={"age_group": "'senior'"})
```

### Delete

```python
# Delete entire dataset
lake.delete_dataset("users")

# Delete rows matching a filter
lake.delete_rows("users", where="age < 25")
```

### Schema Evolution

```python
# Alter column type
lake.alter_column("users", "age", pa.float32())

# Drop a column
lake.drop_column("users", "temporary_flag")

# Full restore (schema change + data reload)
lake.restore_dataset("users", new_table)
```

### Copy, Rename, Merge

```python
lake.copy_dataset("users", "users_backup")
lake.rename_dataset("users_backup", "users_archive")
lake.merge_datasets(["users_jan", "users_feb", "users_mar"], "users_q1")
```

### Versioning & Time Travel

```python
current_version = lake.get_dataset_version("articles")  # e.g. 5
versions = lake.list_dataset_versions("articles")       # list of version dicts

# Search with a specific version
results = lake.search("articles", query_vector, version=3)
```

### Catalog

```python
catalog = lake.catalog()
for entry in catalog.datasets:
    print(f"{entry.name}: {entry.num_rows} rows, v{entry.version}")

lake.list_datasets()  # just the names
```

### Maintenance

```python
# Compact fragmented files
stats = lake.compact_dataset("articles")
```

---

## 4. Data Ingestion

### Files

```python
# CSV, JSON, JSONL, Parquet
report = lake.ingest("sales", ["data/jan.csv", "data/feb.parquet"])
print(report.total_rows, report.total_bytes)

# Batch ingest (same file type, uses Daft write_lance)
report = lake.ingest_batch("logs", ["log1.jsonl", "log2.jsonl", "log3.jsonl"])
```

### HTTP URLs

```python
report = lake.ingest_http("papers", [
    "https://arxiv.org/pdf/2401.00001.pdf",
    "https://example.com/data.json",
])
```

### Images & Videos

```python
report = lake.ingest_images("photos", ["img1.jpg", "img2.png"])
# Extracts thumbnails and EXIF metadata automatically

report = lake.ingest_videos("clips", ["video1.mp4", "video2.webm"])
# Extracts keyframes automatically
```

### Mixed Modalities

```python
report = lake.ingest_mixed("corpus", {
    "files": ["text.txt", "data.csv"],
    "urls": ["https://example.com/report.pdf"],
    "images": ["chart1.png"],
    "videos": ["demo.mp4"],
})
```

### Documents (PDF)

```python
from arrow_lake.config import DocumentConfig

doc_config = DocumentConfig(
    chunk_size=512,
    chunk_overlap=64,
    ocr_enabled=True,
)

report = lake.ingest_documents(
    "knowledge_base",
    ["manual.pdf", "whitepaper.pdf"],
    doc_config=doc_config,
)
```

### SQL Database

```python
report = lake.ingest_sql("orders",
    sql="SELECT * FROM orders WHERE created_at > '2025-01-01'",
    connection_url="postgresql://user:pass@host:5432/mydb",
    partition_col="created_at",
    num_partitions=8,
)
```

### Kafka

```python
report = lake.ingest_kafka("events",
    bootstrap_servers="kafka:9092",
    topics=["user_events", "system_events"],
    start="earliest",
    end="latest",
    json_decode=True,
)
```

### Apache Iceberg & Delta Lake

```python
report = lake.ingest_iceberg("analytics",
    table_uri="s3://warehouse/analytics.db/pageviews")
report = lake.ingest_deltalake("metrics",
    table_uri="s3://lake/metrics", version=5)
```

### Ingest + Embed in One Step

```python
result = lake.ingest_and_embed("docs", ["doc1.txt", "doc2.txt"],
    text_column="text_content",
    embedding_column="text_embedding",
    model="BAAI/bge-small-en-v1.5",
)
print(result.ingestion_stats.total_rows, result.embedding_stats.rows_embedded)
```

### Add Embeddings to Existing Data

```python
rows_embedded = lake.embed_and_add("articles",
    text_column="body",
    embedding_column="text_embedding",
    batch_size=64,
)
```

---

## 5. Search

### Vector Search

```python
# Create index first
index_info = lake.create_vector_index("articles",
    metric="cosine",
    vector_column="text_embedding",
    index_type="IVF_PQ",
)

# Search
results = lake.search("articles",
    query_vector=[0.1, 0.2, ...],  # your embedding
    top_k=10,
    metric="cosine",
    where="category = 'ml'",  # optional metadata filter
    nprobes=10,
)
print(results.table.to_pandas())
```

**Index management:**

```python
lake.list_vector_indexes("articles")
lake.get_vector_index_info("articles", vector_column="text_embedding")
lake.rebuild_vector_index("articles", metric="l2")
lake.delete_vector_index("articles", index_name="idx_text_embedding")
```

### Full-Text Search

```python
# Create FTS index
lake.create_fts_index("articles", fts_column="body")

# Search
results = lake.text_search("articles", "attention mechanism",
    top_k=10,
    where="category = 'ml'",
    offset=0,  # pagination
)
print(results.table.to_pandas())  # columns include _score
```

**FTS index management:**

```python
lake.get_fts_index_info("articles")
lake.delete_fts_index("articles")
```

### Hybrid Search (RRF Fusion)

Combines vector similarity and full-text relevance via Reciprocal Rank Fusion:

```python
results = lake.hybrid_search("articles",
    query_vector=[0.1, 0.2, ...],
    query_text="attention mechanism",
    top_k=10,
    vector_column="text_embedding",
    fts_column="body",
    where="category = 'ml'",
)
print(results.table.to_pandas())  # includes _rrf_score
```

### Faceted Search

Returns search results alongside facet counts for drill-down:

```python
results = lake.faceted_search("articles",
    query_vector=[0.1, 0.2, ...],
    facets=["category", "year"],
    top_k=10,
)
for row in results.table.to_pylist():
    print(row["title"], row["_distance"])
print(results.facet_counts)  # {"category": {"ml": 15, "genai": 8}, ...}
```

### Ensemble Search (Multi-Model)

Searches multiple embedding columns and fuses with weighted RRF:

```python
results = lake.ensemble_search("articles",
    query_vector=[0.1, 0.2, ...],
    columns=["text_embedding", "title_embedding", "image_embedding"],
    weights={"text_embedding": 1.0, "title_embedding": 0.5, "image_embedding": 0.3},
    top_k=10,
)
```

---

## 6. SQL Analytics

### OLAP Queries via DuckDB

```python
result = lake.olap_query("sales", """
    SELECT
        product_category,
        DATE_TRUNC('month', sale_date) AS month,
        SUM(amount) AS revenue,
        COUNT(*) AS orders
    FROM sales
    WHERE sale_date >= '2025-01-01'
    GROUP BY 1, 2
    HAVING revenue > 1000
    ORDER BY revenue DESC
    LIMIT 50
""", max_rows=100)
print(result.table.to_pandas())
```

DuckDB supports: window functions, CTEs, JOINs, subqueries, and the full SQL syntax.

### Metadata Query

```python
result = lake.query("articles", "SELECT * FROM articles LIMIT 10")
```

### JOIN Across Datasets

```python
result = lake.olap_query("orders", """
    SELECT o.order_id, c.name, o.amount
    FROM orders o
    JOIN customers c ON o.customer_id = c.id
""", tables={"customers": lake.read_dataset("customers")})
```

### Materialized Views

```python
# Create a persistent materialized view with TTL
rows = lake.materialize("sales", """
    SELECT product_category, SUM(amount) AS total
    FROM sales
    GROUP BY product_category
""", view_name="sales_summary", ttl_days=7)

# Clean up expired views
dropped = lake.cleanup_materialized(ttl_days=7)
```

### Daft DataFrame API

```python
import daft

# Load as a lazy Daft frame for chained operations
frame = lake.daft_query("articles", columns=["title", "category"])
result = (
    frame
    .filter(daft.col("category") == "ml")
    .select("title")
    .sort("title")
    .collect()  # materialize as Arrow Table
)
```

---

## 7. RAG Pipeline

### Configuration

Set your LLM provider in config or environment:

```yaml
# configs/rag.yaml
llm:
  provider: openai
  model: gpt-4o
  api_key: ${OPENAI_API_KEY}
  context_window_tokens: 128000

rag:
  default_top_k: 5
  default_strategy: hybrid
  chunk_size: 512
  chunk_overlap: 64
  reranker_enabled: true
```

```bash
# Or via environment variables
export ARROW_LAKE__LLM__PROVIDER=openai
export ARROW_LAKE__LLM__MODEL=gpt-4o
export ARROW_LAKE__LLM__API_KEY=sk-...
```

### Query

```python
import asyncio

async def main():
    response = await lake.rag_query(
        "What is attention in transformers?",
        dataset="articles",
        top_k=5,
        strategy="hybrid",       # "fts", "vector", or "hybrid"
        template_name="default_qa",
    )
    print(response.answer)
    for citation in response.citations:
        print(f"  [{citation.score:.2f}] {citation.text[:100]}...")

asyncio.run(main())
```

### Streaming

```python
async for chunk in lake.rag_query_stream(
    "Explain vector databases",
    dataset="docs",
    top_k=5,
):
    print(chunk, end="", flush=True)
```

### Session Management

```python
# Conversations with history
response = await lake.rag_query("What is deep learning?", dataset="docs",
    session_id="user-123-session-1")

response = await lake.rag_query("How does backpropagation work?", dataset="docs",
    session_id="user-123-session-1")  # remembers context

# Get conversation history
history = lake.rag_get_history("user-123-session-1")

# Submit feedback
lake.rag_feedback("user-123-session-1", turn_id=1, rating="positive",
    comment="Clear explanation")

# Get feedback
feedback = lake.rag_get_feedback("user-123-session-1")
```

### Batch Queries

```python
questions = ["What is CNN?", "What is RNN?", "What is GAN?"]
responses = await lake.rag_batch_query(questions, dataset="docs",
    top_k=3, concurrency=5)
```

### Entity Extraction

```python
response = await lake.rag_extract("articles",
    text_column="body",
    top_k=10,
)
print(response.answer)  # extracted entities as JSON
```

### GraphRAG (Knowledge Graph Augmented)

When `hugegraph.enabled=true`, the RAG pipeline automatically augments retrieval
with knowledge graph context for richer answers:

```yaml
hugegraph:
  enabled: true
  host: "localhost"
  port: 8081
  graph: "research_graph"
  default_traversal_depth: 2
```

The same `rag_query` call now uses GraphRAG behind the scenes.

---

## 8. Knowledge Graph

Requires `hugegraph.enabled=true` in config. Backed by HugeGraph with
Gremlin traversal and Vermeer OLAP algorithms.

### Build a Knowledge Graph

```python
import asyncio

async def main():
    task_id = await lake.kg_build("articles")
    print(f"Build started: {task_id}")

    # Check progress
    status = await lake.kg_build_status(task_id)
    print(f"Status: {status['status']}")
    print(f"Processed: {status['processed_chunks']}/{status['total_chunks']}")
    print(f"Entities: {status['entity_count']}, Relations: {status['relation_count']}")

asyncio.run(main())
```

### Document Types & Templates (doc_type routing)

KG extraction is driven by **hyper-extract templates** (29 presets across 6
categories). Each chunk's `doc_type` resolves to one template through a
three-layer router (first hit wins):

1. **Explicit override** — `HugeGraphConfig.he_doc_type_templates` (operator
   control; highest priority).
2. **Auto match** — if no `doc_type` is passed, a classifier infers one from the
   document content, then the gallery matches it by tag/category/name/description.
3. **Default** — `HugeGraphConfig.he_default_template` (`concept_graph`, the project-local strict template at `arrow_lake/knowledge_graph/templates/concept_graph.yaml` with a fixed type/relation enum + **required `definition`**; v1.8.9 default/paper/report switched here from the gallery `general/concept_graph` preset, which left `definition` optional and produced noisy free-typed entities — definition coverage went 0%→100%).

**Recommendation:** pass `doc_type` explicitly at ingest for any known document
kind. This bypasses the classifier (which judges the whole document once and can
misroute), and is the single most effective lever for extraction quality.

```python
# Explicit doc_type → routes to the right template, no classifier guess
await lake.ingest_documents(files=["report.pdf"], doc_type="report")
await lake.ingest_documents(files=["paper.pdf"], doc_type="paper")
```

**Discover available templates** before you ingest — the gallery is exposed at
all three layers:

```python
# SDK
doc_types = await lake.kg_list_doc_types()        # 10 canonical doc_types
templates = await lake.kg_list_templates()        # all presets
templates = await lake.kg_list_templates(category="tcm")
detail    = await lake.kg_describe_template("general/concept_graph")
```

```bash
# CLI
alake kg list-doc-types
alake kg list-templates
alake kg list-templates --category finance
alake kg describe-template general/concept_graph
```

```bash
# REST (Viewer role)
curl 127.0.0.1:8000/api/v1/kg/doc-types
curl 127.0.0.1:8000/api/v1/kg/templates
curl 127.0.0.1:8000/api/v1/kg/templates/general/concept_graph
```

> ⚠️ **High-risk templates:** the four `hypergraph` presets
> (`tcm/formula_composition`, `tcm/syndrome_reasoning`, `medicine/treatment_map`,
> `legal/contract_obligation`) crash or yield **0 entities** on sparse/atypical
> content. The auto layer **degrades** them to the default
> (`resolve_with_source` returns `resolution="degraded"`), so a misclassified
> chunk never zeroes the build. Explicit overrides still reach them if an
> operator forces one via `he_doc_type_templates`.

### Query (Gremlin)

```python
results = await lake.kg_query(
    "g.V().has('name', 'attention').outE('relatedTo').inV()"
)
for r in results:
    print(r)
```

### Traversal

```python
# Neighbor exploration
neighbors = await lake.kg_get_neighbors("entity:42", depth=2)

# Path algorithms
paths = await lake.kg_all_shortest_paths("entity:A", "entity:B", max_depth=10)
wpath = await lake.kg_weighted_shortest_path("entity:A", "entity:B")
rays = await lake.kg_rays("entity:A", max_depth=5)
rings = await lake.kg_rings("entity:A", max_depth=5)
crosspoints = await lake.kg_crosspoints("entity:A", "entity:B")
custom = await lake.kg_customized_paths("entity:A", steps=[
    {"label": "relates_to", "direction": "OUT"},
    {"label": "part_of", "direction": "IN"},
])
```

### OLAP Algorithms (Vermeer)

```python
rank = await lake.kg_pagerank(iterations=20, damping_factor=0.85)
communities = await lake.kg_louvain(resolution=1.0)
components = await lake.kg_wcc()
triangles = await lake.kg_triangle_count()
degree = await lake.kg_degree_centrality()
closeness = await lake.kg_closeness_centrality()
betweenness = await lake.kg_betweenness_centrality()
core = await lake.kg_k_core(k=3)
labels = await lake.kg_label_propagation()
```

### Graph Statistics & Management

```python
stats = await lake.kg_stats()           # vertex/edge counts
await lake.kg_delete_graph()            # clear all data (irreversible)

# Export / Import
graph_data = await lake.kg_export_graph(with_properties=True)
result = await lake.kg_import_graph(graph_data)
```

### Per-dataset isolation (v1.8.6)

Each Lance dataset maps to its **own** HugeGraph graph `kg_{dataset}` — data is
fully isolated across datasets, and dropping a dataset clears only its graph.

```python
# SDK: pass dataset_name to scope any KG op to kg_{dataset}
stats = await lake.kg_stats(dataset_name="articles")
nbrs  = await lake.kg_get_neighbors("entity:42", depth=2, dataset_name="articles")
paths = await lake.kg_all_shortest_paths("A", "B", dataset_name="articles")
await lake.kg_delete_graph(dataset_name="articles")   # clear only kg_articles
# kg_build always auto-isolates per dataset — no arg needed
```

```bash
# CLI: --dataset scopes stats / neighbors / delete + all 8 traversers
alake kg stats --dataset articles
alake kg neighbors entity:42 --dataset articles
alake kg traverser all-shortest-paths A B --dataset articles
alake kg delete --yes --dataset articles      # clear only kg_articles
```

```bash
# REST: ?dataset= query param (per-dataset ACL enforced on read/delete)
GET    /api/v1/kg/stats?dataset=articles
GET    /api/v1/kg/entities/{id}/neighbors?dataset=articles
DELETE /api/v1/kg/graph?dataset=articles                              # ADMIN
POST   /api/v1/kg/traversers/rays   {"source":"A","dataset":"articles"}  # +7 more
```

---

## 9. Data Quality

### Quality Filters

```python
# Run built-in filters (text length, image resolution)
report = lake.quality_filter("articles",
    active_filters="text_length_check,image_resolution_check")
print(f"Accepted: {report.total_accepted}, Rejected: {report.total_rejected}")
for fr in report.filter_results:
    print(f"  {fr.filter_name}: passed={fr.passed_count}, rejected={fr.rejected_count}")
```

### Deduplication

```python
# Exact hash dedup
result = lake.deduplicate("articles", strategy="exact", action="remove")

# Perceptual hash dedup (for near-duplicate images)
result = lake.deduplicate("articles", strategy="perceptual", perceptual_threshold=8)

# Combined strategy
result = lake.deduplicate("articles", strategy="both", action="flag")
# action="flag" marks duplicates without removing them
```

### Configuration

```yaml
quality:
  enabled: true
  filter_mode: all              # "all" = AND, "any" = OR
  schema_validation: strict
  dead_letter_enabled: true    # route rejected rows to dead-letter dataset
  text_min_chars: 1
  text_max_chars: 100000
  image_min_width: 128
  image_min_height: 128
  dedup_enabled: true
  dedup_strategy: both          # "exact", "perceptual", "both"
  dedup_action: remove          # "flag" or "remove"
  dedup_perceptual_threshold: 8
  active_filters: "text_length_check,image_resolution_check,null_check"
```

---

## 10. Export

### To Parquet or CSV

```python
result = lake.export("articles",
    output_path="/tmp/articles.parquet",
    format="parquet",             # or omit (auto-detected from extension)
    columns=["title", "category"], # optional subset
    version=3,                    # time travel export
    compression="snappy",
    overwrite=True,
)
print(result.rows_exported, result.output_path)
```

### To External Systems

```python
# Export via Daft to various targets
result = lake.export_to("articles",
    target_uri="s3://bucket/exports/articles",
    format="parquet",
)

result = lake.export_to("articles",
    target_uri="postgresql://user:pass@host/db",
    format="clickhouse",
)
```

### Export Audit Trail

```python
audit_data = lake.audit_export("articles")
```

---

## 11. Lineage & Audit

### Lineage

```python
# Record events
lake.lineage_record_event("articles", "ingest",
    source_datasets=["raw_articles"],
    transform_type="clean",
    actor="pipeline_v2",
    metadata={"rows": 1500},
)

# Query history
history = lake.lineage_history("articles")

# SQL query over lineage
lineage_table = lake.lineage_query("""
    SELECT * FROM lineage
    WHERE operation = 'ingest'
    ORDER BY timestamp DESC
""")

# Full graph
graph = lake.lineage_graph("articles", max_depth=10)

# Downstream impact analysis
impacts = lake.lineage_impact("raw_articles")
```

### Audit Trail

```python
# Record
audit_id = lake.audit_record("ingest", dataset_name="articles",
    actor="system", payload={"rows": 1500})

# Verify HMAC integrity
is_valid = lake.audit_verify(audit_id)

# Query
entries = lake.audit_query(
    dataset_name="articles",
    start="2025-01-01T00:00:00Z",
    event_type="ingest",
)

# Anomaly detection
anomalies = lake.audit_analyze()
```

---

## 12. Production Deployment

### Docker Compose (recommended)

The deploy directory includes multiple compose files:

```bash
# Start the full stack
docker compose -f deploy/docker-compose.prod.yml up -d

# With GPU support
docker compose -f deploy/docker-compose.gpu.yml up -d

# With HugeGraph for knowledge graphs
docker compose -f deploy/docker-compose.hugegraph.yml up -d

# With monitoring (Prometheus + Grafana)
docker compose -f deploy/docker-compose.monitoring.yml up -d
```

### REST API Server

```bash
arrow-lake --config configs/prod.yaml serve --host 0.0.0.0 --port 8000
```

The REST API provides HTTP endpoints for all SDK operations:
search, query, ingest, RAG, catalog, health checks, metrics.

### TLS Configuration

```yaml
# configs/prod.yaml
api:
  tls_enabled: true
  ssl_certfile: "/etc/arrow-lake/tls/tls.crt"
  ssl_keyfile: "/etc/arrow-lake/tls/tls.key"
  security_headers_enabled: true
  content_security_policy: "default-src 'none'; frame-ancestors 'none'"
  docs_enabled: false
  cors_origins: []
```

### Rate Limiting

```yaml
rate_limit:
  enabled: true
  default_requests_per_minute: 120
  default_burst: 20
  exempt_paths:
    - "/health"
    - "/metrics"
```

### Health Checks

```python
health = lake.health()
print(health.status)           # "ok" or "degraded"
print(health.version)          # "1.5.2"
print(health.storage_status)   # "accessible" or ...
print(health.uptime_seconds)
print(health.session_pool)     # DuckDB pool stats
```

### Backup & Restore

```python
# Create backup
info = lake.backup_create(dataset_names=["articles", "users"],
    backup_id="nightly-20250604")

# List backups
backups = lake.backup_list()

# Restore
lake.backup_restore("nightly-20250604",
    dataset_names=["articles"],
    overwrite=True)

# Delete
lake.backup_delete("nightly-20250604")
```

### Blob Lifecycle (S3)

```python
# Apply tiering rules (standard -> IA -> Glacier)
result = lake.lifecycle_apply(prefix="articles/")

# Check current tier status
status = lake.lifecycle_status(prefix="articles/")

# Restore a Glacier object
lake.lifecycle_restore("articles/archive/old_data.lance", days=7)

# Estimate cost savings
estimate = lake.lifecycle_estimate(total_size_gb=500, target_tier="STANDARD_IA")

# Preview rules without applying
preview = lake.lifecycle_rules(prefix="articles/")
```

### Observability

```yaml
observability:
  metrics_enabled: true
  metrics_port: 8000
  metrics_path: "/metrics"
  log_level: INFO
```

Prometheus metrics are exposed at `/metrics`:
- `ingestion_rows_total`, `ingestion_duration_seconds`
- `query_total`, `query_latency_seconds`, `query_results_total`
- `catalog_queries_total`, `catalog_tables_total`
- `processing_quality_rejects_total`
- `system_uptime_seconds`

---

## 13. Configuration Reference

### Precedence (low to high)

1. Code defaults (Pydantic field defaults)
2. `.env` file (via pydantic-settings)
3. Environment variables (`ARROW_LAKE__` prefix)
4. YAML config file (highest priority)

```bash
# Environment variable format: ARROW_LAKE__SECTION__FIELD
export ARROW_LAKE__STORAGE__BACKEND=s3
export ARROW_LAKE__STORAGE__S3_BUCKET=my-lake
export ARROW_LAKE__LLM__PROVIDER=openai
export ARROW_LAKE__LLM__MODEL=gpt-4o
```

### Config Sections (32)

| Section | Class | Description |
|---------|-------|-------------|
| `storage` | `StorageConfig` | Backend type, S3 credentials, base URI |
| `compute` | `ComputeConfig` | GPU, worker count |
| `observability` | `ObservabilityConfig` | Metrics, logging, log level |
| `http` | `HttpConfig` | HTTP client settings |
| `media` | `MediaConfig` | Media processing defaults |
| `embedding` | `EmbeddingConfig` | Model, backend (local/openai/daft), batch size |
| `decode` | `DecodeConfig` | Image/video decode quality |
| `vector` | `VectorSearchConfig` | Default metric, index type, top_k |
| `fts` | `FullTextSearchConfig` | FTS column, tokenizer settings |
| `hybrid` | `HybridSearchConfig` | RRF weights, fusion params |
| `olap` | `OlapConfig` | DuckDB memory budget, warmup, materialized views |
| `daft` | `DaftConfig` | Daft execution settings |
| `quality` | `QualityConfig` | Filters, dedup, schema validation, dead letter |
| `workflow` | `WorkflowConfig` | Metaflow workflow settings |
| `argo` | `ArgoConfig` | Argo workflow namespace, timeouts |
| `autoscale` | `AutoscaleConfig` | Ray autoscaling bounds |
| `lifecycle` | `LifecycleConfig` | S3 tiering rules, expiration |
| `faceted` | `FacetedSearchConfig` | Facet computation settings |
| `ensemble` | `EnsembleSearchConfig` | Multi-model search weights |
| `lineage` | `LineageConfig` | Lineage tracking settings |
| `export` | `ExportConfig` | Default export format, compression |
| `audit` | `AuditConfig` | HMAC key, audit dataset name |
| `api` | `ApiConfig` | TLS, CORS, security headers, docs |
| `llm` | `LLMConfig` | Provider, model, API key, context window |
| `rag` | `RAGConfig` | Top-k, strategy, chunking, reranker |
| `hugegraph` | `HugeGraphConfig` | HugeGraph host, graph name, traversal depth |
| `opentelemetry` | `OpenTelemetryConfig` | Distributed tracing settings |
| `auth` | `AuthConfig` | Authentication mode and settings |
| `rate_limit` | `RateLimitConfig` | Per-minute limits, burst, exempt paths |
| `document` | `DocumentConfig` | PDF parsing, OCR, chunking |
| `redis` | `RedisConfig` | Redis connection for session management |
| `gravitino` | `GravitinoConfig` | Gravitino metadata catalog integration |

### Example Production YAML

```yaml
# configs/prod.yaml
storage:
  backend: s3
  s3_bucket: arrow-lake-production
  s3_region: us-east-1

embedding:
  backend: openai
  model: text-embedding-3-small
  api_key: ${OPENAI_API_KEY}

llm:
  provider: openai
  model: gpt-4o
  api_key: ${OPENAI_API_KEY}
  context_window_tokens: 128000

vector:
  metric: cosine
  default_index_type: IVF_PQ
  default_top_k: 10

olap:
  memory_budget_mb: 4096
  warmup_enabled: true
  max_result_rows: 10000

quality:
  enabled: true
  dedup_strategy: both
  dead_letter_enabled: true

rag:
  default_top_k: 5
  default_strategy: hybrid

hugegraph:
  enabled: false  # enable for KG features

observability:
  metrics_enabled: true
  log_level: WARNING
```

---

## 14. CLI Reference

### Common Commands

```bash
# System
arrow-lake --version
arrow-lake status                           # Health + system info

# Serve
arrow-lake serve --host 0.0.0.0 --port 8000

# Demo
arrow-lake demo                             # Interactive walkthrough

# Catalog
arrow-lake catalog list                     # List all datasets
```

### Ingestion

```bash
arrow-lake ingest files <dataset> <path> [<path>...]
arrow-lake ingest http <dataset> <url> [<url>...]
arrow-lake ingest images <dataset> <path> [<path>...]
arrow-lake ingest videos <dataset> <path> [<path>...]
arrow-lake ingest documents <dataset> <path> [<path>...]
arrow-lake ingest sql <dataset> --sql "SELECT ..." --connection "postgresql://..."
arrow-lake ingest kafka <dataset> --bootstrap-servers "host:9092" --topics "events"
```

### Search

```bash
arrow-lake search vector <dataset> --query "search text" --top-k 10
arrow-lake search fts <dataset> --query "keyword search" --top-k 10
arrow-lake search hybrid <dataset> --query "text" --vector-column text_embedding
arrow-lake search faceted <dataset> --query "text" --facets "category,year"
```

### Query

```bash
arrow-lake query sql <dataset> --sql "SELECT category, COUNT(*) FROM dataset GROUP BY category"
```

### Knowledge Graph

```bash
arrow-lake kg build <dataset>              # Build KG from dataset
arrow-lake kg status <task-id>             # Check build progress
arrow-lake kg stats                        # Graph statistics
arrow-lake kg query "g.V().count()"        # Gremlin query
```

### RAG

```bash
arrow-lake rag query docs "What is deep learning?" --top-k 5
arrow-lake rag extract docs --text-column body
```

### Quality & Maintenance

```bash
arrow-lake quality filter <dataset> --filters "text_length_check,null_check"
arrow-lake quality dedup <dataset> --strategy both --action remove
arrow-lake maintenance run
```

### Export

```bash
arrow-lake export <dataset> --output /tmp/data.parquet --format parquet
```

### Lineage & Audit

```bash
arrow-lake lineage record <dataset> ingest --sources "raw_data"
arrow-lake lineage history <dataset>
arrow-lake audit query --dataset <dataset> --event-type ingest
```

### Backup

```bash
arrow-lake backup create --datasets "articles,users"
arrow-lake backup list
arrow-lake backup restore <backup-id>
```

### Global Options

```bash
arrow-lake --config configs/prod.yaml <command>    # Use YAML config
arrow-lake --format json <command>                         # JSON output
arrow-lake --verbose <command>                      # Verbose logging
```

---

## Exception Handling

Arrow Lake uses a typed exception hierarchy for precise error handling:

```python
from arrow_lake import (
    ArrowLakeError, StorageError, ValidationError,
    QueryError, EmbeddingError, RAGError,
    KGError, QualityError, IngestError,
)

try:
    lake.create_dataset("users", table)
except ValidationError as e:
    print(f"Invalid data: {e}")
except StorageError as e:
    print(f"Storage issue: {e}")
```

---

*Arrow Lake v1.10.0 | Apache-2.0 License | [GitHub](https://github.com/wits-sunpw/arrow-lake)*
