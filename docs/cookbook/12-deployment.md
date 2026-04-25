# Deployment & Operations

> A comprehensive operations guide covering Docker Compose full-stack deployment, Prometheus
> monitoring, backup and recovery, security hardening, and performance tuning.

***

## 1. Docker Compose Full-Stack Deployment

Arrow Lake uses Docker Compose profiles to selectively start services:

```bash
# Minimal deployment: API + MinIO
docker compose -f deploy/docker-compose.yml --profile core up -d

# Development environment: API + MinIO + Ray + Jupyter
docker compose -f deploy/docker-compose.yml --profile dev up -d

# View logs
docker compose -f deploy/docker-compose.yml logs -f api

# Stop services
docker compose -f deploy/docker-compose.yml --profile dev down
```

| Profile      | Included Services                          | Purpose                       |
| ------------ | ------------------------------------------ | ----------------------------- |
| `core`       | api, minio, minio-init                     | Minimal production deployment |
| `dev`        | core + ray-head, ray-worker, jupyter       | Local development             |
| `compute`    | ray-head, ray-worker                       | Distributed compute           |
| `gpu`        | GPU-enabled ray-head, ray-worker (overlay) | GPU-accelerated inference     |
| `monitoring` | prometheus, grafana, jaeger                | Observability                 |
| `ocr`        | turbo-ocr                                  | OCR service                   |

Key environment variables (passed in via `.env`):

```bash
MINIO_ROOT_USER=admin
MINIO_ROOT_PASSWORD=changeme
ARROW_LAKE__STORAGE__BACKEND=minio
ARROW_LAKE__STORAGE__S3_ENDPOINT=http://minio:9000
ARROW_LAKE__API__ENABLED=true
ARROW_LAKE__API__PORT=8000
ARROW_LAKE__OBSERVABILITY__METRICS_ENABLED=true
```

***

## 2. OCR Service

TurboOCR is deployed via a separate overlay and requires GPU (CUDA) support:

```bash
docker compose -f deploy/docker-compose.yml \
              -f deploy/docker-compose.ocr.yml \
              --profile ocr up -d

# Verify health
curl -sf http://localhost:8002/health
```

Key configuration in `deploy/docker-compose.ocr.yml`:

```yaml
turbo-ocr:
  image: deepdoectection/turbo-ocr:latest
  environment:
    - OCR_MAX_PAGES=100
    - OCR_MAX_FILE_SIZE_MB=50
    - WORKERS=2
```

Security note: TurboOCR runs on its own `ocr-internal` bridge network. For production,
remove the `ports` mapping so the service is only accessible internally.

***

## 3. Prometheus Monitoring

```bash
docker compose -f deploy/docker-compose.yml \
              -f deploy/docker-compose.monitoring.yml \
              --profile monitoring up -d
```

The Prometheus configuration (`deploy/monitoring/prometheus/prometheus.yml`) scrapes three targets:
`arrow-lake-ray-head:8000/metrics`, `:8265/metrics` (Ray), and
`arrow-lake-minio:9000/minio/v2/metrics/cluster`.

### Core Metrics (prefixed with `arrow_lake_`)

**DuckDB Connection Pool:**

| Metric                                  | Type    | Description                          |
| --------------------------------------- | ------- | ------------------------------------ |
| `duckdb_pool_active_sessions`           | Gauge   | Current active connections           |
| `duckdb_pool_queued_requests`           | Gauge   | Requests waiting for a connection    |
| `duckdb_pool_total_queries`             | Counter | Total queries executed               |
| `duckdb_pool_total_errors`              | Counter | Total query errors                   |
| `duckdb_pool_total_timeouts`            | Counter | Connection acquisition timeouts      |
| `duckdb_pool_slow_queries`              | Counter | Slow queries exceeding the threshold |
| `duckdb_pool_evicted_connections_total` | Counter | Idle / zombie connections reclaimed  |

**HTTP Requests:**

| Metric                          | Type      | Labels                     | Description               |
| ------------------------------- | --------- | -------------------------- | ------------------------- |
| `http_request_duration_seconds` | Histogram | method, path, status\_code | Request latency           |
| `auth_requests_total`           | Counter   | auth\_method, status       | Authentication statistics |
| `rate_limit_rejected_total`     | Counter   | endpoint, path             | Rate limit rejections     |

**Data Ingestion:**

| Metric                             | Type    | Labels              | Description               |
| ---------------------------------- | ------- | ------------------- | ------------------------- |
| `ingestion_rows_total`             | Counter | source              | Total rows ingested       |
| `ingestion_bytes_total`            | Counter | source              | Total bytes ingested      |
| `ingestion_errors_total`           | Counter | source, error\_type | Ingestion error count     |
| `processing_quality_rejects_total` | Counter | filter\_name        | Quality filter rejections |

Grafana: `http://localhost:3000` (admin / admin)
Jaeger: `http://localhost:16686` (OTLP endpoint on `:4317`)

***

## 4. Backup & Recovery

```python
from arrow_lake.ops.backup import BackupManager
from arrow_lake.config import StorageConfig

config = StorageConfig(
    backend="minio",
    s3_endpoint="http://localhost:9000",
    s3_bucket="arrow-lake",
)

mgr = BackupManager(
    storage_config=config,
    lance_base_uri="./data",
    backup_bucket="arrow-lake-backups",
)

# Create a backup
info = mgr.create_backup(
    dataset_names=["articles", "photos"],
    blob_prefixes=["thumbnails/"],
)
print(f"Backup ID: {info.backup_id}, Status: {info.status}")

# List all backups
for b in mgr.list_backups():
    print(f"{b.backup_id} | {b.created_at} | {b.status}")

# SHA-256 integrity verification
ok = mgr.verify_backup(info.backup_id)

# Restore from a backup
mgr.restore_backup(info.backup_id, dataset_names=["articles"], overwrite=True)

# Delete old backups
mgr.delete_backup("20260101T000000zabc12345")
```

Via the REST API:

```bash
# Create a backup
curl -X POST http://localhost:8000/api/v1/backup/create \
  -H "Content-Type: application/json" -H "X-API-Key: your-key" \
  -d '{"dataset_names": ["articles"]}'

# Restore from a backup
curl -X POST http://localhost:8000/api/v1/backup/restore \
  -H "Content-Type: application/json" -H "X-API-Key: your-key" \
  -d '{"backup_id": "20260101T000000zabc12345", "overwrite": true}'
```

***

## 5. Security Hardening Checklist

| Item                  | Configuration                  | Default          | Production Recommendation           |
| --------------------- | ------------------------------ | ---------------- | ----------------------------------- |
| **API Key Auth**      | `ARROW_LAKE__API__API_KEY`     | Empty (disabled) | Must set a strong random string     |
| **JWT Auth**          | `auth.auth_mode`               | `api_key`        | Switch to `jwt` or `both`           |
| **JWT Secret**        | `auth.jwt_secret_key`          | Empty            | 32+ character random key            |
| **CORS Restriction**  | `api.cors_origins`             | `[]` (allow all) | Explicitly list allowed domains     |
| **Security Headers**  | `api.security_headers_enabled` | `true`           | Keep enabled                        |
| **CSP**               | `api.content_security_policy`  | Empty            | Set an appropriate CSP              |
| **X-Frame-Options**   | `api.frame_options`            | `DENY`           | Keep as `DENY`                      |
| **Request Body Size** | `api.max_request_size_bytes`   | `100MB`          | Tighten as needed (e.g., 10MB)      |
| **Request Timeout**   | `api.request_timeout_seconds`  | `300s`           | Shorten as needed                   |
| **Rate Limiting**     | `rate_limit.enabled`           | `true`           | Keep enabled                        |
| **HMAC Audit**        | `audit.hmac_secret_key`        | Empty            | Must set for production             |
| **API Key Rotation**  | `api.api_key_rotation_days`    | `90`             | Rotate every 30-90 days             |
| **SSRF Protection**   | Built-in URL validation        | Enabled          | Blocks access to internal addresses |
| **Input Validation**  | `schema_validation`            | `lenient`        | Consider switching to `strict`      |

Production configuration example:

```yaml
api:
  api_key: "${ARROW_LAKE_API_KEY}"
  cors_origins: ["https://app.example.com"]
  max_request_size_bytes: 10485760    # 10 MB
  security_headers_enabled: true
  frame_options: "DENY"

auth:
  auth_mode: jwt
  jwt_secret_key: "${JWT_SECRET_KEY}"
  jwt_access_token_minutes: 30
  jwt_refresh_token_days: 7

rate_limit:
  enabled: true
  default_requests_per_minute: 120

audit:
  enabled: true
  hmac_secret_key: "${AUDIT_HMAC_KEY}"
```

***

## 6. Logging & Audit

```python
from arrow_lake import Lake

lake = Lake.from_yaml("configs/prod.yaml")

# Record an audit event
audit_id = lake.audit_record(
    event_type="data_ingest",
    dataset_name="articles",
    actor="pipeline-user",
    lance_version=42,
    metaflow_run_id="mf-20260424-001",
    payload={"rows": 1000, "source": "s3://raw/"},
)

# HMAC integrity verification
lake.audit_verify(audit_id)   # True / False

# Query audit logs
entries = lake.audit_query(
    dataset_name="articles",
    start="2026-04-01T00:00:00Z",
    end="2026-04-30T23:59:59Z",
    event_type="data_ingest",
)

# Export a dataset's audit records
export = lake.audit_export("articles")
```

| Parameter       | Type   | Description                                       |
| --------------- | ------ | ------------------------------------------------- |
| `event_type`    | `str`  | Event type (e.g., `data_ingest`, `backup_create`) |
| `dataset_name`  | `str`  | Associated dataset                                |
| `actor`         | `str`  | Who performed the action (default: `"system"`)    |
| `lance_version` | `int`  | Lance version number                              |
| `payload`       | `dict` | Additional event data                             |

***

## 7. Performance Tuning

### SessionManager Connection Pool

```python
from arrow_lake import Lake
lake = Lake.from_yaml("configs/dev.yaml")
sm = lake.get_session_manager()

stats = sm.get_stats()
print(f"Active: {stats.active_sessions}, Idle: {stats.idle_connections}")
print(f"Queued: {stats.queued_requests}, Queries: {stats.total_queries}")
```

OLAP configuration (`OlapConfig`):

```yaml
olap:
  max_result_rows: 100000
  enable_predicate_pushdown: true
  enable_join: true
```

### Vector Search nprobes Tuning

The `nprobes` parameter controls how many IVF index partitions are probed during vector search.
Higher values improve recall at the cost of speed:

| nprobes      | Recall | Speed   | Use Case                                     |
| ------------ | ------ | ------- | -------------------------------------------- |
| 10           | Low    | Fastest | Coarse filtering / candidate recall          |
| 20 (default) | Medium | Fast    | General-purpose search                       |
| 64-128       | High   | Slow    | Precision ranking / recall-sensitive queries |

```yaml
vector:
  nprobes: 20
  num_partitions: 256
  num_sub_vectors: 24
```

### Dataset Compaction & Helm Deployment

```python
# Merge fragmented files
stats = lake._get_storage().compact("articles")
print(f"Fragments: {stats.fragments_before} -> {stats.fragments_after}")
```

Kubernetes deployment:

```bash
helm install arrow-lake deploy/helm/arrow-lake/ \
  --namespace arrow-lake --create-namespace \
  -f deploy/helm/arrow-lake/values.yaml
```

```python
# Graceful shutdown
lake.shutdown()
```
