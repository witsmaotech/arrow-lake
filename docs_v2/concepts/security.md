# Security Architecture

Arrow Lake implements defense-in-depth across seven layers: network, transport,
authentication, authorization, input validation, audit, and data integrity.

## 1. Authentication

Two modes operate independently or combined (`api_key`, `jwt`, `both`),
configured via `auth.auth_mode`.

**API Key** -- Constant-time comparison (`hmac.compare_digest`) prevents timing
attacks. Delivered via `X-API-Key` header. Public paths (`/health`, `/metrics`)
bypass auth unconditionally. Empty key returns `401` on all protected paths.

**JWT** -- Supports HS256 and RS256/ES256/PS256. Access tokens expire after 30
minutes; refresh tokens after 7 days. Claims include `sub`, `role`,
`permissions`, `exp`, `iat`, `iss`, `jti`. Secret key minimum length: 32
characters (Pydantic validator). Bootstrap token enables initial acquisition.

**JWT Blacklist** -- Dual-store revocation:

| Store | Scope | Capacity | Persistence |
|---|---|---|---|
| In-memory `OrderedDict` | Single-process | 100K (LRU) | Process lifetime |
| Redis `jwt:blacklist:<jti>` | Multi-replica | Unlimited | TTL-based |

Redis is optional; falls back to in-memory transparently.

## 2. Authorization (RBAC)

| Permission | VIEWER | EDITOR | ADMIN |
|---|:---:|:---:|:---:|
| `dataset:read` | Yes | Yes | Yes |
| `dataset:write` | -- | Yes | Yes |
| `dataset:delete` | -- | Yes | Yes |
| `admin:manage` | -- | -- | Yes |

**Dataset-Level ACLs** -- `PermissionChecker` supports per-dataset, per-role
grants overriding the global matrix. Admin always bypasses.

**Row/Column ACLs** -- `DatasetACL` defines `visible_columns` (whitelist) and
`row_filter` (comparison expression applied via PyArrow compute).

**Gravitino Bridge** -- `GravitinoRBACBridge` delegates to Gravitino
authorization (e.g., `read` -> `SELECT_TABLE`). Falls back to local RBAC when
unavailable. `MaskingEngine` applies Gravitino column masking policies after
ACL filtering.

## 3. Input Validation

**Identifier Whitelist** -- All dataset names, column names, and identifiers
must match `^[a-zA-Z_][a-zA-Z0-9_-]*$`. Applied in
`LanceStorageManager._validate_name()`, `validate_identifier()`, and the Daft
query API. Rejects `/`, `..`, special characters, and SQL metacharacters.

**SQL Injection Prevention** -- Three-layer defense:
1. `DANGEROUS_SQL_KEYWORDS_RE` blocks INSERT, UPDATE, DELETE, DROP, ALTER,
   CREATE, TRUNCATE, GRANT, REVOKE, EXEC, EXECUTE, COPY, IMPORT, EXPORT,
   UNION, EXCEPT, INTERSECT.
2. Semicolons rejected in all SQL input.
3. `escape_sql_literal()` escapes quotes/backslashes with a 10,000 char cap.

**Request Size** -- Configurable maximum (default 100 MB) enforced by
`request_size_limit_middleware_fn`.

## 4. API Security

**Rate Limiting** -- Sliding-window per `(client_ip, path)`. Default 60
requests/minute. Responses include `Retry-After` and `X-RateLimit-Remaining`.

**CORS** -- Configurable origin whitelist. Credentials disabled. Methods
restricted to GET, POST, PUT, DELETE, OPTIONS. Headers limited to
`Authorization`, `Content-Type`, `X-API-Key`, `X-Request-ID`.

**Security Headers** (applied except on `/health`, `/metrics`):

| Header | Value |
|---|---|
| `Strict-Transport-Security` | `max-age=31536000; includeSubDomains` |
| `X-Content-Type-Options` | `nosniff` |
| `X-Frame-Options` | `DENY` (configurable) |
| `Referrer-Policy` | `strict-origin-when-cross-origin` |
| `Permissions-Policy` | `camera=(), microphone=(), geolocation=()` |
| `Content-Security-Policy` | Configurable; omitted when empty |

**Correlation ID** -- UUID per request (from `X-Request-ID` or auto-generated),
propagated through the lifecycle.

## 5. Audit Trail

HMAC-SHA256 integrity on every audit entry. Canonical JSON (sorted keys) is
hashed; `verify()` recomputes and compares via `hmac.compare_digest`. Entries
persist to a Lance dataset (`_audit_trail`) with fields: `audit_id`,
`timestamp`, `event_type`, `actor`, `dataset_name`, `lance_version`,
`metaflow_run_id`, `metaflow_tags`, `payload`, `hmac_hash`.

Startup rejects configuration when audit is enabled but `hmac_secret_key` is
empty. Supports query (by dataset, time range, event type), JSON export, and
version-based replay.

## 6. Thread Safety

- **Per-Dataset Locking** -- `threading.RLock` per dataset (max 1024, LRU
  eviction) in `LanceStorageManager`. Prevents TOCTOU races on writes.
- **Connection Pool** -- DuckDB pool evicts idle connections after 300s, enforces
  max session lifetime. Zombies tracked via `duckdb_pool_evicted_connections_total`.
- **Circuit Breaker** -- CLOSED -> OPEN -> HALF_OPEN state machine, `threading.Lock`
  protected. Failure threshold: 5, recovery timeout: 30s.
- **JWT Blacklist** -- `threading.Lock` with bounded LRU prevents unbounded
  growth.

## 7. Data Protection

Lance format provides ACID guarantees. Each write creates a version; readers
snapshot at a specific version. `StorageVersioningMixin` supports checkpoint,
rollback, and compaction. Dead letter queue captures failed ingests with full
error context for replay.

## 8. Deployment Security

**Docker** -- Multi-stage build (builder + runtime). Runtime image contains
only `.venv`. Runs as non-root. Proxy variables cleared.

**Kubernetes** -- NetworkPolicy default-deny with explicit allowlists: Ray
(metrics), Prometheus (scraping), MinIO/S3 (9000), Redis (6379), HugeGraph
(8080), HTTPS (443), DNS (53). Secrets stored as Kubernetes Secrets
(base64-encoded). Pod Disruption Budget for availability.

**TLS** -- Nginx termination with TLSv1.2 + TLSv1.3, ECDHE cipher suite, HSTS
with preload.

## 9. STRIDE Threat Model

| Threat | Example | Mitigation |
|---|---|---|
| **S**poofing | Stolen API key | JWT rotation, 30-min TTL, blacklist |
| **T**ampering | Modify audit log | HMAC-SHA256 per entry, Lance append-only |
| **R**epudiation | Deny deletion | Immutable audit trail with actor tracking |
| **I**nfo Disclosure | Unauthorized column | Row/column ACLs, Gravitino masking |
| **D**enial of Service | Flood requests | Per-IP rate limiting, request size cap |
| **E**levation of Privilege | SQL injection | Keyword blacklist, identifier regex |

## 10. Security Checklist

- [ ] API key set with >= 32 characters entropy
- [ ] JWT secret >= 32 chars or asymmetric keys configured
- [ ] `auth_mode` not configured with empty credentials in production
- [ ] CORS origins restricted (not `["*"]`)
- [ ] Security headers enabled
- [ ] Rate limiting enabled for production
- [ ] Audit trail enabled with non-empty `hmac_secret_key`
- [ ] TLS termination configured (nginx or load balancer)
- [ ] Redis available for JWT blacklist in multi-replica deployments
- [ ] NetworkPolicy default-deny in Kubernetes
- [ ] Secrets in Kubernetes Secrets, not ConfigMaps
- [ ] Container runs as non-root
- [ ] `allow_unauthenticated_access` is `false` in production
- [ ] API docs disabled in production (`docs_enabled: false`)
- [ ] No secrets in source control
