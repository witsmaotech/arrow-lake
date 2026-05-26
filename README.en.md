# Arrow Lake

> Production-grade multimodal data lakehouse for AI/ML teams.
> Built with Lance + Daft + Ray. 2872 tests, 80%+ coverage, zero high-severity security issues.

[![Tests](https://img.shields.io/badge/tests-2872%20passing-brightgreen)](https://gitee.com/wits__sunpw/wits-infra-dintellihub)
[![Coverage](https://img.shields.io/badge/coverage-80%25-green)](https://gitee.com/wits__sunpw/wits-infra-dintellihub)
[![Security](https://img.shields.io/badge/bandit-0%20HIGH-success)](https://gitee.com/wits__sunpw/wits-infra-dintellihub)
[![Python](https://img.shields.io/badge/python-3.11+-blue)](https://gitee.com/wits__sunpw/wits-infra-dintellihub)
[![Version](https://img.shields.io/badge/version-1.4.4-blue)](https://gitee.com/wits__sunpw/wits-infra-dintellihub)
[![License](https://img.shields.io/badge/license-MIT-informational)](https://gitee.com/wits__sunpw/wits-infra-dintellihub)

## Overview

Arrow Lake is a unified data lakehouse platform for multimodal data (text, images, audio, video). Built on Lance columnar storage + Daft DataFrame processing + Ray distributed compute, it provides end-to-end capabilities from data ingestion and quality control to multi-modal search, OLAP analytics, RAG pipelines, and knowledge graphs.

## Install

```bash
pip install arrow-lake

# With common extras
pip install "arrow-lake[fts,rag,document,chunking-full,jupyter]"
```

## Quick Start

```python
from arrow_lake import Lake

# Local mode, zero config
lake = Lake("./my_lake")

# Create dataset
import pyarrow as pa
table = pa.table({"text": ["machine learning", "deep learning", "data analytics"]})
lake.create_dataset("articles", table)

# SQL analytics
result = lake.olap_query("articles", "SELECT * FROM articles")

# YAML config mode (production)
lake = Lake.from_yaml("configs/prod.yaml")
lake.ingest("docs", ["data/papers/"])
lake.embed_and_add("docs")

# RAG Q&A
answer = await lake.rag_query("What is a vector database?", dataset_name="docs")
```

## Try the Demo

```bash
arrow-lake demo
```

Runs a self-contained demo with synthetic data — vector search, SQL analytics, and full-text search in ~15 seconds. No setup required.

## Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│                         Arrow Lake SDK (Lake)                        │
│   8 Mixin Classes: Ingest | Search | Query | Admin | Lineage |     │
│                    Audit | RAG | Knowledge Graph                     │
├─────────────────────────────────────────────────────────────────────┤
│  API Layer: FastAPI REST (40+) | CLI (16+ commands) | Python SDK   │
├──────────────────────────┬──────────────────────────────────────────┤
│    Query Layer           │          Intelligence Layer              │
│  Vector | FTS | Hybrid  │   RAG Pipeline (Multi-LLM)              │
│  Faceted | Ensemble     │   Knowledge Graph (HugeGraph + GraphRAG) │
│  OLAP SQL | Daft        │                                          │
├──────────────────────────┴──────────────────────────────────────────┤
│    Storage Layer: LanceDB + MinIO/S3/GCS + DuckDB + Redis          │
└─────────────────────────────────────────────────────────────────────┘
```

## Key Capabilities

| Capability | Description |
|---|---|
| **Vector Search** | Cosine/L2/Dot similarity, IVF_PQ / IVF_FLAT / IVF_HNSW_PQ indexes |
| **Full-Text Search** | Tantivy-powered FTS with jieba CJK tokenizer, stemming, stop-word removal |
| **Hybrid Search** | Reciprocal Rank Fusion (RRF) combining vector + text scores |
| **Faceted Search** | Multi-column metadata filtering with configurable facets |
| **Ensemble Search** | Cross-column RRF fusion across multiple embedding columns |
| **SQL Analytics** | DuckDB-powered OLAP: GROUP BY, window functions, JOINs, streaming |
| **Daft DataFrame** | Lazy evaluation + Ray distributed execution |
| **Knowledge Graph** | HugeGraph integration: build, Gremlin query, GraphRAG |
| **RAG Pipeline** | Multi-provider LLM (OpenAI, Anthropic, vLLM, Ollama, DeepSeek), sessions, citations, streaming |
| **Document Pipeline** | PDF parse → chunk → embed → Lance, 7 chunking strategies, OCR fallback |
| **Data Quality** | Schema validation, null detection, dedup (exact hash + perceptual hash), NeMo Curator |
| **Lineage & Audit** | Full-chain lineage tracking, HMAC-SHA256 tamper-evident audit trail |
| **Export** | Parquet / CSV with version selection, column projection, compression |
| **Security** | RBAC (VIEWER/EDITOR/ADMIN), JWT blacklist (Redis-backed), rate limiting, Gremlin injection defense |
| **REST API** | 40+ endpoints with OpenAPI docs, API Key + JWT auth, TLS, security headers |
| **Distributed** | Redis distributed semaphore, Ray distributed ingestion, GPU autoscaling, Helm chart |

## CLI

```bash
arrow-lake demo                  # Interactive demo
arrow-lake serve                 # Start REST API server
arrow-lake ingest files my_data data.csv
arrow-lake search vector my_data --query "ML" --top-k 5
arrow-lake query sql my_data --sql "SELECT * FROM my_data LIMIT 10"
arrow-lake kg build my_data      # Build knowledge graph
arrow-lake rag query "What is RAG?" --dataset docs
```

## Configuration

27 independent config sections with 3-layer precedence: defaults → environment variables (`ARROW_LAKE__` prefix) → YAML overlay.

```python
# Local development
lake = Lake("./data")  # local file storage, no MinIO needed

# Production
lake = Lake.from_yaml("configs/prod.yaml")
```

## Production Deployment

```bash
# Docker Compose (9 services, profile-based activation)
docker compose -f deploy/docker-compose.yml up -d     # core profile
docker compose --profile dev -f deploy/docker-compose.yml up -d  # + Ray + Jupyter

# Kubernetes (Helm)
helm install arrow-lake deploy/helm/arrow-lake/
```

Production features:
- **RBAC**: 3-tier role model (VIEWER / EDITOR / ADMIN) on all 40+ endpoints
- **Auth**: Dual-mode API Key + JWT (HS256/RS256), Redis-backed blacklist with TTL
- **Redis**: Distributed session coordination, JWT blacklist persistence, distributed semaphore
- **TLS**: Configurable TLS termination + security headers (CSP, HSTS, X-Frame-Options)
- **Helm**: Deployment, HPA (CPU + custom metrics), CronJob backup (02:00 UTC), Ingress, PDB, Secret, NetworkPolicy
- **Audit**: HMAC-SHA256 verified tamper-evident audit trail
- **NetworkPolicy**: Restricted pod-to-pod communication
- **Container Hardening**: cap-drop ALL, read-only filesystems, resource limits

## Testing

```bash
# Full suite (2872 tests, 80%+ coverage)
pytest tests/ -q

# By category
pytest tests/unit/ tests/api/ -q          # Unit + API
pytest tests/integration/ tests/e2e/ -q   # Integration + E2E

# Coverage
pytest tests/ --cov=arrow_lake --cov-report=term-missing
```

## Tech Stack

LanceDB + Daft + Ray + DuckDB + PyArrow + FastAPI + HugeGraph + Redis + Metaflow

| Layer | Technology | Version |
|-------|-----------|---------|
| Data Processing | Daft, PyArrow | 0.7.8, 23.0.1 |
| Vector Storage | LanceDB, Lance | 0.30.2 |
| OLAP Engine | DuckDB | 1.5.2 |
| Distributed Compute | Ray, Metaflow | 2.54.1, 2.19.22 |
| Knowledge Graph | HugeGraph | 1.7.0 |
| Session / Cache | Redis (hiredis) | >=5.0 |
| Object Storage | MinIO / S3 / GCS | boto3 >=1.35 |
| HTTP API | FastAPI, Uvicorn, slowapi | >=0.115 |
| LLM Providers | OpenAI, Anthropic, vLLM, Ollama, DeepSeek | — |
| Full-Text Search | Tantivy, jieba | >=0.20.0 |
| Embedding Models | Qwen3-Embedding-0.6B, sentence-transformers | — |
| Security | PyJWT, HMAC-SHA256 | >=2.9 |
| Observability | structlog, Prometheus, OpenTelemetry | — |
| Configuration | Pydantic v2, pydantic-settings, PyYAML | >=2.7 |

## Documentation

- [Usage Guide](docs/usage-guide.md) — comprehensive walkthrough
- [Cookbook](docs/cookbook/README.md) — 13 chapters + 43 examples (bilingual EN/ZH)
- [Product Introduction](docs/arrow-lake-product-introduction.html) — full product overview
- [Security Policy](SECURITY.md) — Auth, RBAC, audit, transport security
- [Contributing](CONTRIBUTING.md) — development setup and coding standards
- [API Docs](http://localhost:8000/docs) — auto-generated OpenAPI/Swagger
- [Changelog](CHANGELOG.md) — version history

## License

MIT License — Copyright (c) 2026 Witshine
