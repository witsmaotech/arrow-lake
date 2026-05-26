# Security Policy

## Reporting Vulnerabilities

If you discover a security vulnerability in Arrow Lake, please report it
responsibly.

**DO NOT** open a public issue. Instead:

1. Email: security@<project-domain>
2. Include: description, affected component, reproduction steps, impact assessment
3. We will acknowledge within 48 hours and provide an estimated fix timeline

## Security Architecture

### Authentication

- **API Key**: `X-API-Key` header for service-to-service auth
- **JWT**: `Authorization: Bearer <token>` for user sessions, blacklisted tokens persisted to Redis
- **Rate Limiting**: Per-IP rate limiting with configurable RPM/burst (default 60 RPM)

### Authorization (RBAC)

Three-tier role model enforced on all API endpoints:

| Role | Capabilities |
|------|-------------|
| **VIEWER** | Read data, query datasets, search, view stats/schema/lineage |
| **EDITOR** | Ingest data, create indexes, modify data quality, export |
| **ADMIN** | Build/delete knowledge graphs, backup/restore, manage users |

Roles are enforced via `Depends(require_role(...))` on 15 router modules covering 40+ endpoints.

### Audit Trail Integrity

- **HMAC-SHA256**: Every audit entry is signed with a server-side secret
- **Startup enforcement**: `audit.enabled=true` without `hmac_secret_key` raises `ValueError`
- **Tamper detection**: `POST /api/v1/audit/verify` uses constant-time comparison
- **Production config**: `audit.hmac_secret_key` must be set via environment variable

### Data Protection

- S3/MinIO credentials: passed via `ArrowLakeConfig`, never hardcoded
- JWT secret: minimum 32 bytes, validated at startup
- SQL injection: centralized validation with comment stripping (`--`, `/* */`)
- Path traversal: `resolve()` + `startswith()` guard on all file operations

### Transport Security

- REST API: TLS configurable via `api.tls_enabled` + Helm TLS volume mount
- CORS: configurable via `api.cors_origins` (empty = disabled in production)
- Request size limits: configurable via `api.max_request_size_mb`
- Security headers: `X-Content-Type-Options`, `X-Frame-Options`, `Referrer-Policy`, `Content-Security-Policy`

## Security Headers (Production)

```
Strict-Transport-Security: max-age=31536000; includeSubDomains
X-Content-Type-Options: nosniff
X-Frame-Options: DENY
Referrer-Policy: strict-origin-when-cross-origin
Content-Security-Policy: default-src 'none'; frame-ancestors 'none'
```

## Input Validation

### FQN (Fully Qualified Name) Validation

- `ValidationMixin` provides global FQN validation across all user-facing APIs
- Rejects illegal characters, path traversal sequences (`..`, `/`, `\`), and null bytes
- Dataset names, column names, and tag names all pass through FQN validation

### JSON Deserialization Safety

- External JSON payloads are validated with depth limits and type whitelists
- Prevents prototype pollution and deeply nested structure attacks
- Applied to all API endpoints accepting JSON request bodies

### Thread Zombie Detection

- Background thread monitor detects and recovers leaked worker threads
- Prevents resource exhaustion from orphaned threads in long-running processes

### Gremlin Query Safety

Multi-layer defense against Gremlin/Groovy injection:

1. **Whitelist**: Only read-only traversal steps allowed (V, E, has, out, in, count, etc.)
2. **Closure blocking**: `{` and `}` characters rejected (prevents Groovy closure execution)
3. **Bare mutation detection**: Regex blocks `drop`, `addV`, `addE`, `property`, `remove`, `delete` without parentheses
4. **Comment stripping**: `//` line comments and `/* */` block comments stripped before validation
5. **Map/flatMap**: Excluded from whitelist (closure execution risk)

### SQL Validation

- Comment stripping (`--` and `/* */`) before validation
- Dangerous keyword detection (DROP, DELETE, INSERT, UPDATE, ALTER, CREATE)
- Parameterized queries via DuckDB

### Path Traversal Prevention

- Export endpoint: absolute paths rejected, `resolve()` + `startswith()` bounds check
- File operations: `os.path.basename` + extension whitelist

## Dependency Security

Dependencies are audited periodically. To run security checks:

```bash
# Bandit — Python security linter
bandit -r arrow_lake/

# pip-audit — known vulnerability database
pip-audit .
```

## Security Checklist (Production)

- [ ] `api.tls_enabled: true` with valid TLS certificate
- [ ] `audit.hmac_secret_key` set via `ARROW_LAKE__AUDIT__HMAC_SECRET_KEY`
- [ ] `redis.ssl: true` for distributed session coordination
- [ ] `rate_limit.enabled: true` with appropriate RPM/burst
- [ ] `api.docs_enabled: false` (disable Swagger UI)
- [ ] `api.cors_origins: []` (restrict CORS)
- [ ] JWT secret ≥ 32 bytes, rotated regularly
- [ ] API key changed from default `dev-api-key-for-local-testing-only`
- [ ] NetworkPolicy enabled in Helm values
- [ ] `securityContext` configured (runAsNonRoot, readOnlyRootFilesystem, drop ALL capabilities)

## Known Security Considerations

| Component | Risk | Mitigation |
|-----------|------|------------|
| Gremlin queries | Injection | Whitelist + closure blocking + comment stripping + bare mutation regex |
| DuckDB SQL | Injection | Comment stripping + keyword blocklist + LIMIT pushdown + FQN validation |
| FQN identifiers | Injection | `ValidationMixin` global validation — rejects illegal chars, path traversal, null bytes |
| File exports | Path traversal | Absolute path rejection + `resolve()` bounds check |
| JSON payloads | Deserialization attack | Depth limits + type whitelists on all external JSON |
| JWT tokens | Revocation | Blacklist with Redis persistence + O(1) LRU eviction |
| S3 credentials | Exposure | Config-only, never hardcoded |
| RAG prompts | Injection | Input sanitization in pipeline |
| Audit log | Tampering | HMAC-SHA256 + startup enforcement + constant-time verify |
| Worker threads | Resource exhaustion | Zombie thread detection + automatic recovery |
