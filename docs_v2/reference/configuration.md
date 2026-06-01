# Configuration Reference

**Last Updated:** 2026-05-26

Arrow Lake uses **pydantic-settings** for configuration management. Config values load from three layers (lowest to highest priority):

1. **Defaults** — hardcoded in each config class
2. **`.env` file / Environment variables** — prefixed with `ARROW_LAKE__`, nested delimiter `__`
3. **YAML config file** — loaded via `ArrowLakeConfig.from_yaml("path/to/config.yaml")`

```python
from arrow_lake.config import ArrowLakeConfig

# From env vars + .env
config = ArrowLakeConfig()

# With YAML overlay (highest priority)
config = ArrowLakeConfig.from_yaml("configs/prod.yaml")
```

---

## Sections

| Section | Config Class | Source | Role |
|---------|-------------|--------|------|
| `storage` | `StorageConfig` | `config/storage.py` | Storage backend (local/S3), base URI |
| `redis` | `RedisConfig` | `config/redis.py` | Redis connection for session/JWT/semaphore |
| `api` | `ApiConfig` | `config/api.py` | REST API host/port/CORS |
| `auth` | `AuthConfig` | `config/api.py` | JWT secret, token expiry, API key |
| `rate_limit` | `RateLimitConfig` | `config/api.py` | Rate limiting (requests/minute) |
| `llm` | `LLMConfig` | `config/rag.py` | Default LLM provider, model, API key |
| `rag` | `RAGConfig` | `config/rag.py` | RAG pipeline (context window, top-k, temperature) |
| `hugegraph` | `HugeGraphConfig` | `config/rag.py` | HugeGraph connection for knowledge graph |
| `embedding` | `EmbeddingConfig` | `config/media.py` | Embedding model, dimensions, device |
| `vector` | `VectorSearchConfig` | `config/search.py` | Vector index type, metric, parameters |
| `fts` | `FullTextSearchConfig` | `config/search.py` | Full-text search (tokenizer, stemming) |
| `hybrid` | `HybridSearchConfig` | `config/search.py` | Hybrid search RRF constant |
| `faceted` | `FacetedSearchConfig` | `config/search.py` | Faceted search columns |
| `ensemble` | `EnsembleSearchConfig` | `config/search.py` | Ensemble search config |
| `olap` | `OlapConfig` | `config/olap.py` | DuckDB OLAP connection settings |
| `daft` | `DaftConfig` | `config/infra.py` | Daft execution engine settings |
| `http` | `HttpConfig` | `config/infra.py` | HTTP client timeout and retry |
| `compute` | `ComputeConfig` | `config/infra.py` | Ray cluster, GPU settings |
| `media` | `MediaConfig` | `config/media.py` | Media processing (image/video) |
| `decode` | `DecodeConfig` | `config/media.py` | Video decode settings |
| `quality` | `QualityConfig` | `config/media.py` | Quality gate thresholds |
| `document` | `DocumentConfig` | `config/document.py` | Document processing (chunking, OCR) |
| `export` | `ExportConfig` | `config/media.py` | Export format, compression |
| `workflow` | `WorkflowConfig` | `config/workflow.py` | Metaflow/Argo workflow settings |
| `argo` | `ArgoConfig` | `config/workflow.py` | Argo Workflows connection |
| `autoscale` | `AutoscaleConfig` | `config/workflow.py` | Autoscaling parameters |
| `lifecycle` | `LifecycleConfig` | `config/infra.py` | Data lifecycle (TTL, retention) |
| `lineage` | `LineageConfig` | `config/api.py` | Lineage tracking on/off |
| `audit` | `AuditConfig` | `config/api.py` | Audit trail on/off, HMAC key |
| `opentelemetry` | `OpenTelemetryConfig` | `config/api.py` | OTel exporter endpoint/protocol |
| `gravitino` | `GravitinoConfig` | `config/gravitino.py` | Gravitino metadata governance |
| `observability` | `ObservabilityConfig` | `config/infra.py` | Structured logging, metrics |

---

## Environment Variable Pattern

All settings are overridable via environment variables:

```bash
# Pattern: ARROW_LAKE__<SECTION>__<KEY>
ARROW_LAKE__STORAGE__BACKEND=s3
ARROW_LAKE__REDIS__URL=redis://redis:6379/0
ARROW_LAKE__AUTH__JWT_SECRET=your-secret-key
ARROW_LAKE__LLM__PROVIDER=openai
ARROW_LAKE__LLM__API_KEY=sk-...
```

---

## YAML Config Example

```yaml
storage:
  backend: s3
  base_uri: s3://my-bucket/lake

redis:
  url: redis://redis:6379/0

auth:
  jwt_secret: ${JWT_SECRET}
  token_expire_minutes: 60

llm:
  provider: openai
  model: gpt-4o
  api_key: ${OPENAI_API_KEY}

embedding:
  model_name: sentence-transformers/all-MiniLM-L6-v2
  device: cuda

vector:
  index_type: IVF_PQ
  metric: cosine
  num_partitions: 32

http:
  timeout_seconds: 30.0
  max_retries: 3

opentelemetry:
  enabled: true
  endpoint: http://otel-collector:4317
  protocol: grpc
```
