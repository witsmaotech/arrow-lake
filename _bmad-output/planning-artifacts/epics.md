---
stepsCompleted: [step-01-prerequisites, step-02-design-epics, step-03-create-stories, step-04-final-validation]
inputDocuments:
  - _bmad-output/planning-artifacts/prd.md
  - _bmad-output/planning-artifacts/architecture.md
  - _bmad-output/planning-artifacts/system_design.md
  - _bmad-output/planning-artifacts/implementation-readiness-report-2026-04-11.md
project_name: arrow-lake
date: 2026-04-11
total_frs: 68
total_nfrs: 32
---

# Arrow Lake - Epic Breakdown

## Overview

This document provides the complete epic and story breakdown for Arrow Lake, decomposing the requirements from the PRD, Architecture, and System Design into implementable stories.

## Requirements Inventory

### Functional Requirements

#### 6.1 Data Ingestion (9 FRs)

FR-ING-01: Ingest text/CSV/JSON/Parquet files from local FS, S3/MinIO, HTTP (P0)
FR-ING-02: Ingest images (JPEG/PNG/WebP) with automatic thumbnail generation (P0)
FR-ING-03: Ingest video: extract keyframes at scene boundaries (PyAV), MVP scope: single keyframe per scene (P1)
FR-ING-04: Compute text embeddings on ingest (HuggingFace local / Ray Serve / external API) (P0)
FR-ING-05: Compute image embeddings on ingest (CLIP/SigLIP) (P0)
FR-ING-06: Store raw data + embeddings in unified Lance table (P0)
FR-ING-07: Build vector index asynchronously after embedding completion (P0)
FR-ING-08: Content-addressed dedup (SHA-256 exact + pHash perceptual) (P0)
FR-ING-09: Multi-fidelity storage (thumbnail + preview + original) (P1)

#### 6.2 Data Processing (9 FRs)

FR-PROC-01: Daft DataFrame API for multimodal transformations (P0)
FR-PROC-02: GPU/CPU heterogeneous scheduling (`use_gpu=True`) (P0)
FR-PROC-03: SQL query support (Daft SQL + DuckDB) (P1)
FR-PROC-04: Quality scoring pipeline (NeMo Curator: dedup, classifier, aesthetic) (P1)
FR-PROC-05: Quality scores as Lance columns with predicate pushdown (P0)
FR-PROC-06: Lazy download + decode for images/video (no full-file download until needed) (P0)
FR-PROC-07: Schema migration: add/alter/drop columns without full rewrite (P0)
FR-PROC-08: Distributed processing via Ray (foreach + AutoScale) (P0)
FR-PROC-09: Remote data loader pattern (CPU decode -> Object Store -> GPU train) (P1)

#### 6.3 Storage and Versioning (8 FRs)

FR-STOR-01: Lance format for all stored data with Arrow-native I/O (P0)
FR-STOR-02: Automatic versioning on every write (Lance version) (P0)
FR-STOR-03: Named tags for important versions (experiment snapshots, production) (P0)
FR-STOR-04: Time-travel query: read any historical version (P0)
FR-STOR-05: Version diff: compare two versions (schema + row + column changes) (P1)
FR-STOR-06: Compaction: merge Fragment files, reclaim space from dropped columns (P0)
FR-STOR-07: Auto-tiered blob lifecycle (Standard -> IA -> Glacier) (P2)
FR-STOR-08: S3/MinIO backend with configurable endpoint (P0)

#### 6.4 Query and Retrieval (8 FRs)

FR-QRY-01: Vector search (HNSW for <1M rows, IVF_PQ for 1M+) (P0)
FR-QRY-02: Full-text search (Lance FTS) (P0)
FR-QRY-03: Hybrid search (vector + text, configurable alpha weight) (P0)
FR-QRY-04: OLAP analytics (Daft SQL primary, DuckDB fallback for catalog queries) (P0)
FR-QRY-05: Streaming results (fetch_record_batch_reader, constant memory) (P0)
FR-QRY-06: Faceted search (DuckDB CUBE + vector search) (P2)
FR-QRY-07: Adaptive index selection based on data size and query patterns (P0)
FR-QRY-08: Multi-model ensemble search (join results from multiple embedding columns) (P2)

#### 6.5 Catalog and Metadata (5 FRs)

FR-CAT-01: Centralized catalog as Ray Named Actor (DuckDB embedded) (P0)
FR-CAT-02: Register datasets with schema, column metadata, and statistics (P0)
FR-CAT-03: Query catalog metadata via SQL (P0)
FR-CAT-04: Unified search API routing through catalog (P0)
FR-CAT-05: Data lineage as SQL queries over Lance event log (P2)

#### 6.6 Workflow Orchestration (11 FRs)

FR-ORCH-01: Metaflow FlowSpec for all batch pipelines (P0)
FR-ORCH-02: Local execution: `python flow.py run` (P0)
FR-ORCH-03: Cluster execution: `python flow.py run --with ray` (P0)
FR-ORCH-04: Production deployment: `python flow.py --with ray argo-workflows create` (P1)
FR-ORCH-05a: Transient retry: @retry with exponential backoff for spot worker preemption and network errors (P0)
FR-ORCH-05b: Error classification: @catch handler classifies errors as retryable vs fatal (P0)
FR-ORCH-05c: State rollback: Lance version checkout to last-known-good on fatal error (P0)
FR-ORCH-06: Scheduled pipelines: @schedule(daily/hourly/cron) (P0)
FR-ORCH-07: Tag-based run tracking and resume (P1)
FR-ORCH-08: Elastic burst: auto-scale GPU workers on demand, scale back on idle (P1)
FR-ORCH-09: Event sourcing: Lance version + Metaflow tag = immutable audit trail (P2)

#### 6.7 Developer Experience (7 FRs)

FR-DEV-01: One-command platform start: `docker compose up -d` (P1)
FR-DEV-02: Jupyter notebook integration for exploration (P1)
FR-DEV-03: uv for dependency management (replaces Poetry) (P0)
FR-DEV-04: Python SDK: `from arrow_lake import Lake` (P0)
FR-DEV-05: Data testing: pytest assertions on Lance/Daft/DuckDB results (P1)
FR-DEV-06: Progressive complexity: 5 API levels (function -> Daft -> SQL -> Ray -> Metaflow) (P0)
FR-DEV-07: CLI for common operations (ingest, search, status, version) (P2)

#### 6.8 Quality Management - ADR-02 Derived (5 FRs)

FR-QUA-01: QualityFilter registration: pluggable row-level filter interface (P0)
FR-QUA-02: Built-in filters: TextLengthFilter + ImageResolutionFilter (P0)
FR-QUA-03: Dead-letter persistence: rejected rows -> `{table}_dead_letter` Lance table (P0)
FR-QUA-04: Quality statistics report: total/passed/rejected + per-filter breakdown (P0)
FR-QUA-05: Schema validation gate: strict mode rejects unknown columns/type mismatches (P0)

#### 6.9 Observability - ADR-02 Derived (6 FRs)

FR-OBS-01: Prometheus `/metrics` HTTP endpoint (Prometheus format) (P0)
FR-OBS-02: Ingestion metrics: rows/bytes/duration/errors per table (P0)
FR-OBS-03: Processing metrics: embeddings/quality rejects/active tasks (P0)
FR-OBS-04: Query metrics: count/latency/results per query_type (P0)
FR-OBS-05: System metrics: Ray actors/table count/uptime (P0)
FR-OBS-06: Metrics configurable: env vars for port/path, support disable (P0)

### NonFunctional Requirements

#### 7.1 Performance (6 NFRs)

NFR-PERF-01: Vector search latency (10M rows, top_k=100) < 10ms
NFR-PERF-02: Ingestion throughput (text, single node) > 50K rows/sec
NFR-PERF-03: Arrow zero-copy utilization across full chain > 90%
NFR-PERF-04: Lazy evaluation speedup at 1% selectivity > 100x vs eager
NFR-PERF-05: Streaming query memory footprint (100M rows) < 100MB
NFR-PERF-06: PyTorch DataLoader zero-copy + async GPU transfer (pin_memory + non_blocking)

#### 7.2 Reliability (4 NFRs)

NFR-REL-01: Workflow recovery rate (no human intervention) > 90% (MVP), > 95% (prod)
NFR-REL-02: Data integrity on failure (Lance version + Metaflow checkpoint) zero data loss
NFR-REL-03: Catalog Actor availability max_restarts=3, auto-recovery
NFR-REL-04: MTTR for transient failures < 10 minutes

#### 7.3 Scalability (5 NFRs)

NFR-SCALE-01: Data volume support (single node) up to 10M rows
NFR-SCALE-02: Data volume support (distributed) up to 1B rows
NFR-SCALE-03: Concurrent query support up to 100 QPS (with read replicas)
NFR-SCALE-04: GPU scaling model fractional GPU (0.5), up to 8 workers
NFR-SCALE-05: Elastic burst: 0 to 8 GPU workers, scale-up in < 5 minutes

#### 7.4 Cost Efficiency (4 NFRs)

NFR-COST-01: Elastic burst monthly cost (100GB/month processing) < $500/month
NFR-COST-02: Storage cost reduction via auto-tiering (100TB) > 50% vs all-Standard
NFR-COST-03: Spot GPU utilization for burst workloads > 70% spot when available
NFR-COST-04: Baseline (idle) platform cost < $400/month

#### 7.5 Usability (4 NFRs)

NFR-USE-01: Developer onboarding time < 30 minutes
NFR-USE-02: Code changes from local to production deployment zero
NFR-USE-03: Embedding model hot-swap zero data rewrite, zero downtime
NFR-USE-04: API complexity levels 5 levels (simple -> advanced)

#### 7.6 Security (4 NFRs)

NFR-SEC-01: Secrets management environment variables / .env files, no hardcoded credentials
NFR-SEC-02: S3/MinIO access control IAM roles (prod) / access keys (dev)
NFR-SEC-03: Input validation at API boundaries Schema validation on ingest
NFR-SEC-04: Container security official base images, minimal attack surface

#### 7.7 Observability (5 NFRs)

NFR-OBS-01: Pipeline metrics Prometheus + Grafana dashboards
NFR-OBS-02: Ray cluster monitoring Ray Dashboard (built-in)
NFR-OBS-03: Structured logging JSON logs with correlation IDs
NFR-OBS-04: Data quality reporting Metaflow Cards (HTML reports per step)
NFR-OBS-05: Cost tracking per pipeline run Ray resource annotation + Prometheus

### Additional Requirements

#### Project Setup & Dependencies

AR-01: Initialize greenfield project using uv for dependency management with pyproject.toml and uv.lock files
AR-02: Fix Python version in .python-version file
AR-03: Configure Ruff for linting and formatting with ruff.toml
AR-04: Configure MyPy for type checking with mypy.ini
AR-05: Set up pre-commit hooks in .pre-commit-config.yaml
AR-06: Define auxiliary library versions in pyproject.toml (structlog, tenacity, pydantic, boto3, prometheus-client)
AR-07: Verify Daft >= 0.7.8 + DuckDB Lance extension + Pydantic v2 Arrow type mapping before implementation

#### Infrastructure & Deployment

AR-08: Create Dockerfile for containerization
AR-09: Create docker-compose.yml for local development with Ray head + 1 worker (CPU with optional GPU)
AR-10: Create docker-compose.gpu.yml overlay for GPU support
AR-11: Create prometheus.yml configuration for monitoring
AR-12: Configure Prometheus and Grafana in docker-compose setup
AR-13: Create Helm Chart for Ray on K8s production deployment using official Ray Helm chart with custom values
AR-14: Create Helm templates: deployment.yaml, service.yaml, networkpolicy.yaml, prometheusrule.yaml
AR-15: Define NetworkPolicy in Helm Chart templates but default to disabled in values.yaml
AR-16: Configure values.yaml and values-dev.yaml for Helm deployments

#### Configuration Management

AR-17: Implement Pydantic Settings with 4-layer override: code defaults -> .env file -> environment variables -> Metaflow Config YAML
AR-18: Create .env.example template file with placeholder values (excluded via .gitignore)
AR-19: Create YAML config files: configs/dev.yaml, configs/staging.yaml, configs/prod.yaml
AR-20: Implement fail-fast validation on startup for required configuration fields
AR-21: Configure Metaflow @project decorator and Config YAML injection

#### Security

AR-22: Implement Docker network isolation for local development (bridge network, expose port 8000 for metrics, 8265 for Ray Dashboard)
AR-23: Configure TLS for Docker Compose (self-signed) and K8s (cert-manager)
AR-24: Ensure AWS GP3 EBS encryption for storage at rest
AR-25: Configure Prometheus service discovery to restrict /metrics endpoint access

#### Monitoring & Logging

AR-26: Implement structured JSON logging with structlog
AR-27: Include correlation_id in all logs (mapped from Metaflow run_id)
AR-28: Expose /metrics HTTP endpoint in Prometheus format
AR-29: Implement 17 Prometheus metrics following naming pattern: arrow_lake_{domain}_{metric}_{unit}
AR-30: Create metrics.py for Prometheus metric registration and definitions
AR-31: Configure structlog in config.py for all modules

#### Integration Requirements

AR-32: Implement S3/MinIO integration via boto3 in arrow_lake/ingest/sources/s3.py
AR-33: Implement local file system data source in arrow_lake/ingest/sources/local.py
AR-34: Integrate with Prometheus via prometheus_client library
AR-35: Configure Metaflow integration with @project decorator and Config YAML

#### Testing Requirements

AR-36: Organize tests in three levels: tests/unit/, tests/integration/, tests/e2e/
AR-37: Create 6 Arrow zero-copy boundary tests in tests/integration/test_boundary_*.py
AR-38: Configure CI pipeline with PR gates: Ruff lint + MyPy type check + pytest (CPU only)
AR-39: Configure nightly GPU test runs with automatic scheduling
AR-40: Create conftest.py for shared test fixtures
AR-41: Implement GPU tests separately from CPU tests due to cost

#### Code Structure

AR-42: Create arrow_lake/ package with submodules: catalog/, ingest/, quality/, embedding/, query/, ray_runtime/, sdk/
AR-43: Create flows/ package outside main package with Metaflow Flow definitions
AR-44: Implement custom exception hierarchy in arrow_lake/exceptions.py
AR-45: Define Pydantic v2 models for all schemas with proper type annotations
AR-46: Implement Pydantic to Arrow schema conversion with proper type mappings

#### CI/CD

AR-47: Create .github/workflows/ci.yml for PR gate controls
AR-48: Create .github/workflows/gpu-tests.yml for nightly and manual GPU tests
AR-49: Create .github/workflows/release.yml for tag-triggered releases

### UX Design Requirements

Not applicable - MVP is CLI/SDK/Notebook-only, no frontend UI required.

### FR Coverage Map

| FR ID | Epic | Brief Description |
|-------|------|-------------------|
| FR-ING-01 | Epic 3 | Ingest text/CSV/JSON/Parquet from local FS, S3/MinIO, HTTP |
| FR-ING-02 | Epic 3 | Ingest images (JPEG/PNG/WebP) with automatic thumbnail generation |
| FR-ING-03 | Epic 3 | Ingest video: extract keyframes at scene boundaries (PyAV), MVP: single keyframe per scene |
| FR-ING-04 | Epic 4 | Compute text embeddings on ingest (HuggingFace local / Ray Serve / external API) |
| FR-ING-05 | Epic 4 | Compute image embeddings on ingest (CLIP/SigLIP) |
| FR-ING-06 | Epic 3 | Store raw data + embeddings in unified Lance table |
| FR-ING-07 | Epic 4 | Build vector index asynchronously after embedding completion |
| FR-ING-08 | Epic 4 | Content-addressed dedup (SHA-256 exact + pHash perceptual) |
| FR-ING-09 | Epic 3 | Multi-fidelity storage (thumbnail + preview + original) |
| FR-PROC-01 | Epic 3 | Daft DataFrame API for multimodal transformations |
| FR-PROC-02 | Epic 4 | GPU/CPU heterogeneous scheduling (use_gpu=True) |
| FR-PROC-03 | Epic 7 | SQL query support (Daft SQL + DuckDB) |
| FR-PROC-04 | Epic 8 | Quality scoring pipeline (NeMo Curator: dedup, classifier, aesthetic) |
| FR-PROC-05 | Epic 4 | Quality scores as Lance columns with predicate pushdown |
| FR-PROC-06 | Epic 3 | Lazy download + decode for images/video |
| FR-PROC-07 | Epic 2 | Schema migration: add/alter/drop columns without full rewrite |
| FR-PROC-08 | Epic 6 | Distributed processing via Ray (foreach + AutoScale) |
| FR-PROC-09 | Epic 6 | Remote data loader pattern (CPU decode -> Object Store -> GPU train) |
| FR-STOR-01 | Epic 1 | Lance format for all stored data with Arrow-native I/O |
| FR-STOR-02 | Epic 2 | Automatic versioning on every write (Lance version) |
| FR-STOR-03 | Epic 2 | Named tags for important versions |
| FR-STOR-04 | Epic 2 | Time-travel query: read any historical version |
| FR-STOR-05 | Epic 2 | Version diff: compare two versions |
| FR-STOR-06 | Epic 2 | Compaction: merge Fragment files, reclaim space |
| FR-STOR-07 | Epic 7 | Auto-tiered blob lifecycle (Standard -> IA -> Glacier) |
| FR-STOR-08 | Epic 1 | S3/MinIO backend with configurable endpoint |
| FR-QRY-01 | Epic 5 | Vector search (HNSW for <1M, IVF_PQ for 1M+) |
| FR-QRY-02 | Epic 5 | Full-text search (Lance FTS) |
| FR-QRY-03 | Epic 5 | Hybrid search (vector + text, configurable alpha) |
| FR-QRY-04 | Epic 5 | OLAP analytics (Daft SQL primary, DuckDB fallback for catalog queries) |
| FR-QRY-05 | Epic 5 | Streaming results (fetch_record_batch_reader, constant memory) |
| FR-QRY-06 | Epic 8 | Faceted search (DuckDB CUBE + vector search) |
| FR-QRY-07 | Epic 5 | Adaptive index selection based on data size and query patterns |
| FR-QRY-08 | Epic 8 | Multi-model ensemble search |
| FR-CAT-01 | Epic 1 | Centralized catalog as Ray Named Actor (DuckDB embedded) |
| FR-CAT-02 | Epic 1, Epic 2 | Register datasets with schema, column metadata, and statistics (Epic 1: initial registration; Epic 2: lifecycle management via versioning) |
| FR-CAT-03 | Epic 5 | Query catalog metadata via SQL |
| FR-CAT-04 | Epic 5 | Unified search API routing through catalog |
| FR-CAT-05 | Epic 8 | Data lineage as SQL queries over Lance event log |
| FR-ORCH-01 | Epic 6 | Metaflow FlowSpec for all batch pipelines |
| FR-ORCH-02 | Epic 6 | Local execution: python flow.py run |
| FR-ORCH-03 | Epic 6 | Cluster execution: python flow.py run --with ray |
| FR-ORCH-04 | Epic 7 | Production deployment: argo-workflows create |
| FR-ORCH-05a | Epic 6 | Transient retry: @retry with exponential backoff |
| FR-ORCH-05b | Epic 6 | Error classification: @catch handler |
| FR-ORCH-05c | Epic 6 | State rollback: Lance version checkout on fatal error |
| FR-ORCH-06 | Epic 6 | Scheduled pipelines: @schedule |
| FR-ORCH-07 | Epic 6 | Tag-based run tracking and resume |
| FR-ORCH-08 | Epic 7 | Elastic burst: auto-scale GPU workers |
| FR-ORCH-09 | Epic 8 | Event sourcing: Lance version + Metaflow tag = audit trail |
| FR-DEV-01 | Epic 1 | One-command platform start: docker compose up -d |
| FR-DEV-02 | Epic 7 | Jupyter notebook integration |
| FR-DEV-03 | Epic 1 | uv for dependency management |
| FR-DEV-04 | Epic 1 | Python SDK: from arrow_lake import Lake (init) |
| FR-DEV-05 | Epic 2 | Data testing: pytest assertions on Lance/Daft/DuckDB |
| FR-DEV-06 | Epic 1 | Progressive complexity: 5 API levels (L1-2 in Epic 1, evolved iteratively) |
| FR-DEV-07 | Epic 7 | CLI for common operations |
| FR-QUA-01 | Epic 4 | QualityFilter registration: pluggable row-level filter interface |
| FR-QUA-02 | Epic 4 | Built-in filters: TextLengthFilter + ImageResolutionFilter |
| FR-QUA-03 | Epic 4 | Dead-letter persistence: rejected rows -> dead_letter Lance table |
| FR-QUA-04 | Epic 4 | Quality statistics report: total/passed/rejected |
| FR-QUA-05 | Epic 4 | Schema validation gate: strict mode rejects unknown columns |
| FR-OBS-01 | Epic 7 | Prometheus /metrics HTTP endpoint |
| FR-OBS-02 | Epic 7 | Ingestion metrics: rows/bytes/duration/errors per table |
| FR-OBS-03 | Epic 7 | Processing metrics: embeddings/quality rejects/active tasks |
| FR-OBS-04 | Epic 7 | Query metrics: count/latency/results per query_type |
| FR-OBS-05 | Epic 7 | System metrics: Ray actors/table count/uptime |
| FR-OBS-06 | Epic 7 | Metrics configurable: env vars for port/path, support disable |

## Epic List

### Epic 1: Platform Bootstrap
**User outcome:** Maya can `docker compose up -d` to start the platform, create a Lance dataset, register it in the Catalog, and see basic metrics and structured logs flowing.

**Sub-phases:**
- 1A: Project Skeleton (pyproject.toml, uv, Ruff, MyPy, pre-commit) — Config validation tests
- 1B: Configuration & Settings (Pydantic Settings 4-layer, .env.example, fail-fast) — Pure business logic TDD
- 1C: Platform Boot (Dockerfile, docker-compose.yml, DuckDB pool, Catalog Actor, observability scaffolding) — Integration tests

**FRs covered:** FR-DEV-01, FR-DEV-03, FR-DEV-04 (SDK init), FR-DEV-06 (L1-2), FR-STOR-01, FR-STOR-08, FR-CAT-01, FR-CAT-02 (8 FRs)

**ARs covered:** AR-01~06, AR-07 (Step 3 Lite Spike), AR-08, AR-09, AR-17~20, AR-26, AR-27, AR-31, AR-42~46, Arrow version pinning, DuckDB WAL pool, DI protocol boundary, test infrastructure (fixtures/factories/mocks), platform boot smoke test (~26 ARs)

**Risk Spikes:** DuckDB Lance extension production validation, Daft >= 0.7.8 Arrow type mapping verification

**NFR Validation:** NFR-USE-01 (TTV < 30 min as Epic acceptance gate)

**Gate:** docker compose up -d -> from arrow_lake import Lake -> create dataset -> register Catalog -> /metrics accessible -> structured logs flowing

**MVP:** Core (week 1-2)

### Epic 2: Data Versioning & Management
**User outcome:** Maya can tag dataset versions, time-travel to any historical state, compare versions side-by-side, compact storage, evolve schemas, and validate data correctness with pytest.

**FRs covered:** FR-STOR-02~06, FR-PROC-07, FR-DEV-05, FR-CAT-02 (8 FRs)

**ARs covered:** Schema evolution strategy, Arrow boundary validation tests, graceful degradation spec, fixture data versioning (~5 ARs)

**NFR Validation:** NFR-STOR (version integrity, zero data loss), NFR-REL-02 (data integrity on failure)

**MVP:** Core (week 2-3)

### Epic 3: Multimodal Ingestion
**User outcome:** Maya can ingest text, images, and video from local FS, S3, or HTTP into a unified Lance table with lazy blob loading and automatic thumbnail generation.

**FRs covered:** FR-ING-01~03, FR-ING-06, FR-ING-09, FR-PROC-01, FR-PROC-06 (7 FRs)

**ARs covered:** AR-32 (S3/boto3), AR-33 (local FS), error code taxonomy (ErrorCode enum) (~4 ARs)

**NFR Validation:** NFR-PERF-02 (ingestion throughput > 50K rows/sec)

**Optional (John's recommendation):** Minimal metadata search (filename/date filtering) to reduce value gap before Epic 5

**MVP:** Core (week 3-4)

### Epic 4: Embedding & Quality
**User outcome:** Maya can compute embeddings during ingestion, apply pluggable quality filters, deduplicate content, and persist rejected rows to a dead-letter table.

**FRs covered:** FR-ING-04, FR-ING-05, FR-ING-07, FR-ING-08, FR-PROC-02, FR-PROC-05, FR-QUA-01~05 (11 FRs)

**Risk Spikes:** NeMo Curator CPU fallback validation (High probability: NVIDIA-only dependency)

**NFR Validation:** NFR-PERF-06 (GPU zero-copy + pin_memory), NFR-SEC-03 (input validation at API boundaries)

**MVP:** Core (week 4-5)

### Epic 5: Semantic Search & Analytics
**User outcome:** Raj can perform vector search, full-text search, hybrid RRF search, and OLAP analytics via SQL, with streaming results and adaptive index selection.

**FRs covered:** FR-QRY-01~05, FR-QRY-07, FR-CAT-03, FR-CAT-04 (8 FRs)

**ARs covered:** Index build time budget, performance benchmark suite (~3 ARs)

**NFR Validation:** NFR-PERF-01 (< 10ms vector search), NFR-PERF-04 (100x lazy eval speedup), NFR-PERF-05 (streaming < 100MB)

**MVP Core Path endpoint** — Raj's "aha moment"

**MVP:** Core (week 5-6)

### Epic 6: Pipeline Orchestration & Integration
**User outcome:** Maya can define automated data pipelines with Metaflow, featuring three-level self-healing (retry/classify/rollback), scheduled execution, and tag-based run tracking.

**Integration Story:** Maya E2E pipeline — 1000 mixed-quality records, 4 steps (ingest -> quality -> embed -> search), < 45 minutes, TTV + /metrics observable.

**FRs covered:** FR-ORCH-01~03, FR-ORCH-05a~c, FR-ORCH-06, FR-ORCH-07, FR-PROC-08, FR-PROC-09 (10 FRs)

**NFR Validation:** NFR-REL-01~04 (reliability), NFR-SCALE-01 (single node 10M rows)

**MVP:** Enhanced (week 6-8)

### Epic 7: Production & Observability
**User outcome:** Sam can deploy to K8s via Helm, leverage elastic GPU burst scaling, monitor via Prometheus/Grafana dashboards, and manage the platform via CLI.

**FRs covered:** FR-DEV-02, FR-DEV-07, FR-ORCH-04, FR-ORCH-08, FR-PROC-03, FR-STOR-07, FR-OBS-01~06 (12 FRs)

**ARs covered:** AR-10~16, AR-22~25, AR-28~30, AR-47~49 (~13 ARs)

**NFR Validation:** NFR-COST-01~04 (cost), NFR-SCALE-02~05 (scalability), NFR-OBS-01~05 (observability)

**MVP:** Production (month 3-4: deploy+observability, month 4-6: scale+security)

### Epic 8: Advanced Features
**User outcome:** Power users can perform faceted search, multi-model ensemble search, data lineage tracing, event sourcing audit, and NeMo Curator GPU-accelerated quality scoring.

**FRs covered:** FR-QRY-06, FR-QRY-08, FR-CAT-05, FR-ORCH-09, FR-PROC-04 (5 FRs)

**MVP:** Scale (month 6-12)

---

### Dependency Chain

```
Epic 1 (week 1-2) → Epic 2 (week 2-3) ─┐
                                         ├→ Epic 3 (week 3-4) → Epic 4 (week 4-5) → Epic 5 (week 5-6)
                                         │                                    │
                                         └─ (parallel)                         ├→ Epic 6 E2E (week 6-8)
                                                                              │
                                         └─────────────────────────────────────┘
                                                                              │
                              Epic 7 (month 3-6) ←─────────────────────────┘
                              Epic 8 (month 6-12) ←─────────────────────────┘
```

### MVP Layered Scope

| Layer | Epics | FRs | Target | Gate Criteria |
|-------|-------|-----|--------|---------------|
| MVP Core | 1-5 (minimal path) | ~18 | Week 1-6 | Raj can search with embeddings |
| MVP Enhanced | + 2-3 (full) + 6 (E2E) | ~30 | Week 6-8 | Maya E2E: 1000 records, 4 steps, <45 min |
| Production | + 6 (full) + 7 | ~50 | Month 3-6 | Sam deploys to K8s, elastic burst works |
| Scale | + 8 | 68 | Month 6-12 | Full feature set |

### Risk Spikes

| Spike | Epic | Risk Level | Trigger | Mitigation |
|-------|------|------------|---------|------------|
| DuckDB Lance extension validation | Epic 1 | P0 | Epic 1C blocked if fails | Evaluate alternative query paths |
| Daft >= 0.7.8 Arrow compatibility | Epic 1 | P0 | Epic 1A/B blocked if fails | Pin Arrow version matrix |
| NeMo Curator CPU fallback | Epic 4 | High | Epic 4 embedding story | Implement CPU quality scoring fallback |
| Metaflow + Ray integration | Epic 6 | Medium | Epic 6 orchestration stories blocked if `--with ray` fails | Validate in Story 6.1; fallback to local-only execution |
| DuckDB multi-connection | Epic 1 | P0 | Story 1.6 connection pool design invalid if concurrent reads fail | Validate in Story 1.2 Spike; fallback to single-connection sequential reads |

---

## Epic 1: Platform Bootstrap

Maya can `docker compose up -d` to start the platform, create a Lance dataset, register it in the Catalog, and see basic metrics and structured logs flowing.

**FRs:** FR-DEV-01, FR-DEV-03, FR-DEV-04, FR-DEV-06, FR-STOR-01, FR-STOR-08, FR-CAT-01, FR-CAT-02

### Story 1.1: Project Skeleton & Toolchain Setup

As a developer,
I want a properly configured Python project with linting, formatting, type checking, and pre-commit hooks,
So that all contributors follow consistent code quality standards from day one.

**Acceptance Criteria:**

**Given** a clean directory with `pyproject.toml` defining uv workspace, Ruff, MyPy, and dependencies
**When** I run `uv sync`
**Then** all dependencies install successfully with a locked `uv.lock`
**And** `ruff check .` passes with zero errors on the `arrow_lake/` package
**And** `mypy arrow_lake/` passes with strict mode enabled
**And** `pre-commit run --all-files` passes on a clean clone
**And** `.python-version` specifies the pinned Python version
**And** the `arrow_lake/` package structure exists with submodules: `catalog/`, `ingest/`, `quality/`, `embedding/`, `query/`, `ray_runtime/`, `sdk/`
**And** the `flows/` package exists outside the main package for Metaflow Flow definitions
**And** `.gitignore` excludes `.env`, `__pycache__/`, `.venv/`, `*.egg-info/`
**And** `uv sync` fails gracefully with actionable error messages when MinIO is unreachable or disk space is insufficient
**And** CI pipeline (GitHub Actions) runs lint + type-check + unit tests (CPU only) on every push to `main` and PR — this covers Ruff check, MyPy strict mode, and `pytest tests/unit/`; advanced CI (GPU tests, Helm validation) is deferred to Story 7.14

### Story 1.2: Spike — Technology Compatibility Validation

As a developer,
I want to validate that Daft >= 0.7.8, DuckDB Lance extension, and Pydantic v2 Arrow type mappings work together,
So that I can confirm the core technology stack is viable before committing to implementation.

**Acceptance Criteria:**

**Time-box:** 3 days (including environment setup, test script, result documentation)

**NO-GO Triggers (any one = NO-GO):**
- DuckDB Lance extension fails to query Lance table (basic `SELECT` returns error)
- Daft cannot convert Lance dataset to Arrow RecordBatch without errors
- Pydantic v2 `list_[float32]` field fails to serialize to Arrow schema
- Arrow buffer address comparison shows silent data copy at Lance→Daft boundary (zero-copy verification fails)
- Metaflow `python flow.py run --with ray` fails to initialize Ray cluster or submit tasks (validates Epic 6 critical dependency)

**NO-GO Fallback Plans:**
- DuckDB failure: Switch to Daft SQL as OLAP engine (sacrifice analytical depth) or DuckDB as pure catalog store with OLAP via Daft
- Daft failure: Pin to minimum viable Daft version; if incompatible, evaluate Polars as DataFrame replacement
- Pydantic failure: Use manual Arrow schema construction with explicit type mappings
- Metaflow+Ray failure: Evaluate `@ray.remote` decorator pattern as lightweight alternative; if incompatible, defer Metaflow to Sprint 5 and use pure Ray for orchestration

**Given** a fresh Python environment with `pip install daft>=0.7.8 duckdb lancedb pydantic>=2.0 pyarrow`
**When** I run a compatibility test script
**Then** Daft can read a Lance dataset and convert to Arrow without errors
**And** DuckDB can query a Lance table via the DuckDB Lance extension with `SELECT * FROM lance_table LIMIT 10`
**And** DuckDB Lance extension supports concurrent read connections (≥4 simultaneous readers) with correct query results — validates Story 1.6 connection pool design
**And** Pydantic v2 models with `list_[float32](768)` fields serialize to Arrow schema correctly
**And** pyarrow version is pinned in `pyproject.toml` to an exact compatible version (e.g., `pyarrow==15.x.y`) — the `>=` range constraint is insufficient; the spike must produce a fixed pin based on Daft + Lance compatibility testing
**And** a compatibility matrix is documented in `docs/tech-compatibility.md` listing tested versions
**And** the spike produces a GO/NO-GO recommendation documented in the project README with explicit pass/fail per validation item
**And** a minimal Metaflow flow with `@ray` decorator can run via `python flow.py run --with ray` and submit a basic task to Ray — validates Epic 6 critical dependency

### Story 1.3: Configuration & Settings Layer

As a developer,
I want a 4-layer configuration system (code defaults -> .env file -> environment variables -> Metaflow YAML),
So that platform configuration works consistently across local dev, staging, and production without code changes.

**Acceptance Criteria:**

**Given** Pydantic Settings model `ArrowLakeConfig` with fields for storage, compute, observability, and security
**When** I load configuration without any `.env` file or environment variables
**Then** sensible defaults are applied (local MinIO, no GPU, metrics on port 8000)
**When** I create a `.env` file with `S3_ENDPOINT=http://localhost:9000`
**Then** the `.env` file values override code defaults
**When** I set `ARROW_LAKE__S3_ENDPOINT=http://staging-s3.internal:9000` as an environment variable
**Then** the environment variable overrides both `.env` and code defaults
**When** a Metaflow Config YAML is loaded
**Then** the YAML values override all other layers
**And** fail-fast validation rejects startup if required fields (e.g., `STORAGE__BACKEND`) are missing or invalid
**And** `.env.example` contains all configurable fields with placeholder values and documentation comments
**And** `configs/dev.yaml`, `configs/staging.yaml`, `configs/prod.yaml` template files exist

### Story 1.4: SDK Foundation & Exception Hierarchy

As a developer,
I want a minimal Python SDK with a clear entry point and custom exception hierarchy,
So that users can start interacting with Arrow Lake from a clean import.

**Acceptance Criteria:**

**Given** the `arrow_lake` package is installed
**When** I run `from arrow_lake import Lake`
**Then** the import succeeds without errors
**When** I call `Lake()` with default configuration
**Then** a Lake instance is created connected to the local development backend
**And** `help(Lake)` shows available methods: `ingest()`, `search()`, `catalog()`, `version()`
**And** a custom exception hierarchy exists: `ArrowLakeError` (base), `StorageError`, `QueryError`, `IngestError`, `ConfigurationError`, `ValidationError`
**And** all exceptions include structured attributes: `error_code` (enum), `message`, `context` (dict)
**And** `tests/unit/test_exceptions.py` validates all exception types are importable and raiseable

### Story 1.5: Observability Scaffolding

As a platform engineer,
I want structured JSON logging with correlation IDs and a Prometheus metrics registry from day one,
So that I can debug issues and track platform health across all components from the start.

**Acceptance Criteria:**

**Given** the `structlog` library is configured in `arrow_lake.core.logging`
**When** any module logs a message at INFO level
**Then** the output is structured JSON with keys: `timestamp`, `level`, `module`, `message`, `correlation_id`
**And** `correlation_id` defaults to a UUID and can be set from environment variable `ARROW_LAKE__CORRELATION_ID`
**When** the metrics module initializes in `arrow_lake.core.metrics`
**Then** a Prometheus registry is created with naming pattern `arrow_lake_{domain}_{metric}_{unit}`
**And** 3 basic metrics are defined for Epic 1: `arrow_lake_system_uptime_seconds` (Gauge, labels: none), `arrow_lake_catalog_tables_total` (Gauge, labels: none), `arrow_lake_catalog_queries_total` (Counter, labels: query_type)
**And** remaining metrics (ingestion, query, processing, quality, error domains) are introduced in their respective feature Epics (Epic 3-7) as each capability is implemented
**And** each metric definition includes: name, type (Counter/Histogram/Gauge), label schema, and description
**And** `tests/unit/test_metrics.py` validates all registered metrics have correct types, labels, and naming convention
**And** metrics can be disabled via `ARROW_LAKE__METRICS_ENABLED=false` environment variable
**And** an `ArrowCopyDetector` utility is provided in `arrow_lake.core.validation` that compares Arrow buffer addresses at component boundaries to verify zero-copy; it logs a WARNING if a silent copy is detected and increments `arrow_lake_zero_copy_violations_total` (Counter, labels: boundary)
**And** `ArrowCopyDetector` is integrated into the 6 standard boundary tests (test_boundary_lance_daft, test_boundary_daft_duckdb, etc.) as a reusable assertion helper

### Story 1.6: DuckDB WAL Connection Pool

As a developer,
I want a custom WAL-mode connection pool for DuckDB with configurable read and write connections,
So that the Catalog Actor and query operations can share DuckDB without writer starvation or connection exhaustion.

**Acceptance Criteria:**

**Given** a `DuckDBConnectionPool` class initialized with `read_connections=4, write_connections=1` (catalog-only sizing)
**When** I acquire a write connection and execute `CREATE TABLE test (id INT)`
**Then** the write succeeds and the connection is returned to the pool after use (context manager)
**When** I acquire 4 read connections concurrently and execute `SELECT * FROM test`
**Then** all 4 reads succeed simultaneously
**When** I attempt to acquire a 5th read connection while all 4 are busy
**Then** the pool blocks until a connection is returned or times out (configurable timeout)
**And** `PoolHealth` model reports: `active_read`, `active_write`, `idle`, `waiters` counts
**And** health check endpoint `GET /health` returns pool status as JSON
**And** `tests/unit/test_connection_pool.py` validates concurrent access patterns

### Story 1.7: Lance Storage Foundation

As a developer,
I want to read and write Lance datasets with Arrow-native I/O on S3/MinIO backends,
So that all multimodal data is stored in a versioned, columnar format with zero-copy potential.

**Acceptance Criteria:**

**Given** a MinIO bucket `arrow-lake-test` is available (from docker-compose)
**When** I create a Lance dataset with schema `pa.schema([pa.field("id", pa.string()), pa.field("modality", pa.string()), pa.field("created_at", pa.timestamp("us"))])`
**Then** the dataset is created at `s3://arrow-lake-test/datasets/test.lance/`
**And** `lance.dataset("s3://arrow-lake-test/datasets/test.lance/")` can read it back as an Arrow Table
**When** I append 1000 rows to the dataset
**Then** the dataset version increments to 2 and both versions are readable
**And** `dataset.version` returns the current version number
**And** `tests/integration/test_lance_roundtrip.py` validates write-read consistency with real MinIO
**And** Arrow zero-copy is validated at component boundaries (NOT write-read address comparison): Lance RecordBatch → DuckDB query produces shared memory reference; Lance RecordBatch → Daft DataFrame shares underlying Arrow array buffers; Lance RecordBatch → PyTorch tensor uses `pin_memory + non_blocking` transfer
**And** `tests/integration/test_boundary_lance_duckdb.py` and `test_boundary_lance_daft.py` verify zero inter-component copy via buffer reference identity

### Story 1.8: Catalog Actor (Ray Named Actor)

As a developer,
I want a centralized Catalog as a Ray Named Actor that registers datasets and exposes metadata via SQL,
So that all components can discover and query available datasets through a single source of truth.

**Acceptance Criteria:**

**Given** a Ray cluster is running (local, single node)
**When** I create a CatalogActor via `ray.remote(CatalogActor).remote()`
**Then** the actor is registered as a named actor and retrievable via `ray.get_actor("CatalogActor")`
**When** I call `catalog.register("my_table", uri="s3://arrow-lake-test/datasets/my.lance/", schema=arrow_schema, namespace="default")`
**Then** the dataset metadata is stored in the embedded DuckDB with `namespace` field reserved for future multi-tenant isolation
**And** `catalog.list_datasets()` returns a list with schema and row count info
**And** `catalog.get_dataset("my_table")` returns the full DatasetInfo including schema, column metadata, statistics
**When** I call `catalog.query_metadata("SELECT name, row_count FROM datasets WHERE modality = 'image'")`
**Then** DuckDB returns matching dataset metadata
**And** the actor has `max_restarts=3` configured for auto-recovery
**And** `tests/integration/test_catalog_actor.py` validates CRUD operations and SQL queries

### Story 1.9: Docker Compose Local Development

As a platform engineer,
I want a single `docker compose up -d` command that starts all platform services (MinIO, Ray, Jupyter),
So that any developer can have a fully functional local environment running in minutes.

**Acceptance Criteria:**

**Given** the project root directory with `docker-compose.yml`
**When** I run `docker compose up -d`
**Then** the following services start successfully: MinIO (port 9000), Ray head (port 8265), Ray worker (optional GPU), Jupyter (port 8888)
**And** MinIO creates a default bucket `arrow-lake` on first startup
**And** Ray Dashboard is accessible at `http://localhost:8265`
**And** Jupyter notebook can `import ray` and `import arrow_lake`
**And** the `docker-compose.yml` configures resource limits (CPU: 4, Memory: 8GB)
**And** a `docker-compose.gpu.yml` overlay exists for GPU passthrough (when `docker compose -f docker-compose.yml -f docker-compose.gpu.yml up -d`)
**And** Prometheus `/metrics` endpoint is accessible at `http://localhost:8000/metrics`
**And** all services use a shared Docker bridge network with proper inter-service DNS resolution

### Story 1.10: Platform Boot Smoke Test

As a platform engineer,
I want an end-to-end smoke test that validates all services start in correct order and configuration resolves properly,
So that I have confidence the entire platform bootstrap is working before building features on top.

**Acceptance Criteria:**

**Given** `docker compose up -d` has completed
**When** I run `python -c "from arrow_lake import Lake; lake = Lake(); print(lake.health())"`
**Then** the health check returns `{"status": "healthy", "services": {"minio": "ok", "ray": "ok", "catalog": "ok"}, "metrics": "accessible"}`
**When** I run `pytest tests/smoke/test_platform_boot.py`
**Then** all 5 smoke tests pass: MinIO connectivity, Ray actor registration, Catalog CRUD, Lance read/write, /metrics endpoint
**And** total smoke test execution time is under 30 seconds
**And** the test logs include service startup order and health check timestamps
**And** NFR-USE-01 gate is validated: onboarding from git clone to smoke test pass < 30 minutes

---

## Epic 2: Data Versioning & Management

Maya can tag dataset versions, time-travel to any historical state, compare versions side-by-side, compact storage, evolve schemas, and validate data correctness with pytest.

**FRs:** FR-STOR-02, FR-STOR-03, FR-STOR-04, FR-STOR-05, FR-STOR-06, FR-PROC-07, FR-DEV-05

### Story 2.1: Automatic Versioning on Every Write

As a data engineer,
I want every write operation to a Lance dataset to automatically create a new version,
So that I never lose data and can always recover to a previous state without manual backups.

**Acceptance Criteria:**

**Given** a Lance dataset at version 2
**When** I append 500 new rows via `dataset.append(arrow_table)`
**Then** the dataset version increments to 3
**And** `dataset.version` returns 3
**And** `dataset.versions()` returns `[1, 2, 3]`
**And** both version 2 and version 3 data are independently queryable
**And** `tests/unit/test_versioning.py` validates version auto-increment on append, merge, and overwrite

### Story 2.2: Named Tags for Important Versions

As a data engineer,
I want to tag specific dataset versions with meaningful names (e.g., "production", "experiment-v3"),
So that I can quickly reference important milestones without remembering version numbers.

**Acceptance Criteria:**

**Given** a Lance dataset at version 5
**When** I call `dataset.create_tag("production", version=5)`
**Then** the tag "production" is created pointing to version 5
**And** `dataset.list_tags()` returns `["production"]`
**When** I call `dataset.checkout("production")`
**Then** the dataset reader points to version 5
**And** `dataset.checkout("nonexistent")` raises a `TagNotFoundError`
**And** tags are persisted in Lance metadata and survive dataset reload
**And** `tests/unit/test_tags.py` validates tag CRUD and checkout

### Story 2.3: Time-Travel Query

As a data engineer,
I want to read any historical version of a dataset without modifying the current version,
So that I can inspect data at any point in time for debugging or auditing.

**Acceptance Criteria:**

**Given** a Lance dataset at version 5
**When** I call `lance.dataset(uri, version=2).to_table()`
**Then** I receive an Arrow Table with data as it existed at version 2
**And** the current dataset remains at version 5 (no side effects)
**When** I query `lance.dataset(uri, version=1).to_table()`
**Then** I receive data from the first version
**And** `tests/unit/test_time_travel.py` validates reading multiple historical versions in sequence

### Story 2.4: Version Diff

As an ML scientist,
I want to compare two dataset versions to see schema changes, row additions/deletions, and column modifications,
So that I can understand what changed between experiments.

**Acceptance Criteria:**

**Given** a Lance dataset with versions 3 and 5
**When** I call `dataset.diff(version_left=3, version_right=5)`
**Then** the result includes: added_rows, removed_rows, schema_changes (added/removed/altered columns), column_stats_diff
**And** schema_changes lists specific column names and their type changes
**And** `dataset.diff("production", "staging")` works with tag names
**And** the diff output is serializable to JSON for logging
**And** `tests/unit/test_version_diff.py` validates diff accuracy on known dataset changes

### Story 2.5: Compaction

As a data engineer,
I want to compact a Lance dataset by merging Fragment files and reclaiming space from dropped columns,
So that query performance stays fast as the dataset grows through many writes.

**Acceptance Criteria:**

**Given** a Lance dataset with 50+ small Fragment files from many append operations
**When** I call `dataset.compact()`
**Then** the number of Fragment files decreases significantly (measurable reduction)
**And** all existing data remains queryable with identical results
**And** `dataset.version` increments (compaction is a write operation)
**And** `dataset.optimize.compaction()` with configurable `target_fragment_size` parameter works
**And** `tests/integration/test_compaction.py` validates: pre-compaction file count, post-compaction file count, data integrity, version increment

### Story 2.6: Schema Migration

As a developer,
I want to add, alter, or drop columns in a Lance dataset without full data rewrite,
So that the schema can evolve as the project matures without costly migration jobs.

**Acceptance Criteria:**

**Given** a Lance dataset with columns `[id, name, age]`
**When** I add a new column `email` via `dataset.alter_columns({"email": pa.string()})`
**Then** the column is added without rewriting existing data
**And** existing rows have `email = null` (NULL-safe)
**When** I alter the `age` column type from `int32` to `int64` via `dataset.alter_columns({"age": pa.int64()})`
**Then** existing integer values are preserved
**And** when I drop a column `age` via `dataset.alter_columns({"age": None})`
**Then** the column is removed and storage is reclaimed on next compaction
**And** `tests/integration/test_schema_migration.py` validates add, alter type, and drop operations

### Story 2.7: Data Testing Framework

As a data engineer,
I want pytest assertions that validate Lance/Daft/DuckDB results for data correctness,
So that I can build regression tests that catch data quality issues early.

**Acceptance Criteria:**

**Given** the `arrow_lake.testing` module with assertion helpers
**When** I write `assert_table_has_schema(table, expected_schema)` in a test
**Then** the assertion passes if Arrow schemas match, with clear diff on failure
**And** `assert_row_count(table, expected=1000)` validates row counts
**And** `assert_column_values_unique(table, "id")` validates uniqueness
**And** `assert_column_within_range(table, "quality_score", min=0.0, max=1.0)` validates numeric ranges
**And** `assert_dataset_version(dataset, expected_version=5)` validates Lance version
**And** `tests/unit/test_testing_framework.py` validates all assertion helpers on both passing and failing cases
**And** all helpers produce clear error messages with expected vs actual values on failure

### Story 2.8: Dataset Lifecycle Management

As a data engineer,
I want to delete and archive datasets with proper cleanup of Lance storage and catalog metadata,
So that I can manage storage costs and remove deprecated or test datasets without orphaned data.

**Acceptance Criteria:**

**Given** a registered dataset in the Catalog
**When** I call `lake.catalog.delete_dataset("my_table", cascade=True)`
**Then** all Lance dataset versions and fragments are removed from S3/MinIO storage
**And** the dataset entry is removed from the Catalog DuckDB database
**And** any associated dead-letter tables are also removed

**Given** a registered dataset that I want to preserve but remove from active use
**When** I call `lake.catalog.archive_dataset("my_table")`
**Then** the dataset entry is marked as `status='archived'` in the Catalog
**And** the dataset no longer appears in `catalog.list_datasets()` (unless `include_archived=True`)
**And** the underlying Lance data remains intact and can be restored via `catalog.restore_dataset("my_table")`

**Given** a delete operation on a dataset that is referenced by an active pipeline
**When** the delete is attempted
**Then** the operation is rejected with `ErrorCode.DATASET_IN_USE` and a message listing the active references
**And** `tests/integration/test_dataset_lifecycle.py` validates delete, archive, restore, and in-use protection

---

## Epic 3: Multimodal Ingestion

Maya can ingest text, images, and video from local FS, S3, or HTTP into a unified Lance table with lazy blob loading and automatic thumbnail generation.

**FRs:** FR-ING-01, FR-ING-02, FR-ING-03, FR-ING-06, FR-ING-09, FR-PROC-01, FR-PROC-06

### Story 3.1: Local and S3 Data Ingestion

As a data engineer,
I want to ingest text, CSV, JSON, and Parquet files from local filesystem and S3/MinIO into a unified format,
So that I can consolidate structured and semi-structured data from local and cloud storage into a single lakehouse table.

**Acceptance Criteria:**

**Given** a directory containing sample CSV, JSON, and Parquet files on local filesystem
**When** the ingestion pipeline is invoked with source path pointing to that directory
**Then** all supported files are detected by file extension and MIME type inspection and loaded into a Daft DataFrame
**And** the resulting DataFrame contains the merged schema of all ingested files with correct column types
**And** unsupported file extensions are logged and skipped without raising exceptions

**Given** an S3/MinIO bucket URI (e.g. `s3://my-bucket/data/`) containing CSV and JSON files
**When** the ingestion pipeline is invoked with the S3 URI as source
**Then** files are listed and read via boto3/S3FS and loaded into Daft using `daft.read_csv` / `daft.read_json` abstractions
**And** authentication credentials are resolved from environment variables (`AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_ENDPOINT_URL`)

**Given** a source configuration with mixed local + S3 protocols
**When** the ingestion pipeline processes all sources
**Then** each source is handled by the appropriate connector and results are unioned into a single DataFrame
**And** per-source ingestion statistics (row count, file count, errors) are reported
**And** `tests/unit/test_ingest_local_s3.py` validates local and S3 connectors with mock responses
**And** ingestion throughput is measured: a benchmark of 50,000 CSV rows (10 columns, mixed types) completes in under 1 second on a single CPU core (NFR-PERF-02 baseline)

### Story 3.2: HTTP Source Ingestion and Mixed-Source Union

As a data engineer,
I want to ingest data from HTTP endpoints and combine results from mixed protocols (local + S3 + HTTP) into a single DataFrame,
So that I can pull data from remote APIs and URLs alongside local and cloud sources.

**Acceptance Criteria:**

**Given** an HTTP URL pointing to a CSV or JSON file
**When** the ingestion pipeline is invoked with the HTTP URL as source
**Then** the file is streamed and parsed into a Daft DataFrame
**And** HTTP errors (4xx, 5xx) are caught and surfaced with descriptive `ErrorCode.HTTP_FETCH_FAILED`
**And** HTTP timeouts and retries are configurable via `ARROW_LAKE__HTTP_TIMEOUT_SECONDS` and `ARROW_LAKE__HTTP_MAX_RETRIES`

**Given** a source configuration with mixed protocols (local + S3 + HTTP)
**When** the ingestion pipeline processes all sources
**Then** schema merge conflicts between sources are resolved: shared columns use their merged type; source-specific columns are NULL-padded
**And** per-source ingestion statistics (row count, file count, errors) are reported
**And** `tests/unit/test_ingest_http_mixed.py` validates HTTP connector with mock responses and mixed-protocol union

### Story 3.3: Image Ingestion with Thumbnail Generation

As a data engineer,
I want to ingest JPEG, PNG, and WebP images with automatic thumbnail generation and EXIF extraction,
So that downstream consumers can browse image previews without loading full-resolution originals.

**Acceptance Criteria:**

**Given** a directory of JPEG, PNG, and WebP image files
**When** the ingestion pipeline processes each image
**Then** the original binary is stored as out-of-line blob data and a 64x64 thumbnail is generated and stored alongside it
**And** EXIF metadata (camera make/model, GPS coordinates, capture timestamp) is extracted and stored in dedicated columns
**And** images with corrupt headers are rejected with `ErrorCode.IMAGE_DECODE_FAILED` and written to the dead-letter table

**Given** an image larger than 10,000 x 10,000 pixels
**When** the thumbnail generator processes the image
**Then** the thumbnail is generated from a downscaled intermediate (max 4096px on longest side) to avoid excessive memory usage
**And** the thumbnail dimensions remain exactly 64x64 or 256x256 as configured

**Given** an image with no EXIF data
**When** EXIF extraction is attempted
**Then** all EXIF columns are populated with NULL and no error is raised
**And** the image still passes ingestion without rejection

**Given** the configuration `ARROW_LAKE__THUMBNAIL_SIZE=256`
**When** thumbnails are generated during ingestion
**Then** all thumbnails are 256x256 pixels
**And** `tests/unit/test_thumbnail.py` validates size, EXIF extraction, and corrupt image handling

### Story 3.4: Video Keyframe Extraction

As a data engineer,
I want to extract representative keyframes from video files at scene boundaries,
So that video content can be indexed, searched, and previewed alongside images and text.

**Acceptance Criteria:**

**Given** a directory of MP4 and MKV video files
**When** the ingestion pipeline processes each video
**Then** keyframes are extracted at detected scene boundaries using PyAV
**And** each keyframe is stored as a JPEG blob with a `timestamp_ms` column recording its position in the video
**And** at least one keyframe is extracted per video (the first frame as fallback)

**Given** a video where PyAV scene detection fails or times out (e.g. corrupted or extremely short video)
**When** keyframe extraction is attempted
**Then** the first frame of the video is extracted as the sole keyframe
**And** a warning with `ErrorCode.SCENE_DETECTION_FALLBACK` is logged but ingestion proceeds

**Given** a video file that cannot be opened by PyAV (unsupported codec, corrupt file)
**When** keyframe extraction is attempted
**Then** the video is rejected with `ErrorCode.VIDEO_DECODE_FAILED`
**And** the rejection reason and video metadata are written to the dead-letter table

**Given** a 60-second video with 5 scene changes
**When** keyframe extraction completes
**Then** exactly 5 keyframes are extracted (or 6 if counting the first frame), each with accurate `timestamp_ms`
**And** `tests/unit/test_video_ingest.py` validates scene detection, fallback behavior, and corrupt video rejection

### Story 3.5: Unified Multimodal Table Storage

As a data engineer,
I want to store text, images, videos, and audio in a single Lance table with a consistent schema,
So that I can query across modalities without joining separate tables.

**Acceptance Criteria:**

**Given** ingested data from multiple modalities (text CSVs, images, videos)
**When** data is written to Lance
**Then** all rows are stored in a single Lance table with schema: `id (string), modality (string), source (string), created_at (timestamp), text_content (string), image_data (binary), video_data (binary)`
**And** modality-specific columns are populated while irrelevant columns contain NULL (e.g. a text row has NULL `image_data`, `video_data`)
**And** the schema is extensible — future modalities (e.g. audio_data) can be added via schema migration (Story 2.6) without breaking existing data
**And** NULL-safe operations return correct results (e.g. `WHERE image_data IS NOT NULL` returns only image rows)

**Given** a new batch of rows with a column not present in the existing Lance table schema
**When** schema validation is applied (Lance native schema enforcement; pluggable strict mode deferred to Epic 4 Story 4.10 per FR-QUA-05)
**Then** rows with unknown columns are rejected with a warning log; rejected rows are tracked in pipeline error metrics (dead-letter table formalization deferred to Epic 4 Story 4.8)
**And** ingestion of valid rows continues without interruption

**Given** the unified Lance table with mixed modality data
**When** a query filters by `modality = 'image'`
**Then** only image rows are returned with correct `image_data` blobs
**And** predicate pushdown delegates the filter to Lance's scanner for efficiency
**And** `tests/integration/test_unified_table.py` validates multi-modality write, NULL safety, and predicate pushdown

### Story 3.6: Multi-Fidelity Blob Storage

As a data engineer,
I want to store media at multiple fidelity levels (thumbnail, preview, original) with lazy loading,
So that queries that only need metadata or previews avoid expensive full-resolution blob I/O.

**Acceptance Criteria:**

**Given** an image ingested into the unified table
**When** the ingestion pipeline stores the image
**Then** three fidelity levels are stored: `thumbnail` (64x64), `preview` (512x512), and `original` (full resolution)
**And** each fidelity level is accessible via a dedicated column or sub-column (e.g. `image_data.thumbnail`, `image_data.preview`, `image_data.original`)

**Given** a query that selects only `id` and `caption` columns
**When** the query is executed
**Then** zero blob I/O occurs — no image bytes are read from disk
**And** query latency is comparable to a metadata-only scan on a text table
**And** the test verifies zero blob I/O via a mock S3 client that tracks `get_object` call count and total bytes transferred (expect: 0 bytes for blob columns not in SELECT)

**Given** a query that selects `id` and requests thumbnail data
**When** the query is executed
**Then** only thumbnail blobs are loaded, not previews or originals
**And** the byte size of loaded data is bounded by the thumbnail size, not the original image size

**Given** an original media file that is no longer needed at full fidelity
**When** a blob lifecycle policy is configured (e.g. `ARROW_LAKE__RETENTION_ORIGINAL_DAYS=90`)
**Then** original blobs older than the retention period are eligible for automatic cleanup
**And** thumbnail and preview fidelity levels are retained regardless of the policy
**And** `tests/unit/test_multi_fidelity.py` validates fidelity-level storage and lazy loading behavior

### Story 3.7: Daft DataFrame API for Transformations

As a data engineer,
I want a Daft DataFrame wrapper that provides select, filter, sort, join, and group operations on multimodal data,
So that I can transform and query ingested data using a familiar DataFrame API before writing results.

**Acceptance Criteria:**

**Given** a Daft DataFrame loaded from the unified Lance table
**When** `.select("id", "modality", "caption")` is called
**Then** a new DataFrame with only the specified columns is returned
**And** no computation is triggered until `.collect()` is called (lazy evaluation)

**Given** a Daft DataFrame with mixed modality data
**When** `.filter(daft.col("modality") == "image")` is called followed by `.sort("created_at", desc=True)`
**Then** a lazy plan is constructed that filters to image rows and sorts by creation timestamp descending
**And** `.collect()` executes the plan and returns the sorted image rows

**Given** two Daft DataFrames — one with image embeddings and one with text embeddings
**When** `.join(other_df, on="id", how="inner")` is called
**Then** rows are matched by `id` and both image and text embedding columns are present in the result
**And** the join is executed lazily until `.collect()`

**Given** a Daft DataFrame result after `.collect()`
**When** the result is converted via `.to_arrow()`
**Then** a valid PyArrow Table is returned with correct schema and data types
**And** binary columns (image_data, video_data) are preserved as `pa.binary()` type
**And** `tests/unit/test_daft_api.py` validates select, filter, sort, join, groupby, and to_arrow conversion

### Story 3.8: Lazy Download & Decode for Media

As a data engineer,
I want images and videos to remain on storage until pixel access is explicitly requested,
So that metadata-only scans and filtering operations avoid the cost of downloading and decoding large media files.

**Acceptance Criteria:**

**Given** a unified Lance table with 1,000 image rows stored in S3
**When** a query `SELECT id, caption FROM table WHERE modality = 'image'` is executed
**Then** zero image bytes are downloaded from S3 and zero decode operations occur
**And** query completes with latency comparable to a text-only table of the same row count

**Given** a query that requests pixel data: `SELECT id, image_data.preview FROM table WHERE id = 'abc123'`
**When** the query is executed
**Then** only the preview fidelity of the matching image is downloaded and decoded
**And** the original fidelity remains on storage and is not accessed

**Given** the configuration `ARROW_LAKE__DECODE_QUALITY=thumbnail`
**When** any media column is accessed
**Then** only thumbnail fidelity is decoded and returned by default
**And** changing configuration to `full` causes full-resolution decode on subsequent access

**Given** a query that accesses image pixel data for 50 rows
**When** the query is executed
**Then** media files are downloaded and decoded lazily one at a time (or in configured batch), not eagerly for the entire table
**And** memory usage remains bounded regardless of total table size
**And** `tests/integration/test_lazy_decode.py` validates zero-download metadata scans and on-demand pixel access

### Story 3.9: Basic Metadata Search Bridge

As a data engineer,
I want to search ingested data by filename, modality, and date range using simple SQL queries,
So that I can verify ingested data correctness and find specific records before full semantic search is available (Epic 5).

**Acceptance Criteria:**

**Given** a Lance table with mixed modality data ingested via Stories 3.1-3.3
**When** I execute `SELECT * FROM my_table WHERE filename LIKE '%report%'` via DuckDB
**Then** matching rows are returned with correct blob data
**And** the query leverages Lance predicate pushdown for efficiency

**Given** the same table
**When** I execute `SELECT modality, COUNT(*) FROM my_table WHERE created_at >= '2026-01-01' GROUP BY modality`
**Then** per-modality row counts are returned correctly
**And** the query completes in under 1 second for tables with up to 100,000 rows

**Given** an SDK call `lake.query("SELECT * FROM my_table WHERE modality = 'image' LIMIT 10")`
**When** the query is executed
**Then** results are returned as a Daft DataFrame convertible to Arrow format
**And** this bridge query API is consistent with the full search API introduced in Epic 5
**And** `tests/integration/test_metadata_search.py` validates filename filtering, date range, and modality grouping

---

## Epic 4: Embedding & Quality

Maya can compute embeddings during ingestion, apply pluggable quality filters, deduplicate content, and persist rejected rows to a dead-letter table.

**FRs:** FR-ING-04, FR-ING-05, FR-ING-07, FR-ING-08, FR-PROC-02, FR-PROC-05, FR-QUA-01, FR-QUA-02, FR-QUA-03, FR-QUA-04, FR-QUA-05

**Risk Spikes:** NeMo Curator CPU fallback validation (High probability: NVIDIA-only dependency)

### Story 4.1: Text Embedding with Local HuggingFace

As a data engineer,
I want to compute vector embeddings for text content using local HuggingFace models with batch processing,
So that text data can be semantically searched and compared via vector similarity without external API dependencies.

**Acceptance Criteria:**

**Given** a Lance table with 10,000 text rows (modality='text')
**When** the embedding pipeline is invoked with `model="BAAI/bge-small-en-v1.5"`
**Then** embeddings are computed and stored in column `text_embedding` with type `pa.list_(pa.float32(), 384)`
**And** the pipeline runs on GPU when available, CPU otherwise

**Given** rows with empty or NULL `text_content`
**When** the embedding pipeline processes those rows
**Then** they receive NULL embeddings without raising errors

**Given** a batch of 10,000 text rows
**When** the embedding pipeline runs with batch size 128
**Then** exactly 78 full batches and 1 partial batch (16 rows) are processed
**And** the embedding column has 10,000 non-NULL values upon completion
**And** `tests/unit/test_text_embedding_local.py` validates batch processing, NULL handling, and GPU/CPU execution

### Story 4.2: Ray Serve Embedding Backend with Fallback

As a data engineer,
I want to deploy embedding computation as a Ray Serve endpoint for scalable distributed processing with automatic fallback,
So that embedding pipelines can scale horizontally under load without external API dependencies.

**Acceptance Criteria:**

**Given** a Ray cluster with Ray Serve deployed
**When** the embedding pipeline is invoked with `ARROW_LAKE__EMBEDDING_BACKEND=ray_serve`
**Then** embeddings are computed via Ray Serve deployment for scalable distributed processing
**And** the pipeline falls back to local HuggingFace inference if Ray Serve is unavailable
**And** the fallback transition logs a warning with `ErrorCode.EMBEDDING_RAY_SERVE_FALLBACK`

**Given** a Ray Serve endpoint under concurrent load (multiple pipeline steps requesting embeddings)
**When** the pipeline processes requests in parallel
**Then** Ray Serve handles concurrent requests with proper queuing and resource management
**And** `tests/unit/test_text_embedding_ray_serve.py` validates Ray Serve invocation, fallback, and concurrent behavior

### Story 4.3: External API Embedding (OpenAI-Compatible)

As a data engineer,
I want to compute embeddings via external API endpoints (OpenAI or compatible) with retry and error handling,
So that I can leverage proprietary or cloud-hosted embedding models without self-hosting.

**Acceptance Criteria:**

**Given** the configuration `ARROW_LAKE__EMBEDDING_BACKEND=openai`
**When** the embedding pipeline is invoked
**Then** embeddings are computed via the OpenAI API (or compatible endpoint at `ARROW_LAKE__EMBEDDING_API_BASE`)
**And** API errors (rate limit, timeout, auth failure) are caught with `ErrorCode.EMBEDDING_API_ERROR` and retried with exponential backoff up to 3 attempts
**And** API key is resolved from environment variable `ARROW_LAKE__EMBEDDING_API_KEY`

**Given** the external API is unreachable
**When** the embedding pipeline is invoked
**Then** the pipeline falls back to local HuggingFace inference with a warning log
**And** `tests/unit/test_text_embedding_api.py` validates API invocation, retry logic, and fallback using mock API responses

**Given** a Lance table with 1,000 rows containing `text_content`
**When** the embedding pipeline is invoked with `model="sentence-transformers/all-MiniLM-L6-v2"`
**Then** embeddings are computed in async batches (configurable batch size via `ARROW_LAKE__EMBEDDING_BATCH_SIZE`) and stored in a new column `text_embedding` with type `pa.list_(pa.float32(), dim)` where dim matches the model output dimension
**And** rows with empty or NULL `text_content` receive NULL embeddings without raising errors

**Given** the configuration `ARROW_LAKE__EMBEDDING_BACKEND=ray_serve`
**When** the embedding pipeline is invoked
**Then** embeddings are computed via Ray Serve deployment for scalable distributed processing
**And** the pipeline falls back to local HuggingFace inference if Ray Serve is unavailable

**Given** a batch of 10,000 text rows
**When** the embedding pipeline runs with batch size 128
**Then** exactly 78 full batches and 1 partial batch (16 rows) are processed
**And** the embedding column has 10,000 non-NULL values upon completion
**And** `tests/unit/test_text_embedding.py` validates batch processing, NULL handling, and backend fallback

### Story 4.4: Image Embedding Computation

As a data engineer,
I want to compute CLIP and SigLIP embeddings for image content with GPU acceleration,
So that images can be semantically searched and compared via cross-modal vector similarity.

**Acceptance Criteria:**

**Given** a Lance table with 500 image rows (modality='image')
**When** the embedding pipeline is invoked with `model="openai/clip-vit-base-patch32"`
**Then** CLIP embeddings are computed and stored in column `image_embedding` with type `pa.list_(pa.float32(), 512)`
**And** GPU acceleration is used when available (CUDA detected)

**Given** the configuration `ARROW_LAKE__IMAGE_EMBEDDING_MODELS=clip-vit-base-patch32,siglip-so400m-patch14-384`
**When** the embedding pipeline is invoked
**Then** both CLIP and SigLIP embeddings are computed in a single pass and stored in columns `image_embedding_clip` and `image_embedding_siglip`
**And** each column has the correct dimensionality for its respective model

**Given** image ingestion with thumbnails at 256x256
**When** CLIP embeddings are computed
**Then** the thumbnail fidelity is used for embedding computation by default (configurable via `ARROW_LAKE__EMBEDDING_IMAGE_FIDELITY`)
**And** switching to `original` fidelity produces embeddings consistent with the model's expected input resolution

**Given** an image row where `image_data` is NULL or corrupt
**When** the embedding pipeline processes that row
**Then** the embedding column is set to NULL and a warning with `ErrorCode.EMBEDDING_IMAGE_FAILED` is logged
**And** `tests/unit/test_image_embedding.py` validates GPU/CPU fallback and NULL handling

### Story 4.5: GPU/CPU Heterogeneous Scheduling

As a platform operator,
I want Daft to use GPU acceleration when available and fall back gracefully to CPU otherwise,
So that the system works on developer laptops (CPU-only) and production GPU clusters without configuration changes.

**Acceptance Criteria:**

**Given** a machine with NVIDIA GPU and CUDA available
**When** Daft is configured with `use_gpu=True`
**Then** image embedding and video keyframe operations execute on GPU
**And** GPU utilization is visible via `nvidia-smi` during pipeline execution

**Given** a machine with no GPU (CPU-only environment)
**When** Daft is configured with `use_gpu=True` (or default)
**Then** all operations fall back to CPU execution without raising CUDA errors
**And** a warning with `ErrorCode.GPU_UNAVAILABLE_FALLBACK` is logged once at pipeline start
**And** pipeline results are functionally identical to GPU execution (embedding values may differ by floating point precision)

**Given** the configuration `ARROW_LAKE__GPU_MEMORY_FRACTION=0.8`
**When** GPU operations are executed
**Then** at most 80% of available GPU memory is allocated by Daft
**And** out-of-memory errors are handled by reducing batch size and retrying, or falling back to CPU for the remaining batch

**Given** a heterogeneous cluster with 3 GPU nodes and 2 CPU-only nodes
**When** the pipeline is deployed on Ray
**Then** GPU-accelerated tasks are scheduled on GPU nodes and CPU tasks on CPU-only nodes
**And** `tests/unit/test_gpu_scheduling.py` validates GPU detection, CPU fallback, and OOM handling

### Story 4.6: Async Vector Index Build

As a data engineer,
I want vector indexes (IVF_PQ or HNSW) to be built asynchronously after embedding computation,
So that the ingestion pipeline is not blocked by index construction and can continue accepting new data.

**Acceptance Criteria:**

**Given** a Lance table with 50,000 rows of computed embeddings
**When** the ingestion pipeline completes embedding computation
**Then** an async task is launched to build an IVF_PQ index (default) or HNSW index (configurable via `ARROW_LAKE__VECTOR_INDEX_TYPE`)
**And** the ingestion pipeline returns immediately without waiting for index completion

**Given** the async index build task is running
**When** the pipeline status is queried
**Then** index build progress (status: building/complete/failed, rows indexed, elapsed time) is reported
**And** the table remains queryable (via brute-force scan) while the index is being built

**Given** the index build completes successfully
**When** a vector similarity query is executed against the indexed column
**Then** the query uses the vector index (verified by query plan inspection) and returns results in under 100ms for a 50,000-row table
**And** index metadata (type, parameters, build timestamp, row count at build time) is recorded in the Lance dataset catalog

**Given** the index build fails due to insufficient memory or corrupted embeddings
**When** the failure is detected
**Then** an error with `ErrorCode.INDEX_BUILD_FAILED` is logged with full context
**And** the table remains fully functional without the index (brute-force fallback)
**And** `tests/integration/test_index_build.py` validates async build, progress reporting, and failure recovery

### Story 4.7: Content-Addressed Dedup

As a data engineer,
I want to deduplicate content using SHA-256 exact hashes and perceptual hashes for near-duplicate images,
So that the dataset contains only unique content and downstream model training is not biased by repeated samples.

**Acceptance Criteria:**

**Given** a Lance table with 1,000 rows including 50 exact duplicates (identical binary content)
**When** the dedup pipeline is invoked with `strategy=exact`
**Then** SHA-256 hashes are computed on raw binary content and stored in column `dedup_hash`
**And** exactly 50 duplicate rows are identified and flagged with `is_duplicate=True`
**And** dedup statistics are reported: total_rows=1000, unique_rows=950, duplicates_found=50

**Given** a Lance table with image rows including near-duplicates (same image with different compression, slight resize, watermark)
**When** the dedup pipeline is invoked with `strategy=perceptual`
**Then** perceptual hashes (pHash) are computed for all image rows and stored in column `dedup_phash`
**And** near-duplicates within Hamming distance threshold (configurable via `ARROW_LAKE__PERCEPTUAL_DUP_THRESHOLD`) are flagged
**And** the dedup report includes the number of near-duplicate groups and their sizes

**Given** both exact and perceptual dedup are enabled
**When** the dedup pipeline runs
**Then** exact dedup is applied first, then perceptual dedup on the remaining unique rows
**And** the `is_duplicate` column reflects the combined result of both strategies

**Given** deduplication is configured with `ARROW_LAKE__DEDUP_ACTION=flag` (vs `remove`)
**When** the pipeline runs
**Then** duplicate rows are flagged but NOT removed from the table
**And** `ARROW_LAKE__DEDUP_ACTION=remove` causes flagged rows to be excluded from the active dataset
**And** `tests/unit/test_dedup.py` validates exact hash, perceptual hash, combined strategy, and flag/remove actions

### Story 4.8: QualityFilter Registration

As a data engineer,
I want to register custom quality filters via a pluggable protocol interface,
So that I can enforce domain-specific data quality rules without modifying the core pipeline code.

**Acceptance Criteria:**

**Given** the `QualityFilter` protocol defined as:
```python
class QualityFilter(Protocol):
    name: str
    def filter(self, row: dict) -> tuple[bool, str | None]: ...
```
**When** a custom filter `LanguageFilter` is implemented conforming to this protocol
**Then** the filter can be registered via `registry.register("language_filter", LanguageFilter())`
**And** the filter appears in `registry.list_filters()` with its name and description

**Given** three registered filters: `text_length_filter`, `image_resolution_filter`, `language_filter`
**When** the quality pipeline runs with `filter_mode="all"` (AND semantics)
**Then** a row passes only if ALL three filters return `(True, None)`
**And** a row that fails any filter is rejected with the rejection reason from the first failing filter

**Given** a filter that raises an unexpected exception during `filter(row)`
**When** the exception is caught by the pipeline
**Then** the row is rejected with `ErrorCode.FILTER_EXECUTION_ERROR` and the exception traceback is logged
**And** the pipeline continues processing remaining rows

**Given** the configuration `ARROW_LAKE__QUALITY_FILTERS=text_length_filter,language_filter`
**When** the pipeline starts
**Then** only the specified filters are loaded from the registry and applied
**And** `tests/unit/test_quality_filter_registry.py` validates registration, AND/OR semantics, and exception handling

### Story 4.9: Built-in Quality Filters

As a data engineer,
I want built-in quality filters for text length and image resolution with configurable thresholds,
So that I can enforce common quality standards out of the box without writing custom filter code.

**Acceptance Criteria:**

**Given** the `TextLengthFilter` with configuration `min_chars=10, max_chars=10000`
**When** a row with `text_content` of 5 characters is processed
**Then** the filter returns `(False, "text_length: 5 < min_chars(10)")`
**And** a row with 5,000 characters returns `(True, None)`

**Given** the `TextLengthFilter` with configuration `min_chars=10`
**When** a row with NULL `text_content` is processed
**Then** the filter returns `(True, None)` (NULL text is not penalized by length filter)
**And** the filter only evaluates rows where `text_content IS NOT NULL`

**Given** the `ImageResolutionFilter` with configuration `min_width=256, min_height=256`
**When** an image row with dimensions 128x128 is processed
**Then** the filter returns `(False, "image_resolution: 128x128 < min(256x256)")`
**And** an image with dimensions 1024x768 returns `(True, None)`

**Given** both `TextLengthFilter` and `ImageResolutionFilter` are registered and enabled
**When** the quality pipeline processes a mixed table
**Then** text rows are evaluated by `TextLengthFilter` only (image filter is a no-op)
**And** image rows are evaluated by `ImageResolutionFilter` only (text filter is a no-op)
**And** `tests/unit/test_builtin_filters.py` validates threshold configurations and modality-specific evaluation

### Story 4.10: Dead-Letter Persistence

As a data engineer,
I want rejected rows to be automatically written to a dead-letter table with rejection context,
So that I can audit, diagnose, and potentially recover rejected data without losing it.

**Acceptance Criteria:**

**Given** a quality pipeline that rejects 50 rows across multiple filters
**When** the pipeline completes
**Then** all 50 rejected rows are written to a Lance table named `{original_table}_dead_letter`
**And** the dead-letter schema includes: all original columns + `rejection_reason (string)` + `filter_name (string)` + `rejected_at (timestamp)`

**Given** the parent table is at version 5 when dead-letter writes occur
**When** the dead-letter table is created or appended to
**Then** the dead-letter table is versioned with a `parent_version` column set to 5
**And** the dead-letter table maintains its own independent version history

**Given** a row rejected by `TextLengthFilter` with reason "text_length: 3 < min_chars(10)"
**When** the row is written to the dead-letter table
**Then** the `rejection_reason` column contains the exact reason string
**And** the `filter_name` column contains `"TextLengthFilter"`
**And** all original row data columns are preserved intact

**Given** a dead-letter table with 200 accumulated rejected rows
**When** a data engineer reviews the dead-letter table
**Then** they can query by `filter_name`, `rejected_at`, or `rejection_reason` to identify patterns
**And** `tests/integration/test_dead_letter.py` validates schema, version tracking, and queryability

### Story 4.11: Quality Statistics Report

As a data engineer,
I want a comprehensive quality statistics report after each pipeline run,
So that I can assess data health, identify problematic filters, and track quality trends over time.

**Acceptance Criteria:**

**Given** a quality pipeline that processes 10,000 rows with 3 active filters
**When** the pipeline completes
**Then** a statistics report is generated with: total_rows=10000, passed_rows=9500, rejected_rows=500
**And** a per-filter breakdown is included: filter_name, passed_count, rejected_count, pass_rate_percentage

**Given** the quality pipeline report
**When** `report.to_json()` is called
**Then** a JSON-serializable dictionary is returned containing all statistics
**And** the JSON is compatible with Metaflow Cards for visualization in the Metaflow UI

**Given** a pipeline run where `ImageResolutionFilter` rejects 300 rows and `TextLengthFilter` rejects 200 rows
**When** the per-filter breakdown is inspected
**Then** `ImageResolutionFilter` shows 9700 passed / 300 rejected / 97.0% pass rate
**And** `TextLengthFilter` shows 9800 passed / 200 rejected / 98.0% pass rate

**Given** multiple pipeline runs over time
**When** historical quality reports are compared
**Then** trend data (total rejected per run, per-filter rejection trends) can be derived from the serialized reports
**And** `tests/unit/test_quality_report.py` validates report structure, JSON serialization, and per-filter accuracy

### Story 4.12: Schema Validation Gate

As a data engineer,
I want configurable schema validation that rejects or adapts rows with column mismatches,
So that I can enforce strict data contracts or gracefully handle evolving schemas depending on the use case.

**Acceptance Criteria:**

**Given** the configuration `ARROW_LAKE__SCHEMA_VALIDATION=strict`
**When** a row is ingested with a column not present in the target Lance table schema
**Then** the row is rejected with `ErrorCode.SCHEMA_UNKNOWN_COLUMN` and the unknown column name is included in the rejection reason
**And** the pipeline logs a summary of rejected rows at the end of the run

**Given** the configuration `ARROW_LAKE__SCHEMA_VALIDATION=strict`
**When** a row has a type mismatch (e.g. string in an int64 column)
**Then** the row is rejected with `ErrorCode.SCHEMA_TYPE_MISMATCH` specifying the column name, expected type, and actual type
**And** the pipeline does not attempt to cast or coerce the value

**Given** the configuration `ARROW_LAKE__SCHEMA_VALIDATION=lenient`
**When** a row has an unknown column
**Then** the unknown column is dropped with a warning log and the row is ingested with remaining valid columns
**And** no rejection occurs for unknown columns in lenient mode

**Given** the configuration `ARROW_LAKE__SCHEMA_VALIDATION=lenient`
**When** a row has a compatible type mismatch (e.g. int32 value in an int64 column)
**Then** the value is automatically cast to the target type per PyArrow `can_cast` safe and same_kind rules (int32→int64 ✓, float32→float64 ✓, int64→float64 ✓)
**And** an unsafe cast (e.g. float64→int64 with potential data loss) triggers a warning and falls through to strict mode rejection
**And** an incompatible cast (e.g. string→int64) causes rejection even in lenient mode
**And** `tests/unit/test_schema_validation.py` validates strict/lenient modes and type coercion behavior

### Story 4.13: Quality Scores as Lance Columns

As a data engineer,
I want quality scores computed during ingestion to be stored as first-class Lance columns with predicate pushdown support,
So that I can filter and analyze data by quality criteria at query time without additional computation.

**Acceptance Criteria:**

**Given** a completed ingestion pipeline with quality scoring enabled
**When** the Lance table is inspected
**Then** it contains columns: `quality_score (float32)`, `is_duplicate (bool)`, `nsfw_score (float32)`, `aesthetic_score (float32)`
**And** `quality_score` is a composite score (0.0-1.0) derived from individual sub-scores and filter results
**And** advanced sub-scores (`nsfw_score`, `aesthetic_score`) are introduced in Epic 8 (NeMo Curator GPU pipeline) — in MVP Core these columns are NULL if the advanced scoring pipeline is not deployed
**And** `is_duplicate` is populated by the dedup pipeline (Story 4.5) as a boolean flag

**Given** a Lance table with quality score columns
**When** a query `SELECT * WHERE quality_score > 0.8` is executed
**Then** the filter is pushed down to the Lance scanner (verified via query plan) and only matching rows are materialized
**And** query performance is comparable to filtering on any other native Lance column

**Given** rows where quality scoring was partially computed
**When** the table is queried
**Then** inapplicable score columns contain NULL (e.g. `aesthetic_score` is NULL for text rows or when advanced scoring pipeline is not deployed)
**And** `WHERE aesthetic_score IS NOT NULL` correctly returns only rows with applicable scores
**And** `tests/integration/test_quality_predpushdown.py` validates predicate pushdown on quality score columns including NULL handling

---

## Epic 5: Semantic Search & Analytics

Raj can perform vector search, full-text search, hybrid RRF search, and OLAP analytics via SQL, with streaming results and adaptive index selection.

**FRs:** FR-QRY-01, FR-QRY-02, FR-QRY-03, FR-QRY-04, FR-QRY-05, FR-QRY-07, FR-CAT-03, FR-CAT-04

**MVP Core Path endpoint** — Raj's "aha moment"

### Story 5.1: Vector Search

As a data analyst,
I want to perform vector similarity search over multimodal embeddings,
So that I can find semantically similar content across text and image collections.

**Acceptance Criteria:**

**Given** a Lance dataset with <1M rows containing embedding vectors
**When** a vector search query is executed with a query vector, top_k=10, and metric="cosine"
**Then** results are returned as an Arrow Table containing the top 10 most similar records ordered by relevance score descending
**And** the search utilizes an HNSW index for retrieval
**And** each result row includes the record's metadata columns alongside the distance score

**Given** a Lance dataset with >=1M rows containing embedding vectors
**When** a vector search query is executed
**Then** the system automatically selects and uses an IVF_PQ index for retrieval
**And** results are returned as an Arrow Table with relevance scores
**And** the distance metric is configurable as either "cosine" or "l2"
**And** top_k is a configurable parameter controlling the number of returned results

**Given** a vector search query where all results have similarity below a minimum threshold
**When** the query is executed
**Then** an empty result set is returned with a clear indication (not an error)
**And** the response metadata includes the actual max similarity score found for diagnostics

**And** `tests/integration/test_vector_search.py` validates HNSW and IVF_PQ index paths including empty-result scenarios

### Story 5.2: Full-Text Search

As a data analyst,
I want to perform full-text search across text and caption fields,
So that I can locate records by keyword, phrase, or BM25-ranked relevance.

**Acceptance Criteria:**

**Given** a Lance dataset with FTS index created on text_content and caption columns via Lance Tantivy backend
**When** a full-text search query is executed with a search string and top_k=20
**Then** results are returned as an Arrow Table ordered by BM25 relevance score descending
**And** the search spans both text_content and caption columns
**And** top_k is a configurable parameter
**And** results include matched record metadata alongside BM25 scores

**Given** a dataset with no FTS index
**When** a full-text search query is executed
**Then** the system raises a clear error indicating that an FTS index must be created before searching
**And** `tests/integration/test_fts.py` validates index creation, search, and missing-index error handling

### Story 5.3: Hybrid Search with RRF

As a data analyst,
I want to combine vector and full-text search results using Reciprocal Rank Fusion,
So that I can leverage both semantic and lexical relevance for higher-quality retrieval.

**Acceptance Criteria:**

**Given** a Lance dataset with both HNSW/IVF_PQ vector index and FTS index
**When** a hybrid search is executed with a query vector, a text query, and default alpha=0.7
**Then** results are returned as an Arrow Table with RRF-combined relevance scores
**And** the vector search results contribute 70% weight (alpha=0.7) and text search results contribute 30% weight (1-alpha=0.3)
**And** individual scores from each search method are normalized before fusion
**And** results are reranked by the combined RRF score in descending order

**Given** a hybrid search request with custom alpha parameter
**When** the alpha weight is set to a value between 0.0 and 1.0
**Then** the fusion weights are applied according to the custom alpha value
**And** alpha=1.0 returns pure vector search results
**And** alpha=0.0 returns pure full-text search results
**And** `tests/unit/test_hybrid_search.py` validates RRF fusion, alpha weights, and edge cases

### Story 5.4: OLAP Analytics

As a data analyst,
I want to run SQL analytics queries over Lance datasets via Daft SQL (primary engine) with DuckDB available for catalog SQL,
So that I can perform aggregations, groupings, and window functions on large-scale multimodal data.

**Acceptance Criteria:**

**Given** a Lance dataset registered in the catalog
**When** a SQL query with GROUP BY and aggregation functions (SUM, AVG, COUNT) is executed via Daft SQL
**Then** results are returned as an Arrow Table with correct aggregated values
**And** Lance predicate pushdown is applied to filter rows at the storage layer before aggregation

**Given** a SQL query containing COUNT(*)
**When** the query is executed
**Then** the COUNT(*) computation is pushed down to the Lance storage layer for efficient counting
**And** the result is returned without materializing all rows into memory

**Given** a SQL query with window functions (ROW_NUMBER, RANK, LAG/LEAD)
**When** the query is executed
**Then** window function results are computed correctly and returned as an Arrow Table
**And** predicate pushdown is applied for WHERE clauses preceding the window function
**And** `tests/integration/test_olap.py` validates aggregations, window functions, and predicate pushdown via Daft SQL

### Story 5.5: Streaming Results

As a data analyst,
I want to iterate over large query results using Arrow RecordBatch streaming,
So that I can process datasets of any size with constant memory usage.

**Acceptance Criteria:**

**Given** a Lance dataset with 100M+ rows
**When** a search or SQL query is executed and results are consumed via `fetch_record_batch_reader()`
**Then** results are returned as an Arrow RecordBatchReader that yields batches iteratively
**And** the memory footprint remains below 100MB regardless of total result set size
**And** each RecordBatch can be processed and released before the next batch is loaded

**Given** a streaming query in progress
**When** the consumer reads batches from the RecordBatchReader
**Then** only one batch (or a bounded number of batches) is held in memory at any given time
**And** the consumer can stop reading early without materializing the full result set
**And** `tests/integration/test_streaming.py` validates memory bounds with large result sets

### Story 5.6: Adaptive Index Selection

As a system operator,
I want the system to automatically select the appropriate vector index type based on dataset size,
So that query performance is optimized without manual index management.

**Acceptance Criteria:**

**Given** a Lance dataset with fewer than the configurable row threshold (default 1,000,000 rows)
**When** a vector search query is executed or an index is requested
**Then** the system selects and builds an HNSW index for the dataset
**And** the index build completes within the defined time budget

**Given** a Lance dataset with row count at or above the configurable threshold (default 1,000,000 rows)
**When** a vector search query is executed or an index is requested
**Then** the system selects and builds an IVF_PQ index for the dataset
**And** the index creation is scheduled when the threshold is crossed (e.g., after an append operation)

**Given** an operator-specified custom threshold value
**When** the threshold is configured to a value other than the default 1M
**Then** the system uses the custom threshold for all subsequent index selection decisions
**And** the threshold is stored as a configurable parameter in the catalog or environment configuration
**And** `tests/unit/test_adaptive_index.py` validates HNSW/IVF_PQ selection logic and threshold crossing

### Story 5.7: Catalog SQL Query & Search Routing

As a data analyst,
I want to query catalog metadata via SQL and use a unified search API that routes to the appropriate search method,
So that I can search across datasets without needing to know which search backend to use.

**Acceptance Criteria:**

**Given** catalog metadata registered via the Catalog Actor
**When** a SQL query targeting catalog metadata tables is executed
**Then** the query is routed through the Catalog Actor and returns accurate metadata results
**And** catalog queries support filtering by dataset name, modality, schema, and custom metadata fields

**Given** a call to the unified search API `lake.search(query, modality, top_k)`
**When** the query parameter is a vector embedding and modality is specified
**Then** the API routes to vector search (HNSW or IVF_PQ) and returns results
**And** the search type is auto-detected based on the shape and content of the query parameters

**Given** a call to the unified search API `lake.search(query, modality, top_k)`
**When** the query parameter is a text string
**Then** the API routes to full-text search and returns BM25-ranked results
**And** when both vector and text query parameters are provided, the API routes to hybrid search with RRF fusion
**And** `tests/integration/test_search_routing.py` validates vector, text, and hybrid routing logic

### Story 5.8: Performance Benchmark Suite

As a system operator,
I want a comprehensive benchmark suite that validates performance non-functional requirements,
So that I can track performance regressions and ensure the system meets its SLA targets.

**Acceptance Criteria:**

**Given** the benchmark suite located in `tests/benchmark/`
**When** the vector search benchmark (NFR-PERF-01) is executed against a dataset with an HNSW index
**Then** the p95 vector search latency is under 10ms
**And** benchmark results are logged as structured JSON including timestamp, dataset size, index type, latency percentiles, and throughput

**Given** the benchmark suite
**When** the lazy evaluation benchmark (NFR-PERF-04) is executed
**Then** lazy evaluation demonstrates at least 100x performance improvement over eager evaluation for filtered queries on large datasets
**And** results are logged as structured JSON for regression tracking

**Given** the benchmark suite
**When** the streaming memory benchmark (NFR-PERF-05) is executed against a 100M+ row dataset
**Then** the memory footprint during streaming iteration remains below 100MB
**And** results are logged as structured JSON with peak memory measurements

**Given** all benchmark scripts in `tests/benchmark/`
**When** the full benchmark suite is executed
**Then** each benchmark produces a structured JSON result file
**And** results can be compared across runs to detect performance regressions

### Story 5.9: Data Export to Standard Formats

As a data engineer,
I want to export Lance table data and query results to standard formats (Parquet, CSV),
So that downstream tools and teams can consume Arrow Lake data without requiring Lance or Arrow-native readers.

**Acceptance Criteria:**

**Given** a Lance table with mixed modality data
**When** I call `lake.export("my_table", format="parquet", path="s3://output/my_table.parquet")`
**Then** the data is exported as a Parquet file preserving schema, data types, and null handling
**And** embedding vector columns are preserved as Parquet LIST<DOUBLE> columns

**Given** a query result from hybrid search
**When** I call `result.export("csv", path="output/search_results.csv")`
**Then** the search results are exported as CSV with score columns included
**And** blob columns (image_data, video_data) are excluded from CSV export with a warning log

**Given** an export operation on a table with 1M rows
**When** the export runs
**Then** it uses Daft's streaming write to avoid loading the entire table into memory
**And** `tests/integration/test_data_export.py` validates Parquet, CSV export and streaming behavior

---

## Epic 6: Pipeline Orchestration & Integration

Maya can define automated data pipelines with Metaflow, featuring three-level self-healing (retry/classify/rollback), scheduled execution, and tag-based run tracking.

**FRs:** FR-ORCH-01, FR-ORCH-02, FR-ORCH-03, FR-ORCH-05a, FR-ORCH-05b, FR-ORCH-05c, FR-ORCH-06, FR-ORCH-07, FR-PROC-08, FR-PROC-09

**Integration Story:** Maya E2E pipeline — 1000 mixed-quality records, 4 steps (ingest -> quality -> embed -> search), < 45 minutes, TTV + /metrics observable.

### Story 6.1: Metaflow FlowSpec Definition

As a pipeline developer,
I want to define batch data pipelines using Metaflow FlowSpec classes with standard decorators,
So that I can create structured, reproducible data processing workflows.

**Acceptance Criteria:**

**Given** a Python file defining a Metaflow FlowSpec subclass
**When** the class is decorated with `@project` for pipeline configuration
**Then** the pipeline is registered with the Arrow Lake project namespace
**And** pipeline configuration (name, description, tags) is stored in the project metadata

**Given** a FlowSpec class with methods decorated with `@step`, `@batch`, and `@card`
**When** `python flow.py run` is executed locally
**Then** each decorated step executes in the defined linear or branching topology
**And** `@batch` steps can specify resource requirements (CPU, memory, GPU)
**And** `@card` steps produce visual artifacts accessible after run completion

**Given** a valid FlowSpec pipeline
**When** the pipeline is executed locally via `python flow.py run`
**Then** all steps complete successfully with input/output artifacts passed between steps
**And** the run status and artifacts are stored in Metaflow's local metadata store
**And** `tests/unit/test_flowspec.py` validates step topology, artifact passing, and local execution

### Story 6.2: Cluster Execution with Ray

As a pipeline developer,
I want to execute Metaflow pipelines on a Ray cluster for distributed processing,
So that I can scale pipeline steps beyond a single machine's resources.

**Acceptance Criteria:**

**Given** a valid FlowSpec pipeline with `@batch` decorated steps
**When** `python flow.py run --with ray` is executed
**Then** the pipeline is submitted to the configured Ray cluster
**And** `@batch` steps are distributed across Ray workers according to resource specifications

**Given** a pipeline with Ray Data integration
**When** the pipeline processes a distributed dataset
**Then** Ray Data handles data partitioning and distribution across cluster nodes
**And** each worker processes its assigned data partition independently

**Given** resource specifications on `@batch` steps (e.g., CPU=4, GPU=1, memory=16GB)
**When** the pipeline runs on Ray
**Then** each step is allocated the specified resources on the cluster
**And** steps wait for resource availability if cluster capacity is insufficient
**And** `tests/integration/test_ray_execution.py` validates distributed step execution and resource allocation

### Story 6.3: Transient Retry with Exponential Backoff

As a pipeline developer,
I want pipeline steps to automatically retry on transient failures with exponential backoff,
So that momentary infrastructure issues do not cause pipeline failures.

**Acceptance Criteria:**

**Given** a pipeline step decorated with `@retry(max_attempts=3, min_backoff=1, max_backoff=60)`
**When** the step fails due to a Spot worker preemption or network error
**Then** the step is retried automatically with exponential backoff starting at `min_backoff` seconds
**And** backoff doubles between each retry attempt (1s, 2s, 4s, ...)
**And** backoff is capped at `max_backoff` seconds
**And** the step is retried up to `max_attempts` times total (initial + retries)

**Given** a step that fails on all retry attempts
**When** the final retry attempt fails
**Then** the step failure is propagated to the error classification handler
**And** the retry history (attempt count, backoff durations, error messages) is logged for debugging

**Given** a step that succeeds on a retry attempt
**When** the step completes successfully after one or more retries
**Then** the pipeline continues to the next step normally
**And** the retry history is recorded in the run metadata
**And** `tests/unit/test_retry.py` validates backoff timing, max attempts, and success-on-retry behavior

### Story 6.4: Error Classification Handler

As a pipeline operator,
I want pipeline errors to be automatically classified into categories,
So that I can distinguish between retryable transient errors and fatal errors requiring intervention.

**Acceptance Criteria:**

**Given** a pipeline step decorated with `@catch` handler
**When** an error occurs during step execution
**Then** the error is classified into one of four categories: TRANSIENT, RESOURCE, VALIDATION, or FATAL
**And** TRANSIENT errors (network timeouts, spot preemptions, temporary S3 503) are flagged for retry with exponential backoff
**And** RESOURCE errors (out-of-memory, disk-full, Ray actor crash) are flagged for retry with resource adjustment (increase memory/replicas)
**And** VALIDATION errors (schema mismatch per Story 4.10 rules, missing required fields, type coercion failure) are flagged as non-retryable
**And** FATAL errors (data corruption, authentication failure, Lance version irrecoverable state) are flagged as non-retryable and trigger rollback (Story 6.5)
**And** ambiguous errors (unclassifiable) default to FATAL with a warning log for manual review

**Given** a classified error event
**When** the error is logged
**Then** the log includes structured error context: error category, original exception type, message, stack trace, step name, and run_id
**And** the error context is queryable via catalog SQL for post-mortem analysis
**And** `tests/unit/test_error_classifier.py` validates all four error categories and logging output

### Story 6.5: State Rollback to Last-Known-Good

As a pipeline operator,
I want datasets to automatically roll back to the last-known-good version on fatal errors,
So that downstream consumers are not exposed to partially-written or corrupted data.

**Acceptance Criteria:**

**Given** a pipeline that writes to a Lance dataset and encounters a FATAL error
**When** the error classification handler determines the error is FATAL
**Then** the Lance dataset is checked out to the last-known-good version
**And** the last-known-good version identifier was stored as a checkpoint in the Metaflow `@catch` handler before the step executed

**Given** a dataset rollback operation
**When** the rollback completes
**Then** the dataset is restored to its state prior to the failed pipeline step
**And** dead-letter tables containing rejected records are preserved
**And** pipeline execution logs are preserved for debugging

**Given** a pipeline that partially succeeded before failing
**When** rollback is triggered
**Then** only the datasets modified by the failed step (and subsequent steps) are rolled back
**And** datasets modified by earlier successful steps remain at their current version
**And** MVP scope supports linear pipeline rollback only; branching pipeline (fan-out/fan-in) rollback is deferred to a future enhancement with explicit per-branch checkpoint semantics
**And** `tests/integration/test_rollback.py` validates checkpoint-restore and partial rollback semantics

### Story 6.6: Scheduled Pipeline Execution

As a pipeline operator,
I want to schedule pipelines for recurring execution at defined intervals,
So that data processing runs automatically without manual triggering.

**Acceptance Criteria:**

**Given** a FlowSpec pipeline decorated with `@schedule(daily="08:00")`
**When** the Metaflow scheduler is active
**Then** the pipeline is executed daily at 08:00 in the configured timezone
**And** execution history is tracked with timestamps and status

**Given** a FlowSpec pipeline decorated with `@schedule(hourly=True)`
**When** the Metaflow scheduler is active
**Then** the pipeline is executed every hour at the top of the hour

**Given** a FlowSpec pipeline decorated with `@schedule(cron="0 2 * * 1-5")`
**When** the Metaflow scheduler is active
**Then** the pipeline is executed at 02:00 Monday through Friday according to the cron expression

**Given** schedule configuration defined in Metaflow Config YAML
**When** the scheduler is started
**Then** schedule definitions are loaded from the YAML configuration
**And** schedule status (active, last_run, next_run, failures) is tracked and queryable via the catalog
**And** `tests/unit/test_scheduler.py` validates daily, hourly, and cron schedule configurations

### Story 6.7: Tag-Based Run Tracking and Resume

As a pipeline operator,
I want to track pipeline runs with auto-generated tags and resume failed runs from checkpoints,
So that I can manage long-running pipelines without restarting from the beginning.

**Acceptance Criteria:**

**Given** a Metaflow pipeline execution
**When** the pipeline starts
**Then** a unique run tag is auto-generated from the Metaflow run_id
**And** the tag is associated with all artifacts, logs, and metadata produced during the run

**Given** a pipeline run that failed at a specific step
**When** `flow.py resume --run-id RUN_ID` is executed
**Then** the pipeline resumes from the checkpoint of the last successfully completed step
**And** intermediate artifacts from the original run are reused
**And** steps after the checkpoint are re-executed

**Given** multiple pipeline runs with tags
**When** a run history query is executed via catalog SQL
**Then** all runs are listed with their tags, status, start time, end time, and step-level status
**And** results can be filtered by tag, status, or time range
**And** `tests/unit/test_run_tracking.py` validates tag generation, resume, and history query

### Story 6.8: Distributed Processing via Ray

As a pipeline developer,
I want to use Ray's foreach API for parallel processing across a Ray cluster,
So that I can scale data processing workloads horizontally with fault tolerance.

**Acceptance Criteria:**

**Given** a dataset partitioned across a Ray cluster
**When** a pipeline step uses the Ray foreach API to apply a processing function
**Then** the function is executed in parallel across all available Ray workers
**And** each worker processes its assigned data partition independently
**And** results are collected and merged into a unified output dataset

**Given** AutoScale configuration specifying min_workers=2 and max_workers=10
**When** the processing workload increases
**Then** Ray automatically scales up workers up to the max_workers limit
**And** when the workload decreases, Ray scales down to min_workers
**And** GPU workers are included in the AutoScale configuration when GPU processing is required

**Given** a Ray worker failure during distributed processing
**When** a worker crashes or becomes unresponsive
**Then** the failed worker's tasks are automatically rescheduled on other available workers
**And** processing continues without manual intervention
**And** `tests/integration/test_ray_foreach.py` validates parallel execution, autoscaling, and fault tolerance

### Story 6.9: Remote Data Loader Pattern

As a pipeline developer,
I want CPU workers to preprocess data and transfer it zero-copy to GPU workers for training,
So that GPU utilization is maximized by eliminating CPU preprocessing bottlenecks.

**Acceptance Criteria:**

**Given** a pipeline with both CPU and GPU worker pools
**When** data is loaded for processing
**Then** CPU workers perform decoding and transformation on data batches
**And** preprocessed batches are placed in the Ray Object Store with zero-copy transfer semantics
**And** GPU workers read preprocessed batches directly from the Ray Object Store for training

**Given** a training workload with variable CPU preprocessing speed
**When** GPU workers consume data faster than CPU workers can preprocess
**Then** the prefetch queue depth ensures GPU workers are not starved
**And** the prefetch queue depth is a configurable parameter (default: 2 batches ahead)

**Given** the remote data loader pipeline
**When** processing throughput is measured
**Then** GPU utilization remains above 80% during sustained training
**And** CPU preprocessing does not become the bottleneck for GPU throughput
**And** `tests/integration/test_remote_dataloader.py` validates zero-copy transfer and prefetch behavior
**And** PyTorch DataLoader integration validates `pin_memory=True` and `non_blocking=True` transfer: a DataLoader consuming Arrow RecordBatches from Ray Object Store feeds a GPU tensor without CPU serialization bottleneck (NFR-PERF-06)

### Story 6.10: Maya E2E Pipeline Integration

As Maya (the product owner),
I want to run a full end-to-end pipeline processing 1000 real-world mixed-quality records through ingest, quality filtering, embedding, and search,
So that I can validate the entire platform works as an integrated system within the target time budget.

**Acceptance Criteria:**

**Given** 1000 real-world records with mixed quality (noisy text, low-resolution images, missing fields)
**When** the 4-step pipeline is executed: ingest -> quality filter -> embed -> search
**Then** all records are ingested into the Lance dataset
**And** low-quality records are routed to the dead-letter table with rejection reasons
**And** remaining records are embedded and stored with their vector representations
**And** a search query against the embedded records returns relevant results

**Given** the 4-step pipeline executing with 1000 mixed-quality records
**When** pipeline execution completes
**Then** total execution time is under 45 minutes
**And** execution is measurable via TTV (time-to-value) verification

**Given** the pipeline is running
**When** the `/metrics` endpoint is queried during execution
**Then** observable metrics are returned including: records processed, step durations, error counts, dead-letter count, and throughput
**And** metrics are updated in real-time as the pipeline progresses

**Given** records rejected by the quality filter
**When** the pipeline completes
**Then** the dead-letter table is populated with rejected records and their rejection reasons
**And** the dead-letter table is queryable via the catalog SQL API

### Story 6.11: Catalog Read Replica for High Availability

As a platform operator,
I want a Catalog read replica that can be started from the DuckDB data file when the primary Ray Named Actor is unavailable,
So that query and metadata operations continue to function during Ray GCS failures or Catalog Actor restarts.

**Acceptance Criteria:**

**Given** the primary CatalogActor is running normally
**When** a read-only query is executed via `lake.catalog.query_metadata(...)`
**Then** the query is served by the primary CatalogActor as usual

**Given** the primary CatalogActor is unavailable (crashed or Ray GCS failure)
**When** a read-only query is executed
**Then** a read-only Catalog replica is automatically started from the DuckDB data file
**And** the replica serves `list_datasets()`, `get_dataset()`, and `query_metadata()` operations
**And** write operations (`register`, schema changes) return `ErrorCode.CATALOG_WRITE_UNAVAILABLE` with a clear message

**Given** the primary CatalogActor recovers (via Ray max_restarts)
**When** the next read query is executed
**Then** the primary actor resumes serving all operations
**And** `tests/integration/test_catalog_read_replica.py` validates failover and recovery behavior

### Story 6.12: Lightweight Production Deployment Package

As a platform engineer (Sam),
I want a simplified deployment package that wraps docker-compose with production-ready defaults and health checks,
So that I can validate the deployment path to production environments without the full K8s/Helm complexity.

**Acceptance Criteria:**

**Given** the Arrow Lake project with all Core Epics (1-5) implemented
**When** I run `docker compose -f docker-compose.prod.yml up -d`
**Then** the platform starts with production-tuned defaults: structured logging to stdout, metrics enabled, health check endpoint at `/health`
**And** the health check returns `{"status": "ok", "catalog": "available", "storage": "accessible"}` with HTTP 200

**Given** the production docker-compose is running
**When** I run `curl http://localhost:8000/health`
**Then** the response indicates all components are healthy within 5 seconds of startup
**And** `docker compose logs` shows structured JSON logs with correlation IDs

**Given** a `.env.production` file with production S3 endpoint and credentials
**When** the production compose starts
**Then** configuration is loaded from `.env.production` with 4-layer override (code defaults → .env → env vars → Metaflow YAML)
**And** `tests/integration/test_prod_compose.py` validates health check, metrics endpoint, and production logging against the production compose file

---

## Epic 7: Production & Observability

Sam can deploy to K8s via Helm, leverage elastic GPU burst scaling, monitor via Prometheus/Grafana dashboards, and manage the platform via CLI.

**FRs:** FR-DEV-02, FR-DEV-07, FR-ORCH-04, FR-ORCH-08, FR-PROC-03, FR-STOR-07, FR-OBS-01, FR-OBS-02, FR-OBS-03, FR-OBS-04, FR-OBS-05, FR-OBS-06

**MVP:** Production (month 3-4: deploy+observability, month 4-6: scale+security)

### Story 7.1: Jupyter Notebook Integration

As a data scientist,
I want a pre-configured Jupyter environment with arrow_lake, ray, and daft imports available out of the box,
So that I can start exploring and querying datasets immediately without manual environment setup.

**Acceptance Criteria:**

**Given** a running Docker Compose environment with the Jupyter service started via `docker compose up -d`
**When** I open `http://localhost:8888` in a browser
**Then** Jupyter Lab launches with a Python kernel that has `arrow_lake`, `ray`, `daft`, `lancedb`, `duckdb`, and `pyarrow` pre-installed and importable
**And** the kernel auto-restarts after a `!pip install` command to pick up newly installed packages
**And** the `docs/examples/` directory contains at minimum `quickstart.ipynb` and `hybrid_search.ipynb` as runnable notebooks
**And** `quickstart.ipynb` demonstrates creating a dataset, ingesting sample data, and performing a basic vector search
**And** `hybrid_search.ipynb` demonstrates hybrid vector + full-text search with configurable alpha weight
**And** all example notebooks execute end-to-end without errors against the local environment
**And** `tests/integration/test_jupyter.py` validates kernel startup, Lance connectivity, and sample notebook execution via nbconvert

### Story 7.2: CLI for Common Operations

As a developer or data engineer,
I want a command-line interface for common Arrow Lake operations including ingest, search, status, and version,
So that I can interact with the platform quickly without writing Python scripts for routine tasks.

**Acceptance Criteria:**

**Given** the `arrow_lake` package is installed in the current environment
**When** I run `arrow-lake --help`
**Then** the CLI displays colored output with subcommands: `ingest`, `search`, `status`, `version`
**And** `arrow-lake version` prints the installed version, Python version, and core dependency versions (Daft, Ray, Metaflow, Lance) in a formatted table
**And** `arrow-lake ingest --source s3://my-bucket/data --table my_data --modality text` ingests files from the source into a Lance table named `my_data`
**And** `arrow-lake search --query "autonomous driving" --modality image --top-k 10` returns the top 10 image results with scores in a formatted table
**And** `arrow-lake search --query "machine learning" --modality text --top-k 5 --alpha 0.7` performs hybrid search with the specified alpha weight
**And** `arrow-lake status` lists all registered datasets with row counts, column schemas, and last update timestamps
**And** error messages are displayed with clear colored formatting (red for errors, yellow for warnings, green for success)
**And** `tests/unit/test_cli.py` validates all subcommands using click.testing.CliRunner with a temporary test dataset

### Story 7.3: Argo Workflows Basic Deployment

As a DevOps engineer,
I want to deploy Metaflow pipelines as Argo Workflows on Kubernetes via `python flow.py --with ray argo-workflows create`,
So that batch pipelines run reliably in production with native K8s orchestration and artifact management.

**Acceptance Criteria:**

**Given** a Metaflow FlowSpec defined in `flows/` with Ray integration configured
**When** I run `python flow.py --with ray argo-workflows create`
**Then** Argo generates a Workflow YAML manifest with RayJob templates for each step
**And** the Workflow YAML includes a Ray head service and configurable worker replicas
**And** artifact passing between steps uses Argo artifact volumes (S3-backed) for model and data outputs
**And** the generated YAML passes `kubectl apply --dry-run=client` validation
**And** the Workflow includes resource requests and limits matching the Ray worker configuration
**And** `tests/integration/test_argo_deploy.py` validates YAML generation and dry-run against a test FlowSpec

### Story 7.4: CronWorkflow Scheduling and Advanced Argo Features

As a DevOps engineer,
I want to schedule pipelines as CronWorkflows and manage artifact volumes with lifecycle policies,
So that batch pipelines run on automated schedules with proper resource lifecycle management.

**Acceptance Criteria:**

**Given** a Metaflow FlowSpec with `@schedule(cron="0 2 * * *")` decorator
**When** I run `python flow.py --with ray argo-workflows create --with cron`
**Then** a CronWorkflow YAML is generated that schedules the pipeline daily at 2 AM
**And** the CronWorkflow inherits the same RayJob templates and artifact volumes as the base Workflow

**Given** a CronWorkflow with artifact volumes configured
**When** the workflow runs over multiple days
**Then** artifact volumes have configurable retention policies (e.g. `ARROW_LAKE__ARGO_ARTIFACT_RETENTION_DAYS=30`)
**And** expired artifacts are cleaned up automatically to prevent unbounded storage growth

**Given** a workflow step that requires access to external secrets (e.g. S3 credentials, API keys)
**When** the Workflow YAML is generated
**Then** K8s Secret references are injected into the RayJob template from Metaflow Config YAML
**And** `tests/unit/test_argo_cron.py` validates CronWorkflow generation, artifact retention, and secret injection

### Story 7.5: Elastic GPU Burst Scaling

As a platform operator,
I want the system to automatically scale GPU worker pods from 0 to 8 based on task queue depth and scale back to 0 on idle,
So that GPU compute costs are minimized during idle periods while burst workloads are handled within SLA.

**Acceptance Criteria:**

**Given** a Ray cluster deployed on Kubernetes with GPU node pool configured and auto-scaling enabled
**When** a batch of 100 embedding tasks is submitted to the task queue
**Then** Ray auto-scaler provisions GPU worker pods incrementally until the queue depth is resolved (up to 8 workers)
**And** scale-up from 0 to 8 GPU workers completes in under 5 minutes (NFR-SCALE-05)
**And** spot GPU instances are preferred when available, with spot utilization exceeding 70% of total GPU hours (NFR-COST-03)
**And** on-demand GPU instances are used as fallback when spot capacity is unavailable
**And** when the task queue is empty for the configured idle timeout, workers scale down to 0
**And** fractional GPU scaling is supported: workers can request 0.5 GPU increments (NFR-SCALE-04)
**And** scaling events are logged as structured JSON with `event_type`, `target_replicas`, `current_replicas`, and `timestamp`
**And** `tests/integration/test_elastic_burst.py` validates scale-up/scale-down timing with mock Ray autoscaler

### Story 7.6: SQL Query Support

As a data analyst,
I want to query Arrow Lake datasets using standard SQL through both Daft SQL and DuckDB interfaces,
So that I can perform ad-hoc analysis without learning a domain-specific API.

**Acceptance Criteria:**

**Given** a registered Lance dataset with columns including text, image metadata, embedding vectors, and quality scores
**When** I execute `df.sql("SELECT * FROM my_table WHERE modality = 'image' AND quality_score > 0.8")` via the Daft SQL interface
**Then** the query returns matching rows as a Daft DataFrame convertible to Arrow format
**And** predicate pushdown is applied so that Lance scans only relevant fragments
**And** I can execute complex OLAP queries via Daft SQL (primary) with DuckDB available for catalog SQL: `SELECT modality, COUNT(*) as cnt, AVG(quality_score) FROM my_table GROUP BY modality HAVING cnt > 100`
**And** DuckDB queries leverage Lance predicate pushdown for filtering operations
**And** SQL query results are convertible to Arrow RecordBatches via `query.to_arrow()`
**And** JOIN operations between two registered Lance tables work correctly in DuckDB
**And** `tests/integration/test_sql_query.py` validates both DuckDB and Daft SQL engines against a test Lance dataset

### Story 7.7: Auto-Tiered Blob Lifecycle

As a platform operator,
I want S3 Lifecycle rules to automatically transition blob data from Standard to Infrequent Access to Glacier storage based on configurable age thresholds,
So that storage costs are reduced for older data that is accessed infrequently.

**Acceptance Criteria:**

**Given** a Lance dataset stored on S3 with multi-fidelity blob storage (thumbnail + preview + original)
**When** I configure lifecycle rules with `standard_days=30`, `ia_days=90`, `glacier_days=365`
**Then** S3 Lifecycle rules are applied: objects transition from Standard to IA after 30 days, IA to Glacier after 90 days, and remain in Glacier after 365 days
**And** lifecycle rules are configurable per dataset via the catalog metadata API
**And** thumbnail and preview tiers are excluded from Glacier transition (they remain in Standard for fast access)
**And** only the original fidelity blobs are subject to lifecycle transitions
**And** estimated storage cost reduction exceeds 50% for a 100TB dataset compared to all-Standard storage (NFR-COST-02)
**And** accessing a Glacier-tiered object triggers a restore request with configurable expedited/standard/bulk retrieval
**And** `tests/unit/test_blob_lifecycle.py` validates lifecycle rule generation and cost estimation

### Story 7.8: Prometheus Metrics Endpoint

As a platform operator,
I want a Prometheus `/metrics` HTTP endpoint exposing all platform metrics with configurable port/path and disable support,
So that Prometheus can scrape system, ingestion, processing, and query metrics for observability.

**Acceptance Criteria:**

**Given** the Arrow Lake platform is running with observability enabled
**When** I send an HTTP GET request to the configured metrics endpoint (default `http://localhost:8000/metrics`)
**Then** the response is in Prometheus exposition format with all feature-epic metrics (introduced incrementally per Epic) following the naming pattern `arrow_lake_{domain}_{metric}_{unit}`
**And** ingestion metrics are present: `arrow_lake_ingestion_rows_total`, `arrow_lake_ingestion_bytes_total`, `arrow_lake_ingestion_duration_seconds`, `arrow_lake_ingestion_errors_total` (FR-OBS-02, introduced in Epic 3)
**And** processing metrics are present: `arrow_lake_processing_embeddings_total`, `arrow_lake_processing_quality_rejects_total`, `arrow_lake_processing_active_tasks` (FR-OBS-03, introduced in Epic 4)
**And** query metrics are present: `arrow_lake_query_total`, `arrow_lake_query_latency_seconds`, `arrow_lake_query_results_total` with `query_type` label (FR-OBS-04, introduced in Epic 5)
**And** system metrics are present: `arrow_lake_system_ray_actors`, `arrow_lake_system_tables`, `arrow_lake_system_uptime_seconds` (FR-OBS-05, introduced in Epic 6)
**And** the metrics port is configurable via `ARROW_LAKE__METRICS_PORT` and the path via `ARROW_LAKE__METRICS_PATH` (FR-OBS-06)
**And** setting `ARROW_LAKE__METRICS_ENABLED=false` disables the metrics endpoint entirely
**And** `tests/unit/test_metrics_endpoint.py` validates port/path configuration, enabled/disabled toggle, and response format

### Story 7.9: Grafana Dashboards

As a platform operator,
I want pre-built Grafana dashboard templates for ingestion, processing, query performance, and system overview,
So that I can monitor platform health in real-time without building dashboards from scratch.

**Acceptance Criteria:**

**Given** Prometheus is configured to scrape the Arrow Lake `/metrics` endpoint
**When** I import the Grafana dashboard JSON templates from `deploy/grafana/`
**Then** the Ingestion Pipeline dashboard shows: rows/second, bytes/second, error rate, and per-table breakdown
**And** the Processing Pipeline dashboard shows: active tasks, embedding throughput, quality rejection rate
**And** the Query Performance dashboard shows: query count by type (vector/text/hybrid/SQL), p50/p95/p99 latency
**And** the System Overview dashboard shows: Ray actor count, registered tables, uptime, resource utilization
**And** each dashboard includes panels with appropriate time ranges and alerting thresholds
**And** dashboards auto-refresh at configurable intervals (default 30 seconds)
**And** `deploy/grafana/` contains: `ingestion-dashboard.json`, `processing-dashboard.json`, `query-dashboard.json`, `system-dashboard.json`

### Story 7.10: K8s Helm Chart Deployment

As a DevOps engineer,
I want a production-ready Helm Chart that deploys Arrow Lake on Kubernetes using the official Ray Helm chart with custom values,
So that I can deploy, upgrade, and rollback the platform with standard Helm workflows.

**Acceptance Criteria:**

**Given** a Kubernetes cluster with Helm 3 installed
**When** I run `helm install arrow-lake deploy/helm/arrow-lake -f deploy/helm/arrow-lake/values.yaml`
**Then** the chart deploys Ray head service, configurable Ray worker replicas, and associated resources using the official Ray Helm chart as a dependency
**And** `deployment.yaml` template creates the Arrow Lake API server deployment with configurable replicas and resource limits
**And** `service.yaml` template exposes the API server, Ray Dashboard, and metrics endpoints as K8s Services
**And** `networkpolicy.yaml` template defines network policies restricting inter-service communication (disabled by default in `values.yaml`)
**And** `prometheusrule.yaml` template configures Prometheus alerting rules for critical metrics
**And** `values.yaml` provides production defaults and `values-dev.yaml` overrides for development
**And** `helm upgrade arrow-lake` performs a rolling update without downtime
**And** `helm rollback arrow-lake 1` reverts to the previous release successfully
**And** the chart passes `helm lint` and `helm template` dry-run validation

### Story 7.11: Docker Network Isolation and Security

As a platform operator,
I want Docker network isolation, TLS encryption, encrypted storage, and restricted metrics access,
So that the platform meets security requirements for development and production environments.

**Acceptance Criteria:**

**Given** the Docker Compose configuration for local development
**When** I run `docker compose up -d`
**Then** all services communicate over a dedicated Docker bridge network isolated from the host network (AR-22)
**And** only the following ports are exposed to the host: 8000 (metrics), 8265 (Ray Dashboard), 9000 (MinIO), 8888 (Jupyter)
**And** self-signed TLS certificates are generated and configured for Docker Compose services (AR-23)
**And** AWS GP3 EBS volumes with encryption-at-rest enabled are configured for persistent storage in production (AR-24)
**And** Prometheus service discovery is configured to scrape `/metrics` only from within the internal Docker network (AR-25)
**And** a direct HTTP request to the metrics endpoint from outside the Docker network is refused
**And** `tests/unit/test_security_config.py` validates network isolation, TLS config, and metrics access control

### Story 7.12: Single-Node Scalability and Concurrent Query Testing

As a performance engineer,
I want automated load test scripts that validate single-node data volume and concurrent query throughput (NFR-SCALE-01, NFR-SCALE-03),
So that I can confirm the platform meets baseline scalability requirements on a single machine before investing in distributed infrastructure.

**Acceptance Criteria:**

**Given** a deployed Arrow Lake instance (single node, CPU or GPU)
**When** I run `pytest tests/benchmark/test_scale_single_node.py`
**Then** single-node test ingests 10M rows and verifies query retrieval latency meets NFR-SCALE-01 (< 10ms vector search at 10M rows)
**And** concurrent query test sustains 100 QPS and measures P50/P95/P99 latency (NFR-SCALE-03)
**And** all benchmark results are logged as structured JSON in `tests/benchmark/results/`
**And** results include: timestamp, cluster config, data volume, throughput, latency percentiles
**And** `tests/benchmark/test_scale_single_node.py` can run in CI (no K8s or multi-node required)

### Story 7.13: Distributed Scalability and GPU Burst Testing

As a performance engineer,
I want distributed scalability and GPU burst load tests that validate NFR-SCALE-02, NFR-SCALE-04, and NFR-SCALE-05,
So that I can confirm the platform meets production-scale requirements on a K8s cluster with GPU support.

**Acceptance Criteria:**

**Given** a K8s cluster with Arrow Lake deployed and GPU node pool configured
**When** I run `pytest tests/benchmark/test_scale_distributed.py`
**Then** distributed test validates data volume support up to 1B rows across multiple nodes (NFR-SCALE-02)
**And** fractional GPU test provisions workers with 0.5 GPU increments and verifies correct allocation (NFR-SCALE-04)
**And** elastic burst test triggers scale-up from 0 to 8 GPU workers under 5 minutes (NFR-SCALE-05)
**And** fractional GPU scaling requires NVIDIA MIG support; if MIG is unavailable, the test falls back to integer GPU allocation with a warning
**And** these tests are excluded from CI and run only on dedicated K8s test infrastructure (marked with `@pytest.mark.distributed_gpu`)

### Story 7.14: CI/CD Pipeline

As a developer,
I want automated CI checks on every pull request, nightly GPU test runs, and tag-triggered release workflows,
So that code quality is enforced consistently and releases are produced reliably.

**Acceptance Criteria:**

**Given** a GitHub repository with the Arrow Lake codebase
**When** I open a pull request against the main branch
**Then** GitHub Actions CI workflow runs: `ruff check .` (lint), `mypy arrow_lake/` (type check), and `pytest tests/unit/ tests/integration/` (CPU-only tests) (AR-47)
**And** the CI workflow fails if any check does not pass, blocking the PR merge
**And** a nightly workflow triggers GPU tests at a scheduled time using `schedule: cron` (AR-48)
**And** the nightly GPU test workflow can also be triggered manually via `workflow_dispatch`
**And** when a git tag matching `v*` pattern is pushed, the release workflow builds and publishes artifacts (AR-49)
**And** the release workflow publishes the Python package to the configured registry
**And** the release workflow generates a changelog from conventional commit messages
**And** `tests/unit/test_ci_workflows.py` validates all three workflow YAML files parse correctly and contain required job steps

---

## Epic 8: Advanced Features

Power users can perform faceted search, multi-model ensemble search, data lineage tracing, event sourcing audit, and NeMo Curator GPU-accelerated quality scoring.

**FRs:** FR-QRY-06, FR-QRY-08, FR-CAT-05, FR-ORCH-09, FR-PROC-04

**MVP:** Scale (month 6-12)

### Story 8.1: Faceted Search with DuckDB CUBE

As a data analyst,
I want to perform multi-dimensional faceted navigation alongside vector search,
So that I can narrow down search results by metadata facets such as modality, date range, quality score, and source while maintaining relevance ranking.

**Acceptance Criteria:**

**Given** a registered Lance dataset with metadata columns: `modality`, `source`, `quality_score`, `created_at`, and vector embedding columns
**When** I execute a faceted search query with filters: `modality='image'`, `quality_score > 0.7`, and a vector query embedding
**Then** DuckDB CUBE computes facet counts for all dimension combinations from the filtered dataset
**And** facet counts are returned alongside the vector search results in a single response object
**And** the facet response includes: facet name, facet value, count, and optional sub-facet breakdown
**And** applying a facet filter re-executes the query with the additional filter without recomputing all facet counts
**And** vector search results are correctly intersected with the faceted filter criteria, maintaining relevance ranking
**And** the faceted search API is accessible via `lake.search(query_vector, facets=["modality", "source", "quality_tier"])`
**And** `tests/integration/test_faceted_search.py` validates facet count correctness against a test dataset with known data

### Story 8.2: Multi-Model Ensemble Search

As a machine learning engineer,
I want to combine search results from multiple embedding models with configurable score fusion,
So that I can leverage complementary strengths of different embedding models for improved retrieval quality.

**Acceptance Criteria:**

**Given** a Lance dataset with multiple embedding columns: `emb_text_768` (text encoder) and `emb_clip_512` (CLIP vision-language encoder)
**When** I execute an ensemble search with a text query against both embedding columns
**Then** the system performs vector search independently against each embedding column using the appropriate index
**And** results from both searches are merged using the configured fusion strategy
**And** score fusion supports three modes: `average` (mean of normalized scores), `max` (best score per result), `weighted` (configurable weights per model)
**And** weighted fusion uses configurable weights per model: `ensemble_weights={"emb_text_768": 0.6, "emb_clip_512": 0.4}`
**And** fused results are deduplicated by row ID and re-ranked by the fused score
**And** the top-k results are returned with individual per-model scores and the fused score
**And** `tests/integration/test_ensemble_search.py` validates RRF scoring correctness, deduplication, and result ordering

### Story 8.3: Data Lineage via SQL

As a data governance officer,
I want to query the complete data lineage of any dataset via SQL,
So that I can trace where data came from, what transformations were applied, and which pipelines produced each dataset version.

**Acceptance Criteria:**

**Given** multiple datasets have been created through ingestion and transformation pipelines, each with Lance version history
**When** I query the lineage table: `SELECT * FROM lineage WHERE output_table = 'processed_images' ORDER BY timestamp DESC`
**Then** the result includes: source dataset name, transformation type (ingest/embed/quality/filter), output dataset name, output version, pipeline run_id, operator identity, and timestamp
**And** the lineage data is derived from the Lance event log and stored in a SQL-queryable format in the catalog
**And** lineage queries support filtering by: `output_table`, `source_table`, `transform_type`, `run_id`, `timestamp range`
**And** lineage queries support JOIN with the catalog metadata to enrich results with schema and statistics information
**And** the lineage trail is complete: any output dataset can be traced back through all intermediate steps to the original source
**And** Metaflow `run_id` is stored alongside lineage records for pipeline-level correlation
**And** lineage data is immutable: records are append-only and never modified or deleted
**And** `tests/integration/test_data_lineage.py` creates a multi-version test dataset and verifies lineage SQL queries

### Story 8.4: Event Sourcing Audit Trail

As a compliance auditor,
I want an immutable audit trail that records every data mutation with full provenance,
So that I can verify data integrity, reconstruct any historical state, and meet regulatory audit requirements.

**Acceptance Criteria:**

**Given** the Arrow Lake platform is running with audit trail enabled
**When** any data mutation occurs (ingest, update, delete, schema change, quality filter, embedding compute)
**Then** an immutable audit event is recorded containing: event_id (UUID), timestamp, operator identity, action type, affected table, affected version (before/after), and pipeline run_id
**And** the audit trail is implemented as Lance version changelog + Metaflow tag = combined immutable event source (FR-ORCH-09)
**And** each audit event is append-only and cannot be modified or deleted after creation
**And** the complete history of any table can be reconstructed by replaying audit events in timestamp order
**And** audit events are queryable via SQL: `SELECT * FROM audit_log WHERE table_name = 'my_data' AND action = 'ingest' ORDER BY timestamp`
**And** each audit event includes an HMAC-SHA256 signature (using a server-side secret key) covering the event payload + previous event's HMAC — providing tamper detection without the complexity of a full hash chain
**And** `tests/integration/test_event_sourcing.py` creates a test flow and verifies audit event creation, append-only immutability, and HMAC chain integrity

### Story 8.5: NeMo Curator GPU Quality Scoring Pipeline

As a data engineer preparing training data at scale,
I want GPU-accelerated deduplication and quality scoring powered by NeMo Curator,
So that I can process millions of samples rapidly with classifier-based quality filters for content quality and semantic deduplication.

**Acceptance Criteria:**

**Given** a Lance dataset with image and text columns requiring quality scoring
**When** I run the NeMo Curator quality pipeline with GPU acceleration enabled
**Then** GPU-accelerated exact deduplication identifies and removes duplicate samples using MinHash and LSH
**And** classifier-based quality scoring runs on GPU: content detection, aesthetic quality scoring, and text quality classification
**And** quality scores are written as new Lance columns: `quality_nsfw_score`, `quality_aesthetic_score`, `quality_text_score`, and an aggregated `quality_composite_score`
**And** the cuDF to Arrow bridge performs data transfer without CPU serialization bottleneck
**And** the pipeline processes at least 5x faster on GPU compared to CPU-only baseline for a 100K sample dataset
**And** when GPU is unavailable, the pipeline automatically falls back to CPU-based quality scoring with basic heuristics
**And** the CPU fallback produces compatible quality score columns with the same schema, allowing transparent switching
**And** quality scoring results integrate with the existing QualityFilter system (FR-QUA-01) for downstream filtering
**And** rejected samples (below threshold) are routed to the dead-letter table per FR-QUA-03
**And** `tests/integration/test_nemo_curator.py` validates dedup correctness, classification threshold behavior, and CPU fallback with mocked GPU
