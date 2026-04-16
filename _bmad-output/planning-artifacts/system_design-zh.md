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
language: 'zh'
chineseVersionOf: system_design.md
---

# Arrow Lake — 系统设计文档

> 本文档是实现蓝图。经验丰富的 Python 工程师可以仅凭此文档实现任何模块，无需额外上下文。

---

## 1. 系统概览

### 1.1 系统上下文（C4 第一层）

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

**用户：** ML 工程师完全通过 Python SDK 交互。无 UI，无 REST API（MVP 阶段）。

**外部依赖：**

| 系统 | 用途 | 协议 |
|--------|---------|----------|
| Lance (本地/S3) | 主存储 | Python API |
| DuckDB | 目录元数据存储 + Lance 扩展 | Python API（嵌入 Ray Actor） |
| Ray | 分布式运行时 | 内部 |
| Metaflow | 流水线编排 | Python API |
| Argo Workflows | K8s 原生工作流引擎 | YAML / API |
| Prometheus | 指标采集 | HTTP scrape |
| S3 / MinIO | 对象存储（可选） | S3 API (boto3) |

### 1.2 核心设计原则

**六条铁律（不可变）：**

1. **Arrow 零拷贝是法则** — 每个组件边界必须输出 Arrow 格式。任何边界的拷贝/序列化都是集成缺陷，而非架构选择。
2. **Ray Placement Group 是零拷贝的前提** — CPU 和 GPU Worker 必须在同一节点。跨节点 Object Store 性能退化 100-500 倍。
3. **Catalog Actor 只路由，不分析** — 重查询绕过 Actor，直接命中 DuckDB 连接池。
4. **Lance Fragment 大小必须监控** — 128MB-512MB 为最佳范围。写入后自动执行 `compact_files`。
5. **版本膨胀需要主动管理** — `@schedule` 定期清理。`production` 标签永久保留。
6. **GPU 成本需要硬性上限** — 命名空间 ResourceQuota + Prometheus 预算告警。

**第一性原理（五个一）：**

| 原则 | 含义 |
|-----------|---------|
| 一种格式 | Lance 用于所有持久化数据 |
| 一种内存 | Arrow 用于所有内存数据 |
| 一种引擎 | Daft SQL（主要 OLAP）+ DuckDB（目录元数据）+ Lance（向量/FTS） |
| 一种编排器 | Metaflow 用于所有流水线 |
| 一种桥梁 | Ray Object Store 用于所有跨组件数据传输 |

### 1.3 技术栈版本矩阵

**核心栈（DARMU）：**

| 组件 | 版本约束 | 用途 |
|-----------|-------------------|---------|
| Python | >= 3.10, < 3.13 | 运行时 |
| uv | >= 0.4.0 | 包管理器 |
| Daft | >= 0.7.8 | 惰性计算引擎 |
| Argo Workflows | >= 3.5 | K8s 工作流引擎 |
| Ray | >= 2.54.1 | 分布式运行时 |
| Metaflow | >= 2.19.22 | 流水线编排器 |

**扩展层：**

| 组件 | 版本约束 | 用途 |
|-----------|-------------------|---------|
| Lance | >= 4.0.0 | 版本化列式存储 |
| DuckDB | >= 1.5.1 | 目录元数据存储 |
| PyArrow | >= 15.0.0 | Arrow 格式（由 Daft 安装） |
| NeMo Curator | >= 1.1.0 | GPU 加速质量过滤 |
| PyTorch | >= 2.2.0 | ML 训练框架 |

**辅助库：**

| 组件 | 用途 |
|-----------|---------|
| Pydantic | >= 2.0 — Schema 定义 + Settings |
| structlog | JSON 结构化日志 |
| tenacity | 指数退避重试 |
| prometheus-client | 指标暴露 |
| boto3 | S3 源连接器 |
| typer | CLI 入口（可选） |

**依赖风险矩阵：**

| 依赖 | 风险 | 缓解措施 |
|------------|------|------------|
| Lance | API 变更可能破坏零拷贝链 | 锁定版本 + 集成回归测试 |
| Daft | Ray 集成稳定性 | 降级方案：Daft 独立模式 |
| DuckDB Lance Extension | 第三方扩展成熟度 | 降级方案：Daft SQL |
| Ray | GCS 瓶颈，AutoScale v2 稳定性 | 降级方案：Redis 事件总线 |
| NeMo Curator | 仅支持 NVIDIA GPU | 降级方案：CPU 质量评分 |

---

## 2. 架构层（C4 第二层）

### 2.1 SDK 层

**职责：** 面向用户的 API。将开发者意图转化为内部操作。

**组件：**
- `ArrowLakeClient` — 入口点，生命周期管理
- `TableHandle` — 表操作的流式构建器
- `SearchBuilder` — 查询操作的流式构建器

**设计规则：**
- 永远不直接暴露 Ray、Lance 或 DuckDB API
- 所有返回类型为 `pa.Table` 或 Pydantic 模型（绝不用原始 dict）
- 惰性求值：在调用 `.execute()` 或 `.to_arrow()` 之前不执行 I/O

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

### 2.2 服务层

**职责：** 业务逻辑 — 目录管理、数据摄取、查询执行。

**组件：**
- `CatalogActor`（Ray Actor）— 表元数据的唯一事实来源
- `QueryEngine`（同步）— 通过 DuckDB 路由并执行对 Lance 的查询
- `IngestPipeline`（同步）— 声明式摄取工作流
- `QualityFilter` 链 — 行级质量门控
- `EmbeddingEncoder` — 可插拔向量嵌入模型
- `IndexManager` — 索引生命周期（构建、更新、删除）

**设计规则：**
- `CatalogActor` 是唯一写入目录元数据存储的组件
- `QueryEngine` 不依赖 Ray — 通过 Daft SQL（主要 OLAP）和 DuckDB（目录查询）+ Lance 同步执行
- `IngestPipeline` 按确定性链组合过滤器、验证器和写入器
- 质量过滤器串行执行（顺序对短路优化很重要）

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

### 2.3 Ray 运行时层

**职责：** 分布式执行、资源管理、零拷贝数据传输。

**组件：**
- `PlacementManager` — 创建和管理 Ray Placement Group
- `ObjectStoreCache` — Ray Object Store 中 Arrow 数据的 LRU + TTL 缓存
- `HealthMonitor` — Actor 健康检查和自动重启

**设计规则：**
- CPU 和 GPU Worker 必须在同一 Placement Group 中（零拷贝前提）
- Object Store 缓存 TTL 默认：30 分钟
- Blob 内联阈值：1MB
- GPU Worker 设置 `shutdown_after_job_finishes: true`（Ray 选项；KubeRay Helm 使用 `shutdownAfterJobFinishes`）

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

### 2.4 存储层

**职责：** 持久化数据 — 表、索引、版本、死信记录。

**组件：**
- Lance Dataset API — 读取、写入、版本管理、压缩、索引
- Lance FTS (Tantivy) — 全文搜索索引
- Lance IVF_PQ — 向量相似度索引

**设计规则：**
- 所有数据存储为 Lance 数据集（列式、版本化）
- Fragment 大小：128MB-512MB（写入后自动压缩）
- 新增列：优先使用 `add_columns`（零成本、可空）而非 `alter_columns`（重写）
- 死信表：`{table_name}_dead_letter`（每表独立目录）

**存储布局：**

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

### 2.5 横切关注点：配置、日志、指标

**配置（`arrow_lake/config.py`）：**

四层覆盖链（后者覆盖前者）：

```
Code defaults → .env file → Environment variables → Metaflow Config YAML
```

通过 Pydantic Settings 解析。所有配置在启动时验证（快速失败）。

**日志（`structlog`）：**

```
JSON format + correlation_id (Metaflow run_id)
Logger per module: arrow_lake.{module}
Levels: DEBUG, INFO, WARNING, ERROR, CRITICAL
```

**指标（`prometheus_client`）：**

15 个指标，前缀为 `arrow_lake_{domain}_{metric}_{unit}`。在 `:8000/metrics` 暴露。

---

## 3. 核心组件规范

### 3.1 CatalogActor

**类型：** Ray Actor（单例）
**文件：** `arrow_lake/catalog/actor.py`
**职责：** 表元数据和生命周期操作的唯一事实来源。

**Ray 装饰器：**

```python
@ray.remote(
    num_cpus=1,
    resources={"catalog": 1},  # Requires: ray.init(resources={"catalog": 1})
    max_restarts=3,
    max_task_retries=2,
)
class CatalogActor:
```

**构造函数：**

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

**公开接口（远程调用）：**

| 方法 | 签名 | 返回值 | 描述 |
|--------|-----------|---------|-------------|
| `create_table` | `(name: str, schema: pa.Schema, metadata: dict) -> None` | `None` | 使用 schema 创建新 Lance 数据集 |
| `get_table` | `(name: str) -> TableMetadata` | Pydantic 模型 | 获取表元数据（从缓存或连接池） |
| `list_tables` | `() -> list[TableMetadata]` | List[Pydantic] | 列出所有表 |
| `append_data` | `(name: str, data: pa.Table) -> AppendResult` | Pydantic 模型 | 向现有表追加行 |
| `delete_table` | `(name: str) -> None` | `None` | 删除表及所有版本 |
| `create_index` | `(name: str, column: str, index_type: IndexType, params: dict) -> None` | `None` | 在列上构建索引 |
| `list_versions` | `(name: str) -> list[VersionInfo]` | List[Pydantic] | 列出所有版本 |
| `checkout_version` | `(name: str, version: int) -> None` | `None` | 将表固定到指定版本 |
| `compact_files` | `(name: str, target_fragment_bytes: int) -> CompactResult` | Pydantic 模型 | 将 Fragment 压缩到目标大小 |
| `cleanup_versions` | `(name: str, retain_latest: int, keep_tags: list[str]) -> CleanupResult` | Pydantic 模型 | 清理旧版本 |

**内部方法（非远程）：**

| 方法 | 描述 |
|--------|-------------|
| `_get_read_conn() → DuckDBPyConnection` | 从连接池获取读连接 |
| `_get_write_conn() → DuckDBPyConnection` | 从连接池获取写连接 |
| `_validate_schema_compatible(new: pa.Schema, existing: pa.Schema) → None` | 检查 Schema 演化规则 |
| `_update_cache(name: str, metadata: TableMetadata) → None` | 刷新内存缓存 |
| `_check_fragment_size(name: str) → bool` | 如果 Fragment 超出 128-512MB 范围则告警 |

**连接池协议（`DuckDBConnectionPool`）：**

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

**错误处理：**

| 场景 | 异常 | 重试 |
|----------|-----------|-------|
| 表不存在 | `TableNotFoundError(CatalogError)` | 否 |
| 连接池耗尽 | `ConnectionPoolExhaustedError(CatalogError)` | 否 |
| Schema 不兼容 | `SchemaValidationError(IngestionError)` | 否 |
| DuckDB 写冲突 | `CatalogError` | 是（3次，指数退避） |
| Actor 不可用 | `RayRuntimeError` | 是（3次，指数退避 1-30s） |

### 3.2 QueryEngine

**类型：** 同步类（非 Ray Actor）
**文件：** `arrow_lake/query/engine.py`
**职责：** 通过 Daft SQL（主要 OLAP）和 DuckDB（目录查询）路由并执行对 Lance 数据集的查询。

**为什么是同步的：** QueryEngine 通过 Daft SQL 对本地 Lance 数据执行 OLAP 读取。单节点查询不需要分布式计算，不值得引入 Ray 开销。Daft SQL 是主要 OLAP 引擎（Arrow 原生，支持通过 Ray 分布式）。DuckDB 仅用于目录元数据查询和通过 Lance 扩展执行基本 SQL。目录连接池（4 读）是为 CatalogActor 的短生命周期元数据操作设计的。

**构造函数：**

```python
class QueryEngine:
    def __init__(self, storage_path: str, config: ArrowLakeSettings) -> None:
        self._storage_path = storage_path
        # Primary OLAP: Daft SQL (Arrow-native, lazy eval, distributed via Ray)
        # Secondary: DuckDB for catalog SQL and Lance extension queries
        self._duckdb_conn = duckdb.connect()
        self._duckdb_conn.execute("INSTALL lance; LOAD lance;")
```

**查询路由（5 种 SQL 模式）：**

```python
class QueryMode(str, Enum):
    VECTOR = "vector"              # Lance vector search (IVF_PQ)
    FTS = "fts"                    # Lance full-text search (Tantivy)
    HYBRID = "hybrid"              # RRF fusion of vector + FTS
    OLAP = "olap"                  # Daft SQL aggregation (primary) / DuckDB SQL (fallback)
    ANALYTICS_VECTOR = "analytics_vector"  # OLAP + vector similarity combined
```

**公开接口：**

| 方法 | 签名 | 返回值 | 描述 |
|--------|-----------|---------|-------------|
| `execute` | `(query: QuerySpec) -> pa.Table` | Arrow Table | 路由到适当的子引擎 |
| `vector_search` | `(table: str, column: str, query_vector: list[float], top_k: int) -> pa.Table` | Arrow Table | IVF_PQ 最近邻搜索 |
| `fts_search` | `(table: str, column: str, query_text: str, top_k: int) -> pa.Table` | Arrow Table | Tantivy 全文搜索 |
| `hybrid_search` | `(table: str, vector_query: list[float], fts_query: str, top_k: int, alpha: float) -> pa.Table` | Arrow Table | RRF 融合 |
| `sql_query` | `(sql: str, engine: str = "daft") -> pa.Table` | Arrow Table | 通过 Daft SQL（默认）或 DuckDB 执行 SQL 查询 |

**混合搜索算法（RRF — Reciprocal Rank Fusion）：**

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

**SQL 示例（Daft SQL 为主，DuckDB 降级）：**

#### Daft SQL 示例（主要 — Arrow 原生，惰性求值）

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

#### DuckDB 目录查询示例（次要 — 仅限元数据）

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

#### Lance 向量搜索 SQL 示例（分析+向量混合）

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

**类型：** 同步类（可组合）
**文件：** `arrow_lake/ingest/pipeline.py`
**职责：** 声明式摄取工作流 — 数据源 → 验证 → 去重 → 质量过滤 → 写入。

**配置（Pydantic 模型）：**

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

**执行流程：**

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

**公开接口：**

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

**类型：** 抽象基类 + 内置实现
**文件：** `arrow_lake/quality/base.py`、`arrow_lake/quality/builtin.py`

**抽象接口：**

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

**内置过滤器：**

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

**死信协议：**

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

**QualityReport：**

```python
class QualityReport(BaseModel):
    total_rows: int
    passed_rows: int
    rejected_rows: int
    rejection_by_filter: dict[str, int]  # {filter_name: count}
```

### 3.5 EmbeddingEncoder

**类型：** 可插拔协议类
**文件：** `arrow_lake/embedding/encoder.py`
**职责：** 计算数据列的向量嵌入。

**接口：**

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

**内置实现（第一阶段）：**

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

**索引管理：**

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

### 3.6 ArrowLakeClient（SDK 入口点）

**类型：** 门面类
**文件：** `arrow_lake/sdk/client.py`
**职责：** 所有 SDK 操作的唯一入口点。

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

## 4. Arrow 零拷贝链技术规范

### 4.1 链概览

零拷贝链是 Arrow Lake 的性能骨干。数据以 Arrow 格式进入，并在每个阶段保持 Arrow 格式（共享内存缓冲区），直到到达消费者（PyTorch、用户代码）。

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

### 4.2 边界规范

#### 边界 1：Lance → Daft

**数据格式：** Arrow IPC（零拷贝）

**协议：**
```python
import lance
import daft

# Lance reads Arrow Table
lance_table = lance.open_table("{storage_path}/user_documents")
arrow_table = lance_table.to_table()  # Returns pa.Table

# Daft consumes Arrow Table (zero-copy)
daft_df = daft.from_arrow(arrow_table)
```

**零拷贝验证：**
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

**失败模式：** 如果 Daft 需要类型转换，它会产生拷贝。这是集成缺陷。

#### 边界 2：Daft → DuckDB

**数据格式：** Arrow RecordBatch（通过 Arrow 流式传输零拷贝）

**协议：**
```python
import duckdb

# Daft evaluates to Arrow Table
arrow_table = daft_df.to_arrow()

# DuckDB registers Arrow data (zero-copy)
conn = duckdb.connect()
conn.register("temp", arrow_table)  # Zero-copy Arrow ingestion
result = conn.execute("SELECT * FROM temp WHERE ...").arrow()
```

**零拷贝验证：**
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

**失败模式：** DuckDB 过滤下推可能创建新缓冲区（这是预期行为 — 下推是创建新 Arrow 数据的优化，而非输入数据的拷贝）。

#### 边界 3：DuckDB → PyTorch

**数据格式：** Arrow → pinned CPU tensor → GPU tensor

**协议：**
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

**零拷贝验证：**
```python
def verify_boundary_duckdb_pytorch(arrow_table) -> None:
    column = arrow_table.column(0)
    numpy_view = column.to_numpy(zero_copy_only=True)
    tensor = torch.from_numpy(numpy_view)
    # Tensor and numpy share memory
    assert tensor.data_ptr() == numpy_view.__array_interface__["data"][0]
```

#### 边界 4：CPU → GPU（pin_memory）

**数据格式：** Pinned CPU 内存 → 通过异步 DMA 传输到 GPU

**协议：**
```python
# CPU tensor pinned for efficient GPU transfer
cpu_tensor = torch.from_numpy(numpy_data).pin_memory()

# Async transfer to GPU (non-blocking)
gpu_tensor = cpu_tensor.cuda(non_blocking=True)

# Synchronize if needed
torch.cuda.synchronize()
```

**约束：** CPU 和 GPU 必须在同一节点（Ray Placement Group）。通过 Ray Object Store 跨节点传输性能退化 100-500 倍。

#### 边界 5：Ray Object Store（同节点）

**数据格式：** 共享内存中的 Arrow IPC

**协议：**
```python
import ray

# Put Arrow Table into Object Store (shared memory, not serialized)
object_ref = ray.put(arrow_table)  # Arrow IPC in Plasma/Object Store

# Get from Object Store (zero-copy if same node)
retrieved = ray.get(object_ref)     # Returns pa.Table, shared buffers
```

**零拷贝验证：**
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

#### 边界 6：cuDF → Arrow（受控拷贝）

**这是链中唯一可接受的拷贝点。**

**上下文：** NeMo Curator 在 cuDF（GPU DataFrame）上操作。cuDF 可以导出为 Arrow，但这需要 GPU→CPU 传输（不可避免）。

**协议：**
```python
import cudf

# NeMo Curator produces cuDF DataFrame
cudf_df = curator.filter(cudf_df, ...)  # GPU processing

# Export to Arrow (GPU→CPU copy — controlled and expected)
arrow_table = cudf_df.to_arrow()  # This IS a copy, but it's the only one

# Performance note: Exclude this boundary from NF-PERF-03 latency measurement
# NF-PERF-03 covers Lance→Daft→DuckDB→PyTorch only
```

**重要说明：** 这个拷贝在架构上是可接受的，因为：
1. 它发生在质量过滤阶段（不在热查询路径上）
2. 没有替代方案 — cuDF 运行在 GPU 上，Arrow 零拷贝需要共享 CPU 内存
3. 它已被文档化并排除在零拷贝性能指标之外

### 4.3 零拷贝断言工具

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

### 4.4 开发中的拷贝检测

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

### 4.5 惰性求值层级（5 级）

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

**性能预期：** 对于 1000 万行、768 维嵌入的表：
- L1+L2+L5 组合：仅实际加载总数据量的约 1-2% 到内存
- L3 确保不产生不必要的中间 Arrow 表
- L4 将大 Blob 延迟到训练循环需要时才加载

---

## 5. 数据流

### 5.1 摄取数据流

主要写入路径：外部数据源 → 质量门控 → Lance 存储。

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

**Arrow 格式保持：** 数据以 `pa.Table` 格式从源进入，并在每个阶段保持 Arrow 格式。唯一可接受的拷贝点是使用 NeMo Curator 进行 GPU 质量过滤时的 cuDF→Arrow 边界。

**批处理：** 大数据集以可配置批次（默认 10,000 行）处理以管理内存。每个批次独立通过完整流水线。

### 5.2 查询数据流

主要读取路径：用户查询 → 索引查找 → Arrow 结果。

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

**应用的惰性求值层级：**

| 层级 | 机制 | 效果 |
|-------|-----------|--------|
| L1 | Ray Object Store Cache | 热数据保留在共享内存中 |
| L2 | Lance 谓词下推 | 仅加载匹配的行 |
| L3 | Daft 惰性求值 | 表达式融合为单次扫描 |
| L4 | Blob 延迟加载 | 大列延迟加载 |
| L5 | Daft SQL 下推 | SQL 过滤器推入 Daft 扫描（目录查询降级到 DuckDB） |

### 5.3 Metaflow 编排流

跨环境的流水线编排（本地 → K8s）。

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

**使用的 Metaflow 核心特性：**

- `@project` 命名空间隔离
- `@schedule` 定期清理（版本压缩）— cron 表达式在第二阶段确定
- `@conda` / `@pip` 依赖管理
- `self.config` 来自环境特定 YAML
- `--with ray` 用于分布式执行
- `argo-workflows create` 用于 K8s 部署

### 5.4 错误恢复流

瞬态故障的自愈策略。

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

**可重试错误（tenacity）：**

| 错误类型 | 最大重试次数 | 退避策略 | 抖动 |
|------------|-------------|---------|--------|
| `RayRuntimeError`（Worker 被抢占） | 3 | 指数退避 1-30s | 是 |
| `CatalogError`（DuckDB 写冲突） | 3 | 指数退避 1-30s | 是 |
| `ConnectionPoolExhaustedError` | 5 | 指数退避 0.5-10s | 是 |
| 网络超时（S3） | 5 | 指数退避 0.5-10s | 是 |

**不可重试错误（快速失败）：**

| 错误类型 | 操作 |
|------------|--------|
| `SchemaValidationError` | 立即抛出 + 记录日志 |
| `TableNotFoundError` | 立即抛出 |
| `QualityFilterError` | 死信记录 + 继续 |

---

## 6. 接口定义

### 6.1 SDK 公开 API 参考

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

### 6.2 Pydantic 模型

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

### 6.3 数据源协议

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

**内置实现：**

| 数据源 | 文件 | 协议 |
|--------|------|----------|
| 本地文件（Parquet/JSONL/CSV） | `ingest/sources/local.py` | `pathlib.Path` |
| S3 / MinIO | `ingest/sources/s3.py` | boto3 S3 API |

### 6.4 Metaflow Flow 接口

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

## 7. 部署架构

### 7.1 开发环境（Docker Compose）

用于开发和测试的单机部署。

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

**资源需求（开发环境）：**

| 组件 | CPU | 内存 | GPU |
|-----------|-----|--------|-----|
| Ray Head | 2 核 | 4 GB | 无 |
| Ray Worker | 2 核 | 4 GB | 可选 |
| MinIO | 0.5 核 | 1 GB | 无 |
| Prometheus | 0.5 核 | 512 MB | 无 |
| Grafana | 0.5 核 | 256 MB | 无 |

**启动命令：**
```bash
docker compose up -d                    # CPU only
docker compose -f docker-compose.gpu.yml up -d  # With GPU
```

### 7.2 迷你集群（3-4 节点，SSH 模式）

用于在 K8s 之前测试分布式行为的过渡部署。

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

**Ray 集群初始化：**
```bash
# On head node
ray start --head --port=6379 --dashboard-host=0.0.0.0

# On worker nodes
ray start --address=head-node:6379 --num-cpus=4
ray start --address=head-node:6379 --num-cpus=2 --num-gpus=1
```

### 7.3 生产环境（K8s + Helm）

使用 KubeRay 的 Kubernetes 部署，适用于生产工作负载。

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

**Helm values 结构：**

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

### 7.4 部署演进路径

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

### 7.5 Prometheus Scrape 配置

基于现有的 `deploy/monitoring/prometheus/prometheus.yml`：

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

## 8. 配置参考

### 8.1 ArrowLakeSettings（Pydantic Settings）

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

### 8.2 存储配置

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

| 环境变量 | 默认值 | 描述 |
|---------------------|---------|-------------|
| `ARROW_LAKE_STORAGE__BASE_PATH` | `./data/lance` | Lance 存储根路径 |
| `ARROW_LAKE_STORAGE__MAX_FRAGMENT_SIZE_MB` | `256` | 目标 Fragment 大小 |
| `ARROW_LAKE_STORAGE__AUTO_COMPACT_THRESHOLD_MB` | `512` | 自动压缩触发阈值 |
| `ARROW_LAKE_STORAGE__S3_ENDPOINT_URL` | `None` | MinIO/S3 端点 |
| `ARROW_LAKE_STORAGE__S3_ACCESS_KEY` | `None` | S3 访问密钥 |
| `ARROW_LAKE_STORAGE__S3_SECRET_KEY` | `None` | S3 密钥 |

### 8.3 缓存配置

```python
class CacheSettings(BaseModel):
    """Ray Object Store cache configuration."""
    ttl_seconds: int = 1800                  # 30 minutes
    blob_threshold_mb: int = 1               # Out-of-line threshold
    max_memory_fraction: float = 0.3         # Max fraction of Ray Object Store
    evict_on_shutdown: bool = True           # Clean up on disconnect
```

| 环境变量 | 默认值 | 描述 |
|---------------------|---------|-------------|
| `ARROW_LAKE_CACHE__TTL_SECONDS` | `1800` | 缓存 TTL（秒） |
| `ARROW_LAKE_CACHE__BLOB_THRESHOLD_MB` | `1` | 大 Blob 阈值 |
| `ARROW_LAKE_CACHE__MAX_MEMORY_FRACTION` | `0.3` | Object Store 内存限制 |
| `ARROW_LAKE_CACHE__EVICT_ON_SHUTDOWN` | `True` | 断开连接时清除 |

### 8.4 Ray 配置

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

| 环境变量 | 默认值 | 描述 |
|---------------------|---------|-------------|
| `ARROW_LAKE_RAY__ADDRESS` | `None` | Ray 集群地址 |
| `ARROW_LAKE_RAY__NUM_CPU_WORKERS` | `2` | CPU Worker 数量 |
| `ARROW_LAKE_RAY__GPU_PER_WORKER` | `0` | 每个 Worker 的 GPU 数 |
| `ARROW_LAKE_RAY__WORKER_MEMORY_GB` | `4.0` | Worker 内存（GB） |
| `ARROW_LAKE_RAY__DASHBOARD_PORT` | `8265` | Dashboard 端口 |

### 8.5 目录配置

```python
class CatalogSettings(BaseModel):
    """Catalog (DuckDB WAL) configuration."""
    read_connections: int = 4                # Read connection pool size (catalog-only)
    write_connections: int = 1               # Write connection pool size
    connection_timeout_seconds: float = 30.0 # Pool acquire timeout
    metadata_cache_size: int = 256           # In-memory metadata cache entries
    database_path: str = "_catalog/catalog.db"  # Relative to storage base
```

| 环境变量 | 默认值 | 描述 |
|---------------------|---------|-------------|
| `ARROW_LAKE_CATALOG__READ_CONNECTIONS` | `8` | 读连接池大小 |
| `ARROW_LAKE_CATALOG__WRITE_CONNECTIONS` | `1` | 写连接池大小 |
| `ARROW_LAKE_CATALOG__CONNECTION_TIMEOUT_SECONDS` | `30.0` | 获取连接超时 |
| `ARROW_LAKE_CATALOG__METADATA_CACHE_SIZE` | `256` | 缓存条目数 |

### 8.6 查询配置

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

| 环境变量 | 默认值 | 描述 |
|---------------------|---------|-------------|
| `ARROW_LAKE_QUERY__DEFAULT_TOP_K` | `10` | 默认返回数量 |
| `ARROW_LAKE_QUERY__MAX_TOP_K` | `1000` | 最大返回数量 |
| `ARROW_LAKE_QUERY__HYBRID_RRF_K` | `60` | RRF 融合常数 |
| `ARROW_LAKE_QUERY__DEFAULT_HYBRID_ALPHA` | `0.7` | 向量与 FTS 权重 |

### 8.7 指标配置

```python
class MetricsSettings(BaseModel):
    """Prometheus metrics configuration."""
    enabled: bool = True
    port: int = 8000
    path: str = "/metrics"
    namespace: str = "arrow_lake"
```

| 环境变量 | 默认值 | 描述 |
|---------------------|---------|-------------|
| `ARROW_LAKE_METRICS__ENABLED` | `True` | 启用指标 |
| `ARROW_LAKE_METRICS__PORT` | `8000` | 指标 HTTP 端口 |
| `ARROW_LAKE_METRICS__PATH` | `/metrics` | 指标端点路径 |

### 8.8 日志配置

```python
class LoggingSettings(BaseModel):
    """Structured logging configuration."""
    level: str = "INFO"                      # DEBUG, INFO, WARNING, ERROR, CRITICAL
    format: str = "json"                     # json or console
    correlation_id_source: str = "metaflow"  # metaflow run_id or custom
```

| 环境变量 | 默认值 | 描述 |
|---------------------|---------|-------------|
| `ARROW_LAKE_LOGGING__LEVEL` | `INFO` | 日志级别 |
| `ARROW_LAKE_LOGGING__FORMAT` | `json` | 输出格式 |

### 8.9 环境特定 YAML 配置

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

## 9. 错误处理矩阵

### 9.1 异常层次结构

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

### 9.2 完整错误处理矩阵

| 错误 | 组件 | 重试 | 退避策略 | 降级方案 | 用户操作 |
|-------|-----------|-------|---------|----------|-------------|
| `SourceConnectionError` | IngestPipeline | 是（5次） | 指数退避 0.5-10s | 无 | 检查源 URL/凭据 |
| `SourceFormatError` | IngestPipeline | 否 | — | 无 | 修复源数据格式 |
| `SchemaValidationError` | IngestPipeline | 否 | — | 无 | 修复输入 Schema |
| `QualityFilterError` | QualityFilter | 否 | — | 死信记录 | 检查过滤器配置 |
| `IndexNotFoundError` | QueryEngine | 否 | — | 全表扫描警告 | 先构建索引 |
| `QueryTimeoutError` | QueryEngine | 是（2次） | 线性退避 5s | 减少 top_k | 简化查询 |
| `InvalidQueryModeError` | QueryEngine | 否 | — | 无 | 使用有效的 QueryMode |
| `ColumnNotFoundError` | QueryEngine | 否 | — | 无 | 检查列名 |
| `TableNotFoundError` | CatalogActor | 否 | — | 无 | 先创建表 |
| `TableAlreadyExistsError` | CatalogActor | 否 | — | 无 | 使用不同名称 |
| `ConnectionPoolExhaustedError` | CatalogActor | 是（5次） | 指数退避 0.5-10s | 排队请求 | 增加连接池大小 |
| `VersionNotFoundError` | CatalogActor | 否 | — | 无 | 检查版本号 |
| `SchemaEvolutionError` | CatalogActor | 否 | — | 无 | 使用兼容 Schema |
| `WorkerUnavailableError` | RayRuntime | 是（3次） | 指数退避 1-30s | 自动重启 | 检查 Ray 集群 |
| `PlacementGroupError` | RayRuntime | 是（3次） | 指数退避 1-30s | CPU 降级 | 检查 GPU 可用性 |
| `ObjectStoreFullError` | RayRuntime | 否 | — | 清除缓存 | 增加内存 |
| `ActorRestartError` | RayRuntime | 是（3次） | 指数退避 1-30s | 重建 Actor | 检查日志 |

### 9.3 跨边界错误传播

```
SDK 层（面向用户）
    │
    │  所有异常以 ArrowLakeError 子类传播
    │  原始原因通过 __cause__ 链接
    │
    ▼
服务层
    │
    │  CatalogActor: Ray 通过 .remote() 序列化异常
    │  QueryEngine: 直接异常传播（同步）
    │  IngestPipeline: 将内部错误包装为 IngestionError
    │
    ▼
运行时层
    │
    │  Ray: RayTaskError 包装远程异常
    │  tenacity: 重试耗尽后 → 重新抛出原始异常
    │
    ▼
存储层
    │
    │  Lance: OSError, ValueError → 包装为 CatalogError
    │  DuckDB: duckdb.Error → 包装为 QueryError
    │
    ▼
外部
    │
    │  S3: botocore exceptions → SourceConnectionError
    │  网络: ConnectionError → 可重试错误
```

### 9.4 死信协议

当质量过滤器拒绝行时，被拒绝的数据会被保留以供分析：

```python
# Rejected row schema (added columns):
# _rejection_reason: str      — Why the row was rejected
# _filter_name: str           — Which filter rejected it
# _batch_id: str              — Correlation ID for tracing
# _rejected_at: timestamp     — When it was rejected
```

**死信表生命周期：**
1. 首次拒绝时自动创建
2. 命名为 `{table_name}_dead_letter`
3. 独立的 Lance 数据集（独立目录）
4. 通过标准 SDK 查询：`lake.table("user_documents_dead_letter").query("SELECT * FROM ...")`
5. 手动清理：`lake.table("user_documents_dead_letter").cleanup_versions(retain_latest=3)`

### 9.5 告警规则（Prometheus）

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

## 10. 测试策略

### 10.1 测试金字塔

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

### 10.2 测试类别

#### 单元测试（约 35 个）

| 模块 | 测试文件 | 覆盖率目标 | 关键测试 |
|--------|-----------|----------------|-----------|
| Config | `tests/unit/test_config.py` | 90% | 四层覆盖、验证、默认值 |
| Exceptions | `tests/unit/test_exceptions.py` | 95% | 层次结构、链接、消息格式 |
| Connection Pool | `tests/unit/test_connection_pool.py` | 85% | 获取/释放、超时、健康检查 |
| Schema Conversion | `tests/unit/test_schema_conversion.py` | 90% | Pydantic→Arrow、类型映射、可空 |
| Quality Filters | `tests/unit/test_quality_filters.py` | 90% | 通过/拒绝分离、死信格式 |
| Dead-letter Writer | `tests/unit/test_dead_letter.py` | 85% | 写入 Lance、带拒绝列的 Schema |
| Pipeline | `tests/unit/test_pipeline.py` | 80% | 配置验证、dry_run、批处理 |
| Encoder | `tests/unit/test_encoder.py` | 85% | MockEncoder 输出、维度、批处理 |
| Index Manager | `tests/unit/test_index_manager.py` | 80% | 创建/更新/删除索引、增量 |
| Query Engine | `tests/unit/test_query_engine.py` | 80% | 路由模式、SQL 生成、超时 |
| Placement Manager | `tests/unit/test_placement.py` | 80% | PG 创建/销毁、bundle 格式 |
| Health Monitor | `tests/unit/test_health_monitor.py` | 80% | Actor 健康检查、自动重启 |
| Cache | `tests/unit/test_cache.py` | 85% | put/get、TTL 驱逐、LRU 行为 |
| SDK Client | `tests/unit/test_sdk_client.py` | 85% | 惰性初始化、连接、断开 |

#### 集成测试（6 个 Arrow 边界测试 + 3 个跨组件测试）

| 边界 | 测试文件 | 验证内容 |
|----------|-----------|-----------|
| Lance → Daft | `tests/integration/test_boundary_lance_daft.py` | `buf.address` 匹配 |
| Daft → DuckDB | `tests/integration/test_boundary_daft_duckdb.py` | `buf.address` 匹配 |
| DuckDB → PyTorch | `tests/integration/test_boundary_duckdb_pytorch.py` | `data_ptr` 匹配 |
| CPU → GPU | `tests/integration/test_boundary_cpu_gpu.py` | `pin_memory` + 异步 DMA |
| Ray Object Store | `tests/integration/test_boundary_ray_object_store.py` | 同节点 `buf.address` |
| cuDF → Arrow | `tests/integration/test_boundary_cudf_arrow.py` | 受控拷贝（预期行为） |
| Catalog + Lance | `tests/integration/test_catalog_lance.py` | 创建/追加/读取循环 |
| Ingest + Quality | `tests/integration/test_ingest_quality.py` | 过滤器链 + 死信 |
| Query + Index | `tests/integration/test_query_index.py` | 向量/FTS/混合搜索 |

#### E2E 测试（2 个）

| 测试 | 文件 | 验证内容 |
|------|------|-----------|
| 完整流水线 | `tests/e2e/test_full_pipeline.py` | 摄取→质量过滤→嵌入→搜索 |
| TTV 验证 | `tests/e2e/test_ttv.py` | Time-to-value < 45 分钟 |

#### 契约测试

| 测试 | 验证内容 |
|------|-----------|
| Arrow Schema 兼容性 | Lance Schema 演化（添加可空列） |
| Pydantic → Arrow 映射 | 所有支持类型的正确往返转换 |
| 索引兼容性 | IVF_PQ 参数生成有效索引 |

### 10.3 测试 Fixtures

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

### 10.4 CI 流水线

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

### 10.5 零拷贝回归测试

每个 Arrow 边界都有专门的集成测试来验证缓冲区地址共享：

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

**回归策略：** 这些测试在每个 PR 上运行。如果依赖升级破坏了零拷贝，CI 会立即失败并显示具体的边界和缓冲区地址。

### 10.6 性能基线测试

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

## 附录 C：与架构文档的偏差

以下 system_design.md 中的组件/规范未在 architecture.md 的 ADR 流程中明确决定。它们是从架构原则推导出的实现级细化：

| 组件 | 位置 | 理由 |
|-----------|----------|-----------|
| `HealthMonitor` | 第 2.3 节 | 运维必需 — Ray Actor 健康检查 |
| `LRUMetadataCache`（max_size=256） | 第 3.1 节 | 性能优化 — 避免对热元数据的重复数据库读取 |
| `ArrowCopyDetector` | 第 4.4 节 | 开发工具 — 架构文档 F-DEV-06 中引用，放置于 `ray_runtime/cache.py` |
| 5 种 SQL 查询模式（含 ANALYTICS_VECTOR） | 第 3.2 节 | 扩展架构文档中的"5 种 SQL 模式"为具体定义 |
| `TableAlreadyExistsError`、`VersionNotFoundError`、`SchemaEvolutionError`、`ObjectStoreFullError`、`ActorRestartError` | 第 9.1 节 | 更丰富的异常层次结构覆盖边界情况 |
| QueryEngine 独立 DuckDB 连接 | 第 3.2 节 | 防止长时间 OLAP 查询饿死目录连接池 |

---

## 附录 A：Prometheus 指标参考（17 个指标）

| 指标名称 | 类型 | 标签 | 描述 |
|-------------|------|--------|-------------|
| `arrow_lake_ingestion_rows_total` | Counter | `table_name` | 摄取的总行数 |
| `arrow_lake_ingestion_bytes_total` | Counter | `table_name` | 摄取的总字节数 |
| `arrow_lake_ingestion_duration_seconds` | Histogram | `table_name` | 摄取耗时 |
| `arrow_lake_ingestion_errors_total` | Counter | `table_name`、`error_type` | 摄取错误 |
| `arrow_lake_embedding_rows_total` | Counter | `model_name` | 计算了嵌入的行数 |
| `arrow_lake_embedding_duration_seconds` | Histogram | `model_name` | 嵌入计算耗时 |
| `arrow_lake_quality_rejected_rows_total` | Counter | `table_name`、`filter_name` | 被质量过滤器拒绝的行数 |
| `arrow_lake_processing_active_tasks` | Gauge | `task_type` | 当前活跃的处理任务 |
| `arrow_lake_query_total` | Counter | `table_name`、`query_type` | 执行的总查询数 |
| `arrow_lake_query_duration_seconds` | Histogram | `table_name`、`query_type` | 查询执行时间 |
| `arrow_lake_query_result_count` | Histogram | `table_name`、`query_type` | 每次查询返回的结果数 |
| `arrow_lake_ray_actors_active` | Gauge | `actor_type` | 活跃的 Ray Actor |
| `arrow_lake_lance_table_count` | Gauge | — | 表数量 |
| `arrow_lake_lance_fragment_size_bytes` | Gauge | `table_name` | 当前 Fragment 大小 |
| `arrow_lake_ray_gpu_hours_total` | Counter | — | 累计 GPU 使用小时数 |
| `arrow_lake_lance_version_count` | Gauge | `table_name` | 每个表的版本数量 |
| `arrow_lake_uptime_seconds` | Gauge | — | 进程运行时间 |

---

## 附录 B：术语表

| 术语 | 定义 |
|------|-----------|
| **Arrow** | Apache Arrow — 用于零拷贝数据访问的列式内存格式 |
| **DARMU** | Daft + Argo + Ray + Metaflow + uv — 核心技术栈 |
| **Lance** | 基于 Arrow 构建的版本化列式存储格式 |
| **IVF_PQ** | Inverted File with Product Quantization — 向量索引类型 |
| **FTS** | Full-Text Search — 通过 Tantivy 的文本搜索 |
| **RRF** | Reciprocal Rank Fusion — 混合搜索结果合并算法 |
| **Placement Group** | Ray 机制，用于将 CPU/GPU Worker 放置在同一节点 |
| **Dead-letter** | 被拒绝的行持久化存储以供后续分析 |
| **TTV** | Time to Value — 从部署到首次成功查询的分钟数 |
| **Zero-copy** | 无内存拷贝的数据访问 — 通过缓冲区地址比较验证 |
| **WAL** | Write-Ahead Log — DuckDB 日志模式 |
| **Object Store** | Ray 共享内存存储，用于跨 Actor 数据传输 |
| **Fragment** | Lance 存储单元 — 最佳大小 128-512MB |
| **Compact** | 将小的 Lance Fragment 合并为较大的 Fragment |
