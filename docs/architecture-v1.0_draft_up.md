# Arrow Lake v1.0 产品架构设计文档 (优化版)

**版本**: v1.0-draft-up | **日期**: 2026-04-20
**基于**: v1.0-draft + 四方评审反馈整合
**状态**: 待用户审批

---

## 文档修订说明

本文档基于 `architecture-v1.0-draft.md` 和四轮评审 (`v1.0-architecture-review-2026-04-20.md`) 整合优化。主要变更：

| 变更项 | 来源 | 说明 |
|--------|------|------|
| DuckDB Lance 原生集成设计 | W1/W2/A1/W9 | 新增独立章节，覆盖版本锁定、扩展加载、抽象层、回滚路径 |
| `__lance_scan()` 抽象层 | A1( Amelia CRITICAL) | `_base.py` LanceScanAdapter 设计 |
| DuckLake 保留在 v1.0 | 用户决策 + ADR-06 修订 | 数据格式统一的关键决策 |
| LanceDB 恢复为平级组件 | 用户决策 | LanceDB SDK (数据管理层) + DuckDB (查询分析层) 互补，非替代 |
| 分布式追踪 | Winston 缺失项 | OpenTelemetry traces 设计 |
| Schema 演化传播 | Winston 缺失项 | Lance 新列→DuckDB catalog 刷新 |
| 多租户隔离 | Winston 缺失项 | 数据集级 RBAC + DuckDB session 隔离 |
| 备份恢复 | Winston 缺失项 | LanceDB + MinIO + HugeGraph 备份策略 |
| 成本模型 | Winston 缺失项 | 运行成本估算 + 缩放因子 |
| 错误边界定义 | W3 | Lance 扩展错误传播策略 |
| 跨存储 JOIN 预算 | W4 | 查询复杂度限制 + TTL |
| 计算框架契约 | W5/W6/W7/W8 | 数据流契约 + 资源隔离 + 失败域 + 选型论证 |
| M0 显式 AC | A3/A5/Amelia | 可量化验收标准 |
| M2/M3 NO-GO Trigger | John/Mary | 1 周 spike + go/no-go 决策门 |
| OMTM | John | "1 小时从零到 hybrid search API" |

---

## Context

v0.2.0 阶段评审揭示：代码质量 8/10 但生产就绪度仅 ~5/10。核心差距不在功能而在生产运维"最后一公里"。v1.0 目标：

1. **LanceDB + DuckDB + DuckLake 数据格式统一** — LanceDB SDK 负责数据管理（写入/索引/版本），DuckDB SQL 负责查询分析（OLAP/向量/FTS/混合），DuckLake 负责可写衍生（ETL/物化/工作区）
2. **多模态 RAG + 知识图谱 (HugeGraph)** — 从"数据平台"升级为"智能数据平台"
3. **MinIO 真实集成** — 当前所有数据仅存储在本地文件系统
4. **生产基线** — 可观测性、RBAC、CI/CD、备份恢复

---

## 零、当前架构问题诊断（代码审查发现）

### 0A. DuckDB 定位（ADR-05/ADR-06 已确认）

原始设计 (project-context Rule 6) 规定 DuckDB 仅用于 Catalog，Daft SQL 作为主 OLAP 引擎。但 Daft 0.7.8 无 SQL 能力，经两个 ADR 演进，DuckDB **正式成为 OLAP + Catalog 引擎**。

**DuckDB 在代码中的实际职责：**
- OLAP SQL 查询（`arrow_lake/query/olap.py`）
- 分面搜索 CUBE（`arrow_lake/query/faceted.py`）
- 元数据 SQL（`arrow_lake/query/metadata.py`）
- Catalog 元数据存储（`arrow_lake/catalog/actor.py` — DuckDB 临时文件）

### 0B. DuckDB 单节点生产方案

**渐进式解决（3 阶段）：**

| 阶段 | 方案 | 适用场景 |
|------|------|---------|
| v1.0 | 查询资源治理 (内存限制+并发控制+超时熔断) | 单节点+多并发 |
| v1.1 | MotherDuck 云托管 | 需要弹性扩展 |
| v1.2+ | DuckDB 分布式模式 | 数据量>10M行 |

**v1.0 查询资源治理（具体设计）：**

```python
# arrow_lake/query/_db.py
class DuckDBSession:
    def __init__(self, max_memory_mb: int = 2048, timeout_seconds: int = 60):
        conn = duckdb.connect()
        conn.execute(f"SET memory_limit = '{max_memory_mb}MB'")
        conn.execute(f"SET threads = {os.cpu_count()}")
        conn.execute(f"SET statement_timeout = '{timeout_seconds}s'")

# OlatConfig 新增字段
class OlapConfig(BaseModel):
    max_query_memory_mb: int = 2048
    max_concurrent_queries: int = 4
    query_timeout_seconds: int = 60

# asyncio.Semaphore 限制并发
_query_semaphore = asyncio.Semaphore(OlapConfig.max_concurrent_queries)
```

**MotherDuck 集成路径（零代码迁移）：**
- MotherDuck 提供 DuckDB 协议兼容的云服务
- 仅需替换 `duckdb.connect()` → MotherDuck 连接字符串
- Arrow Lake 通过 `OlapConfig.backend = "motherduck"` 切换

### 0C. DuckDB Lance 原生扩展 + DuckLake 分层架构 (2026-04-20)

**重大发现**: DuckDB 1.5.2 内置 Lance 扩展 + DuckLake 扩展，可直接在 Lance 文件上执行 SQL，并支持 DuckLake 可写衍生层。

**核心变更**：
- DuckDB 同时加载 `lance` 和 `ducklake` 扩展，成为统一 SQL 引擎
- `__lance_scan()` 替代 PyArrow 中间层，直接读取 Lance 文件
- `lance_vector_search()` / `lance_fts()` / `lance_hybrid_search()` 原生 SQL 函数
- DuckLake 作为可写衍生层：ETL 物化、工作区暂存、DML 支持

**已验证的 OLAP 操作（全部通过）：**

| 操作 | 验证 | 说明 |
|------|------|------|
| `SELECT` + `WHERE` | ✓ | 基础过滤 |
| `GROUP BY` + 聚合 | ✓ | 10K 行聚合 |
| `ORDER BY` + `LIMIT` | ✓ | 排序分页 |
| `JOIN` (含跨 Lance 自连接) | ✓ | 多表关联 |
| 窗口函数 (`RANK`, `PARTITION BY`) | ✓ | 分面分析 |
| `EXPLAIN` / `EXPLAIN ANALYZE` | ✓ | 查询计划 |
| `lance_vector_search` | ✓ | 需要 IVF-PQ 索引 |
| `lance_fts` | ✓ | 全文搜索 |
| `lance_hybrid_search` | ✓ | 向量+FTS RRF 融合 |

**对当前架构的影响：**

| 方面 | 当前方案 (v0.2) | v1.0 方案 |
|------|-----------------|-----------|
| **数据流 (读)** | Lance → PyArrow RecordBatchReader → `conn.register()` → SQL | Lance → DuckDB 直接 SQL (`__lance_scan`) |
| **数据流 (写)** | LanceDB SDK (`table.add`, `table.create_index`) | **不变**: LanceDB SDK 继续负责写入/索引 |
| **向量搜索** | LanceDB Python SDK (`vector.py`) | DuckDB `lance_vector_search()` (可选 LanceDB SDK fallback) |
| **FTS** | LanceDB Python SDK (`fts.py`) | DuckDB `lance_fts()` (可选 LanceDB SDK fallback) |
| **混合搜索** | Python 代码编排 RRF 融合 (`hybrid.py`) | DuckDB `lance_hybrid_search()` 原生 RRF |
| **OLAP** | DuckDB on registered Arrow 表 | `__lance_scan()` 直接读 Lance 文件 |
| **PyArrow 中间层** | 必须 (读操作) | 读操作不需要，写操作仍通过 LanceDB SDK |

**分层架构**：
```
LanceDB SDK (数据管理层)
├── 写入 (table.add) → Lance 文件
├── 索引 (create_index / create_fts_index) → Lance 索引
├── Schema 演化 (add/drop/alter columns) → Lance 文件
└── 版本管理 (list_versions / tags) → Lance 元数据

DuckDB (查询分析层)
├── lance 扩展 → Lance (只读 SSOT: 原始数据、向量、FTS)
├── ducklake 扩展 → DuckLake (可写衍生: ETL、物化、工作区)
└── 原生 SQL → JOIN 跨存储查询
```

### 0D. DuckDB + DuckLake + Lance 分层验证

**已验证的联合架构**：

```sql
LOAD lance; LOAD ducklake;
-- Lance 作为只读数据源 (SSOT)
CREATE VIEW lance_data AS SELECT * FROM __lance_scan('/path/to/lance', explain_verbose := false);
-- DuckLake 作为可写工作区
ATTACH '/path/to/ducklake' AS workspace (TYPE ducklake);
-- 物化聚合到 DuckLake
CREATE TABLE workspace.stats AS SELECT category, AVG(score) FROM lance_data GROUP BY category;
-- DuckLake DML (Lance 不支持)
INSERT INTO workspace.stats VALUES ('new', 99.9);
UPDATE workspace.stats SET score = score * 1.1 WHERE category = 'A';
-- 跨存储 JOIN
SELECT l.*, s.avg FROM lance_data l JOIN workspace.stats s ON l.category = s.category;
```

**已验证的 DuckLake 能力：**

| 能力 | 支持 | 说明 |
|------|------|------|
| `CREATE TABLE` | ✓ | Parquet 格式存储 |
| `INSERT` | ✓ | 追加写入 |
| `UPDATE` | ✓ | 原地更新 |
| `DELETE` | ✓ | 行级删除 |
| 快照/时间旅行 | ✓ | `ducklake_snapshots()` |
| 从 Lance 物化 | ✓ | `CREATE TABLE ... AS SELECT FROM __lance_scan(...)` |
| 与 Lance 跨查询 JOIN | ✓ | DuckDB 统一执行 |
| ACID 事务 | ✓ | WAL + snapshot 隔离 |

**注意事项**：
- DuckLake 使用 Parquet 内部格式，与 Lance 形成"双格式"管道
- Lance 和 Parquet NULL 表示不同 — 跨格式 JOIN 的 NULL 语义需显式处理（详见第四章）
- DuckLake 不支持 Lance 向量列（`FLOAT[]` 类型转换限制）

### 0E. 其他架构问题

| # | 问题 | 严重度 | 位置 | 修复方案 |
|---|------|--------|------|---------|
| 1 | Lake God Class (950+ 行, 30+ 方法) | HIGH | `__init__.py` | Mixin 拆分 (M0 AC) |
| 2 | S3/MinIO storage_options 未传递 | HIGH | `storage.py` | `_build_storage_options()` |
| 3 | Ingestor 线程不安全 | MEDIUM | `ingestor.py` L43-45 | asyncio 锁或队列 |
| 4 | Schema 演化能力有限 | MEDIUM | `schema.py` | M1 评估 |
| 5 | Ray 单 Named Actor Catalog | MEDIUM | `catalog/actor.py` | M0 CatalogActor SLA 定义 |

---

## 一、系统架构总览

```
                             +---------------------------+
                             |     客户端 / 前端应用       |
                             +-------------+-------------+
                                           |
                             +-------------v-------------+
                             |   API Gateway / Ingress   |
                             +-------------+-------------+
                                           |
                  +------------------------+------------------------+
                  |                        |                        |
       +----------v----------+  +----------v----------+  +----------v----------+
       |  Arrow Lake API v1  |  |  Arrow Lake API v2  |  |  Grafana Dashboard  |
       |  (现有 36 端点)      |  |  (RAG + KG 扩展)    |  |  (:3000)            |
       +----------+----------+  +----------+----------+  +---------------------+
                  |                        |
         +--------+--------+       +-------+-------+
         |  Core Lake SDK  |       |  RAG Engine   |
         |  (Lake facade)  |       |  (检索+生成)   |
         +--------+--------+       +---+-----+-----+
                  |                    |     |
         +--------v---+--------+  +---v--+  +--v-----------+
         | LanceDB SDK |        |DuckDB|  | LLM Provider   |
         | (数据管理层) |        |SQL   |  | OpenAI/vLLM    |
         | ─────────── |        |引擎  |  | Anthropic/Ollama|
         | 写入/索引    |        |(查询 │  +----------------+
         | Schema 演化  |        |分析层│
         | 版本管理/标签|        |)    |
         +--------+-----+        +--+--+-+
                  |          +------+  +--------+
                  |          |                |
         +--------v----------v-------+  +----v-----------+
         |      Lance 数据格式层     |  | DuckLake 衍生层 |
         |  列式+向量+FTS+版本管理    |  | ETL/物化/工作区  |
         +----+---------------------+  +----+-----------+
              |                            |
              +──────────┬─────────────────+
                         |
              +----------v-----------+
              |   MinIO / S3 存储    |
              |   (Lance 格式后端)    |
              |   storage_options    |
              |   媒体二进制生命周期    |
              +----------+-----------+
                         |
              +----------v-----------+
              |   本地文件系统 (开发)  |
              |   s3:// (生产)       |
              +----------------------+

         +------------------------------------------------------------+
         |                       外部依赖                             |
         |  +--------------+  +--------------+  +------------------+  |
         |  | HugeGraph    |  | LLM Providers|  | Prometheus+OTel |  |
         |  | (KG, 外部)   |  | OpenAI/vLLM  |  | (可观测性)       |  |
         |  | Gremlin      |  | Anthropic    |  +------------------+  |
         |  | Cypher       |  | Ollama      |                       |
         |  +--------------+  +--------------+  +------------------+  |
         |                                         | Ray Cluster     |  |
         |                                         | (分布式计算)     |  |
         |                                         +------------------+  |
         +------------------------------------------------------------+
```

### 分层说明

| 层级 | 组件 | 职责 |
|------|------|------|
| **API 层** | FastAPI v1/v2 | HTTP 接口、认证、版本控制 |
| **SDK 层** | Lake facade | 统一编程接口 |
| **管理层** | **LanceDB SDK** | 数据写入 / 索引创建 / Schema 演化 / 版本管理 / 数据生命周期 |
| **查询层** | DuckDB + Lance 扩展 | OLAP SQL / 向量搜索 / FTS / 混合搜索 |
| **衍生层** | DuckDB + DuckLake 扩展 | ETL 物化 / 可写工作区 / DML |
| **格式层** | **Lance** | 列式存储 + 向量索引 + 全文索引 + 版本管理 (统一数据格式) |
| **存储层** | MinIO (S3) / 本地 FS | Lance 格式的持久化后端 |
| **外部层** | HugeGraph / LLM / Ray / OTel | 知识图谱 / 生成 / 计算 / 可观测 |

### 组件职责矩阵

| 组件 | 职责 | 格式 | 读写 | v0.2 状态 | v1.0 变更 |
|------|------|------|------|----------|----------|
| **Lance** | 统一数据格式 (列式+向量+FTS+版本) | Lance 列式 | SSOT | 已有 | 独立为格式层 |
| **LanceDB SDK** | 数据管理层 (写入/索引/Schema/版本) | Python SDK | 写入+管理 | 已有 | 明确定位为管理层,与 DuckDB 互补 |
| **DuckDB** | 查询分析层 (OLAP+向量+FTS+混合) | 内存 SQL | 查询为主 | OLAP+Catalog (ADR-06) | lance+ducklake 扩展,查询治理 |
| **DuckLake** | 可写衍生层 (ETL/物化/工作区) | Parquet | 完整 DML | 不存在 | **v1.0 新增**: DuckDB 扩展加载 |
| **MinIO** | Lance 格式存储后端 (S3) | S3 对象 | 读写 | 配置就绪,未连接 | storage_options 接通,成为 Lance 生产存储 |
| **HugeGraph** | 知识图谱 (外部部署) | 图数据库 | 读写 | 不存在 | 新增: 图Schema,实体抽取,GraphRAG |
| **RAG Engine** | 检索增强生成 (DuckDB→LLM) | — | 读写 | 仅检索(R),无生成(G) | 新增: LLM 抽象,Prompt 模板,上下文管理 |
| **FastAPI REST** | HTTP 接口 | — | — | 36 端点,API Key auth | 新增: RAG/KG 端点,RBAC,版本控制 |
| **Ray Cluster** | 分布式计算 | — | — | 已有 | 新增: KG 构建作为 Ray 任务 |
| **Prometheus+OTel** | 可观测性 | — | — | 基础 metrics | 新增: traces,完整 healthcheck,告警规则 |

---

## 二、数据流总体关系

```
[原始数据] --ingest--> [LanceDB SDK] --create_index--> [Lance 格式层 (元数据+向量+索引)]
       |                      |  (数据管理层)                  |
       |                      | 写入/Schema演化/版本            |
       |                      +-------------------------------+
       |                      |
       |              +-------v-------+
       |              | MinIO / S3    | ← Lance 持久化后端 (storage_options)
       |              | (生产)         |
       |              +-------+-------+
       |                      |
       |              +-------v-------+
       |              | 本地 FS       | ← Lance 持久化后端 (开发)
       |              +---------------+
       |
       +---> [HugeGraph] ← KG Construction
       |
       +---> [MinIO 原始媒体] ← 二进制文件 (非 Lance)
                      |
                      +------------+------------+
                                   |
                          [DuckDB SQL 查询层]
                          ┌──────────────────────┐
                          │ lance 扩展:          │
                          │ · __lance_scan       │ → OLAP SQL
                          │ · lance_vector_search│ → 向量搜索
                          │ · lance_fts          │ → 全文搜索
                          │ · lance_hybrid_search│ → 混合搜索
                          ├──────────────────────┤
                          │ ducklake 扩展:       │
                          │ · ETL 物化           │ → 衍生数据
                          │ · DML (读写)         │ → 工作区
                          │ · 快照时间旅行         │ → 版本管理
                          └──────────┬───────────┘
                                     |
                 +───────────────────+───────────────────+
                 |                   |                   |
         [REST API 直查]     [RAG Engine 检索]    [Graph Traversal]
         OLAP/Faceted        |                    [HugeGraph]
         向量/FTS/混合        │                        |
                            +──┬─────────────+─────────┘
                               │             │
                    +──────────▼───┐  +───────▼──────┐
                    │ 上下文组装    │  │  图三元组      │
                    │ token预算/去重 │  │  子图序列化    │
                    +──────┬───────┘  +───────┬──────┘
                           +────────┬────────┘
                                    │
                           [LLM Provider]
                           OpenAI/Anthropic/vLLM/Ollama
                                    │
                           [Cited Response]
```

### LanceDB SDK 与 DuckDB 分工

**核心原则：LanceDB SDK 负责数据管理，DuckDB 负责查询分析。两者是互补关系，不是替代关系。**

DuckDB lance 扩展是**只读查询层** — 它可以**使用**索引但不能**创建**索引。LanceDB SDK 是**数据管理层** — 负责索引创建、Schema 演化、版本管理等管理操作。

| 操作类别 | DuckDB lance 扩展 | LanceDB Python SDK | 说明 |
|---------|:-:|:-:|------|
| **创建向量索引** (IVF-PQ) | ❌ | ✓ | `table.create_index(metric, index_type="IVF_PQ")` |
| **创建 FTS 索引** (BM25) | ❌ | ✓ | `table.create_fts_index(field_names)` |
| **向量搜索** (使用已有索引) | ✓ | ✓ | DuckDB SQL `lance_vector_search()` |
| **全文搜索** (使用已有索引) | ✓ | ✓ | DuckDB SQL `lance_fts()` |
| **混合搜索** (RRF 融合) | ✓ | ✓ (手动编排) | DuckDB 原生 `lance_hybrid_search()` |
| **OLAP SQL** (聚合/JOIN/窗口) | ✓ | ❌ | DuckDB 强项 |
| **CUBE 分面分析** | ✓ | ❌ | DuckDB 强项 |
| **数据写入** (add/optimize) | ❌ | ✓ | `table.add()`, `table.optimize()` |
| **Schema 演化** (add/drop/alter 列) | ❌ | ✓ | `table.add_columns()`, `table.drop_columns()` |
| **版本管理** (list/tags) | ❌ | ✓ | `table.list_versions()`, `table.tags.create()` |
| **索引统计** (list/stats) | ❌ | ✓ | `table.list_indices()`, `table.index_stats()` |
| **跨存储 JOIN** (Lance+DuckLake) | ✓ | ❌ | DuckDB SQL 独有能力 |

**调用关系**：

```
Ingest 流程:
  数据 → LanceDB SDK (table.add + table.create_index) → Lance 文件

Query 流程:
  REST API → DuckDB SQL (lance_vector_search / lance_fts / __lance_scan) → Lance 文件

管理流程:
  Admin API → LanceDB SDK (schema_evolution / version_management / optimize) → Lance 文件
```

**保留 LanceDB 的好处**：
1. **职责清晰** — 管理和查询分离，LanceDB SDK 负责所有写操作，DuckDB 负责所有读操作
2. **回滚路径** — DuckDB lance 扩展是前沿内部 API (`__lance_scan`)，LanceDB SDK 是稳定的纯 Python fallback
3. **性能场景** — 纯向量搜索高 QPS 场景，LanceDB SDK 直接调用 Rust 底层无 SQL 解析开销
4. **v0.2.0 兼容** — 当前 `vector.py`/`fts.py`/`hybrid.py` 基于 LanceDB SDK，迁移到 DuckDB SQL 可逐步进行
5. **客户端模式** — LanceDB 支持独立 client-server 部署 (v1.1+ 可选)

---

## 三、计算框架层：Ray / Daft / Metaflow

> 本章说明架构图中"外部依赖"区域内三个计算框架的职责、数据流、**数据流契约**、**失败域分析**和**技术选型论证**。

### 3.0 框架总览

```
                    Lake Facade (统一入口)
                    ┌──────────────────────────────┐
                    │  lake.ingest()               │
                    │  lake.query()                │
                    │  lake.daft_query()           │
                    │  lake.list_flows()           │
                    └──────┬───────┬───────┬───────┘
                           │       │       │
              ┌────────────┘       │       └────────────┐
              ▼                    ▼                      ▼
     +────────────────+  +────────────────+  +──────────────────+
     |     Ray         |  |     Daft       |  |    Metaflow      |
     |  分布式计算      |  |  DataFrame API |  |   工作流编排      |
     ├────────────────┤  ├────────────────┤  ├──────────────────┤
     │ CatalogActor   │  │ read_lance()   │  │ QualityPipeline   │
     │ @ray.remote    │  │ read_csv/json  │  │ MayaE2EFlow       │
     │ foreach()      │  │ LazyDaftFrame  │  │ ScheduledQuality  │
     │ RemoteDataLoader│ │ select/filter  │  │ @schedule @retry  │
     │ GPUAutoscaler  │  │ sort/join/     │  │ AuditTrail       │
     │ Ray Serve      │  │   groupby      │  │ StateRollback    │
     └───────┬────────┘  └───────┬────────┘  └────────┬─────────┘
             │                   │                    │
             │  metaflow-ray 插件 │                    │
             +───────────────────┼────────────────────┘
                                 │
                          ┌──────▼──────┐
                          │ Lake Facade │  Metaflow Step 调用 Lake API
                          │ (Ingest/    │  Ray 执行分布式任务
                          │  Query/     │  Daft 读取+转换数据
                          │  Catalog)   │
                          └─────────────┘
```

### 3.1 Ray — 分布式计算引擎

**职责**: 任务并行化、服务编排、GPU 调度、Catalog 分布式管理。

| 组件 | 位置 | 职责 |
|------|------|------|
| `CatalogActor` | `catalog/actor.py` | Ray Named Actor，分布式表元数据管理（内嵌 DuckDB） |
| `CatalogReadReplica` | `catalog/replica.py` | CatalogActor 的高可用读副本 |
| `foreach()` | `ray_runtime/distributed.py` | 并行 map：Arrow Table → N 分区 → @ray.remote 处理 → 合并 |
| `RemoteDataLoader` | `ray_runtime/data_loader.py` | CPU→GPU 零拷贝数据管道（预取队列 + PyTorch DataLoader） |
| `GPUAutoscaler` | `ray_runtime/autoscaler.py` | GPU 0→N 弹性伸缩（空闲超时缩容） |
| `RayServeEmbeddingEncoder` | `embed/ray_serve_encoder.py` | Ray Serve 部署的分布式 Embedding 推理 |

**CatalogActor SLA 目标**：

| 指标 | 目标 | 说明 |
|------|------|------|
| 可用性 | 99.9% (单节点) | Ray Actor 自动重启保活 |
| 读取延迟 P99 | < 10ms (本地), < 50ms (跨节点) | 内嵌 DuckDB 内存查询 |
| 写入延迟 P99 | < 100ms | register_table() + _open_lance() |
| 故障恢复时间 | < 30s | Ray Actor 自动重启 + DuckDB 持久化 |

**单点故障缓解**：
- CatalogActor 持久化到 Lance 文件（非纯内存）
- CatalogReadReplica 提供读副本
- v1.0 承认单点风险，v1.1 通过 MotherDuck 替代内嵌 DuckDB

### 3.2 Daft — 惰性 DataFrame 引擎

**职责**: 提供非 SQL 的表达式式 DataFrame API，作为 DuckDB SQL 的补充。

| 组件 | 位置 | 职责 |
|------|------|------|
| `LazyDaftFrame` | `query/daft_api.py` | 惰性 DataFrame 封装 |
| `daft.read_lance()` | `query/daft_api.py` | 直接读取 Lance 数据集 |
| `daft.read_*()` | `ingest/ingestor.py` | Ingest 阶段读取 CSV/JSON/Parquet |

**与 DuckDB 的分工**:

| 维度 | DuckDB (SQL) | Daft (DataFrame API) |
|------|-------------|---------------------|
| 查询方式 | SQL 字符串 | Python 方法链 |
| 评估策略 | 即时执行 | 惰性求值（collect() 触发） |
| 强项 | 复杂聚合、JOIN、窗口函数 | ETL 管道、schema 演化、多模态 |
| 向量搜索 | lance_vector_search() SQL | 不支持 |
| 分布式 | 单进程 | 可运行在 Ray 集群上 |
| 适用场景 | OLAP 分析、BI | 数据预处理、ETL、编程式转换 |

**v1.0 变更**: DuckDB Lance 扩展原生 SQL 后，Daft 的 OLAP 角色进一步收窄。Daft 继续承担 Ingest 文件读取和编程式 ETL。

### 3.3 Metaflow — 工作流编排

**职责**: 将多步骤数据处理管道编排为可追踪、可重试、可调度的 DAG。

| 组件 | 位置 | 职责 |
|------|------|------|
| `ArrowLakeFlowSpec` | `workflow/base.py` | 所有 Flow 的基类 Mixin |
| `FlowRegistry` | `workflow/base.py` | Flow 注册/发现/列表 |
| `QualityPipelineFlow` | `flows/quality_pipeline_flow.py` | 数据质量过滤管道 |
| `MayaE2EFlow` | `flows/maya_e2e_flow.py` | 端到端演示管道 |
| `ScheduledQualityFlow` | `flows/scheduled_quality_flow.py` | 每日定时质量检查 |
| `AuditTrail` | `workflow/audit.py` | 工作流事件审计（HMAC 完整性） |
| `StateRollback` | `workflow/rollback.py` | 检查点级状态恢复 |

**v1.0 变更**:
- KG 构建新增为 Metaflow Flow (`KnowledgeGraphBuildFlow`)
- RAG 管道可封装为 Metaflow Flow 用于批量处理
- Argo Bridge 推迟到 v1.1+（John 建议，释放工程容量）

### 3.4 三框架协作关系

```
┌──────────────────────────────────────────────────────────┐
│                    Metaflow 工作流                        │
│  (编排层: 定义步骤顺序、重试、调度、审计)                    │
│                                                          │
│  ┌─────────┐   ┌──────────┐   ┌──────────┐              │
│  │  Ingest │ → │  Quality │ → │  Embed   │   ...       │
│  │  Step   │   │  Step    │   │  Step    │              │
│  └────┬────┘   └────┬─────┘   └────┬─────┘              │
│       │              │              │                     │
│       ▼              ▼              ▼                     │
│  ┌─────────────────────────────────────────────┐         │
│  │            Lake Facade API                   │         │
│  └────────┬──────────┬───────────┬─────────────┘         │
│           │          │           │                          │
│           ▼          ▼           ▼                          │
│     ┌──────────┐ ┌────────┐ ┌──────────┐                  │
│     │   Daft   │ │ DuckDB │ │   Ray    │                  │
│     │ 文件读取  │ │ SQL OLAP│ │ 分布式    │                  │
│     │ ETL 转换  │ │ 向量/FTS│ │ Catalog  │                  │
│     │ 惰性求值  │ │ 混合搜索│ │ GPU 推理  │                  │
│     └──────────┘ └────────┘ └──────────┘                  │
│                                                         │
│  基础设施: Ray Cluster                                    │
└──────────────────────────────────────────────────────────┘
```

### 3.5 数据流契约（评审新增）

**框架间数据一致性保证**：

| 数据流路径 | 一致性模型 | 保证机制 | 最终一致窗口 |
|-----------|-----------|---------|------------|
| Ray 写 Lance → DuckDB 读 Lance | **强一致** | 同一进程内 DuckDB `__lance_scan()` 直接读 Lance 文件 | 即时 |
| Ray 写 Lance → Daft 读 Lance | **最终一致** | Daft `read_lance()` 读 Lance 版本 N，Ray 写入版本 N+1 | < 1s（文件系统 flush） |
| Metaflow Step → Lake API → Ray | **强一致** | `ray.get()` 同步等待任务完成 | 即时 |
| Daft 惰性求值 + 副作用 | **需注意** | Daft `.collect()` 之前副作用不执行 | 不可预测（见 3.6） |

### 3.6 失败域分析（评审新增）

| 失败场景 | 影响范围 | 恢复机制 | 数据丢失风险 |
|---------|---------|---------|------------|
| Ray worker 崩溃 | 正在执行的 foreach 任务 | Ray 自动重试 + `@retry` 装饰器 | 无（Lance 版本化） |
| Ray head 节点崩溃 | CatalogActor + 所有任务 | Ray 自动重启 + 检查点 | CatalogActor 内存状态丢失（需持久化） |
| Daft 任务失败 | 单个 ETL 管道 | Metaflow `@retry` + `StateRollback` | 无（Lance 写入是原子的） |
| Metaflow 流程失败 | 整个工作流 | 检查点级恢复 (`resume_from`) | 已完成步骤结果保留 |
| DuckDB OOM | 当前查询 | 超时熔断 + 内存限制 | 无（只读查询） |
| MinIO 不可用 | Lance S3 写入 | 重试 + 本地降级 | 写入队列中的数据 |

**Daft 惰性求值与副作用的特殊处理**：
- Daft 的 LazyDaftFrame 在 `.collect()` 之前不执行任何操作
- 如果管线中有副作用（写磁盘、审计事件），必须在 `.collect()` **之后**显式触发
- **规则**: Daft 管线仅用于数据转换，所有副作用（写 Lance、审计）在 Lake API 层触发

### 3.7 资源隔离策略（评审新增）

**v1.0 方案：共享集群 + 资源配额**

| 组件 | CPU 配额 | 内存配额 | GPU | 隔离方式 |
|------|---------|---------|-----|---------|
| Metaflow Steps | 无限制 (按需) | 4GB/step | 无 | 进程级 |
| Ray foreach 任务 | 2 CPU/task | 2GB/task | 可选 | Ray 资源调度 |
| Ray CatalogActor | 1 CPU | 512MB | 无 | Ray Actor |
| Ray GPU 推理 | 4 CPU | 8GB | 1 GPU | Ray GPU 调度 |
| DuckDB 查询 | `os.cpu_count()` | `max_query_memory_mb` | 无 | asyncio.Semaphore |

**v1.1+ 扩展路径**: 独立集群 + Ray 集群联邦

### 3.8 技术选型论证（评审新增）

**为什么 Ray + Daft + Metaflow 而非其他方案？**

| 维度 | 选择 | 被排除方案 | 排除理由 |
|------|------|-----------|---------|
| 分布式计算 | Ray | Dask | Ray 提供 Actor 模型 + GPU 调度 + Serve，与现有代码一致 |
| DataFrame | Daft | Polars | Daft 原生支持 Lance 读取 + 多模态文件，代码已集成 |
| 工作流编排 | Metaflow | Prefect/Airflow | Metaflow 适合数据科学工作流，Python 原生，代码已集成 |
| GPU 调度 | Ray Autoscaler | Kubernetes GPU | Ray 在异构 GPU 场景更灵活，代码已有 `GPUAutoscaler` |

---

## 四、DuckDB Lance 原生集成设计（评审新增核心章节）

> 本章是对评审中 **Winston W1/W2/W3/W9, Amelia A1/A2/A3/A4** 等阻断项的完整响应。

### 4.1 DuckDB 版本锁定策略 (W1)

**决策**: 锁定 `duckdb==1.5.2`，不自动升级。

**可用性矩阵**：

| DuckDB 版本 | Lance 扩展 | DuckLake 扩展 | 状态 |
|------------|-----------|-------------|------|
| 1.5.2 | ✓ 内置 | ✓ 内置 | **锁定版本** |
| 1.5.1 | ✗ 无内置 | ✗ 无内置 | 不兼容 |
| 1.6.x (未来) | 待验证 | 待验证 | 升级前必须全量回归测试 |

**版本锁定实现**：
```toml
# pyproject.toml
[project.dependencies]
duckdb = "==1.5.2"  # 锁定，非 >=
```

**升级流程**：
1. 新版本发布后，在独立分支运行 `tests/integration/test_duckdb_extensions.py`
2. 验证 `__lance_scan()`, `lance_vector_search()`, `lance_fts()`, `lance_hybrid_search()` 全部通过
3. 验证 DuckLake `ATTACH TYPE ducklake` + DML + 跨存储 JOIN 全部通过
4. 性能回归测试 (OLAP 基线对比)
5. 全部通过后更新版本号 + 合并

### 4.2 扩展加载策略 (A3)

**决策**: 启动时加载 + Docker pre-bundle + fast-fail。

```python
# arrow_lake/query/_db.py
class DuckDBSession:
    """DuckDB session with extension loading and resource governance."""

    REQUIRED_EXTENSIONS = ["lance", "ducklake"]

    def __init__(self, *, max_memory_mb: int = 2048,
                 timeout_seconds: int = 60,
                 load_ducklake: bool = True):
        conn = duckdb.connect()
        self._load_extensions(conn, load_ducklake)
        self._configure_resources(conn, max_memory_mb, timeout_seconds)
        self._conn = conn

    def _load_extensions(self, conn, load_ducklake: bool) -> None:
        """Load required extensions. Fast-fail on any failure."""
        for ext in ["lance"]:
            try:
                conn.execute(f"INSTALL {ext}")
                conn.execute(f"LOAD {ext}")
            except Exception as e:
                raise RuntimeError(
                    f"Failed to load DuckDB extension '{ext}'. "
                    f"This is a startup-critical dependency. Error: {e}"
                ) from e
        if load_ducklake:
            try:
                conn.execute("INSTALL ducklake")
                conn.execute("LOAD ducklake")
            except Exception as e:
                raise RuntimeError(
                    f"Failed to load DuckDB extension 'ducklake'. "
                    f"Ensure ducklake extension is pre-bundled. Error: {e}"
                ) from e

    def _configure_resources(self, conn, max_memory_mb: int, timeout_seconds: int) -> None:
        conn.execute(f"SET memory_limit = '{max_memory_mb}MB'")
        conn.execute(f"SET threads = {os.cpu_count()}")
        conn.execute(f"SET statement_timeout = '{timeout_seconds}s'")
```

**Docker pre-bundle**：
```dockerfile
# deploy/Dockerfile
RUN python -c "import duckdb; c=duckdb.connect(); c.execute('INSTALL lance'); c.execute('LOAD lance'); c.execute('INSTALL ducklake'); c.execute('LOAD ducklake'); print('Extensions pre-bundled')"
```

### 4.3 `__lance_scan()` 抽象层 (A1 CRITICAL, W9 HIGH)

**决策**: 通过 `_base.py` `LanceScanAdapter` 抽象，任何模块不得直接调用 `__lance_scan()`。

```python
# arrow_lake/query/_base.py 扩展
from abc import ABC, abstractmethod
from typing import Any

class LanceScanAdapter(ABC):
    """Abstract adapter for Lance dataset scanning.

    Isolates callers from the internal __lance_scan() API.
    If DuckDB removes or renames __lance_scan(), only this
    adapter needs to change.
    """

    @abstractmethod
    def scan(self, conn: Any, dataset_uri: str, **kwargs: Any) -> Any:
        """Execute a scan and return a DuckDB result object."""
        ...

    @abstractmethod
    def create_view(self, conn: Any, view_name: str, dataset_uri: str) -> None:
        """Create a DuckDB VIEW over a Lance dataset."""
        ...

    @abstractmethod
    def is_available(self, conn: Any) -> bool:
        """Check if the native Lance extension is available."""
        ...


class NativeLanceScanAdapter(LanceScanAdapter):
    """Uses DuckDB's built-in __lance_scan() — zero-copy, fastest path."""

    def scan(self, conn, dataset_uri: str, **kwargs):
        return conn.execute(
            f"SELECT * FROM __lance_scan(?, explain_verbose := false)",
            [dataset_uri]
        )

    def create_view(self, conn, view_name: str, dataset_uri: str) -> None:
        conn.execute(
            f"CREATE OR REPLACE VIEW {view_name} AS "
            f"SELECT * FROM __lance_scan(?, explain_verbose := false)",
            [dataset_uri]
        )

    def is_available(self, conn: Any) -> bool:
        try:
            conn.execute("SELECT 1 FROM __lance_scan('', explain_verbose := false) LIMIT 0")
            return True
        except Exception:
            return False


class PyArrowFallbackAdapter(LanceScanAdapter):
    """Fallback path: PyArrow RecordBatchReader → conn.register().

    Used when:
    - DuckDB Lance extension fails to load
    - Running in environments without the extension
    - A/B testing or migration scenarios
    """

    def scan(self, conn, dataset_uri: str, **kwargs):
        import lance
        dataset = lance.dataset(dataset_uri)
        reader = dataset.to_table().to_reader()
        conn.register("_lance_fallback", reader)
        return conn.execute("SELECT * FROM _lance_fallback")

    def create_view(self, conn, view_name: str, dataset_uri: str) -> None:
        import lance
        dataset = lance.dataset(dataset_uri)
        table = dataset.to_table()
        conn.register(view_name, table)

    def is_available(self, conn: Any) -> bool:
        return True  # PyArrow always available


def create_lance_scan_adapter(conn: Any) -> LanceScanAdapter:
    """Factory: try native first, fall back to PyArrow."""
    native = NativeLanceScanAdapter()
    if native.is_available(conn):
        return native
    return PyArrowFallbackAdapter()
```

**Feature flag 支持 (W9 回滚路径)**：
```python
# arrow_lake/config.py
class OlapConfig(BaseModel):
    lance_scan_mode: str = "native"  # "native" | "pyarrow_fallback" | "auto"

# 在 _db.py 中:
def create_lance_scan_adapter(conn, mode: str) -> LanceScanAdapter:
    if mode == "native":
        return NativeLanceScanAdapter()
    if mode == "pyarrow_fallback":
        return PyArrowFallbackAdapter()
    return create_lance_scan_adapter(conn)  # auto-detect
```

**规则**: **任何文件不得直接调用 `__lance_scan()`**。所有 Lance 数据集访问必须通过 `LanceScanAdapter`。

### 4.4 `ATTACH TYPE lance` 表发现问题 (A2)

**已知问题**: DuckDB 1.5.2 的 `ATTACH ... TYPE lance` 存在表发现不一致 — `lance_ro.data` 在某些上下文中工作，在其他上下文中失败。

**解决方案**: 不使用 `ATTACH TYPE lance`。统一使用 `CREATE VIEW ... AS SELECT FROM __lance_scan(...)` 替代。

```sql
-- 错误: 不可靠
ATTACH '/path/to/lance' AS lance_ds (TYPE lance);
SELECT * FROM lance_ds.data;  -- 可能失败

-- 正确: 可靠
CREATE VIEW lance_ds AS
SELECT * FROM __lance_scan('/path/to/lance', explain_verbose := false);
SELECT * FROM lance_ds;  -- 总是工作
```

**测试要求**: M0 必须包含 `information_schema` 列发现测试，验证 `CREATE VIEW` 模式下 `metadata.py`/`faceted.py` 的列发现功能正常。

### 4.5 `storage_options` Schema 定义 (A4)

**决策**: 全局统一，从 `StorageConfig` 派生。

```python
# arrow_lake/config.py
class StorageConfig(BaseModel):
    """Storage backend configuration."""

    backend: str = "local"  # "local" | "minio"
    base_uri: str = "./data"

    # S3/MinIO credentials
    s3_region: str = "us-east-1"
    s3_endpoint: str = ""       # "http://minio:9000"
    s3_access_key: str = ""
    s3_secret_key: str = ""
    s3_bucket: str = "arrow-lake"

    @property
    def s3_uri(self) -> str:
        if self.backend == "minio":
            return f"s3://{self.s3_bucket}/data"
        return self.base_uri

    def to_storage_options(self) -> dict[str, str] | None:
        """Build storage_options dict for Lance/DuckDB."""
        if self.backend == "local":
            return None
        return {
            "region": self.s3_region,
            "endpoint_url": self.s3_endpoint,
            "aws_access_key_id": self.s3_access_key,
            "aws_secret_access_key": self.s3_secret_key,
            "allow_anonymous": "false",
        }

    def to_duckdb_s3_config(self) -> list[str]:
        """Build DuckDB SET statements for S3 access."""
        if self.backend == "local":
            return []
        return [
            f"SET s3_region='{self.s3_region}'",
            f"SET s3_endpoint='{self.s3_endpoint}'",
            f"SET s3_access_key_id='{self.s3_access_key}'",
            f"SET s3_secret_access_key='{self.s3_secret_key}'",
        ]
```

**环境变量派生** (4 层覆盖)：
```python
# 优先级: YAML > 环境变量 > .env > 代码默认值
class StorageConfig(BaseModel):
    s3_endpoint: str = ""
    # 环境变量自动派生
    @classmethod
    def from_env(cls):
        return cls(
            s3_endpoint=os.getenv("S3_ENDPOINT", ""),
            s3_access_key=os.getenv("AWS_ACCESS_KEY_ID", ""),
            s3_secret_key=os.getenv("AWS_SECRET_ACCESS_KEY", ""),
            s3_region=os.getenv("AWS_REGION", "us-east-1"),
            s3_bucket=os.getenv("S3_BUCKET", "arrow-lake"),
        )
```

### 4.6 错误边界定义 (W3)

**Lance 扩展与 DuckDB 之间的错误传播策略**：

| 错误场景 | DuckDB 行为 | Arrow Lake 处理 |
|---------|------------|----------------|
| Lance 文件损坏 | `IOException: Invalid lance file` | 捕获 → `LanceFileCorruptedError` → 建议恢复 |
| 索引过期 (IVF-PQ) | `IOException: Index not found` | 自动重建索引 → 重试 |
| Lance 版本不兼容 | `IOException: Unsupported version` | `LanceVersionError` → 阻断启动 |
| S3 连接超时 | `IOException: Connection timeout` | 重试 3 次 → 降级到本地 |
| DuckLake 扩展加载失败 | `IOException: Cannot load extension` | Fast-fail → 阻断启动 |
| `__lance_scan()` 被移除 | `CatalogException` | 自动切换到 `PyArrowFallbackAdapter` |
| DuckDB OOM | `OutOfMemoryException` | 查询取消 → 释放资源 → 返回 503 |

**错误码定义**：

```python
# arrow_lake/exceptions.py 新增
class LanceExtensionError(ArrowLakeError):
    """Base error for DuckDB Lance extension issues."""
    error_code = ErrorCode.LANCE_EXTENSION_ERROR

class LanceScanError(LanceExtensionError):
    """__lance_scan() execution failed."""
    error_code = ErrorCode.LANCE_SCAN_FAILED

class DuckLakeExtensionError(LanceExtensionError):
    """DuckLake extension load or execution failed."""
    error_code = ErrorCode.DUCKLAKE_EXTENSION_ERROR
```

### 4.7 跨存储 JOIN 复杂度预算 (W4)

**问题**: DuckDB 内存中同时拉取 Lance + DuckLake 数据可能导致 OOM。

**缓解策略**：

| 策略 | 实现 | 触发条件 |
|------|------|---------|
| **查询超时熔断** | `statement_timeout = 60s` | 所有查询 |
| **内存限制** | `memory_limit = 2048MB` | 所有查询 |
| **行数预算** | `LIMIT` 强制限制 | 用户未指定 LIMIT 时默认 10000 |
| **DuckLake 数据生命周期** | 自动清理 > 7 天的临时物化表 | Cron 任务 |
| **DuckLake TTL** | 创建时标注 `expires_at` | 每次查询前检查 |
| **降级路径** | Lance JOIN 失败 → 仅查 DuckLake | 超时或 OOM 后 |

```python
# DuckLake 工作区生命周期管理
class DuckLakeWorkspace:
    MAX_JOIN_ROWS = 100_000
    DEFAULT_TTL_DAYS = 7

    def materialize(self, conn, table_name: str, query: str, ttl_days: int = DEFAULT_TTL_DAYS) -> int:
        row_count = conn.execute(f"SELECT COUNT(*) FROM ({query})").fetchone()[0]
        if row_count > self.MAX_JOIN_ROWS:
            raise DuckLakeQuotaExceededError(
                f"Materialization would produce {row_count} rows, "
                f"exceeding limit of {self.MAX_JOIN_ROWS}"
            )
        conn.execute(f"CREATE TABLE workspace.{table_name} AS {query}")
        # 标注过期时间
        conn.execute(
            f"INSERT INTO workspace._metadata (table_name, expires_at) "
            f"VALUES ('{table_name}', NOW() + INTERVAL '{ttl_days} days')"
        )
```

### 4.8 跨格式 NULL 语义 (A8)

**问题**: Lance 和 Parquet (DuckLake) 的 NULL 表示不同。

| 格式 | NULL 表示 | NaN 表示 |
|------|----------|---------|
| Lance | Apache Arrow null bitmap | IEEE 754 NaN |
| Parquet (DuckLake) | Parquet null definition levels | IEEE 754 NaN |

**规则**:
- 跨格式 JOIN 时，NULL = NULL 为 `FALSE`（SQL 标准语义）
- DuckDB 的 `COALESCE()` 可统一 NULL 处理
- 向量列 (`FLOAT[]`) 不写入 DuckLake（已有限制，见 0D）
- **M0 需要测试覆盖**: Lance NULL 列 → DuckLake → JOIN 回 Lance 的 NULL 保留

### 4.9 Schema 演化传播 (Winston 缺失项)

**问题**: Lance 新增列后，DuckDB catalog 是否自动刷新？

**决策**: 使用 `CREATE VIEW` 模式（非 `ATTACH`），每次查询时 DuckDB 从 Lance 文件头读取最新 schema。

```
时间线:
  T1: Lance 数据集有 [col_a, col_b]
  T2: Ingest 添加 col_c → Lance 文件自动支持 schema evolution
  T3: DuckDB `CREATE VIEW v AS SELECT * FROM __lance_scan(path)`
      → 自动包含 col_a, col_b, col_c（无需手动刷新）
```

**限制**:
- DuckDB 不支持删除列（Lance 也不支持）— 仅支持 `add_columns`
- DuckLake (Parquet) 支持完整 `ALTER TABLE`，包括删列
- 跨存储 JOIN 时以 Lance schema 为主

### 4.10 多租户隔离 (Winston 缺失项)

**v1.0 方案**: 数据集级 RBAC + DuckDB session 隔离。

| 隔离维度 | v1.0 实现 | v1.1+ 扩展 |
|---------|----------|-----------|
| 数据隔离 | DuckLake workspace 按租户分离 + RBAC | 独立 DuckDB 实例 |
| 查询隔离 | `asyncio.Semaphore` + `memory_limit` | 资源池 + 查询队列 |
| 认证 | JWT token → Role 映射 | OAuth 2.0 / SAML |

**RBAC 数据集级权限**:

```python
# arrow_lake/security/rbac.py
class Permission(StrEnum):
    READ = "read"
    WRITE = "write"
    ADMIN = "admin"

class Role(StrEnum):
    ADMIN = "admin"             # 全部数据集读写管理
    DATA_ENGINEER = "data_engineer"  # 全部数据集读写
    ANALYST = "analyst"         # 指定数据集只读
    VIEWER = "viewer"           # 指定数据集只读, 无导出

# 数据集级权限映射
class DatasetACL:
    def check_permission(self, user_role: Role, dataset_name: str,
                         permission: Permission) -> bool:
        ...
```

---

## 五、模块设计

### 5A. Lance 格式存储后端 (MinIO/S3)

**定位**: MinIO 不是独立存储组件，而是 **Lance 格式的 S3 存储后端**。

**核心修改** — `arrow_lake/ingest/storage.py`:

```python
@staticmethod
def _build_storage_options(config: StorageConfig | None) -> dict[str, str] | None:
    if config is None or config.backend == StorageBackend.LOCAL:
        return None
    return config.to_storage_options()
```

**新增** — `arrow_lake/storage/blob_store.py`:

```python
class BlobStoreManager:
    """MinIO/S3 二进制对象管理器 — 与 LanceStorageManager 互补"""

    def upload_media(self, dataset_name: str, file_id: str,
                     data: bytes, content_type: str) -> str: ...
    def download_media(self, s3_uri: str) -> bytes: ...
    def get_presigned_url(self, s3_uri: str, expires: int = 3600) -> str: ...
    def delete_media(self, s3_uri: str) -> None: ...
    def list_media(self, dataset_name: str, prefix: str = "") -> list[str]: ...
```

**媒体存储分离策略**:

| 数据类型 | 存储位置 | LanceDB 保留 |
|---------|---------|-------------|
| 文本内容 | LanceDB | text_content 列 |
| 向量嵌入 | LanceDB | embedding 列 |
| 缩略图 | LanceDB | thumbnail_bytes 列 (小) |
| 预览图 | MinIO + LanceDB 引用 | preview_s3_uri 列 |
| 原始图片/视频/音频 | MinIO | original_s3_uri 列 |
| EXIF/元数据 | LanceDB | metadata 列 |

### 5B. 多模态 RAG Pipeline

**RAG Engine 是 DuckDB SQL 查询层的消费者，不直接操作 Lance 文件。** 其检索部分通过 DuckDB lance 扩展访问已索引的 Lance 数据，生成部分通过 LLM Provider 访问外部大模型服务。

```
RAG Engine 内部数据流:

  [用户问题]
      │
  ┌───▼───────────────────────────────────────────┐
  │              RAG Pipeline                      │
  │                                               │
  │  ┌─────────────────────────────────────────┐  │
  │  │ 1. 检索层 (R) — DuckDB lance 扩展       │  │
  │  │    ├─ lance_vector_search() → 向量检索   │  │
  │  │    ├─ lance_fts()          → 全文检索   │  │
  │  │    ├─ lance_hybrid_search()→ 混合检索   │  │
  │  │    └─ __lance_scan()       → OLAP 过滤  │  │
  │  └──────────────┬──────────────────────────┘  │
  │                 │                             │
  │  ┌──────────────▼──────────────────────────┐  │
  │  │ 2. 上下文组装 — Python                   │  │
  │  │    ├─ Token 预算管理 (ContextWindow)     │  │
  │  │    ├─ 结果去重 + 引用追踪               │  │
  │  │    └─ 图三元组合并 (GraphRAG)            │  │
  │  └──────────────┬──────────────────────────┘  │
  │                 │                             │
  │  ┌──────────────▼──────────────────────────┐  │
  │  │ 3. 生成层 (G) — LLM Provider            │  │
  │  │    ├─ Prompt 渲染 (Jinja2 模板)         │  │
  │  │    ├─ LLM 同步/流式调用                  │  │
  │  │    └─ 引用标注 + 返回                   │  │
  │  └─────────────────────────────────────────┘  │
  └───────────────────────────────────────────────┘
```

**关键依赖关系**：
- **DuckDB** (查询层): RAG 的向量/FTS/混合检索全部走 DuckDB lance 扩展 SQL，不直接调用 LanceDB SDK
- **HugeGraph** (外部): GraphRAG 的图遍历走 HugeGraph Gremlin API
- **LLM Provider** (外部): RAG 的生成走 OpenAI/Anthropic/vLLM/Ollama，由 `LLMConfig` 配置

**新增** — `arrow_lake/rag/`:

| 文件 | 职责 |
|------|------|
| `provider.py` | LLM 抽象层 (OpenAI/Anthropic/vLLM/Ollama 工厂) |
| `prompt.py` | Jinja2 Prompt 模板系统 (QA/总结/抽取/多模态) |
| `context.py` | 上下文窗口管理 (token 预算 + 去重 + 引用追踪) |
| `pipeline.py` | RAG 管线编排 (检索→组装→生成→引用) |
| `graph_rag.py` | GraphRAG 增强 (向量+图遍历三路 RRF 融合) |

**LLM Provider 抽象**:

```python
class LLMProvider(StrEnum):
    OPENAI = "openai"
    ANTHROPIC = "anthropic"
    VLLM = "vllm"
    OLLAMA = "ollama"

@dataclass(frozen=True)
class LLMResponse:
    content: str
    model: str
    usage: dict[str, int]
    finish_reason: str

class BaseLLMProvider(ABC):
    @abstractmethod
    async def generate(self, messages: list[LLMMessage], *,
                       temperature: float = 0.7, max_tokens: int = 2048) -> LLMResponse: ...
    @abstractmethod
    async def generate_stream(self, messages: list[LLMMessage], **kwargs) -> AsyncIterator[str]: ...

def create_llm_provider(config: LLMConfig) -> BaseLLMProvider: ...
```

**RAG Pipeline**:

```python
class RAGPipeline:
    def __init__(self, lake: Lake, llm_provider: BaseLLMProvider, *,
                 retrieval_strategy: str = "hybrid",
                 prompt_template: PromptTemplate | None = None,
                 context_window: ContextWindow | None = None,
                 enable_citations: bool = True): ...

    async def query(self, question: str, *, dataset_name: str,
                    top_k: int = 10, filters: dict | None = None) -> RAGResponse: ...
    async def query_stream(self, question: str, *,
                           dataset_name: str, top_k: int = 10) -> AsyncIterator[str]: ...
```

**GraphRAG 增强**:

```python
class GraphRAGPipeline(RAGPipeline):
    """图增强 RAG — 融合向量检索 + 知识图谱遍历"""

    async def query(self, question: str, *, dataset_name: str,
                    graph_traversal_depth: int = 2,
                    graph_weight: float = 0.3) -> RAGResponse:
        """
        Pipeline:
        1. LLM 抽取问题中的实体
        2. HugeGraph 中查找实体节点
        3. 多跳遍历获取关联子图
        4. 与向量/FTS 检索结果合并
        5. 上下文组装 (文本 + 图三元组)
        6. LLM 生成带引用的回答
        """
```

**REST 端点** — `/api/v2/rag/`:

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/rag/query` | RAG 问答 (同步) |
| POST | `/rag/query/stream` | RAG 问答 (SSE 流式) |
| POST | `/rag/extract` | 从指定数据集抽取实体+关系 |
| GET | `/rag/templates` | 列出可用 Prompt 模板 |
| GET | `/rag/history/{session_id}` | 获取会话历史 |

### 5C. HugeGraph 知识图谱

**新增** — `arrow_lake/knowledge_graph/`:

| 文件 | 职责 |
|------|------|
| `client.py` | HugeGraph REST 客户端封装 |
| `schema.py` | 图 Schema 定义 |
| `extractor.py` | LLM 驱动实体+关系抽取 |
| `builder.py` | KG 构建管线 |
| `retriever.py` | 图增强检索 |
| `queries.py` | 预定义 Gremlin 查询模板 |

**图 Schema**:

顶点标签: document, chunk, entity, person, organization, location, concept, event

边标签: contains_chunk, references, next_chunk, related_to, part_of, belongs_to, located_in, participates_in, depicts

**REST 端点** — `/api/v2/kg/`:

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/kg/build` | 触发 KG 构建 (异步) |
| GET | `/kg/build/{task_id}/status` | 查询构建状态 |
| GET | `/kg/schema` | 获取当前图 Schema |
| POST | `/kg/query` | Gremlin/Cypher 查询 |
| GET | `/kg/entities/{id}/neighbors` | 获取实体邻居 |
| GET | `/kg/stats` | 图统计信息 |
| DELETE | `/kg/graph` | 清空图数据 |

### 5D. 生产基础设施 (评审 P0)

#### 5D.1 CI/CD Pipeline

```yaml
# .github/workflows/ci.yml
jobs:
  lint-and-test:
    services:
      minio:
        image: minio/minio:latest
        ports: ["9000:9000"]
    steps:
      - ruff check + format
      - mypy arrow_lake/
      - pytest tests/unit/ --cov
      - pytest tests/integration/  # MinIO 真实集成
      - bandit -r arrow_lake/     # 安全扫描

  build-and-push:
    needs: lint-and-test
    if: github.ref == 'refs/heads/master'
    steps:
      - docker build + push
```

#### 5D.2 可观测性 (Winston 缺失项)

**OpenTelemetry 集成** — `arrow_lake/core/tracing.py`:

```python
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter

def setup_tracing(service_name: str = "arrow-lake", otlp_endpoint: str = "localhost:4317"):
    provider = TracerProvider()
    provider.add_span_processor(
        BatchSpanProcessor(OTLPSpanExporter(endpoint=otlp_endpoint))
    )
    trace.set_tracer_provider(provider)
```

**Trace Span 覆盖**:

| 操作 | Span 名称 | 关键属性 |
|------|----------|---------|
| Ingest | `arrow_lake.ingest` | dataset, row_count, duration_ms |
| OLAP 查询 | `arrow_lake.olap.query` | sql_hash, row_count, duration_ms |
| 向量搜索 | `arrow_lake.vector.search` | dataset, k, ef, duration_ms |
| RAG 查询 | `arrow_lake.rag.query` | strategy, top_k, llm_model, duration_ms |
| KG 构建 | `arrow_lake.kg.build` | dataset, entity_count, duration_ms |
| DuckDB 扩展加载 | `arrow_lake.duckdb.extensions` | extensions, success |
| Lance S3 操作 | `arrow_lake.lance.s3` | operation, uri, bytes |

**健康检查分离**:

| 端点 | 检查内容 | 用途 |
|------|---------|------|
| `/health/live` | 进程存活 | K8s livenessProbe |
| `/health/ready` | LanceDB + MinIO + HugeGraph + Ray + DuckDB | K8s readinessProbe |

#### 5D.3 RBAC (Mary M4)

```python
class Role(StrEnum):
    ADMIN = "admin"
    DATA_ENGINEER = "data_engineer"
    ANALYST = "analyst"
    VIEWER = "viewer"

# 双模式认证: JWT (优先) 或 API Key (向后兼容)
class AuthMiddleware(BaseHTTPMiddleware): ...
```

#### 5D.4 API 版本控制

- `/api/v1/*` — 现有 36 端点，完全保留
- `/api/v2/*` — 新增 RAG + KG 端点

#### 5D.5 备份恢复 (Winston 缺失项)

```python
class BackupManager:
    """LanceDB + MinIO + HugeGraph 备份恢复"""

    async def create_backup(self, *, include_lance: bool = True,
                            include_minio: bool = True,
                            include_hugegraph: bool = True) -> BackupReport: ...

    async def restore_backup(self, backup_id: str) -> RestoreReport: ...

    async def list_backups(self) -> list[BackupInfo]: ...

    async def cleanup_old_backups(self, keep_count: int = 5) -> None: ...
```

**备份策略**:

| 组件 | 备份方式 | 频率 | 保留 |
|------|---------|------|------|
| Lance 数据 | `mc mirror` MinIO → 备份桶 | 每日 | 7 天 |
| DuckLake 工作区 | DuckLake snapshot 导出 | 每日 | 3 天 (衍生数据可重建) |
| HugeGraph | Gremlin dump → JSON | 每周 | 4 周 |
| 配置 | YAML + .env 版本化 (Git) | 每次变更 | Git 历史 |
| RBAC 策略 | JSON 导出 | 每次变更 | Git 历史 |

---

## 六、数据流详细设计

### 6.1 增强摄取流程

```
[原始文件]
    |
    v
+--- Ingestor.ingest_mixed() ---+
|                               |
| 1. 文件分类                   |
|    ├─ 文本 -> text_content    |
|    ├─ 图片 -> ImageProcessor  |
|    └─ 视频 -> VideoProcessor  |
|                               |
| 2. 向量化 (Local/API)          |
|                               |
| 3. 质量过滤                   |
|    └─ QualityFilterRegistry   |
|                               |
| 4. [新增] 媒体上传 MinIO      |
|    ├─ original -> MinIO       |
|    └─ 存 S3 URI 到 LanceDB   |
|                               |
| 5. [新增] 实体抽取            |
|    └─ EntityExtractor        |
|                               |
| 6. 写入 LanceDB                |
|    └─ metadata + vectors      |
|                               |
| 7. [新增] 构建 KG              |
|    └─ KGBuilder -> HugeGraph |
|                               |
| 8. [新增] 审计记录             |
|    └─ AuditTrail.record()      |
+-------------------------------+
```

### 6.2 RAG 查询流程

```
[用户问题] "谁参与了XX事件,结果如何?"
    |
    v
+--- RAGPipeline.query() ---+
|                            |
| 1. 实体识别                 |
|    └─ LLM: 抽取"XX事件"     |
|                            |
| 2. 并行检索 (via DuckDB)   |
|    ├─ lance_vector_search()│ → DuckDB → Lance 索引
|    ├─ lance_fts()          │ → DuckDB → Lance 索引
|    └─ 图遍历 (HugeGraph)    │ → Gremlin API
|       └─ 找到事件节点        |
|       └─ BFS -> 参与者        |
|       └─ 子图三元组           |
|                            |
| 3. RRF 融合 (DuckDB SQL    |
|    或 lance_hybrid_search)  |
|                            |
| 4. 上下文组装               |
|    ├─ 文本 + 图三元组         |
|    ├─ Token 预算管理         |
|    └─ 去重                    |
|                            |
| 5. Prompt 渲染               |
|                            |
| 6. LLM 生成 (via Provider)  |
|    └─ OpenAI/Anthropic/vLLM |
|                            |
| 7. 引用标注 + 返回            |
+----------------------------+
```

### 6.3 DuckLake ETL 物化流程

```
[Lance 只读 SSOT]
    |
    v
+--- DuckDB SQL ---+
|                   |
| 1. CREATE VIEW    |
|    __lance_scan   |
|                   |
| 2. OLAP 聚合      |
|    GROUP BY / CUBE|
|                   |
| 3. CREATE TABLE   |
|    workspace.*    |
|    (物化到 DuckLake)|
|                   |
| 4. DML 操作       |
|    INSERT/UPDATE  |
|                   |
| 5. 快照标记       |
|    expires_at     |
+-------------------+
```

---

## 七、文件变更清单

### 新增文件 (25 个)

```
arrow_lake/rag/__init__.py
arrow_lake/rag/provider.py           # LLM 抽象层
arrow_lake/rag/prompt.py             # Prompt 模板系统
arrow_lake/rag/context.py            # 上下文窗口管理
arrow_lake/rag/pipeline.py           # RAG 管线编排
arrow_lake/rag/graph_rag.py          # GraphRAG 增强
arrow_lake/knowledge_graph/__init__.py
arrow_lake/knowledge_graph/client.py # HugeGraph REST 客户端
arrow_lake/knowledge_graph/schema.py # 图 Schema 定义
arrow_lake/knowledge_graph/extractor.py # 实体抽取
arrow_lake/knowledge_graph/builder.py   # KG 构建管线
arrow_lake/knowledge_graph/retriever.py # 图检索
arrow_lake/knowledge_graph/queries.py   # 查询模板
arrow_lake/storage/__init__.py
arrow_lake/storage/blob_store.py     # MinIO 管理
arrow_lake/security/__init__.py
arrow_lake/security/rbac.py         # RBAC
arrow_lake/security/jwt_auth.py     # JWT
arrow_lake/security/middleware.py    # 认证中间件
arrow_lake/ops/__init__.py
arrow_lake/ops/backup.py             # 备份恢复
arrow_lake/core/tracing.py           # OpenTelemetry
arrow_lake/api/routers/rag.py        # RAG 端点
arrow_lake/api/routers/knowledge_graph.py # KG 端点
arrow_lake/query/lance_adapter.py    # LanceScanAdapter (从 _base.py 扩展)
```

### 修改文件 (10 个)

| 文件 | 修改内容 |
|------|---------|
| `arrow_lake/ingest/storage.py` | storage_options 传递, config 注入 |
| `arrow_lake/ingest/media.py` | 上传原始媒体到 MinIO |
| `arrow_lake/config.py` | 新增 LLMConfig, HugeGraphConfig, SecurityConfig, StorageConfig 增强 |
| `arrow_lake/exceptions.py` | 新增 RAG/KG/Security/LanceExtension 错误码 |
| `arrow_lake/__init__.py` | Lake facade Mixin 拆分 + RAG/KG API |
| `arrow_lake/query/_db.py` | DuckDBSession 扩展: 扩展加载 + 资源治理 + LanceScanAdapter |
| `arrow_lake/query/_base.py` | 新增 LanceScanAdapter Protocol |
| `arrow_lake/api/app.py` | v2 路由注册, JWT 中间件 |
| `arrow_lake/api/auth.py` | 双模式认证 |
| `arrow_lake/api/routers/system.py` | 增强健康检查 (liveness/readiness 分离) |

### Lake Facade 分解策略

```python
# 通过 Mixin 模式拆分，不改变现有方法签名
class Lake(_LakeIngestMixin, _LakeSearchMixin, _LakeRAGMixin, _LakeKGMixin):
    """API 完全不变 — 仅内部拆分"""
```

**M0 必须完成的分解 AC (A5)**：

| Mixin | 方法数 | 来源 |
|-------|--------|------|
| `_LakeIngestMixin` | ~8 | ingest, embed, quality_filter, media |
| `_LakeSearchMixin` | ~12 | vector_search, fts, hybrid, olap, faceted, export |
| `_LakeAdminMixin` | ~6 | catalog, list_datasets, delete, config |
| `_LakeRAGMixin` | ~3 | M2 新增 (rag_query, rag_extract, rag_history) |
| `_LakeKGMixin` | ~3 | M3 新增 (kg_build, kg_query, kg_stats) |

---

## 八、新增依赖

```toml
# [project.dependencies]
duckdb = "==1.5.2"              # 锁定版本 (W1)
openai>=1.50, anthropic>=0.40, jinja2>=3.1,
opentelemetry-api>=1.28, opentelemetry-sdk>=1.28, opentelemetry-exporter-otlp>=1.28,
pyjwt>=2.9, passlib[bcrypt]>=1.7

# [project.optional-dependencies]
hugegraph = ["hugegraph-client>=1.5"]
ollama = ["ollama>=0.4"]
```

---

## 九、配置设计

### 新增配置段

```python
class LLMConfig(BaseModel):
    provider: str = "openai"
    model: str = "gpt-4o-mini"
    api_key: str = ""
    api_base: str = ""
    temperature: float = 0.7
    max_tokens: int = 2048
    context_window_tokens: int = 128000
    timeout_seconds: float = 60.0

class HugeGraphConfig(BaseModel):
    enabled: bool = False
    host: str = "localhost"
    port: int = 8080
    graph_name: str = "arrow_lake_kg"
    timeout_seconds: float = 30.0
    username: str = ""
    password: str = ""
    auto_build_on_ingest: bool = False
    build_batch_size: int = 50
    default_traversal_depth: int = 2
    max_traversal_depth: int = 5

class SecurityConfig(BaseModel):
    auth_mode: str = "api_key"     # "api_key"|"jwt"|"both"
    jwt_secret_key: str = ""
    jwt_algorithm: str = "HS256"
    jwt_expiration_hours: int = 24

class OlapConfig(BaseModel):
    lance_scan_mode: str = "auto"  # "native"|"pyarrow_fallback"|"auto" (W9)
    max_query_memory_mb: int = 2048
    max_concurrent_queries: int = 4
    query_timeout_seconds: int = 60
    ducklake_enabled: bool = True
    ducklake_ttl_days: int = 7
    ducklake_max_join_rows: int = 100_000
```

---

## 十、部署扩展

> **注意**: HugeGraph 作为外部依赖独立部署。详见附录 B。

### Profile 矩阵

| Profile | 服务 | 用途 |
|---------|------|------|
| core | api, minio, hugegraph | 最小生产 |
| dev | core + ray, jupyter | 开发 |
| gpu | dev + GPU 资源 | GPU 加速 |
| monitoring | core + prometheus, grafana | 可观测 |

---

## 十一、迁移路径 (5 个 Milestone)

### M0: 架构技术债 (~1.5 周)

**目标**: 消除所有 CRITICAL/HIGH 阻断项，为后续里程碑奠定基础。

| 任务 | 验收标准 (AC) | 来源 |
|------|-------------|------|
| DuckDB 查询资源治理 | 10 并发 OLAP 查询不 OOM，P95 < 2s | 0B |
| DuckDB 扩展加载 | `INSTALL lance; LOAD lance; INSTALL ducklake; LOAD ducklake` 成功 | 0C, A3 |
| Docker pre-bundle 扩展 | 无网络环境启动成功 | A3 |
| `LanceScanAdapter` 实现 | `NativeLanceScanAdapter` + `PyArrowFallbackAdapter` + 自动切换 | A1, W9 |
| `storage_options` Schema | `StorageConfig.to_storage_options()` + `to_duckdb_s3_config()` 完成 | A4 |
| `__init__.py` Mixin 拆分 | 分解为 3+ Mixin，每个 < 300 行，所有现有测试通过 | A5 |
| `ATTACH TYPE lance` 列发现测试 | `information_schema` 列发现 + `metadata.py`/`faceted.py` 通过 | A2 |
| 查询层迁移 (olap/fts/hybrid/vector) | 从 PyArrow bridge 迁移到 `LanceScanAdapter`，全部测试通过 | 0C |
| DuckLake 衍生层集成 | ETL 物化 + DML + 跨存储 JOIN + 快照管理通过 | 0D |
| S3 `storage_options` 接通 | `LanceStorageManager._write_lance()` 传递 storage_options | 0E #2 |
| 跨格式 NULL JOIN 测试 | Lance NULL → DuckLake → JOIN 回 Lance NULL 保留 | A8 |
| Lake facade 分解 | IngestMixin + SearchMixin + AdminMixin，API 不变 | 0E #1 |

**M0 显式阻断标准**: 以上 12 项全部通过后才进入 M1。

### M1: 生产存储 (~2 周)

| 任务 | 验收标准 |
|------|---------|
| LanceStorageManager S3 集成 | `base_uri="s3://arrow-lake"` 全链路可用 |
| BlobStoreManager | upload/download/presigned_url/delete |
| MinIO 集成测试 | tests/integration/test_s3_storage.py 全通过 |
| 备份恢复 (Lance + MinIO) | create_backup + restore_backup 端到端 |

### M2: RAG Pipeline (~4 周, 含 NO-GO Trigger)

**NO-GO Trigger (第 1 周结束)**:
- [ ] LLM API 延迟 P95 < 5s (目标链路: 检索 < 500ms + LLM < 3s + 组装 < 500ms)
- [ ] 至少 1 个 LLM provider (OpenAI/vLLM) 端到端可用
- [ ] SSE 流式响应正常工作
- [ ] 如任一条件不满足 → 缩减 scope 为 "检索增强提示" (RAG without G)

**如 NO-GO 通过**:

| 任务 | 验收标准 |
|------|---------|
| `arrow_lake/rag/provider.py` | 2+ LLM provider 通过集成测试 |
| `arrow_lake/rag/pipeline.py` | 同步 + SSE 流式 RAG 查询 |
| `arrow_lake/rag/context.py` | Token 预算管理 + 去重 + 引用追踪 |
| `/api/v2/rag/` 端点 | POST /rag/query 返回带引用的回答 |

### M3: 知识图谱 + GraphRAG (~4 周, 含 NO-GO Trigger)

**NO-GO Trigger (第 1 周结束)**:
- [ ] HugeGraph 单机部署稳定 (7 天无重启)
- [ ] 实体抽取准确率 > 70% (50 样本人工评估)
- [ ] 图遍历延迟 P95 < 1s (2 跳 BFS)
- [ ] 如任一条件不满足 → 推迟到 v1.1

**如 NO-GO 通过**:

| 任务 | 验收标准 |
|------|---------|
| `arrow_lake/knowledge_graph/` | 图 Schema + 构建 + 查询 |
| GraphRAG Pipeline | 多跳推理问题回答正确 |
| Docker 集成 HugeGraph | docker compose up 全链路 |

### M4: 生产就绪 (~4 周)

| 任务 | 验收标准 |
|------|---------|
| CI/CD | GitHub Actions + MinIO service + 安全扫描 + Docker |
| OpenTelemetry | traces + metrics + /health/live + /health/ready |
| RBAC | JWT + API Key 双模式 + 数据集级权限 |
| 备份恢复 | LanceDB + MinIO + HugeGraph |
| 性能基线 | OLAP 基线文档化 + 回滚 trigger 定义 |

**向后兼容**: `/api/v1/*` 签名不变, `Lake` facade 方法不变, 本地存储默认行为不变。

---

## 十二、风险与缓解

| 风险 | 影响 | 概率 | 缓解措施 | 回滚 Trigger |
|------|------|------|---------|-------------|
| DuckDB Lance 扩展版本耦合 | 高 | 中 | 版本锁定 1.5.2 + PyArrow fallback | `lance_scan_mode = "pyarrow_fallback"` |
| `__lance_scan()` 被移除 | 高 | 低 | LanceScanAdapter 抽象层 | 自动切换 PyArrowFallbackAdapter |
| Lance 查询性能墙 | 高 | 中 | 内存限制 + 并发控制 + MotherDuck 路径 | 并发用户 > Y 时超时率 > 5% |
| 跨存储 JOIN OOM | 中 | 中 | 行数预算 + TTL + 降级路径 | DuckLakeQuotaExceededError |
| Lance/Parquet NULL 语义 | 中 | 低 | 显式 COALESCE + 测试覆盖 | 数据验证测试失败 |
| LLM API 延迟 | 中 | 高 | SSE 流式 + 本地 vLLM + 缓存 | P95 > 10s |
| HugeGraph 内存 | 中 | 中 | 限制遍历深度 + 分批构建 | 遍历 > 5 跳时拒绝 |
| 实体抽取幻觉 | 中 | 高 | 置信度阈值 + 人工审核模式 | 准确率 < 50% |
| RBAC 破坏现有用户 | 高 | 低 | 双模式认证 + API Key 映射 admin | 端到端回归测试 |
| Lance 单格式供应商风险 | 中 | 低 | Parquet export 兼容 + 风险注册 | Lance 项目停止维护 |

---

## 十三、成本模型 (Winston 缺失项)

### 运行成本估算

| 组件 | 规格 | 月成本 (估算) | 说明 |
|------|------|-------------|------|
| Arrow Lake API | 2 vCPU, 4GB RAM | $20-40 | 含 CI/CD runner |
| MinIO | 100GB SSD | $10-20 | S3 兼容存储 |
| DuckDB | 嵌入式, 无额外成本 | $0 | 共享 API 进程资源 |
| Ray Cluster | 4 vCPU, 16GB RAM | $40-80 | 可选, GPU 额外 |
| HugeGraph | 4 vCPU, 8GB RAM | $30-50 | 外部独立部署 |
| LLM API | 按调用量 | $10-200 | 取决于查询量 |
| Prometheus + Grafana | 1 vCPU, 2GB RAM | $10-15 | 可观测性 |
| **合计 (最小生产)** | | **$110-405/月** | 不含 GPU + LLM |

### 缩放因子

| 指标 | 阈值 | 扩展动作 |
|------|------|---------|
| 并发查询 | > 10 QPS | MotherDuck 迁移 (v1.1) |
| 数据量 | > 10M 行 | DuckDB 分布式或 MotherDuck (v1.1) |
| 向量索引 | > 1M 向量 | Ray GPU 集群 (已有) |
| KG 节点 | > 1M | HugeGraph 集群模式 |

---

## 十四、OMTM — 唯一关键指标 (John)

> **"数据工程师从零到可工作的 hybrid search API 能否在 1 小时内完成？"**

| 步骤 | 操作 | 预期时间 |
|------|------|---------|
| 1 | `git clone` + `docker compose up` | 5 min |
| 2 | 等待服务启动 (API + MinIO) | 5 min |
| 3 | `Lake.ingest()` 真正 ingest（不是 NotImplementedError） | 10 min |
| 4 | `Lake.search()` 返回真实结果 | 5 min |
| 5 | REST API 暴露这些操作 | 已在运行 |
| 6 | 文档与实际匹配 | 10 min |
| **合计** | | **< 35 min** |

**M0 完成后即开始跟踪此指标。M4 必须达标。**

---

## 附录 A: v0.2.0 深度 Gap 分析

### A.1 架构文档遗漏项

| # | 遗漏 | 补充 |
|---|------|------|
| 1 | S3Connector 未接入 Ingestor | Ingestor 需支持 `s3://` URI |
| 2 | `scan_dataset` 也需要 storage_options | 所有 `lance.dataset()` 调用 |
| 3 | CLIPImageEncoder 用于图像嵌入 | 多模态 RAG 复用 |
| 4 | QualityFilterRegistry 存在但未自动调用 | Ingestor 中注入自动过滤 |
| 5 | 现有 50+ ErrorCode 覆盖 | 新增错误码保持命名一致 |
| 6 | API 已有 Request Size Limit + CORS + GZip | RBAC 中间件插入位置 |
| 7 | Metrics 端点已存在 | OTel 增加 tracing |

### A.2 设计模式复用

| 现有模式 | v1.0 复用场景 |
|---------|-------------|
| Protocol 协议 | `BaseLLMProvider`, `LanceScanAdapter` |
| Registry 模式 | `PromptTemplateRegistry` |
| Bridge 模式 | `RAGQueryBridge`, `GraphQueryBridge` |
| Config 4 层覆盖 | `LLMConfig`, `HugeGraphConfig`, `SecurityConfig` |
| Mixin 模式 | `_LakeRAGMixin`, `_LakeKGMixin` |
| Factory 方法 | `create_llm_provider()`, `create_lance_scan_adapter()` |

---

## 附录 B: 部署架构

### B.1 本地服务依赖

| 服务 | 部署方式 | 端口 |
|------|---------|------|
| Arrow Lake API | `deploy/Dockerfile` | 8000 |
| MinIO | `deploy/docker-compose.yml` | 9000/9001 |
| Ray Cluster | `deploy/docker-compose.gpu.yml` | 6379 |
| HugeGraph | 本地部署 (已存在) | 8080 |
| LLM (可选) | 本地 vLLM/Ollama | 11434 |
| Prometheus | `deploy/docker-compose.monitoring.yml` | 9090 |
| Grafana | `deploy/docker-compose.monitoring.yml` | 3000 |

### B.2 服务健康检查依赖链

```
GET /health/ready 检查顺序:
  1. LanceDB 存储连接 (本地/S3)
  2. MinIO S3 可达性
  3. DuckDB lance + ducklake 扩展加载
  4. HugeGraph REST API (localhost:8080)
  5. Ray Cluster (如启用)
  6. LLM Provider (如配置)
```

---

## 附录 C: 相关文档

- [v0.2.0 阶段评审报告](phase-review-v0.2.0.md)
- [ADR-05: DuckDB OLAP Deviation](adr-05-duckdb-olap-deviation.md)
- [ADR-06: DuckDB OLAP + DuckLake 评估](adr-06-duckdb-olap-and-ducklake-evaluation.md)
- [v1.0 架构评审纪要](../_bmad-output/implementation-artifacts/reviews/v1.0-architecture-review-2026-04-20.md)

---

## 附录 D: 评审决策记录

| ID | 决策 | 立场 | 理由 |
|----|------|------|------|
| D-01 | DuckLake 保留在 v1.0 scope | **用户决策** | 数据格式统一的关键决策。Lance (只读 SSOT) + DuckLake (可写衍生) 在 DuckDB SQL 下互补，不是竞争。 |
| D-02 | `__lance_scan()` 通过 LanceScanAdapter 抽象 | 全票同意 | 内部 API，次版本可能移除。禁止任何模块直接调用。 |
| D-03 | M0 包含 DuckDB 扩展回滚路径 | 全票同意 | Feature flag: `lance_scan_mode` 支持 native/pyarrow_fallback/auto。 |
| D-04 | M0 包含 `__lance_scan()` 可量化 AC | 全票同意 | 不只"加载成功"，需性能基线 (P95 延迟, 10 并发不 OOM)。 |
| D-05 | 扩展加载 startup fast-fail + Docker pre-bundle | 全票同意 | GPU 容器无网络时必须 pre-bundle。 |
| D-06 | `storage_options` 从 `StorageConfig` 派生 | 全票同意 | 全局统一 schema，4 层覆盖 (YAML > env > .env > defaults)。 |
| D-07 | SDK facade 优先于 REST API | John+Mary+Winston | 确保 `Lake.ingest()` / `Lake.search()` 真正工作后再加端点。 |
| D-08 | M2/M3 各含 NO-GO trigger | John+Mary | 1 周 spike + go/no-go 决策门，避免沉没成本。 |
| D-09 | Faceted search 推迟到 v1.1 | John 建议 | 低需求信号，释放工程容量。M0 仅保留基础 OLAP。 |
| D-10 | RBAC 分配到 M4 | 妥协 | Mary 要求 M3 前置，John 认为单团队可推迟。M4 实施，M2/M3 预留接口。 |
| D-11 | LanceDB 保留为 DuckDB 平级组件 | **用户决策** | LanceDB SDK (数据管理层: 写入/索引/Schema/版本) + DuckDB (查询分析层: OLAP/向量/FTS/混合) 互补非替代。DuckDB lance 扩展只能用索引不能建索引。 |
