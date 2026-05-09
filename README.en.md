# Arrow Lake

> Production-grade multimodal data lakehouse for AI/ML teams.
> Built with Lance + Daft + Ray. 2822 tests, 78% coverage, zero high-severity security issues.

## Overview

Arrow Lake is a unified data lakehouse platform for multimodal data (text, images, audio, video). Built on the DARMU stack (Daft + Arrow + Ray + Metaflow + MinIO + LanceDB), it provides end-to-end capabilities from data ingestion and quality control to vector search, knowledge graphs, and data lineage.

### Key Capabilities

- **Multimodal Ingestion** — Text/images/audio/video/PDF, batch and HTTP ingestion
- **Vector Search** — Semantic + hybrid search (BM25 + vectors), IVF_PQ indexes
- **Knowledge Graph** — HugeGraph integration: build, Gremlin query, GraphRAG
- **RAG Pipeline** — Multi-provider LLM, session history, citations, streaming
- **Document Pipeline** — PDF parse → chunk → embed → Lance, with OCR fallback
- **Data Quality** — Schema validation, null detection, content dedup (SHA-256 + pHash)
- **Data Lineage** — Full-chain lineage tracking and SQL query
- **Audit Trail** — HMAC-SHA256 tamper-evident audit logging
- **Security** — RBAC (VIEWER/EDITOR/ADMIN), JWT blacklist, Gremlin injection defense, path traversal prevention
- **Data Export** — Parquet / CSV with version selection and column projection
- **Distributed** — Redis session coordination, Ray distributed ingestion, Metaflow workflows
- **Observability** — structlog + Prometheus metrics + OpenTelemetry integration
- **REST API** — 40+ endpoints, OpenAPI docs, API Key + JWT auth, rate limiting
- **CLI** — `arrow-lake` command-line interface for ingest, query, and management

## Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│                         Arrow Lake SDK (Lake)                        │
├──────┬──────┬──────┬──────┬──────┬──────┬──────┬──────┬────────────┤
│Ingest│Search│Quality│Export│Catalog│ KG   │ RAG  │CLI   │  Server   │
├──────┴──────┴──────┴──────┴──────┴──────┴──────┴──────┴────────────┤
│           Core Layer (Config / Exceptions / Metrics / Auth)         │
├──────────────────────────┬──────────────────────────────────────────┤
│       Storage            │          Runtime                         │
│  LanceDB + MinIO/S3      │   Ray + Metaflow + Redis + HugeGraph     │
└──────────────────────────┴──────────────────────────────────────────┘
```

## Quick Start

### Install

```bash
uv venv && source .venv/bin/activate
uv sync
```

### Configure

```bash
cp .env.example .env
# Edit .env and configs/dev.yaml as needed
```

### Start Infrastructure

```bash
docker compose -f deploy/docker-compose.yml up -d
```

Services: MinIO (S3), Redis, Grafana, Prometheus, Jaeger.
Knowledge Graph: External HugeGraph deployment (network-bridged).

### Usage

```python
from arrow_lake import Lake

lake = Lake.from_yaml("configs/dev.yaml")

# Ingest data
report = lake.ingest("my_dataset", ["data/data.csv"])

# Vector search
results = lake.search("my_dataset", query_vector=[0.1, 0.2, ...], top_k=10)

# SQL analytics
result = lake.olap_query("my_dataset", "SELECT category, COUNT(*) FROM my_dataset GROUP BY category")

# Knowledge graph
task_id = await lake.kg_build("my_dataset")
stats = await lake.kg_stats()

# RAG Q&A
answer = await lake.rag_query("What is vector database?", dataset_name="docs")
```

## Production Deployment

### Docker Compose

```bash
docker compose -f deploy/docker-compose.yml up -d
```

### Kubernetes (Helm)

```bash
helm install arrow-lake deploy/helm/arrow-lake/
```

Production security features:
- **RBAC**: 3-tier role model (VIEWER / EDITOR / ADMIN) on 40+ endpoints
- **Redis**: Distributed session coordination + JWT blacklist persistence
- **TLS**: Configurable TLS termination + security headers
- **Helm**: Deployment / HPA / CronJob backup / Ingress / PDB / Secret templates
- **Audit**: HMAC-SHA256 tamper-evident audit trail with startup key enforcement
- **NetworkPolicy**: Restricted pod-to-pod communication

## Testing

```bash
# Full suite (2822 tests)
uv run pytest tests/ -q

# By category
uv run pytest tests/unit/ tests/api/ -q        # Unit + API (2539)
uv run pytest tests/integration/ tests/e2e/ -q  # Integration + E2E (283)

# Coverage
uv run pytest tests/ --cov=arrow_lake --cov-report=term-missing
```

78% coverage, Bandit security scan: zero HIGH severity issues.

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Data Processing | Daft 0.7.8, PyArrow 23.0.1 |
| Vector Storage | LanceDB 0.30.2 |
| Distributed Compute | Ray 2.54.1 |
| Workflow | Metaflow 2.19.22 |
| SQL Query | DuckDB 1.5.1 |
| Knowledge Graph | HugeGraph 1.7.0 (hstore) |
| Session Coordination | Redis 7.4 (distributed semaphore) |
| Object Storage | MinIO (S3-compatible) |
| HTTP API | FastAPI, httpx, Click (CLI) |
| Security | JWT + API Key, RBAC, HMAC audit |
| AI Embedding | PyTorch, sentence-transformers |
| Observability | structlog, Prometheus, OpenTelemetry |
| Configuration | Pydantic v2, PyYAML |

## Documentation

- [Cookbook](docs/cookbook/README.md) — 13 chapters + 43 examples
- [Security Policy](SECURITY.md) — Auth, RBAC, audit, transport security
- [Contributing](CONTRIBUTING.md) — Development setup and coding standards
- [Changelog](CHANGELOG.md) — Version history

## License

MIT License — Copyright (c) 2026 Witshine
