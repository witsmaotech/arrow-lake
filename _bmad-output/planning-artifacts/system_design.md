---
type: system_design
project_name: arrow-lake
author: Winston (System Architect) + Witshine
created: 2026-04-11
status: complete
reviewed: 2026-04-11
review_notes: |
  Review findings fixed:
  - 5 CRITICAL: IngestConfig type, missing import, DuckDB search_path, array_distance SQL, lancedb import
  - 10 HIGH: SearchBuilder.select(), ImageResFilter naming, shutdown_after_job_finishes,
    undefined alert metrics, missing Pydantic models, test coverage gaps, read_lance() path,
    custom resource registration, @schedule Phase 2 note
  - Remaining MEDIUM/LOW: deferred to implementation phase (arrow-copy-detector placement,
    version tagging spec, TableHandle constructor docs)
based_on:
  - _bmad-output/planning-artifacts/architecture.md
  - _bmad-output/planning-artifacts/prd.md
  - _bmad-output/brainstorming/appendix-deep-dives.md
---

# Arrow Lake — System Design Document

> This document is the implementation blueprint. An experienced Python engineer can implement any module from this document alone, without additional context.

---

## 1. System Overview

### 1.1 System Context (C4 Level 1)

```
                          ┌─────────────────┐
                          │   ML Engineer   │
                          │  (Python SDK)   │
                          └────────┬────────┘
                                   │
                                   ▼
┌──────────────────────────────────────────────────────┐
│                    Arrow Lake                        │
│                                                      │
│  ┌─────────┐  ┌──────────┐  ┌────────────────────┐  │
│  │  SDK    │  │ Catalog  │  │  Query Engine      │  │
│  │ Layer   │  │ Actor    │  │  (Daft SQL primary + DuckDB catalog)    │  │
│  └────┬────┘  └────┬─────┘  └────────┬───────────┘  │
│       │            │                  │               │
│  ┌────┴────────────┴──────────────────┴───────────┐  │
│  │              Ray Runtime                        │  │
│  │  (Placement Group + Object Store + Cache)      │  │
│  └────────────────────┬───────────────────────────┘  │
│                       │                              │
│  ┌────────────────────┴───────────────────────────┐  │
│  │              Lance Storage                     │  │
│  │  (Versioned Columnar + IVF_PQ + FTS)          │  │
│  └────────────────────────────────────────────────┘  │
│                                                      │
│  ┌──────────────────┐  ┌────────────────────────┐   │
│  │ Metaflow         │  │ Prometheus /metrics    │   │
│  │ Orchestration    │  │                        │   │
│  └──────────────────┘  └────────────────────────┘   │
└──────────────────────────────────────────────────────┘
           │                          │
           ▼                          ▼
    ┌────────────┐            ┌─────────────┐
    │ Argo Workflows│         │ Prometheus  │
    │ (K8s)       │            │ + Grafana   │
    └────────────┘            └─────────────┘
```

**Users:** ML Engineers interact exclusively through the Python SDK. No UI, no REST API (MVP).

**External Dependencies:**

| System | Purpose | Protocol |
|--------|---------|----------|
| Lance (local/S3) | Primary storage | Python API |
| DuckDB | Catalog metadata storage + Lance extension | Python API (embedded in Ray Actor) |
| Ray | Distributed runtime | Internal |
| Metaflow | Pipeline orchestration | Python API |
| Argo Workflows | K8s-native workflow engine | YAML / API |
| Prometheus | Metrics collection | HTTP scrape |
| S3 / MinIO | Object storage (optional) | S3 API (boto3) |

### 1.2 Core Design Principles

**Six Iron Rules (immutable):**

1. **Arrow zero-copy is law** — Every component boundary must output Arrow format. A copy/serialization at any boundary is an integration bug, not an architectural choice.
2. **Ray Placement Group is prerequisite for zero-copy** — CPU and GPU workers MUST be on the same node. Cross-node Object Store degrades 100-500x.
3. **Catalog Actor routes only, never analyzes** — Heavy queries bypass the Actor and hit the DuckDB connection pool directly.
4. **Lance Fragment size must be monitored** — 128MB-512MB optimal range. Auto `compact_files` after write.
5. **Version bloat requires active management** — `@schedule` periodic cleanup. `production` tag permanently retained.
6. **GPU cost requires hard cap** — Namespace ResourceQuota + Prometheus budget alert.

**First Principles (five ones):**

| Principle | Meaning |
|-----------|---------|
| One Format | Lance for all persistent data |
| One Memory | Arrow for all in-memory data |
| One Engine | Daft SQL (primary OLAP) + DuckDB (catalog metadata) + Lance (vector/FTS) |
| One Orchestrator | Metaflow for all pipelines |
| One Bridge | Ray Object Store for all cross-component data transfer |

### 1.3 Technology Stack Version Matrix

**Core Stack (DARMU):**

| Component | Version Constraint | Purpose |
|-----------|-------------------|---------|
| Python | >= 3.10, < 3.13 | Runtime |
| uv | >= 0.4.0 | Package manager |
| Daft | >= 0.7.8 | Lazy compute engine |
| Argo Workflows | >= 3.5 | K8s workflow engine |
| Ray | >= 2.54.1 | Distributed runtime |
| Metaflow | >= 2.19.22 | Pipeline orchestrator |

**Extension Layer:**

| Component | Version Constraint | Purpose |
|-----------|-------------------|---------|
| Lance | >= 4.0.0 | Versioned columnar storage |
| DuckDB | >= 1.5.1 | Catalog metadata storage |
| PyArrow | >= 15.0.0 | Arrow format (installed by Daft) |
| NeMo Curator | >= 1.1.0 | GPU-accelerated quality filtering |
| PyTorch | >= 2.2.0 | ML training framework |

**Auxiliary Libraries:**

| Component | Purpose |
|-----------|---------|
| Pydantic | >= 2.0 — Schema definition + Settings |
| structlog | JSON structured logging |
| tenacity | Retry with exponential backoff |
| prometheus-client | Metrics exposition |
| boto3 | S3 source connector |
| typer | CLI entry point (optional) |

**Dependency Risk Matrix:**

| Dependency | Risk | Mitigation |
|------------|------|------------|
| Lance | API changes may break zero-copy chain | Pin version + integration regression tests |
| Daft | Ray integration stability | Fallback: Daft standalone mode |
| DuckDB Lance Extension | Third-party extension maturity | Fallback: Daft SQL |
| Ray | GCS bottleneck, AutoScale v2 stability | Fallback: Redis event bus |
| NeMo Curator | NVIDIA GPU only | Fallback: CPU quality scoring |

---

## 2. Architecture Layers (C4 Level 2)

### 2.1 SDK Layer

**Responsibility:** User-facing API. Translates developer intent into internal operations.

**Components:**
- `ArrowLakeClient` — Entry point, lifecycle management
- `TableHandle` — Fluent builder for table operations
- `SearchBuilder` — Fluent builder for query operations

**Design Rules:**
- NEVER expose Ray, Lance, or DuckDB APIs directly
- All return types are `pa.Table` or Pydantic models (never raw dicts)
- Lazy evaluation: no I/O until `.execute()` or `.to_arrow()` is called

```
┌────────────────────────────────────────────┐
│                 SDK Layer                   │
│                                             │
│  ArrowLakeClient                            │
│  ├── .connect(storage_path)  → self        │
│  ├── .table(name)           → TableHandle  │
│  └── .list_tables()         → list[str]    │
│                                             │
│  TableHandle                                │
│  ├── .create(schema)        → TableHandle  │
│  ├── .ingest(source, ...)   → IngestResult │
│  ├── .search("query")       → SearchBuilder│
│  ├── .query(sql)            → pa.Table     │
│  ├── .versions()            → list[Version]│
│  └── .compact()             → CompactResult│
│                                             │
│  SearchBuilder                               │
│  ├── .vector(top_k=10)     → SearchBuilder │
│  ├── .fts(top_k=10)        → SearchBuilder │
│  ├── .hybrid(alpha=0.7)    → SearchBuilder │
│  ├── .filter(expr)         → SearchBuilder │
│  ├── .select(cols)         → SearchBuilder │
│  └── .to_arrow()           → pa.Table      │
└────────────────────────────────────────────┘
```

### 2.2 Service Layer

**Responsibility:** Business logic — catalog management, data ingestion, query execution.

**Components:**
- `CatalogActor` (Ray Actor) — Single source of truth for table metadata
- `QueryEngine` (synchronous) — Route and execute queries against Lance via DuckDB
- `IngestPipeline` (synchronous) — Declarative ingest workflow
- `QualityFilter` chain — Row-level quality gates
- `EmbeddingEncoder` — Pluggable model for vector embedding
- `IndexManager` — Index lifecycle (build, update, delete)

**Design Rules:**
- `CatalogActor` is the ONLY component that writes to the catalog metadata store
- `QueryEngine` does NOT depend on Ray — synchronous execution via Daft SQL (primary OLAP) and DuckDB (catalog queries) + Lance
- `IngestPipeline` composes filters, validators, and writers in a deterministic chain
- Quality filters execute serially (order matters for short-circuit optimization)

```
┌──────────────────────────────────────────────────┐
│                  Service Layer                     │
│                                                   │
│  ┌─────────────────┐    ┌────────────────────┐   │
│  │  CatalogActor   │    │   QueryEngine      │   │
│  │  (Ray Actor)    │    │   (synchronous)    │   │
│  │                 │    │                    │   │
│  │  create_table   │    │  route(mode)       │   │
│  │  append_data    │    │  ├→ vector()       │   │
│  │  get_metadata   │    │  ├→ fts()          │   │
│  │  create_index   │    │  ├→ hybrid()       │   │
│  │  list_versions  │    │  ├→ olap()         │   │
│  │  compact_files  │    │  └→ analytics()    │   │
│  │  cleanup_versions│    │                    │   │
│  └────────┬────────┘    └────────────────────┘   │
│           │                                       │
│  ┌────────┴──────────────────────────────────┐   │
│  │        DuckDB WAL Connection Pool          │   │
│  │  4 read connections + 1 write connection (catalog-only)  │   │
│  └───────────────────────────────────────────┘   │
│                                                   │
│  ┌─────────────────┐    ┌────────────────────┐   │
│  │ IngestPipeline  │    │ QualityFilter      │   │
│  │                 │    │ (chain pattern)     │   │
│  │  source.read()  │    │                    │   │
│  │  → validate()   │    │  TextLengthFilter  │   │
│  │  → dedup()      │    │  ImageResFilter    │   │ (abbr for ImageResolutionFilter)
│  │  → filter()     │    │  → dead_letter()   │   │
│  │  → write()      │    │                    │   │
│  └─────────────────┘    └────────────────────┘   │
└──────────────────────────────────────────────────┘
```

### 2.3 Ray Runtime Layer

**Responsibility:** Distributed execution, resource management, zero-copy data transfer.

**Components:**
- `PlacementManager` — Create and manage Ray Placement Groups
- `ObjectStoreCache` — LRU + TTL cache for Arrow data in Ray Object Store
- `HealthMonitor` — Actor health checks and auto-restart

**Design Rules:**
- CPU and GPU workers MUST be in the same Placement Group (zero-copy prerequisite)
- Object Store cache TTL default: 30 minutes
- Blob out-of-line threshold: 1MB
- `shutdown_after_job_finishes: true` for GPU workers (Ray option; KubeRay Helm uses `shutdownAfterJobFinishes`)

```
┌──────────────────────────────────────────────────┐
│               Ray Runtime Layer                   │
│                                                   │
│  PlacementManager                                 │
│  ├── create_pg(bundles=[{CPU,N}])               │
│  ├── get_current_pg() → PlacementGroup           │
│  └── teardown_pg()                                │
│                                                   │
│  ObjectStoreCache                                 │
│  ├── put(key, pa.Table)   → ObjectRef            │
│  ├── get(key)              → pa.Table (zero-copy) │
│  ├── evict(table_name)     → None                 │
│  └── _lru_ttl_evict()      → None (background)    │
│                                                   │
│  HealthMonitor                                    │
│  ├── check_actor(actor)   → HealthStatus          │
│  └── restart_unhealthy()   → None                 │
│                                                   │
│  ┌─────────────────────────────────────────┐     │
│  │         Ray Cluster Topology             │     │
│  │                                          │     │
│  │  Head Node                               │     │
│  │  ├── GCS (Global Control Store)         │     │
│  │  ├── CatalogActor                        │     │
│  │  ├── Dashboard (:8265)                   │     │
│  │  └── Metrics HTTP (:8000)                │     │
│  │                                          │     │
│  │  Worker Nodes (Placement Group)          │     │
│  │  ├── CPU Worker 1 ─┐                    │     │
│  │  ├── CPU Worker 2 ─┤ Same Node          │     │
│  │  ├── GPU Worker 1 ─┤ (zero-copy)        │     │
│  │  └── GPU Worker 2 ─┘                    │     │
│  └─────────────────────────────────────────┘     │
└──────────────────────────────────────────────────┘
```

### 2.4 Storage Layer

**Responsibility:** Persistent data — tables, indexes, versions, dead-letter records.

**Components:**
- Lance Dataset API — Read, write, version, compact, index
- Lance FTS (Tantivy) — Full-text search index
- Lance IVF_PQ — Vector similarity index

**Design Rules:**
- All data stored as Lance datasets (columnar, versioned)
- Fragment size: 128MB-512MB (auto-compact after write)
- New columns: `add_columns` (zero-cost, nullable) preferred over `alter_columns` (rewrite)
- Dead-letter tables: `{table_name}_dead_letter` (per-table independent directory)

**Storage Layout:**

```
<lance_base_path>/
├── user_documents/                    # Lance dataset directory
│   ├── .lance/
│   │   ├── _manifest                 # Fragment metadata
│   │   ├── _versions/                # Version history
│   │   └── _indices/
│   │       ├── text_content.ftz       # FTS index (Tantivy)
│   │       └── embedding_vector.ivf_pq # Vector index
│   ├── data/
│   │   ├── 00000000-0000-4000-8000-000000000000.lance  # Fragment 1
│   │   └── 00000000-0000-4000-8000-000000000001.lance  # Fragment 2
│   └── _deletions/                    # Soft-delete markers
│
├── user_documents_dead_letter/         # Dead-letter for rejected rows
│   └── ...                             # Same Lance structure
│
├── _catalog/                           # Catalog metadata (DuckDB WAL)
│   ├── catalog.db                      # Main catalog (DuckDB)
│   └── catalog.db.wal                  # Write-ahead log
│
└── _system/                            # Internal state
    └── _locks/                         # Distributed locks (if needed)
```

### 2.5 Cross-Cutting: Configuration, Logging, Metrics

**Configuration (`arrow_lake/config.py`):**

Four-layer override chain (later overrides earlier):

```
Code defaults → .env file → Environment variables → Metaflow Config YAML
```

Resolved via Pydantic Settings. All config validated at startup (fail fast).

**Logging (`structlog`):**

```
JSON format + correlation_id (Metaflow run_id)
Logger per module: arrow_lake.{module}
Levels: DEBUG, INFO, WARNING, ERROR, CRITICAL
```

**Metrics (`prometheus_client`):**

15 metrics with prefix `arrow_lake_{domain}_{metric}_{unit}`. Exposed at `:8000/metrics`.

---

## 3. Core Component Specifications

### 3.1 CatalogActor

**Type:** Ray Actor (singleton)
**File:** `arrow_lake/catalog/actor.py`
**Responsibility:** Single source of truth for table metadata and lifecycle operations.

**Ray Decorator:**

```python
@ray.remote(
    num_cpus=1,
    resources={"catalog": 1},  # Requires: ray.init(resources={"catalog": 1})
    max_restarts=3,
    max_task_retries=2,
)
class CatalogActor:
```

**Constructor:**

```python
def __init__(self, storage_path: str, config: ArrowLakeSettings, namespace: str = "default") -> None:
    self._storage_path = storage_path
    self._namespace = namespace  # Future multi-tenant isolation (Story 1.8)
    self._connection_pool = DuckDBConnectionPool(
        read_connections=config.catalog.read_connections,  # default 4 (reduced from 8 — catalog-only workload)
        write_connections=config.catalog.write_connections,  # default 1
        database_path=f"{storage_path}/_catalog/catalog.db",
    )
    self._cache = LRUMetadataCache(max_size=256)  # In-memory metadata cache
```

**Public Interface (remote calls):**

| Method | Signature | Returns | Description |
|--------|-----------|---------|-------------|
| `create_table` | `(name: str, schema: pa.Schema, metadata: dict) -> None` | `None` | Create new Lance dataset with schema |
| `get_table` | `(name: str) -> TableMetadata` | Pydantic model | Get table metadata (from cache or pool) |
| `list_tables` | `() -> list[TableMetadata]` | List[Pydantic] | List all tables |
| `append_data` | `(name: str, data: pa.Table) -> AppendResult` | Pydantic model | Append rows to existing table |
| `delete_table` | `(name: str) -> None` | `None` | Delete table and all versions |
| `create_index` | `(name: str, column: str, index_type: IndexType, params: dict) -> None` | `None` | Build index on column |
| `list_versions` | `(name: str) -> list[VersionInfo]` | List[Pydantic] | List all versions |
| `checkout_version` | `(name: str, version: int) -> None` | `None` | Pin table to specific version |
| `compact_files` | `(name: str, target_fragment_bytes: int) -> CompactResult` | Pydantic model | Compact fragments to target size |
| `cleanup_versions` | `(name: str, retain_latest: int, keep_tags: list[str]) -> CleanupResult` | Pydantic model | Remove old versions |

**Internal Methods (not remote):**

| Method | Description |
|--------|-------------|
| `_get_read_conn() → DuckDBPyConnection` | Acquire read connection from pool |
| `_get_write_conn() → DuckDBPyConnection` | Acquire write connection from pool |
| `_validate_schema_compatible(new: pa.Schema, existing: pa.Schema) → None` | Check schema evolution rules |
| `_update_cache(name: str, metadata: TableMetadata) → None` | Refresh in-memory cache |
| `_check_fragment_size(name: str) → bool` | Alert if fragments out of 128-512MB range |

**Connection Pool Protocol (`DuckDBConnectionPool`):**

```python
class DuckDBConnectionPool:
    """Thread-safe DuckDB WAL connection pool (Catalog-only workload).

    NOTE: DuckDB does not provide a built-in connection pool. This is a custom
    implementation built on top of DuckDB's WAL mode for CATALOG metadata storage
    only. OLAP queries are handled by Daft SQL (primary) — DuckDB is NOT used for
    analytical workloads. This simplifies pool sizing since catalog operations are
    short-lived metadata reads/writes, not long-running OLAP queries.

    VALIDATION: Story 1.2 Spike (3-day time-box) validates DuckDB WAL multi-reader
    support. NO-GO fallback: DuckDB → pure catalog store with Daft SQL for all SQL.
    """

    def __init__(
        self,
        read_connections: int = 4,
        write_connections: int = 1,
        database_path: str = ":memory:",
    ) -> None:
        # Read connections: read_only=True, access_mode="read_only"
        # Write connection: access_mode="read_write"
        # All connections point to same DB file (WAL mode)
        # Pool sized for catalog metadata ops (NOT OLAP queries)

    def acquire_read(self, timeout: float = 30.0) -> DuckDBPyConnection: ...
    def release_read(self, conn: DuckDBPyConnection) -> None: ...
    def acquire_write(self, timeout: float = 30.0) -> DuckDBPyConnection: ...
    def release_write(self, conn: DuckDBPyConnection) -> None: ...
    def health_check(self) -> PoolHealth: ...
```

**Error Handling:**

| Scenario | Exception | Retry |
|----------|-----------|-------|
| Table not found | `TableNotFoundError(CatalogError)` | No |
| Connection pool exhausted | `ConnectionPoolExhaustedError(CatalogError)` | No |
| Schema incompatible | `SchemaValidationError(IngestionError)` | No |
| DuckDB write conflict | `CatalogError` | Yes (3x, exponential) |
| Actor unavailable | `RayRuntimeError` | Yes (3x, exponential 1-30s) |

### 3.2 QueryEngine

**Type:** Synchronous class (NOT a Ray Actor)
**File:** `arrow_lake/query/engine.py`
**Responsibility:** Route and execute queries against Lance datasets via Daft SQL (primary OLAP) and DuckDB (catalog queries).

**Why synchronous:** QueryEngine performs OLAP reads against local Lance data through Daft SQL. No distributed computation needed for single-node queries. No Ray overhead warranted. Daft SQL is the primary OLAP engine (Arrow-native, supports distributed via Ray). DuckDB is used only for catalog metadata queries and basic SQL via Lance extension. The catalog connection pool (4 read) is sized for CatalogActor's short metadata operations.

**Constructor:**

```python
class QueryEngine:
    def __init__(self, storage_path: str, config: ArrowLakeSettings) -> None:
        self._storage_path = storage_path
        # Primary OLAP: Daft SQL (Arrow-native, lazy eval, distributed via Ray)
        # Secondary: DuckDB for catalog SQL and Lance extension queries
        self._duckdb_conn = duckdb.connect()
        self._duckdb_conn.execute("INSTALL lance; LOAD lance;")
```

**Query Routing (5 SQL Modes):**

```python
class QueryMode(str, Enum):
    VECTOR = "vector"              # Lance vector search (IVF_PQ)
    FTS = "fts"                    # Lance full-text search (Tantivy)
    HYBRID = "hybrid"              # RRF fusion of vector + FTS
    OLAP = "olap"                  # Daft SQL aggregation (primary) / DuckDB SQL (fallback)
    ANALYTICS_VECTOR = "analytics_vector"  # OLAP + vector similarity combined
```

**Public Interface:**

| Method | Signature | Returns | Description |
|--------|-----------|---------|-------------|
| `execute` | `(query: QuerySpec) -> pa.Table` | Arrow Table | Route to appropriate sub-engine |
| `vector_search` | `(table: str, column: str, query_vector: list[float], top_k: int) -> pa.Table` | Arrow Table | IVF_PQ nearest neighbor |
| `fts_search` | `(table: str, column: str, query_text: str, top_k: int) -> pa.Table` | Arrow Table | Tantivy full-text search |
| `hybrid_search` | `(table: str, vector_query: list[float], fts_query: str, top_k: int, alpha: float) -> pa.Table` | Arrow Table | RRF fusion |
| `sql_query` | `(sql: str, engine: str = "daft") -> pa.Table` | Arrow Table | SQL query via Daft SQL (default) or DuckDB |

**Hybrid Search Algorithm (RRF — Reciprocal Rank Fusion):**

```python
def _hybrid_rrf(
    vector_results: pa.Table,     # Columns: id, score, ...
    fts_results: pa.Table,        # Columns: id, score, ...
    top_k: int,
    alpha: float = 0.7,           # vector weight (0.7 vector, 0.3 FTS)
    k: int = 60,                  # RRF constant
) -> pa.Table:
    """
    RRF formula:
        score(doc) = alpha * (1 / (k + rank_vector(doc)))
                   + (1 - alpha) * (1 / (k + rank_fts(doc)))

    Steps:
    1. Rank vector results by descending score
    2. Rank FTS results by descending score
    3. For each unique doc in union of both:
       - Compute RRF score
       - If missing from one result set, use rank = infinity
    4. Sort by RRF score descending, return top_k
    """
```

**SQL Examples (Daft SQL primary, DuckDB fallback):**

#### Daft SQL Example (Primary — Arrow-native, lazy evaluation)

```python
import daft

# Read Lance data into a Daft DataFrame
df = daft.read_lance("{storage_path}/user_documents")

# DataFrame-level SQL: df.sql() runs SQL against the current DataFrame.
# Use {self} placeholder to reference the DataFrame in the FROM clause (Daft >= 0.7.8).
# NOTE: Exact API surface to be validated in Story 1.2 Spike (Daft >= 0.7.8 Lance integration).
result = df.sql("SELECT category, count(*) as cnt, avg(quality_score) as avg_quality "
                "FROM {self} WHERE _ingested_at > '2026-01-01' "
                "GROUP BY category ORDER BY cnt DESC")
arrow_table = result.to_arrow()

# Alternative: daft.sql() global function (requires table registration or inline references)
# result = daft.sql("SELECT * FROM read_lance('{storage_path}/user_documents') WHERE ...")
```

#### DuckDB Catalog Query Example (Secondary — metadata only)

```python
import duckdb

conn = duckdb.connect()
# Catalog metadata query via DuckDB (embedded in Ray Named Actor)
conn.execute("""
    SELECT table_name, modality, count(*) as rows
    FROM catalog_tables
    WHERE modality = 'image'
    GROUP BY table_name, modality
""").arrow()
```

#### Lance Vector Search SQL Example (Analytics+Vector hybrid)

```sql
-- Find similar documents in a category
-- Uses Lance vector_search SQL function (leverages IVF_PQ index)
SELECT id, text_content, _distance as distance
FROM lance_vector_search(
    '{storage_path}/user_documents',
    column => 'embedding_vector',
    query_vector => [0.1, 0.2, ...],
    k => 10
)
WHERE category = 'research'
ORDER BY _distance ASC;
```

### 3.3 IngestPipeline

**Type:** Synchronous class (composable)
**File:** `arrow_lake/ingest/pipeline.py`
**Responsibility:** Declarative ingest workflow — source → validate → dedup → quality filter → write.

**Configuration (Pydantic model):**

```python
class IngestConfig(BaseModel):
    source: DataSourceConfig          # Where data comes from
    table_name: str                   # Target Lance table
    schema: dict | None = None        # JSON-serializable schema hint (converted to pa.Schema at runtime)
    strict_schema: bool = False       # If True, reject unknown columns
    dedup_columns: list[str] = []     # Content-addressable dedup on these columns
    quality_filters: list[FilterConfig] = []  # Quality filter chain
    embed: bool = False               # Compute embeddings after ingest
    embedding_model: str = "default"  # Model identifier
    embedding_column: str = "embedding_vector"
    batch_size: int = 10_000          # Rows per write batch
    on_reject: Literal["skip", "dead_letter"] = "dead_letter"
```

**Execution Flow:**

```
source.read()
    │
    ▼
┌─ schema_validation ─┐
│  If schema provided: │
│  - strict: reject rows with extra columns or type mismatch
│  - non-strict: cast known columns, drop unknown
└────────┬──────────────┘
         ▼
┌─ dedup ─────────────┐
│  If dedup_columns:  │
│  - Hash specified columns
│  - Filter rows where hash already exists in target table
│  - Uses Lance version scan for existing hashes
└────────┬─────────────┘
         ▼
┌─ quality_filters ───┐
│  Execute filters     │
│  serially in order:  │
│  1. TextLengthFilter │
│  2. ImageResolutionFilter (abbreviated in diagrams) │
│  3. CustomFilter...  │
│                      │
│  Rejected rows →     │
│  dead_letter table   │
└────────┬─────────────┘
         ▼
┌─ write ─────────────┐
│  Write batch to     │
│  Lance via          │
│  CatalogActor       │
└────────┬─────────────┘
         ▼
    IngestResult {
        total_rows: int
        passed_rows: int
        rejected_rows: int
        deduped_rows: int
        table_name: str
        version: int
        quality_report: QualityReport
    }
```

**Public Interface:**

```python
class IngestPipeline:
    def __init__(self, config: IngestConfig) -> None: ...

    def run(self) -> IngestResult:
        """Execute the full ingest pipeline. Returns result summary."""
        ...

    def dry_run(self) -> DryRunResult:
        """Validate config and source without writing. Returns row counts."""
        ...
```

### 3.4 QualityFilter

**Type:** Abstract base + built-in implementations
**File:** `arrow_lake/quality/base.py`, `arrow_lake/quality/builtin.py`

**Abstract Interface:**

```python
from abc import ABC, abstractmethod

class QualityFilter(ABC):
    """Row-level quality filter. Reject rows that don't meet criteria."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Unique filter name for reporting."""
        ...

    @abstractmethod
    def filter(self, table: pa.Table) -> tuple[pa.Table, pa.Table]:
        """
        Apply quality filter.

        Args:
            table: Input Arrow Table.

        Returns:
            (passed_table, rejected_table) — both Arrow Tables with same schema.
            Rejected table has an additional `_rejection_reason` column.
        """
        ...
```

**Built-in Filters:**

```python
class TextLengthFilter(QualityFilter):
    """Reject rows where text column is too short or too long."""

    def __init__(
        self,
        column: str = "text_content",
        min_chars: int = 1,
        max_chars: int | None = None,
    ) -> None: ...

class ImageResolutionFilter(QualityFilter):
    """Reject rows where image resolution is below minimum."""

    def __init__(
        self,
        column: str = "image_bytes",
        min_width: int = 64,
        min_height: int = 64,
    ) -> None: ...
```

**Dead-letter Protocol:**

```python
class DeadLetterWriter:
    """Write rejected rows to {table_name}_dead_letter Lance table."""

    def __init__(self, storage_path: str) -> None: ...

    def write(
        self,
        table_name: str,
        rejected_rows: pa.Table,       # Must have _rejection_reason column
        filter_name: str,
        batch_id: str,                 # correlation_id for traceability
    ) -> int:
        """Append rejected rows to dead-letter table. Returns count written."""
        # Target table: {table_name}_dead_letter
        # Schema: same as source + _rejection_reason + _filter_name + _batch_id + _rejected_at
```

**QualityReport:**

```python
class QualityReport(BaseModel):
    total_rows: int
    passed_rows: int
    rejected_rows: int
    rejection_by_filter: dict[str, int]  # {filter_name: count}
```

### 3.5 EmbeddingEncoder

**Type:** Pluggable protocol class
**File:** `arrow_lake/embedding/encoder.py`
**Responsibility:** Compute vector embeddings for a column of data.

**Interface:**

```python
class EmbeddingEncoder(Protocol):
    """Protocol for pluggable embedding models."""

    @property
    def dimension(self) -> int:
        """Output embedding dimension."""
        ...

    @property
    def model_name(self) -> str:
        """Human-readable model identifier."""
        ...

    def encode(self, data: pa.Table, column: str) -> pa.Table:
        """
        Compute embeddings for the specified column.

        Args:
            data: Input Arrow Table.
            column: Column to embed (text or image).

        Returns:
            New Arrow Table with an additional column named
            after the embedding target (e.g., "text_content_embedding_vector").

        The embedding column type: pa.list_(pa.float32(), self.dimension)
        """
        ...

    def encode_batch(self, data: pa.Table, column: str, batch_size: int = 256) -> pa.Table:
        """Encode in batches to manage GPU memory."""
        ...
```

**Built-in Implementations (Phase 1):**

```python
class SentenceTransformerEncoder:
    """Sentence-Transformers model for text embedding."""
    # GPU via Ray Placement Group, CPU fallback
    # Model loaded once, reused across batches

class CLIPImageEncoder:
    """CLIP model for image embedding."""
    # Requires GPU, pinned to Placement Group

class MockEncoder:
    """Random embeddings for testing."""
    # dimension=768, deterministic seed for reproducibility
```

**Index Management:**

```python
class IndexManager:
    """Manage Lance index lifecycle."""

    def __init__(self, catalog_actor: CatalogActor) -> None: ...

    def create_vector_index(
        self,
        table_name: str,
        column: str,
        index_type: str = "IVF_PQ",
        num_partitions: int = 256,
        num_sub_vectors: int = 128,
    ) -> None:
        """Build IVF_PQ index. Incremental update if data appended."""
        ...

    def create_fts_index(
        self,
        table_name: str,
        column: str,
    ) -> None:
        """Build Tantivy FTS index."""
        ...

    def update_index(self, table_name: str, column: str) -> None:
        """Incrementally update existing index after data append."""
        ...

    def delete_index(self, table_name: str, column: str) -> None:
        """Remove index."""
        ...
```

### 3.6 ArrowLakeClient (SDK Entry Point)

**Type:** Facade class
**File:** `arrow_lake/sdk/client.py`
**Responsibility:** Single entry point for all SDK operations.

```python
class ArrowLakeClient:
    """Arrow Lake SDK entry point.

    Usage:
        lake = ArrowLakeClient.connect("./data/lance")
        table = lake.table("user_documents")
        table.ingest(source=..., filters=[...])
        results = table.search("query").vector(top_k=10).to_arrow()
    """

    def __init__(
        self,
        storage_path: str,
        config: ArrowLakeSettings | None = None,
    ) -> None:
        self._storage_path = storage_path
        self._config = config or ArrowLakeSettings()
        self._catalog: CatalogActor = None  # Lazy init
        self._query_engine: QueryEngine = None  # Lazy init

    @classmethod
    def connect(cls, storage_path: str, **kwargs) -> "ArrowLakeClient":
        """Factory method. Alias for constructor."""
        return cls(storage_path=storage_path, **kwargs)

    def _ensure_catalog(self) -> CatalogActor:
        """Lazy-initialize Ray and CatalogActor on first use."""
        if self._catalog is None:
            if not ray.is_initialized():
                ray.init(
                    address="auto" if self._config.ray.address else None,
                    resources={"catalog": 1},  # Register custom resource for CatalogActor pinning
                )
            self._catalog = CatalogActor.remote(self._storage_path, self._config)
        return self._catalog

    def _ensure_query_engine(self) -> QueryEngine:
        """Lazy-initialize QueryEngine."""
        if self._query_engine is None:
            self._query_engine = QueryEngine(self._storage_path, self._config)
        return self._query_engine

    def table(self, name: str) -> TableHandle:
        """Return a TableHandle for the named table."""
        return TableHandle(
            name=name,
            client=self,
            catalog=self._ensure_catalog(),
            query_engine=self._ensure_query_engine(),
        )

    def list_tables(self) -> list[TableMetadata]:
        """List all tables in the catalog."""
        return ray.get(self._ensure_catalog().list_tables.remote())

    def disconnect(self) -> None:
        """Clean up resources."""
        if ray.is_initialized():
            ray.shutdown()
```

---

## 4. Arrow Zero-Copy Chain Technical Specification

### 4.1 Chain Overview

The zero-copy chain is the performance backbone of Arrow Lake. Data enters as Arrow and remains Arrow (shared memory buffers) through every stage until it reaches the consumer (PyTorch, user code).

```
                    Arrow Zero-Copy Chain
                    =====================

Lance ──→ Daft ──→ PyTorch
  │          │         │
  │ Arrow    │ Arrow   │ Arrow
  │ IPC      │ Table   │ Tensor
  │          │         │
  ▼          ▼         ▼
shared    shared     pin_memory
buf ref    buf ref    + CUDA DMA

              ↕ (Catalog queries only)
           DuckDB
             │
             ▼
          shared
          buf ref

                     ┌── cuDF ──┐
                     │ (GPU)    │
                     │ Arrow    │ ← Controlled copy point
                     │ → CPU    │
                     └──────────┘
```

### 4.2 Boundary Specifications

#### Boundary 1: Lance → Daft

**Data Format:** Arrow IPC (zero-copy)

**Protocol:**
```python
import lance
import daft

# Lance reads Arrow Table
lance_table = lance.open_table("{storage_path}/user_documents")
arrow_table = lance_table.to_table()  # Returns pa.Table

# Daft consumes Arrow Table (zero-copy)
daft_df = daft.from_arrow(arrow_table)
```

**Zero-copy verification:**
```python
def verify_boundary_lance_daft(lance_table, daft_df) -> None:
    arrow_table = lance_table.to_table()
    # Daft stores Arrow data internally
    daft_arrow = daft_df.to_arrow()
    for i in range(arrow_table.num_columns):
        src_bufs = arrow_table.column(i).buffers
        tgt_bufs = daft_arrow.column(i).buffers
        for s, t in zip(src_bufs, tgt_bufs):
            if s and t:
                assert_zero_copy(s, t)
```

**Failure mode:** If Daft needs to cast types, it copies. This is an integration bug.

#### Boundary 2: Daft → DuckDB

**Data Format:** Arrow RecordBatch (zero-copy via Arrow streaming)

**Protocol:**
```python
import duckdb

# Daft evaluates to Arrow Table
arrow_table = daft_df.to_arrow()

# DuckDB registers Arrow data (zero-copy)
conn = duckdb.connect()
conn.register("temp", arrow_table)  # Zero-copy Arrow ingestion
result = conn.execute("SELECT * FROM temp WHERE ...").arrow()
```

**Zero-copy verification:**
```python
def verify_boundary_daft_duckdb(arrow_table, duckdb_conn) -> None:
    conn.register("verify_input", arrow_table)
    result = conn.execute("SELECT * FROM verify_input").arrow()
    for i in range(arrow_table.num_columns):
        src = arrow_table.column(i)
        tgt = result.column(i)
        for j in range(src.num_chunks):
            for s, t in zip(src.chunks[j].buffers, tgt.chunks[j].buffers):
                if s and t:
                    assert_zero_copy(s, t)
```

**Failure mode:** DuckDB filter pushdown may create new buffers (this is expected — pushdown is an optimization that creates new Arrow data, not a copy of input).

#### Boundary 3: DuckDB → PyTorch

**Data Format:** Arrow → pinned CPU tensor → GPU tensor

**Protocol:**
```python
import torch

# DuckDB returns Arrow Table
arrow_table = conn.execute("SELECT embedding_vector FROM ...").arrow()

# Extract Arrow array → numpy (zero-copy via Arrow C buffer) → pinned tensor
column = arrow_table.column(0)  # pa.FixedSizeListArray (embedding)
numpy_view = column.to_numpy(zero_copy_only=True)  # Arrow zero-copy to numpy
tensor = torch.from_numpy(numpy_view).pin_memory()  # Pinned for GPU transfer
gpu_tensor = tensor.cuda(non_blocking=True)         # Async DMA to GPU
```

**Zero-copy verification:**
```python
def verify_boundary_duckdb_pytorch(arrow_table) -> None:
    column = arrow_table.column(0)
    numpy_view = column.to_numpy(zero_copy_only=True)
    tensor = torch.from_numpy(numpy_view)
    # Tensor and numpy share memory
    assert tensor.data_ptr() == numpy_view.__array_interface__["data"][0]
```

#### Boundary 4: CPU → GPU (pin_memory)

**Data Format:** Pinned CPU memory → GPU via async DMA

**Protocol:**
```python
# CPU tensor pinned for efficient GPU transfer
cpu_tensor = torch.from_numpy(numpy_data).pin_memory()

# Async transfer to GPU (non-blocking)
gpu_tensor = cpu_tensor.cuda(non_blocking=True)

# Synchronize if needed
torch.cuda.synchronize()
```

**Constraint:** CPU and GPU MUST be on the same node (Ray Placement Group). Cross-node transfer via Ray Object Store degrades 100-500x.

#### Boundary 5: Ray Object Store (same node)

**Data Format:** Arrow IPC in shared memory

**Protocol:**
```python
import ray

# Put Arrow Table into Object Store (shared memory, not serialized)
object_ref = ray.put(arrow_table)  # Arrow IPC in Plasma/Object Store

# Get from Object Store (zero-copy if same node)
retrieved = ray.get(object_ref)     # Returns pa.Table, shared buffers
```

**Zero-copy verification:**
```python
def verify_boundary_object_store(arrow_table) -> None:
    ref = ray.put(arrow_table)
    retrieved = ray.get(ref)
    for i in range(arrow_table.num_columns):
        src = arrow_table.column(i)
        tgt = retrieved.column(i)
        # Same-node: buffers share memory address
        for j in range(src.num_chunks):
            for s, t in zip(src.chunks[j].buffers, tgt.chunks[j].buffers):
                if s and t:
                    assert_zero_copy(s, t)
```

#### Boundary 6: cuDF → Arrow (controlled copy)

**This is the ONLY acceptable copy point in the chain.**

**Context:** NeMo Curator operates on cuDF (GPU DataFrames). cuDF can export to Arrow, but this requires GPU→CPU transfer (unavoidable).

**Protocol:**
```python
import cudf

# NeMo Curator produces cuDF DataFrame
cudf_df = curator.filter(cudf_df, ...)  # GPU processing

# Export to Arrow (GPU→CPU copy — controlled and expected)
arrow_table = cudf_df.to_arrow()  # This IS a copy, but it's the only one

# Performance note: Exclude this boundary from NF-PERF-03 latency measurement
# NF-PERF-03 covers Lance→Daft→DuckDB→PyTorch only
```

**Important:** This copy is ARCHITECTURALLY ACCEPTABLE because:
1. It's at the quality filtering stage (not on the hot query path)
2. There is no alternative — cuDF lives on GPU, Arrow zero-copy requires shared CPU memory
3. It's documented and excluded from zero-copy performance metrics

### 4.3 Zero-Copy Assertion Utility

```python
# tests/integration/test_zero_copy_utils.py

def assert_zero_copy(source_buf: pa.Buffer, target_buf: pa.Buffer) -> None:
    """
    Verify two Arrow Buffers share the same underlying memory.

    Raises AssertionError if buffers have different addresses (copy detected).

    This is the primary tool for regression-testing the zero-copy chain.
    Call it at every boundary in integration tests.
    """
    if source_buf is None or target_buf is None:
        return  # Null buffers are not comparable

    src_addr = source_buf.address
    tgt_addr = target_buf.address
    size = min(source_buf.size, target_buf.size)

    assert src_addr == tgt_addr, (
        f"ZERO-COPY VIOLATION: "
        f"source=0x{src_addr:x} (size={source_buf.size}), "
        f"target=0x{tgt_addr:x} (size={target_buf.size}), "
        f"delta={abs(tgt_addr - src_addr)} bytes"
    )
```

### 4.4 Copy Detection in Development

```python
# arrow_lake/ray_runtime/cache.py

from contextlib import contextmanager

class ArrowCopyDetector:
    """
    Development tool that wraps Arrow operations and detects
    unintended copies by monitoring buffer addresses.

    Usage:
        detector = ArrowCopyDetector()
        with detector.monitor():
            result = some_arrow_operation(input_table)
        detector.report()  # Prints any detected copies

    NOT for production use — overhead from address tracking.
    """

    def __init__(self) -> None:
        self._snapshots: dict[str, list[int]] = {}
        self._copies: list[CopyEvent] = []

    @contextmanager
    def monitor(self, label: str = ""):
        """Context manager that snapshots buffer addresses before and after."""
        yield
        # Compare before/after snapshots to detect copies

    def report(self) -> str:
        """Return human-readable report of any detected copies."""
        ...
```

### 4.5 Lazy Evaluation Levels (5-Level)

```
Level 1: Ray Object Store Cache
  ← LRU + TTL (30min). Data stays in shared memory across tasks.

Level 2: Lance Pushdown
  ← Predicate and column pushdown at storage scan.
  ← Only requested columns loaded. Row filters applied before Arrow deserialization.

Level 3: Daft Lazy Download
  ← Daft expressions are not evaluated until .to_arrow() or .collect() called.
  ← Intermediate operations fuse into single scan.

Level 4: Blob Out-of-Line
  ← Columns > 1MB (e.g., raw image bytes) loaded lazily on first access.
  ← PyTorch DataLoader triggers actual read per batch.

Level 5: Daft SQL Pushdown
  ← SQL filters pushed down to Daft execution engine (Arrow-native).
  ← Daft operates on Arrow directly without materializing intermediate results.
  ← DuckDB pushdown available as fallback for catalog SQL queries.
```

**Performance expectation:** For a 10M row table with 768-dim embeddings:
- L1+L2+L5 combined: Only ~1-2% of total data actually loaded into memory
- L3 ensures no unnecessary intermediate Arrow tables
- L4 defers large blobs until the training loop needs them

---

## 5. Data Flow

### 5.1 Ingestion Data Flow

The primary write path: external data source → quality gates → Lance storage.

```
┌──────────────┐     ┌──────────────────┐     ┌──────────────────┐
│  Data Source  │     │  Schema Validate  │     │   Content Dedup  │
│  S3 / Local  │────▶│  Pydantic→Arrow   │────▶│  Hash + Filter   │
│  pa.Table    │     │  strict/lenient   │     │  existing rows   │
└──────────────┘     └──────────────────┘     └────────┬─────────┘
                                                        │
                      ┌──────────────────┐               │
                      │  Quality Filter  │               │
                      │  Chain           │◀──────────────┘
                      │  serial exec     │
                      │  TextLength      │
                      │  ImageRes        │
                      │  Custom...       │
                      └────────┬─────────┘
                               │
                 ┌─────────────┼─────────────┐
                 ▼                             ▼
        ┌────────────────┐          ┌──────────────────┐
        │  Passed Rows   │          │  Rejected Rows   │
        │  pa.Table      │          │  + _rejection_   │
        └───────┬────────┘          │  + _filter_name  │
                │                   │  + _batch_id     │
                ▼                   └────────┬─────────┘
        ┌────────────────┐                   │
        │  Embedding     │                   ▼
        │  Encoder       │          ┌──────────────────┐
        │  GPU/CPU       │          │  Dead-letter     │
        │  batch=256     │          │  Lance Table     │
        └───────┬────────┘          │  {name}_dead_    │
                │                   │  letter           │
                ▼                   └──────────────────┘
        ┌────────────────┐
        │  Lance Write   │
        │  CatalogActor  │
        │  .append_data  │
        │  .remote()     │
        └───────┬────────┘
                │
                ▼
        ┌────────────────┐
        │  Auto Compact  │
        │  if fragment   │
        │  > 512MB       │
        └───────┬────────┘
                │
                ▼
        ┌────────────────┐
        │  Index Update  │
        │  IVF_PQ / FTS  │
        │  incremental   │
        └────────────────┘
```

**Arrow format preservation:** Data enters as `pa.Table` from source and remains Arrow through every stage. The only acceptable copy point is the cuDF→Arrow boundary when NeMo Curator is used for GPU quality filtering.

**Batch processing:** Large datasets are processed in configurable batches (default 10,000 rows) to manage memory. Each batch goes through the full pipeline independently.

### 5.2 Query Data Flow

The primary read path: user query → index lookup → Arrow result.

```
User Code
    │
    ▼
┌──────────────────────────────────────────────────────┐
│  SDK: lake.table - "docs" - .search - "query"       │
│         .vector top_k=10 - .fts top_k=10             │
│         .hybrid alpha=0.7 - .filter expr - .to_arrow │
└──────────────────────┬───────────────────────────────┘
                       │
                       ▼
┌──────────────────────────────────────────────────────┐
│  QueryEngine.execute - QuerySpec                     │
│                                                      │
│  ┌─────────────────────────────────────────────┐     │
│  │  Query Router                               │     │
│  │  mode = VECTOR / FTS / HYBRID / OLAP /      │     │
│  │         ANALYTICS_VECTOR                     │     │
│  └──────┬──────────┬──────────┬────────────────┘     │
│         │          │          │                       │
│    ┌────▼───┐ ┌────▼───┐ ┌───▼────────┐             │
│    │VECTOR  │ │  FTS   │ │  HYBRID    │             │
│    │IVF_PQ  │ │Tantivy │ │ RRF Fusion │             │
│    │search  │ │ search │ │            │             │
│    └────┬───┘ └────┬───┘ └───┬────────┘             │
│         │          │          │                       │
│         ▼          ▼          ▼                       │
│    ┌─────────────────────────────────┐               │
│    │  Lance Dataset (versioned)      │               │
│    │  Column pushdown + Row filter   │               │
│    │  Lazy evaluation (5-level)      │               │
│    └──────────────┬──────────────────┘               │
│                   │                                   │
│    ┌──────────────▼──────────────────┐               │
│    │  Daft SQL (OLAP, primary)       │               │
│    │  df.sql() + .to_arrow()         │               │
│    │  DuckDB (catalog SQL, fallback) │               │
│    └──────────────┬──────────────────┘               │
│                   │                                   │
│                   ▼                                   │
│    ┌──────────────────────────────────┐              │
│    │  pa.Table (zero-copy from Lance) │              │
│    └──────────────────────────────────┘              │
└──────────────────────────────────────────────────────┘
```

**Lazy evaluation levels applied:**

| Level | Mechanism | Effect |
|-------|-----------|--------|
| L1 | Ray Object Store Cache | Hot data stays in shared memory |
| L2 | Lance Predicate Pushdown | Only matching rows loaded |
| L3 | Daft Lazy Evaluation | Expressions fused into single scan |
| L4 | Blob Out-of-Line | Large columns deferred |
| L5 | Daft SQL Pushdown | SQL filters push into Daft scan (DuckDB fallback for catalog) |

### 5.3 Metaflow Orchestration Flow

Pipeline orchestration across environments (local → K8s).

```
┌──────────────────────────────────────────────────────────────┐
│  Metaflow Flow Execution                                     │
│                                                              │
│  @project - name="arrow-lake"                                │
│  class IngestFlow - FlowSpec:                                │
│                                                              │
│  ┌─────────┐    ┌──────────┐    ┌──────────┐    ┌────────┐  │
│  │  start  │───▶│ validate │───▶│  ingest  │───▶│  end   │  │
│  │         │    │          │    │          │    │        │  │
│  │ config  │    │ schema   │    │ source   │    │ report │  │
│  │ load    │    │ check    │    │ read     │    │ metrics│  │
│  └─────────┘    └──────────┘    │ quality  │    └────────┘  │
│                                 │ filter   │                │
│                                 │ embed    │                │
│                                 │ write    │                │
│                                 └──────────┘                │
│                                                              │
│  Environments:                                               │
│  ┌────────────────┐  ┌────────────────┐  ┌──────────────┐  │
│  │  Local         │  │  Ray           │  │  Argo/K8s    │  │
│  │  python flow   │  │  --with ray    │  │  argo-create │  │
│  │  run           │  │  run           │  │              │  │
│  └────────────────┘  └────────────────┘  └──────────────┘  │
│                                                              │
│  Config injection:                                           │
│  configs/dev.yaml ──→ configs/staging.yaml ──→ configs/prod.yaml│
└──────────────────────────────────────────────────────────────┘
```

**Key Metaflow features used:**

- `@project` namespace isolation
- `@schedule` periodic cleanup (version compaction) — cron expression to be finalized in Phase 2
- `@conda` / `@pip` dependency management
- `self.config` from environment-specific YAML
- `--with ray` for distributed execution
- `argo-workflows create` for K8s deployment

### 5.4 Error Recovery Flow

Self-healing strategy for transient failures.

```
┌───────────────────────────────────────────────────────────┐
│  Error Recovery Decision Tree                             │
│                                                           │
│  Operation fails                                          │
│       │                                                   │
│       ▼                                                   │
│  ┌─────────────┐     Yes    ┌──────────────────────┐     │
│  │ Retryable?  │───────────▶│ tenacity retry        │     │
│  │             │            │ 3x, exponential 1-30s │     │
│  └──────┬──────┘            └──────────┬───────────┘     │
│         │ No                           │                   │
│         │                              ▼                   │
│         │                    ┌──────────────────┐         │
│         │                    │ Success?         │         │
│         │                    └────┬────────┬────┘         │
│         │                    Yes  │        │ No            │
│         │                    ▼    │        ▼               │
│         │              ┌──────┐  │  ┌────────────────┐   │
│         │              │Done  │  │  │ Lance Version  │   │
│         │              └──────┘  │  │ Rollback       │   │
│         │                        │  └───────┬────────┘   │
│         ▼                        │          │             │
│  ┌─────────────────┐             │          ▼             │
│  │ Classify Error  │             │  ┌──────────────┐     │
│  │                 │             │  │ Dead-letter  │     │
│  │ Schema invalid  │             │  │ + Alert      │     │
│  │ → Fail fast     │             │  └──────────────┘     │
│  │                 │             │                        │
│  │ Source error    │             │                        │
│  │ → Fail + report │             │                        │
│  │                 │             │                        │
│  │ Quality reject  │             │                        │
│  │ → Dead-letter   │             │                        │
│  └─────────────────┘             │                        │
└──────────────────────────────────┘────────────────────────┘
```

**Retryable errors (tenacity):**

| Error Type | Max Retries | Backoff | Jitter |
|------------|-------------|---------|--------|
| `RayRuntimeError` (Worker preempted) | 3 | exponential 1-30s | Yes |
| `CatalogError` (DuckDB write conflict) | 3 | exponential 1-30s | Yes |
| `ConnectionPoolExhaustedError` | 5 | exponential 0.5-10s | Yes |
| Network timeout (S3) | 5 | exponential 0.5-10s | Yes |

**Non-retryable errors (fail fast):**

| Error Type | Action |
|------------|--------|
| `SchemaValidationError` | Raise immediately + log |
| `TableNotFoundError` | Raise immediately |
| `QualityFilterError` | Dead-letter + continue |

---

## 6. Interface Definitions

### 6.1 SDK Public API Reference

#### `ArrowLakeClient`

```python
class ArrowLakeClient:
    """Arrow Lake SDK entry point.

    Usage:
        lake = ArrowLakeClient.connect("./data/lance")
        table = lake.table("user_documents")
        table.ingest(source=..., filters=[...])
        results = table.search("query").vector(top_k=10).to_arrow()
    """

    @classmethod
    def connect(cls, storage_path: str, **kwargs) -> "ArrowLakeClient":
        """Factory method. Initializes Ray and catalog on first use.

        Args:
            storage_path: Local path or S3 URI for Lance storage.
            **kwargs: Override ArrowLakeSettings fields.

        Returns:
            ArrowLakeClient instance.

        Raises:
            ConnectionError: If Ray cluster unreachable.
            ValueError: If storage_path invalid.
        """
        ...

    def table(self, name: str) -> "TableHandle":
        """Get a handle for a named table.

        Args:
            name: Table name (snake_case, plural).

        Returns:
            TableHandle for chaining operations.

        Raises:
            ValueError: If name format invalid.
        """
        ...

    def list_tables(self) -> list["TableMetadata"]:
        """List all tables in the catalog.

        Returns:
            List of TableMetadata Pydantic models.
        """
        ...

    def disconnect(self) -> None:
        """Clean up Ray resources. Call on shutdown."""
        ...
```

#### `TableHandle`

```python
class TableHandle:
    """Fluent builder for table operations."""

    def create(
        self,
        schema: pa.Schema,
        metadata: dict[str, str] | None = None,
    ) -> "TableHandle":
        """Create a new Lance table with the given schema.

        Args:
            schema: Arrow Schema for the table.
            metadata: Optional key-value metadata.

        Returns:
            self for chaining.

        Raises:
            TableAlreadyExistsError: If table already exists.
            SchemaValidationError: If schema contains unsupported types.
        """
        ...

    def ingest(
        self,
        source: "DataSource",
        *,
        filters: list["QualityFilter"] | None = None,
        dedup_columns: list[str] | None = None,
        embed: bool = False,
        embedding_model: str = "default",
        batch_size: int = 10_000,
        on_reject: Literal["skip", "dead_letter"] = "dead_letter",
    ) -> "IngestResult":
        """Execute full ingestion pipeline.

        Args:
            source: Data source (LocalSource, S3Source).
            filters: Quality filter chain.
            dedup_columns: Columns for content-addressable dedup.
            embed: Compute embeddings after ingest.
            embedding_model: Model identifier for embedding.
            batch_size: Rows per write batch.
            on_reject: How to handle rejected rows.

        Returns:
            IngestResult with row counts and quality report.

        Raises:
            IngestionError: On pipeline failure.
            SourceConnectionError: If source unreachable.
        """
        ...

    def search(self, query: str) -> "SearchBuilder":
        """Start a search query.

        Args:
            query: Search text.

        Returns:
            SearchBuilder for fluent chaining.
        """
        ...

    def query(self, sql: str) -> pa.Table:
        """Execute raw DuckDB SQL against the table.

        Args:
            sql: DuckDB SQL query. Table name available as identifier.

        Returns:
            Arrow Table with query results.

        Raises:
            QueryError: On SQL execution failure.
        """
        ...

    def create_index(
        self,
        column: str,
        index_type: Literal["vector", "fts"],
        **params,
    ) -> "IndexResult":
        """Build index on a column.

        Args:
            column: Column to index.
            index_type: "vector" (IVF_PQ) or "fts" (Tantivy).
            **params: Index-specific parameters.

        Returns:
            IndexResult with build stats.

        Raises:
            ColumnNotFoundError: If column doesn't exist.
            IndexError: On index build failure.
        """
        ...

    def versions(self) -> list["VersionInfo"]:
        """List all versions of this table.

        Returns:
            List of VersionInfo Pydantic models.
        """
        ...

    def compact(self, target_fragment_bytes: int = 256 * 1024 * 1024) -> "CompactResult":
        """Compact fragments to target size.

        Args:
            target_fragment_bytes: Target fragment size in bytes.

        Returns:
            CompactResult with before/after fragment counts.
        """
        ...

    def cleanup_versions(
        self,
        retain_latest: int = 5,
        keep_tags: list[str] | None = None,
    ) -> "CleanupResult":
        """Remove old versions, keeping specified ones.

        Args:
            retain_latest: Number of latest versions to keep.
            keep_tags: Version tags to always retain (e.g., "production").

        Returns:
            CleanupResult with versions removed count.
        """
        ...
```

#### `SearchBuilder`

```python
class SearchBuilder:
    """Fluent builder for search queries."""

    def vector(self, top_k: int = 10) -> "SearchBuilder":
        """Enable vector similarity search.

        Args:
            top_k: Number of nearest neighbors to return.

        Returns:
            self for chaining.
        """
        ...

    def fts(self, top_k: int = 10) -> "SearchBuilder":
        """Enable full-text search.

        Args:
            top_k: Number of text matches to return.

        Returns:
            self for chaining.
        """
        ...

    def hybrid(self, alpha: float = 0.7, top_k: int = 10) -> "SearchBuilder":
        """Enable hybrid search (RRF fusion of vector + FTS).

        Args:
            alpha: Vector weight (0.0-1.0). 1.0 = pure vector.
            top_k: Number of fused results to return.

        Returns:
            self for chaining.
        """
        ...

    def filter(self, expression: str) -> "SearchBuilder":
        """Add a filter expression.

        Args:
            expression: SQL-like filter (e.g., "category = 'research'").

        Returns:
            self for chaining.
        """
        ...

    def select(self, columns: list[str]) -> "SearchBuilder":
        """Select specific columns (column pushdown).

        Args:
            columns: Column names to include in results.

        Returns:
            self for chaining.
        """
        ...

    def to_arrow(self) -> pa.Table:
        """Execute search and return results as Arrow Table.

        Returns:
            Arrow Table with search results.

        Raises:
            QueryError: On search execution failure.
            IndexNotFoundError: If required index doesn't exist.
        """
        ...
```

### 6.2 Pydantic Models

```python
# arrow_lake/catalog/models.py

class TableMetadata(BaseModel):
    """Table metadata stored in catalog."""
    name: str
    schema_json: str                    # Serialized pa.Schema
    row_count: int
    byte_size: int
    fragment_count: int
    version: int
    created_at: datetime
    updated_at: datetime
    tags: list[str] = []
    indexes: list[IndexInfo] = []

class VersionInfo(BaseModel):
    """Lance version information."""
    version: int
    created_at: datetime
    row_count: int
    byte_size: int
    tags: list[str] = []

class IndexInfo(BaseModel):
    """Index metadata."""
    column: str
    index_type: Literal["vector", "fts"]
    params: dict[str, Any]
    created_at: datetime
    row_count_at_creation: int

# arrow_lake/ingest/models.py

class IngestResult(BaseModel):
    """Result of an ingestion pipeline run."""
    table_name: str
    version: int
    total_rows: int
    passed_rows: int
    rejected_rows: int
    deduped_rows: int
    quality_report: QualityReport
    duration_seconds: float

class IngestConfig(BaseModel):
    """Configuration for ingestion pipeline."""
    source: DataSourceConfig
    table_name: str
    schema: dict | None = None           # JSON-serializable schema hint (converted to pa.Schema at runtime)
    strict_schema: bool = False
    dedup_columns: list[str] = []
    quality_filters: list[FilterConfig] = []
    embed: bool = False
    embedding_model: str = "default"
    embedding_column: str = "embedding_vector"
    batch_size: int = 10_000
    on_reject: Literal["skip", "dead_letter"] = "dead_letter"

class DataSourceConfig(BaseModel):
    """Data source configuration."""
    type: Literal["local", "s3"]
    path: str = ""                      # For local
    bucket: str = ""                    # For S3
    prefix: str = ""                    # For S3
    format: Literal["parquet", "jsonl", "csv"] = "parquet"

class FilterConfig(BaseModel):
    """Quality filter configuration."""
    type: str                           # Filter class name
    params: dict[str, Any] = {}         # Filter-specific params

# arrow_lake/query/models.py

class QuerySpec(BaseModel):
    """Query specification."""
    table_name: str
    mode: QueryMode
    query_text: str = ""
    query_vector: list[float] = []
    top_k: int = 10
    alpha: float = 0.7
    filter_expression: str = ""
    select_columns: list[str] = []
    sql: str = ""                       # For OLAP mode

class SearchResult(BaseModel):
    """Search result metadata."""
    table_name: str
    mode: QueryMode
    total_matches: int
    returned_rows: int
    duration_ms: float

# arrow_lake/quality/models.py

class QualityReport(BaseModel):
    """Quality filtering report."""
    total_rows: int
    passed_rows: int
    rejected_rows: int
    rejection_by_filter: dict[str, int]  # {filter_name: count}

class CompactResult(BaseModel):
    """Compaction result."""
    table_name: str
    fragments_before: int
    fragments_after: int
    bytes_reclaimed: int

class CleanupResult(BaseModel):
    """Version cleanup result."""
    table_name: str
    versions_removed: int
    bytes_reclaimed: int

class IndexResult(BaseModel):
    """Index build result."""
    table_name: str
    column: str
    index_type: Literal["IVF_PQ", "FTS"]
    rows_indexed: int
    build_duration_seconds: float

class AppendResult(BaseModel):
    """Result of appending data to a table."""
    table_name: str
    version: int
    rows_appended: int
    bytes_written: int

class DryRunResult(BaseModel):
    """Preview result from dry_run() — no data written."""
    estimated_rows: int
    schema_match: bool
    schema_issues: list[str] = []
    dedup_estimate: int | None = None
    active_filters: list[str] = []

class PoolHealth(BaseModel):
    """DuckDB connection pool health status."""
    read_pool_size: int
    read_pool_available: int
    write_pool_size: int
    write_pool_available: bool  # True if write conn is free
    total_queries: int
    total_wait_seconds: float
```

### 6.3 Data Source Protocol

```python
# arrow_lake/ingest/sources/base.py

class DataSource(Protocol):
    """Protocol for pluggable data sources."""

    def read(self) -> pa.Table:
        """Read data from source as Arrow Table.

        Returns:
            Arrow Table with source data.

        Raises:
            SourceConnectionError: If source unreachable.
            SourceFormatError: If data format invalid.
        """
        ...

    def estimate_row_count(self) -> int:
        """Estimate total rows without full read.

        Returns:
            Estimated row count.
        """
        ...

    def validate(self) -> bool:
        """Check source accessibility without reading.

        Returns:
            True if source is accessible.
        """
        ...
```

**Built-in implementations:**

| Source | File | Protocol |
|--------|------|----------|
| Local files (Parquet/JSONL/CSV) | `ingest/sources/local.py` | `pathlib.Path` |
| S3 / MinIO | `ingest/sources/s3.py` | boto3 S3 API |

### 6.4 Metaflow Flow Interface

```python
# flows/ingest_flow.py

from metaflow import FlowSpec, step, project, Parameter

@project(name="arrow-lake")
class IngestFlow(FlowSpec):
    """Metaflow-managed ingestion pipeline.

    Run locally:
        python flows/ingest_flow.py run

    Run on Ray:
        python flows/ingest_flow.py --with ray run

    Deploy to Argo/K8s:
        python flows/ingest_flow.py argo-workflows create
    """

    table_name = Parameter("table", default="user_documents")
    source_path = Parameter("source", required=True)
    config_env = Parameter("config", default="dev")

    @step
    def start(self):
        """Load config and validate source."""
        ...

    @step
    def validate(self):
        """Schema validation and source accessibility check."""
        ...

    @step
    def ingest(self):
        """Execute ingestion pipeline with quality filtering."""
        ...

    @step
    def end(self):
        """Report results and emit metrics."""
        ...
```

---

## 7. Deployment Architecture

### 7.1 Development Environment (Docker Compose)

Single-machine deployment for development and testing.

```
┌─────────────────────────────────────────────────────────────┐
│  Docker Compose - dev environment                           │
│                                                             │
│  ┌───────────────────────────────────────────────────────┐  │
│  │  arrow-lake-sdk container                             │  │
│  │  ├── Ray Head Node (GCS + Dashboard :8265)           │  │
│  │  ├── CatalogActor (Ray Actor)                        │  │
│  │  ├── QueryEngine (Daft SQL + DuckDB catalog)                   │  │
│  │  ├── Metrics HTTP (:8000)                            │  │
│  │  └── Jupyter Notebook (:8888, optional)              │  │
│  └───────────────────────────────────────────────────────┘  │
│                                                             │
│  ┌──────────────────┐  ┌──────────────────────────────────┐│
│  │  Ray Worker      │  │  MinIO                           ││
│  │  (CPU, 2 workers)│  │  S3-compatible storage           ││
│  │  (GPU optional)  │  │  :9000 API / :9001 Console       ││
│  └──────────────────┘  └──────────────────────────────────┘│
│                                                             │
│  ┌──────────────────┐  ┌──────────────────────────────────┐│
│  │  Prometheus      │  │  Grafana                         ││
│  │  :9090           │  │  :3000                           ││
│  │  scrape configs  │  │  dashboards                      ││
│  └──────────────────┘  └──────────────────────────────────┘│
│                                                             │
│  Network: wits-dintellihub (bridge)                         │
│  Volumes: lance_data, minio_data, prometheus_data           │
└─────────────────────────────────────────────────────────────┘
```

**Resource requirements (dev):**

| Component | CPU | Memory | GPU |
|-----------|-----|--------|-----|
| Ray Head | 2 cores | 4 GB | None |
| Ray Worker | 2 cores | 4 GB | Optional |
| MinIO | 0.5 core | 1 GB | None |
| Prometheus | 0.5 core | 512 MB | None |
| Grafana | 0.5 core | 256 MB | None |

**Startup command:**
```bash
docker compose up -d                    # CPU only
docker compose -f docker-compose.gpu.yml up -d  # With GPU
```

### 7.2 Mini Cluster (3-4 Nodes, SSH Mode)

Transitional deployment for testing distributed behavior before K8s.

```
┌──────────────────────────────────────────────────────────┐
│  Mini Cluster - Ray SSH Mode                             │
│                                                          │
│  ┌────────────────────────────────────────────────────┐  │
│  │  Head Node (Node 1)                                │  │
│  │  ├── Ray GCS (Global Control Store)                │  │
│  │  ├── CatalogActor                                  │  │
│  │  ├── Ray Dashboard (:8265)                         │  │
│  │  ├── Metrics HTTP (:8000)                          │  │
│  │  ├── Prometheus + Grafana                          │  │
│  │  └── MinIO (S3-compatible)                         │  │
│  │  Specs: 8 cores, 16GB RAM                          │  │
│  └────────────────────────────────────────────────────┘  │
│                                                          │
│  ┌──────────────────────┐  ┌──────────────────────────┐  │
│  │  Worker Node 2       │  │  Worker Node 3           │  │
│  │  ├── CPU Workers (4) │  │  ├── CPU Workers (2)     │  │
│  │  └── 16GB RAM        │  │  ├── GPU Worker (1)      │  │
│  │                      │  │  └── 16GB RAM + 1 GPU    │  │
│  └──────────────────────┘  └──────────────────────────┘  │
│                                                          │
│  Placement Group: Workers 2+3 same node for zero-copy   │
│  AutoScale: Ray autoscaler monitors load                │
└──────────────────────────────────────────────────────────┘
```

**Ray cluster init:**
```bash
# On head node
ray start --head --port=6379 --dashboard-host=0.0.0.0

# On worker nodes
ray start --address=head-node:6379 --num-cpus=4
ray start --address=head-node:6379 --num-cpus=2 --num-gpus=1
```

### 7.3 Production Environment (K8s + Helm)

Kubernetes deployment with KubeRay for production workloads.

```
┌──────────────────────────────────────────────────────────────────┐
│  Kubernetes Cluster                                              │
│                                                                  │
│  ┌────────────────────────────────────────────────────────────┐  │
│  │  Namespace: arrow-lake                                     │  │
│  │                                                            │  │
│  │  ┌──────────────────────────────────────────────────────┐  │  │
│  │  │  KubeRay RayCluster CR                                │  │  │
│  │  │                                                        │  │  │
│  │  │  Head Pod                                              │  │  │
│  │  │  ├── Ray GCS + Dashboard (:8265)                      │  │  │
│  │  │  ├── CatalogActor                                     │  │  │
│  │  │  ├── Metrics (:8000) + ServiceMonitor                 │  │  │
│  │  │  ├── Resource: 4 CPU, 8GB RAM                         │  │  │
│  │  │  └── PVC: catalog-data (10GB, GP3)                    │  │  │
│  │  │                                                        │  │  │
│  │  │  Worker Pod Group (CPU)                                │  │  │
│  │  │  ├── Replicas: 2-8 (AutoScale v2)                     │  │  │
│  │  │  ├── Resource: 4 CPU, 8GB RAM each                    │  │  │
│  │  │  └── PVC: lance-data (100GB, GP3)                     │  │  │
│  │  │                                                        │  │  │
│  │  │  Worker Pod Group (GPU)                                │  │  │
│  │  │  ├── Replicas: 0-2 (Spot GPU, AutoScale)              │  │  │
│  │  │  ├── Resource: 4 CPU, 16GB RAM + 1 GPU (T4/A10G)     │  │  │
│  │  │  ├── shutdownAfterJobFinishes: true                    │  │  │
│  │  │  └── Placement Group: same node as CPU workers        │  │  │
│  │  └──────────────────────────────────────────────────────┘  │  │
│  │                                                            │  │
│  │  ┌─────────────────┐  ┌────────────────────────────────┐  │  │
│  │  │  MinIO StatefulSet│  │  Prometheus Operator          │  │  │
│  │  │  :9000 / :9001   │  │  ServiceMonitor → Ray metrics │  │  │
│  │  │  PVC: 500GB      │  │  PrometheusRule: alerts       │  │  │
│  │  └─────────────────┘  └────────────────────────────────┘  │  │
│  │                                                            │  │
│  │  ┌─────────────────┐  ┌────────────────────────────────┐  │  │
│  │  │  Grafana         │  │  Argo Workflows               │  │  │
│  │  │  :3000           │  │  Metaflow-managed pipelines   │  │  │
│  │  │  dashboards      │  │  CronWorkflow for @schedule   │  │  │
│  │  └─────────────────┘  └────────────────────────────────┘  │  │
│  │                                                            │  │
│  │  ResourceQuota:                                            │  │
│  │  ├── requests.cpu: 32                                     │  │
│  │  ├── requests.memory: 64Gi                                │  │
│  │  ├── requests.nvidia.com/gpu: 2                           │  │
│  │  └── limits.nvidia.com/gpu: 4                             │  │
│  └────────────────────────────────────────────────────────────┘  │
│                                                                  │
│  ┌────────────────────────────────────────────────────────────┐  │
│  │  Monitoring Stack (namespace: monitoring)                  │  │
│  │  ├── Prometheus (federation from arrow-lake namespace)     │  │
│  │  ├── Grafana (dashboards)                                  │  │
│  │  └── Alertmanager (PagerDuty/Slack integration)            │  │
│  └────────────────────────────────────────────────────────────┘  │
└──────────────────────────────────────────────────────────────────┘
```

**Helm values structure:**

```yaml
# deploy/helm/arrow-lake/values.yaml
rayCluster:
  head:
    resources:
      requests:
        cpu: 4
        memory: 8Gi
    persistence:
      enabled: true
      size: 10Gi
      storageClass: gp3

  workerGroups:
    - name: cpu
      minReplicas: 2
      maxReplicas: 8
      resources:
        requests:
          cpu: 4
          memory: 8Gi
      persistence:
        enabled: true
        size: 100Gi

    - name: gpu
      minReplicas: 0
      maxReplicas: 2
      resources:
        requests:
          cpu: 4
          memory: 16Gi
          nvidia.com/gpu: 1
      shutdownAfterJobFinishes: true

monitoring:
  prometheus:
    enabled: true
  grafana:
    enabled: true
  serviceMonitor:
    enabled: true

minio:
  enabled: true
  persistence:
    size: 500Gi

resourceQuota:
  hard:
    requests.cpu: "32"
    requests.memory: 64Gi
    nvidia.com/gpu: "2"
```

### 7.4 Deployment Evolution Path

```
Phase 1: Docker Compose          Phase 2: Mini Cluster         Phase 3: K8s Helm
┌─────────────────────┐    ┌─────────────────────────┐    ┌──────────────────────┐
│ Single machine       │    │ 3-4 nodes, SSH mode     │    │ Full K8s cluster     │
│ docker compose up    │───▶│ Ray autoscaler          │───▶│ KubeRay CR           │
│ CPU-only (GPU opt)   │    │ Spot GPU testing        │    │ Argo Workflows       │
│ Local Lance storage  │    │ Shared NFS/S3           │    │ S3 + EBS             │
│ TTV < 45 min         │    │ Distributed validation  │    │ AutoScale v2         │
└─────────────────────┘    └─────────────────────────┘    │ Prometheus Operator  │
                                                           └──────────────────────┘
```

### 7.5 Prometheus Scrape Configuration

Based on existing `deploy/monitoring/prometheus/prometheus.yml`:

```yaml
global:
  scrape_interval: 15s
  evaluation_interval: 15s

scrape_configs:
  - job_name: "arrow-lake-sdk"
    static_configs:
      - targets: ["arrow-lake-head:8000"]
    metrics_path: "/metrics"

  - job_name: "ray-head"
    static_configs:
      - targets: ["arrow-lake-head:8265"]
    metrics_path: "/metrics"

  - job_name: "minio"
    static_configs:
      - targets: ["minio:9000"]
    metrics_path: "/minio/v2/metrics/cluster"

  - job_name: "ray-workers"
    ray_sd_configs:
      - ray_cluster_name: "arrow-lake"
    metrics_path: "/metrics"
```

---

## 8. Configuration Reference

### 8.1 ArrowLakeSettings (Pydantic Settings)

```python
# arrow_lake/config.py

class ArrowLakeSettings(BaseSettings):
    """Four-layer override: code defaults → .env → env vars → Metaflow YAML."""

    model_config = SettingsConfigDict(
        env_prefix="ARROW_LAKE_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- Storage ---
    storage: StorageSettings = StorageSettings()

    # --- Cache ---
    cache: CacheSettings = CacheSettings()

    # --- Ray ---
    ray: RaySettings = RaySettings()

    # --- Catalog ---
    catalog: CatalogSettings = CatalogSettings()

    # --- Query ---
    query: QuerySettings = QuerySettings()

    # --- Metrics ---
    metrics: MetricsSettings = MetricsSettings()

    # --- Logging ---
    logging: LoggingSettings = LoggingSettings()
```

### 8.2 Storage Settings

```python
class StorageSettings(BaseModel):
    """Lance storage configuration."""
    base_path: str = "./data/lance"          # Local path or s3://bucket/prefix
    max_fragment_size_mb: int = 256           # Target fragment size
    auto_compact_threshold_mb: int = 512      # Auto-compact above this
    s3_endpoint_url: str | None = None        # MinIO endpoint
    s3_access_key: str | None = None
    s3_secret_key: str | None = None
    s3_region: str = "us-east-1"
```

| Environment Variable | Default | Description |
|---------------------|---------|-------------|
| `ARROW_LAKE_STORAGE__BASE_PATH` | `./data/lance` | Lance storage root |
| `ARROW_LAKE_STORAGE__MAX_FRAGMENT_SIZE_MB` | `256` | Target fragment size |
| `ARROW_LAKE_STORAGE__AUTO_COMPACT_THRESHOLD_MB` | `512` | Auto-compact trigger |
| `ARROW_LAKE_STORAGE__S3_ENDPOINT_URL` | `None` | MinIO/S3 endpoint |
| `ARROW_LAKE_STORAGE__S3_ACCESS_KEY` | `None` | S3 access key |
| `ARROW_LAKE_STORAGE__S3_SECRET_KEY` | `None` | S3 secret key |

### 8.3 Cache Settings

```python
class CacheSettings(BaseModel):
    """Ray Object Store cache configuration."""
    ttl_seconds: int = 1800                  # 30 minutes
    blob_threshold_mb: int = 1               # Out-of-line threshold
    max_memory_fraction: float = 0.3         # Max fraction of Ray Object Store
    evict_on_shutdown: bool = True           # Clean up on disconnect
```

| Environment Variable | Default | Description |
|---------------------|---------|-------------|
| `ARROW_LAKE_CACHE__TTL_SECONDS` | `1800` | Cache TTL in seconds |
| `ARROW_LAKE_CACHE__BLOB_THRESHOLD_MB` | `1` | Large blob threshold |
| `ARROW_LAKE_CACHE__MAX_MEMORY_FRACTION` | `0.3` | Object Store memory limit |
| `ARROW_LAKE_CACHE__EVICT_ON_SHUTDOWN` | `True` | Evict on disconnect |

### 8.4 Ray Settings

```python
class RaySettings(BaseModel):
    """Ray cluster configuration."""
    address: str | None = None               # None = auto-detect, "auto" = existing
    num_cpu_workers: int = 2
    gpu_per_worker: int = 0
    worker_memory_gb: float = 4.0
    head_memory_gb: float = 8.0
    dashboard_host: str = "0.0.0.0"
    dashboard_port: int = 8265
    shutdown_on_disconnect: bool = True      # Shutdown Ray on client disconnect
```

| Environment Variable | Default | Description |
|---------------------|---------|-------------|
| `ARROW_LAKE_RAY__ADDRESS` | `None` | Ray cluster address |
| `ARROW_LAKE_RAY__NUM_CPU_WORKERS` | `2` | Number of CPU workers |
| `ARROW_LAKE_RAY__GPU_PER_WORKER` | `0` | GPUs per worker |
| `ARROW_LAKE_RAY__WORKER_MEMORY_GB` | `4.0` | Worker memory in GB |
| `ARROW_LAKE_RAY__DASHBOARD_PORT` | `8265` | Dashboard port |

### 8.5 Catalog Settings

```python
class CatalogSettings(BaseModel):
    """Catalog (DuckDB WAL) configuration."""
    read_connections: int = 4                # Read connection pool size (catalog-only)
    write_connections: int = 1               # Write connection pool size
    connection_timeout_seconds: float = 30.0 # Pool acquire timeout
    metadata_cache_size: int = 256           # In-memory metadata cache entries
    database_path: str = "_catalog/catalog.db"  # Relative to storage base
```

| Environment Variable | Default | Description |
|---------------------|---------|-------------|
| `ARROW_LAKE_CATALOG__READ_CONNECTIONS` | `8` | Read pool size |
| `ARROW_LAKE_CATALOG__WRITE_CONNECTIONS` | `1` | Write pool size |
| `ARROW_LAKE_CATALOG__CONNECTION_TIMEOUT_SECONDS` | `30.0` | Acquire timeout |
| `ARROW_LAKE_CATALOG__METADATA_CACHE_SIZE` | `256` | Cache entries |

### 8.6 Query Settings

```python
class QuerySettings(BaseModel):
    """Query engine configuration."""
    default_top_k: int = 10
    max_top_k: int = 1000
    vector_search_timeout_seconds: float = 30.0
    fts_search_timeout_seconds: float = 10.0
    hybrid_rrf_k: int = 60                  # RRF constant
    default_hybrid_alpha: float = 0.7       # Vector weight
```

| Environment Variable | Default | Description |
|---------------------|---------|-------------|
| `ARROW_LAKE_QUERY__DEFAULT_TOP_K` | `10` | Default result count |
| `ARROW_LAKE_QUERY__MAX_TOP_K` | `1000` | Maximum result count |
| `ARROW_LAKE_QUERY__HYBRID_RRF_K` | `60` | RRF fusion constant |
| `ARROW_LAKE_QUERY__DEFAULT_HYBRID_ALPHA` | `0.7` | Vector vs FTS weight |

### 8.7 Metrics Settings

```python
class MetricsSettings(BaseModel):
    """Prometheus metrics configuration."""
    enabled: bool = True
    port: int = 8000
    path: str = "/metrics"
    namespace: str = "arrow_lake"
```

| Environment Variable | Default | Description |
|---------------------|---------|-------------|
| `ARROW_LAKE_METRICS__ENABLED` | `True` | Enable metrics |
| `ARROW_LAKE_METRICS__PORT` | `8000` | Metrics HTTP port |
| `ARROW_LAKE_METRICS__PATH` | `/metrics` | Metrics endpoint path |

### 8.8 Logging Settings

```python
class LoggingSettings(BaseModel):
    """Structured logging configuration."""
    level: str = "INFO"                      # DEBUG, INFO, WARNING, ERROR, CRITICAL
    format: str = "json"                     # json or console
    correlation_id_source: str = "metaflow"  # metaflow run_id or custom
```

| Environment Variable | Default | Description |
|---------------------|---------|-------------|
| `ARROW_LAKE_LOGGING__LEVEL` | `INFO` | Log level |
| `ARROW_LAKE_LOGGING__FORMAT` | `json` | Output format |

### 8.9 Environment-Specific YAML Configs

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
    num_cpu_workers: 2
    gpu_per_worker: 0
  catalog:
    read_connections: 4
    write_connections: 1
  metrics:
    enabled: true
    port: 8000
  logging:
    level: DEBUG
    format: console

# configs/prod.yaml
arrow_lake:
  storage:
    base_path: s3://arrow-lake-data/lance
    max_fragment_size_mb: 512
    auto_compact_threshold_mb: 512
  cache:
    ttl_seconds: 3600
    blob_threshold_mb: 1
    max_memory_fraction: 0.4
  ray:
    address: auto
    num_cpu_workers: 8
    gpu_per_worker: 1
    worker_memory_gb: 16.0
  catalog:
    read_connections: 16
    write_connections: 2
  query:
    default_top_k: 20
    max_top_k: 5000
  metrics:
    enabled: true
    port: 8000
  logging:
    level: INFO
    format: json
```

---

## 9. Error Handling Matrix

### 9.1 Exception Hierarchy

```
ArrowLakeError (base)
├── IngestionError
│   ├── SourceConnectionError         # S3/local source unreachable
│   ├── SourceFormatError             # Data format invalid
│   ├── SchemaValidationError         # Schema mismatch
│   └── QualityFilterError            # Filter execution failure
├── QueryError
│   ├── IndexNotFoundError            # Required index missing
│   ├── QueryTimeoutError             # Query exceeded timeout
│   ├── InvalidQueryModeError         # Unsupported query mode
│   └── ColumnNotFoundError           # Referenced column missing
├── CatalogError
│   ├── TableNotFoundError            # Table doesn't exist
│   ├── TableAlreadyExistsError       # Table already exists
│   ├── ConnectionPoolExhaustedError  # All connections in use
│   ├── VersionNotFoundError          # Version doesn't exist
│   └── SchemaEvolutionError          # Incompatible schema change
└── RayRuntimeError
    ├── WorkerUnavailableError         # Ray worker died
    ├── PlacementGroupError            # PG creation failure
    ├── ObjectStoreFullError           # Object Store capacity
    └── ActorRestartError              # Actor exceeded max_restarts
```

### 9.2 Complete Error Handling Matrix

| Error | Component | Retry | Backoff | Fallback | User Action |
|-------|-----------|-------|---------|----------|-------------|
| `SourceConnectionError` | IngestPipeline | Yes (5x) | exp 0.5-10s | None | Check source URL/credentials |
| `SourceFormatError` | IngestPipeline | No | — | None | Fix source data format |
| `SchemaValidationError` | IngestPipeline | No | — | None | Fix input schema |
| `QualityFilterError` | QualityFilter | No | — | Dead-letter | Review filter config |
| `IndexNotFoundError` | QueryEngine | No | — | Full scan warning | Build index first |
| `QueryTimeoutError` | QueryEngine | Yes (2x) | linear 5s | Reduce top_k | Simplify query |
| `InvalidQueryModeError` | QueryEngine | No | — | None | Use valid QueryMode |
| `ColumnNotFoundError` | QueryEngine | No | — | None | Check column name |
| `TableNotFoundError` | CatalogActor | No | — | None | Create table first |
| `TableAlreadyExistsError` | CatalogActor | No | — | None | Use different name |
| `ConnectionPoolExhaustedError` | CatalogActor | Yes (5x) | exp 0.5-10s | Queue request | Increase pool size |
| `VersionNotFoundError` | CatalogActor | No | — | None | Check version number |
| `SchemaEvolutionError` | CatalogActor | No | — | None | Use compatible schema |
| `WorkerUnavailableError` | RayRuntime | Yes (3x) | exp 1-30s | Auto-restart | Check Ray cluster |
| `PlacementGroupError` | RayRuntime | Yes (3x) | exp 1-30s | CPU fallback | Check GPU availability |
| `ObjectStoreFullError` | RayRuntime | No | — | Evict cache | Increase memory |
| `ActorRestartError` | RayRuntime | Yes (3x) | exp 1-30s | Re-create actor | Check logs |

### 9.3 Error Propagation Across Boundaries

```
SDK Layer (user-facing)
    │
    │  All exceptions propagated as ArrowLakeError subclasses
    │  Original cause chained via __cause__
    │
    ▼
Service Layer
    │
    │  CatalogActor: Ray serializes exceptions across .remote()
    │  QueryEngine: Direct exception propagation (synchronous)
    │  IngestPipeline: Wraps internal errors in IngestionError
    │
    ▼
Runtime Layer
    │
    │  Ray: RayTaskError wraps remote exceptions
    │  tenacity: Retry exhausted → reraise original
    │
    ▼
Storage Layer
    │
    │  Lance: OSError, ValueError → wrapped in CatalogError
    │  DuckDB: duckdb.Error → wrapped in QueryError
    │
    ▼
External
    │
    │  S3: botocore exceptions → SourceConnectionError
    │  Network: ConnectionError → retryable errors
```

### 9.4 Dead-Letter Protocol

When quality filters reject rows, the rejected data is preserved for analysis:

```python
# Rejected row schema (added columns):
# _rejection_reason: str      — Why the row was rejected
# _filter_name: str           — Which filter rejected it
# _batch_id: str              — Correlation ID for tracing
# _rejected_at: timestamp     — When it was rejected
```

**Dead-letter table lifecycle:**
1. Created automatically on first rejection for a table
2. Named `{table_name}_dead_letter`
3. Independent Lance dataset (separate directory)
4. Queried via standard SDK: `lake.table("user_documents_dead_letter").query("SELECT * FROM ...")`
5. Manual cleanup: `lake.table("user_documents_dead_letter").cleanup_versions(retain_latest=3)`

### 9.5 Alert Rules (Prometheus)

```yaml
# Prometheus alerting rules
groups:
  - name: arrow_lake_alerts
    rules:
      - alert: HighErrorRate
        expr: rate(arrow_lake_ingestion_errors_total[5m]) > 0.1
        for: 5m
        labels:
          severity: warning
        annotations:
          summary: "High ingestion error rate"

      - alert: GPUBudgetExceeded
        expr: increase(arrow_lake_ray_gpu_hours_total[30d]) > 440  # monthly budget
        for: 1h
        labels:
          severity: critical
        annotations:
          summary: "GPU monthly budget exceeded"

      - alert: CatalogActorUnhealthy
        expr: up{job="arrow-lake-sdk"} == 0
        for: 2m
        labels:
          severity: critical
        annotations:
          summary: "Catalog actor unreachable"

      - alert: FragmentSizeDrift
        expr: arrow_lake_lance_fragment_size_bytes > 536870912  # 512MB
        for: 10m
        labels:
          severity: warning
        annotations:
          summary: "Lance fragments exceeding 512MB"

      - alert: VersionBloat
        expr: arrow_lake_lance_version_count > 50
        for: 30m
        labels:
          severity: warning
        annotations:
          summary: "Version count exceeding 50"
```

---

## 10. Testing Strategy

### 10.1 Test Pyramid

```
                    ┌──────────┐
                    │   E2E    │     2 tests
                    │  Tests   │     Full pipeline validation
                    ├──────────┤
                    │Integration│     6 tests (Arrow boundaries)
                    │  Tests   │     + 3 cross-component tests
                    ├──────────┤
                    │  Unit    │     ~30 tests
                    │  Tests   │     Per-component logic
                    ├──────────┤
                    │ Contract │     Schema compatibility
                    │  Tests   │     Arrow format validation
                    └──────────┘
```

### 10.2 Test Categories

#### Unit Tests (~35 tests)

| Module | Test File | Coverage Target | Key Tests |
|--------|-----------|----------------|-----------|
| Config | `tests/unit/test_config.py` | 90% | Four-layer override, validation, defaults |
| Exceptions | `tests/unit/test_exceptions.py` | 95% | Hierarchy, chaining, message format |
| Connection Pool | `tests/unit/test_connection_pool.py` | 85% | Acquire/release, timeout, health check |
| Schema Conversion | `tests/unit/test_schema_conversion.py` | 90% | Pydantic→Arrow, type mapping, nullable |
| Quality Filters | `tests/unit/test_quality_filters.py` | 90% | Pass/reject split, dead-letter format |
| Dead-letter Writer | `tests/unit/test_dead_letter.py` | 85% | Write to Lance, schema with rejection columns |
| Pipeline | `tests/unit/test_pipeline.py` | 80% | Config validation, dry_run, batch processing |
| Encoder | `tests/unit/test_encoder.py` | 85% | MockEncoder output, dimension, batch |
| Index Manager | `tests/unit/test_index_manager.py` | 80% | Create/update/delete index, incremental |
| Query Engine | `tests/unit/test_query_engine.py` | 80% | Route mode, SQL generation, timeout |
| Placement Manager | `tests/unit/test_placement.py` | 80% | PG create/teardown, bundle format |
| Health Monitor | `tests/unit/test_health_monitor.py` | 80% | Actor health check, auto-restart |
| Cache | `tests/unit/test_cache.py` | 85% | Put/get, TTL eviction, LRU behavior |
| SDK Client | `tests/unit/test_sdk_client.py` | 85% | Lazy init, connect, disconnect |

#### Integration Tests (6 Arrow Boundary Tests + 3 Cross-Component)

| Boundary | Test File | Validates |
|----------|-----------|-----------|
| Lance → Daft | `tests/integration/test_boundary_lance_daft.py` | `buf.address` match |
| Daft → DuckDB | `tests/integration/test_boundary_daft_duckdb.py` | `buf.address` match |
| DuckDB → PyTorch | `tests/integration/test_boundary_duckdb_pytorch.py` | `data_ptr` match |
| CPU → GPU | `tests/integration/test_boundary_cpu_gpu.py` | `pin_memory` + async DMA |
| Ray Object Store | `tests/integration/test_boundary_ray_object_store.py` | Same-node `buf.address` |
| cuDF → Arrow | `tests/integration/test_boundary_cudf_arrow.py` | Controlled copy (expected) |
| Catalog + Lance | `tests/integration/test_catalog_lance.py` | Create/append/read cycle |
| Ingest + Quality | `tests/integration/test_ingest_quality.py` | Filter chain + dead-letter |
| Query + Index | `tests/integration/test_query_index.py` | Vector/FTS/hybrid search |

#### E2E Tests (2 tests)

| Test | File | Validates |
|------|------|-----------|
| Full Pipeline | `tests/e2e/test_full_pipeline.py` | Ingest→Quality→Embed→Search |
| TTV Validation | `tests/e2e/test_ttv.py` | Time-to-value < 45 minutes |

#### Contract Tests

| Test | Validates |
|------|-----------|
| Arrow Schema compatibility | Lance schema evolution (add nullable column) |
| Pydantic → Arrow mapping | All supported types round-trip correctly |
| Index compatibility | IVF_PQ params produce valid index |

### 10.3 Test Fixtures

```python
# tests/conftest.py

@pytest.fixture
def sample_text_table() -> pa.Table:
    """1000-row Arrow Table with text data."""
    return pa.table({
        "id": pa.array(range(1000), type=pa.int64()),
        "text_content": pa.array([f"Document {i}" for i in range(1000)]),
        "category": pa.array(["research", "news", "blog"][i % 3] for i in range(1000)),
        "_source_url": pa.array([f"https://example.com/{i}" for i in range(1000)]),
        "_ingested_at": pa.array([datetime.utcnow()] * 1000),
    })

@pytest.fixture
def sample_multimodal_table() -> pa.Table:
    """100-row Arrow Table with text + image data."""
    ...

@pytest.fixture
def lance_dataset(tmp_path, sample_text_table) -> lance.LanceDataset:
    """Pre-built Lance dataset for query testing."""
    ...

@pytest.fixture
def mock_encoder() -> MockEncoder:
    """Deterministic mock encoder for testing."""
    return MockEncoder(dimension=768, seed=42)

@pytest.fixture
def catalog_actor() -> CatalogActor:
    """Ray Actor instance for integration testing."""
    ...
```

### 10.4 CI Pipeline

```yaml
# .github/workflows/ci.yml
name: CI
on: [push, pull_request]

jobs:
  lint:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - run: uv run ruff check .
      - run: uv run mypy arrow_lake/

  unit-tests:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - run: uv run pytest tests/unit/ -v --cov=arrow_lake --cov-fail-under=80

  integration-tests:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - run: uv run pytest tests/integration/ -v

  e2e-tests:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - run: uv run pytest tests/e2e/ -v --timeout=300

  gpu-tests:  # Nightly + manual trigger
    runs-on: [self-hosted, gpu]
    if: github.event_name == 'schedule' || contains(github.event.comment.body, '@bot run-gpu')
    steps:
      - uses: actions/checkout@v4
      - run: uv run pytest tests/integration/test_boundary_cpu_gpu.py tests/integration/test_boundary_cudf_arrow.py -v
```

### 10.5 Zero-Copy Regression Testing

Every Arrow boundary has a dedicated integration test that verifies buffer address sharing:

```python
# tests/integration/test_boundary_lance_daft.py

def test_lance_to_daft_zero_copy(lance_dataset):
    """Verify Lance→Daft boundary preserves Arrow buffer addresses."""
    arrow_table = lance_dataset.to_table()
    daft_df = daft.from_arrow(arrow_table)
    daft_arrow = daft_df.to_arrow()

    for i in range(arrow_table.num_columns):
        src_bufs = arrow_table.column(i).buffers
        tgt_bufs = daft_arrow.column(i).buffers
        for src, tgt in zip(src_bufs, tgt_bufs):
            if src and tgt:
                assert_zero_copy(src, tgt)
```

**Regression strategy:** These tests run on every PR. If a dependency upgrade breaks zero-copy, the CI fails immediately with the specific boundary and buffer addresses.

### 10.6 Performance Baseline Tests

```python
# tests/e2e/test_performance_baseline.py

class TestPerformanceBaseline:
    """Establish and track performance baselines."""

    def test_vector_search_latency(self, catalog_actor, indexed_dataset):
        """Vector search must be < 10ms at 1M rows."""
        ...

    def test_ingestion_throughput(self, catalog_actor):
        """Ingestion must exceed 50K rows/sec (text)."""
        ...

    def test_zero_copy_chain_utilization(self):
        """Verify > 90% Arrow-native operations."""
        ...
```

---

## Appendix C: Deviations from Architecture Document

The following components/specifications in this system_design.md were not explicitly decided in architecture.md's ADR process. They are implementation-level refinements that follow from the architectural principles:

| Component | Location | Rationale |
|-----------|----------|-----------|
| `HealthMonitor` | Section 2.3 | Operational necessity — Ray Actor health checks |
| `LRUMetadataCache` (max_size=256) | Section 3.1 | Performance optimization — avoid repeated DB reads for hot metadata |
| `ArrowCopyDetector` | Section 4.4 | Development tool — referenced in arch F-DEV-06, placement in `ray_runtime/cache.py` |
| 5 SQL Query Modes (incl. ANALYTICS_VECTOR) | Section 3.2 | Extends arch's "5 种 SQL 模式" with concrete definitions |
| `TableAlreadyExistsError`, `VersionNotFoundError`, `SchemaEvolutionError`, `ObjectStoreFullError`, `ActorRestartError` | Section 9.1 | Richer exception hierarchy covering edge cases |
| QueryEngine independent DuckDB connection | Section 3.2 | Prevents long OLAP queries from starving catalog pool |

---

## Appendix A: Prometheus Metrics Reference (17 Metrics)

| Metric Name | Type | Labels | Description |
|-------------|------|--------|-------------|
| `arrow_lake_ingestion_rows_total` | Counter | `table_name` | Total rows ingested |
| `arrow_lake_ingestion_bytes_total` | Counter | `table_name` | Total bytes ingested |
| `arrow_lake_ingestion_duration_seconds` | Histogram | `table_name` | Ingestion duration |
| `arrow_lake_ingestion_errors_total` | Counter | `table_name`, `error_type` | Ingestion errors |
| `arrow_lake_embedding_rows_total` | Counter | `model_name` | Rows with embeddings computed |
| `arrow_lake_embedding_duration_seconds` | Histogram | `model_name` | Embedding computation time |
| `arrow_lake_quality_rejected_rows_total` | Counter | `table_name`, `filter_name` | Rows rejected by quality filters |
| `arrow_lake_processing_active_tasks` | Gauge | `task_type` | Currently active processing tasks |
| `arrow_lake_query_total` | Counter | `table_name`, `query_type` | Total queries executed |
| `arrow_lake_query_duration_seconds` | Histogram | `table_name`, `query_type` | Query execution time |
| `arrow_lake_query_result_count` | Histogram | `table_name`, `query_type` | Results returned per query |
| `arrow_lake_ray_actors_active` | Gauge | `actor_type` | Active Ray actors |
| `arrow_lake_lance_table_count` | Gauge | — | Number of tables |
| `arrow_lake_lance_fragment_size_bytes` | Gauge | `table_name` | Current fragment size |
| `arrow_lake_ray_gpu_hours_total` | Counter | — | Cumulative GPU hours consumed |
| `arrow_lake_lance_version_count` | Gauge | `table_name` | Number of versions per table |
| `arrow_lake_uptime_seconds` | Gauge | — | Process uptime |

---

## Appendix B: Glossary

| Term | Definition |
|------|-----------|
| **Arrow** | Apache Arrow — columnar memory format for zero-copy data access |
| **DARMU** | Daft + Argo + Ray + Metaflow + uv — the core technology stack |
| **Lance** | Versioned columnar storage format built on Arrow |
| **IVF_PQ** | Inverted File with Product Quantization — vector index type |
| **FTS** | Full-Text Search — text search via Tantivy |
| **RRF** | Reciprocal Rank Fusion — hybrid search result merging algorithm |
| **Placement Group** | Ray mechanism to co-locate CPU/GPU workers on same node |
| **Dead-letter** | Rejected rows persisted for later analysis |
| **TTV** | Time to Value — minutes from setup to first successful query |
| **Zero-copy** | Data access without memory copying — verified via buffer address comparison |
| **WAL** | Write-Ahead Log — DuckDB journaling mode |
| **Object Store** | Ray shared memory store for cross-actor data transfer |
| **Fragment** | Lance storage unit — optimal size 128-512MB |
| **Compact** | Merge small Lance fragments into larger ones |
