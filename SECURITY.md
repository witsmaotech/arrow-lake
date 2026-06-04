# Security Policy

## Security Overview

Arrow Lake is a production-grade multimodal data lakehouse built on FastAPI, Apache Arrow, and Parquet. Security is implemented as a layered defense spanning authentication, authorization, input validation, infrastructure hardening, and observability.

This document describes the security architecture, configuration requirements for production deployments, and the process for reporting vulnerabilities.

---

## Supported Versions

Security updates are applied to the latest release branch. Older branches receive fixes only for critical vulnerabilities at project discretion.

| Version | Supported | Security Updates |
|---------|-----------|------------------|
| 1.5.x   | Yes       | Active           |
| 1.4.x   | No        | End-of-life      |
| < 1.4   | No        | End-of-life      |

---

## Reporting a Vulnerability

Arrow Lake follows a coordinated responsible disclosure process.

1. **Report** vulnerabilities privately by contacting the maintainers. Include a description, affected component, reproduction steps, and impact assessment.
2. **Acknowledge** — maintainers will respond within 72 hours with an initial assessment and expected timeline.
3. **Coordinate** — a fix is developed and a CVE is requested if applicable. Details remain private until a patch is released.
4. **Disclose** — the vulnerability is publicly disclosed after a patch is available in a release.

Do not open public issues for security vulnerabilities. Responsible disclosure protects all users.

---

## Security Features

### Authentication

Dual-mode authentication supports API Key and JWT tokens.

| Feature | Implementation |
|---------|----------------|
| API Key authentication | Static key via `X-API-Key` header for service-to-service auth |
| JWT authentication | `Authorization: Bearer <token>` for user sessions; HS256 (symmetric) or RS256 (asymmetric) signing |
| Token revocation | Redis-backed JWT blacklist with configurable TTL and O(1) LRU eviction |
| Credential storage | Environment variables via `.env` (gitignored); minimum 32-byte JWT secret validated at startup |
| Prod validation | Compose enforces required vars with `:?` syntax — startup fails on missing secrets |

### Authorization

Role-based access control (RBAC) enforces least-privilege across all endpoints via `Depends(require_role(...))` on 15 router modules covering 40+ endpoints.

| Role | Scope | Typical Use |
|------|-------|-------------|
| `VIEWER` | Read data, query datasets, search, view stats/schema/lineage | Analysts, monitoring dashboards |
| `EDITOR` | Read + write: ingest data, create indexes, modify data quality, export | Data engineers |
| `ADMIN` | Full access: knowledge graphs, backup/restore, user and role management | Platform operators |

Unauthorized requests receive `403 Forbidden`.

### Input Validation & Injection Prevention

| Threat | Mitigation |
|--------|------------|
| SQL Injection | Comment stripping (`--`, `/* */`); forbidden-statement regex blocks `DROP`, `DELETE`, `INSERT`, `UPDATE`, `ALTER`, `CREATE`; parameterized queries via DuckDB; `LIMIT` pushdown |
| Gremlin Injection | Multi-layer defense: read-only step whitelist; `{`/`}` closure blocking; bare mutation regex (`drop`, `addV`, `addE`, `property`, `remove`, `delete`); comment stripping; `map`/`flatMap` exclusion |
| SSRF | SQL connectors block private IP ranges (`127.0.0.0/8`, `10.0.0.0/8`, `172.16.0.0/12`, `192.168.0.0/16`, `169.254.0.0/16`) |
| Path Traversal | Absolute path rejection; `resolve()` + `startswith()` bounds check; `os.path.basename` + extension whitelist |
| XSS | FastAPI JSON-only responses; no server-side HTML rendering; `Content-Type` enforcement |
| CSRF | Stateless JWT auth; no cookie-based sessions |
| FQN Injection | `ValidationMixin` provides global validation — rejects illegal characters, path traversal sequences (`..`, `/`, `\`), and null bytes |
| JSON Deserialization | Depth limits and type whitelists on all external JSON payloads; prevents prototype pollution and deeply nested structure attacks |

### Rate Limiting

Endpoint-level rate limiting is enforced via slowapi backed by Redis.

- Configurable requests-per-minute (RPM) and burst capacity per endpoint (default 60 RPM).
- Rate-limit headers (`X-RateLimit-Limit`, `X-RateLimit-Remaining`, `X-RateLimit-Reset`) included in responses.
- Exceeded limits return `429 Too Many Requests`.

### Audit Trail

All significant operations are recorded in a tamper-evident audit log.

- Each log entry is signed with HMAC-SHA256 using a dedicated audit secret.
- Startup enforcement: `audit.enabled=true` without `hmac_secret_key` raises `ValueError`.
- Tamper detection: `POST /api/v1/audit/verify` uses constant-time comparison to validate the HMAC chain.
- Audit entries capture actor identity, action, resource, timestamp, and result.

### Data Protection

| Feature | Implementation |
|---------|----------------|
| PII masking | Configurable hash-based masking applied to sensitive fields during export and query results |
| Credential handling | S3/MinIO credentials passed via `ArrowLakeConfig`, never hardcoded |
| In-transit encryption | TLS terminated at Nginx; internal traffic on `127.0.0.1`; configurable via `api.tls_enabled` + Helm TLS volume mount |
| Storage security | Parquet files on mounted volumes; filesystem permissions enforced by container user |

### Container & Infrastructure Hardening

Container images follow CIS Docker Benchmark guidelines.

| Control | Configuration |
|---------|---------------|
| Kernel capabilities | `cap_drop: ALL` |
| Filesystem | Read-only root filesystem (`readOnlyRootFilesystem`); writable volumes for data and logs only |
| Resource limits | CPU and memory caps enforced via Docker Compose and Kubernetes `resources.limits` |
| Process user | Non-root user inside containers (`runAsNonRoot`) |
| Port binding | Application binds to `127.0.0.1` only; Nginx handles external traffic |
| Network isolation | Kubernetes `NetworkPolicy` restricts pod-to-pod communication to required services |

### Security Headers

The following headers are enforced in production:

```
Strict-Transport-Security: max-age=31536000; includeSubDomains
X-Content-Type-Options: nosniff
X-Frame-Options: DENY
Referrer-Policy: strict-origin-when-cross-origin
Content-Security-Policy: default-src 'none'; frame-ancestors 'none'
```

CORS is configurable via `api.cors_origins` (empty = disabled in production). Request size limits are enforced via `api.max_request_size_mb`.

### Observability

| Component | Tool |
|-----------|------|
| Distributed tracing | OpenTelemetry (OTLP export) |
| Metrics | Prometheus + Alertmanager with configurable alert rules |
| Structured logging | structlog with JSON output for machine parsing |
| Thread monitoring | Background zombie thread detection with automatic recovery |

---

## Security Configuration Checklist

Use this checklist when deploying Arrow Lake to production. All items are required.

### Pre-Deployment

| # | Check | How to Verify |
|---|-------|---------------|
| 1 | `.env` file exists with all required secrets | `docker compose --env-file .env config` succeeds |
| 2 | `.env` is not tracked in git | `git status` does not show `.env` |
| 3 | `JWT_SECRET` is cryptographically random (>= 32 bytes) | `python3 -c "import os; print(len(os.getenv('JWT_SECRET','')) >= 32)"` |
| 4 | `API_KEY` is unique and not the default `dev-api-key-for-local-testing-only` | Compare against known defaults |
| 5 | `AUDIT_HMAC_SECRET_KEY` is set and differs from `JWT_SECRET` | Verify both keys exist and differ |
| 6 | `ADMIN_API_KEY` is set if admin API is exposed | Present in `.env` |
| 7 | Redis is secured (password auth, SSL, or network isolation) | `redis-cli ping` requires authentication |

### Network & TLS

| # | Check | How to Verify |
|---|-------|---------------|
| 8 | Nginx TLS is enabled with valid certificates | `curl --head https://<host>` returns `200` |
| 9 | HTTP redirects to HTTPS | `curl -I http://<host>` returns `301/302` |
| 10 | Application port not publicly accessible | `ss -tlnp` shows no external listeners on app port |
| 11 | Kubernetes NetworkPolicy applied | `kubectl get networkpolicy` shows policies for Arrow Lake pods |
| 12 | CORS origins restricted | `api.cors_origins` is empty or set to known domains |

### Runtime

| # | Check | How to Verify |
|---|-------|---------------|
| 13 | Container runs as non-root | `docker exec <container> id` shows UID != 0 |
| 14 | Read-only filesystem enabled | `docker inspect` shows `ReadonlyRootfs: true` |
| 15 | All capabilities dropped | `docker inspect` shows `CapAdd` empty, `CapDrop: ALL` |
| 16 | Resource limits set | `docker inspect` shows `Memory` and `NanoCpu` limits |
| 17 | Rate limiting is active | Send >N requests; observe `429` response |
| 18 | JWT blacklist Redis is reachable | Revoked token returns `401` |
| 19 | Audit log is being written | Check audit log volume for new entries after an action |
| 20 | Swagger UI disabled | `api.docs_enabled: false`; `/docs` returns `404` |

### Post-Deployment

| # | Check | How to Verify |
|---|-------|---------------|
| 21 | `/health` returns `200` | `curl http://127.0.0.1:<port>/health` |
| 22 | Unauthenticated requests return `401` | `curl` without credentials |
| 23 | VIEWER role cannot write | Attempt `POST` with VIEWER token; expect `403` |
| 24 | EDITOR role cannot access admin endpoints | Attempt admin endpoint with EDITOR token; expect `403` |
| 25 | OpenTelemetry traces are exported | Check tracing backend for Arrow Lake spans |
| 26 | Prometheus metrics are scraped | Check Prometheus targets for Arrow Lake job |
| 27 | Audit log HMAC verification passes | `POST /api/v1/audit/verify` returns valid |

### Dependency Security

| # | Check | How to Verify |
|---|-------|---------------|
| 28 | Bandit scan passes | `bandit -r arrow_lake/` returns no HIGH/CRITICAL findings |
| 29 | pip-audit passes | `pip-audit .` returns no known vulnerabilities |

---

## Known Security Considerations

| Component | Risk | Mitigation |
|-----------|------|------------|
| Gremlin queries | Injection via Groovy closures | Whitelist + closure blocking + comment stripping + bare mutation regex |
| DuckDB SQL | Injection via comment-embedded keywords | Comment stripping + keyword blocklist + LIMIT pushdown + FQN validation |
| FQN identifiers | Injection via path traversal characters | `ValidationMixin` global validation — rejects illegal chars, `..`, `/`, `\`, null bytes |
| File exports | Path traversal to arbitrary files | Absolute path rejection + `resolve()` bounds check + extension whitelist |
| JSON payloads | Prototype pollution / deserialization attacks | Depth limits + type whitelists on all external JSON |
| JWT tokens | Use after revocation | Blacklist with Redis persistence + O(1) LRU eviction |
| S3 credentials | Exposure in logs or config dumps | Config-only injection, never hardcoded |
| RAG prompts | Prompt injection via user input | Input sanitization in pipeline |
| Audit log | Post-hoc tampering | HMAC-SHA256 chain + startup enforcement + constant-time verify |
| Worker threads | Resource exhaustion via leaked threads | Zombie thread detection + automatic recovery |

---

## Dependency Security

Run these tools as part of CI and before releases:

```bash
# Bandit — Python security linter
bandit -r arrow_lake/

# pip-audit — known vulnerability database
pip-audit .
```

---

## Security Audit History

| Date | Version | Scope | Findings | Status |
|------|---------|-------|----------|--------|
| 2025-05 | v1.5.2 | Full security hardening | Env validation, rate-limit tuning, RBAC enforcement, path traversal hardening | Closed |
| 2025-04 | v1.5.1 | Dependency audit | Updated packages with known CVEs; no application-level findings | Closed |
