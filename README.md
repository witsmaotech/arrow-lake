# Arrow Lake

> Production-grade multimodal data lakehouse for AI/ML teams.
> Text, images, audio, vectors, knowledge graphs — one platform.

[![Tests](https://img.shields.io/badge/tests-5325%20passing-brightgreen)](https://gitee.com/wits__sunpw/wits-infra-dintellihub)
[![Coverage](https://img.shields.io/badge/coverage-90%25-brightgreen)](https://gitee.com/wits__sunpw/wits-infra-dintellihub)
[![Security](https://img.shields.io/badge/bandit-0%20HIGH-success)](https://gitee.com/wits__sunpw/wits-infra-dintellihub)
[![Python](https://img.shields.io/badge/python-3.11+-blue)](https://gitee.com/wits__sunpw/wits-infra-dintellihub)
[![Version](https://img.shields.io/badge/version-1.6.0-blue)](https://gitee.com/wits__sunpw/wits-infra-dintellihub)
[![License](https://img.shields.io/badge/license-MIT-informational)](https://gitee.com/wits__sunpw/wits-infra-dintellihub)

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
| **Vector Search** | Cosine/L2/Dot similarity, IVF_PQ / IVF_FLAT / IVF_HNSW_PQ indexes |
| **Full-Text Search** | Tantivy-powered FTS with jieba CJK tokenizer, stemming, stop-word removal |
| **Hybrid Search** | Reciprocal Rank Fusion (RRF) combining vector + text scores |
| **Faceted Search** | Multi-column metadata filtering with configurable facets |
| **Ensemble Search** | Cross-column RRF fusion across multiple embedding columns |
| **SQL Analytics** | DuckDB-powered OLAP: GROUP BY, window functions, JOINs, streaming |
| **Daft DataFrame** | Lazy evaluation + Ray distributed execution |
| **Knowledge Graph** | HugeGraph integration: build, Gremlin query, GraphRAG |
| **RAG Pipeline** | Multi-provider LLM (OpenAI, Anthropic, vLLM, Ollama, DeepSeek), sessions, citations, streaming, reranking (CrossEncoder/LLM), query transformation (HyDE/MultiQuery), multi-turn conversation |
| **Document Pipeline** | PDF parse → chunk → embed → Lance, 7 chunking strategies, OCR fallback |
| **Data Quality** | Schema validation, null detection, dedup (exact hash + perceptual hash), NeMo Curator |
| **Lineage & Audit** | Full-chain lineage tracking, HMAC-SHA256 tamper-evident audit trail |
| **Export** | Parquet / CSV with version selection, column projection, compression |
| **Metadata Governance** | Gravitino 1.2.1 federation: DuckDB ↔ Lance Catalog bidirectional sync, Tags, Policies, Model Catalog |
| **Security** | RBAC (VIEWER/EDITOR/ADMIN), JWT blacklist (Redis-backed), rate limiting, Gremlin injection defense, FQN/SQL injection prevention, path traversal prevention |
| **Observability** | OpenTelemetry tracing, Prometheus + Alertmanager, Grafana dashboards, structlog, latency breakdown tracking |
| **REST API** | 40+ endpoints + `/metadata/*` proxy (catalogs/tables/tags/policies/statistics/models), API Key + JWT auth, TLS, security headers |
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
arrow-lake status
```

## Configuration

Copy `.env.example` to `.env` and edit. For production, use YAML:

```python
lake = Lake.from_yaml("configs/prod.yaml")
```

27 independent config sections with 3-layer precedence: defaults → environment variables (`ARROW_LAKE__` prefix) → YAML overlay.

For local development, just pass `base_uri`:

```python
lake = Lake("./data")  # local file storage, no MinIO needed
```

## Production Deployment

```bash
# Docker Compose (11 services, profile-based activation)
docker compose -f deploy/docker-compose.yml up -d     # core profile
docker compose --profile dev -f deploy/docker-compose.yml up -d  # + Ray + Jupyter
docker compose --profile gravitino -f deploy/docker-compose.yml up -d  # + Gravitino + Lance REST

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
- **NetworkPolicy**: Restricted pod-to-pod communication (Redis 6379, HugeGraph 8080, HTTPS 443, DNS 53)
- **Container Hardening**: cap-drop ALL, read-only filesystems, resource limits, PID constraints

## Testing

```bash
# Full suite (5325 tests, 90%+ coverage)
pytest tests/ -q

# By category
pytest tests/unit/ tests/api/ -q          # Unit + API
pytest tests/integration/ tests/e2e/ -q   # Integration + E2E

# Coverage
pytest tests/ --cov=arrow_lake --cov-report=term-missing
```

## Tech Stack

LanceDB + Daft + Ray + DuckDB + PyArrow + FastAPI + HugeGraph + Redis + Metaflow + Gravitino

| Layer | Technology | Version |
|-------|-----------|---------|
| Data Processing | Daft, PyArrow | 0.7.8, 23.0.1 |
| Vector Storage | LanceDB, Lance | 0.30.2 |
| OLAP Engine | DuckDB | 1.5.2 |
| Distributed Compute | Ray, Metaflow | 2.54.1, 2.19.22 |
| Metadata Governance | Gravitino, Lance REST Catalog | 1.2.1 |
| Knowledge Graph | HugeGraph | 1.7.0 |
| Session / Cache | Redis (hiredis) | >=5.0 |
| Object Storage | MinIO / S3 / GCS | boto3 >=1.35 |
| HTTP API | FastAPI, Uvicorn, slowapi | >=0.115 |
| LLM Providers | OpenAI, Anthropic, vLLM, Ollama, DeepSeek | — |
| Full-Text Search | Tantivy, jieba | >=0.20.0 |
| Embedding Models | Qwen3-Embedding-0.6B, sentence-transformers | — |
| Security | PyJWT, HMAC-SHA256 | >=2.9 |
| Observability | structlog, Prometheus, OpenTelemetry, Alertmanager | — |
| Configuration | Pydantic v2, pydantic-settings, PyYAML | >=2.7 |

## Documentation

- [Usage Guide](docs/usage-guide.md) — comprehensive walkthrough
- [Cookbook](docs/cookbook/README.md) — 13 chapters + 43 examples (bilingual EN/ZH)
- [Product Introduction](docs/arrow-lake-product-introduction.html) — full product overview
- [Security Policy](SECURITY.md) — auth, RBAC, audit, transport security
- [Contributing](CONTRIBUTING.md) — development setup and coding standards
- [API Docs](http://localhost:8000/docs) — auto-generated OpenAPI/Swagger
- [Changelog](CHANGELOG.md) — version history

## License

MIT — Copyright (c) 2026 Witshine

---

中文文档: [README.zh.md](README.zh.md) | English: [README.en.md](README.en.md)
