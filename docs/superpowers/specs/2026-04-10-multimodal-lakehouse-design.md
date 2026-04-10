# DIntelliHub — 多模态数据湖仓平台设计规范

> **状态**: Approved (Brainstorming v2)
> **日期**: 2026-04-10
> **决策记录**: `dev_notes/briefprojs/multimodal_lakehouse_brief.md`

---

## 1. 目标与范围

### 1.1 项目目标

构建一个**通用多模态数据湖仓基础设施**，提供从摄入到检索的端到端数据处理管线。不做特定应用绑定，后续按需扩展。

### 1.2 支持的模态

文本/文档、图片、视频、结构化数据（全模态覆盖）。

### 1.3 MVP 范围

端到端完整管线：**摄入 → 处理 → 存储 → 检索 → 编排 → 目录**。

### 1.4 部署优先级

本地/开发环境优先（Docker Compose），快速迭代。生产环境可演进至 K8s + KubeRay。

---

## 2. 技术选型与决策

| # | 决策项 | 选择 | 理由 |
|---|--------|------|------|
| 1 | 架构风格 | 分层松耦合 | 各层独立演进，与 DREAM 栈一致 |
| 2 | 层间契约 | 事件驱动 + Lance URI | 吸取微服务优点，兼顾性能与容错 |
| 3 | 数据处理 | Daft（薄封装 + 原生优先） | ~80% Ingestion、~65% Processing Daft 原生覆盖 |
| 4 | 分布式底座 | Ray 全栈（Data/Serve/Actor） | Checkpoint、AutoScale、Remote Data Loader |
| 5 | 存储 | Lance Format | 零成本加列、多模态统一、版本管理、向量索引 |
| 6 | OLAP 查询 | DuckDB | Arrow 零拷贝桥接 Lance、向量化执行、lakehouse 扩展生态 |
| 7 | 元数据管理 | DuckDB + Ray Actor | SQL 化元数据管理、有状态分布式访问 |
| 8 | 向量检索 | Lance 向量索引（IVF_PQ/HNSW） | 原生 ANN，无需额外向量数据库 |
| 9 | 工作流编排 | Metaflow + @raystep | 用户友好 + 一键扩展至 Ray 集群 |
| 10 | 生产调度 | Argo Workflows | K8s 原生工作流引擎，Metaflow 后端 |
| 11 | 数据质量 | NVIDIA NeMo Curator | GPU 加速管线，RayActorPoolExecutor 集成 |
| 12 | 依赖管理 | Poetry | Python 生态兼容，与 DREAM 栈一致 |
| 13 | 云端 Catalog | MotherDuck（可选） | 团队共享，与本地 DuckDB 无缝同步 |

---

## 3. 系统架构

### 3.1 分层架构

```
┌─────────────────────────────────────────────────────────────┐
│                    Orchestration Layer                       │
│         Metaflow (@poetry + @raystep + @schedule)            │
│         Argo Workflows (生产部署)                             │
├──────────┬──────────┬──────────┬──────────┬─────────────────┤
│ Ingestion │ Process  │ Storage  │ Retrieval │   Catalog       │
│   Layer   │  Layer   │  Layer   │  Layer   │   Layer         │
│           │          │          │          │                 │
│ · File    │ · Daft   │ · Lance  │ · DuckDB │ · DuckDB        │
│ · DB      │   on Ray │   Format │   OLAP   │   元数据管理     │
│ · API     │ · NeMo   │ · Version│ · Lance  │ · Ray Actor     │
│ · Stream  │   Curator│ · Mgmt   │   Vector │   有状态服务     │
│           │ · GPU/CPU│ · Dist   │   Index  │ · Lance         │
│           │   异构   │   Write  │ · Hybrid │   Namespace     │
│           │          │          │   Query  │ · MotherDuck    │
│           │          │          │          │   (生产可选)     │
├──────────┴──────────┴──────────┴──────────┴─────────────────┤
│                    Ray Distributed Layer                     │
│  Ray Data (Checkpoint/AutoScale) · Ray Serve (检索服务)      │
│  Ray Actor (Catalog) · Object Store · Fault Tolerance        │
├─────────────────────────────────────────────────────────────┤
│                    Infrastructure Layer                       │
│    Local FS / S3-compat · Docker Compose · Ray Cluster       │
├─────────────────────────────────────────────────────────────┤
│                 Cross-cutting Concerns                       │
│    Config (Pydantic) · Structured Logging · Prometheus       │
│    Error Handling (4层) · Security (env/secret)              │
│    DuckDB Extensions (httpfs/iceberg/delta)                  │
└─────────────────────────────────────────────────────────────┘
```

### 3.2 Ray 分布式能力在各层的角色

| Ray 组件 | 所在层 | 职责 |
|----------|--------|------|
| **Ray Data** | Processing | Checkpoint（lineage 重建）、AutoScale（动态并行度）、Streaming（流式传递） |
| **Ray Serve** | Retrieval | 检索服务弹性伸缩、批量推理、分卡服务（`num_gpus=0.25`） |
| **Ray Actor** | Catalog | 有状态 DuckDB Catalog 服务，`max_restarts=2` 容错 |
| **Ray Object Store** | 跨层 | CPU/GPU Worker 间零拷贝数据传递 |
| **Ray Core** | 全局 | Task/Actor 调度、资源管理、GCS 容错 |

### 3.3 集群部署模式

| 模式 | 技术 | 适用 |
|------|------|------|
| 本地开发 | Docker Compose + Ray head + 2 workers | 单机调试 |
| 批处理任务 | KubeRay `RayJob`（`shutdownAfterJobFinishes: true`） | 成本优化，完成后自动删集群 |
| 在线服务 | KubeRay `RayService`（零停机升级） | 检索服务常驻 |
| 常驻集群 | KubeRay `RayCluster` | 持续运行的 Ray 环境 |

---

## 4. 数据流与层间契约

### 4.1 事件驱动 + Lance URI

每个管线 Stage 输出一个 Lance Dataset，下一 Stage 通过 Lance URI 读取。Event Bus 异步通知阶段完成状态。

```
Ingestion → lance://raw/{run_id}/{source}/
    ↓ Event Bus: stage.completed
Processing → lance://processed/{run_id}/{source}/
    ↓ Event Bus: stage.completed
Index → lance://processed/{run_id}/{source}/ v(N+1) (含索引)
    ↓ Event Bus: stage.completed
Catalog → DuckDB datasets 表 + 血缘记录
```

### 4.2 StageEvent 模型

```python
@dataclass
class StageEvent:
    event_id: str
    event_type: Literal["stage.completed", "stage.failed", "stage.progress"]
    pipeline_id: str
    run_id: str
    stage_name: str
    lance_uri: str           # 输出的 Lance Dataset URI
    metadata: dict           # 行数、模态类型、耗时等
    error: Optional[str]
    timestamp: datetime
```

### 4.3 容错机制（四层）

| 层级 | 机制 | 适用场景 |
|------|------|---------|
| Metaflow | `@retry(times=3)` | 瞬态故障自动重试 |
| Metaflow | `@catch(var="error")` | 非关键 Stage 失败不阻塞 |
| Ray | Actor `max_restarts` + lineage reconstruction | Worker 故障恢复 |
| Lance | 版本管理 + Checkpoint | 数据不丢失，可从任意 version 恢复 |

### 4.4 微服务优点映射

| 微服务优点 | 实现方式 |
|-----------|---------|
| 独立部署 | 每个 Stage 是独立的 Metaflow `@step` + Ray task/actor |
| 服务边界 | Lance URI 是唯一的跨 Stage 契约 |
| 异步通信 | Event Bus（asyncio.Queue / Ray Queue / Redis） |
| 容错恢复 | Lance Checkpoint + Ray Data Checkpoint + Event Replay |
| 独立扩展 | `@raystep` + Ray Data AutoScale 按阶段粒度弹性扩展 |
| 可观测性 | StageEvent + Ray Dashboard + DuckDB pipeline_runs 表 |
| API 契约 | Lance Schema（数据契约）+ DuckDB Catalog（元数据契约） |

---

## 5. 各层详细设计

### 5.1 Ingestion Layer

**设计原则**：薄封装 + Daft 原生优先。Daft 覆盖 ~80% 能力，仅 2 个外部集成点。

| 能力 | 实现 | 依赖 |
|------|------|------|
| CSV/JSON/Parquet/图片/视频/SQL/S3/二进制 | Daft 原生 | 无额外依赖 |
| Lance 写入 | `daft.write_lance()` | 无 |
| PDF 文本提取 | 自定义连接器 | kreuzberg / PyMuPDF |
| REST API 拉取 | 自定义连接器 | httpx |

**统一连接器接口**：

```python
def read_source(config: SourceConfig) -> daft.DataFrame:
    """根据配置选择 Daft 原生读取器或自定义连接器"""
    match config.source_type:
        case "file":    return daft.read_parquet(config.path)
        case "image":   return daft.read_images(config.path)
        case "video":   return daft.read_video(config.path)
        case "sql":     return daft.read_sql(config.query, uri=config.uri)
        case "s3":      return daft.read_parquet(config.path)  # s3:// 支持
        case "pdf":     return pdf_connector.read(config.path)   # 自定义
        case "api":     return api_connector.fetch(config.url)   # 自定义
```

### 5.2 Processing Layer

**设计原则**：Daft 原生处理 + NeMo Curator GPU 加速。三层算子体系。

| 算子类型 | 覆盖范围 | 示例 |
|----------|---------|------|
| Daft 原生（~65%） | filter, map, SQL, embed, classify, prompt | `df.filter()`, `df.embed()` |
| NeMo Curator（GPU 加速） | 去重、质量分类、图片评分 | `QualityClassifier`, `TextDuplicatesRemovalWorkflow` |
| 自定义 UDF | PDF 解析、视频关键帧 | opencv, decord |

**NeMo Curator Executor 选择**：

| Executor | 用途 | 自动选择条件 |
|----------|------|-------------|
| XennaExecutor | 生产默认（图片/视频） | 默认 |
| RayActorPoolExecutor | 去重工作流 | `TextDuplicatesRemovalWorkflow` 自动启用 |
| RayDataExecutor | 实验性 | 手动指定 |

**GPU 资源精细控制**：`Resources(gpus=0.5)` 半卡运行，`Resources(gpus=1.0)` 整卡运行。

**GPU/CPU 算力分离**（Remote Data Loader 模式）：
CPU Workers（读取/解码/预处理）→ Ray Object Store（零拷贝）→ GPU Workers（NeMo Curator/embedding）。火山引擎实践证明 GPU 利用率从 60% 提升至 96%。

### 5.3 Storage Layer (Lance)

**核心特性在管线中的应用**：

| 特性 | 管线应用 |
|------|---------|
| 零成本加列 | 每阶段追加新列（quality_score, embedding），不重写历史 Fragment |
| 版本管理 | 每次写入原子产生新 version，关键版本打 Tag |
| 多模态统一 | 标量 + 向量 + 二进制同表管理 |
| 分布式写入 | Ray Workers 并行写 Fragment → 单次 Commit（两阶段提交） |
| Schema 演化 | 追加列兼容，重命名不兼容，通过 merge_insert 实现 |

**数据生命周期**：

| 阶段 | URI 模式 | 保留策略 |
|------|---------|---------|
| Raw | `lance://raw/{run_id}/{source}/` | 7 天后清理 |
| Processed | `lance://processed/{run_id}/{source}/` | 打 Tag 永久保留 |
| Clean | `lance://clean/{dataset_id}/` | 生产 Tag 永久保留 |
| Archive | `lance://archive/{dataset_id}/` | S3 Glacier 冷存储 |

**向量索引策略**：

| 索引类型 | 适用规模 | 内存 | 延迟 | 参数 |
|----------|---------|------|------|------|
| IVF_PQ | >100 万向量 | 最低 | 中 | `num_partitions=256, num_sub_vectors=16` |
| IVF_SQ | 10-100 万 | 中 | 低 | `num_partitions=256` |
| HNSW | <100 万 | 高 | 最低 | `num_edges=32` |

**全文检索**：`create_index(column, index_type="FTS", with_position=True)` 支持短语查询。

### 5.4 Retrieval Layer

**双引擎设计**：

| 引擎 | 职责 | 技术 |
|------|------|------|
| DuckDB OLAP | SQL 聚合、JOIN、窗口函数、数据审计 | Arrow 零拷贝桥接 Lance |
| Lance Vector | ANN 向量检索、混合检索、全文检索 | IVF_PQ / HNSW / FTS |

**DuckDB + Lance 集成方式**：

```python
import duckdb, lance

conn = duckdb.connect()
ds = lance.dataset("lance://processed/{dataset_id}/")
conn.register("lake", ds)
# DuckDB 自动将 SQL 谓词下推到 Lance Scanner
conn.execute("SELECT modality, COUNT(*) FROM lake GROUP BY modality")
```

**DuckDB Lakehouse 扩展**：httpfs（云存储）、Iceberg、Delta、DuckLake、Unity Catalog、MotherDuck 均为核心扩展，自动加载。

**统一查询入口（Ray Serve）**：

| 请求类型 | 路由 | 实现 |
|----------|------|------|
| `type=sql` | DuckDB OLAP | `conn.execute(sql)` |
| `type=vector` | Lance ANN | `ds.search("embedding").nearest(q=..., k=...)` |
| `type=hybrid` | Lance + filter | `.nearest().where(sql_filter)` |

**Ray Serve 部署**：`autoscaling_config={min_replicas:1, max_replicas:8}`，分卡服务 `num_gpus=0.25`。

### 5.5 Catalog Layer

**双元数据管理**：

| 组件 | 职责 |
|------|------|
| DuckDB（Ray Actor） | 管线元数据：datasets、lineage、pipeline_runs、schemas、tags 表 |
| Lance Namespace | 数据发现：9 种 Catalog 后端（REST/Directory/Hive/Gravitino/Polaris/Glue/OneLake/Dataproc/UC） |
| MotherDuck（可选） | 生产环境团队共享 Catalog |

**DuckDB Catalog Schema**：

| 表 | 核心字段 | 用途 |
|----|---------|------|
| `datasets` | dataset_id, name, lance_uri, modality[], schema_json, row_count, tags[], version | 数据集注册 |
| `lineage` | upstream_dataset_id, downstream_dataset_id, pipeline_id, transform_description | 血缘追踪 |
| `pipeline_runs` | run_id, pipeline_id, status, stage_name, lance_uri, error_message | 运行记录 |
| `schemas` | dataset_id, version, fields_json | Schema 演化历史 |
| `tags` | tag_name, dataset_ids[] | 标签索引 |

**Ray Actor 部署**：`@ray.remote(num_cpus=1, max_restarts=2, max_task_retries=3)` 单例高可用。

### 5.6 Event Bus

**三级演进**：

| 阶段 | 实现 | 延迟 | 持久化 |
|------|------|------|--------|
| 开发 | `asyncio.Queue` | <1ms | 内存 |
| 分布式 | `RayEventBusActor` | ~5ms | Object Store |
| 生产 | Redis Streams / Kafka | ~10ms | 磁盘 |

**核心接口**：`emit(event)`, `consume(event_type)`, `replay(run_id)`。与 Catalog Actor 联动：`stage.completed` 事件自动记录到 pipeline_runs 表。

---

## 6. Daft 能力边界

### 6.1 Ingestion Layer（Daft 原生 ~80%）

外部集成点：PDF（kreuzberg）、REST API（httpx）。

### 6.2 Processing Layer（Daft 原生 ~65%）

外部集成点：视频关键帧（opencv）、质量评分（NeMo Curator）、语义去重（Lance 向量索引）。

### 6.3 Daft on Ray 集成

`ray.init()` 后 Daft 自动检测 Ray 环境，分布式执行透明。Daft Lazy Evaluation + Ray 分布式调度 = 最优组合。

---

## 7. Cross-cutting Concerns

### 7.1 配置管理

Pydantic Settings v2，环境变量 + `.env` 文件，`DINTELLI_` 前缀。关键配置：`ray_address`, `catalog_path`, `lance_base_uri`, `event_bus_type`, `motherduck_token`。

### 7.2 日志

structlog 结构化日志，StageEvent 驱动，关键字段：`run_id`, `stage`, `lance_uri`, `row_count`, `duration_s`。

### 7.3 监控

Prometheus 指标：`stage_duration_seconds`（Histogram）、`rows_processed_total`（Counter）、`lance_dataset_rows`（Gauge）、`ray_active_tasks`（Gauge）、`gpu_utilization`（Gauge）。

### 7.4 错误处理

四层策略：Metaflow `@retry` → `@catch` → Ray Actor `max_restarts` → Lance versioning 回滚。

### 7.5 安全

凭证通过环境变量管理（Pydantic Settings 不存储凭证）。DuckDB GRANT/REVOKE 权限控制（MotherDuck）。S3 SSE-KMS 静态加密 + TLS 传输加密。Ray Dashboard auth 认证。

---

## 8. 项目结构

```
dintellihub/
├── pyproject.toml
├── src/dintellihub/
│   ├── config/                     # Pydantic Settings 配置管理
│   ├── ingestion/connectors/       # 文件/数据库/API 连接器
│   ├── processing/
│   │   ├── cleaners/               # 数据清洗算子
│   │   ├── transformers/           # 转换算子
│   │   ├── embedders/              # Embedding 算子
│   │   └── curator/                # NeMo Curator 集成
│   ├── storage/
│   │   ├── lance_io.py             # Lance 读写
│   │   ├── versioning.py           # 版本管理
│   │   ├── indexer.py              # 向量索引
│   │   └── distributed.py          # 两阶段提交
│   ├── retrieval/
│   │   ├── query_engine.py         # 统一查询入口
│   │   ├── olap.py                 # DuckDB OLAP
│   │   ├── vector_search.py        # Lance 向量检索
│   │   └── serve.py                # Ray Serve
│   ├── catalog/
│   │   ├── actor.py                # Ray Actor Catalog
│   │   ├── schema.py               # DuckDB 元数据 schema
│   │   ├── registry.py             # 数据集注册
│   │   └── lineage.py              # 血缘追踪
│   ├── events/
│   │   ├── bus.py                  # 事件总线
│   │   └── models.py               # StageEvent 模型
│   └── workflows/                  # Metaflow flows
│       ├── ingest_flow.py
│       ├── process_flow.py
│       ├── quality_flow.py
│       ├── full_pipeline.py
│       └── schedule.py
├── tests/
│   ├── unit/
│   ├── integration/
│   └── e2e/
├── deploy/compose/
│   └── docker-compose.yml
└── docs/superpowers/specs/
```

---

## 9. 测试策略

| 层级 | 范围 | 工具 | 覆盖率 |
|------|------|------|--------|
| Unit | 单函数/算子 | pytest | ≥ 80% |
| Integration | 组件间交互 | pytest + Ray init | 关键路径 100% |
| E2E | 完整管线 | Metaflow `run` + 断言 | 主流程 100% |

测试数据：`sample_lance_dataset` fixture 提供 1000 行可重复测试数据。

---

## 10. 性能参考

### 开发环境

8 CPU / 16 GB RAM / 可选 1x T4 GPU / 100 GB SSD。

### 生产环境

Ray Head (4 CPU, 8 GB) + 3-10 CPU Workers (8 CPU/节点) + 2-4 GPU Workers (1-4 GPU/节点) + S3/MinIO 存储。

### 吞吐量参考

| 操作 | 规模 | 资源 | 预期 |
|------|------|------|------|
| Parquet → Lance | 100 GB | 8 CPU | ~15 min |
| 图片 Embedding (CLIP) | 100 万张 | 4x T4 | ~20 min |
| 文本质量分类 (NeMo Curator) | 1000 万条 | 4x A100 | ~30 min |
| IVF_PQ 索引构建 | 100 万向量 768d | 8 CPU | ~10 min |
| DuckDB 聚合查询 | 1000 万行 | 8 CPU | <5s |
| HNSW 向量检索 | 100 万向量 | 1 CPU | <50ms/query |

---

## 11. 技术栈

| 组件 | 技术 | 角色 |
|------|------|------|
| 包管理 | Poetry | 依赖管理 |
| 数据处理 | Daft | 数据语义层（DataFrame API） |
| 分布式计算 | Ray (Core/Data/Serve) | 算力调度 + 容错 + 服务 |
| 存储 | Lance | 多模态格式 + 向量索引 + 版本管理 |
| OLAP 查询 | DuckDB | SQL 分析 + Catalog 元数据 |
| 云端 Catalog | MotherDuck | 团队共享（可选） |
| 工作流编排 | Metaflow | 用户友好编排前端 |
| 生产调度 | Argo Workflows | K8s 工作流引擎 |
| 数据质量 | NVIDIA NeMo Curator | GPU 加速质量管线 |
| 容器化 | Docker Compose | 本地开发环境 |

## 12. 参考架构

- **DREAM 栈** (CloudKitchens): Daft + Ray + Metaflow + Argo + Poetry
- **火山引擎 LAS**: Lance + Daft + Ray + NeMo Curator + Catalog
- **DuckDB Lakehouse**: DuckDB + Lance + httpfs/iceberg/delta
- 本方案融合三者精华，增加事件驱动层间通信和 Ray 全栈分布式能力
