# Arrow Lake

> Production-grade multimodal data lakehouse for AI/ML teams.
> Text, images, audio, vectors, knowledge graphs — one platform.

[![Tests](https://img.shields.io/badge/tests-2822%20passing-brightgreen)](https://github.com)
[![Coverage](https://img.shields.io/badge/coverage-78%25-green)](https://github.com)
[![Security](https://img.shields.io/badge/bandit-0%20HIGH-success)](https://github.com)
[![Python](https://img.shields.io/badge/python-3.11+-blue)](https://github.com)
[![Version](https://img.shields.io/badge/version-1.3.0-blue)](https://github.com)
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
| **Knowledge Graph** | HugeGraph integration: build, Gremlin query, GraphRAG |
| **RAG Pipeline** | Multi-provider LLM, session history, citations, streaming |
| **Document Pipeline** | PDF parse → chunk → embed → Lance, with OCR fallback |
| **Data Quality** | Schema validation, null detection, content dedup |
| **Lineage & Audit** | Full-chain lineage tracking, HMAC-verified audit trail |
| **Export** | Parquet / CSV with version selection and column projection |
| **Security** | RBAC (VIEWER/EDITOR/ADMIN), JWT blacklist, Gremlin injection defense |
| **REST API** | 40+ endpoints with OpenAPI docs, API key + JWT auth, rate limiting |
| **Distributed** | Redis session coordination, Ray distributed ingestion, Helm chart |

## CLI

```bash
arrow-lake demo                  # Interactive demo
arrow-lake serve                 # Start REST API server
arrow-lake ingest files my_data data.csv
arrow-lake search vector my_data --query "ML" --top-k 5
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

## Production Deployment

```bash
# Docker Compose
docker compose -f deploy/docker-compose.yml up -d

# Kubernetes (Helm)
helm install arrow-lake deploy/helm/arrow-lake/
```

Production features:
- **RBAC**: 3-tier role model (VIEWER / EDITOR / ADMIN) on 40+ endpoints
- **Redis**: Distributed session coordination, JWT blacklist persistence
- **TLS**: Configurable TLS termination + security headers
- **Helm**: Deployment, HPA, CronJob backup, Ingress, PDB, Secret templates
- **Audit**: HMAC-SHA256 verified tamper-evident audit trail
- **NetworkPolicy**: Restricted pod-to-pod communication

## Documentation

- [Usage Guide](docs/usage-guide.md) — comprehensive walkthrough
- [Cookbook](docs/cookbook/README.md) — 13 chapters + 43 examples
- [Security Policy](SECURITY.md) — auth, RBAC, audit, transport security
- [Contributing](CONTRIBUTING.md) — development setup and coding standards
- [API Docs](http://localhost:8000/docs) — auto-generated OpenAPI/Swagger
- [Changelog](CHANGELOG.md) — version history

## Tech Stack

LanceDB + Daft + Ray + DuckDB + PyArrow + FastAPI + HugeGraph + Redis

## License

MIT — Copyright (c) 2026 Witshine

---

中文文档: [README.zh.md](README.zh.md) | English: [README.en.md](README.en.md)
