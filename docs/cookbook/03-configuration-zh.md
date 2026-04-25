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

config.storage    # StorageConfig       — 存储层
config.llm        # LLMConfig           — LLM 提供商
config.rag        # RAGConfig           — RAG 流水线
config.vector     # VectorSearchConfig  — 向量搜索
config.fts        # FullTextSearchConfig— 全文搜索
config.hybrid     # HybridSearchConfig  — 混合搜索
config.embedding  # EmbeddingConfig     — 嵌入模型
config.quality    # QualityConfig       — 质量过滤
config.olap       # OlapConfig          — OLAP 查询
config.api        # ApiConfig           — API 服务
config.auth       # AuthConfig          — 认证
config.rate_limit # RateLimitConfig     — 限流
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
```

**LLMConfig 字段说明：**

| 字段                | 类型              | 默认值                                         | 说明 |
| ----------------- | --------------- | ------------------------------------------- | -- |
| `provider`        | `"openai"`      | 后端：`openai`, `anthropic`, `vllm`, `ollama` |    |
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
| `default_index_type` | `"IVF_PQ"` | 索引类型：`IVF_PQ`, `IVF_FLAT`, `IVF_HNSW_PQ` |
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
