# Compute Plane

> You are a **platform SRE** responsible for deploying, observing, scaling, and securing Arrow Lake infrastructure in staging and production.

Your system flows through this path:

```
Docker Compose (dev / staging)
  --> Helm Chart (Kubernetes production)
    --> Service Mesh (Ingress + NetworkPolicy)
      --> Observability (OTel + Prometheus + Alertmanager)
        --> Autoscaling (HPA + Ray cluster)
          --> GPU Management (vGPU / MIG partitioning)
```

## Core Tasks

### 🟢 Starter

| Task | Description |
|------|-------------|
| [Local Deployment](deploy/docker-compose.md) | Spin up Arrow Lake with Docker Compose using the 6 built-in profiles (api, minio, redis, ray-head, ray-worker, jupyter) |
| [Configuration Reference](../reference/config.md) | Understand environment variables, `.env` files, profile flags, and storage backend selection |
| [Health Checks](observe/health.md) | Verify service readiness with `/healthz` endpoints, Lance storage connectivity, and Redis ping |

### 🟡 Professional

| Task | Description |
|------|-------------|
| [Kubernetes + Helm](deploy/helm.md) | Deploy with the official Helm chart; configure HPA, PDB, Ingress, Secrets, and NetworkPolicy |
| [OpenTelemetry Tracing](observe/tracing.md) | Enable OTel SDK traces for API requests, ingestion pipelines, and RAG retrieval chains; export to Jaeger or Tempo |
| [Prometheus Metrics](observe/metrics.md) | Scrape built-in `prometheus_client` counters for request rates, ingestion throughput, query latency, and Ray task metrics |
| [Alertmanager Rules](observe/alerts.md) | Configure Alertmanager with severity-based routing; set thresholds for error rates, storage capacity, and GPU memory |

### 🔴 Enterprise

| Task | Description |
|------|-------------|
| [HPA & Ray Autoscaling](scale/hpa.md) | Configure horizontal pod autoscaler for API workers; manage Ray autoscaling for embedding and RAG workloads |
| [GPU Management](scale/gpu.md) | Schedule GPU workloads with NVIDIA device plugin; configure vGPU / MIG partitioning for embedding model serving |
| [Backup & Disaster Recovery](scale/backup.md) | Set up CronJob-based Lance dataset backup to S3/MinIO; test restoration procedures and validate data integrity |

## Next Steps

- **Loading data before deployment?** Follow the [Data Plane](../data-plane/README.md) to understand ingestion and storage.
- **Tuning RAG performance in production?** The [Knowledge Plane](../knowledge-plane/README.md) covers retrieval quality benchmarking.
- **Security hardening?** Read [Security & Auth](../concepts/security.md) for JWT authentication, rate limiting, and RBAC configuration.
