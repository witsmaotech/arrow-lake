# Arrow Lake Quickstart

> Go from zero to a working data lake in 5 minutes: create, ingest, run SQL queries, and export results.

***

## 1. Prerequisites

### System Requirements

* Python 3.11+
* Operating system: Linux / macOS / WSL2

### Install Dependencies

```bash
# Clone the repository
git clone https://github.com/witshine/wits-infra-dintellihub.git
cd wits-infra-dintellihub

# Install all dependencies with uv (recommended)
uv sync

# Or use pip (core install; add extras like [rag,he,docling,fts] as needed)
pip install -e .
```

### Verify Installation

```bash
# Check version and dependencies
arrow-lake version

# Example output:
# ┌───────────┬──────────┐
# │ Component │ Version  │
# ├───────────┼──────────┤
# │ arrow-lake│ 1.10.4   │
# │ python    │ 3.12.4   │
# │ daft      │ 0.7.21   │
# │ pyarrow   │ 23.0.1   │
# │ duckdb    │ 1.5.5    │
# └───────────┴──────────┘
```

***

## 2. Five-Minute Example: Create → Ingest → Query → Export

Save the snippet below as `quickstart_demo.py` and run it with `python quickstart_demo.py` (or paste it into a Python REPL / Jupyter cell). It uses only local storage — no Docker, no external services.

```python
"""quickstart_demo.py — Arrow Lake minimal working example"""
from arrow_lake import Lake
import pyarrow as pa

# 1. Initialize the Lake (data stored in ./my_lake directory)
lake = Lake(base_uri="./my_lake")

# 2. Create a dataset — write directly from an Arrow Table
data = pa.table({
    "name": ["Alice", "Bob", "Charlie", "Diana"],
    "age": [30, 25, 35, 28],
    "department": ["Engineering", "Product", "Engineering", "Design"],
})
lake.create_dataset("users", data)
print(f"Created users: {data.num_rows} rows")

# 3. Append data — schema must match
more_data = pa.table({
    "name": ["Eve", "Frank"],
    "age": [32, 27],
    "department": ["Engineering", "Product"],
})
lake.append_dataset("users", more_data)

# 4. SQL query
# Note: SQL table name must match the dataset name ("users")
result = lake.query("users", "SELECT * FROM users WHERE age > 26")
print(result.to_pandas())

# 5. OLAP aggregation — supports GROUP BY, window functions, JOINs
olap_result = lake.olap_query(
    "users",
    "SELECT department, COUNT(*) AS cnt, AVG(age) AS avg_age "
    "FROM users GROUP BY department ORDER BY cnt DESC",
)
print(olap_result.table.to_pandas())

# 6. Export to Parquet
# Note: Parent directory "output/" must exist; create it first or use an absolute path.
lake.export("users", "output/users.parquet", columns=["name", "age"])

# 7. Browse the catalog
catalog = lake.catalog()
for ds in catalog.datasets:
    print(f"  {ds.name}: {ds.num_rows} rows, v{ds.version}")

# 8. Shut down
lake.shutdown()

# Tip: Use as context manager for automatic cleanup
# with Lake(base_uri="./my_lake") as lake:
#     lake.create_dataset("users", data)
#     # ... lake.shutdown() called automatically on exit
```

***

## 3. CLI Cheat Sheet

Arrow Lake ships with the `arrow-lake` command-line tool for everyday operations.

### Start the API Server

```bash
# Production mode
arrow-lake serve --host 0.0.0.0 --port 8000

# Development mode (hot reload)
arrow-lake serve --reload

# Open the Swagger docs
# http://localhost:8000/docs
```

### Check Lake Status

```bash
# List all datasets and their metadata
arrow-lake --base-uri ./my_lake status

# Example output:
# ┌──────────┬──────┬──────────────────┬─────────┐
# │ Name     │ Rows │ Columns          │ Version │
# ├──────────┼──────┼──────────────────┼─────────┤
# │ users    │    6 │ name, age, dep…  │       2 │
# │ products │  120 │ title, price, …  │       1 │
# └──────────┴──────┴──────────────────┴─────────┘
```

### Ingest Data

```bash
# Ingest a local file into a target dataset
arrow-lake --base-uri ./my_lake ingest files sales datas/transactions/sales_2024.csv

# Supported formats: CSV, JSON, JSONL, Parquet
```

### Search

> The `users` dataset built above has no text/vector columns, so search needs a dataset with them. The fastest way to *see* search in action is the built-in demo (it creates searchable data), or jump to [04 Vector Search](./04-vector-search.md) / [05 Full-Text Search](./05-fulltext-search.md).

```bash
# Run the built-in demo — creates a dataset with text + vectors, then runs search
arrow-lake demo --base-uri ./demo_data

# Full-text search (needs a dataset with a text/FTS column)
arrow-lake --base-uri ./my_lake search fts <dataset> \
    --query "electronics" --top-k 10

# Hybrid search (vector + full-text RRF fusion; needs both columns)
arrow-lake --base-uri ./my_lake search hybrid <dataset> \
    --query "wireless mouse"
```

### Interactive Demo

```bash
# Run the built-in demo (no Docker, no config needed)
arrow-lake demo --base-uri ./demo_data

# Keep the demo data after it finishes
arrow-lake demo --no-cleanup
```

***

## 4. Directory Layout

Arrow Lake uses [Lance](https://lancedb.github.io/lance/) columnar format for storage.
After initialization, the `base_uri` directory looks like this:

```text
my_lake/                          # base_uri root
├── users/                        # dataset name
│   ├── .lance/                   # Lance metadata directory
│   │   ├── versions/             # Data versioning
│   │   │   ├── 1.manifest        # Version 1 manifest
│   │   │   └── 2.manifest        # Version 2 manifest (created after append)
│   │   └── _metadata.json        # Schema and statistics
│   ├── data/                     # Lance columnar data files
│   │   ├── xxx.lance             # Data shard files
│   │   └── ...
│   └── indices/                  # Index files (optional)
│       ├── vector/               # Vector index (IVF-PQ)
│       └── fts/                  # Full-text index (native FTS (ICU))
├── products/                     # Another dataset
│   └── ...
└── ingest_dlq.jsonl              # Dead letter queue (failed ingestion records)
```

### Key Concepts

| Concept        | Description                                             |
| -------------- | ------------------------------------------------------- |
| **dataset**    | A Lance dataset, equivalent to a table                  |
| **version**    | Lance-native versioning, auto-incremented on each write |
| **base\_uri**  | Lake storage root; accepts local paths or S3 URIs       |
| **S3 backend** | Set to `s3://bucket/prefix` to use MinIO or AWS S3. See [03-Configuration](./03-configuration.md#3-storage-configuration-storageconfig) for credential setup. |

***

## 5. Creating a Lake from YAML Configuration

For production use, a YAML config file is the recommended way to manage all parameters:

```yaml
# config.yaml — top-level sections map to ArrowLakeConfig fields (see config/main.py)
storage:
  backend: local          # local dev; use minio/s3 in production (see 12-deployment)
  base_uri: ./data        # storage root (local path or s3://bucket/prefix)

olap:
  max_result_rows: 100000 # max rows returned per query
  lance_scan_mode: "auto" # valid values: auto / native / pyarrow_fallback only

vector:                   # VectorSearchConfig
  metric: "cosine"        # cosine / l2 / dot
  default_index_type: "IVF_PQ"
  num_sub_vectors: 24     # 24 recommended for 1024-dim (must be a multiple of 8)

fts:                      # FullTextSearchConfig
  fts_column: "text_content"
  tokenizer_type: "jieba" # jieba segmentation recommended for Chinese

# v1.9.0 control plane (RBAC / identity / personal_token / task history / RAG sessions via libSQL)
system_db:
  enabled: false          # set true in production and configure url (see 12-deployment); off by default locally
```

```python
from arrow_lake import Lake

# Create a Lake instance from a config file
lake = Lake.from_yaml("config.yaml", base_uri="./production_data")
```

> **Required for production (v1.9.6)**: set the `ARROW_LAKE__MASKING__HMAC_KEY` environment
> variable to enable masking governance (fail-fast — startup is blocked if it is missing;
> this key is a plain env var, not part of the YAML). When `system_db.enabled: true`, RBAC /
> identity / personal_token run on libSQL, and any store outage fails closed (returns 401).
> See [12-Deployment & Operations](./12-deployment.md).

***

## 6. Next Steps

After completing the quickstart, explore the rest of the Cookbook:

* **[02-Data Ingestion Guide](./02-ingestion.md)** — Multi-modal ingestion for CSV/JSON/Parquet/images/video/PDF
* **[04-Vector Search](./04-vector-search.md)** — `lake.search()`, `lake.create_vector_index()`
* **[05-Full-Text Search](./05-fulltext-search.md)** — `lake.text_search()`, `lake.create_fts_index()`
* **[06-Hybrid & Faceted](./06-hybrid-faceted.md)** — `lake.hybrid_search()`, `lake.faceted_search()`, `lake.ensemble_search()`
* **[07-OLAP Analytics](./07-olap-analytics.md)** — `lake.olap_query()`, `lake.materialize()`, `lake.daft_query()`
* **[08-RAG Pipeline](./08-rag-pipeline.md)** — `lake.rag_query()`, `lake.rag_extract()`, streaming RAG
* **[09-Knowledge Graph](./09-knowledge-graph.md)** — `lake.kg_build()`, `lake.kg_query()`, GraphRAG
* **[10-REST API Guide](./10-rest-api.md)** — Full HTTP API reference after `arrow-lake serve`
* **[11-Quality & Dedup](./11-quality-dedup.md)** — `lake.quality_filter()`, `lake.deduplicate()`
* **[12-Deployment](./12-deployment.md)** — Docker, Helm, production checklist
* **[13-CLI Reference](./13-cli-reference.md)** — Complete CLI command manual
* **[15-Gravitino](./15-gravitino-metadata.md)** — Metadata governance with Apache Gravitino

***

> **Environment Variables**: Arrow Lake supports `.env` files and environment variables for S3 credentials,
> LLM API keys, and more. See the `ArrowLakeConfig` documentation for the full list of configurable options.
