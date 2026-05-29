# Compute Plane

> You are a **platform SRE** responsible for deploying, observing, scaling, and securing Arrow Lake infrastructure in staging and production.

## Deployment Flow

```
Docker Compose (dev / staging)
  --> Helm Chart (Kubernetes production)
    --> Service Mesh (Ingress + NetworkPolicy)
      --> Observability (OTel + Prometheus + Alertmanager)
        --> Autoscaling (HPA + Ray cluster)
          --> GPU Management (vGPU / MIG partitioning)
```

---

## Local Deployment (Docker Compose)

### Quick Start

```bash
# Minimal stack: API + MinIO + Redis
make up
# or: docker compose --profile core up -d

# Development (adds Jupyter, source mount, hot-reload)
make dev

# Full stack (monitoring + Ray + KG)
make full

# GPU support
make gpu

# Knowledge Graph only
make kg
```

### Profiles

| Profile | Services | Use Case |
|---------|----------|----------|
| `core` | api, minio, redis, proxy-forward | Minimal evaluation |
| `dev` | core + ray-head, ray-worker, jupyter | Local development |
| `monitoring` | core + ray + prometheus, grafana, jaeger | Observability |
| `gpu` | core + ray (GPU Dockerfile, NVIDIA device) | GPU inference |
| `kg` | hugegraph | Knowledge Graph |
| `ocr` | turbo-ocr | OCR processing |

### Environment Variables

```bash
# Storage
ARROW_LAKE__STORAGE__BACKEND=local          # local | minio | s3 | gcs
ARROW_LAKE__STORAGE__BASE_URI=./data/lake
ARROW_LAKE__STORAGE__S3_ENDPOINT=http://minio:9000
ARROW_LAKE__STORAGE__S3_ACCESS_KEY=minioadmin
ARROW_LAKE__STORAGE__S3_SECRET_KEY=minioadmin
ARROW_LAKE__STORAGE__S3_BUCKET=arrow-lake

# API
ARROW_LAKE__API__PORT=8000
ARROW_LAKE__API__API_KEY=your-32-char-key-here
ARROW_LAKE__API__CORS_ORIGINS=["http://localhost:3000"]

# Auth
ARROW_LAKE__AUTH__AUTH_MODE=api_key          # api_key | jwt | both
ARROW_LAKE__AUTH__JWT_SECRET_KEY=your-jwt-secret-min-32-chars

# Compute
ARROW_LAKE__COMPUTE__GPU_ENABLED=false
ARROW_LAKE__COMPUTE__RAY_ADDRESS=auto
ARROW_LAKE__COMPUTE__NUM_WORKERS=2

# Redis
ARROW_LAKE__REDIS__ENABLED=true
ARROW_LAKE__REDIS__URL=redis://redis:6379/0
ARROW_LAKE__REDIS__PASSWORD=

# OpenTelemetry
ARROW_LAKE__OPENTELEMETRY__ENABLED=false
ARROW_LAKE__OPENTELEMETRY__OTEL_ENDPOINT=http://jaeger:4317
ARROW_LAKE__OPENTELEMETRY__TRACE_SAMPLE_RATE=1.0

# Maintenance (auto background compaction + cleanup)
ARROW_LAKE__STORAGE__MAINTENANCE_ENABLED=true
ARROW_LAKE__STORAGE__MAINTENANCE_INTERVAL_SECONDS=3600
ARROW_LAKE__STORAGE__COMPACTION_FRAGMENT_THRESHOLD=10
ARROW_LAKE__STORAGE__VERSION_RETENTION_DAYS=7
```

### 4-Layer Config Override

```
Code defaults < .env file < Environment variables < YAML config
```

Env var prefix: `ARROW_LAKE__` with `__` as nested delimiter.
Example: `ARROW_LAKE__STORAGE__BACKEND=minio` -> `config.storage.backend`

### Health Checks

```bash
# Liveness (always returns 200 if process running)
curl http://localhost:8000/health/live

# Readiness (checks storage, Gravitino, Ray, Redis; returns 503 if degraded)
curl http://localhost:8000/health/ready
# Response: {"status": "ok"|"degraded", "duckdb_pool": {...}, ...}

# Backward-compatible health
curl http://localhost:8000/health
```

---

## Production Deployment

### Docker Compose Production

`docker-compose.prod.yml` is a standalone compose file with all services always active:

```bash
make prod
```

**Services**: API (4 workers, scalable via `API_REPLICAS`), nginx (TLS termination), MinIO, Redis, Ray (head + workers + GPU workers), Prometheus (30d retention), Alertmanager, Grafana, Jaeger, Loki + Promtail, HugeGraph, Gravitino, Lance REST Catalog.

### Kubernetes + Helm

```bash
# Deploy with Helm
helm install arrow-lake ./deploy/helm/ \
  --set api.replicas=3 \
  --set api.apiKey=$API_KEY \
  --set storage.backend=s3 \
  --set redis.enabled=true \
  --set monitoring.enabled=true
```

Key configurations:

| Resource | Setting | Purpose |
|----------|---------|---------|
| HPA | `minReplicas: 2`, `maxReplicas: 10`, CPU target 70% | API auto-scaling |
| PDB | `minAvailable: 1` | Availability during disruptions |
| NetworkPolicy | Default-deny, explicit allowlists | Network isolation |
| Secrets | Kubernetes Secrets (base64) | API keys, JWT secrets, S3 credentials |
| Ingress | TLSv1.2 + TLSv1.3, ECDHE cipher suite | TLS termination |

### Ingress Allowlist

| Source | Port | Purpose |
|--------|------|---------|
| Ray metrics | 8080 | Scraping |
| Prometheus | 8000/metrics | Scraping |
| MinIO / S3 | 9000 | Object storage |
| Redis | 6379 | Session/JWT |
| HugeGraph | 8080 | Graph DB |
| HTTPS | 443 | External access |
| DNS | 53 | Service discovery |

---

## Observability

### Prometheus Metrics

All metrics use the `arrow_lake_` prefix. Scrape endpoint: `/metrics` (port 8000).

**Key metrics to monitor:**

| Metric | Type | Labels | Use Case |
|--------|------|--------|----------|
| `arrow_lake_http_request_duration_seconds` | Histogram | method, path, status_code | API latency P50/P95/P99 |
| `arrow_lake_ingestion_rows_total` | Counter | source | Ingestion throughput |
| `arrow_lake_query_latency_seconds` | Histogram | query_type | OLAP query performance |
| `arrow_lake_duckdb_pool_active_sessions` | Gauge | — | Connection pool utilization |
| `arrow_lake_processing_embeddings_total` | Counter | model | Embedding throughput |
| `arrow_lake_quality_reject_total` | Counter | dataset, reason | Quality gate rejection rate |
| `arrow_lake_maintenance_compaction_runs_total` | Counter | dataset | Compaction frequency |

**Prometheus scrape config:**

```yaml
scrape_configs:
  - job_name: 'arrow-lake'
    metrics_path: '/metrics'
    static_configs:
      - targets: ['api:8000']
```

### OpenTelemetry Tracing

Enable via environment:

```bash
ARROW_LAKE__OPENTELEMETRY__ENABLED=true
ARROW_LAKE__OPENTELEMETRY__OTEL_ENDPOINT=http://jaeger:4317
ARROW_LAKE__OPENTELEMETRY__TRACE_SAMPLE_RATE=1.0
```

**OTel span names in code:**

| Span | Module | What It Traces |
|------|--------|---------------|
| `vector_search` | `_lake_search.py` | Vector similarity search |
| `text_search` | `_lake_search.py` | Full-text search |
| `hybrid_search` | `_lake_search.py` | Hybrid RRF fusion |
| `create_dataset` | `_lake_ingest.py` | Dataset creation |
| `upsert` | `_lake_ingest.py` | Upsert operations |
| `delete_rows` | `_lake_ingest.py` | Row deletion |
| `update_rows` | `_lake_ingest.py` | Row updates |

FastAPI auto-instrumented via `FastAPIInstrumentor`. Jaeger UI: `http://localhost:16686`.

### Grafana Dashboards

Pre-provisioned dashboards for:
- SLO overview (P50/P95/P99 latency by endpoint)
- Ingestion throughput and error rates
- DuckDB connection pool health
- Quality gate rejection distribution
- Maintenance compaction trends

### Alertmanager

Production config includes severity-based routing:

- **Critical**: service down, storage unreachable -> immediate page
- **Warning**: high latency, pool exhaustion -> Slack notification
- **Info**: compaction completed, schema migration -> log only

See [SLO & Dependency Criticality](slo-and-criticality.md) for threshold definitions.

---

## Maintenance & Operations

### CLI Commands

```bash
# Check maintenance status
arrow-lake maintenance status

# Run single maintenance cycle (compaction + version cleanup)
arrow-lake maintenance run
arrow-lake maintenance run --json    # JSON output

# Backup
arrow-lake backup create --datasets my_dataset
arrow-lake backup create              # all datasets
arrow-lake backup list
arrow-lake backup restore BACKUP_ID --datasets my_dataset
arrow-lake backup delete BACKUP_ID
```

### Automated Maintenance

When `ARROW_LAKE__STORAGE__MAINTENANCE_ENABLED=true`, a background `MaintenanceScheduler` runs:
1. **Compaction**: merge small fragments when `fragment_count > threshold` (default 10)
2. **Version cleanup**: remove versions older than `retention_days` (default 7)
3. **DuckDB pool health**: evict idle connections, track zombies

### Backup

Production includes a `minio-backup` one-shot service (`deploy/scripts/backup-minio.sh`) with configurable `BACKUP_RETAIN_DAYS` (default 7).

---

## SLO Targets

| Metric | SLO | Alert Threshold |
|--------|-----|-----------------|
| RAG query P95 | < 2.0s | > 2.5s for 5 min |
| Vector search P95 | < 300ms | > 500ms for 5 min |
| Full-text search P95 | < 200ms | > 400ms for 5 min |
| Ingestion throughput | > 100 rows/s | < 50 rows/s for 10 min |
| API availability | > 99.5% | 2 consecutive health failures |
| Index build success | > 99% | Failure rate > 5% |

Full SLO definitions, dependency criticality tiers, and incident response playbook: [SLO & Dependency Criticality](slo-and-criticality.md).

---

## Next Steps

- **Loading data before deployment?** -> [Data Plane](../data-plane/README.md) for ingestion and storage.
- **Tuning RAG in production?** -> [Knowledge Plane](../knowledge-plane/README.md) for retrieval quality benchmarking.
- **Security hardening?** -> [Security](../concepts/security.md) for JWT, RBAC, rate limiting, and STRIDE threat model.
- **Configuration reference?** -> [Configuration](../reference/configuration.md) for all 28 config sections.
