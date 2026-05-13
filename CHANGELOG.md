# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.3.1] - 2026-05-12

### Changed
- **pylance 升级**: 4.0.1 → 6.0.0，解锁 Lance 文件格式 v2.1/v2.2 双层编码、io_uring 高性能 I/O
- **lance-namespace 升级**: 0.6.1 → 0.7.6（pylance 6.0.0 依赖）
- **FTS 中文分词**: tantivy 后端移除后自动切换 lance-index + jieba 预分词，无需代码改动
- **文档更新**: 产品介绍 (中/英)、CLI Reference、Tech Compatibility Report 版本矩阵同步

### Verified
- 2610 单元测试通过，242 集成测试通过，覆盖率 76.61%（与升级前一致）
- Storage / Search / Vector / FTS / DuckDB lance_scan / Daft 零拷贝集成正常

## [1.3.0] - 2026-05-09

### Added
- **Redis 分布式 Session**: `RedisConfig` + `RedisSemaphore` 适配器，DuckDB Session 池支持水平扩展
- **QueryEngine Protocol**: `arrow_lake/query/engine.py` 定义 acquire/release/get_stats/shutdown 接口
- **RBAC 路由守卫**: 10 个路由文件添加 `Depends(require_role(...))`，覆盖 VIEWER/EDITOR/ADMIN 三级权限
- **JWT 黑名单 LRU**: `OrderedDict` 替换 O(n) dict rebuild，防 DoS 内存耗尽
- **Gremlin 注入防护增强**: 正则匹配裸 mutation step、闭包语法 `{}` 拒绝、`//` 行注释剥离
- **SQL 注入防护增强**: lineage SQL 验证剥离 `--` 和 `/* */` 注释
- **路径穿越防护**: export 路由 `resolve()` + `startswith()` 防止 `../` 逃逸
- **Helm HPA 模板**: `deploy/helm/arrow-lake/templates/hpa.yaml`（基于 CPU + 自定义指标）
- **Helm CronJob 备份模板**: `deploy/helm/arrow-lake/templates/cronjob-backup.yaml`（每日 02:00）
- **Helm Redis 环境变量**: Deployment 模板条件注入 Redis 配置
- **Gremlin 安全测试**: 17 个测试覆盖闭包绕过、裸 mutation、注释剥离、合法查询
- **Redis 信号量测试**: acquire/release、超时、回退、重连测试
- Cookbook examples: Redis Session (40)、RBAC Roles (41)、Gremlin Security (42)、JWT Blacklist (43)
- `sdk/` 模块: `LakeClient` 别名导出

### Changed
- **版本号**: pyproject.toml / _version.py / Chart.yaml → 1.3.0
- **Ingestor 并发修复**: ThreadPoolExecutor → 顺序执行，消除 Daft 读取竞争
- **lancedb API 兼容**: `open_dataset()` → `open_table()` (v0.30+)
- **备份测试**: `StorageBackend.MINIO` → `LOCAL`，消除 MinIO 环境污染
- **全量测试隔离**: 所有 Lake() 构造添加 `StorageConfig(backend="local")`
- **prod.yaml**: OLAP 配置完善、Redis 段、rate_limit 段、audit HMAC 注释
- **dev.yaml**: Redis 默认禁用
- `respx` 从生产依赖移至开发依赖
- 新增生产依赖 `redis[hiredis]>=5.0,<6.0`，开发依赖 `fakeredis>=2.0`

### Fixed
- Gremlin 注入绕过: `map`/`flatMap` 从白名单移除（闭包执行风险）
- Redis 信号量双释放: thread-local 后端跟踪防止 Redis→fallback 双减
- Redis TTL 幽灵许可: 仅首次 acquire 时设置 EXPIRE
- JWT 黑名单 O(n) 逐出: `OrderedDict.popitem(last=False)` O(1) 替换
- `/api/v1/version` 信息泄露: 添加 VIEWER RBAC 守卫
- `auth_service.py` coverage: 38% → 补充测试覆盖
- Gate B4 embedding 测试: HF model 下载依赖测试标记 skip
- RAG E2E 测试: auth header + `text_content` 列名修复
- KG E2E 测试: env var 隔离 + auth header 修复

### Removed
- `arrow_lake/query/_async.py`: 死代码删除（零外部引用）
- `tests/unit/duckdb/test_async_query.py`: 对应测试删除

## [1.2.2] - 2026-05-08

### Added
- `Lake.embed_and_add()`: 向量化管线 — 使用配置的 embedding 后端（HuggingFace/Ollama API）将文本列编码为向量，通过 `add_columns_table` 原位写入，无需全量重写
- `Lake.add_columns_table()`: Facade 暴露 Lance 原位列添加能力
- `Lake.config` 属性: 公开当前 ArrowLakeConfig 供外部读取
- `StorageAdvancedMixin.add_columns_table()`: Lance 原生 `add_columns` 避免全量 rewrite
- S3 远程备份/恢复: `BackupManager` 和 `BackupRestore` 支持 S3 server-side copy 路径（不再依赖本地 Path 操作）
- `ExportBridge` 自动检测 `/app/exports` 不可写时回退到 cwd
- 6 个行业分块测试数据文件: finance/tech/medical/business/education/literature

### Changed
- **chonkie 兼容性**: `TokenChunker` 参数 `token_chunk_size` → `chunk_size`，`SemanticChunker` 参数 `min_chunk_size` → `chunk_size`；`SDPMChunker` 自动 fallback（chonkie ≥1.6 移除）
- **HugeGraph 默认配置**: 端口 `8089` → `8091`，graph 名 `arrow_lake_kg` → `hugegraph`（匹配 docker-compose 部署）
- **docker-compose healthcheck**: graph 名 `arrow_lake_kg` → `hugegraph`
- Cookbook examples (34 files): `_add_vectors` 统一使用 `lake.embed_and_add()` + random fallback，不再走 `to_arrow()+restore_dataset` 全量重写路径
- `arrow_lake/query/olap.py`: 添加缺失的 `import contextlib`
- Deployment REST API 示例 (13/14/15): 修复 API Key 认证、容器内路径映射、`_post` 参数名、JSON 解析容错

### Fixed
- `e2e_chunking_scenarios.py` 数据文件缺失时 `KeyError: 'strategy'`（返回完整结果 dict）
- `olap.py` 中 `contextlib` 未导入导致 `NameError`
- `export.py` 默认 `base_dir=/app/exports` 本地运行 `PermissionError`
- `jwt_auth.py` 空 refresh token 断言过严（接受 400/401）
- `24_ensemble_search.py` `_ensemble_score` 列名检查顺序
- `26_audit_trail.py` / `27_data_lineage.py` `AuditEntry`/`LineageEvent` 的 `.get()` 调用错误
- `28_backup_restore.py` 缺少 `overwrite=True` 导致恢复失败
- `32_kg_traversal.py` KG build 使用全量数据集导致超时（改用 10 行小样本）
- `graphrag_e2e_test.py` 硬编码 HugeGraph 端口 8089（改为 8091）
- `s3_minio/01,03,04` hybrid search `_rrf_score` 列不存在（优先 `_hybrid_score`）
- `07_e2e_pipeline.py` 残留数据集未清理导致重复运行失败

## [1.2.1] - 2026-04-27

### Added
- 9 new facade methods in `_LakeAdminMixin`: `restore_dataset`, `get_dataset_version`, `list_dataset_versions`, `add_column`, `alter_column`, `drop_column`, `compact_dataset`, `read_dataset`, `scan_dataset`
- `CONTRIBUTING.md` — development setup, code standards, architecture overview, testing guidelines
- `SECURITY.md` — vulnerability reporting, auth architecture, data protection, transport security

### Changed
- Cookbook examples (39 files) unified to argparse CLI pattern (`--base-uri`, `--no-cleanup`)
- `_get_storage()` eliminated from all cookbook examples and most root examples (0 in .py files except tag operations)
- Bare `except Exception` reduced from 73 to 38 in cookbook examples (context-specific types)
- `server.py` deprecation notice updated with v2.0 removal timeline
- Duplicate `VectorSearchResult` removed from `__all__`

### Fixed
- `config_changed` NameError in `session_manager.py` — restored computation block
- Idle connection health check reliability — removed unreliable `_health_skip_seconds` optimization, always runs `SELECT 1`
- `_fallback_cache` cache pollution causing intermittent `test_fallback_encode_import_error_raises` failure
- RUF001 lint warnings in `chunker.py` — suppressed for intentional CJK punctuation

## [1.2.0] - 2026-04-24

### Added
- Kreuzberg PDF parser (Rust-core, 91+ format support) replacing marker_pdf/pypdf
- TurboOCR GPU acceleration service with circuit breaker pattern and retry logic
- Ingest dead-letter queue (`IngestDeadLetterQueue`) for failed document tracking with retry/resolve/purge
- Performance benchmark suite (`examples/query/benchmark.py`) — DuckDB query, chunking, validation, token counting baselines
- Document processing E2E test (`examples/ingestion/e2e_document_pipeline.py`)
- Test fixture generator (`tests/fixtures/documents/`) — 10 synthetic documents (EN/ZH/markdown/CSV/JSONL/multilingual)
- GraphRAG E2E test script (`examples/knowledge_graph/graphrag_e2e_test.py`)

### Changed
- Default OCR backend changed from `tesseract` to `paddleocr` (Kreuzberg config)
- `backup.py` refactored from 617 to 375 lines — extracted `_manifest_to_info`, `_restore_item`, `_paginate_keys` helpers
- `except Exception` narrowed from 17 to 4 occurrences — replaced with specific exception types across 12 files

### Fixed
- **Security**: SSRF prevention in TurboOcrClient (`_validate_endpoint` blocks private IPs)
- **Security**: Gremlin injection prevention in HugeGraph client (`_BLOCKED_GREMLIN_PATTERNS`)
- **Security**: SQL injection hardening — `escape_sql_literal()` with type check and length limit in `validation.py`
- **Security**: JWT error message sanitization — non-expiry errors return generic message
- **Security**: Blob key path sanitization in ingestor (prevents path traversal)
- **Security**: Backup dataset name validation (rejects `..`, `/`, `\\`)
- **Security**: API key empty-config defense-in-depth (rejects protected endpoints when no key configured)
- Import-order bug in `hybrid.py` (`escape_sql_literal` used before import)
- Duplicate property key creation loop removed in `knowledge_graph/client.py`
- structlog-style logger call fixed in `ray_serve_encoder.py`
- SentenceTransformer API compatibility (both `get_sentence_embedding_dimension` and `get_embedding_dimension`)

### Removed
- 52+ stale unit test files (unmaintained, referencing deleted modules)

## [1.1.0] - 2026-04-22

### Added
- Production hardening: observability, metrics, and operational tooling

## [1.0.0] - 2026-04-21

### Added

v1.0 GA release — 2224 tests, 82.92% coverage, production-ready data lakehouse.

**M0: Infrastructure & Query Migration**
- DuckDB Lance extension abstraction layer (`_base.py`, `_db.py`, `lance_adapter.py`)
- Native `__lance_scan()` with PyArrow streaming fallback
- DuckLake workspace management (materialize, TTL cleanup, metadata tracking)
- S3 storage_options schema and dual-path integration (Lance SDK + DuckDB SET)
- Lake Facade decomposed into 9 focused mixins (ingest, search, query, admin, lineage, audit, rag, kg)

**M1: Production Storage**
- S3/MinIO blob storage with BlobStoreManager (multipart upload, presigned URLs)
- Backup/restore manager (Lance + MinIO + DuckLake)
- REST API backup endpoints

**M2: RAG Pipeline**
- LLM provider abstraction (Anthropic Claude, OpenAI-compatible)
- RAG pipeline with citation support (retrieval → context assembly → generation)
- Session management for multi-turn conversations
- SSE streaming for real-time generation
- Entity extraction endpoints
- REST API: `/api/v2/rag/*`

**M3: Knowledge Graph + GraphRAG**
- HugeGraph REST client with schema management
- Entity/relation extraction from unstructured text
- Knowledge graph builder with task management
- GraphRAG retrieval with 3-way RRF fusion
- REST API: `/api/v2/kg/*`

**M4: Production Readiness**
- JWT authentication with access + refresh token flow
- RBAC with ADMIN/EDITOR/VIEWER role hierarchy
- API key authentication with rotation (90-day default)
- OpenTelemetry distributed tracing
- Separated liveness/readiness health probes
- 15 REST API routers (system, datasets, search, query, quality, embedding, export, lineage, audit, backup, rag, kg, auth, admin)
- Performance benchmark framework with baseline tracking
- 6 Grafana dashboards (system, ingestion, processing, query, OMTM, SLO)

**M5: Operations & Governance**
- Rate limiting middleware (slowapi, disabled by default, per-endpoint config)
- HTTP security response headers (HSTS, X-Content-Type-Options, X-Frame-Options, Referrer-Policy, CSP)
- 11 Prometheus alert rules (HTTP errors, auth failures, ingestion stalled, rate limit, memory, latency)
- SLO thresholds configuration in Helm values

**Security Hardening**
- SQL injection prevention centralized in `validation.py`
- Path traversal protection in export
- HMAC integrity verification on audit trail
- JWT state propagation fallback in `get_current_user()`

**Deploy Artifacts**
- Multi-stage Docker build (CPU + GPU variants)
- Docker Compose profiles (core, dev, gpu, monitoring, kg)
- Helm chart with PrometheusRule, NetworkPolicy
- Init scripts, TLS cert generation, bucket setup

### Changed

- Lake class decomposed from 1049-line monolith to 9 mixin modules
- Query layer migrated from direct LanceDB SDK to DuckDB-native SQL with Lance extension
- Auth middleware migrated from class-based BaseHTTPMiddleware to function-based with `@app.middleware("http")`
- All config sections registered in `_build_merged_update()` and `from_yaml()` constructor

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
