# Arrow Lake

> Unified multimodal data lakehouse for AI/ML teams. Text, images, audio, vectors — one platform.

[![Tests](https://img.shields.io/badge/tests-1673%20passing-brightgreen)](https://github.com)
[![Coverage](https://img.shields.io/badge/coverage-82%25+-green)](https://github.com)
[![Python](https://img.shields.io/badge/python-3.11+-blue)](https://github.com)
[![License](https://img.shields.io/badge/license-MIT-informational)](https://github.com)

## Install

```bash
pip install arrow-lake
```

## Quickstart

```python
from arrow_lake import Lake
import pyarrow as pa

lake = Lake("./my_lake")

# Create a dataset from an Arrow Table
table = pa.table({
    "id": ["1", "2", "3"],
    "text": ["machine learning", "deep learning", "data analytics"],
    "category": ["ml", "dl", "data"],
})
lake.create_dataset("articles", table)

# SQL analytics
result = lake.olap_query(
    "articles",
    "SELECT category, COUNT(*) as cnt FROM articles GROUP BY category",
)
print(result.table.to_pandas())
```

No Docker. No config files. From `pip install` to first result in under a minute.

## Try the Demo

```bash
arrow-lake demo
```

Runs a self-contained demo with synthetic data — vector search, SQL analytics, and full-text search in ~15 seconds. No setup required.

## What's Inside

| Capability | Description |
|---|---|
| **Vector Search** | Semantic + hybrid search (BM25 + vectors), IVF_PQ indexes |
| **SQL Analytics** | DuckDB-powered OLAP: GROUP BY, window functions, JOINs |
| **Full-Text Search** | LanceDB FTS with stemming and stop-word removal |
| **Data Quality** | Schema validation, null detection, content dedup |
| **Lineage** | Full-chain lineage tracking and SQL query |
| **Export** | Parquet / CSV with version selection and column projection |
| **REST API** | 35 endpoints with OpenAPI docs, API key auth, GZip |
| **Distributed** | Ray distributed ingestion, Metaflow workflow orchestration |

## CLI

```bash
arrow-lake demo                  # Interactive demo
arrow-lake serve                 # Start REST API server
arrow-lake ingest --source data.csv --table my_data
arrow-lake search --query "ML" --table my_data --top-k 5
arrow-lake status
```

## Configuration

Copy `.env.example` to `.env` and edit. For production, use YAML:

```python
lake = Lake.from_yaml("configs/prod.yaml")
```

For local development, just pass `base_uri`:

```python
lake = Lake("./data")  # local file storage, no MinIO needed
```

## Documentation

- [Usage Guide](docs/usage-guide.md) — comprehensive 16-chapter walkthrough
- [Examples](examples/) — 30+ scripts covering all features
- [API Docs](http://localhost:8000/docs) — auto-generated OpenAPI/Swagger

## Tech Stack

LanceDB + Daft + Ray + DuckDB + PyArrow + FastAPI

## License

MIT — Copyright (c) 2026 Witshine

---

中文文档: [README.zh.md](README.zh.md)
