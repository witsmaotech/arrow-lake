# Data Plane

> You are a **data engineer** who moves, stores, transforms, and governs structured and unstructured data across the lake.

Your data flows through this path:

```
Ingest (CLI / SDK / REST API)
  --> Validate & Quality Gate
    --> Store (Lance columnar format)
      --> Index (Vector / FTS / Scalar)
        --> Query (DuckDB SQL / Daft DataFrame)
          --> Export or Serve downstream
```

## Core Tasks

### 🟢 Starter

| Task | Description |
|------|-------------|
| [Install & Configure](ingest/install.md) | Set up Arrow Lake with `pip install arrow-lake`, configure storage backends (local / MinIO / S3) |
| [Ingest Data](ingest/quickstart.md) | Load Parquet, JSONL, CSV, and multimodal files via CLI, SDK, or REST API |
| [Create & Query Datasets](storage/datasets.md) | Create Lance datasets, append data, read versions, list datasets with `LanceStorageManager` |

### 🟡 Professional

| Task | Description |
|------|-------------|
| [SQL Analytics](storage/sql-analytics.md) | Run DuckDB SQL directly over Lance datasets -- joins, aggregations, window functions, time travel queries |
| [Schema Evolution & Versioning](storage/versioning.md) | Add columns, change types, and travel across dataset versions without rewriting data |
| [Quality Gates](quality/README.md) | Validate row counts, null ratios, schema conformance on ingest; reject dirty batches automatically |
| [Full-Text Search](indexing/fts.md) | Create Tantivy-based FTS indexes with jieba tokenization for Chinese + English text search |

### 🔴 Enterprise

| Task | Description |
|------|-------------|
| [Catalog & Metadata](catalog/README.md) | Register datasets in Apache Gravitino for federated metadata management across engines |
| [Lifecycle & Retention](storage/lifecycle.md) | Configure retention policies, automated cleanup, and version pruning for production datasets |

## Next Steps

- **Need vector search or RAG?** Your indexed data feeds the [Knowledge Plane](../knowledge-plane/README.md) -- set up embeddings there.
- **Deploying to production?** See the [Compute Plane](../compute-plane/README.md) for Docker Compose and Helm charts.
- **Architecture deep-dive?** Read [Three-Layer Architecture](../concepts/architecture.md) to understand how the Data Plane sits in the Kernel layer.
