# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.0] - 2026-04-15

### Added

Initial release — 80 stories across 9 Sprints, 1414 tests.

**Core Infrastructure (Sprint 1)**
- LanceStorageManager: create, read, append, delete, version, tag, compact, schema migration
- Pydantic-based configuration system (YAML + environment variables)
- Unified exception hierarchy with error codes
- Prometheus metrics integration
- CLI via Click (`arrow-lake` command)
- HTTP API server

**Data Ingestion (Sprint 2-3)**
- Batch ingestion from Parquet, JSON, CSV, images, audio, video
- MinIO/S3-compatible object storage integration
- Multi-process distributed ingestion via Ray
- Streaming ingestion pipeline

**Embedding & Vector Search (Sprint 3-5)**
- Multi-model embedding generation (sentence-transformers)
- Semantic vector search via LanceDB
- Hybrid search (BM25 + vector scoring)
- Multi-vector index support (multi-modal)
- Faceted search with drill-down

**Data Quality (Sprint 4-5)**
- Schema validation framework
- Null value detection and statistics
- Quality filter pipeline
- Content deduplication: SHA-256 exact match + pHash perceptual hash
- Incremental cross-batch dedup with seen-hash accumulation

**Data Catalog (Sprint 6-7)**
- Dataset catalog with metadata management
- Data lineage tracking with SQL query interface (DuckDB)
- Actor-based access management
- Audit logging with HMAC integrity verification

**Data Export (Sprint 5)**
- Export to Parquet (with compression: snappy, gzip, brotli, zstd, lz4)
- Export to CSV (binary columns excluded with warnings)
- Format auto-detection from file suffix
- Column selection and version selection

**Workflow & Orchestration (Sprint 8-9)**
- Metaflow workflow integration
- Ray distributed runtime
- Pipeline orchestration and scheduling

**Testing**
- 1414 tests (unit + integration)
- 82%+ code coverage
- Comprehensive test utilities and fixtures

**Security**
- SQL injection prevention (parameterized queries, keyword validation)
- Path traversal protection
- Input validation on all public APIs
- No hardcoded credentials

## [0.1.0] - Unreleased

_No unreleased changes._
