# Arrow Lake v1.0 产品架构设计文档

**版本**: v1.0-draft | **日期**: 2026-04-20
**基于**: v0.2.0 五方评审共识 (架构师 5.8 / 开发 6.2 / BA 3.5 / PM 3.2 / 敏捷PM 4.5)
**状态**: 设计阶段，待用户审批

---

## Context

v0.2.0 阶段评审揭示：代码质量 8/10 但生产就绪度仅 ~5/10。核心差距不在功能而在生产运维"最后一公里"。用户决定在推进生产基线的同时，增加两个核心业务能力：

1. **多模态 RAG + 知识图谱 (HugeGraph)** — 从"数据平台"升级为"智能数据平台"
2. **MinIO 真实集成** — 当前所有数据仅存储在本地文件系统

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
                  |                        |
    +-------------+-------------+  +-------+-------+
    |             |             |  |               |
+---v---+  +-----v-----+ +----v---v--+  +---------v---------+
| Lance |  |  MinIO    | | HugeGraph |  |   LLM Providers   |
|  DB   |  |  (S3)     | | (KG)      |  | OpenAI/Anthropic/  |
| 向量+  |  | 媒体二进制 | | 图存储    |  | vLLM/Ollama        |
| 元数据 |  | 生命周期   | | Gremlin   |  +-------------------+
+-------+  +-----+-----+ +----+------+
    |             |             |
    +-----------+---------------+
                |
    +-----------v-----------+
    |  Ray Cluster          |
    |  (分布式计算引擎)       |
    +-----------+-----------+
                |
    +-----------v-----------+
    |  Prometheus + OTel    |
    |  (可观测性)            |
    +-----------------------+
```

### 组件职责矩阵

| 组件 | 职责 | v0.2 状态 | v1.0 变更 |
|------|------|----------|----------|
| LanceStorageManager | 向量+元数据存储 | 仅本地路径 | 传递 storage_options 支持S3 |
| MinIO | 对象存储 | 配置就绪,未连接 | 全链路集成,媒体二进制存储 |
| HugeGraph Server | 知识图谱 | 不存在 | 新增: 图Schema,实体抽取,GraphRAG |
| RAG Engine | 检索增强生成 | 仅检索(R),无生成(G) | 新增: LLM抽象,Prompt模板,上下文管理 |
| FastAPI REST | HTTP接口 | 36端点,API Key auth | 新增: RAG/KG端点,RBAC,版本控制 |
| Ray Cluster | 分布式计算 | 已有 | 新增: KG构建作为Ray任务 |
| Prometheus+OTel | 可观测性 | 基础metrics | 新增: traces,完整healthcheck,告警规则 |

---

## 二、数据流总体关系

```
[原始数据] --ingest--> [MinIO(二进制)] + [LanceDB(元数据+向量)]
                                   |
                      +------------+------------+
                      |                         |
               [KG Construction]         [Embedding]
                      |                         |
              [HugeGraph(实体+关系)]    [LanceDB(向量列)]
                      |                         |
                      +------------+------------+
                                   |
                          [RAG Query Flow]
                                   |
                      +------------+------------+
                      |                         |
              [Vector/FTS/Hybrid]        [Graph Traversal]
                      |                         |
                      +------------+------------+
                                   |
                          [Context Assembly]
                                   |
                          [LLM Generation]
                                   |
                          [Cited Response]
```

---

## 三、模块设计

### 3A. MinIO/S3 生产存储集成

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

## 四、数据流详细设计

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

## 五、文件变更清单

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

## 六、新增依赖

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

## 七、配置设计

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

## 八、部署扩展

### Docker Compose 新增

```yaml
hugegraph-server:
  image: hugegraph/hugegraph-server:1.5.0
  container_name: arrow-lake-hugegraph
  ports: ["8080:8080"]
  volumes: [hugegraph-data:/opt/hugegraph-data]
  healthcheck:
    test: ["CMD-SHELL", "curl -sf http://localhost:8080/graphs/arrow_lake_kg/graph || exit 1"]
    interval: 15s
    timeout: 10s
    retries: 5
    start_period: 30s
```

### Profile 矩阵

| Profile | 服务 | 用途 |
|---------|------|------|
| core | api, minio, hugegraph | 最小生产 |
| dev | core + ray, jupyter | 开发 |
| gpu | dev + GPU 资源 | GPU 加速 |
| monitoring | core + prometheus, grafana | 可观测 |

---

## 九、迁移路径 (4 个 Milestone)

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

## 十、风险与缓解

| 风险 | 影响 | 概率 | 缓解措施 |
|------|------|------|---------|
| LanceDB S3 storage_options 高并发不稳定 | 高 | 中 | 连接池重试; 保留本地模式降级 |
| LLM API 延迟影响 RAG 响应 | 中 | 高 | SSE 流式; 本地 vLLM 降级; 缓存热门查询 |
| HugeGraph 内存占用过高 | 中 | 中 | 限制遍历深度; 分批构建; 独立部署可单独扩容 |
| RBAC 破坏现有 API Key 用户 | 高 | 低 | 双模式认证; API Key 映射为 admin 角色 |
| 实体抽取 LLM 幻觉 | 中 | 高 | 置信度阈值过滤; 人工审核模式 |

---

## 附录: 相关文档

- [v0.2.0 阶段评审报告](phase-review-v0.2.0.md)
- [ADR-05: DuckDB OLAP Deviation](adr-05-duckdb-olap-deviation.md)
- [HugeGraph Skill 文档索引](../dev_notes/hugegraph_build_skills/INDEX.md)
