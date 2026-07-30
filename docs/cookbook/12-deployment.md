# Deployment & Operations

> A comprehensive operations guide covering Docker Compose full-stack deployment, Prometheus
> monitoring, backup and recovery, security hardening, and performance tuning.

***

## 1. Docker Compose Full-Stack Deployment

### 1.1 Recommended Production Stack: prod_minimal.yml (v1.9.x)

The **primary production path** is `deploy/docker-compose.prod_minimal.yml` (16 service blocks: api / system-db / minio / redis / hg-server / hg-hubble / gravitino / lance-rest / nginx / jaeger, etc., with init jobs and jaeger gated by profiles). It supersedes the legacy `docker-compose.yml --profile core` and is the only deployment stack operationally validated for v1.9.x.

```bash
# Bring up the entire production stack (reads deploy/.env)
make prod-minimal

# Build only the api image (avoids the BuildKit shared-tag quirk)
make prod-minimal-build

# Start a single service directly with docker compose
docker compose --project-directory deploy -p arrow-lake \
  -f deploy/docker-compose.prod_minimal.yml up -d api
```

Host-side ports (bound to `127.0.0.1` only; remote access requires a reverse proxy):

| Service   | Host Port → Container      | Credentials / Notes                  |
| --------- | -------------------------- | ------------------------------------ |
| api       | `127.0.0.1:8000` → 8000    | API key in `deploy/.env`             |
| minio     | `127.0.0.1:9000` → 9000    | `minioadmin` / `minioadmin` (default)|
| hg-server | `127.0.0.1:8089` → 8080    | auth required: `admin` / `pa`        |
| redis     | `127.0.0.1:6380` → 6379    | password `:?` mandatory              |
| gravitino | `127.0.0.1:8090` → 8090    | —                                    |
| system-db | (internal) → 8080          | libSQL sqld, v1.9.0 control plane    |

> **Note**: Changing `arrow_lake/` Python source requires rebuilding the api image; changing bind-mounted files like `deploy/scripts/*.sh` only needs a container restart. For second-level hot-reload see §17.6 dev override.

### 1.2 Legacy Profile Stack (Reference)

`deploy/docker-compose.yml` still offers profile-based selective startup (Ray / Jupyter / GPU compute scenarios):

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
| **Masking HMAC (v1.9.6)** | `ARROW_LAKE__MASKING__HMAC_KEY` (env var) | Empty | Missing blocks startup; downgrade via `ALLOW_MISSING_KEY=1` (see §17.2) |
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

> **v1.9.0 RBAC depends on external system_db**: In the prod_minimal stack, control-plane state (RBAC / identity / personal_token / task history / etc.) is now backed by a standalone `system-db` (libSQL sqld) service, active when `SYSTEM_DB_ENABLED=true`. When the store is unreachable it is **fail-closed** (returns 401) — requests are not allowed through (unless you explicitly opt in via `SYSTEM_DB_SERVE_STALE_ON_ERROR=true`). See §17.1.

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
    metaflow_tags={"env": "prod", "team": "data"},
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

# Run anomaly detection on audit trail
anomalies = lake.audit_analyze()
for a in anomalies:
    print(f"{a['severity']}: {a['description']}")
```

| Parameter        | Type   | Description                                       |
| ---------------- | ------ | ------------------------------------------------- |
| `event_type`     | `str`  | Event type (e.g., `data_ingest`, `backup_create`) |
| `dataset_name`   | `str`  | Associated dataset                                |
| `actor`          | `str`  | Who performed the action (default: `"system"`)    |
| `lance_version`  | `int`  | Lance version number                              |
| `metaflow_run_id`| `str`  | Associated Metaflow run ID                        |
| `metaflow_tags`  | `dict` | Associated Metaflow tags                          |
| `payload`        | `dict` | Additional event data                             |

### Audit REST API

```bash
# Record an audit event
curl -X POST http://localhost:8000/api/v1/datasets/articles/audit \
  -H "X-API-Key: your-key" \
  -H "Content-Type: application/json" \
  -d '{"action": "data_ingest", "actor": "pipeline-user"}'

# Query audit entries
curl "http://localhost:8000/api/v1/datasets/articles/audit?start=2026-04-01T00:00:00Z" \
  -H "X-API-Key: your-key"

# Verify audit entry integrity
curl http://localhost:8000/api/v1/audit/{audit_id}/verify -H "X-API-Key: your-key"

# Run anomaly detection
curl -X POST http://localhost:8000/api/v1/audit/analyze -H "X-API-Key: your-key"
```

### Audit CLI

```bash
# Record an audit event
arrow-lake audit record --dataset articles --action data_ingest --actor pipeline-user

# Query audit log
arrow-lake audit query --dataset articles --start 2026-04-01 --end 2026-04-30

# Run anomaly analysis
arrow-lake audit analyze
```

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
stats = lake.compact_dataset("articles")
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

***

## 8. Redis Deployment

Redis is required for distributed session coordination when running multiple API replicas
(HPA, Kubernetes, or multi-instance Docker Compose). It provides:

- **Distributed semaphore** for DuckDB connection pool coordination across pods
- **JWT token blacklist** shared across API instances
- Automatic fallback to in-process `threading.Semaphore` when Redis is unavailable

### Docker Compose

Redis is included in the `core`, `dev`, `monitoring`, and `gpu` profiles:

```yaml
# deploy/docker-compose.yml (excerpt)
redis:
  image: redis:7.4
  container_name: arrow-lake-redis
  profiles: ["core", "dev", "monitoring", "gpu"]
  command: >
    redis-server
    --maxmemory ${REDIS_MAXMEMORY:-256mb}
    --maxmemory-policy allkeys-lru
    --appendonly yes
  volumes:
    - redis-data:/data
  healthcheck:
    test: ["CMD", "redis-cli", "ping"]
```

The API server connects to Redis automatically when `redis.enabled` is set in the environment:

```bash
ARROW_LAKE__REDIS__ENABLED=true
ARROW_LAKE__REDIS__URL=redis://redis:6379/0
ARROW_LAKE__REDIS__PASSWORD=${REDIS_PASSWORD:-}
```

> **v1.9.x prod_minimal requires a Redis password**: the prod_minimal stack uses the `${REDIS_PASSWORD:?REDIS_PASSWORD must be set}` syntax, so **the whole stack refuses to start if it is unset** (fail-fast, unlike the legacy empty-default behavior). You must set `REDIS_PASSWORD` explicitly in `deploy/.env`.

### Helm (Kubernetes)

Redis is configured via `values.yaml`:

```yaml
# deploy/helm/arrow-lake/values.yaml
redis:
  enabled: true
  url: "redis://redis:6379/0"
  password: ""  # Set via ARROW_LAKE__REDIS__PASSWORD or a Kubernetes secret
  ssl: false
```

For managed Redis services (AWS ElastiCache, Azure Cache for Redis, GCP Memorystore),
enable TLS and set the endpoint:

```yaml
redis:
  enabled: true
  url: "rediss://primary.xxxxxx.use1.cache.amazonaws.com:6379/0"
  password: "${REDIS_PASSWORD}"
  ssl: true
```

***

## 9. Horizontal Pod Autoscaler (HPA)

Arrow Lake ships with a Kubernetes HPA template that automatically scales API pods based on
CPU and memory utilization. **HPA requires Redis** for distributed semaphore coordination
across replicas.

### Enabling HPA

```yaml
# values.yaml — enable HPA
apiServer:
  replicaCount: 2
  autoscaling:
    enabled: true
    minReplicas: 2
    maxReplicas: 8
    targetCPUUtilizationPercentage: 70
    targetMemoryUtilizationPercentage: 80

redis:
  enabled: true  # REQUIRED for HPA
```

```bash
helm install arrow-lake deploy/helm/arrow-lake/ \
  --namespace arrow-lake --create-namespace \
  -f values.yaml
```

### Scaling Behavior

The HPA template (`deploy/helm/arrow-lake/templates/hpa.yaml`) configures:

| Parameter                     | Default | Description                                      |
| ----------------------------- | ------- | ------------------------------------------------ |
| `minReplicas`                 | `2`     | Minimum number of API pods                       |
| `maxReplicas`                 | `8`     | Maximum number of API pods                       |
| `targetCPUUtilizationPercentage` | `70` | Scale up when average CPU exceeds this threshold  |
| `targetMemoryUtilizationPercentage` | `80` | Scale up when average memory exceeds this threshold |

**Scale-down stabilization**: 300 seconds window to prevent thrashing.
**Scale-up policy**: Adds up to 100% or 2 pods every 60 seconds, whichever is greater.

> **Important**: Without Redis, all API replicas share in-process semaphores independently,
> which can lead to DuckDB connection pool exhaustion under load. Always enable Redis when
> using HPA.

***

## 10. CronJob Backup Automation

The Helm chart includes a CronJob template for automated daily backups via the backup REST API.

### Enabling Scheduled Backups

```yaml
# values.yaml — enable automated backups
backup:
  enabled: true
  schedule: "0 2 * * *"  # Daily at 02:00 UTC
  image:
    repository: curlimages/curl
    tag: "8.12.1"
  apiKeySecret:
    name: arrow-lake-api-key
    key: api-key
  successfulJobsHistoryLimit: 3
  failedJobsHistoryLimit: 1
```

### Creating the API Key Secret

```bash
kubectl create secret generic arrow-lake-api-key \
  --namespace arrow-lake \
  --from-literal=api-key='your-secret-api-key-here'
```

### How It Works

The CronJob runs a lightweight `curl` container that calls `POST /api/v1/backup/create` with
`dataset_names: null` (backs up all datasets). Key features:

- **Concurrency policy**: `Forbid` — prevents overlapping backup jobs
- **Retry**: `backoffLimit: 2` with `restartPolicy: OnFailure`
- **History**: Retains 3 successful and 1 failed job for debugging
- **Idempotency**: Each run creates a new timestamped backup; old backups must be pruned separately

### Verifying Backup Jobs

```bash
# Check CronJob status
kubectl get cronjob -n arrow-lake

# View recent backup job logs
kubectl logs -n arrow-lake job/arrow-lake-backup-$(date +%Y%m%d) -c backup

# List backups via API
curl -s http://localhost:8000/api/v1/backup/list -H "X-API-Key: your-key" | jq
```

***

## 11. Production Security Checklist

Beyond the general hardening in Section 5, multi-replica and Kubernetes deployments require
additional security controls.

### Transport Security (TLS)

```yaml
# values.yaml — TLS termination
apiServer:
  env:
    ARROW_LAKE__API__TLS_ENABLED: "true"
    ARROW_LAKE__API__TLS_CERT_PATH: "/certs/tls.crt"
    ARROW_LAKE__API__TLS_KEY_PATH: "/certs/tls.key"
```

For Kubernetes, use cert-manager with an Ingress resource:

```yaml
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  annotations:
    cert-manager.io/cluster-issuer: letsencrypt-prod
spec:
  tls:
    - hosts: [arrow-lake.example.com]
      secretName: arrow-lake-tls
```

### Content Security Policy (CSP)

```yaml
# config.yaml
api:
  security_headers_enabled: true
  content_security_policy: >
    default-src 'self';
    script-src 'self' 'nonce-{REQUEST_ID}';
    style-src 'self' 'unsafe-inline';
    img-src 'self' data: https:;
    frame-ancestors 'none';
    object-src 'none'
  frame_options: "DENY"
```

### Rate Limiting

```yaml
# config.yaml — production rate limits
rate_limit:
  enabled: true
  default_requests_per_minute: 120
  default_burst: 20
```

### Network Policy

The Helm chart ships with a default `NetworkPolicy` that restricts traffic:

```yaml
# values.yaml
networkPolicy:
  enabled: true
  allowExternal: false  # Only allow intra-namespace traffic
```

| Direction | Allowed Traffic                             | Port  |
| --------- | ------------------------------------------- | ----- |
| Ingress   | Ray pods, Prometheus                        | 8000  |
| Ingress   | External (if `allowExternal: true`)         | 8000  |
| Egress    | MinIO / S3 storage endpoint                 | 9000  |
| Egress    | DNS resolution                              | 53    |

For production, set `allowExternal: false` and use an Ingress controller or service mesh
to route external traffic.

### Security Summary

| Control                | Docker Compose                          | Kubernetes (Helm)                        |
| ---------------------- | --------------------------------------- | ---------------------------------------- |
| **TLS**                | Reverse proxy (nginx/traefik)           | cert-manager + Ingress                   |
| **CSP**                | `api.content_security_policy`           | `api.content_security_policy`            |
| **Rate Limiting**      | `rate_limit.enabled: true`              | `rate_limit.enabled: true`               |
| **Network Isolation**  | Docker bridge networks                  | `networkPolicy.enabled: true`            |
| **Redis Auth**         | `REDIS_PASSWORD` env var                | Kubernetes secret + `redis.password`     |
| **API Key**            | `ARROW_LAKE__API__API_KEY`              | Kubernetes secret                        |
| **JWT**                | `auth.auth_mode: jwt`                   | `auth.auth_mode: jwt`                    |
| **RBAC**               | 30+ endpoints with role checks          | 30+ endpoints with role checks           |
| **Audit HMAC**         | `audit.hmac_secret_key`                 | `audit.hmac_secret_key` via secret       |

***

## 12. v1.5.2 Security Hardening

Version 1.5.2 introduced critical security fixes across authentication, injection prevention, and
network binding. All deployments should upgrade to at least this version.

### Security Fixes

| Fix | Description | Impact |
| --- | ----------- | ------ |
| JWT empty key block | Server rejects startup if `jwt_secret_key` is empty or default | Prevents unauthenticated JWT token minting |
| Kerberos command injection | Shell metacharacters in Kerberos principal names are sanitized | Eliminates remote code execution via crafted principals |
| SQL injection parameterization | All user-supplied SQL parameters use parameterized queries | Prevents SQL injection in OLAP and lineage query endpoints |
| Redis default password removal | No default password in Docker Compose or Helm values | Forces explicit password configuration in production; prod_minimal uses `:?` syntax so a missing password fails at startup |
| 127.0.0.1 binding | Default API bind address changed to localhost only | Reduces attack surface; override with `api.host: 0.0.0.0` for remote access |
| SSRF protection | URL validation blocks private/internal network addresses | Prevents server-side request forgery via ingest URLs |
| Admin bypass to Role enum | Hardcoded admin string checks replaced with `Role` enum | Type-safe role checks prevent string comparison bypass |
| Refresh token rotation | Refresh tokens are single-use and rotated on each use | Stolen refresh tokens cannot be reused |

### Health Endpoints

```bash
# Liveness check (no dependency checks)
curl http://localhost:8000/health/live

# Readiness check (verifies storage, Redis if enabled)
curl http://localhost:8000/health/ready

# Full health report
curl http://localhost:8000/health -H "X-API-Key: your-key"

# Prometheus metrics
curl http://localhost:8000/metrics
```

***

## 13. Data Lineage

Arrow Lake provides built-in data lineage tracking for tracing dataset dependencies and
downstream impact analysis.

### Python API

```python
from arrow_lake import Lake

lake = Lake.from_yaml("configs/prod.yaml")

# Record a lineage event
lake.lineage_record_event(
    "articles_clean",
    "transform",
    source_datasets=["articles_raw"],
    transform_type="quality_filter",
    metadata={"rows_removed": 580},
)

# View lineage history for a dataset
history = lake.lineage_history("articles_clean")
for event in history:
    print(f"{event['operation']} at {event['timestamp']}")

# Query lineage events with SQL
import pyarrow as pa
result = lake.lineage_query(
    "SELECT * FROM lineage WHERE operation = 'transform'"
)

# Get full lineage graph (upstream + downstream)
graph = lake.lineage_graph("articles_clean", max_depth=10)
print(f"Nodes: {len(graph['nodes'])}, Edges: {len(graph['edges'])}")

# Analyze downstream impact of changing a dataset
impact = lake.lineage_impact("articles_raw")
for item in impact:
    print(f"Affected: {item['dataset']}, depth: {item['depth']}")
```

### Lineage REST API

```bash
# Record a lineage event
curl -X POST http://localhost:8000/api/v1/lineage/record \
  -H "X-API-Key: your-key" \
  -H "Content-Type: application/json" \
  -d '{"dataset_name": "articles_clean", "event_type": "transform", "source_datasets": ["articles_raw"]}'

# Get lineage history
curl http://localhost:8000/api/v1/lineage/history/articles_clean \
  -H "X-API-Key: your-key"

# Query lineage with SQL
curl -X POST http://localhost:8000/api/v1/lineage/query \
  -H "X-API-Key: your-key" \
  -H "Content-Type: application/json" \
  -d '{"sql": "SELECT * FROM lineage WHERE operation = '\''transform'\''"}'

# Get lineage graph
curl http://localhost:8000/api/v1/lineage/graph/articles_clean?max_depth=10 \
  -H "X-API-Key: your-key"

# Analyze downstream impact
curl -X POST http://localhost:8000/api/v1/lineage/impact \
  -H "X-API-Key: your-key" \
  -H "Content-Type: application/json" \
  -d '{"dataset_name": "articles_raw"}'
```

### Lineage CLI

```bash
# Record a lineage event
arrow-lake lineage record --dataset articles_clean --operation transform --sources articles_raw

# View lineage history
arrow-lake lineage history --dataset articles_clean

# Show lineage graph
arrow-lake lineage graph --dataset articles_clean --max-depth 10

# Analyze downstream impact
arrow-lake lineage impact --dataset articles_raw
```

***

## 14. Storage Lifecycle Management

Arrow Lake supports S3 storage tiering with lifecycle rules for automatic transition to
cost-effective storage classes (e.g., Glacier) and restoration on demand.

### Python API

```python
from arrow_lake import Lake

lake = Lake.from_yaml("configs/prod.yaml")

# Preview lifecycle rules without applying
rules = lake.lifecycle_rules(prefix="archive/")
print(rules)

# Apply lifecycle rules to a bucket prefix
result = lake.lifecycle_apply(prefix="archive/")
print(f"Applied: {result}")

# Check storage tier for objects
tiers = lake.lifecycle_status(prefix="archive/")
for item in tiers:
    print(f"{item['key']}: {item['storage_class']}")

# Restore a Glacier-tiered object for temporary access
lake.lifecycle_restore("archive/old_data.parquet", days=7)
```

### Lifecycle CLI

```bash
# Preview lifecycle rules
arrow-lake lifecycle rules --prefix archive/

# Apply lifecycle rules
arrow-lake lifecycle apply --prefix archive/

# Check storage tier status
arrow-lake lifecycle status --prefix archive/

# Restore a Glacier object
arrow-lake lifecycle restore --key archive/old_data.parquet --days 7
```

***

## 15. Backup via Lake API

In addition to the low-level `BackupManager` shown in Section 4, backups can be managed directly
through the `Lake` object:

```python
from arrow_lake import Lake

lake = Lake.from_yaml("configs/prod.yaml")

# Create a full backup (all datasets)
info = lake.backup_create()
print(f"Backup ID: {info.backup_id}")

# Create a partial backup
info = lake.backup_create(dataset_names=["articles", "photos"])

# Restore a backup
lake.backup_restore(
    info.backup_id,
    dataset_names=["articles"],
    overwrite=True,
)

# List all backups
for b in lake.backup_list():
    print(f"{b.backup_id} | {b.created_at} | {b.status}")

# Delete a backup
lake.backup_delete("20260101T000000zabc12345")
```

### Backup REST API

```bash
# Create a backup
curl -X POST http://localhost:8000/api/v1/backup/create \
  -H "Content-Type: application/json" -H "X-API-Key: your-key" \
  -d '{"dataset_names": ["articles"]}'

# List backups
curl http://localhost:8000/api/v1/backup/list -H "X-API-Key: your-key"

# Restore from a backup
curl -X POST http://localhost:8000/api/v1/backup/restore \
  -H "Content-Type: application/json" -H "X-API-Key: your-key" \
  -d '{"backup_id": "20260101T000000zabc12345", "overwrite": true}'

# Delete a backup
curl -X DELETE http://localhost:8000/api/v1/backup/20260101T000000zabc12345 \
  -H "X-API-Key: your-key"
```

### Backup CLI

```bash
# Create a backup
arrow-lake backup create --datasets articles,photos

# List backups
arrow-lake backup list

# Restore a backup
arrow-lake backup restore --id 20260101T000000zabc12345 --datasets articles

# Delete a backup
arrow-lake backup delete --id 20260101T000000zabc12345
```

***

## 16. Maintenance

```bash
# Run all maintenance tasks
arrow-lake maintenance

# Quality dedup via CLI
arrow-lake quality dedup --dataset articles --strategy exact
arrow-lake quality filter --dataset articles --mode all
```

***

## 17. v1.9.x Production Operations Notes (prod_minimal)

> This section consolidates operationally validated notes and common pitfalls from v1.9.0–v1.9.6,
> all in the prod_minimal stack context.

### 17.1 system_db Control Plane (v1.9.0 libSQL)

Starting in v1.9.0, control-plane state (RBAC / identity / personal_token / catalog / task history / RAG sessions / lineage index) moved to a standalone `system-db` service (libSQL / Turso sqld):

- prod_minimal provides the `system-db` service block; set `SYSTEM_DB_ENABLED=true` in `deploy/.env`.
- Migrations V001–V004 (RBAC, identity, personal_token, catalog, task_history, RAG sessions, lineage) run **automatically** inside the container.
- **Fail-closed**: when the store is unreachable, RBAC reads return 401 and requests are not allowed through (unless you opt in via `SYSTEM_DB_SERVE_STALE_ON_ERROR=true`, which may honor permissions revoked during the outage — use with caution).

### 17.2 Masking HMAC is Mandatory (v1.9.6 Security Baseline)

`ARROW_LAKE__MASKING__HMAC_KEY` is a **pure environment variable** (not a YAML config section) and gates hash-masking availability:

- **Missing it blocks startup** (fail-fast). prod_minimal already wires a placeholder `${ARROW_LAKE__MASKING__HMAC_KEY:-}`; you must set a strong key in `deploy/.env` (`openssl rand -hex 32`, 32+ bytes).
- Opt-in downgrade: `ARROW_LAKE__MASKING__ALLOW_MISSING_KEY=1` (dev/test only; hash masking is unusable but the service starts).
- After deploy you **must configure `HMAC_KEY`**, otherwise production will not start.

### 17.3 HugeGraph Operations (Common Pitfalls)

- **Per-dataset dynamic graphs**: each dataset gets its own graph `kg_{ds}` with a rocksdb backend at `/var/lib/hugegraph/graphs/{name}/` (persistent volume) inside the container. **Auth is mandatory** (required for dynamic graph creation); credentials `admin` / `pa` (env `HUGEGRAPH_PASSWORD`).
- **Traverser OOM (fixed)**: the HugeGraph start script used to inject `-Xmx32768m`, conflicting with compose `JAVA_OPTS`' `-Xmx2g`; the JVM takes the **last duplicate `-Xmx`** → effective heap was only 2g, causing OOM on dense-graph traversals. Fix: `HG_SERVER_MEMORY_LIMIT=12288M` + compose `JAVA_OPTS="-Xms2g -Xmx8g ..."`. Verify:
  ```bash
  docker exec arrow-lake-hg-server ps -ef | grep -oE "Xmx[0-9]+[mg]" | tail -1
  # should print Xmx8g (the last value wins)
  ```
- **Restart hg-server after any graph DROP/CLEAR**: the in-memory GraphManager does not refresh its schema cache, otherwise subsequent `ensure_schema` calls fail with 500 (not the benign 400). One-shot SOP:
  ```bash
  make kg-clear-graph DS=<dataset>   # clear: keeps the shell, storage is clean
  make kg-drop-graph  DS=<dataset>   # drop: deletes the registration, memory is clean
  ```
  Both automatically clear/drop + restart hg-server + wait for healthy.
- **Dynamic-graph gremlin traversal source is not globally bound**: `g.V()` / `{name}.traversal()` raise `MissingProperty`. Query vertices/edges via **REST** (`GET /graphs/{name}/graph/vertices?limit=N --compressed`) or the project endpoint `/api/v1/kg/stats` — do not use raw gremlin.

### 17.4 Gravitino 1.3.0 Upgrade Changes

- Server `apache/gravitino:${GRAVITINO_VERSION:-1.3.0}`; **the proxy must be neutralized**: the compose `gravitino` service explicitly sets `HTTP_PROXY/HTTPS_PROXY/http_proxy/https_proxy: ""` + `NO_PROXY/no_proxy: "*"`, otherwise the Docker daemon injects a dead proxy → s3a routes through it → the SigV4 signature is mangled → minio returns `403 Forbidden`.
- **`GRAVITINO_HOME=/opt/gravitino`** (layout change in 1.3.0; the data volume must mount `/opt/gravitino/data`); S3 properties use **`s3.*`** (`s3.endpoint` / `s3.access-key-id` / `s3.secret-access-key`, location `s3://`) — the legacy `fs.s3a.*` keys **do not take effect** on the 1.3.0 fileset catalog.
- Note: Gravitino is **optional governance** (RBAC / tags / lineage / fileset) and is **not on the data/query hot path** (dataset CRUD / query / KG / search / RAG do not depend on it). When it stalls you can disable it temporarily: `GRAVITINO_ENABLED=false`. `GravitinoSyncScheduler` has a circuit breaker (stops after 5 consecutive failures).

### 17.5 docling GPU Switching

- prod_minimal **mounts the GPU inline by default** (`deploy.resources.devices` + `count: ${GPU_COUNT:-1}`), not via `gpu.override.yml`; `DOCUMENT_OCR_BACKEND` defaults to `docling`. On hosts without a GPU set `GPU_COUNT=0` or switch back to `kreuzberg`.
- The default `API_CPU_LIMIT=1.0` is the docling CPU bottleneck (PDF rendering / layout / table post-processing is CPU-bound; 552 pages on a single core will time out/crash). On a multi-core host set `API_CPU_LIMIT=8.0` for roughly 5.4 min / 552 pages (close to bare-metal).
- To switch to GPU explicitly (overriding the inline config):
  ```bash
  docker compose --project-directory deploy -p arrow-lake \
    -f deploy/docker-compose.prod_minimal.yml \
    -f deploy/docker-compose.gpu.override.yml \
    up -d --force-recreate api
  ```

### 17.6 dev override Second-Level Hot-Reload

Changing `arrow_lake/` Python source **does not require rebuilding the image** — layer the dev override to bind-mount the source + run `uvicorn --reload`:

```bash
docker compose --project-directory deploy -p arrow-lake \
  -f deploy/docker-compose.prod_minimal.yml \
  -f deploy/docker-compose.dev.override.yml \
  up -d --force-recreate api
```

> **`--force-recreate` is mandatory**: otherwise the container keeps the prod command (uvicorn without `--reload`), so new code/endpoints do not take effect and 404. The dev override also bind-mounts `console/`, so editing frontend `*.html|css|js` takes effect on browser refresh. Rebuild only when finalizing an image.

### 17.7 RAG Hybrid 502 Fix

The default `lance_scan_mode=auto` routes the bridge through the **DuckDB native lance vector stream + IVF_PQ** → triggering a **Rust panic** (abort, uvicorn worker dies, browser "Failed to fetch" / curl 502). Standalone sync vector search works fine; only the DuckDB lance scanner has an abort bug on IVF_PQ's async vector stream. Fix: the prod_minimal api env sets:

```yaml
ARROW_LAKE__OLAP__LANCE_SCAN_MODE: "pyarrow_fallback"
```

This routes through the pyarrow-fallback sub-bridge (bypasses the DuckDB path; slightly slower but RAG works). Triage rhyme: RAG 502 + api logs show `Failed to create Lance search stream ... Index for column text_embedding` + `terminate called` → this bug.

### 17.8 Compose env Injection & export base_dir

- **Compose env injection**: the api service uses a compose `environment:` block with `${VAR:-default}` interpolation. **Bare values in `deploy/.env` are NOT injected into the container automatically** — only variables referenced via compose `${VAR}` take effect. To change backend config, edit the compose `environment:` block (or the dev override).
- **export base_dir**: the api container is `read_only: true`, so `/app/exports` is **ephemeral tmpfs** (lost on restart). For persistent exports set `ARROW_LAKE__EXPORT__BASE_DIR=/data/lake/exports` (a persistent writable volume; prod_minimal already configures this). Likewise any config that writes to a local path (e.g. `he_ka_base_dir`) must point at a mounted volume (e.g. `/data/lake/ka`).
