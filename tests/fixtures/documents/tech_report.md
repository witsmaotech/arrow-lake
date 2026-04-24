# Arrow Lake Technical Report

## Architecture Overview

Arrow Lake is a production-ready data lake platform built on Apache Arrow, Lance, and DuckDB. It provides end-to-end capabilities for data ingestion, processing, search, and retrieval-augmented generation (RAG).

### Core Components

1. **Storage Layer**: Lance format for columnar storage with versioning. Supports local filesystem and S3/MinIO backends.
2. **Query Engine**: DuckDB for OLAP queries, vector search, and full-text search.
3. **Embedding Pipeline**: Local (HuggingFace SentenceTransformer) and API-based (OpenAI-compatible) embedding generation.
4. **Knowledge Graph**: HugeGraph integration for entity-relationship modeling and GraphRAG.
5. **API Layer**: FastAPI with JWT authentication and rate limiting.

### Performance Characteristics

| Operation | Latency (p50) | Throughput |
|-----------|---------------|------------|
| SELECT 10k rows | ~1ms | 1000 ops/s |
| GROUP BY aggregation | ~1.2ms | 850 ops/s |
| Full-text search | ~0.5ms | 2100 ops/s |
| Document chunking | ~0.35ms | 2900 ops/s |

### Security Features

- SQL injection prevention via centralized validation
- Gremlin injection prevention in knowledge graph queries
- SSRF protection for external service calls
- JWT-based API authentication
- Rate limiting per client

## Deployment

Arrow Lake supports Docker Compose deployment with MinIO, HugeGraph, and optional OCR service (TurboOCR).

## API Endpoints

### Dataset Management
- `POST /api/v1/datasets/{name}/ingest` — Ingest documents
- `GET /api/v1/datasets` — List datasets
- `DELETE /api/v1/datasets/{name}` — Delete dataset

### Search
- `POST /api/v1/search/vector` — Vector similarity search
- `POST /api/v1/search/fts` — Full-text search
- `POST /api/v1/search/hybrid` — Hybrid RRF-fused search

### RAG
- `POST /api/v1/rag/query` — RAG question answering
- `POST /api/v1/rag/stream` — Streaming RAG responses
