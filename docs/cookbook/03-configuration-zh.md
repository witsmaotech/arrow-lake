# Arrow Lake 配置系统

Arrow Lake 采用 **四层覆盖机制**，优先级从低到高为：代码默认值 → `.env` 文件 → 环境变量 → YAML 配置文件。每一层都会覆盖前一层中同名的配置项。

```python
from arrow_lake.config import (
    ArrowLakeConfig,
    StorageConfig,
    LLMConfig,
    VectorSearchConfig,
    FullTextSearchConfig,
    HybridSearchConfig,
)
from arrow_lake import Lake

# 方式 1: 纯代码配置（优先级最低，可被更高层覆盖）
config = ArrowLakeConfig()
config.storage = StorageConfig(backend="local", base_uri="./data")
config.llm = LLMConfig(
    provider="ollama",
    model="qwen3.5:9b",
    api_base="http://localhost:11434",
)
lake = Lake(base_uri="./data", config=config)

# 方式 2: 从 YAML 加载（优先级最高）
lake = Lake.from_yaml("config.yaml")
```

***

## 1. 四层覆盖机制

| 优先级    | 层级        | 来源                            | 说明                                     |
| ------ | --------- | ----------------------------- | -------------------------------------- |
| 1 (最低) | 代码默认值     | Pydantic field defaults       | 每个配置字段的初始默认值                           |
| 2      | `.env` 文件 | 项目根目录 `.env`                  | pydantic-settings 自动加载                 |
| 3      | 环境变量      | `ARROW_LAKE__` 前缀             | 如 `ARROW_LAKE__STORAGE__BACKEND=local` |
| 4 (最高) | YAML 文件   | `ArrowLakeConfig.from_yaml()` | 显式加载，覆盖以上所有层                           |

环境变量使用 `ARROW_LAKE__` 前缀 + `__` 分隔嵌套结构。例如：

```bash
# 设置存储后端
export ARROW_LAKE__STORAGE__BACKEND=s3

# 设置 LLM 提供商
export ARROW_LAKE__LLM__PROVIDER=openai

# 设置向量搜索度量
export ARROW_LAKE__VECTOR__METRIC=cosine
```

***

## 2. 核心配置类

`ArrowLakeConfig` 是顶层入口，聚合了 30+ 个子配置模块：

```python
config = ArrowLakeConfig()

# --- 核心 ---
config.storage    # StorageConfig        — 存储层 (local, MinIO, S3, GCS)
config.compute    # ComputeConfig        — 计算资源
config.http       # HttpConfig           — HTTP 客户端设置
config.observability # ObservabilityConfig — 日志和追踪

# --- 搜索 ---
config.vector     # VectorSearchConfig   — 向量搜索
config.fts        # FullTextSearchConfig — 全文搜索
config.hybrid     # HybridSearchConfig   — 混合搜索 (RRF 融合)
config.faceted    # FacetedSearchConfig  — 分面搜索
config.ensemble   # EnsembleSearchConfig — 集成搜索

# --- AI / RAG ---
config.llm        # LLMConfig            — LLM 提供商
config.rag        # RAGConfig            — RAG 流水线
config.embedding  # EmbeddingConfig      — 嵌入模型
config.hugegraph  # HugeGraphConfig      — HugeGraph 知识图谱

# --- 媒体与文档 ---
config.media      # MediaConfig          — 媒体处理
config.decode     # DecodeConfig         — 图像解码设置
config.document   # DocumentConfig       — PDF 解析和分块
config.export     # ExportConfig         — 导出设置

# --- 数据 ---
config.olap       # OlapConfig           — OLAP / DuckDB 查询
config.daft       # DaftConfig           — Daft 计算引擎
config.quality    # QualityConfig        — 质量过滤

# --- 基础设施 ---
config.api        # ApiConfig            — API 服务
config.auth       # AuthConfig           — 认证
config.rate_limit # RateLimitConfig      — 限流
config.redis      # RedisConfig          — Redis 分布式会话
config.workflow   # WorkflowConfig       — 工作流编排
config.argo       # ArgoConfig           — Argo Workflows 集成
config.autoscale  # AutoscaleConfig      — 自动扩缩容
config.lifecycle  # LifecycleConfig      — 数据集生命周期管理

# --- 治理 ---
config.gravitino  # GravitinoConfig      — Apache Gravitino 元数据目录
config.lineage    # LineageConfig        — 数据血缘追踪
config.audit      # AuditConfig          — 审计日志
config.opentelemetry # OpenTelemetryConfig — OpenTelemetry 追踪
```

***

## 3. 存储配置 (StorageConfig)

控制 Lance 数据集的存储位置和 S3/MinIO 连接参数：

```python
from arrow_lake.config import StorageConfig, StorageBackend

# 本地存储
local_storage = StorageConfig(backend=StorageBackend.LOCAL, base_uri="./data")

# MinIO 存储
minio_storage = StorageConfig(
    backend=StorageBackend.MINIO,
    base_uri="datasets",
    s3_endpoint="http://localhost:9000",
    s3_access_key="minioadmin",
    s3_secret_key="minioadmin",
    s3_bucket="arrow-lake",
    s3_region="us-east-1",
)

# AWS S3 存储
s3_storage = StorageConfig(
    backend=StorageBackend.S3,
    base_uri="production",
    s3_endpoint="",  # 使用 AWS 默认端点
    s3_access_key="AKIA...",
    s3_secret_key="...",
    s3_bucket="my-arrow-lake",
    s3_region="ap-southeast-1",
)

# 辅助方法
print(minio_storage.s3_uri)                  # s3://arrow-lake/datasets
opts = minio_storage.to_storage_options()     # lance/boto3 存储选项
sqls = minio_storage.to_duckdb_s3_config()    # DuckDB SET 语句列表
env_storage = StorageConfig.from_env()        # 从环境变量自动构建
```

**StorageConfig 字段说明：**

| 字段              | 类型                        | 默认值                                 | 说明 |
| --------------- | ------------------------- | ----------------------------------- | -- |
| `backend`       | `"minio"`                 | 存储后端：`local`, `minio`, `s3`, `gcs` |    |
| `base_uri`      | `"./data"`                | Lance 数据集存储路径                       |    |
| `s3_endpoint`   | `"http://localhost:9000"` | S3 兼容端点                             |    |
| `s3_access_key` | `""`                      | S3 访问密钥                             |    |
| `s3_secret_key` | `""`                      | S3 秘密密钥                             |    |
| `s3_bucket`     | `"arrow-lake"`            | 默认存储桶                               |    |
| `s3_region`     | `"us-east-1"`             | S3 区域                               |    |
| `s3_uploads_bucket` | `""`                  | v1.9.5 上传原始文件独立桶（空=复用 `s3_bucket`，与 Lance 数据面隔离） |    |
| `uploads_expiration_days` | `0`             | 上传原始文件自动过期天数（0=禁用；启用后过期将无法重解析如切换 OCR 后端） |    |
| `lance_cache_size` | `0`                     | Lance 读缓存字节数（0=禁用）。生产环境建议增大以加速重复扫描 |    |

***

## 4. LLM 提供商配置 (LLMConfig)

用于 RAG 生成阶段的 LLM 后端配置：

```python
from arrow_lake.config import LLMConfig, LLMProviderType

# Ollama (本地)
ollama_cfg = LLMConfig(
    provider=LLMProviderType.OLLAMA,
    model="qwen3.5:9b",
    api_base="http://localhost:11434",
    temperature=0.7,
)

# OpenAI
openai_cfg = LLMConfig(
    provider=LLMProviderType.OPENAI,
    model="gpt-4o-mini",
    api_key="sk-...",
    temperature=0.3,
    max_tokens=4096,
)

# Anthropic
anthropic_cfg = LLMConfig(
    provider=LLMProviderType.ANTHROPIC,
    model="claude-sonnet-4-20250514",
    api_key="sk-ant-...",
    anthropic_version="2023-06-01",
)

# vLLM (自部署)
vllm_cfg = LLMConfig(
    provider=LLMProviderType.VLLM,
    model="Qwen/Qwen3-8B",
    api_base="http://localhost:8000/v1",
    timeout_seconds=120.0,
)

# DeepSeek
deepseek_cfg = LLMConfig(
    provider=LLMProviderType.DEEPSEEK,
    model="deepseek-chat",
    api_key="sk-...",
    api_base="https://api.deepseek.com/v1",
)
```

**LLMConfig 字段说明：**

| 字段                | 类型              | 默认值                                         | 说明 |
| ----------------- | --------------- | ------------------------------------------- | -- |
| `provider`        | `"openai"`      | 后端：`openai`, `anthropic`, `vllm`, `ollama`, `deepseek` |    |
| `model`           | `"gpt-4o-mini"` | 模型名称                                        |    |
| `api_key`         | `""`            | API 密钥（本地模型可为空）                             |    |
| `api_base`        | `""`            | 自定义 API 端点                                  |    |
| `temperature`     | `0.7`           | 采样温度 (0.0-2.0)                              |    |
| `max_tokens`      | `2048`          | 最大生成 token 数                                |    |
| `timeout_seconds` | `60.0`          | HTTP 请求超时 (>= 1.0)                          |    |

***

## 5. 搜索配置

### 5.1 向量搜索 (VectorSearchConfig)

```python
from arrow_lake.config import VectorSearchConfig, DistanceMetric, VectorIndexType

vector_cfg = VectorSearchConfig(
    metric=DistanceMetric.COSINE,       # 距离度量：cosine / l2 / dot
    default_index_type=VectorIndexType.IVF_PQ,
    default_top_k=10,
    num_partitions=256,                 # IVF 分区数 (大数据集自动调整)
    num_sub_vectors=24,                 # PQ 子向量数 (8 的倍数)
    num_bits=8,                         # PQ 量化位数
    nprobes=20,                         # 搜索探测分区数
    max_nprobes=256,
)
```

| 字段                   | 默认值        | 说明                                        |
| -------------------- | ---------- | ----------------------------------------- |
| `metric`             | `"cosine"` | 距离度量：`cosine`, `l2`, `dot`               |
| `default_index_type` | `"IVF_PQ"` | 索引类型：`IVF_PQ`, `IVF_FLAT`, `IVF_HNSW_PQ`, `HNSW` |
| `default_top_k`      | `10`       | 默认返回结果数                                   |
| `num_partitions`     | `256`      | IVF 分区数                                   |
| `num_sub_vectors`    | `24`       | PQ 子向量数 (8 的倍数)                           |
| `nprobes`            | `20`       | 搜索时探测的分区数                                 |

### 5.2 全文搜索 (FullTextSearchConfig)

```python
from arrow_lake.config import FullTextSearchConfig

fts_cfg = FullTextSearchConfig(
    default_top_k=10,
    fts_column="text_content",     # 索引的文本列
    stem=True,                     # 词干提取
    remove_stop_words=True,
    lower_case=True,
    tokenizer_type="jieba",        # 中文推荐 jieba; 英文用 default
    jieba_user_dict=None,
)
```

| 字段               | 默认值              | 说明                                        |
| ---------------- | ---------------- | ----------------------------------------- |
| `fts_column`     | `"text_content"` | 索引的文本列名                                   |
| `stem`           | `True`           | 词干提取                                      |
| `tokenizer_type` | `"jieba"`        | `"default"` (LanceDB 内置) 或 `"jieba"` (中文) |

### 5.3 混合搜索 (HybridSearchConfig)

```python
from arrow_lake.config import HybridSearchConfig

hybrid_cfg = HybridSearchConfig(
    default_top_k=10,
    rrf_k=60,                      # RRF 常数 (论文推荐 K=60)
    vector_top_k_multiplier=3,     # 向量候选数 = top_k * multiplier
    fts_top_k_multiplier=3,
)
# 通过 RRF 融合向量搜索和全文搜索排序
```

***

## 6. 从 YAML 加载配置

YAML 具有最高优先级。创建 `config.yaml`：

```yaml
# config.yaml — 生产配置
storage:
  backend: minio
  base_uri: production
  s3_endpoint: http://minio:9000
  s3_access_key: ${MINIO_ACCESS_KEY}
  s3_secret_key: ${MINIO_SECRET_KEY}
  s3_bucket: arrow-lake
  s3_region: us-east-1

llm:
  provider: ollama
  model: qwen3.5:9b
  api_base: http://llm-host:11434
  temperature: 0.5

embedding:
  model: Qwen/Qwen3-Embedding-0.6B
  backend: local

vector:
  metric: cosine
  default_index_type: IVF_PQ
  num_partitions: 256
  nprobes: 20

fts:
  fts_column: text_content
  tokenizer_type: jieba

hybrid:
  rrf_k: 60
  vector_top_k_multiplier: 3

redis:
  enabled: true
  url: "redis://redis:6379/0"
  password: "${REDIS_PASSWORD}"
  ssl: false
  redis_pool_size: 10
```

```python
from arrow_lake import Lake
from arrow_lake.config import ArrowLakeConfig

# 方式 1: Lake.from_yaml 加载 YAML 并合并到默认配置之上
lake = Lake.from_yaml("config.yaml", base_uri="./data")

# 方式 2: 只构建配置对象
config = ArrowLakeConfig.from_yaml("config.yaml")
print(config.storage.s3_endpoint)  # http://minio:9000
```

> **注意**: YAML 加载使用深度合并 (deep-merge)，未指定的字段保留代码默认值。YAML 的值会覆盖 `.env` 和环境变量中的同名配置。

***

## 7. .env 文件示例

在项目根目录创建 `.env`，pydantic-settings 自动加载：

```bash
# .env — 开发环境
ARROW_LAKE__STORAGE__BACKEND=local
ARROW_LAKE__STORAGE__BASE_URI=./data

# MinIO/S3
ARROW_LAKE__STORAGE__S3_ENDPOINT=http://localhost:9000
ARROW_LAKE__STORAGE__S3_ACCESS_KEY=minioadmin
ARROW_LAKE__STORAGE__S3_SECRET_KEY=minioadmin
ARROW_LAKE__STORAGE__S3_BUCKET=arrow-lake

# LLM
ARROW_LAKE__LLM__PROVIDER=ollama
ARROW_LAKE__LLM__MODEL=qwen3.5:9b
ARROW_LAKE__LLM__API_BASE=http://localhost:11434

# 向量搜索 / 全文搜索 / 嵌入
ARROW_LAKE__VECTOR__METRIC=cosine
ARROW_LAKE__FTS__TOKENIZER_TYPE=jieba
ARROW_LAKE__EMBEDDING__BACKEND=local
ARROW_LAKE__EMBEDDING__MODEL=Qwen/Qwen3-Embedding-0.6B
```

***

## 8. 配置最佳实践

1. **开发环境**: 使用 `.env` 文件管理本地配置，不要将含密钥的 `.env` 提交到版本控制
2. **生产环境**: 使用 YAML 配置文件，敏感信息通过环境变量注入
3. **容器部署**: 通过 `ARROW_LAKE__` 环境变量覆盖关键配置，无需修改配置文件
4. **中文场景**: 设置 `fts.tokenizer_type = "jieba"` 以获得更好的中文分词效果
5. **大向量维度**: `num_sub_vectors` 必须是 8 的倍数，且不大于向量维度
6. **生产 Redis**: 运行多个 API 副本时 (HPA / Kubernetes) 启用 `redis.enabled = true`，使 DuckDB 会话信号量跨 Pod 协调
7. **Redis TLS**: 连接托管 Redis 服务 (ElastiCache、Azure Cache 等) 时设置 `redis.ssl = true` 并提供 `redis.password`

***

## 9. Redis 分布式会话配置 (RedisConfig)

当 Arrow Lake 运行在多个 API 副本之后时，DuckDB 会话协调和 JWT Token 黑名单必须在进程间共享。`RedisConfig` 启用基于 Redis 的分布式信号量来替代默认的 `threading.Semaphore`。

当 `enabled` 为 `False`（默认值）时，系统回退到进程内同步。

```python
from arrow_lake.config import RedisConfig

# 本地开发（进程内信号量，无需 Redis）
redis_cfg = RedisConfig()  # 默认 enabled=False

# 生产环境使用 Redis
redis_cfg = RedisConfig(
    enabled=True,
    url="redis://redis:6379/0",
    password="",
    ssl=False,
    semaphore_key_prefix="arrow_lake:semaphore:",
    semaphore_ttl_seconds=300,
    redis_pool_size=10,
)
```

**RedisConfig 字段说明：**

| 字段                          | 类型     | 默认值                          | 说明                                          |
| --------------------------- | ------ | ---------------------------- | ------------------------------------------- |
| `enabled`                   | `bool` | `False`                      | 启用基于 Redis 的分布式信号量和 JWT 黑名单                |
| `url`                       | `str`  | `"redis://localhost:6379/0"` | Redis 连接 URL                                |
| `password`                  | `str`  | `""`                         | Redis 认证密码                                  |
| `ssl`                       | `bool` | `False`                      | 启用 TLS 加密 Redis 连接                          |
| `ssl_cert_reqs`             | `str`  | `"required"`                 | `ssl=True` 时的 SSL 证书验证模式                    |
| `semaphore_key_prefix`      | `str`  | `"arrow_lake:semaphore:"`    | 分布式信号量计数器的 Redis 键前缀                        |
| `semaphore_ttl_seconds`     | `int`  | `300` (>= 1)                 | 信号量键的 TTL — 自动回收过期的许可                       |
| `redis_pool_size`           | `int`  | `10` (>= 1)                  | Redis 客户端连接池大小                              |
| `instance_registry_key`     | `str`  | `"arrow_lake:instances"`     | 多实例注册的 Redis 键                              |
| `instance_heartbeat_ttl_seconds` | `int` | `30` (>= 5)                | 实例心跳键的 TTL                                  |

### YAML 配置

```yaml
# config.yaml — Redis 分布式会话协调
redis:
  enabled: true
  url: "redis://redis:6379/0"
  password: "${REDIS_PASSWORD}"
  ssl: false
  semaphore_key_prefix: "arrow_lake:semaphore:"
  semaphore_ttl_seconds: 300
  redis_pool_size: 10
```

### 环境变量覆盖

```bash
# 启用 Redis 并配置连接
ARROW_LAKE__REDIS__ENABLED=true
ARROW_LAKE__REDIS__URL=redis://redis:6379/0
ARROW_LAKE__REDIS__PASSWORD=your-redis-password
ARROW_LAKE__REDIS__SSL=false
ARROW_LAKE__REDIS__SEMAPHORE_KEY_PREFIX=arrow_lake:semaphore:
ARROW_LAKE__REDIS__SEMAPHORE_TTL_SECONDS=300
ARROW_LAKE__REDIS__REDIS_POOL_SIZE=10
```

### 工作原理

`RedisCountingSemaphore` 使用 Lua 脚本实现原子的获取/释放操作：
- **获取 (Acquire)**：当 Redis 计数器低于 `max_permits` 时，原子递增计数器；设置 TTL 自动回收过期的许可。
- **释放 (Release)**：原子递减计数器，防止下溢。
- **回退 (Fallback)**：如果 Redis 不可用，透明回退到 `threading.Semaphore` 并记录警告日志。

***

## 10. 系统数据库配置 (SystemDBConfig)

> v1.9.0 引入：统一的关系型 **控制面** 存储（基于 libSQL / Turso），承载 RBAC、身份、personal token、catalog 注册表、任务历史、血缘索引、RAG 会话和治理历史。**数据面**（Lance / DuckDB / HugeGraph / MinIO）完全不受影响。

当 `enabled` 为 `False`（默认值）时，控制面结构退化为 v1.9.0 之前的内存 / 临时文件行为，可逐步灰度接入。

```python
from arrow_lake.config import SystemDBConfig

# 开发：嵌入式（无服务器、无 token）
dev_db = SystemDBConfig()  # 默认 enabled=False, url="file:local.db"

# 生产：自托管 libSQL server（4 workers）
prod_db = SystemDBConfig(
    enabled=True,
    url="http://system-db:8080",
    auth_token="${SQLD_AUTH_TOKEN}",
    fail_mode="fail_close",          # RBAC/身份：store 宕机时拒绝请求
    serve_stale_on_error=False,      # 安全 fail-close（默认）
    acl_cache_ttl_seconds=5.0,       # 多 worker 最终一致性窗口
)
```

**部署模式由 `url` 选择：**

| `url`                 | 模式           | 说明                                   |
| --------------------- | -------------- | -------------------------------------- |
| `file:local.db`       | 嵌入式（默认） | 开发，无服务器、无 token               |
| `http://system-db:8080` | 自托管 libSQL  | 生产，4 workers                        |
| `:memory:`            | 内存           | 单元测试                               |

**SystemDBConfig 关键字段：**

| 字段                       | 默认值             | 说明 |
| -------------------------- | ------------------ | ---- |
| `enabled`                  | `False`            | 启用控制面数据库（关闭=退化为内存/临时文件） |
| `url`                      | `"file:local.db"`  | libSQL 连接 URL（决定部署模式） |
| `auth_token`               | `""`               | 远程 server 的认证 token（嵌入式留空） |
| `fail_mode`                | `"fail_close"`     | `"fail_close"`（RBAC/身份，宕机拒绝）或 `"fail_soft"`（catalog/tasks/rag，记日志降级） |
| `serve_stale_on_error`     | `False`            | **⚠️ 安全：FAIL-OPEN**。`True`=store 不可达时返回上次缓存决策（可能在中断期放行已撤销权限）。默认 `False`=安全 fail-close。仅在显式接受该权衡时启用，正确的高可用方案是 sqld HA |
| `acl_cache_ttl_seconds`    | `5.0`              | 每 worker 短 TTL ACL 缓存（多 worker 最终一致性窗口） |

***

## 11. RAG 流水线配置 (RAGConfig)

控制 RAG 检索增强生成的检索策略、重排、两阶段 LLM 与验证。默认检索策略为 **hybrid**（向量 + 全文 RRF 融合）。

```python
from arrow_lake.config import RAGConfig, LLMConfig

rag_cfg = RAGConfig(
    enabled=True,
    default_retrieval_strategy="hybrid",   # vector | fts | hybrid
    default_top_k=10,
    # 重排（默认 ollama Qwen3-Reranker）
    reranker="ollama",
    reranker_model="dengcao/Qwen3-Reranker-0.6B:F16",
    reranker_device="auto",                # cpu | cuda | auto
    # 两阶段独立 LLM（None → 回退全局 llm）
    extract_llm=LLMConfig(provider="openai", model="qwen-turbo", api_key="sk-..."),
    qa_llm=LLMConfig(provider="openai", model="qwen-plus", api_key="sk-..."),
    # 可选：faithfulness 校验（默认关闭，opt-in）
    enable_verification=False,
)
```

**RAGConfig 关键字段：**

| 字段                          | 默认值                                  | 说明 |
| ----------------------------- | --------------------------------------- | ---- |
| `default_retrieval_strategy`  | `"hybrid"`                              | 检索策略：`vector`, `fts`, `hybrid` |
| `default_top_k`               | `10`                                    | 默认检索结果数 |
| `reranker`                    | `"ollama"`                              | 重排器类型（`ollama` / `cross_encoder` / `llm` / `noop`） |
| `reranker_model`              | `"dengcao/Qwen3-Reranker-0.6B:F16"`     | 重排模型 |
| `reranker_device`             | `"auto"`                                | 重排设备：`cpu` / `cuda` / `auto` |
| `extract_llm`                 | `None`                                  | 抽取/重排阶段 LLM（`None`=回退全局 `llm`） |
| `qa_llm`                      | `None`                                  | 问答生成阶段 LLM（`None`=回退全局 `llm`；设旗舰可显著提质量） |
| `enable_verification`         | `False`                                 | v1.9.6 轻量 faithfulness 校验（默认关闭） |
| `enable_citations`            | `True`                                  | 是否跟踪并返回引用 |
