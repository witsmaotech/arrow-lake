# Arrow Lake Configuration System

Arrow Lake uses a **four-layer override mechanism** with priorities from lowest to highest: code defaults, `.env` files, environment variables, and YAML configuration files. Each layer overrides matching keys from the layer below it.

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

# Approach 1: Code-only configuration (lowest priority, overridden by higher layers)
config = ArrowLakeConfig()
config.storage = StorageConfig(backend="local", base_uri="./data")
config.llm = LLMConfig(
    provider="ollama",
    model="qwen3.5:9b",
    api_base="http://localhost:11434",
)
lake = Lake(base_uri="./data", config=config)

# Approach 2: Load from YAML (highest priority)
lake = Lake.from_yaml("config.yaml")
```

***

## 1. Four-Layer Override Mechanism

| Priority    | Layer                 | Source                        | Description                                   |
| ----------- | --------------------- | ----------------------------- | --------------------------------------------- |
| 1 (lowest)  | Code defaults         | Pydantic field defaults       | Initial default values for every config field |
| 2           | `.env` file           | `.env` in project root        | Auto-loaded by pydantic-settings              |
| 3           | Environment variables | `ARROW_LAKE__` prefix         | e.g. `ARROW_LAKE__STORAGE__BACKEND=local`     |
| 4 (highest) | YAML file             | `ArrowLakeConfig.from_yaml()` | Explicitly loaded, overrides all layers above |

Environment variables use the `ARROW_LAKE__` prefix with `__` separating nested structures. For example:

```bash
# Set storage backend
export ARROW_LAKE__STORAGE__BACKEND=s3

# Set LLM provider
export ARROW_LAKE__LLM__PROVIDER=openai

# Set vector search metric
export ARROW_LAKE__VECTOR__METRIC=cosine
```

***

## 2. Core Configuration Classes

`ArrowLakeConfig` is the top-level entry point that aggregates 30+ sub-modules:

```python
config = ArrowLakeConfig()

config.storage    # StorageConfig       — Storage layer
config.llm        # LLMConfig           — LLM provider
config.rag        # RAGConfig           — RAG pipeline
config.vector     # VectorSearchConfig  — Vector search
config.fts        # FullTextSearchConfig— Full-text search
config.hybrid     # HybridSearchConfig  — Hybrid search
config.embedding  # EmbeddingConfig     — Embedding model
config.quality    # QualityConfig       — Quality filtering
config.olap       # OlapConfig          — OLAP queries
config.api        # ApiConfig           — API service
config.auth       # AuthConfig          — Authentication
config.rate_limit # RateLimitConfig     — Rate limiting
```

***

## 3. Storage Configuration (StorageConfig)

Controls where Lance datasets are stored and how to connect to S3/MinIO:

```python
from arrow_lake.config import StorageConfig, StorageBackend

# Local storage
local_storage = StorageConfig(backend=StorageBackend.LOCAL, base_uri="./data")

# MinIO storage
minio_storage = StorageConfig(
    backend=StorageBackend.MINIO,
    base_uri="datasets",
    s3_endpoint="http://localhost:9000",
    s3_access_key="minioadmin",
    s3_secret_key="minioadmin",
    s3_bucket="arrow-lake",
    s3_region="us-east-1",
)

# AWS S3 storage
s3_storage = StorageConfig(
    backend=StorageBackend.S3,
    base_uri="production",
    s3_endpoint="",  # Uses default AWS endpoint
    s3_access_key="AKIA...",
    s3_secret_key="...",
    s3_bucket="my-arrow-lake",
    s3_region="ap-southeast-1",
)

# Helper methods
print(minio_storage.s3_uri)                  # s3://arrow-lake/datasets
opts = minio_storage.to_storage_options()     # lance/boto3 storage options
sqls = minio_storage.to_duckdb_s3_config()    # DuckDB SET statements
env_storage = StorageConfig.from_env()        # Auto-build from environment variables
```

**StorageConfig field reference:**

| Field           | Type                      | Default                                        | Description |
| --------------- | ------------------------- | ---------------------------------------------- | ----------- |
| `backend`       | `"minio"`                 | Storage backend: `local`, `minio`, `s3`, `gcs` |             |
| `base_uri`      | `"./data"`                | Lance dataset storage path                     |             |
| `s3_endpoint`   | `"http://localhost:9000"` | S3-compatible endpoint                         |             |
| `s3_access_key` | `""`                      | S3 access key                                  |             |
| `s3_secret_key` | `""`                      | S3 secret key                                  |             |
| `s3_bucket`     | `"arrow-lake"`            | Default bucket                                 |             |
| `s3_region`     | `"us-east-1"`             | S3 region                                      |             |

***

## 4. LLM Provider Configuration (LLMConfig)

Configures the LLM backend used during RAG generation:

```python
from arrow_lake.config import LLMConfig, LLMProviderType

# Ollama (local)
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

# vLLM (self-hosted)
vllm_cfg = LLMConfig(
    provider=LLMProviderType.VLLM,
    model="Qwen/Qwen3-8B",
    api_base="http://localhost:8000/v1",
    timeout_seconds=120.0,
)
```

**LLMConfig field reference:**

| Field             | Type            | Default                                          | Description |
| ----------------- | --------------- | ------------------------------------------------ | ----------- |
| `provider`        | `"openai"`      | Backend: `openai`, `anthropic`, `vllm`, `ollama` |             |
| `model`           | `"gpt-4o-mini"` | Model name                                       |             |
| `api_key`         | `""`            | API key (can be empty for local models)          |             |
| `api_base`        | `""`            | Custom API endpoint                              |             |
| `temperature`     | `0.7`           | Sampling temperature (0.0-2.0)                   |             |
| `max_tokens`      | `2048`          | Maximum generation tokens                        |             |
| `timeout_seconds` | `60.0`          | HTTP request timeout (>= 1.0)                    |             |

***

## 5. Search Configuration

### 5.1 Vector Search (VectorSearchConfig)

```python
from arrow_lake.config import VectorSearchConfig, DistanceMetric, VectorIndexType

vector_cfg = VectorSearchConfig(
    metric=DistanceMetric.COSINE,       # Distance metric: cosine / l2 / dot
    default_index_type=VectorIndexType.IVF_PQ,
    default_top_k=10,
    num_partitions=256,                 # IVF partitions (auto-adjusted for large datasets)
    num_sub_vectors=24,                 # PQ sub-vectors (must be multiple of 8)
    num_bits=8,                         # PQ quantization bits
    nprobes=20,                         # Partitions to probe during search
    max_nprobes=256,
)
```

| Field                | Default    | Description                                     |
| -------------------- | ---------- | ----------------------------------------------- |
| `metric`             | `"cosine"` | Distance metric: `cosine`, `l2`, `dot`          |
| `default_index_type` | `"IVF_PQ"` | Index type: `IVF_PQ`, `IVF_FLAT`, `IVF_HNSW_PQ` |
| `default_top_k`      | `10`       | Default number of results to return             |
| `num_partitions`     | `256`      | Number of IVF partitions                        |
| `num_sub_vectors`    | `24`       | PQ sub-vectors (must be multiple of 8)          |
| `nprobes`            | `20`       | Number of partitions to probe during search     |

### 5.2 Full-Text Search (FullTextSearchConfig)

```python
from arrow_lake.config import FullTextSearchConfig

fts_cfg = FullTextSearchConfig(
    default_top_k=10,
    fts_column="text_content",     # Text column to index
    stem=True,                     # Stemming
    remove_stop_words=True,
    lower_case=True,
    tokenizer_type="jieba",        # Use jieba for Chinese; "default" for English
    jieba_user_dict=None,
)
```

| Field            | Default          | Description                                   |
| ---------------- | ---------------- | --------------------------------------------- |
| `fts_column`     | `"text_content"` | Name of the text column to index              |
| `stem`           | `True`           | Apply stemming                                |
| `tokenizer_type` | `"jieba"`        | `"default"` (built-in) or `"jieba"` (Chinese) |

### 5.3 Hybrid Search (HybridSearchConfig)

```python
from arrow_lake.config import HybridSearchConfig

hybrid_cfg = HybridSearchConfig(
    default_top_k=10,
    rrf_k=60,                      # RRF constant (paper recommends K=60)
    vector_top_k_multiplier=3,     # Vector candidates = top_k * multiplier
    fts_top_k_multiplier=3,
)
# Merges vector search and full-text search rankings via RRF
```

***

## 6. Loading Configuration from YAML

YAML has the highest priority. Create a `config.yaml`:

```yaml
# config.yaml — production configuration
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

# Approach 1: Lake.from_yaml loads YAML and merges on top of defaults
lake = Lake.from_yaml("config.yaml", base_uri="./data")

# Approach 2: Build just the config object
config = ArrowLakeConfig.from_yaml("config.yaml")
print(config.storage.s3_endpoint)  # http://minio:9000
```

> **Note**: YAML loading uses deep-merge -- fields not specified retain their code defaults. YAML values override matching keys from `.env` and environment variables.

***

## 7. Example .env File

Create a `.env` file in the project root; pydantic-settings loads it automatically:

```bash
# .env — development environment
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

# Vector search / Full-text search / Embedding
ARROW_LAKE__VECTOR__METRIC=cosine
ARROW_LAKE__FTS__TOKENIZER_TYPE=jieba
ARROW_LAKE__EMBEDDING__BACKEND=local
ARROW_LAKE__EMBEDDING__MODEL=Qwen/Qwen3-Embedding-0.6B
```

***

## 8. Configuration Best Practices

1. **Development**: Use `.env` files for local configuration. Never commit `.env` files containing secrets to version control.
2. **Production**: Use YAML configuration files with sensitive values injected via environment variables.
3. **Container deployments**: Override key settings through `ARROW_LAKE__` environment variables without modifying config files.
4. **Chinese text**: Set `fts.tokenizer_type = "jieba"` for better Chinese tokenization.
5. **High-dimensional vectors**: `num_sub_vectors` must be a multiple of 8 and must not exceed the embedding dimension.
