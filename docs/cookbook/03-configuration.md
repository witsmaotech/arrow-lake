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

# --- Core ---
config.storage    # StorageConfig        — Storage layer (local, MinIO, S3, GCS)
config.compute    # ComputeConfig        — Compute resources
config.http       # HttpConfig           — HTTP client settings
config.observability # ObservabilityConfig — Logging and tracing

# --- Search ---
config.vector     # VectorSearchConfig   — Vector search
config.fts        # FullTextSearchConfig — Full-text search
config.hybrid     # HybridSearchConfig   — Hybrid search (RRF fusion)
config.faceted    # FacetedSearchConfig  — Faceted search
config.ensemble   # EnsembleSearchConfig — Ensemble search

# --- AI / RAG ---
config.llm        # LLMConfig            — LLM provider
config.rag        # RAGConfig            — RAG pipeline
config.embedding  # EmbeddingConfig      — Embedding model
config.hugegraph  # HugeGraphConfig      — HugeGraph knowledge graph

# --- Media & Documents ---
config.media      # MediaConfig          — Media processing
config.decode     # DecodeConfig         — Image decode settings
config.document   # DocumentConfig       — PDF parsing and chunking
config.export     # ExportConfig         — Export settings

# --- Data ---
config.olap       # OlapConfig           — OLAP / DuckDB queries
config.daft       # DaftConfig           — Daft compute engine
config.quality    # QualityConfig        — Quality filtering

# --- Infrastructure ---
config.api        # ApiConfig            — API service
config.auth       # AuthConfig           — Authentication
config.rate_limit # RateLimitConfig      — Rate limiting
config.redis      # RedisConfig          — Redis distributed session
config.workflow   # WorkflowConfig       — Workflow orchestration
config.argo       # ArgoConfig           — Argo Workflows integration
config.autoscale  # AutoscaleConfig      — Auto-scaling
config.lifecycle  # LifecycleConfig      — Dataset lifecycle management

# --- Governance ---
config.gravitino  # GravitinoConfig      — Apache Gravitino metadata catalog
config.lineage    # LineageConfig        — Data lineage tracking
config.audit      # AuditConfig          — Audit logging
config.opentelemetry # OpenTelemetryConfig — OpenTelemetry tracing
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
| `s3_uploads_bucket` | `""`                  | v1.9.5 separate bucket for raw uploaded files (empty=reuse `s3_bucket`, isolating them from the Lance data plane) |             |
| `uploads_expiration_days` | `0`             | Auto-expire uploaded raw files after N days (0=disabled; once expired, re-parsing e.g. switching OCR backend is no longer possible) |             |
| `lance_cache_size` | `0`                     | Lance read cache in bytes (0=disabled). Increase in production to speed up repeated scans |             |

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

# DeepSeek
deepseek_cfg = LLMConfig(
    provider=LLMProviderType.DEEPSEEK,
    model="deepseek-chat",
    api_key="sk-...",
    api_base="https://api.deepseek.com/v1",
)
```

**LLMConfig field reference:**

| Field             | Type            | Default                                          | Description |
| ----------------- | --------------- | ------------------------------------------------ | ----------- |
| `provider`        | `"openai"`      | Backend: `openai`, `anthropic`, `vllm`, `ollama`, `deepseek` |             |
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
| `default_index_type` | `"IVF_PQ"` | Index type: `IVF_PQ`, `IVF_FLAT`, `IVF_HNSW_PQ`, `HNSW` |
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
6. **Redis for production**: Enable `redis.enabled = true` when running multiple API replicas (HPA / Kubernetes) so that DuckDB session semaphores are coordinated across pods.
7. **Redis TLS**: Set `redis.ssl = true` and provide `redis.password` when connecting to a managed Redis service (ElastiCache, Azure Cache, etc.).

***

## 9. Redis Distributed Session Configuration (RedisConfig)

When running Arrow Lake behind multiple API replicas, DuckDB session coordination and JWT token
blacklisting must be shared across processes. `RedisConfig` enables a Redis-backed distributed
semaphore that replaces the default `threading.Semaphore`.

When `enabled` is `False` (default), the system falls back to in-process synchronization.

```python
from arrow_lake.config import RedisConfig

# Local development (in-process semaphore, no Redis needed)
redis_cfg = RedisConfig()  # enabled=False by default

# Production with Redis
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

**RedisConfig field reference:**

| Field                        | Type     | Default                        | Description                                                    |
| ---------------------------- | -------- | ------------------------------ | -------------------------------------------------------------- |
| `enabled`                    | `bool`   | `False`                        | Enable Redis-backed distributed semaphore and JWT blacklist    |
| `url`                        | `str`    | `"redis://localhost:6379/0"`   | Redis connection URL                                           |
| `password`                   | `str`    | `""`                           | Redis authentication password                                  |
| `ssl`                        | `bool`   | `False`                        | Enable TLS for Redis connections                               |
| `ssl_cert_reqs`              | `str`    | `"required"`                   | SSL certificate verification mode when `ssl=True`              |
| `semaphore_key_prefix`       | `str`    | `"arrow_lake:semaphore:"`      | Redis key prefix for distributed semaphore counters            |
| `semaphore_ttl_seconds`      | `int`    | `300` (>= 1)                   | TTL for semaphore keys — auto-reclaims stale permits           |
| `redis_pool_size`            | `int`    | `10` (>= 1)                    | Connection pool size for the Redis client                      |
| `instance_registry_key`      | `str`    | `"arrow_lake:instances"`       | Redis key for multi-instance registry                          |
| `instance_heartbeat_ttl_seconds` | `int` | `30` (>= 5)                   | TTL for instance heartbeat keys                                |

### YAML Configuration

```yaml
# config.yaml — Redis distributed session coordination
redis:
  enabled: true
  url: "redis://redis:6379/0"
  password: "${REDIS_PASSWORD}"
  ssl: false
  semaphore_key_prefix: "arrow_lake:semaphore:"
  semaphore_ttl_seconds: 300
  redis_pool_size: 10
```

### Environment Variable Overrides

```bash
# Enable Redis and configure connection
ARROW_LAKE__REDIS__ENABLED=true
ARROW_LAKE__REDIS__URL=redis://redis:6379/0
ARROW_LAKE__REDIS__PASSWORD=your-redis-password
ARROW_LAKE__REDIS__SSL=false
ARROW_LAKE__REDIS__SEMAPHORE_KEY_PREFIX=arrow_lake:semaphore:
ARROW_LAKE__REDIS__SEMAPHORE_TTL_SECONDS=300
ARROW_LAKE__REDIS__REDIS_POOL_SIZE=10
```

### How It Works

The `RedisCountingSemaphore` uses Lua scripts for atomic acquire/release operations:
- **Acquire**: Atomically increments a Redis counter if below `max_permits`; sets TTL to auto-reclaim stale permits.
- **Release**: Atomically decrements the counter, guarding against underflow.
- **Fallback**: If Redis becomes unavailable, transparently falls back to `threading.Semaphore` and logs a warning.

***

## 10. System Database Configuration (SystemDBConfig)

> Introduced in v1.9.0: a unified relational **control-plane** store (libSQL / Turso) backing RBAC, identity, personal tokens, the catalog registry, task history, the lineage index, RAG sessions, and governance history. The **data plane** (Lance / DuckDB / HugeGraph / MinIO) is intentionally untouched.

When `enabled` is `False` (default), control-plane structs fall back to their pre-v1.9.0 in-memory / ephemeral-file behavior, so a deployment can opt in incrementally.

```python
from arrow_lake.config import SystemDBConfig

# Development: embedded (no server, no token)
dev_db = SystemDBConfig()  # default enabled=False, url="file:local.db"

# Production: self-hosted libSQL server (4 workers)
prod_db = SystemDBConfig(
    enabled=True,
    url="http://system-db:8080",
    auth_token="${SQLD_AUTH_TOKEN}",
    fail_mode="fail_close",          # RBAC/identity: refuse requests when store down
    serve_stale_on_error=False,      # secure fail-close (default)
    acl_cache_ttl_seconds=5.0,       # multi-worker eventual-consistency window
)
```

**Deployment mode is selected by `url`:**

| `url`                   | Mode                | Notes                                 |
| ----------------------- | ------------------- | ------------------------------------- |
| `file:local.db`         | Embedded (default)  | Dev, no server, no token              |
| `http://system-db:8080` | Self-hosted libSQL  | Production, 4 workers                 |
| `:memory:`              | In-memory           | Unit tests                            |

**Key SystemDBConfig fields:**

| Field                      | Default             | Description |
| -------------------------- | ------------------- | ----------- |
| `enabled`                  | `False`             | Enable the control-plane DB (off=degrade to in-memory/ephemeral files) |
| `url`                      | `"file:local.db"`   | libSQL connection URL (selects deployment mode) |
| `auth_token`               | `""`                | Auth token for a remote server (empty for embedded) |
| `fail_mode`                | `"fail_close"`      | `"fail_close"` (RBAC/identity, deny on outage) or `"fail_soft"` (catalog/tasks/rag, log + degrade) |
| `serve_stale_on_error`     | `False`             | **⚠️ SECURITY: FAIL-OPEN.** `True`=serve last-cached decision when store unreachable (may honor a permission revoked during the outage). Default `False`=secure fail-close. Enable only when you explicitly accept the tradeoff; prefer sqld HA for proper availability |
| `acl_cache_ttl_seconds`    | `5.0`               | Per-worker short-TTL ACL cache (multi-worker eventual-consistency window) |

***

## 11. RAG Pipeline Configuration (RAGConfig)

Controls RAG retrieval strategy, reranking, the two-stage LLM split, and verification. The default retrieval strategy is **hybrid** (vector + full-text RRF fusion).

```python
from arrow_lake.config import RAGConfig, LLMConfig

rag_cfg = RAGConfig(
    enabled=True,
    default_retrieval_strategy="hybrid",   # vector | fts | hybrid
    default_top_k=10,
    # Reranker (default ollama Qwen3-Reranker)
    reranker="ollama",
    reranker_model="dengcao/Qwen3-Reranker-0.6B:F16",
    reranker_device="auto",                # cpu | cuda | auto
    # Two-stage independent LLMs (None falls back to global llm)
    extract_llm=LLMConfig(provider="openai", model="qwen-turbo", api_key="sk-..."),
    qa_llm=LLMConfig(provider="openai", model="qwen-plus", api_key="sk-..."),
    # Optional: faithfulness verification (off by default, opt-in)
    enable_verification=False,
)
```

**Key RAGConfig fields:**

| Field                        | Default                                 | Description |
| ---------------------------- | --------------------------------------- | ----------- |
| `default_retrieval_strategy` | `"hybrid"`                              | Retrieval strategy: `vector`, `fts`, `hybrid` |
| `default_top_k`              | `10`                                    | Default number of results to retrieve |
| `reranker`                   | `"ollama"`                              | Reranker type (`ollama` / `cross_encoder` / `llm` / `noop`) |
| `reranker_model`             | `"dengcao/Qwen3-Reranker-0.6B:F16"`     | Rerank model |
| `reranker_device`            | `"auto"`                                | Rerank device: `cpu` / `cuda` / `auto` |
| `extract_llm`                | `None`                                  | Extraction/rerank stage LLM (`None`=fall back to global `llm`) |
| `qa_llm`                     | `None`                                  | Generation stage LLM (`None`=fall back to global `llm`; a flagship model significantly improves answer quality) |
| `enable_verification`        | `False`                                 | v1.9.6 lightweight faithfulness verification (off by default) |
| `enable_citations`           | `True`                                  | Whether to track and return citations |
