# Security Audit Report — Arrow Lake / DIntelliHub

**Date:** 2026-05-27
**Scope:** 222 Python files, 17 modules (full project)
**Method:** 3-phase parallel scan → false-positive filtering (confidence < 7 auto-excluded)

---

## Vulnerability Summary

| # | File | Severity | Category | Confidence |
|---|------|----------|----------|------------|
| 1 | `ingest/connectors_sql.py:32-62` | HIGH | SSRF | 8/10 |
| 2 | `query/federated_engine.py:18-21` | MEDIUM | SQL Injection | 7/10 |
| 3 | `api/routers/gravitino.py:63-372` | MEDIUM | Authorization Bypass | 8/10 |
| 4 | `quality/masking_engine.py:44-47` | MEDIUM | Hardcoded Secret | 7/10 |

---

## Vuln 1: SSRF via SQL Connector `connection_url`

**File:** `arrow_lake/ingest/connectors_sql.py`, lines 32-62
**Severity:** HIGH | **Category:** `ssrf` | **Confidence:** 8/10

**Description:** `POST /api/v1/datasets/{name}/ingest/sql` accepts user-provided `connection_url` (SQLAlchemy connection string) with zero hostname/IP validation. While `IngestHttpRequest` has full SSRF protection (scheme whitelist, private IP blocking, DNS rebinding protection), the SQL connector has none of these checks.

**Exploit Scenario:** Authenticated EDITOR submits `connection_url: "postgresql://169.254.169.254:5432/"` to access AWS IMDSv1 metadata, or `connection_url: "mysql://10.0.0.1:3306/production"` to probe internal database services.

**Fix:** Extract hostname from SQLAlchemy URL and validate against private IP ranges (matching `connectors_http.py` patterns).

---

## Vuln 2: SQL Injection in Federated Query Engine

**File:** `arrow_lake/query/federated_engine.py`, lines 18-21
**Severity:** MEDIUM | **Category:** `sql_injection` | **Confidence:** 7/10

**Description:** `_DANGEROUS_SQL` regex only matches dangerous keywords after semicolons (`;\s*(DROP|DELETE|...)`). DDL/DML statements without a preceding semicolon pass through entirely. No downstream validation exists — SQL goes directly to `conn.execute()`.

**Exploit Scenario:** If federated query path is exposed to API, attacker submits `join_sql: "DROP TABLE alias1"`. Current impact limited to in-memory DuckDB (ephemeral Arrow tables only), but validation gap is real.

**Fix:** Replace with `arrow_lake.validation.validate_sql_safety()` or fix regex to match statement-start keywords.

---

## Vuln 3: Gravitino Router Missing Per-Route RBAC

**File:** `arrow_lake/api/routers/gravitino.py`, lines 63-372
**Severity:** MEDIUM | **Category:** `authorization_bypass` | **Confidence:** 8/10

**Description:** All 13 Gravitino metadata routes lack `Depends(require_role(...))`. While global auth middleware covers `/metadata/*` (unauthenticated requests get 401), any authenticated role (including VIEWER) can call write operations: `create_tag`, `create_masking_policy`, `create_retention_policy`, `enforce_policies` (triggers data deletion).

**Exploit Scenario:** VIEWER role user sends `POST /metadata/policies/enforce?table=sensitive_table`, triggering retention policy enforcement that deletes production data.

**Fix:** Add `Depends(require_role(Role.ADMIN))` to destructive endpoints, `Depends(require_role(Role.EDITOR))` to write endpoints.

---

## Vuln 4: Hardcoded Default HMAC Key

**File:** `arrow_lake/quality/masking_engine.py`, lines 44-47
**Severity:** MEDIUM | **Category:** `hardcoded_secret` | **Confidence:** 7/10

**Description:** `MaskingEngine` silently falls back to hardcoded default key `"default-dev-key-change-in-prod"` when `ARROW_LAKE__MASKING__HMAC_KEY` env var is unset. No startup warning or runtime log. Key is used for HMAC-SHA256 hash masking of PII data.

**Exploit Scenario:** Operator forgets to set HMAC env var. System uses publicly known default key. Attacker with source access precomputes rainbow table to reverse hash-masked PII (phone numbers, ID numbers).

**Fix:** Log WARNING when using default key, or refuse to start with default in production mode.

---

## Confirmed Safe Designs

| Control | Location | Assessment |
|---------|----------|------------|
| SQL injection defense (core paths) | `validation.py` `DANGEROUS_SQL_KEYWORDS_RE` | Blocks UNION/EXCEPT/semicolons/all DML DDL |
| HTTP connector SSRF protection | `connectors_http.py` | DNS pre-resolution + IP validation + scheme whitelist |
| Path traversal defense | `_check_no_traversal`, `_sanitize_filename` | Handles URL encoding, null bytes, `..` |
| Dataset name validation | `validate_identifier()` | `^[a-zA-Z_][a-zA-Z0-9_-]*$` consistent everywhere |
| No unsafe deserialization | Global | Uses `yaml.safe_load`, no `pickle`/`eval`/`exec` |
| JWT + API Key dual auth | `auth.py`, `jwt_auth.py` | Global middleware, precise public path whitelist |
| CORS configuration | `app.py:288` | Default empty origins, `credentials=False` |
| Gremlin injection defense | `kg/client.py` | `_gremlin_escape()` + blocked pattern list |
| Export path validation | `ExportRequest` | Blocks `..`, absolute paths, null bytes; download validates `is_relative_to` |
