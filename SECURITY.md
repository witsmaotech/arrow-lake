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
- **JWT**: `Authorization: Bearer <token>` for user sessions
- **Rate Limiting**: Enabled by default via built-in RateLimitMiddleware (configurable)

### Data Protection

- S3/MinIO credentials: passed via `ArrowLakeConfig`, never hardcoded
- JWT secret: minimum 32 bytes, validated at startup
- SQL injection: centralized validation in `arrow_lake/validation.py`

### Transport

- REST API: TLS should be enabled in production via uvicorn (`--ssl-keyfile`/`--ssl-certfile`) or a reverse proxy (nginx/Caddy). See `api.tls_enabled` for configuration flag.
- CORS: configurable via `api.cors_origins`
- Request size limits: configurable via `api.max_request_size_mb`

## Security Headers (Production)

```
X-Content-Type-Options: nosniff
X-Frame-Options: DENY
Referrer-Policy: strict-origin-when-cross-origin
```

## Dependency Security

Dependencies are audited periodically. To run a security check:

```bash
pip-audit .
```

## Known Security Considerations

| Component | Risk | Mitigation |
|-----------|-------|------------|
| Gremlin queries | Injection | Blocked patterns + parameterized mode |
| S3 credentials | Exposure | Config-only, never env direct injection |
| DuckDB SQL | Injection | Centralized validation + LIMIT pushdown |
| File uploads | Path traversal | `os.path.basename` + whitelist |
| RAG prompts | Injection | Input sanitization in pipeline |
