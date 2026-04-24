# ADR-08: v1.2 Architecture Decisions

**Status**: Accepted
**Date**: 2026-04-22
**Deciders**: Architecture team

## Context

v1.2 extends Arrow Lake from a data lake platform to an enterprise knowledge management system. Key additions: document processing pipeline, DuckDB high availability, and security hardening.

## Decision 1: DuckDB as Unified SQL Engine (confirmed from ADR-06)

**Status**: Already decided in v1.1, reinforced in v1.2.

DuckDB handles ALL SQL workloads (OLAP, vector search, FTS, hybrid, faceted, metadata). Lance extensions provide native scan capabilities. No additional OLAP engine is planned.

## Decision 2: SessionManager with Connection Reuse

**Problem**: v1.1 had two DuckDB connection management mechanisms — `DuckDBConnectionPool` (catalog-only) and per-query `create_duckdb_session()` (all bridges). No global concurrency control.

**Decision**: Unified `DuckDBSessionManager` in `query/session_manager.py` with:
- Semaphore-based concurrency control (`max_concurrent_queries`)
- Idle connection pool with health checks and timeout eviction
- Zombie connection detection (`max_session_lifetime_seconds=3600`)
- Per-connection resource governance (memory_limit, statement_timeout, threads)
- Connection creation retry (1 attempt on duckdb.Error)
- 8 Prometheus metrics for pool monitoring

**Consequences**:
- All 6 query bridges migrated to use SessionManager
- `DuckDBConnectionPool` retained as internal catalog component
- Backward compatible: bridges fall back to `create_duckdb_session()` when no manager is set

## Decision 3: Document Processing Architecture

**Problem**: No PDF processing capability for enterprise knowledge management use cases.

**Decision**: Three-tier cascading parser architecture:
1. **marker-pdf CLI** (primary) — subprocess invocation, avoids GPL-3.0 license contamination
2. **TurboOCR HTTP** (fallback) — for scanned/image-heavy PDFs, accessed via internal Docker network
3. **pypdf** (last resort) — text-only extraction, no OCR capability

**Data flow**:
```
PDF → BlobStore (raw file) → DocumentParser → DocumentChunker →
LanceStorageManager (text + embedding + blob_key + page_number) → RAG
```

**Consequences**:
- `DocumentConfig` added to configuration system
- New API endpoint: `POST /api/v1/datasets/{name}/ingest/documents`
- TurboOCR runs in isolated Docker network (`expose`, not `ports`)
- Chunking supports page, paragraph, and recursive strategies

## Decision 4: Qwen3-VL-Embedding as Standard

**Problem**: No standardized embedding model specification. Mixed models across environments.

**Decision**: Qwen3-VL-Embedding series (Apache-2.0) as the recommended standard:
- **2B** (default): 2048 dimensions, multimodal (text + image)
- **8B**: 4096 dimensions, higher quality

EmbeddingConfig extended with:
- `QWEN3_VL_EMBEDDING_MODELS` whitelist (model → dimension mapping)
- `known_dimension` property for dimension auto-detection
- `is_multimodal` property for multimodal capability check
- `expected_dim` + `validate_dimension` for runtime validation

## Decision 5: marker-pdf License Isolation

**Problem**: marker-pdf is GPL-3.0 licensed, incompatible with Arrow Lake's MIT license.

**Decision**: Invoke marker-pdf exclusively via `subprocess.run()` — never import its Python modules. This keeps Arrow Lake MIT-licensed while allowing marker-pdf as an optional external tool.

**Consequence**: marker-pdf must be installed separately by the user. Arrow Lake does not declare it as a dependency.

## Decision 6: Security Hardening

**Changes**:
- SQL injection prevention in `ducklake_workspace.py` (parameterized queries, identifier validation)
- Auth enforcement in `app.py` (reject startup with empty credentials when auth enabled)
- Docs endpoint conditional exposure (`docs_enabled` config flag)
- Rate limiting enabled by default
- File type whitelist in document ingestion (PDF only in v1.2)
- File size limit (configurable, default 100MB)

## Metrics Added

8 new Prometheus metrics:
- `duckdb_pool_health_checks_total` — connection health check counter
- `duckdb_pool_evicted_connections_total` — idle timeout + zombie eviction counter

## Files Changed

| Category | Files |
|----------|-------|
| Security | `ducklake_workspace.py`, `auth.py`, `jwt_auth.py`, `app.py` |
| DuckDB HA | `session_manager.py` (new), `olap.py`, `vector.py`, `fts.py`, `hybrid.py`, `faceted.py`, `metadata.py` |
| Document | `document.py` (new), `chunker.py` (new), `ocr.py` (new), `document.py` config (new) |
| Backup | `backup.py`, `backup_restore.py` (new) |
| Embedding | `media.py`, `encoder.py` |
| RAG | `graph_rag.py` |
| Tests | `test_session_manager.py` (new), `test_document_ingest.py` (new), `test_turbo_ocr.py` (new) |
