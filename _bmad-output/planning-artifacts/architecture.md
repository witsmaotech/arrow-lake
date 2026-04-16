---
stepsCompleted: [1, 2, 4, 5, 6, 7, 8]
step3Skipped: true
step3Impact: |
  Step 3 (Component Ecosystem) was skipped during architecture decisions. Impact:
  - Dependency versions not fully validated (Daft version contradiction >=0.4.0 vs >=0.7.0)
  - Auxiliary library versions (structlog, tenacity, pydantic, boto3, prometheus-client) deferred to implementation
  - Recommended "Step 3 Lite" before implementation: validate Daft>=0.7.8 + DuckDB Lance extension + Pydantic v2 Arrow type mapping
lastStep: 8
status: 'complete'
completedAt: '2026-04-11'
updatedAt: '2026-04-13'
inputDocuments:
  - _bmad-output/planning-artifacts/prd.md
  - _bmad-output/planning-artifacts/prd-zh.md
  - docs/superpowers/specs/2026-04-10-multimodal-lakehouse-design.md (git HEAD)
  - _bmad-output/brainstorming/brainstorming-session-2026-04-10-1500.md
  - _bmad-output/brainstorming/appendix-deep-dives.md
workflowType: 'architecture'
project_name: 'arrow-lake'
user_name: 'Witshine'
date: '2026-04-11'
language: 'en'
---

# Architecture Decision Document — Arrow Lake

_This document builds collaboratively through step-by-step discovery. Sections are appended as we work through each architectural decision together._

## Project Context Analysis

### Requirements Overview

**Functional Requirements:**

57 PRD functional requirements distributed across 7 categories: ingestion (9), processing (9), storage (8), query (8), catalog (5), orchestration (11, including F-ORCH-05 split into 05a/05b/05c), and developer experience (7). P0 requirements: 39, P1 requirements: 12, P2 requirements: 6. ADR-02 supplemented with 11 derived FRs (F-QUA-01~05 quality control + F-OBS-01~06 observability), totaling 68. P0 requirements total: 50, P1: 12, P2: 6.

**Non-Functional Requirements:**

27 non-functional requirements spanning 7 domains: performance (6, core constraint: vector search <10ms, zero-copy utilization >90%), reliability (4, core constraint: auto-recovery rate >95%), scalability (5, core constraint: elastic scaling <5 minutes), cost (4, core constraint: monthly <$500), usability (4, core constraint: onboarding <30 minutes), security (4), observability (5).

**Scale & Complexity:**

- Primary domain: Scientific ML platform / backend infrastructure
- Complexity level: Medium (greenfield, single-team, no RBAC)
- Estimated architectural components: ~15

### Technical Constraints & Dependencies

**Core Architectural Constraints (confirmed through ADR analysis):**

1. **Arrow zero-copy is iron law** — All component boundaries must output Arrow format. If any component requires copy/serialization, that is an integration bug, not an architectural choice
2. **Ray Placement Group is prerequisite for zero-copy** — CPU/GPU workers must be co-located on the same node; otherwise Object Store degrades to serialized transfer (100-500x degradation)
3. **Catalog Actor handles metadata management only** — DuckDB embedded in Catalog Actor is solely responsible for metadata storage and catalog queries; OLAP analytics are executed by Daft SQL; >100 QPS scenarios require read replicas (Story 6.11)
4. **Lance Fragment size must be monitored** — 128MB-512MB is the optimal range; automatically run `compact_files` after writes
5. **Version bloat requires proactive management** — `@schedule` periodic cleanup; `production` tag permanently retained
6. **GPU cost requires hard cap** — namespace `ResourceQuota` + Prometheus budget alerts

**Technology Dependency Matrix:**

| Dependency | Version Constraint | Risk | Fallback Strategy |
|------|---------|------|---------|
| Lance | >= 4.0.0 | API changes may break zero-copy chain | Pin version + integration tests |
| Daft | >= 0.7.8 | Ray integration stability | Degrade to Daft standalone mode |
| Ray | >= 2.54.1 | GCS bottleneck, AutoScale v2 | Redis event bus replacement |
| DuckDB | >= 1.5.1 | Lance extension maturity + WAL multi-connection stability | Catalog-only degradation: Daft SQL takes over OLAP, DuckDB retains metadata storage only |
| Metaflow | >= 2.19.22 | Argo integration issues | Direct Argo YAML |
| NeMo Curator | >= 1.1.0 | NVIDIA GPU only, cuDF→Arrow bridge | CPU quality scoring fallback |

### Cross-Cutting Concerns Identified

**Cross-Component Concerns:**

1. **Arrow zero-copy discipline** — All data paths (Lance→Daft, Lance→DuckDB, Lance→PyTorch) must verify Arrow shared memory across component boundaries; intermediate serialization is not allowed
2. **Configuration management** — Pydantic Settings, differentiated by environment (dev/staging/prod), injected via Metaflow Config
3. **Structured logging** — JSON format + correlation ID (Metaflow `run_id`), for cross-distributed component tracing
4. **Cost tracking** — Ray resource annotations + Prometheus; record GPU-hours and cost per pipeline run
5. **Schema evolution compatibility** — Lance `add_columns` (zero-cost) preferred over `alter_columns` (requires rewrite); new columns nullable

**Risk Identification (through pre-mortem + failure mode analysis):**

| # | Risk | Probability | Impact | Prevention |
|---|------|------|------|---------|
| R1 | Arrow zero-copy chain breakage (dependency upgrade) | Medium | Fatal | Pin versions + zero-copy chain regression tests |
| R2 | Catalog Actor single point of failure (memory leak / high QPS) | High | Severe | Read replicas + memory monitoring + auto-restart |
| R3 | Lance version bloat (storage cost runaway) | Medium | Medium | `@schedule` periodic cleanup + retention policy |
| R4 | GPU cost overrun (workers not releasing) | High | High | `shutdownAfterJobFinishes` + `ResourceQuota` |
| R5 | Ray Object Store cross-node degradation | Medium | High | Placement Group constraint co-location |
| R6 | Spot Worker high-frequency preemption | High | Low | AutoScale v2 auto-replacement + retry |
| R7 | cuDF→Arrow bridge performance bottleneck | Medium | Medium | Prototype validation + CPU fallback |
| R8 | DuckDB Lance extension bug + WAL multi-connection failure | Medium | High | Degrade to Daft SQL for OLAP; Story 1.2 Spike validation (3-day limit, with NO-GO trigger) |
| R9 | Arrow Schema evolution incompatibility | Medium | High | DuckDB/Daft tolerance validation for schema changes |

### Architecture Decisions from ADR Analysis

**ADR-01: Catalog Architecture — Connection Pool Model (Option C)**

After a three-way debate between routing model (A) vs separation model (B) vs connection pool model (C), Option C was selected.

| Dimension | A: Routing | B: Separation | C: Connection Pool ✅ |
|------|-----------|-----------|-------------|
| Throughput ceiling | ~50-80 QPS | High (horizontal scaling) | ~100-200 QPS |
| Architectural complexity | Low | High | Medium |
| Data consistency | Strong | Eventual consistency | Strong |
| Development cost | Low | High | Medium |
| Fault isolation | Single point | Good | Semi-isolated |

**Design Highlights:**
- Catalog Actor remains singleton, but internally implements DuckDB WAL connection pool (4 read connections + 1 write connection, catalog-only default; original 8 read connections reduced due to OLAP migration to Daft SQL)
- Connection pool serves only metadata operations (schema queries, table registration, version listings); OLAP analytics executed by Daft SQL
- Streaming queries executed through Daft SQL (not DuckDB connection pool)
- Evolution path: Phase 1 catalog-only connection pool → Phase 2 read replicas (Story 6.11) for high availability

**ADR-02: MVP P0 Scope Supplement — Quality Control + Observability**

MVP P0 adds 11 FRs from filling two structural gaps:

**Quality Control (5 items):**

| ID | Requirement | Description |
|----|------|------|
| F-QUA-01 | Quality filter registration | `QualityFilter` abstract interface, supporting serial execution of row-level filters |
| F-QUA-02 | Built-in basic filters | `TextLengthFilter` + `ImageResolutionFilter` reference implementations |
| F-QUA-03 | Dead-letter persistence | Rejected records written to `_dead_letter` Lance table, with rejection reason |
| F-QUA-04 | Quality statistics report | Record total/passed/rejected row counts + rejection distribution by filter dimension |
| F-QUA-05 | Schema validation gating | Optional strict mode at ingestion time, validating column types and non-null constraints |

**Observability (6 items):**

| ID | Requirement | Description |
|----|------|------|
| F-OBS-01 | Prometheus endpoint | `/metrics` HTTP endpoint, Prometheus format |
| F-OBS-02 | Ingestion metrics | Row count/byte count/duration/error count, grouped by `table_name` |
| F-OBS-03 | Processing metrics | Embedded row count/duration/quality rejection count/active task count |
| F-OBS-04 | Query metrics | Query count/latency/result count, grouped by `query_type` |
| F-OBS-05 | System metrics | Ray Actor count/table count/uptime |
| F-OBS-06 | Metrics configurability | Environment variables control port/path, support disabling |

**Minimum Prometheus Metrics Set (17 metrics):** `arrow_lake_ingestion_rows_total`, `arrow_lake_ingestion_bytes_total`, `arrow_lake_ingestion_duration_seconds`, `arrow_lake_ingestion_errors_total`, `arrow_lake_embedding_rows_total`, `arrow_lake_embedding_duration_seconds`, `arrow_lake_quality_rejected_rows_total`, `arrow_lake_processing_active_tasks`, `arrow_lake_query_total`, `arrow_lake_query_duration_seconds`, `arrow_lake_query_result_count`, `arrow_lake_ray_actors_active`, `arrow_lake_lance_table_count`, `arrow_lake_lance_fragment_size_bytes`, `arrow_lake_ray_gpu_hours_total`, `arrow_lake_lance_version_count`, `arrow_lake_uptime_seconds`.

**MVP Gate Adjustments:**
- Time: 30 minutes → 45 minutes (adding quality filter configuration time)
- Data: clean data → 1000 rows of mixed quality real data (including noisy text, low-resolution images)
- Pipeline: three steps → four steps (ingest → quality filter → embed → retrieve)
- Validation: TTV + `/metrics` endpoint observability

**ADR-03: Ray Object Store Sizing & Eviction Policy**

When loading multi-GB Lance fragments into Ray Object Store for GPU processing (remote data loader pattern, Story 7.5), memory pressure is inevitable without deliberate sizing.

| Dimension | Option A: Fixed Budget | Option B: Proportional | Option C: Adaptive ✅ |
|------|-----------|-----------|-------------|
| Memory allocation | Fixed 2GB per worker | 40% of node RAM per worker | 60% of available (excl. head) + LRU eviction |
| Spill-to-disk | No | Manual trigger | Automatic at 80% threshold |
| GPU pin_memory | Not managed | Pre-allocate per batch | Dynamic per `ArrowDataset` request |
| Complexity | Low | Medium | Medium |
| Production risk | OOM on large batches | Under-utilization | Mature pattern from Ray docs |

**Design Highlights:**
- Object Store memory budget = 60% of available node RAM (excluding head node and system overhead)
- LRU eviction triggered at 80% capacity; evicted Arrow tables re-read from Lance on demand (zero-copy intact)
- Spill-to-disk enabled at 80% capacity to `/tmp/ray_spill` with automatic cleanup on worker exit
- `pin_memory` managed by PyTorch `ArrowDataset` — no manual `cuda` calls in user code
- Monitor via: `arrow_lake_ray_object_store_usage_bytes` (Gauge), `arrow_lake_ray_object_store_evictions_total` (Counter)
- Sizing validated in Story 7.5 with 10GB image dataset before production deployment

**ADR-04: Embedding Model Serving Strategy**

Three embedding paths exist (HuggingFace local, Ray Serve, external API) with no explicit MVP default. A clear default prevents analysis paralysis and uncontrolled GPU cost.

| Dimension | Option A: HuggingFace Local ✅ (MVP) | Option B: Ray Serve (Prod) | Option C: External API (Optional) |
|------|-----------|-----------|-------------|
| Infrastructure | Single GPU or CPU | Ray Serve cluster | API key + network |
| Latency | ~50-200ms per batch | ~20-100ms per batch | ~100-500ms per batch |
| Cost | Free (self-hosted) | Ray GPU hours | Per-request billing |
| Model hot-swap | Restart flow | Blue-green deploy | Header routing |
| Complexity | Low | Medium | Low |

**Design Highlights:**
- **MVP default (Week 1-6):** HuggingFace `SentenceTransformers` local inference — zero infrastructure beyond existing GPU workers. Models cached on first load, shared across Ray tasks via `model_cache` in Object Store.
- **Production scale (Month 3+):** Migrate to Ray Serve when concurrent inference > 10 QPS or multi-model serving required (Story 8.8). Migration path: wrap existing `Encoder` class as Ray Serve deployment — no API change.
- **External API (optional):** Supported via `EmbeddingProvider` interface (Story 1.4). Useful for proprietary models (OpenAI `text-embedding-3-large`). Rate limiting and cost tracking via `arrow_lake_embedding_external_requests_total` metric.
- **Cost governance:** `shutdownAfterJobFinishes` ensures GPU workers release after embedding batch completes. Metaflow `@resources(gpu=1)` controls per-flow GPU allocation.

### Functional Requirement Conflicts Identified

| Conflict | Requirements Involved | Resolution |
|------|---------|---------|
| Zero-copy vs NeMo Curator | F-PROC-04 + Constraint #1 | NeMo Curator cuDF→Arrow is a controlled copy point; NF-PERF-03 computation excludes this stage |
| Catalog singleton vs 100 QPS | F-CAT-01 + NF-SCALE-03 | Adopt ADR-01 connection pool model; queries bypass Actor routing via direct connection |
| Progressive complexity vs zero-copy | F-DEV-06 + Constraint #2 | L4 level provides default Placement Group template; `ArrowCopyDetector` detects non-Arrow transfers |
| Embedding column bloat vs memory | F-ING-04/05 + NF-PERF-06 | 80GB embeddings cannot all be pinned in memory; IVF_PQ compression elevated from P2 to P0 prerequisite |

### MVP First-Week Execution Plan

**"Integration Before Features" Strategy:** 5 days to complete end-to-end zero-copy pipeline validation.

| Day | Morning | Afternoon |
|-----|------|------|
| 1 | Environment reachability validation + sample Lance dataset fixture | Boundaries 1-3: Lance→Daft, Daft→DuckDB, DuckDB→PyTorch |
| 2 | Boundary 4: CPU→GPU (pin_memory) | Boundaries 5-6: Ray Object Store + cuDF→Arrow |
| 3 | End-to-end chain smoke test + performance baseline | SDK interface definition (`ArrowLakeClient`) |
| 4 | Minimal pipeline implementation: ingest→index→search | Pipeline integration tests |
| 5 | Docker Compose + TTV automated testing | CI pipeline configuration + baseline recording |

**Test Strategy Layers:**

| Layer | Coverage | MVP Target |
|------|------|---------|
| Unit | Each operator/connector | 80% |
| Integration | 6 Arrow boundaries | Critical path 100% |
| E2E | Full pipeline (4 steps) | Main flow 100% |
| Contract | Arrow Schema compatibility | Schema changes 100% |
| Performance | Search latency/throughput | P50 baseline comparison |

**Zero-Copy Verification Method:** Arrow Buffer address comparison (`buf.address`), refcount detection to confirm shared memory — not "feels like zero-copy" but quantitative evidence.

### Priority Adjustments

**Elevated to P0:**
- F-ING-08 (content-addressable deduplication) P1→P0: deduplication is a prerequisite for quality pipeline
- F-PROC-08 (Ray distributed) P1→P0: MVP roadmap already includes `--with ray`
- F-ORCH-06 (@schedule) P1→P0: necessary condition for automated version bloat management
- F-STOR-06 (compact) P1→P0: prerequisite for fragment size control
- F-QRY-07 (adaptive index) P2→P0: IVF_PQ is necessary under 10 million rows

**Deferred to P1:**
- F-DEV-01 (Docker Compose) P0→P1: convenience, does not affect core functionality
- F-DEV-02 (Jupyter) P0→P1: UX optimization
- F-PROC-03 (SQL query) P0→P1: Python API first, SQL later
- F-ORCH-07 (tag tracking and recovery) P0→P1: Metaflow has built-in `run_id` tracking

### KPI Recommendations

**Three OMTMs (One Metric That Matters) for MVP Phase:**

1. **TTV (Time to Value):** New user from `docker compose up` to first hybrid search result < 45 minutes
2. **Pipeline completion rate:** Success rate of users attempting the full 4-step pipeline (ingest → quality → embed → retrieve) > 70%
3. **Weekly active usage days:** Beta users using >= 3 days per week

Replace imprecise subjective metrics (developer satisfaction NPS, absolute query latency) with behavioral metrics to validate product value.

### Risk Priority Reassessment

| Priority | Risk | Rationale |
|--------|------|------|
| **P0** | DuckDB Lance extension bug | Only query layer exit point; third-party dependency uncontrollable |
| **P0** | Zero-copy chain breakage | Performance baseline; any broken link = performance unusable |
| **P1** | Arrow Schema evolution incompatibility | DuckDB/Daft have different tolerance for breaking changes |
| **P1** | GPU cost overrun | Business risk; could lead to project cancellation |
| **P1** | Catalog single point of failure | Connection pool model mitigated, but routing remains critical path |
| **P2** | Version bloat | Clear mitigation exists |
| **P2** | Spot Worker preemption | Ray built-in recovery mechanism |
| **P3** | Object Store cross-node degradation | Placement Group deployment topology issue |
| **P3** | cuDF→Arrow bridge bottleneck | Controllable through batch processing tuning |

### MVP Evolution Path Risks

**Ravine 1: Local Docker → Multi-node Ray on K8s**
- Data sharding strategy undefined
- Ray cluster lifecycle management learning curve
- **Recommendation:** Insert Mini Cluster milestone (3-4 nodes, Ray autoscaler + SSH mode)

**Ravine 2: Technical Validation → Production Launch**
- No multi-tenant isolation (Raj GPU tasks may starve Maya ETL)
- No data governance (lineage/audit/access control)
- **Recommendation:** Plan multi-tenant architecture early; Beta→Production transition time may be underestimated 2-3x

## Core Architectural Decisions

### Decision Priority Analysis

**Critical Decisions (Block Implementation):**

| ID | Decision | Rationale |
|----|----------|-----------|
| D-1.1 | Schema: Pydantic-first | SDK experience core, foundation for all data operations |
| D-2.1 | SDK: Hybrid (Fluent + Declarative) | Primary user interaction entry point, API contract definition |
| D-2.3 | Error handling: Exception + tenacity | Foundation for error propagation across Ray Actor boundaries |
| D-3.1 | Deployment: Docker Compose + Helm Chart | Prerequisite for TTV < 45min |
| D-4.2 | Encryption: TLS + EBS + raw memory access | Hard constraint for Arrow zero-copy chain |

**Important Decisions (Shape Architecture):**

| ID | Decision | Rationale |
|----|----------|-----------|
| D-1.2 | Index: On-demand build + incremental update | Vector search performance guarantee |
| D-1.3 | Index pattern: One index per column | Prerequisite for FTS + vector hybrid query |
| D-2.2 | Actor communication: Pure Ray Actor Call | MVP simplicity, Phase 2 evolution to Queue |
| D-3.2 | CI/CD: Squash merge + Trunk-based | Optimal for single-team efficiency |
| D-3.6 | Metaflow: @project + Config YAML | Pipeline reproducibility |

**Deferred Decisions (Post-MVP):**

| Decision | Rationale | Earliest Phase |
|----------|-----------|---------------|
| Frontend UI | MVP is backend-only + Python SDK | Phase 2 |
| API Key / OAuth2 auth | MVP has no external users; Docker network isolation sufficient | Phase 2 |
| Ray Queue decoupling | MVP has few Actors; direct RPC sufficient | Phase 2 (multi-tenant) |
| Vault / HCSI Secrets | `.env` sufficient for MVP use | Production |
| K8s NetworkPolicy | Docker default isolation sufficient | Production |

### Data Architecture

**D-1.1 Schema Definition: Pydantic-first**

- Define business schemas with Pydantic v2 models, converted to `pyarrow.schema()` at runtime
- Rationale: Python SDK-first platform; Pydantic provides type safety, IDE completion, JSON serialization
- Pydantic v2 `CoreSchema` → Arrow type mapping: `str→pa.string()`, `int→pa.int64()`, `float→pa.float32()`, `list[float]→pa.list_(pa.float32())`
- Schema evolution follows Lance rules: `add_columns` (zero-cost) preferred over `alter_columns` (requires rewrite); new columns nullable

**D-1.2 Index Build: On-demand Trigger**

- User explicitly calls `lake.table("docs").create_index()` to trigger index build
- MVP does not auto-index; Phase 2 considers `after_commit` hook for automatic building
- Rationale: User control is safer; index building is GPU-intensive and requires explicit confirmation

**D-1.3 Index Pattern: One Index Per Column**

- Text columns → FTS index (Tantivy via Lance)
- Vector columns → IVF_PQ index (Lance built-in)
- Hybrid queries hit different indexes respectively, results merged
- Rationale: FTS and vector are orthogonal dimensions; unified index cannot optimize both simultaneously

**D-1.4 Index Update: Incremental Update**

- Lance version append-friendly; incrementally update indexes after new data append
- Avoid full rebuild (full rebuild of 10 million rows has unacceptable duration)

**D-1.5 Ray Object Store Cache: LRU + TTL(30min)**

- Ray `put/get` built-in LRU eviction
- 30min TTL overlay prevents memory leaks in long-running pipelines
- Manual eviction interface: `lake.cache.evict(table_name)` for proactive user cleanup

**D-1.6 Blob Out-of-line Threshold: 1MB**

- Column values exceeding 1MB (e.g., raw image bytes) are lazily loaded
- PyTorch DataLoader triggers actual reads per batch demand
- Threshold configurable via settings

### API & Communication

**D-2.1 SDK Design: Hybrid Mode**

- **Interactive queries (Fluent Builder):** `lake.table("docs").search("query").vector(top_k=10).to_arrow()`
- **Batch pipelines (Declarative Config):**
  ```python
  pipeline = IngestPipeline(
      source=S3Source(bucket="my-data", prefix="images/"),
      filters=[TextLengthFilter(min_chars=10), ImageResolutionFilter(min_px=64)],
      embed=True,
      index=True,
  )
  pipeline.run()
  ```
- Style references Daft's own API to reduce learning curve

**D-2.2 Actor Communication: Pure Ray Actor Call**

- MVP all internal component communication via `actor.method.remote()` direct calls
- No Message Queue decoupling needed
- Rationale: Limited number of Actors (~5-10), single namespace; Ray Actor Call has built-in serialization/retry/timeout
- Evolution path: Phase 2 multi-tenant scenario introduces Ray Queue for backpressure and decoupling

**D-2.3 Error Handling: Custom Exception + tenacity**

- Exception hierarchy:
  ```
  ArrowLakeError (base)
  ├── IngestionError
  │   ├── SourceConnectionError
  │   ├── SchemaValidationError
  │   └── QualityFilterError
  ├── QueryError
  │   ├── IndexNotFoundError
  │   └── QueryTimeoutError
  ├── CatalogError
  │   ├── TableNotFoundError
  │   └── ConnectionPoolExhaustedError
  └── RayRuntimeError
      ├── WorkerUnavailableError
      └── PlacementGroupError
  ```
- Retry strategies (tenacity):
  - Spot Worker preemption: `retry(stop_after_attempt=3, wait=exponential(multiplier=1, max=30))`
  - Transient network errors: `retry(stop_after_attempt=5, wait=exponential(multiplier=0.5, max=10))`
  - Non-retryable errors (schema validation failure, etc.): no retry, raise immediately

**D-2.4 REST API: MVP Python SDK Only**

- MVP does not provide HTTP REST layer
- `/metrics` endpoint exposed independently (Prometheus scrape), not equivalent to full REST API
- 5-level progressive API complexity implemented through Python SDK (L1 Function → L5 Metaflow)
- Phase 2 introduces FastAPI wrapper for non-Python clients

### Infrastructure & Deployment

**D-3.1 Deployment Topology: Docker Compose + Helm Chart**

- **Development environment:** `docker compose up` one-click startup
  - Ray head + 1 worker (CPU only, GPU optional)
  - DuckDB (embedded in catalog actor)
  - Prometheus + Grafana (monitoring)
- **Production environment:** Helm Chart deploying Ray on K8s
  - Official Ray Helm Chart + custom values
  - Prometheus Operator + ServiceMonitor
- **Evolution path:** Docker Compose → Mini Cluster (3-4 node Ray SSH) → K8s Helm

**D-3.2 CI/CD: Squash Merge + Trunk-based**

- Single branch `master`, feature branches + PR
- Squash merge keeps main branch linear
- PR gates: Ruff lint + MyPy type check + pytest (CPU) → merge → GPU nightly + E2E

**D-3.3 GPU Testing: Nightly + Manual Trigger**

- CPU tests run on every PR (covering logic correctness)
- GPU tests run nightly automatically + `@bot run-gpu` PR comment for manual trigger
- Rationale: GPU runner cost is high; zero-copy boundary tests need real GPU

**D-3.4 Configuration Management: Four-Layer Overlay**

```
Code defaults → .env file (local) → Environment variables (Docker/K8s) → Metaflow Config YAML (runtime)
```

- Pydantic Settings automatically merges all four layers
- Full validation at startup; missing required fields fail immediately (Fail Fast)

**D-3.5 Secrets: .env → Vault**

- MVP: `.env` file + `.gitignore` exclusion
- Production: environment variable injection / HashiCorp Vault (Phase 2)
- `.env.example` provides template without real values

**D-3.6 Metaflow Parameter Injection: @project + Config YAML**

```python
from metaflow import FlowSpec, step, project

@project(name="arrow-lake")
class IngestFlow(FlowSpec):
    @step
    def start(self):
        config = self.config  # Injected from Config YAML
```

- Declarative, versionable, diffable
- Config YAML split by environment (dev/staging/prod)

### Security

**D-4.1 Authentication: MVP No Auth**

- Docker network isolation sufficient for internal testing
- `/metrics` access restricted through Prometheus service discovery
- Phase 2 introduces API Key (simple header validation)

**D-4.2 Encryption Strategy**

| Data State | Approach | Notes |
|---------|------|------|
| In transit | TLS | Docker Compose self-signed / K8s cert-manager |
| At rest | EBS encryption | AWS GP3 default block-level encryption |
| In memory | No encryption | Arrow zero-copy requires raw buffer access |

**D-4.3 Network Policy**

- **Local Docker Compose:** Default bridge network, exposing `8000` (metrics) + `8265` (Ray Dashboard)
- **K8s Production:** NetworkPolicy predefined in Helm Chart, `values.yaml` defaults to disabled, enabled for production deployment

### Deferred Decisions

- **Frontend UI:** MVP has no frontend. Grafana Dashboard (Prometheus) satisfies monitoring needs. Data browser deferred to Phase 2
- **Authentication upgrade:** API Key → OAuth2/JWT deferred until external users are introduced
- **Actor decoupling:** Ray Queue deferred to multi-tenant scenario (Phase 2)
- **Secrets upgrade:** Vault deferred to production environment
- **NetworkPolicy:** K8s policy predefined but disabled by default

### Decision Impact Analysis

**Implementation Sequence:**

1. **Day 1-2:** D-1.1 (Schema) + D-2.3 (Error handling) + D-3.4 (Configuration) — Infrastructure layer
2. **Day 2-3:** D-1.5/D-1.6 (Cache) + D-2.2 (Actor communication) — Runtime layer
3. **Day 3-4:** D-2.1 (SDK) + D-1.2/D-1.3/D-1.4 (Index) — Feature layer
4. **Day 4-5:** D-3.1 (Deployment) + D-3.2/D-3.3 (CI/CD) — Release layer
5. **Day 5:** D-4.1/D-4.2/D-4.3 (Security) — Hardening layer

**Cross-Component Dependencies:**

```
D-1.1 (Schema) ──→ D-2.1 (SDK) ──→ D-1.2/D-1.3 (Index)
                     │
D-3.4 (Config) ──→ D-2.3 (Error) ──→ D-2.2 (Actor)
                     │
D-4.2 (Encryption) ──→ D-3.1 (Deploy) ──→ D-3.2 (CI/CD)
```

Schema definition is prerequisite for SDK and index; configuration management is prerequisite for error handling and Actor communication; encryption strategy constrains deployment topology.

## Implementation Patterns & Consistency Rules

### Pattern Categories Defined

**Critical Conflict Points Identified:** 18 conflict points where AI Agents may diverge, distributed across 5 categories.

### Naming Patterns

**Lance Table Naming:** snake_case plural

```
user_documents       ✅ Correct
UserDocuments        ❌ Wrong
user_document        ❌ Singular
raw.user_documents   ❌ No prefix needed
```

**Python Code Naming:**

| Element | Rule | Correct Example | Wrong Example |
|------|------|---------|---------|
| Ray Actor class | PascalCase + `Actor` suffix | `CatalogActor` | `Catalog`, `catalog_actor` |
| Metaflow Flow class | PascalCase + `Flow` suffix | `IngestFlow` | `Ingest`, `ingest_flow` |
| Pydantic Model | PascalCase + semantic suffix | `TableSchema`, `IngestConfig` | `tableSchema`, `table_schema` |
| SDK public method | snake_case | `create_table()` | `createTable()` |
| Lance Schema column name | snake_case | `text_content`, `embedding_vector` | `textContent`, `TextContent` |
| Constants | UPPER_SNAKE_CASE | `DEFAULT_CACHE_TTL` | `defaultCacheTTL`, `Default_Cache_Ttl` |
| Private methods | Single underscore prefix | `_validate_schema()` | `validate_schema_` |

**Prometheus Metrics Naming:** `arrow_lake_{domain}_{metric}_{unit}`

```
arrow_lake_ingestion_rows_total         ✅
arrow_lake_embedding_duration_seconds   ✅
arrow_lake_quality_rejected_rows_total  ✅
arrow_lake_query_duration_seconds       ✅
arw_lake_ingest_rows                    ❌ Inconsistent prefix/naming
ingestion_rows                          ❌ Missing prefix
```

### Structure Patterns

**Package Organization (by functional domain):**

```
arrow_lake/                    # Main package
├── __init__.py               # SDK entry point (ArrowLakeClient)
├── catalog/                  # Catalog module
│   ├── actor.py              # CatalogActor
│   ├── schema.py             # Pydantic → Arrow conversion
│   └── connection_pool.py    # DuckDB WAL connection pool
├── ingest/                   # Ingestion module
│   ├── pipeline.py           # IngestPipeline (Declarative)
│   ├── sources/              # Data source connectors
│   │   ├── base.py           # Abstract base class
│   │   ├── local.py          # Local files
│   │   └── s3.py             # S3
│   └── validators.py         # Ingestion-time validation
├── quality/                  # Quality filtering module
│   ├── filters.py            # QualityFilter abstract + built-in implementations
│   └── dead_letter.py        # Dead-letter persistence
├── embedding/                # Embedding module
│   ├── encoder.py            # Embedding encoder (pluggable)
│   └── manager.py            # Index management
├── query/                    # Query module
│   ├── engine.py             # Dual-mode query engine
│   ├── vector.py             # Vector search
│   ├── fts.py                # Full-text search
│   └── hybrid.py             # Hybrid query result merging
├── ray_runtime/              # Ray runtime
│   ├── placement.py          # Placement Group management
│   └── cache.py              # Object Store cache wrapper
├── config.py                 # Pydantic Settings (four-layer overlay)
├── exceptions.py             # Exception hierarchy
└── metrics.py                # Prometheus metrics definition

flows/                        # Metaflow Flow definitions (outside package)
├── ingest_flow.py
├── embedding_flow.py
└── search_flow.py

tests/
├── unit/
├── integration/              # Arrow zero-copy boundary tests
├── e2e/
└── conftest.py

deploy/
├── docker/
│   └── Dockerfile
├── compose/
│   └── docker-compose.yml
└── helm/
    └── arrow-lake/

configs/                      # YAML configuration (by environment)
├── dev.yaml
├── staging.yaml
└── prod.yaml
```

**Test Naming and Location:**

```
tests/unit/test_catalog_actor.py          ✅ Same name as module
tests/unit/test_connection_pool.py        ✅
tests/integration/test_arrow_boundary.py  ✅ Zero-copy boundary test
tests/e2e/test_full_pipeline.py           ✅ End-to-end pipeline

tests/test_stuff.py          ❌ Not classified by layer
catalog_test.py              ❌ Tests not under tests/
```

### Format Patterns

**Arrow Schema Conventions:**

```python
# ✅ Correct: snake_case column names, new columns nullable, vector columns fixed dimension
pa.schema([
    pa.field("text_content", pa.string()),
    pa.field("image_bytes", pa.binary()),
    pa.field("embedding_vector", pa.list_(pa.float32(), 768)),
    pa.field("_source_url", pa.string()),        # Metadata columns with _ prefix
    pa.field("_ingested_at", pa.timestamp("us")),
    pa.field("_quality_score", pa.float32()),    # New columns nullable
])

# ❌ Wrong
pa.field("textContent", pa.string())             # camelCase
pa.field("embedding", pa.list_(pa.float32()))     # Missing dimension
pa.field("quality_score", pa.float32(), nullable=False)  # New column forced non-null
```

**Log Format (JSON + structlog):**

```json
{
  "timestamp": "2026-04-11T10:30:00.000Z",
  "level": "INFO",
  "logger": "arrow_lake.ingest.pipeline",
  "message": "Ingestion completed",
  "correlation_id": "mf-run-abc123",
  "table": "user_documents",
  "rows": 1500,
  "duration_ms": 2340
}
```

- `correlation_id` = Metaflow `run_id`
- Additional fields attached by context (table, rows, duration_ms)
- `print()` and bare `logging.info()` are prohibited

**Configuration File Format (YAML):**

```yaml
# configs/dev.yaml
arrow_lake:
  storage:
    base_path: ./data/lance
    max_fragment_size_mb: 256
  cache:
    ttl_seconds: 1800
    blob_threshold_mb: 1
  ray:
    num_workers: 2
    gpu_per_worker: 0
  catalog:
    read_connections: 4
    write_connections: 1
```

- snake_case keys, numeric values with unit suffixes (`_mb`, `_seconds`)
- `.json` configuration files are prohibited

### Communication Patterns

**Ray Actor Method Conventions:**

| Rule | Description | Correct | Wrong |
|------|------|------|------|
| Method name | snake_case, verb-first | `get_table()` | `getTable()`, `table_get` |
| Return value | Arrow Table or Pydantic model | `return pa.Table` | `return {"data": [...]}` |
| External methods | `.remote()` call | `actor.ingest.remote(data)` | `actor.ingest(data)` |
| Internal methods | `_` prefix + normal call | `self._validate(data)` | Exposing internal methods |
| Timeout | Configurable default 30s | `ray.wait(ref, timeout=30)` | No timeout |

**Metaflow Flow Conventions:**

| Rule | Description |
|------|------|
| Flow naming | PascalCase + `Flow` suffix |
| Step naming | snake_case: `start`, `transform`, `end` |
| Parameter injection | `@project` + Config YAML |
| Self-contained | `python flows/ingest_flow.py run` independently runnable |
| Logging | Use Metaflow logger, auto-associated `run_id` |

### Process Patterns

**Error Handling Pattern:**

```python
# ✅ Correct: Custom exception + tenacity retry
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

class IngestionError(ArrowLakeError): ...

@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, max=30),
    retry=retry_if_exception_type(RayRuntimeError),
    reraise=True,
)
def _write_to_lance(self, table: pa.Table, table_name: str) -> None:
    ...
```

```python
# ❌ Wrong: Bare except swallowing exceptions
def write_to_lance(self, table, table_name):
    try:
        lance.write_dataset(table, table_name)
    except:  # Prohibited
        pass
```

**Pipeline Execution Pattern:**

```python
# ✅ Correct: Declarative Config + explicit steps + returns Pydantic model
pipeline = IngestPipeline(
    source=S3Source(bucket="data", prefix="docs/"),
    filters=[TextLengthFilter(min_chars=10)],
    embed=True,
)
result = pipeline.run()  # IngestResult

# ❌ Wrong: Implicit steps, no return value
ingest_data("data/docs/", filters=True, embed=True)
```

**Arrow Zero-Copy Verification Pattern:**

```python
def assert_zero_copy(source_buf: pa.Buffer, target_buf: pa.Buffer) -> None:
    """Verify Arrow shared memory across component boundaries.

    Zero-copy definition: Data passes between components via shared memory
    buffer references rather than copies. Verification method varies by boundary:
    - Lance→DuckDB: DuckDB Arrow scanner uses shared memory
    - Lance→Daft: Daft creates references from Arrow IPC, not copies
    - Lance→PyTorch: pin_memory + CUDA async DMA
    """
    if source_buf is None or target_buf is None:
        return
    src_addr = source_buf.address
    tgt_addr = target_buf.address
    assert src_addr == tgt_addr, (
        f"Zero-copy violation: source=0x{src_addr:x}, "
        f"target=0x{tgt_addr:x}"
    )
```

### Enforcement Guidelines

**All AI Agents must:**

1. Follow suffix conventions for Ray Actor / Flow / Pydantic classes (`Actor`, `Flow`)
2. Use snake_case for Arrow Schema column names; new columns nullable
3. Use custom exception hierarchy (`ArrowLakeError` subclasses); bare `Exception` prohibited
4. Use JSON + `structlog` + `correlation_id` for logging; `print()` prohibited
5. Organize tests in three tiers: `tests/unit/`, `tests/integration/`, `tests/e2e/`
6. Use YAML + Pydantic Settings for configuration; `.json` configuration files prohibited
7. Follow `arrow_lake_{domain}_{metric}_{unit}` format for Prometheus metrics
8. Actor returns Arrow Table or Pydantic model; never return raw dict
9. Use `.remote()` for external methods; `_` prefix for internal methods
10. Metaflow Flows are self-contained; `python flows/{name}_flow.py run` independently runnable

**Enforcement Methods:**
- CI gates: Ruff (lint) + MyPy (type check) + pytest (three-tier tests)
- PR review checklist includes naming/structure/format checks
- `conftest.py` shared fixtures ensure test consistency

## Project Structure & Boundaries

### Complete Project Directory Structure

```
arrow-lake/                           # Project root
├── pyproject.toml                    # uv project config + dependency declaration
├── uv.lock                           # Lock file (auto-generated)
├── .python-version                   # Python version pinning
├── ruff.toml                         # Ruff lint + format config
├── mypy.ini                          # MyPy type check config
├── .pre-commit-config.yaml           # pre-commit hooks
├── .env.example                      # Environment variable template
├── .gitignore
├── CLAUDE.md                         # AI Agent instructions
│
├── arrow_lake/                       # ====== Main Package ======
│   ├── __init__.py                   # Public API: ArrowLakeClient
│   ├── _version.py                   # Version number (single source of truth)
│   ├── config.py                     # Pydantic Settings (four-layer overlay)
│   ├── exceptions.py                 # Exception hierarchy definition
│   ├── metrics.py                    # Prometheus metrics registration + definitions
│   │
│   ├── catalog/                      # --- Catalog Module ---
│   │   ├── __init__.py
│   │   ├── actor.py                  # CatalogActor (Ray Actor)
│   │   ├── schema.py                 # Pydantic → Arrow Schema conversion
│   │   ├── connection_pool.py        # DuckDB WAL connection pool
│   │   └── models.py                 # Table metadata Pydantic models
│   │
│   ├── ingest/                       # --- Ingestion Module ---
│   │   ├── __init__.py
│   │   ├── pipeline.py               # IngestPipeline (Declarative)
│   │   ├── models.py                 # IngestConfig, IngestResult
│   │   ├── sources/                  # Data source connectors
│   │   │   ├── __init__.py
│   │   │   ├── base.py               # DataSource abstract base class
│   │   │   ├── local.py              # Local filesystem
│   │   │   └── s3.py                 # S3 / MinIO
│   │   └── validators.py             # Ingestion-time Schema validation
│   │
│   ├── quality/                      # --- Quality Filtering Module ---
│   │   ├── __init__.py
│   │   ├── base.py                   # QualityFilter abstract interface
│   │   ├── builtin.py                # TextLengthFilter + ImageResolutionFilter
│   │   ├── dead_letter.py            # Dead-letter Lance table write
│   │   └── models.py                 # QualityReport Pydantic model
│   │
│   ├── embedding/                    # --- Embedding Module ---
│   │   ├── __init__.py
│   │   ├── encoder.py                # EmbeddingEncoder (pluggable)
│   │   ├── manager.py                # Index build + incremental update
│   │   └── models.py                 # EmbeddingConfig, IndexSpec
│   │
│   ├── query/                        # --- Query Module ---
│   │   ├── __init__.py
│   │   ├── engine.py                 # QueryEngine (5 SQL mode routing)
│   │   ├── vector.py                 # Vector search (IVF_PQ)
│   │   ├── fts.py                    # Full-text search (Tantivy)
│   │   ├── hybrid.py                 # Hybrid query + RRF fusion
│   │   └── models.py                 # SearchResult, QueryConfig
│   │
│   ├── ray_runtime/                  # --- Ray Runtime ---
│   │   ├── __init__.py
│   │   ├── placement.py              # Placement Group creation + management
│   │   ├── cache.py                  # Object Store cache wrapper (LRU + TTL)
│   │   └── health.py                 # Actor health check
│   │
│   └── sdk/                          # --- SDK Public API ---
│       ├── __init__.py
│       ├── client.py                 # ArrowLakeClient main entry point
│       ├── table.py                  # TableHandle (Fluent Builder)
│       └── search.py                 # SearchBuilder (Fluent chained queries)
│
├── flows/                            # ====== Metaflow Flows ======
│   ├── __init__.py
│   ├── ingest_flow.py                # Ingestion pipeline Flow
│   ├── embedding_flow.py             # Embedding pipeline Flow
│   └── search_flow.py                # Search pipeline Flow
│
├── tests/                            # ====== Tests ======
│   ├── conftest.py                   # Shared fixtures
│   ├── unit/
│   │   ├── test_config.py
│   │   ├── test_exceptions.py
│   │   ├── test_connection_pool.py
│   │   ├── test_schema_conversion.py
│   │   ├── test_quality_filters.py
│   │   ├── test_pipeline.py
│   │   ├── test_encoder.py
│   │   ├── test_query_engine.py
│   │   ├── test_cache.py
│   │   └── test_sdk_client.py
│   ├── integration/
│   │   ├── test_boundary_lance_daft.py
│   │   ├── test_boundary_daft_duckdb.py
│   │   ├── test_boundary_duckdb_pytorch.py
│   │   ├── test_boundary_cpu_gpu.py
│   │   ├── test_boundary_ray_object_store.py
│   │   └── test_boundary_cudf_arrow.py
│   ├── e2e/
│   │   ├── test_full_pipeline.py     # 4-step pipeline
│   │   └── test_ttv.py              # TTV automated verification
│   └── fixtures/
│       ├── sample_arrow_data.py
│       └── sample_lance_dataset.py
│
├── configs/                          # ====== Configuration Files ======
│   ├── dev.yaml
│   ├── staging.yaml
│   └── prod.yaml
│
├── deploy/                           # ====== Deployment ======
│   ├── docker/
│   │   └── Dockerfile
│   ├── compose/
│   │   ├── docker-compose.yml
│   │   ├── docker-compose.gpu.yml    # GPU overlay
│   │   └── prometheus.yml
│   └── helm/
│       └── arrow-lake/
│           ├── Chart.yaml
│           ├── values.yaml
│           ├── values-dev.yaml
│           └── templates/
│               ├── deployment.yaml
│               ├── service.yaml
│               ├── networkpolicy.yaml
│               └── prometheusrule.yaml
│
├── .github/                          # ====== CI/CD ======
│   └── workflows/
│       ├── ci.yml                    # PR gate
│       ├── gpu-tests.yml             # Nightly + manual GPU tests
│       └── release.yml               # Tag-triggered release
│
└── docs/                             # ====== Documentation ======
    ├── architecture.md
    └── examples/
        ├── quickstart.ipynb
        └── hybrid_search.ipynb
```

### Architectural Boundaries

**Component Boundary Layers:**

```
┌─────────────────────────────────────────────────┐
│                  SDK Layer                       │
│  ArrowLakeClient → TableHandle → SearchBuilder   │
├──────────────────┬──────────────────────────────┤
│  CatalogActor    │  QueryEngine                  │
│  (Ray Actor)     │  (non-Actor, synchronous)     │
├──────────────────┼──────────────────────────────┤
│                  │  VectorSearch / FTSSearch      │
│  ConnectionPool  │  HybridFusion                  │
│  (DuckDB WAL)    │                                │
├──────────────────┴──────────────────────────────┤
│            Ray Runtime Layer                     │
│  PlacementGroup / ObjectStore Cache / Health     │
├─────────────────────────────────────────────────┤
│            Storage Layer (Lance)                 │
│  Tables / Indexes / Versions / Dead-letter       │
└─────────────────────────────────────────────────┘
```

**Boundary Rules:**
- SDK layer does not directly operate Lance API — goes through CatalogActor or QueryEngine
- QueryEngine does not depend on Ray — synchronous execution; OLAP queries through Daft SQL (primary path) or DuckDB (Catalog queries) + Lance calls
- CatalogActor is the sole entry point for writing to the Catalog
- Ray Runtime layer is transparently used by upper layers; does not expose Ray API to SDK users

**Data Boundaries:**

| Boundary | Data Format | Verification Method |
|------|---------|---------|
| SDK → CatalogActor | Pydantic model | `model_validate()` |
| CatalogActor → Lance | `pa.Table` | `assert_zero_copy()` |
| Lance → Daft | Arrow IPC | Daft array reference verification |
| Daft → DuckDB | Arrow RecordBatch | Shared memory buffer verification (secondary path: catalog-only, not main analysis chain) |
| DuckDB → PyTorch | Arrow → Tensor | `pin_memory` + CUDA async DMA verification (secondary path: catalog-only) |
| Metaflow → Ray Actor | Ray serialized | Custom exception propagation |

**External Integration Boundaries:**

| Integration | Entry File | Communication Method |
|------|---------|---------|
| Prometheus | `arrow_lake/metrics.py` | HTTP `/metrics` |
| S3 / MinIO | `arrow_lake/ingest/sources/s3.py` | boto3 / S3 API |
| Metaflow | `flows/*.py` | Python import + `@project` |
| Ray Dashboard | Built-in | HTTP `:8265` |

### Requirements to Structure Mapping

| FR Category | Requirements | Implementation Location |
|---------|------|---------|
| **Ingestion** | F-ING-01~09 | `arrow_lake/ingest/` + `flows/ingest_flow.py` |
| | Data source connectors | `ingest/sources/{local,s3}.py` |
| | Schema validation | `ingest/validators.py` |
| | Deduplication | `ingest/pipeline.py` |
| **Processing** | F-PROC-01~09 | `arrow_lake/embedding/` + `arrow_lake/quality/` |
| | Quality filtering | `quality/base.py`, `quality/builtin.py` |
| | Dead-letter | `quality/dead_letter.py` |
| | Embedding computation | `embedding/encoder.py` |
| | Ray distributed | `ray_runtime/placement.py` |
| **Storage** | F-STOR-01~08 | `arrow_lake/catalog/` + Lance API |
| | Table management | `catalog/actor.py` |
| | Version management | `catalog/actor.py` |
| | Compact | `catalog/actor.py` |
| **Query** | F-QRY-01~08 | `arrow_lake/query/` |
| | Vector search | `query/vector.py` |
| | Full-text search | `query/fts.py` |
| | Hybrid query | `query/hybrid.py` |
| | 5 SQL modes | `query/engine.py` |
| **Catalog** | F-CAT-01~05 | `arrow_lake/catalog/` |
| | Connection pool | `catalog/connection_pool.py` |
| | Schema conversion | `catalog/schema.py` |
| **Orchestration** | F-ORCH-01~09 | `flows/` + `arrow_lake/config.py` |
| | Metaflow Flows | `flows/{ingest,embedding,search}_flow.py` |
| | @schedule | Metaflow `@schedule` decorator |
| **DevEx** | F-DEV-01~07 | `arrow_lake/sdk/` + `configs/` |
| | SDK entry point | `sdk/client.py` |
| | Fluent queries | `sdk/table.py`, `sdk/search.py` |
| **Quality** | F-QUA-01~05 | `arrow_lake/quality/` |
| **Observability** | F-OBS-01~06 | `arrow_lake/metrics.py` |

### Cross-Cutting Concerns Locations

| Concern | Implementation Location | Cross-Module Impact |
|--------|---------|-----------|
| Arrow zero-copy discipline | `tests/integration/test_boundary_*.py` | All 6 Arrow boundaries |
| Configuration management (four-layer overlay) | `arrow_lake/config.py` | All modules via `Settings` injection |
| Structured logging (JSON + correlation_id) | `arrow_lake/config.py` (structlog configuration) | All modules unified logger |
| Cost tracking | `arrow_lake/metrics.py` + `ray_runtime/` | Ray annotations + Prometheus metrics |
| Schema evolution | `arrow_lake/catalog/schema.py` | Catalog + Ingest + Query |
| Exception hierarchy | `arrow_lake/exceptions.py` | All modules |
| Prometheus metrics | `arrow_lake/metrics.py` | Ingest + Embedding + Query + Catalog |

### Integration Points

**Internal Communication Flow:**

```
User Python Code
    │
    ▼
ArrowLakeClient (sdk/client.py)
    │
    ├─→ TableHandle.create() ──→ CatalogActor.create_table.remote()
    │                              └─→ ConnectionPool (write) → Lance
    │
    ├─→ TableHandle.ingest() ──→ IngestPipeline.run()
    │                              ├─→ DataSource.read() → pa.Table
    │                              ├─→ QualityFilter.filter() → pa.Table
    │                              ├─→ CatalogActor.append.remote() → Lance
    │                              └─→ IngestResult (Pydantic)
    │
    ├─→ TableHandle.search() ──→ SearchBuilder.vector().to_arrow()
    │                              └─→ QueryEngine.execute()
    │                                  ├─→ VectorSearch (IVF_PQ)
    │                                  ├─→ FTSSearch (Tantivy)
    │                                  └─→ HybridFusion (RRF) → pa.Table
    │
    └─→ EmbeddingFlow.run() ──→ Metaflow orchestrates
                                  ├─→ Ray Actor (encoder) on Placement Group
                                  └─→ CatalogActor.create_index.remote()
```

**External Integration Flow:**

```
Prometheus ←── HTTP /metrics ─── metrics.py (prometheus_client)

S3/MinIO  ←── boto3 ─── ingest/sources/s3.py

Metaflow CLI ──→ python flows/ingest_flow.py run
                    └─→ @project → configs/{env}.yaml → Settings
```

## Architecture Validation Results

### Coherence Validation ✅

**Decision Compatibility:**
- DARMU Stack (Daft + Argo + Ray + Metaflow + uv) is fully Arrow-native compatible across the entire chain
- Ray Placement Group + Object Store satisfy zero-copy prerequisites
- DuckDB WAL connection pool (Catalog-only) + Daft SQL (OLAP primary path) + Lance Extension read-write separation have no conflicts
- Pydantic-first Schema → Arrow type mapping is compatible with Lance columnar storage
- Hybrid SDK (Fluent synchronous + Declarative orchestration) is coordinated with Ray Actor + Metaflow
- Docker Compose → Mini Cluster → Helm Chart evolution path is clear
- Resolved tensions: cuDF→Arrow controlled copy point (recorded in FR conflict table), QueryEngine synchronous vs CatalogActor asynchronous (SDK layer coordination)

**Pattern Consistency:**
- Naming conventions (Actor/Flow suffix, snake_case column names) align with Ray and Metaflow conventions
- JSON + correlation_id logging aligns with Metaflow `run_id`
- Prometheus `arrow_lake_{domain}_{metric}_{unit}` covers all 15 metrics
- 10 mandatory rules span all modules

**Structure Alignment:**
- Package organization by functional domain fully corresponds to FR category mapping
- Three-tier test directory structure matches test strategy layering
- `flows/` package is independent outside main package, following Metaflow Flow self-contained best practice

### Requirements Coverage Validation ✅

**Functional Requirements Coverage: 68/68 (100%)**

> **FR Source Note:** The original PRD defined 57 FRs (F-ING-01~09, F-PROC-01~09, F-STOR-01~08, F-QRY-01~08, F-CAT-01~05, F-ORCH-01~04/05a/05b/05c/06~09, F-DEV-01~07). This architecture adds 11 derived FRs via ADR-02 (F-QUA-01~05 quality control + F-OBS-01~06 observability). Total: 68.

| FR Category | Count | Coverage | Notes |
|---------|------|------|------|
| Ingestion (F-ING-01~09) | 9 | ✅ | |
| Processing (F-PROC-01~09) | 9 | ✅ | |
| Storage (F-STOR-01~08) | 8 | ✅ | |
| Query (F-QRY-01~08) | 8 | ⚠️ | F-QRY-01 HNSW strategy degraded to IVF_PQ (see H3); F-QRY-05 streaming results deferred to Phase 2 (see H4) |
| Catalog (F-CAT-01~05) | 5 | ✅ | |
| Orchestration (F-ORCH-01~09) | 9 | ⚠️ | F-ORCH-09 event sourcing deferred to Phase 2 (see H5) |
| DevEx (F-DEV-01~07) | 7 | ✅ | |
| Quality (F-QUA-01~05) | 5 | ✅ | ADR-02 addition |
| Observability (F-OBS-01~06) | 6 | ✅ | ADR-02 addition |

**Known Coverage Gaps (Phase 2 Supplements):**
- **H3 (F-QRY-01):** PRD defined adaptive strategy: HNSW (<1M rows) + IVF_PQ (1M+ rows). MVP uniformly uses IVF_PQ (Lance built-in); latency for <1M rows may be slightly worse than HNSW but acceptable. Phase 2 considers adding Lance HNSW support.
- **H4 (F-QRY-05):** Streaming results (`fetch_record_batch_reader`) require constant memory. MVP all queries return complete `pa.Table`. **Input-side optimized (2026-04-15):** `LanceStorageManager.scan_dataset()` returns `RecordBatchReader` for streaming reads, avoiding full materialization. `OlapSearchBridge` auto-detects JOIN/subquery patterns and falls back. Output-side streaming still deferred to Phase 2.
- **H5 (F-ORCH-09):** Event sourcing / audit log. MVP records operation logs via structlog + correlation_id. Phase 2 introduces immutable event storage.

**Non-Functional Requirements Coverage: All 7/7 Domains Covered**

| NFR | Core Constraint | Architecture Support |
|-----|---------|---------|
| Performance | Vector <10ms, zero-copy >90% | IVF_PQ + 5-level lazy eval + Arrow Buffer verification |
| Reliability | Auto-recovery >95% | tenacity retry + Lance version rollback |
| Scalability | Scaling <5min | Ray AutoScale v2 + Spot GPU |
| Cost | <$500/month | Elastic Burst $440/mo + ResourceQuota |
| Usability | Onboarding <30min | Docker Compose TTV <45min + Hybrid SDK |
| Security | Data encryption | TLS + EBS + Docker network isolation |
| Observability | 17 metrics | Prometheus + structlog |

### Implementation Readiness Validation ✅

**Decision Completeness:**
- All 20 architectural decisions have conclusions, version constraints, rationale, and impact analysis
- 6 deferred decisions have Earliest Phase markers
- 2 ADRs (Catalog architecture + MVP P0 supplement) have complete debate records

**Structure Completeness:**
- ~60 files/directories explicitly defined, with module responsibility descriptions
- 6 Arrow boundary test files correspond to 6 data boundaries
- FR → file mapping table covers all 68 requirements

**Pattern Completeness:**
- 18 AI Agent conflict points covering 5 categories (naming/structure/format/communication/process)
- 10 mandatory enforcement rules + correct/incorrect example comparisons
- Zero-copy verification pattern (`assert_zero_copy`) + pipeline execution pattern + error handling pattern

### Gap Analysis Results

**Critical Gaps: None**

**Important Gaps (2 items, not blocking implementation):**

| # | Gap | Resolution |
|---|------|---------|
| G1 | Auxiliary library versions not listed in dependency matrix | Define in `pyproject.toml` during implementation (structlog, tenacity, pydantic, boto3) |
| G2 | Dead-letter table naming | Confirmed: `{table_name}_dead_letter` (per-table independent directory) |

**Nice-to-Have Gaps (3 items, Phase 2 supplements):**

| # | Gap | Description |
|---|------|------|
| G3 | Arrow Schema version migration strategy | Lance schema evolution migration script template |
| G4 | Grafana Dashboard template | Pre-configured monitoring panel JSON |
| G5 | `@schedule` cleanup Cron expression | Version cleanup scheduling strategy |

### Architecture Completeness Checklist

**✅ Requirements Analysis**
- [x] Comprehensive project context analysis (57 FR from PRD + 27 NFR + 11 ADR-02 derived FR)
- [x] Scale and complexity assessment (Medium, ~15 components)
- [x] Technical constraint identification (6 iron laws)
- [x] Cross-component concern mapping (5 items)
- [x] Risk identification and assessment (R1-R9)

**✅ Architectural Decisions**
- [x] 2 ADRs completed (Catalog architecture + MVP P0 supplement)
- [x] 20 decisions recorded (data/API/infrastructure/security)
- [x] Tech stack version validation (Daft >= 0.7.8 etc.; Step 3 partial validation deferred to pre-implementation)
- [x] FR conflict identification and resolution (4 items)
- [x] Priority adjustments (5 items elevated to P0, 4 items deferred to P1)

**✅ Implementation Patterns**
- [x] Naming conventions established (7 naming rule categories)
- [x] Structure patterns defined (package organization + test organization)
- [x] Format patterns specified (Arrow Schema + logging + configuration)
- [x] Communication patterns prescribed (Actor + Metaflow)
- [x] Process patterns documented (errors + pipeline + zero-copy verification)
- [x] 10 mandatory rules + enforcement methods

**✅ Project Structure**
- [x] Complete directory structure definition (~60 files)
- [x] Component boundaries established (5-layer architecture)
- [x] Integration point mapping (6 data boundaries + 4 external integrations)
- [x] FR → file mapping complete (68/68, including 11 architecture-derived FRs)
- [x] Cross-component concern locations

**✅ Validation**
- [x] Coherence validation passed
- [x] Requirements coverage 100%
- [x] Implementation readiness confirmed
- [x] Gap analysis complete (0 Critical, 2 Important)

### Architecture Readiness Assessment

**Overall Status: READY FOR IMPLEMENTATION**

**Confidence Level: HIGH** — based on:
- 50 deep brainstorming sessions covering 10 dimensions
- 2 complete ADR debates
- 68 FRs fully mapped
- 20 architectural decisions + 18 conflict points resolved
- 6 Arrow zero-copy boundary test strategies

**Key Strengths:**
1. Arrow zero-copy full-chain design has quantitative verification methods (Buffer address comparison)
2. 5-level Lazy Evaluation covers all optimization points from storage to query
3. Catalog connection pool model (ADR-01) resolves the core contradiction between singleton and high QPS; Daft SQL as primary OLAP engine decouples analytics workload from Catalog metadata
4. MVP P0 quality control + observability supplement (ADR-02) ensures end-to-end verifiability
5. Progressive complexity 5-level API guarantees zero code changes from local to K8s

**Areas for Future Enhancement:**
1. Multi-tenant isolation (Phase 2, Ravine 2)
2. Frontend data browser (Phase 2)
3. Arrow Schema migration tooling (Phase 2)
4. GPU cost automated control closed loop (ResourceQuota + automatic degradation)

**Performance Baseline Note:**
The "<10ms vector search latency" baseline defined by NF-PERF-01 needs recalibration. The original PRD was designed around HNSW indexing; MVP uniformly adopts IVF_PQ indexing. IVF_PQ latency may be slightly higher than HNSW in <1M row scenarios, but IVF_PQ has clear advantages in 1M+ row scenarios. Recommended to re-establish P50/P99 baselines with real data after implementation.

**Cost Estimate Note:**
$440/month Elastic Burst is a rough estimate, assuming AWS us-east-1, Spot GPU instances. Breakdown: 2x T4 Spot ~$200 + 32 vCPU Spot ~$120 + storage ~$60 + Argo/Prometheus ~$60 = ~$440. Actual costs depend on usage patterns and region. Should be calibrated against actual usage after implementation.

**Deferred Decisions Requiring Pre-Implementation Resolution:**
| Decision | Impact | Recommended Timing |
|------|------|---------|
| HNSW vs IVF_PQ strategy | F-QRY-01 P0 | Confirm MVP uses only IVF_PQ, accept slightly increased latency for small datasets |
| Streaming results interface | F-QRY-05 P0 | Confirm MVP returns `pa.Table`, streaming deferred to Phase 2 |
| @schedule cron expression | F-ORCH-06 P0 | Accept MVP manual trigger for cleanup, cron in Phase 2 |
| Arrow Schema migration strategy | F-PROC-07 P0 | Accept MVP uses only `add_columns`, `alter` migration in Phase 2 |
| Auxiliary library exact versions | All modules | Deferred to unified validation during `pyproject.toml` definition |

### Implementation Handoff

**AI Agent Guidelines:**
1. Follow all architectural decisions in this document; do not improvise independently
2. Strictly follow the 10 mandatory rules (naming/format/logging/testing etc.)
3. Respect component boundaries — SDK layer must not directly operate Lance API
4. All Arrow boundaries must be verified through `assert_zero_copy()`
5. PR review checklist references this document's Enforcement Guidelines

**First Implementation Priority:**
1. `arrow_lake/config.py` + `arrow_lake/exceptions.py` — Infrastructure
2. `arrow_lake/catalog/connection_pool.py` — DuckDB WAL connection pool (Catalog-only, Story 1.2 Spike validation)
3. `arrow_lake/catalog/actor.py` — CatalogActor (Ray Actor, supports namespace parameter for future multi-tenant isolation)
4. `arrow_lake/sdk/client.py` — ArrowLakeClient entry point
5. Zero-copy boundary verification (6 integration tests, using component boundary verification rather than simple address comparison)

> **Architecture Decision Update (2026-04-12):** DuckDB's role has been redefined from OLAP + Catalog to Catalog-only storage. Daft SQL has been promoted to the primary OLAP engine (referencing CloudKitchens DREAM stack and ByteDance Volcano Engine production practices). This change is reflected in the 80 stories in epics.md, with key impacts on Story 1.2 (Spike validation), Story 1.6 (connection pool simplification), and Story 7.6 (dual SQL interface).
