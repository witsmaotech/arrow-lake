# Deployment — Docker, Helm, Auth, Observability

> Back-reference: [../SKILL.md](../SKILL.md) · parent: [architecture.md](architecture.md). Verified v1.7.0 (incl. deploy hardening).

## Docker Compose (profile-based)

`deploy/docker-compose.yml` core services (verified):
`api` · `minio` · `minio-init` · `redis` · `ray-head` · `ray-worker` · `jupyter` · `turbo-ocr` · `proxy-forward`.

Networks: `arrow-lake-net`, `hg-net`. Volumes: `minio-data`, `lake-data`, `ocr-cache`.

```bash
# Core (minimal prod)
docker compose -f deploy/docker-compose.yml up -d

# Dev profile (+ Ray + Jupyter)
docker compose --profile dev -f deploy/docker-compose.yml up -d

# Gravitino (+ Gravitino + Lance REST)
docker compose --profile gravitino -f deploy/docker-compose.yml up -d

# HugeGraph overlay (with v1.6.3 Gremlin-fix entrypoint)
docker compose -f deploy/docker-compose.prod.yml -f deploy/docker-compose.hugegraph.yml up -d
```

Production file `deploy/docker-compose.prod.yml` adds: nginx (gzip/CSP/proxy-buffer/SSE 600s), redis-exporter sidecar, the HugeGraph entrypoint wrapper, `REDISCLI_AUTH` healthcheck, `tmpfs /tmp` for read-only Ray workers, pinned image tags.

## Helm

```bash
helm install arrow-lake deploy/helm/arrow-lake/
helm upgrade arrow-lake deploy/helm/arrow-lake/
```
Resources charted: Deployment, HPA (CPU + custom metrics), CronJob backup (02:00 UTC), Ingress, PDB, Secret, NetworkPolicy (Redis 6379 / HugeGraph 8080 / HTTPS 443 / DNS 53 only).

## Configuration

3-layer precedence (per README v1.6.x): defaults → env vars (`ARROW_LAKE__` prefix) → YAML overlay. Use `deploy/.env.example` (sanitized template, v1.6.3) for secrets. Production loads via `Lake.from_yaml("configs/prod.yaml")`.

## Auth & RBAC

- **Dual auth**: API Key (HMAC compare) + JWT (HS256/RS256/ES256), Redis-backed blacklist with TTL.
- **App rejects startup with empty credentials** when auth is enabled (don't ship with placeholder keys).
- **Roles**: `ADMIN` > `EDITOR` > `VIEWER`. Enforced on all 40+ endpoints via decorators.
- **Granular ACL**: `DatasetACL` (row/column level), `SchemaACL`. Gravitino tag-driven ACL + masking + retention.
- **Docs endpoint** conditional (`api.docs_enabled` — turn OFF in prod).

## Security hardening (v1.6.3 deploy pass)

- Rate limiting: sliding window per `IP:path`.
- SQL injection: dangerous-keyword regex + identifier validation (see [query-layer.md](query-layer.md)).
- Gremlin injection defense on KG queries.
- Path traversal prevention + file-type whitelist (PDF in v1.2) + size limit.
- Security headers middleware (CSP, HSTS, X-Frame-Options, nosniff, Referrer-Policy).
- TLS termination configurable.
- Container hardening: `cap_drop: ALL`, read-only filesystems, resource limits, PID constraints.
- Audit: HMAC-SHA256 tamper-evident trail.
- Secrets: env/secret manager only — never hardcoded.

## Observability

- **structlog** — JSON structured logs.
- **Prometheus** + **redis-exporter** (v1.6.3) + Alertmanager (+8 infra alert rules).
- **Grafana** dashboards.
- **OpenTelemetry** tracing (OTLP exporter), latency-breakdown tracking.
- Metrics at `/metrics`; health at `/api/v1/health`.

## Async tasks (v1.6.1+) — important for ops

Heavy ops are fire-and-forget with a task-id:
- `POST /api/v1/datasets/{name}/ingest/async` (HTTP 202)
- `POST /api/v1/backup/create/async` (HTTP 202)
- `POST /api/v1/backup/restore/async` (HTTP 202)
- `GET /api/v1/tasks` (list, filterable)
- `GET /api/v1/tasks/{task_id}/status` (unified status)

`TaskManager` dual-writes to Redis (`RedisTaskStore`, v1.6.2) so status is correct across uvicorn workers. Config: `RedisConfig.task_key_prefix`, `RedisConfig.task_ttl_seconds`. The app initializes/closes the Redis store in its lifespan.

## Production checklist

- [ ] Non-empty API keys / JWT secret (startup enforces)
- [ ] `docs_enabled=false` in prod
- [ ] Rate limiting on
- [ ] TLS + security headers
- [ ] Redis persisted (JWT blacklist + task store survive restarts)
- [ ] HugeGraph overlay applied (Gremlin fix) if using KG
- [ ] NetworkPolicy restricts pod-to-pod
- [ ] Container hardening (cap_drop, read-only fs)
- [ ] Backups scheduled (CronJob 02:00 UTC)
- [ ] Prometheus + redis-exporter + alert rules

## Common Mistakes

- **Shipping placeholder secrets** (`<CHANGE_ME>`): the app rejects startup when real creds are missing; `LanceStorageManager` only passes S3 config when creds don't start with `<`.
- **Forgetting the HugeGraph overlay**: `g.V()` fails without the v1.6.3 entrypoint fix.
- **Single-worker assumption**: without Redis task store, `kg_build_status` is wrong across workers (v1.6.2 fixes this).
- **Leaving docs endpoint on** in prod.
- **Not re-embedding after updates** → silent search drift (see [ingestion-quality.md](ingestion-quality.md)).
