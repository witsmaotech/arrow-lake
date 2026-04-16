---
stepsCompleted:
  - step-01-init
  - step-02-discovery
  - step-02b-vision
  - step-02c-executive-summary
  - step-03-success
  - step-04-journeys
  - step-05-domain
  - step-06-innovation
  - step-07-project-type
  - step-08-scoping
  - step-09-functional
  - step-10-nonfunctional
  - step-11-polish
  - step-12-complete
inputDocuments:
  - _bmad-output/brainstorming/brainstorming-session-2026-04-10-1500.md
  - _bmad-output/brainstorming/appendix-deep-dives.md
  - docs/superpowers/specs/2026-04-10-multimodal-lakehouse-design.md (git HEAD)
workflowType: 'prd'
project_name: 'wits-infra-dintellihub'
user_name: 'Witshine'
date: '2026-04-11'
documentCounts:
  briefs: 0
  research: 0
  brainstorming: 2
  projectDocs: 1
classification:
  type: greenfield
  domain: scientific_ml_platform
  complexity: medium
---

# Product Requirements Document — Arrow Lake

**Unified Multimodal Data Lakehouse Platform**

**Author:** Witshine
**Date:** 2026-04-11
**Status:** Draft v1.0

---

## Executive Summary

Arrow Lake is a **greenfield unified multimodal data lakehouse platform** built on the **DARMU stack** (Daft + Argo + Ray + Metaflow + uv) with an extension layer of Lance (storage), NeMo Curator (quality), and DuckDB (catalog metadata). Daft SQL serves as the primary OLAP engine for analytical queries. It provides end-to-end infrastructure from data ingestion to retrieval, designed for AI/ML teams working with text, images, video, audio, and structured data.

**The core differentiator is the Arrow zero-copy full stack**: Lance → Daft → PyTorch with no serialization overhead at any layer, achieving ~4x end-to-end speedup over traditional pipelines. Combined with embedding-first ingestion, cross-modality unified tables, and a bimodal query engine (OLAP via Daft SQL + vector + full-text in a single SQL), Arrow Lake eliminates the data silos that plague current multimodal ML platforms.

**Key business outcomes:**
- 90% cost reduction via elastic burst processing (spot GPU + auto-scale vs always-on cluster)
- 100x query speedup at 1% selectivity through 5-level lazy evaluation
- Zero code changes from laptop development to production K8s deployment
- 56% storage cost reduction via auto-tiered blob lifecycle management

---

## 1. Vision and Goals

### 1.1 Product Vision

Build the foundational data infrastructure that makes multimodal AI/ML development as straightforward as tabular data work. A platform where ingesting 100GB of mixed modalities, computing embeddings, running quality scoring, and performing hybrid semantic search requires **one command** — not a team of data engineers.

### 1.2 Guiding Principles

| # | Principle | Description |
|---|-----------|-------------|
| 1 | Arrow-Native Zero-Copy | Every layer speaks Apache Arrow — no serialization, no copying |
| 2 | Cross-Modality Unified | One table for all modalities, eliminating data silos |
| 3 | Embedding-First | Embeddings are first-class citizens, computed on ingest, not afterthought |
| 4 | Progressive Complexity | Simple things simple (1 function call), complex things possible (full K8s) |
| 5 | Self-Healing by Default | Workflows recover from transient failures without human intervention |

### 1.3 Out of Scope (v1)

- Custom UI/visualization dashboard (CLI + notebook-first)
- Real-time streaming ingestion (batch-first, streaming in v2)
- Multi-user RBAC/auth system (single-team deployment, multi-tenant isolation in v2)
- Cloud provider-specific integrations beyond S3/MinIO
- Model training framework (platform provides data, not training loops)

---

## 2. Success Metrics

### 2.1 Quantitative KPIs

| Metric | MVP Target | Production Target |
|--------|-----------|-------------------|
| Time to first query | < 5 minutes (local) | < 10 minutes (cluster) |
| Ingestion throughput (text, 10M rows) | > 50K rows/sec | > 200K rows/sec (distributed) |
| Vector search latency (10M rows) | < 10ms (HNSW) | < 5ms (IVF_PQ with prefilter) |
| Zero-copy chain utilization | > 90% Arrow-native | > 95% Arrow-native |
| Workflow recovery rate (no human) | > 90% | > 95% |
| Storage cost vs naive Parquet | < 80% (multi-fidelity) | < 60% (auto-tiered + compressed) |
| Developer onboarding time | < 30 minutes | < 15 minutes |

### 2.2 Qualitative Indicators

- A data scientist can go from raw data to hybrid search results in a single Jupyter notebook
- Switching from local development to K8s cluster requires zero code changes
- Embedding model hot-swap completes without data rewrite or downtime
- Quality gate failures automatically rollback to last known-good Lance version

---

## 3. User Personas and Journeys

### 3.1 Primary Personas

**Persona A: ML Data Engineer (Maya)**
- Manages data pipelines from ingestion to model-ready datasets
- Needs: reliable batch processing, quality scoring, version control, cost visibility
- Pain points: separate systems for ETL, quality, vector DB, catalog; data silos between modalities
- Arrow Lake value: unified pipeline (Metaflow), embedding-first ingestion, Lance versioning

**Persona B: Applied ML Scientist (Raj)**
- Experiments with embeddings, retrieval-augmented generation, and multimodal models
- Needs: fast iteration, flexible queries, GPU access, reproducible experiments
- Pain points: slow data loading, GPU starvation from CPU preprocessing, no cross-modal search
- Arrow Lake value: zero-copy PyTorch DataLoader, remote data loader (CPU→GPU), hybrid search

**Persona C: Platform Engineer (Sam)**
- Deploys and operates the platform for the team
- Needs: simple deployment, auto-scaling, cost control, observability
- Pain points: complex K8s setup, unpredictable GPU costs, manual scaling
- Arrow Lake value: Docker Compose one-command start, elastic burst ($440/mo vs $4,286/mo), self-healing

### 3.2 Key User Journeys

**Journey 1: Ingest and Search (Maya — First-time Setup)**
1. `docker compose up -d` — platform starts (MinIO + Ray + Jupyter)
2. Write ingestion Metaflow flow in notebook
3. `python flow.py run` — ingests data, computes embeddings, builds vector index
4. Run hybrid search: `lake.search("autonomous driving safety", modality="image", top_k=10)`
5. Total time: ~30 minutes from zero to results

**Journey 2: Scale to Production (Sam — Deployment)**
1. Same Metaflow flow, no code changes
2. `python flow.py --with ray argo-workflows create` — deploys to K8s
3. KubeRay auto-scales GPU workers on burst, scales back after
4. Monitor via Ray Dashboard + Prometheus metrics
5. Cost: $440/mo with elastic burst vs $4,286/mo always-on

**Journey 3: Model Iteration (Raj — Experiment Cycle)**
1. Add new embedding column with different model (Lance zero-cost add_column)
2. Build index on new column while old column remains queryable
3. Compare model quality via version diff: Daft SQL FULL OUTER JOIN across versions (via SDK `.version_diff()` API)
4. Promote best version: `lance.create_tag("production")`
5. No data rewrite, no downtime

---

## 4. Domain Model

### 4.1 Core Concepts

```
┌─────────────────────────────────────────────────────────┐
│  Dataset                                                │
│  ├── uri: s3://lake/namespace/dataset.lance             │
│  ├── version: int (auto-incremented on write)            │
│  ├── tags: [string] (named snapshots)                    │
│  ├── schema: Arrow Schema                               │
│  └── indices: [VectorIndex, FTSIndex]                    │
│                                                         │
│  Catalog                                                │
│  ├── datasets: [Dataset]                                │
│  ├── metadata: {name → DatasetInfo}                     │
│  └── singleton: Ray Actor (DuckDB)                      │
│                                                         │
│  Pipeline (Metaflow FlowSpec)                           │
│  ├── steps: [Ingest, Quality, Embed, Index, Publish]    │
│  ├── version_tags: [lance_tag per step]                 │
│  └── schedule: @schedule(daily/hourly/cron)             │
│                                                         │
│  Query                                                  │
│  ├── mode: vector | fts | hybrid | olap | streaming     │
│  ├── source: Lance dataset URI                          │
│  └── result: Arrow Table | RecordBatchReader            │
└─────────────────────────────────────────────────────────┘
```

### 4.2 Entity Relationships

- A **Dataset** contains multimodal data in a single Lance table (cross-modality unified)
- A **Catalog** manages multiple Datasets, implemented as a Ray Named Actor with embedded DuckDB
- A **Pipeline** reads from and writes to Datasets, creating Lance versions as checkpoints
- A **Query** operates on Datasets through the Catalog, supporting 5 query modes

### 4.3 Data Model (Lance Schema)

```python
# Standard Arrow schema for unified multimodal table
schema = pa.schema([
    # Identity
    pa.field("id", pa.string()),
    pa.field("modality", pa.string()),          # 'text' | 'image' | 'video' | 'audio'
    pa.field("source", pa.string()),
    pa.field("created_at", pa.timestamp("us")),

    # Modality-specific (NULL-safe)
    pa.field("text_content", pa.string()),       # NULL for non-text
    pa.field("image_data", pa.binary()),         # Blob out-of-line
    pa.field("video_data", pa.binary()),         # Blob out-of-line
    pa.field("audio_data", pa.binary()),         # Blob out-of-line

    # Quality (NeMo Curator)
    pa.field("quality_score", pa.float32()),     # 0.0-1.0 overall
    pa.field("is_duplicate", pa.bool_()),
    pa.field("dedup_hash", pa.binary()),
    pa.field("nsfw_score", pa.float32()),
    pa.field("aesthetic_score", pa.float32()),   # Image quality

    # Embeddings (multi-model)
    pa.field("emb_text_768", pa.list_(pa.float32(), 768)),
    pa.field("emb_clip_512", pa.list_(pa.float32(), 512)),
    pa.field("emb_multimodal_1024", pa.list_(pa.float32(), 1024)),

    # Summaries
    pa.field("caption", pa.string()),
    pa.field("thumbnail", pa.binary()),          # 64x64 or 256x256 preview
])
```

---

## 5. Innovation Highlights

### 5.1 Arrow Zero-Copy Full Stack

The entire data path from disk to GPU uses Apache Arrow without a single copy or serialization step:

| Stage | Traditional | Arrow Zero-Copy | Savings |
|-------|------------|-----------------|---------|
| Lance → Memory | Parquet decompress + copy | Lance mmap + Arrow | ~2x |
| Daft → DuckDB | to_pandas() → DuckDB | to_arrow() → duckdb.arrow() | ~10x (catalog query path only) |
| DuckDB → PyTorch | .df().values → torch.tensor | .arrow() → ArrowDataset | ~5x (catalog query path only) |
| CPU → GPU | numpy → torch → .cuda() | Arrow → pin_memory → .cuda(non_blocking) | ~3x |

### 5.2 5-Level Lazy Evaluation

| Level | Mechanism | Example |
|-------|-----------|---------|
| 1 | Daft lazy evaluation | `df.where(...)` — no computation until `.collect()` |
| 2 | Lance predicate pushdown | Filter pushed to Fragment scan — skip entire files |
| 3 | Daft Lazy Download | `read_images()` — metadata only until decode needed |
| 4 | Blob out-of-line | `SELECT id, caption` — zero blob I/O |
| 5 | Daft SQL pushdown | `SELECT count(*)` — aggregation at storage layer (DuckDB fallback for catalog queries) |

Result: **100x speedup at 1% selectivity** (filter 100K from 10M rows)

### 5.3 Embedding-First Ingestion

Embeddings are computed as part of the ingestion pipeline, not as a separate step. Model hot-swap is zero-cost: rename old column → add new column → build index. Old data is never rewritten.

### 5.4 Bimodal Query Engine

Five unified SQL query modes through a unified QueryEngine (Daft SQL primary, DuckDB catalog bridge):

1. **Pure vector search** — `lance_vector_search(emb_col, query_vec, top_k)`
2. **Pure full-text search** — `lance_fts(text_col, query_text, top_k)`
3. **Hybrid search** — `lance_hybrid_search(emb, text, vec, txt, alpha, top_k)`
4. **OLAP analytics** — `SELECT ... GROUP BY ...` with Lance predicate pushdown
5. **Combined analytics + vector** — JOIN aggregation with vector search results

---

## 6. Functional Requirements

### 6.1 Data Ingestion

| ID | Requirement | Priority |
|----|------------|----------|
| F-ING-01 | Ingest text/CSV/JSON/Parquet files from local FS, S3/MinIO, HTTP | P0 |
| F-ING-02 | Ingest images (JPEG/PNG/WebP) with automatic thumbnail generation | P0 |
| F-ING-03 | Ingest video files: extract keyframes at scene boundaries (PyAV), store as image column in Lance. Scene detection threshold TBD; output = Lance table with keyframe images + timestamps. MVP scope: single keyframe per scene. | P1 |
| F-ING-04 | Compute text embeddings on ingest (HuggingFace local / Ray Serve / external API) | P0 |
| F-ING-05 | Compute image embeddings on ingest (CLIP/SigLIP) | P0 |
| F-ING-06 | Store raw data + embeddings in unified Lance table | P0 |
| F-ING-07 | Build vector index asynchronously after embedding completion | P0 |
| F-ING-08 | Content-addressed dedup (SHA-256 exact + pHash perceptual) | P0 |  ⬆️ upgraded by ADR-02
| F-ING-09 | Multi-fidelity storage (thumbnail + preview + original) | P1 |

### 6.2 Data Processing

| ID | Requirement | Priority |
|----|------------|----------|
| F-PROC-01 | Daft DataFrame API for multimodal transformations | P0 |
| F-PROC-02 | GPU/CPU heterogeneous scheduling (`use_gpu=True`) | P0 |
| F-PROC-03 | SQL query support (Daft SQL + DuckDB) | P1 |  ⬇️ demoted by ADR-02
| F-PROC-04 | Quality scoring pipeline (NeMo Curator: dedup, classifier, aesthetic) | P1 |
| F-PROC-05 | Quality scores as Lance columns with predicate pushdown | P0 |
| F-PROC-06 | Lazy download + decode for images/video (no full-file download until needed) | P0 |
| F-PROC-07 | Schema migration: add/alter/drop columns without full rewrite | P0 |
| F-PROC-08 | Distributed processing via Ray (foreach + AutoScale) | P0 |  ⬆️ upgraded by ADR-02
| F-PROC-09 | Remote data loader pattern (CPU decode → Object Store → GPU train) | P1 |

### 6.3 Storage and Versioning

| ID | Requirement | Priority |
|----|------------|----------|
| F-STOR-01 | Lance format for all stored data with Arrow-native I/O | P0 |
| F-STOR-02 | Automatic versioning on every write (Lance version) | P0 |
| F-STOR-03 | Named tags for important versions (experiment snapshots, production) | P0 |
| F-STOR-04 | Time-travel query: read any historical version | P0 |
| F-STOR-05 | Version diff: compare two versions (schema + row + column changes) | P1 |
| F-STOR-06 | Compaction: merge Fragment files, reclaim space from dropped columns | P0 |  ⬆️ upgraded by ADR-02
| F-STOR-07 | Auto-tiered blob lifecycle (Standard → IA → Glacier) | P2 |
| F-STOR-08 | S3/MinIO backend with configurable endpoint | P0 |

### 6.4 Query and Retrieval

| ID | Requirement | Priority |
|----|------------|----------|
| F-QRY-01 | Vector search (HNSW for <1M rows, IVF_PQ for 1M+) | P0 |
| F-QRY-02 | Full-text search (Lance FTS) | P0 |
| F-QRY-03 | Hybrid search (vector + text, configurable alpha weight) | P0 |
| F-QRY-04 | OLAP analytics (Daft SQL primary with Lance predicate pushdown, DuckDB fallback for catalog queries) | P0 |
| F-QRY-05 | Streaming results (fetch_record_batch_reader, constant memory) | P0 |
| F-QRY-06 | Faceted search (DuckDB CUBE + vector search) | P2 |
| F-QRY-07 | Adaptive index selection based on data size and query patterns | P0 |  ⬆️ upgraded by ADR-02 (IVF_PQ required for 10M rows)
| F-QRY-08 | Multi-model ensemble search (join results from multiple embedding columns) | P2 |

### 6.5 Catalog and Metadata

| ID | Requirement | Priority |
|----|------------|----------|
| F-CAT-01 | Centralized catalog as Ray Named Actor (DuckDB embedded) | P0 |
| F-CAT-02 | Register datasets with schema, column metadata, and statistics | P0 |
| F-CAT-03 | Query catalog metadata via SQL | P0 |
| F-CAT-04 | Unified search API routing through catalog | P0 |
| F-CAT-05 | Data lineage as SQL queries over Lance event log | P2 |

### 6.6 Workflow Orchestration

| ID | Requirement | Priority |
|----|------------|----------|
| F-ORCH-01 | Metaflow FlowSpec for all batch pipelines | P0 |
| F-ORCH-02 | Local execution: `python flow.py run` | P0 |
| F-ORCH-03 | Cluster execution: `python flow.py run --with ray` | P0 |
| F-ORCH-04 | Production deployment: `python flow.py --with ray argo-workflows create` | P1 |
| F-ORCH-05a | Transient retry: @retry with exponential backoff for spot worker preemption and network errors | P0 |
| F-ORCH-05b | Error classification: @catch handler classifies errors as retryable vs fatal | P0 |
| F-ORCH-05c | State rollback: Lance version checkout to last-known-good on fatal error | P0 |
| F-ORCH-06 | Scheduled pipelines: @schedule(daily/hourly/cron) | P0 |  ⬆️ upgraded by ADR-02
| F-ORCH-07 | Tag-based run tracking and resume | P1 |  ⬇️ demoted by ADR-02
| F-ORCH-08 | Elastic burst: auto-scale GPU workers on demand, scale back on idle | P1 |
| F-ORCH-09 | Event sourcing: Lance version + Metaflow tag = immutable audit trail | P2 |

### 6.7 Developer Experience

| ID | Requirement | Priority |
|----|------------|----------|
| F-DEV-01 | One-command platform start: `docker compose up -d` | P1 |  ⬇️ demoted by ADR-02
| F-DEV-02 | Jupyter notebook integration for exploration | P1 |  ⬇️ demoted by ADR-02
| F-DEV-03 | uv for dependency management (replaces Poetry) | P0 |
| F-DEV-04 | Python SDK: `from arrow_lake import Lake` | P0 |
| F-DEV-05 | Data testing: pytest assertions on Lance/Daft/DuckDB results | P1 |
| F-DEV-06 | Progressive complexity: 5 API levels (function → Daft → SQL → Ray → Metaflow) | P0 |
| F-DEV-07 | CLI for common operations (ingest, search, status, version) | P2 |

### 6.8 Quality Management (Derived from Architecture ADR-02)

> The following FRs were derived during architecture design (ADR-02) to address structural gaps in quality control and observability. Full specifications are in `_bmad-output/planning-artifacts/architecture.md`.

| ID | Requirement | Priority |
|----|------------|----------|
| F-QUA-01 | QualityFilter registration: pluggable row-level filter interface | P0 |
| F-QUA-02 | Built-in filters: TextLengthFilter + ImageResolutionFilter | P0 |
| F-QUA-03 | Dead-letter persistence: rejected rows → `{table}_dead_letter` Lance table | P0 |
| F-QUA-04 | Quality statistics report: total/passed/rejected + per-filter breakdown | P0 |
| F-QUA-05 | Schema validation gate: strict mode rejects unknown columns/type mismatches | P0 |

### 6.9 Observability (Derived from Architecture ADR-02)

| ID | Requirement | Priority |
|----|------------|----------|
| F-OBS-01 | Prometheus `/metrics` HTTP endpoint (Prometheus format) | P0 |
| F-OBS-02 | Ingestion metrics: rows/bytes/duration/errors per table | P0 |
| F-OBS-03 | Processing metrics: embeddings/quality rejects/active tasks | P0 |
| F-OBS-04 | Query metrics: count/latency/results per query_type | P0 |
| F-OBS-05 | System metrics: Ray actors/table count/uptime | P0 |
| F-OBS-06 | Metrics configurable: env vars for port/path, support disable | P0 |

---

## 7. Non-Functional Requirements

### 7.1 Performance

| ID | Requirement | Target |
|----|------------|--------|
| NF-PERF-01 | Vector search latency (10M rows, top_k=100) | < 10ms |
| NF-PERF-02 | Ingestion throughput (text, single node) | > 50K rows/sec |
| NF-PERF-03 | Arrow zero-copy utilization across full chain | > 90% |
| NF-PERF-04 | Lazy evaluation speedup at 1% selectivity | > 100x vs eager |
| NF-PERF-05 | Streaming query memory footprint (100M rows) | < 100MB |
| NF-PERF-06 | PyTorch DataLoader zero-copy + async GPU transfer | pin_memory + non_blocking |

### 7.2 Reliability

| ID | Requirement | Target |
|----|------------|--------|
| NF-REL-01 | Workflow recovery rate (no human intervention) | > 90% (MVP), > 95% (prod) |
| NF-REL-02 | Data integrity on failure (Lance version + Metaflow checkpoint) | Zero data loss |
| NF-REL-03 | Catalog Actor availability | max_restarts=3, auto-recovery |
| NF-REL-04 | MTTR for transient failures | < 10 minutes |

### 7.3 Scalability

| ID | Requirement | Target |
|----|------------|--------|
| NF-SCALE-01 | Data volume support (single node) | Up to 10M rows |
| NF-SCALE-02 | Data volume support (distributed) | Up to 1B rows |
| NF-SCALE-03 | Concurrent query support | Up to 100 QPS (with read replicas) |
| NF-SCALE-04 | GPU scaling model | Fractional GPU (0.5), up to 8 workers |
| NF-SCALE-05 | Elastic burst: 0 to 8 GPU workers | Scale-up in < 5 minutes |

### 7.4 Cost Efficiency

| ID | Requirement | Target |
|----|------------|--------|
| NF-COST-01 | Elastic burst monthly cost (100GB/month processing) | < $500/month |
| NF-COST-02 | Storage cost reduction via auto-tiering (100TB) | > 50% vs all-Standard |
| NF-COST-03 | Spot GPU utilization for burst workloads | > 70% spot when available |
| NF-COST-04 | Baseline (idle) platform cost | < $400/month |

### 7.5 Usability

| ID | Requirement | Target |
|----|------------|--------|
| NF-USE-01 | Developer onboarding time | < 30 minutes |
| NF-USE-02 | Code changes from local to production deployment | Zero |
| NF-USE-03 | Embedding model hot-swap | Zero data rewrite, zero downtime |
| NF-USE-04 | API complexity levels | 5 levels (simple → advanced) |

### 7.6 Security

| ID | Requirement | Target |
|----|------------|--------|
| NF-SEC-01 | Secrets management | Environment variables / .env files, no hardcoded credentials |
| NF-SEC-02 | S3/MinIO access control | IAM roles (prod) / access keys (dev) |
| NF-SEC-03 | Input validation at API boundaries | Schema validation on ingest |
| NF-SEC-04 | Container security | Official base images, minimal attack surface |

### 7.7 Observability

| ID | Requirement | Target |
|----|------------|--------|
| NF-OBS-01 | Pipeline metrics | Prometheus + Grafana dashboards |
| NF-OBS-02 | Ray cluster monitoring | Ray Dashboard (built-in) |
| NF-OBS-03 | Structured logging | JSON logs with correlation IDs |
| NF-OBS-04 | Data quality reporting | Metaflow Cards (HTML reports per step) |
| NF-OBS-05 | Cost tracking per pipeline run | Ray resource annotation + Prometheus |

---

## 8. Technology Stack

### 8.1 Core Stack (DARMU)

| Component | Technology | Version | Role |
|-----------|-----------|---------|------|
| **D** | Daft | >= 0.7.8 | Multimodal DataFrame engine, Rust kernel |
| **A** | Argo Workflows | >= 3.5 | Workflow engine on K8s |
| **R** | Ray | >= 2.54.1 | Distributed computing (Data/Serve/Actor/ObjectStore) |
| **M** | Metaflow | >= 2.19.22 | User-facing workflow orchestration |
| **U** | uv | latest | Python dependency management |

### 8.2 Extension Layer

| Component | Technology | Role |
|-----------|-----------|------|
| Storage | Lance | Multimodal format, vector index, versioning |
| Quality | NeMo Curator | Data quality scoring, dedup, GPU acceleration |
| OLAP Engine | Daft SQL | Primary OLAP analytics, Arrow-native SQL (validated by CloudKitchens DREAM stack) |
| Catalog | DuckDB | Catalog metadata storage, SQL bridge for metadata queries |
| Inference | Ray Serve | Model serving, autoscaling, GPU management |

### 8.3 Infrastructure

| Component | Dev | Staging | Production |
|-----------|-----|---------|------------|
| Object Storage | MinIO (Docker) | MinIO (SSH) | AWS S3 |
| Orchestration | Docker Compose | Ray SSH (3-4 nodes) | Kubernetes + KubeRay |
| GPU | Local GPU (optional) | Spot GPU (1-2x) | KubeRay with GPU nodes |
| Message Bus | asyncio.Queue | asyncio.Queue | Redis Streams |
| Monitoring | CLI only | Prometheus + Grafana | Prometheus + Grafana |

---

## 9. Architecture Overview

### 9.1 Layered Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                    ORCHESTRATION LAYER                       │
│              Metaflow + Argo Workflows                       │
│  @project │ @schedule │ tag/resume │ @retry/@catch           │
├─────────────────────────────────────────────────────────────┤
│                    PROCESSING LAYER                          │
│              Daft (Rust kernel, multimodal)                  │
│  embed │ classify │ Lazy Download │ SQL │ GPU/CPU hetero      │
├──────────┬───────────────────┬──────────────────────────────┤
│ QUALITY  │     COMPUTE       │         SERVE                 │
│ NeMo     │  Ray Data         │  Ray Serve                    │
│ Curator  │  Checkpoint       │  Autoscale                   │
│          │  AutoScale        │  Fractional GPU               │
├──────────┴───────────────────┴──────────────────────────────┤
│                    STORAGE LAYER                             │
│           Lance + S3 (MinIO dev / AWS prod)                  │
│  Unified Table │ Multi-Fidelity Blob │ Version/Tag           │
│  IVF_PQ / HNSW │ FTS │ Hybrid Search │ Auto-Tier            │
├────────────┬──────────────────┬─────────────────────────────┤
│  QUERY     │   CATALOG        │    OBJECT                     │
│  Daft SQL  │   Ray Actor      │  Ray Object                  │
│  OLAP+Vec  │   + DuckDB       │  Store (Zero-Copy)           │
│  +FTS      │   (Catalog-only) │                              │
└────────────┴──────────────────┴─────────────────────────────┘
│  Cross-cutting: uv │ Config │ Logging │ Metrics │ Security    │
│  Docker Compose (dev) │ KubeRay (prod) │ CI/CD                │
└─────────────────────────────────────────────────────────────┘
```

### 9.2 Data Flow

```
Ingestion:  Raw → Daft read → embed (GPU) → quality score → Lance write → index (async)
Query:      SQL/Daft → Catalog Actor → Daft SQL OLAP + Lance hybrid_search → Arrow → results
Processing: Metaflow → Daft → Ray distributed → NeMo Curator → Lance merge → version tag
Training:   Lance → Daft → Daft SQL stream → ArrowDataset → pin_memory → .cuda(non_blocking)
```

### 9.3 Key Architecture Patterns

**Catalog-as-Actor:** Ray Named Actor wrapping DuckDB solves single-writer bottleneck. Read replicas for scaling. `max_restarts=3` for resilience.

**Remote Data Loader:** CPU workers decode and transform → Ray Object Store (zero-copy) → GPU workers train. Eliminates GPU starvation.

**Hybrid Event Bus:** Progressive evolution: `asyncio.Queue` (dev) → `Ray Queue Actor` (multi-node) → `Redis Streams` (production).

**Self-Healing Workflow:** Three levels: `@retry` (transient) → `@catch` + classification (semantic) → `resume` + Lance version rollback (state).

---

## 10. MVP Scope and Roadmap

### 10.1 MVP (Month 1-2)

> Updated by ADR-02: MVP Gate adjusted to include quality filtering and observability.

- [ ] P1: Lance + Daft + DuckDB local integration
- [ ] P2: Text + Image unified table
- [ ] P3: HuggingFace local model embedding (text + image)
- [ ] A1: DuckDB in Ray Actor (single node Catalog metadata store) + Daft SQL for OLAP
- [ ] A2: lance_vector_search + lance_fts + lance_hybrid_search
- [ ] A5: Docker Compose local development environment
- [ ] O2: Basic version diff
- [ ] Q1: Quality filter chain (TextLengthFilter + ImageResolutionFilter + dead_letter)
- [ ] Q2: Prometheus /metrics endpoint with 17 minimum metrics
- [ ] DARMU stack: uv + Metaflow + Daft + Ray (local)
- [ ] Python SDK: `from arrow_lake import Lake`
- [ ] Basic metadata search (SQL filename/date/modality filtering before full search)
- [ ] Dataset lifecycle management (delete/archive/restore)
- [ ] Data export to standard formats (Parquet, CSV)

**MVP Gate Criteria (ADR-02 adjusted):**
- Time: < 45 minutes (was 30 min - increased for quality filtering configuration)
- Data: 1000 mixed-quality real records (with noisy text, low-res images - not clean data)
- Pipeline: 4 steps (ingest -> quality filter -> embed -> search), not 3 steps
- Validation: TTV + /metrics endpoint observable

**Sprint Plan:** MVP Core (Week 1-6) covers Epics 1-5 (~18 FRs). MVP Enhanced (Week 6-8) covers full E2E pipeline validation with Epics 2-3 (full) + 6 (E2E).

### 10.2 Production (Month 3-6)

- [ ] Video support (Daft video + Cosmos-Embed1)
- [ ] A3: S3 + KubeRay RayJob deployment
- [ ] A4: Redis Streams event bus
- [ ] O1: S3 Lifecycle + Glacier auto-tier
- [ ] O3: NeMo Curator GPU dedup + scoring pipeline
- [ ] O4: Metaflow @retry + Ray Checkpoint self-healing (full 3-level)
- [ ] Multi-tenant: KubeRay namespace + Lance path prefix isolation
- [ ] Argo Workflows production deployment
- [ ] Prometheus + Grafana monitoring
- [ ] Catalog read replica for high availability (read-only failover)
- [ ] Lightweight production deployment package (docker-compose.prod.yml + health checks)

### 10.3 Scale (Month 6-12)

- [ ] Adaptive index selection (auto HNSW/IVF_PQ)
- [ ] Multi-model ensemble search
- [ ] Faceted search with DuckDB CUBE
- [ ] Edge Lakehouse (Jetson Orin deployment)
- [ ] Multimodal RAG pipeline (Ray Serve rerank)
- [ ] Self-evolving pipeline (Metaflow parameter search + feedback)
- [ ] MotherDuck cloud catalog integration (if DuckDB retained) or Daft SQL distributed federation

---

## 11. Open Questions and Risks

### 11.1 Open Questions

| # | Question | Impact | Decision Needed |
|---|----------|--------|-----------------|
| 1 | Lance Parquet ↔ Lance native conversion overhead | Data migration complexity | Prototype measurement |
| 2 | DuckDB Lance extension maturity for production | Query reliability | Monitor upstream progress |
| 3 | Daft + Ray integration stability at scale | Processing reliability | Load testing at 100M+ rows |
| 4 | NeMo Curator Lance bridge (cuDF → Arrow) performance | Quality pipeline throughput | Prototype measurement |
| 5 | Ray AutoScale v2 spot instance preemption behavior | Cost predictability | Test in staging |

### 11.2 Technical Risks

| Risk | Probability | Impact | Mitigation |
|------|------------|--------|------------|
| Lance breaking API changes | Low | High | Pin versions, test on upgrade |
| DuckDB single-writer bottleneck under load | Medium | Medium | Catalog Actor read replicas |
| Ray GCS bottleneck at large scale | Low | High | Use Redis event bus for coordination |
| NeMo Curator requires NVIDIA GPU only | High | Medium | Fallback to CPU quality scoring |
| Metaflow Argo integration issues | Low | Medium | Direct Argo YAML as fallback |
