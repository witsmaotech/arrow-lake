# Arrow Lake v1.0 产品架构设计文档

**版本**: v1.0-draft | **日期**: 2026-04-20
**基于**: v0.2.0 五方评审共识 + 深度代码审查
**状态**: 设计阶段，待用户审批

---

## Context

v0.2.0 阶段评审揭示：代码质量 8/10 但生产就绪度仅 ~5/10。核心差距不在功能而在生产运维"最后一公里"。用户决定在推进生产基线的同时，增加两个核心业务能力：

1. **多模态 RAG + 知识图谱 (HugeGraph)** — 从"数据平台"升级为"智能数据平台"
2. **MinIO 真实集成** — 当前所有数据仅存储在本地文件系统

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

### 0C. DuckDB Lance 原生扩展 + DuckLake 分层架构 (2026-04-20 新增)

**重大发现**: DuckDB 1.5.2 内置 Lance 扩展 + DuckLake 扩展，可直接在 Lance 文件上执行 SQL，并支持 DuckLake 可写衍生层。详见 `docs/adr-06-duckdb-olap-and-ducklake-evaluation.md`。

**核心变更**：
- DuckDB 同时加载 `lance` 和 `ducklake` 扩展，成为统一 SQL 引擎
- `__lance_scan()` 替代 PyArrow 中间层，直接读取 Lance 文件
- `lance_vector_search()` / `lance_fts()` / `lance_hybrid_search()` 原生 SQL 函数
- DuckLake 作为可写衍生层：ETL 物化、工作区暂存、DML 支持

**分层架构**：
```
DuckDB (统一 SQL 引擎)
├── lance 扩展 → Lance (只读 SSOT: 原始数据、向量、FTS)
├── ducklake 扩展 → DuckLake (可写衍生: ETL、物化、工作区)
└── 原生 SQL → JOIN 跨存储查询
```

### 0D. 其他架构问题

| # | 问题 | 严重度 | 位置 |
|---|------|--------|------|
| 1 | Lake God Class (950+ 行, 30+ 方法) | HIGH | `__init__.py` |
| 2 | S3/MinIO storage_options 未传递 | HIGH | `storage.py` |
| 3 | Ingestor 线程不安全 | MEDIUM | `ingestor.py` |
| 4 | Schema 演化能力有限 | MEDIUM | `schema.py` |
| 5 | Ray 单 Named Actor Catalog | MEDIUM | `catalog/actor.py` |

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
         |  (Lake facade)  |       |  (新增模块)    |
         +--------+--------+       +-------+-------+
                  |
         +--------v----------------------------------------------------+
         |               Lance 数据格式层                               |
         |  (列式存储 + 向量索引 IVF-PQ + 全文索引 Tantivy + 版本管理)    |
         +----+----------------------------+---------------------------+
              |                            |
    +---------v---------+        +---------v-----------+
    |  DuckDB SQL 引擎  |        |  DuckLake 衍生层   |
    |  lance 扩展加载    |        |  ducklake 扩展加载 |
    |  ───────────────  |        |  ─────────────────  |
    |  __lance_scan     |        |  ETL 物化/暂存      |
    |  lance_vector     |        |  DML (可读写)       |
    |  lance_fts        |        |  快照/时间旅行       |
    |  lance_hybrid     |        |  Parquet 格式        |
    +---------+---------+        +---------+-----------+
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
| **格式层** | **Lance** | 列式存储 + 向量索引 + 全文索引 + 版本管理 |
| **计算层** | DuckDB + Lance 扩展 | OLAP SQL / 向量搜索 / FTS / 混合搜索 |
| **衍生层** | DuckDB + DuckLake 扩展 | ETL 物化 / 可写工作区 / DML |
| **存储层** | MinIO (S3) / 本地 FS | Lance 格式的持久化后端 |
| **外部层** | HugeGraph / LLM / Ray / OTel | 知识图谱 / 生成 / 计算 / 可观测 |

### 组件职责矩阵

| 组件 | 职责 | 格式 | v0.2 状态 | v1.0 变更 |
|------|------|------|----------|----------|
| **Lance** | 统一数据格式 (列式+向量+FTS+版本) | Lance 列式 | 已有 (via LanceDB) | 独立为格式层，不再与 LanceDB 绑定 |
| **DuckDB** | 统一 SQL 引擎 (OLAP+向量+FTS+混合) | 内存计算 | OLAP+Catalog (ADR-06) | lance+ducklake 扩展,查询治理 |
| **DuckLake** | 可写衍生层 (ETL/物化/工作区) | Parquet | 不存在 | 新增: DuckDB扩展加载 |
| **MinIO** | Lance 格式存储后端 (S3) | S3 对象 | 配置就绪,未连接 | storage_options 接通,成为 Lance 生产存储 |
| **HugeGraph** | 知识图谱 (外部部署) | 图数据库 | 不存在 | 新增: 图Schema,实体抽取,GraphRAG |
| **RAG Engine** | 检索增强生成 | — | 仅检索(R),无生成(G) | 新增: LLM抽象,Prompt模板,上下文管理 |
| **FastAPI REST** | HTTP接口 | — | 36端点,API Key auth | 新增: RAG/KG端点,RBAC,版本控制 |
| **Ray Cluster** | 分布式计算 | — | 已有 | 新增: KG构建作为Ray任务 |
| **Prometheus+OTel** | 可观测性 | — | 基础metrics | 新增: traces,完整healthcheck,告警规则 |

---

## 二、数据流总体关系

```
[原始数据] --ingest--> [Lance 格式层 (元数据+向量)]
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
                          [DuckDB 统一查询层]
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
                      +--------------+--------------+
                      |                             |
              [RAG Context Assembly]        [Graph Traversal]
                      |                   [HugeGraph]
                      |                             |
                      +--------------+--------------+
                                     |
                              [LLM Generation]
                                     |
                              [Cited Response]
```

---

## 三、计算框架层：Ray / Daft / Metaflow

> 本章说明架构图中"外部依赖"区域内三个计算框架的职责和数据流。

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
     │ GPUAutoscaler  │  │ sort/join/     │  │ Argo Bridge      │
     │ Ray Serve      │  │   groupby      │  │ AuditTrail       │
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

### 3.0A. Ray — 分布式计算引擎

**职责**: 任务并行化、服务编排、GPU 调度、Catalog 分布式管理。

| 组件 | 位置 | 职责 |
|------|------|------|
| `CatalogActor` | `catalog/actor.py` | Ray Named Actor，分布式表元数据管理（内嵌 DuckDB） |
| `CatalogReadReplica` | `catalog/replica.py` | CatalogActor 的高可用读副本 |
| `foreach()` | `ray_runtime/distributed.py` | 并行 map：Arrow Table → N 分区 → @ray.remote 处理 → 合并 |
| `RemoteDataLoader` | `ray_runtime/data_loader.py` | CPU→GPU 零拷贝数据管道（预取队列 + PyTorch DataLoader） |
| `GPUAutoscaler` | `ray_runtime/autoscaler.py` | GPU 0→N 弹性伸缩（空闲超时缩容） |
| `RayServeEmbeddingEncoder` | `embed/ray_serve_encoder.py` | Ray Serve 部署的分布式 Embedding 推理 |
| Cluster 管理 | `ray_runtime/cluster.py` | `initialize_ray()`, `detect_gpu()`, `get_cluster_info()` |

**数据流**:

```
Ingest 流程:
  CSV/JSON/Parquet → Daft.read_*() → Arrow Table → Ray.foreach(partitions) → Lance

Catalog 流程:
  Lake.catalog.register_table() → ray.get(CatalogActor.register_table.remote()) → DuckDB (embedded)

Embedding 流程:
  Text Column → Ray Serve Deployment → Embedding Vector → Lance (vector column)

分布式处理:
  Arrow Table → foreach(table, fn, num_partitions=4) → N × @ray.remote(_process_partition) → 合并
```

**v1.0 变更**: 无重大变更。CatalogActor 的单节点 Named Actor 问题（0C #5）保持现状，未来可考虑 DuckDB MotherDuck 替代。

### 3.0B. Daft — 惰性 DataFrame 引擎

**职责**: 提供非 SQL 的表达式式 DataFrame API，作为 DuckDB SQL 的补充。

| 组件 | 位置 | 职责 |
|------|------|------|
| `LazyDaftFrame` | `query/daft_api.py` | 惰性 DataFrame 封装（select/filter/sort/join/groupby/collect） |
| `daft.read_lance()` | `query/daft_api.py` | 直接读取 Lance 数据集为 Daft DataFrame |
| `daft.read_*()` | `ingest/ingestor.py` | Ingest 阶段读取 CSV/JSON/Parquet 文件 |

**与 DuckDB 的分工**:

| 维度 | DuckDB (SQL) | Daft (DataFrame API) |
|------|-------------|---------------------|
| 查询方式 | SQL 字符串 | Python 方法链 |
| 评估策略 | 即时执行 | 惰性求值（collect() 触发） |
| 强项 | 复杂聚合、JOIN、窗口函数 | ETL 管道、schema 演化、多模态 |
| 向量搜索 | lance_vector_search() SQL | 不支持 |
| 分布式 | 单进程 | 可运行在 Ray 集群上 |
| 适用场景 | OLAP 分析、BI | 数据预处理、ETL、编程式转换 |

**数据流**:

```
Query 流程 (DataFrame 路径):
  lake.daft_query("dataset") → LazyDaftFrame
    .select("col1", "col2")
    .filter("col1 > 5")
    .sort("col2", desc=True)
    .groupby("col1")
    .collect() → Arrow Table

Ingest 流程:
  daft.read_csv("file.csv") → Daft DataFrame → .to_arrow() → Lance
```

**v1.0 变更**: DuckDB Lance 扩展原生 SQL 后，Daft 的 OLAP 角色进一步收窄。Daft 继续承担 Ingest 文件读取和编程式 ETL，OLAP 分析统一走 DuckDB SQL。

### 3.0C. Metaflow — 工作流编排

**职责**: 将多步骤数据处理管道编排为可追踪、可重试、可调度的有向无环图 (DAG)。

| 组件 | 位置 | 职责 |
|------|------|------|
| `ArrowLakeFlowSpec` | `workflow/base.py` | 所有 Flow 的基类 Mixin（配置加载、自动标记） |
| `FlowRegistry` | `workflow/base.py` | Flow 注册/发现/列表 |
| `QualityPipelineFlow` | `flows/quality_pipeline_flow.py` | 数据质量过滤管道 |
| `MayaE2EFlow` | `flows/maya_e2e_flow.py` | 端到端演示管道（ingest→quality→embed→search） |
| `ScheduledQualityFlow` | `flows/scheduled_quality_flow.py` | 每日定时质量检查（@schedule） |
| `@retry` / `@schedule` | `workflow/retry.py`, `schedule.py` | 步骤重试 + 定时调度 |
| `AuditTrail` | `workflow/audit.py` | 工作流事件审计（HMAC 完整性） |
| `StateRollback` | `workflow/rollback.py` | 检查点级状态恢复 |
| `ArgoWorkflowBridge` | `workflow/argo.py` | Metaflow → Argo Workflows 转换（K8s 原生） |

**数据流**:

```
QualityPipelineFlow:
  start → load_config → apply_filters(lake.quality_filter()) → end

MayaE2EFlow:
  start → ingest(lake.ingest()) → quality_filter(lake.quality_filter())
        → embed(lake.embed()) → search(lake.vector_search()) → end

Metaflow + Ray 集成:
  Metaflow Step → Lake API → Ray.foreach() / CatalogActor → 分布式执行

Metaflow → Argo:
  Flow 定义 → ArgoWorkflowBridge → Kubernetes Argo Workflows → 生产调度
```

**v1.0 变更**:
- KG 构建新增为 Metaflow Flow (`KnowledgeGraphBuildFlow`)
- RAG 管道可封装为 Metaflow Flow 用于批量处理
- Argo Bridge 用于生产环境 K8s 调度

### 3.0D. 三框架协作关系

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
│  基础设施: Ray Cluster / Argo Workflows (K8s)            │
└──────────────────────────────────────────────────────────┘
```

**协作规则**:
1. **Metaflow 编排，不执行计算** — 每个 @step 调用 Lake API，Lake 内部选择 DuckDB/Daft/Ray
2. **Ray 提供分布式能力** — Metaflow 通过 `metaflow-ray` 插件在 Ray 集群上运行
3. **Daft 负责数据读取** — Ingest 阶段文件解析统一走 Daft（CSV/JSON/Parquet），编程式 ETL 也走 Daft
4. **DuckDB 负责数据查询** — OLAP/向量/FTS/混合搜索统一走 DuckDB Lance 扩展 SQL
5. **Ray 负责 Catalog** — CatalogActor 是 Ray Named Actor，内嵌 DuckDB 做元数据存储

---

## 四、模块设计

**定位**: MinIO 不是独立存储组件，而是 **Lance 格式的 S3 存储后端**。通过 `storage_options` 配置，Lance 数据可以写入 `s3://` URI。DuckDB Lance 扩展也可直接读取 S3 上的 Lance 文件。

**现状诊断**: `LanceStorageManager._write_lance()` 和 `_open_lance()` 调用 `lancedb.connect(self.base_uri)` 未传递 `storage_options`，S3 模式不可用。

**核心修改** — `arrow_lake/ingest/storage.py`:
- 构造函数接收 `StorageConfig`，构造 `storage_options` 字典
- `_write_lance()` / `_open_lance()` 传递 `storage_options` 给 `lancedb.connect()`

```python
@staticmethod
def _build_storage_options(config: StorageConfig | None) -> dict[str, str] | None:
    if config is None or config.backend == StorageBackend.LOCAL:
        return None
    return {
        "region": config.s3_region,
        "endpoint_url": config.s3_endpoint,
        "aws_access_key_id": config.s3_access_key,
        "aws_secret_access_key": config.s3_secret_key,
        "allow_anonymous": "false",
    }
```

**新增** — `arrow_lake/storage/blob_store.py`:

```python
class BlobStoreManager:
    """MinIO/S3 二进制对象管理器 -- 与 LanceStorageManager 互补"""

    def upload_media(self, dataset_name: str, file_id: str, data: bytes, content_type: str) -> str: ...
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
| 预览图 | MinIO + LanceDB引用 | preview_s3_uri 列 |
| 原始图片/视频/音频 | MinIO | original_s3_uri 列 |
| EXIF/元数据 | LanceDB | metadata 列 |

---

### 3B. 多模态 RAG Pipeline

**现状**: `examples/08_rag_pipeline.py` 展示了完整检索流程，但生成(G)部分仅用模拟 prompt。检索(R)已有 vector/FTS/hybrid/faceted/ensemble 五种。

**新增** — `arrow_lake/rag/`:

| 文件 | 职责 |
|------|------|
| `provider.py` | LLM 抽象层 (OpenAI/Anthropic/vLLM/Ollama 工厂) |
| `prompt.py` | Jinja2 Prompt 模板系统 (QA/总结/抽取/多模态) |
| `context.py` | 上下文窗口管理 (token 预算 + 去重 + 引用追踪) |
| `pipeline.py` | RAG 管线编排 (检索→组装→生成→引用) |
| `graph_rag.py` | GraphRAG 增强 (向量+图遍历三路 RRF 融合) |

#### 3B.1 LLM 提供商抽象 (`provider.py`)

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
    async def generate(self, messages: list[LLMMessage], *, temperature: float = 0.7, max_tokens: int = 2048) -> LLMResponse: ...
    @abstractmethod
    async def generate_stream(self, messages: list[LLMMessage], **kwargs) -> AsyncIterator[str]: ...

def create_llm_provider(config: LLMConfig) -> BaseLLMProvider: ...
```

#### 3B.2 RAG 管线编排 (`pipeline.py`)

```python
class RAGPipeline:
    def __init__(self, lake: Lake, llm_provider: BaseLLMProvider, *,
                 retrieval_strategy: str = "hybrid", prompt_template: PromptTemplate | None = None,
                 context_window: ContextWindow | None = None, enable_citations: bool = True): ...

    async def query(self, question: str, *, dataset_name: str, top_k: int = 10, filters: dict | None = None) -> RAGResponse: ...
    async def query_stream(self, question: str, *, dataset_name: str, top_k: int = 10) -> AsyncIterator[str]: ...

@dataclass(frozen=True)
class RAGResponse:
    answer: str
    citations: list[dict[str, Any]]
    context_chunks: list[ContextChunk]
    retrieval_strategy: str
    llm_model: str
    token_usage: dict[str, int]
```

#### 3B.3 GraphRAG 增强 (`graph_rag.py`)

```python
class GraphRAGPipeline(RAGPipeline):
    """图增强 RAG — 融合向量检索 + 知识图谱遍历"""

    async def query(self, question: str, *, dataset_name: str,
                    graph_traversal_depth: int = 2, graph_weight: float = 0.3) -> RAGResponse:
        """
        Pipeline:
        1. LLM 抽取问题中的实体
        2. HugeGraph 中查找实体节点
        3. 多跳遍历获取关联子图
        4. 与向量/FTS检索结果合并
        5. 上下文组装 (文本 + 图三元组)
        6. LLM 生成带引用的回答
        """
```

#### 3B.4 REST 端点 (`/api/v2/rag/`)

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/rag/query` | RAG 问答 (同步) |
| POST | `/rag/query/stream` | RAG 问答 (SSE 流式) |
| POST | `/rag/extract` | 从指定数据集抽取实体+关系 |
| GET | `/rag/templates` | 列出可用 Prompt 模板 |
| GET | `/rag/history/{session_id}` | 获取会话历史 |

**POST /rag/query 请求体**:
```json
{
  "question": "谁参与了XX事件?",
  "dataset_name": "my_docs",
  "retrieval_strategy": "hybrid",
  "use_graph": true,
  "graph_depth": 2,
  "top_k": 10,
  "include_citations": true
}
```

**响应**:
```json
{
  "answer": "根据文档[1], 张三和李四参与了XX事件...",
  "citations": [{"chunk_id": "c-001", "source": "report.pdf", "text": "张三于2024年...", "score": 0.92}],
  "graph_facts": [{"subject": "张三", "relation": "参与", "object": "XX事件", "confidence": 0.95}],
  "retrieval_strategy": "hybrid+graph",
  "token_usage": {"prompt_tokens": 2500, "completion_tokens": 350}
}
```

---

### 3C. HugeGraph 知识图谱

**新增** — `arrow_lake/knowledge_graph/`:

| 文件 | 职责 |
|------|------|
| `client.py` | HugeGraph REST 客户端封装 |
| `schema.py` | 图 Schema 定义 (document/chunk/entity/person/org...) |
| `extractor.py` | LLM 驱动实体+关系抽取 |
| `builder.py` | KG 构建管线 (从 LanceDB 数据集自动构建) |
| `retriever.py` | 图增强检索 (实体匹配→BFS→子图序列化) |
| `queries.py` | 预定义 Gremlin 查询模板 |

#### 3C.1 图 Schema 设计

**顶点标签**:
| 标签 | 主键 | 说明 |
|------|------|------|
| document | name | 文档实体 |
| chunk | chunk_id | 文档片段 |
| entity | name | 通用实体 |
| person | name | 人物 |
| organization | name | 组织 |
| location | name | 地点 |
| concept | name | 概念 |
| event | name | 事件 |

**边标签**:
| 边标签 | 源→目标 | 说明 |
|--------|--------|------|
| contains_chunk | document→chunk | 文档包含片段 |
| references | chunk→entity | 片段引用实体 |
| next_chunk | chunk→chunk | 片段顺序 |
| related_to | entity→entity | 泛化关系 |
| part_of | entity→entity | 部分-整体 |
| belongs_to | entity→organization | 归属 |
| located_in | entity→location | 位置 |
| participates_in | person→event | 参与事件 |
| depicts | chunk→entity | 图像描绘实体 |

#### 3C.2 KG 构建管线 (`builder.py`)

```python
class KnowledgeGraphBuilder:
    """知识图谱构建管线 -- 从 LanceDB 数据集自动构建 KG"""

    async def build_from_dataset(self, dataset_name: str, *, chunk_column: str = "text_content",
                                 entity_types: list[str] | None = None, batch_size: int = 50) -> KGBuildReport:
        """
        Pipeline:
        1. 从 LanceDB 读取数据集
        2. 按行分批
        3. LLM 抽取实体+关系 (可 Ray 并行)
        4. 去重+合并实体 (同名合并)
        5. 写入 HugeGraph (batch API)
        6. 创建索引
        7. 返回构建报告
        """
```

#### 3C.3 REST 端点 (`/api/v2/kg/`)

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/kg/build` | 触发 KG 构建 (异步) |
| GET | `/kg/build/{task_id}/status` | 查询构建状态 |
| GET | `/kg/schema` | 获取当前图 Schema |
| POST | `/kg/query` | Gremlin/Cypher 查询 |
| GET | `/kg/entities/{id}/neighbors` | 获取实体邻居 |
| GET | `/kg/stats` | 图统计信息 |
| DELETE | `/kg/graph` | 清空图数据 |

---

### 3D. 生产基础设施 (评审 P0)

#### CI/CD Pipeline

```yaml
# .github/workflows/ci.yml 增强
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
      - pytest tests/integration/  # MinIO 真实集成测试
      - bandit -r arrow_lake/     # 安全扫描

  build-and-push:
    needs: lint-and-test
    if: github.ref == 'refs/heads/master'
    steps:
      - docker build + push to registry
```

#### 可观测性

- `arrow_lake/core/tracing.py` — OpenTelemetry 集成
- `/health/live` — 进程存活探针 (K8s livenessProbe)
- `/health/ready` — 依赖就绪探针 (检查 LanceDB + MinIO + HugeGraph + Ray)

#### RBAC

```python
class Role(StrEnum):
    ADMIN = "admin"
    DATA_ENGINEER = "data_engineer"
    ANALYST = "analyst"
    VIEWER = "viewer"

# 双模式认证: JWT (优先) 或 API Key (向后兼容)
class AuthMiddleware(BaseHTTPMiddleware): ...
```

#### API 版本控制

- `/api/v1/*` — 现有 36 端点，完全保留
- `/api/v2/*` — 新增 RAG + KG 端点

#### 备份恢复

```python
class BackupManager:
    async def create_backup(self, *, include_lance: bool, include_minio: bool, include_hugegraph: bool) -> BackupReport: ...
    async def restore_backup(self, backup_id: str) -> RestoreReport: ...
```

---

## 五、数据流详细设计

### 4.1 增强摄取流程

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

### 4.2 RAG 查询流程

```
[用户问题] "谁参与了XX事件,结果如何?"
    |
    v
+--- RAGPipeline.query() ---+
|                            |
| 1. 实体识别                 |
|    └─ LLM: 抽取"XX事件"     |
|                            |
| 2. 并行检索                 |
|    ├─ 向量检索 (LanceDB)    |
|    ├─ FTS 检索 (LanceDB)    |
|    └─ 图遍历 (HugeGraph)    |
|       └─ 找到事件节点        |
|       └─ BFS -> 参与者        |
|       └─ 子图三元组           |
|                            |
| 3. RRF 融合                 |
|                            |
| 4. 上下文组装               |
|    ├─ 文本 + 图三元组         |
|    ├─ Token 预算管理         |
|    └─ 去重                    |
|                            |
| 5. Prompt 渲染               |
|                            |
| 6. LLM 生成                 |
|                            |
| 7. 引用标注 + 返回            |
+----------------------------+
```

---

## 六、文件变更清单

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
```

### 修改文件 (8 个)

| 文件 | 修改内容 |
|------|---------|
| `arrow_lake/ingest/storage.py` | storage_options 传递, config 注入 |
| `arrow_lake/ingest/media.py` | 上传原始媒体到 MinIO |
| `arrow_lake/config.py` | 新增 LLMConfig, HugeGraphConfig, SecurityConfig |
| `arrow_lake/exceptions.py` | 新增 RAG/KG/Security 错误码 |
| `arrow_lake/__init__.py` | Lake facade Mixin 拆分 + RAG/KG API |
| `arrow_lake/api/app.py` | v2 路由注册, JWT 中间件 |
| `arrow_lake/api/auth.py` | 双模式认证 |
| `arrow_lake/api/routers/system.py` | 增强健康检查 |

### Lake Facade 分解策略

```python
# 通过 Mixin 模式拆分，不改变现有方法签名
class Lake(_LakeIngestMixin, _LakeSearchMixin, _LakeRAGMixin, _LakeKGMixin):
    """API 完全不变 — 仅内部拆分"""
```

---

## 七、新增依赖

```toml
# [project.dependencies]
openai>=1.50, anthropic>=0.40, jinja2>=3.1,
opentelemetry-api>=1.28, opentelemetry-sdk>=1.28, opentelemetry-exporter-otlp>=1.28,
pyjwt>=2.9, passlib[bcrypt]>=1.7

# [project.optional-dependencies]
hugegraph = ["hugegraph-client>=1.5"]
ollama = ["ollama>=0.4"]
```

---

## 八、配置设计

### 新增配置段

```python
class LLMConfig(BaseModel):
    provider: str = "openai"         # "openai"|"anthropic"|"vllm"|"ollama"
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
```

### YAML 示例 (configs/prod.yaml)

```yaml
llm:
  provider: openai
  model: gpt-4o-mini
  temperature: 0.3

hugegraph:
  enabled: true
  host: hugegraph-server
  port: 8080
  graph_name: arrow_lake_kg

security:
  auth_mode: both
  jwt_expiration_hours: 8
```

---

## 九、部署扩展

> **注意**: HugeGraph 作为外部依赖独立部署，不由 Arrow Lake 管理。本地开发环境已部署 HugeGraph Server (:8080)。详见附录 B。

### Profile 矩阵

| Profile | 服务 | 用途 |
|---------|------|------|
| core | api, minio, hugegraph | 最小生产 |
| dev | core + ray, jupyter | 开发 |
| gpu | dev + GPU 资源 | GPU 加速 |
| monitoring | core + prometheus, grafana | 可观测 |

---

## 十、迁移路径 (5 个 Milestone)

### M0: 架构技术债 (~1 周)
- DuckDB 查询资源治理 (内存限制 + 并发控制 + 超时熔断)
- DuckDB 扩展加载：`INSTALL lance; LOAD lance; INSTALL ducklake; LOAD ducklake`
- 查询层重构：`olap.py` / `fts.py` / `hybrid.py` / `vector.py` 迁移到 DuckDB Lance 原生 SQL
- DuckLake 衍生层集成：ETL 物化、可写工作区、快照管理
- Lake facade Mixin 拆分 (IngestMixin, QueryMixin, AdminMixin)
- S3/MinIO `storage_options` 接通
- **验收**: DuckDB 10 并发查询不 OOM，Lance SQL OLAP 通过，DuckLake DML 正常

### M1: 生产存储 (~2 周)
- LanceStorageManager S3 集成 + BlobStoreManager
- MinIO 集成测试
- **验收**: `base_uri="s3://arrow-lake"` 全链路可用

### M2: RAG Pipeline (~4 周)
- `arrow_lake/rag/` 模块 + `/api/v2/rag/` 端点
- **验收**: 向知识库提问获得引用式回答

### M3: 知识图谱 + GraphRAG (~4 周)
- `arrow_lake/knowledge_graph/` 模块 + Docker 集成 HugeGraph
- **验收**: GraphRAG 回答多跳推理问题

### M4: 生产就绪 (~4 周)
- CI/CD + OTel + Health Check + RBAC + 备份
- **验收**: 五方评审 P0 全部通过

**向后兼容**: `/api/v1/*` 签名不变, `Lake` facade 方法不变, 本地存储默认行为不变。

---

## 十一、风险与缓解

| 风险 | 影响 | 概率 | 缓解措施 |
|------|------|------|---------|
| LanceDB S3 storage_options 高并发不稳定 | 高 | 中 | 连接池重试; 保留本地模式降级 |
| LLM API 延迟影响 RAG 响应 | 中 | 高 | SSE 流式; 本地 vLLM 降级; 缓存热门查询 |
| HugeGraph 内存占用过高 | 中 | 中 | 限制遍历深度; 分批构建; 独立部署可单独扩容 |
| RBAC 破坏现有 API Key 用户 | 高 | 低 | 双模式认证; API Key 映射为 admin 角色 |
| 实体抽取 LLM 幻觉 | 中 | 高 | 置信度阈值过滤; 人工审核模式 |

---

## 附录 A: v0.2.0 深度 Gap 分析补充

### A.1 架构文档遗漏项

通过逐文件审查 v0.2.0 代码，发现以下遗漏：

#### 1. S3Connector 未接入 Ingestor

`arrow_lake/ingest/connectors.py` 中 `S3Connector` 已实现完整的文件发现功能（boto3 + 分页），但 `Ingestor.ingest_mixed()` 不支持消费 `s3://` URI。当前仅处理本地路径和 HTTP URL。

**补充**: Ingestor 需增加 S3 URI 解析，支持 `S3Connector.list_files()` → Ingestor 管线的直通。

#### 2. scan_dataset 也需要 storage_options

`LanceStorageManager.scan_dataset()` (第 557 行) 使用 `lance.dataset()` 直接读取 Lance 文件，未经过 `lancedb.connect()`。当 `base_uri` 为 S3 路径时，同样需要传递 `storage_options`。

**补充**: 所有调用 `lance.dataset()` / `lance.LanceDataset` 的位置都需要传递 S3 凭证。

#### 3. CLIPImageEncoder 用于图像嵌入

`arrow_lake/embed/` 下已有 `CLIPImageEncoder`，可直接复用于多模态 RAG 中的图像理解。RAG pipeline 需在 `context.py` 中区分 `modality` 类型选择对应的嵌入策略。

#### 4. QualityFilterRegistry 存在但未自动调用

质量过滤框架完整（`TextLengthFilter`, `ImageResolutionFilter`），但在 Ingestor 管线中是手动调用。v1.0 需要在 Ingestor 中注入自动质量过滤。

#### 5. 现有 50+ ErrorCode 覆盖

`exceptions.py` 已有 Storage/Query/Ingestion/Catalog/RayRuntime/Validation 等 50+ 错误码。新增 RAG/KG/Security 错误码时需保持命名风格一致。

#### 6. API 已有 Request Size Limit + CORS + GZip

`arrow_lake/api/app.py` 的中间件链已包含：请求体大小限制、CORS 配置、GZip 压缩。RBAC 中间件需插入在此链之后。

#### 7. Metrics 端点已存在

`arrow_lake/core/metrics.py` 和 `/metrics` Prometheus 端点已实现。OpenTelemetry 需要在此基础上增加 tracing（分布式追踪），而非替代现有 metrics。

### A.2 设计模式复用指南

v1.0 新模块应遵循 v0.2.0 已验证的设计模式：

| 现有模式 | 示例 | v1.0 复用场景 |
|---------|------|-------------|
| Protocol 协议 | `QualityFilter` protocol | `BaseLLMProvider` 抽象 |
| Registry 模式 | `QualityFilterRegistry` | `PromptTemplateRegistry` |
| Bridge 模式 | `VectorSearchBridge`, `OlapSearchBridge` | `RAGQueryBridge`, `GraphQueryBridge` |
| Config 4 层覆盖 | 代码→.env→环境变量→YAML | `LLMConfig`, `HugeGraphConfig` |
| Mixin 模式 | Lake facade 拆分 | `_LakeRAGMixin`, `_LakeKGMixin` |
| Factory 方法 | `create_llm_provider()` | LLM Provider 选择 |

---

## 附录 B: 部署架构 (独立文档)

> HugeGraph 已在本地部署，不纳入 Arrow Lake 的 docker-compose 管理。部署扩展设计见独立文档 `docs/deploy-guide-v1.0.md`。

### B.1 本地服务依赖 (v1.0)

| 服务 | 部署方式 | 端口 | 用途 |
|------|---------|------|------|
| Arrow Lake API | `deploy/Dockerfile` | 8000 | 核心 API 服务 |
| MinIO | `deploy/docker-compose.yml` | 9000/9001 | 对象存储 |
| Ray Cluster | `deploy/docker-compose.gpu.yml` | 6379 | 分布式计算 |
| **HugeGraph** | **本地部署 (已存在)** | **8080** | **知识图谱** |
| **LLM (可选)** | **本地 vLLM/Ollama** | **11434/** | **本地推理** |
| **Prometheus** | `deploy/docker-compose.monitoring.yml` | 9090 | **指标采集** |
| **Grafana** | `deploy/docker-compose.monitoring.yml` | 3000 | **监控仪表盘** |

### B.2 Arrow Lake docker-compose 仅管理自身服务

Arrow Lake 的 docker-compose 仅管理：
- API Server
- MinIO (对象存储依赖)
- Ray Cluster (可选)
- Prometheus + Grafana (监控，可选)

HugeGraph、LLM 推理服务作为**外部依赖**，通过配置文件连接：

```yaml
# configs/prod.yaml
storage:
  backend: minio
  s3_endpoint: "http://localhost:9000"

hugegraph:
  enabled: true
  host: localhost        # 本地已部署的 HugeGraph
  port: 8080

llm:
  provider: vllm         # 本地 vLLM
  api_base: "http://localhost:11434/v1"
```

### B3 服务连接拓扑

```
┌─────────────────────────────────────────────┐
│              用户本地环境                      │
│                                             │
│  ┌──────────┐  ┌──────────┐  ┌──────────────┐ │
│  │HugeGraph │  │  vLLM    │  │  Arrow Lake  │ │
│  │  :8080   │  │  :11434  │  │  :8000      │ │
│  └─────┬────┘  └────┬────┘  └──────┬──────┘ │
│        │             │              │         │
│  ┌─────v────┐  ┌───v───────┐  ┌────v──────┐ │
│  │  MinIO    │  │          │  │  Ray     │ │
│  │  :9000   │  │          │  │          │ │
│  └──────────┘  └──────────┘  └──────────┘ │
└─────────────────────────────────────────────┘
```

### B4 服务健康检查依赖链

```
GET /health/ready 检查顺序:
  1. LanceDB 存储连接 (本地/S3)
  2. MinIO S3 可达性
  3. HugeGraph REST API (localhost:8080)
  4. Ray Cluster (如启用)
  5. LLM Provider (如配置)
```

---

## 附录 C: 相关文档

- [v0.2.0 阶段评审报告](phase-review-v0.2.0.md)
- [ADR-05: DuckDB OLAP Deviation](adr-05-duckdb-olap-deviation.md)
- [HugeGraph Skill 文档索引](../dev_notes/hugegraph_build_skills/INDEX.md)
