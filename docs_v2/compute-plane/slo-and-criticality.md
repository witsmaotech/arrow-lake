# SLO Definitions & Dependency Criticality

**Version:** v1.5.2 | **Last Updated:** 2026-05-26

---

## Service Level Objectives (SLOs)

| Metric | SLO Target | Measurement | Alert Threshold |
|--------|-----------|-------------|-----------------|
| RAG query P95 latency | < 2.0s | OTel trace `rag_query` | > 2.5s for 5 min |
| RAG query P99 latency | < 5.0s | OTel trace `rag_query` | > 6.0s for 5 min |
| Vector search P95 latency | < 300ms | OTel trace `vector_search` | > 500ms for 5 min |
| Vector search P99 latency | < 500ms | OTel trace `vector_search` | > 800ms for 5 min |
| Full-text search P95 latency | < 200ms | OTel trace `text_search` | > 400ms for 5 min |
| Data ingestion throughput | > 100 rows/s | Prometheus `arrow_lake_ingestion_rows_total` | < 50 rows/s for 10 min |
| REST API availability | > 99.5% | Uptime probe `/health/live` | 2 consecutive failures |
| Index build success rate | > 99% | Prometheus `arrow_lake_processing_embeddings_total` | Failure rate > 5% |
| Dataset read success rate | > 99.9% | OTel span errors | Error rate > 0.5% |

### Measurement Tools

- **OTel Traces**: Distributed tracing for request-level latency breakdown (retrieval → reranking → generation)
- **Prometheus Metrics**: Counter/gauge for throughput, error rates, cache hit rates
- **Grafana Dashboards**: Pre-built SLO dashboard (P50/P95/P99), latency breakdown by stage
- **Alertmanager**: Multi-channel alerts (email, Slack, webhook)

---

## Dependency Criticality Tiers

### Tier 1 — Critical (system stops without it)

| Dependency | Role | Failure Impact | Degradation Strategy |
|-----------|------|---------------|---------------------|
| **LanceDB** | Vector + FTS storage + data format | No data read/write. Full outage. | None. Must be highly available. |
| **Redis** | Session, JWT blacklist, distributed semaphore | Auth fails, distributed locks unavailable | JWT stateless fallback + in-process semaphore |

### Tier 2 — High (major feature unavailable)

| Dependency | Role | Failure Impact | Degradation Strategy |
|-----------|------|---------------|---------------------|
| **DuckDB** | OLAP analytics + metadata catalog | SQL queries fail. Catalog metadata stale. | Read cache for recent queries. Continue serving vector/FTS search. |
| **MinIO / S3** | Blob storage (raw files) | New data cannot be written. Existing Lance data still readable (local). | Write-behind queue. Retry with exponential backoff. |
| **PyArrow** | IPC layer shared by Daft + Lance + DuckDB | All data operations fail. | None. Pin version, test before upgrade. |

### Tier 3 — Medium (feature degraded, not blocked)

| Dependency | Role | Failure Impact | Degradation Strategy |
|-----------|------|---------------|---------------------|
| **HugeGraph** | Knowledge graph storage + GraphRAG | Graph-augmented retrieval unavailable. | Fall back to pure vector + FTS search (no GraphRAG fusion). |
| **Gravitino** | Metadata governance federation | Cross-source metadata sync paused. | Local DuckDB metadata continues serving. Sync resumes when Gravitino recovers. |
| **Sentence-Transformers** | Local embedding model | New embeddings cannot be generated. | Serve existing embeddings. Queue new ingestion. |
| **Ray** | Distributed compute + inference | Distributed features unavailable. | Fall back to single-node execution. |

### Tier 4 — Low (optional enhancement)

| Dependency | Role | Failure Impact | Degradation Strategy |
|-----------|------|---------------|---------------------|
| **Argo Workflows** | K8s production workflows | Scheduled workflows don't trigger. | Manual trigger or alternative scheduler. |
| **Metaflow** | ML workflow orchestration | User-facing ML pipelines unavailable. | SDK and CLI continue working. |
| **OTel Collector** | Distributed trace export | Traces not exported. | Local structlog continues. Metrics still served via Prometheus. |

---

## Redis Degradation Strategy (Detail)

Redis is the most critical single-point-of-failure. Three-level fallback:

```
Level 1: Redis Sentinel (recommended for v1.5.2)
  ├── Primary + 2 replicas
  ├── Automatic failover (< 10s)
  └── Docker Compose profile: redis-sentinel

Level 2: JWT stateless fallback
  ├── JWT validation continues without Redis
  ├── Blacklist check skipped (short token TTL limits window)
  └── In-process rate limiter replaces Redis-backed limiter

Level 3: In-process semaphore
  ├── threading.Semaphore replaces Redis distributed semaphore
  ├── Single-node only — no cross-instance coordination
  └── Acceptable for single-pod deployments
```

---

## Incident Response Playbook

| Symptom | Likely Cause | Check | Fix |
|---------|-------------|-------|-----|
| RAG queries timing out | LanceDB index not loaded | `arrow-lake maintenance status` | Trigger index warmup |
| Search returns 0 results | No vector/FTS index | `arrow-lake index vector <ds> --info` | Rebuild index |
| Auth failing | Redis down | `docker compose ps redis` | Restart Redis, check Sentinel |
| Ingestion slow | DuckDB lock contention | OTel trace for `create_dataset` | Check concurrent write limit |
| Full outage | MinIO/S3 unreachable | `aws s3 ls` / `mc admin info` | Check network, credentials |
