---
project_name: 'arrow-lake'
user_name: 'Witshine'
date: '2026-04-13'
sections_completed:
  ['technology_stack', 'language_rules', 'framework_rules', 'testing_rules', 'quality_rules', 'workflow_rules', 'anti_patterns']
status: 'complete'
rule_count: 45
optimized_for_llm: true
language: 'en'
---

# Project Context for AI Agents

_This file contains critical rules and patterns that AI agents must follow when implementing code in this project. Focus on unobvious details that agents might otherwise miss._

**Documentation Language:** English (primary), Chinese (`*-zh.md`) as supplementary reference.

**Planning Documents:**
- PRD: `_bmad-output/planning-artifacts/prd.md`
- Architecture: `_bmad-output/planning-artifacts/architecture.md`
- System Design: `_bmad-output/planning-artifacts/system_design.md`
- Epics & Stories (80 stories): `_bmad-output/planning-artifacts/epics.md`
- Implementation Readiness: `_bmad-output/planning-artifacts/implementation-readiness-report-2026-04-11.md`

---

## Technology Stack & Versions

### Core Stack (DARMU)

| Component | Technology | Version | Role |
|-----------|-----------|---------|------|
| D | Daft | >= 0.7.8 | Primary OLAP engine, multimodal DataFrame (Rust kernel) |
| A | Argo Workflows | >= 3.5 | Workflow engine on K8s (production) |
| R | Ray | >= 2.54.1 | Distributed computing (Data/Serve/Actor/ObjectStore) |
| M | Metaflow | >= 2.19.22 | User-facing workflow orchestration |
| U | uv | latest | Python dependency management |

### Extension Layer

| Component | Version | Role |
|-----------|---------|------|
| Lance | >= 4.0.0 | Multimodal columnar storage, vector index, versioning |
| DuckDB | >= 1.5.1 | **Catalog metadata storage ONLY** (NOT OLAP) |
| NeMo Curator | >= 1.1.0 | Data quality scoring, dedup, GPU acceleration |
| Ray Serve | latest | Model serving, autoscaling, GPU management |

### Key Dependencies

| Dependency | Version | Purpose |
|-----------|---------|---------|
| Pydantic | v2 | Schema definitions, Settings, API models |
| structlog | latest | JSON structured logging with correlation_id |
| tenacity | latest | Retry logic with exponential backoff |
| boto3 | latest | S3/MinIO interaction |
| prometheus-client | latest | /metrics endpoint |
| Tantivy | via Lance | Full-text search |
| PyAV | latest | Video keyframe extraction |
| SentenceTransformers | latest | Text embedding (HuggingFace local) |
| torch | latest | Tensor operations, pin_memory, CUDA |

### Infrastructure

| Environment | Object Storage | Orchestration | GPU | Monitoring |
|-------------|---------------|---------------|-----|------------|
| Dev | MinIO (Docker) | Docker Compose | Local (optional) | CLI only |
| Staging | MinIO (SSH) | Ray SSH (3-4 nodes) | Spot GPU (1-2x) | Prometheus + Grafana |
| Production | AWS S3 | K8s + KubeRay | KubeRay GPU nodes | Prometheus + Grafana |

### Version Pinning Strategy

- **Core stack (DARMU + Lance + DuckDB + NeMo Curator):** `>=` denotes minimum verified version. Story 1.2 Spike produces an **exact pin document** (`docs/tech-compatibility.md`) with fixed versions (e.g., `daft==0.7.8`, `lancedb==4.0.0`). `pyproject.toml` must use exact pins for all core components post-spike.
- **Auxiliary libraries (structlog, tenacity, boto3, etc.):** `latest` or `>=` is acceptable. Pin only if a specific bugfix or compatibility issue requires it.
- **PyArrow:** Must be pinned to the exact version bundled by Daft (verified in Story 1.2). Do NOT use version ranges — Arrow ABI changes cause silent zero-copy breakage.

---

## Critical Implementation Rules

### Language-Specific Rules (Python)

1. **Python 3.11+** — managed by `uv`, version pinned in `.python-version`
2. **Type hints required** on all public functions and class methods
3. **No `print()` or bare `logging`** — use `structlog` exclusively with JSON format
4. **Pydantic v2** for all data models — use `model_validate()`, not `parse_obj()`
5. **Arrow types via PyArrow** — `pa.string()`, `pa.float32()`, `pa.list_(pa.float32(), dim)`

### Architecture-Specific Rules

6. **DuckDB for Catalog metadata + OLAP SQL** — DuckDB embedded in CatalogActor for metadata storage. DuckDB also executes OLAP SQL queries via Arrow zero-copy register (see ADR-05). Long-term target: migrate to Daft SQL when `df.sql()` becomes available. Daft programming API (groupby/agg) is preferred for non-SQL use cases.
7. **Arrow zero-copy is an iron law** — All component boundaries (Lance→Daft, Lance→DuckDB, Lance→PyTorch) must share Arrow memory buffers. Intermediate serialization is a bug.
8. **Ray Placement Group required** — CPU/GPU workers must be co-located on the same node. Cross-node Object Store access degrades 100-500x.
9. **Catalog Actor is singleton** — Ray Named Actor with `resources={"catalog": 1}`. Only route for table metadata operations.
10. **QueryEngine is NOT a Ray Actor** — Synchronous class. OLAP and catalog queries via DuckDB SQL (see ADR-05).
11. **Connection pool: 4 read + 1 write** — Catalog-only workload sizing. Do NOT use 8 read connections (that was the pre-demotion number).
12. **Lance Fragment size: 128-512MB** — Monitor and auto-compact if out of range.
13. **Version cleanup on schedule** — Use Metaflow `@schedule` for periodic version cleanup. `production` tag permanently retained.
14. **GPU cost hard cap** — namespace `ResourceQuota` + Prometheus budget alerts.
15. **Schema evolution: `add_columns` preferred** — Zero-cost vs `alter_columns` (requires rewrite). New columns must be nullable.

### Naming Conventions

16. **Ray Actor classes** — PascalCase + `Actor` suffix: `CatalogActor`
17. **Metaflow Flow classes** — PascalCase + `Flow` suffix: `IngestFlow`
18. **Pydantic models** — PascalCase + semantic suffix: `TableSchema`, `IngestConfig`, `QualityReport`
19. **SDK public methods** — snake_case: `create_table()`, `list_tables()`
20. **Lance table names** — snake_case plural: `user_documents`, `embedding_models`
21. **Lance column names** — snake_case: `text_content`, `embedding_vector`
22. **Constants** — UPPER_SNAKE_CASE: `DEFAULT_CACHE_TTL`, `MAX_FRAGMENT_SIZE_MB`
23. **Private methods** — single underscore prefix: `_validate_schema()`
24. **Prometheus metrics** — `arrow_lake_{domain}_{metric}_{unit}`: `arrow_lake_ingestion_rows_total`
25. **Metadata columns** — underscore prefix: `_source_url`, `_ingested_at`, `_quality_score`

### Package Organization

```
arrow_lake/
├── __init__.py           # SDK entry: ArrowLakeClient
├── config.py             # Pydantic Settings (4-layer override)
├── exceptions.py         # ArrowLakeError hierarchy
├── metrics.py            # Prometheus metric definitions
├── catalog/              # Catalog module
├── ingest/               # Ingestion module (pipeline, sources, validators)
├── quality/              # Quality filtering (filters, dead_letter)
├── embedding/            # Embedding (encoder, manager)
├── query/                # Query engine, vector, fts, hybrid
├── ray_runtime/          # Placement group, cache, health
└── sdk/                  # Public API (client, table, search)

flows/                    # Metaflow Flow definitions (outside main package)
tests/
├── unit/
├── integration/          # Arrow zero-copy boundary tests
├── e2e/
└── conftest.py
configs/                  # YAML configs (dev.yaml, staging.yaml, prod.yaml)
deploy/                   # Docker, Compose, Helm
```

### Error Handling Rules

26. **Custom exception hierarchy** — All exceptions inherit from `ArrowLakeError`. Subcategories: `IngestionError`, `QueryError`, `CatalogError`, `RayRuntimeError`. Never raise bare `Exception`.
27. **Retry with tenacity** — Spot Worker: 3 attempts, exponential 1-30s. Transient network: 5 attempts, exponential 0.5-10s. Non-retryable (schema validation): no retry, raise immediately.
28. **No bare `except:`** — Always specify exception type. Never silently swallow errors.
29. **Dead-letter protocol** — Rejected rows go to `{table_name}_dead_letter` Lance table with `_rejection_reason` column.

### Testing Rules

30. **3-tier test directory** — `tests/unit/`, `tests/integration/`, `tests/e2e/`
31. **Zero-copy boundary tests** — 6 integration tests for all Arrow boundaries: `test_boundary_lance_daft.py`, `test_boundary_daft_duckdb.py` (catalog-only path), `test_boundary_duckdb_pytorch.py` (catalog-only path), `test_boundary_cpu_gpu.py`, `test_boundary_ray_object_store.py`, `test_boundary_cudf_arrow.py`
32. **Test naming** — match module: `tests/unit/test_catalog_actor.py`
33. **CI gate (two-tier)** — **Basic CI** (Story 1.1): Ruff lint + MyPy strict + `pytest tests/unit/` (CPU only) per push/PR. **Advanced CI** (Story 7.14): GPU tests nightly, Helm chart validation on deploy PRs. Basic CI must be operational before Sprint 1 ends.
34. **Minimum 80% coverage** — enforced via CI.

### Configuration Rules

35. **4-layer config override** — Code defaults → `.env` → environment variables → Metaflow Config YAML
36. **Pydantic Settings** — Auto-merge all 4 layers. Fail fast on missing required values.
37. **YAML config only** — No `.json` config files. Keys in snake_case, values with unit suffixes (`_mb`, `_seconds`).
38. **Secrets** — MVP: `.env` + `.gitignore`. Production: environment variables. No hardcoded credentials.

### Code Quality Rules

39. **File size limit** — Max 800 lines per file. Extract modules if exceeded.
40. **Function size limit** — Max 50 lines per function. Split if exceeded.
41. **No deep nesting** — Max 4 levels. Use early returns.
42. **Actor return values** — Always return `pa.Table` or Pydantic model. Never return raw `dict`.
43. **Schema evolution: breaking changes require migration** — `add_columns` (zero-cost, nullable) for additive changes. `alter_columns` (rewrite) for type changes requires explicit migration step with version tag before and after. Never alter non-nullable columns without a migration plan.
44. **Catalog rate limiting** — Catalog Actor must enforce max 100 concurrent metadata operations. Reject excess requests with `CatalogError(error_code=CatalogError.RATE_LIMITED)`. Log `arrow_lake_catalog_rate_limited_total` on rejection.
45. **Connection pool deadlock prevention** — `DuckDBConnectionPool` must use `asyncio.Semaphore` with 30s timeout on connection acquisition. Never hold write connection during read operations. If write lock timeout triggers, log CRITICAL and abort the operation — do not queue indefinitely.

### Anti-Patterns (DO NOT)

- ❌ Using DuckDB for distributed OLAP — use Daft SQL for scale-out (see ADR-05)
- ❌ Reading Lance data without checking zero-copy at boundaries
- ❌ Creating all database tables upfront — create only what each story needs
- ❌ Using `print()` or `logging.info()` — use `structlog`
- ❌ Returning `dict` from Actor methods — return Pydantic model or `pa.Table`
- ❌ Using `.json` config files — use YAML
- ❌ Hardcoding secrets — use environment variables
- ❌ Skipping zero-copy boundary tests
- ❌ Forward story dependencies — each story must work based only on previous stories
- ❌ Initializing heavy resources (Ray, GPU) in module scope — use lazy initialization

---

## Usage Guidelines

### For AI Agents

1. Read this file first before implementing any code
2. Follow the 42 rules above — they are derived from architecture decisions and expert review
3. Cross-reference `architecture.md` for ADR details and `system_design.md` for component specs
4. When in doubt, check `epics.md` for the specific story acceptance criteria
5. All Arrow boundaries must be verified with `assert_zero_copy()` in integration tests

### For Humans

1. Update this file when architecture decisions change or new patterns are established
2. Review quarterly to keep rules current with implementation
3. Rule count should match `rule_count` in frontmatter
4. Cross-reference with architecture.md ADR sections for decision rationale

### Last Updated

2026-04-14 — ADR-05: DuckDB OLAP 偏差记录。Rule 6 更新为反映 DuckDB 同时用于 Catalog + OLAP SQL 的当前实现。长期目标仍为 Daft SQL 迁移。
2026-04-13 — Version pin strategy documented. ArrowCopyDetector added as Story 1.5 AC. Rules 43-45 added (schema migration, rate limiting, deadlock prevention). ADR-03 (Object Store sizing) and ADR-04 (Embedding serving) added to architecture.
