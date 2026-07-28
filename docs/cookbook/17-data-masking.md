# Data Masking

> Column-level privacy controls in the read path. Policies map sensitive columns
> to one of four masking functions, enforced transparently for VIEWER roles,
> fail-closed, and audited through the Lance audit trail.

Masking is governed by `MaskingEngine` (`arrow_lake/quality/masking_engine.py`)
and exposed through the Gravitino policy layer plus a preview endpoint. It
**requires an HMAC key at startup** — without it the service refuses to boot.

## 1. Configure the HMAC Key (Required)

The engine signs `hash` outputs and fails closed when the key is absent. Set the
key before starting the API:

```bash
# deploy/.env or compose environment
ARROW_LAKE__MASKING__HMAC_KEY=<your-secret-key>
```

If the key is missing, startup raises `RuntimeError` and the container exits. For
development only, opt into a degraded mode:

```bash
ARROW_LAKE__MASKING__ALLOW_MISSING_KEY=1   # dev only; hash() then raises at call time
```

## 2. Create a Masking Policy

A policy names a set of columns and the function to apply:

| Function | Behavior |
|---|---|
| `redact` | Replace with a fixed sentinel (default) |
| `hash` | HMAC-SHA256, 128-bit (`[:32]` hexdigest) — deterministic, joinable |
| `partial` | Keep prefix/suffix, mask the middle (e.g. `138****1234`) |
| `nullify` | Replace with NULL |

```bash
curl -X POST http://127.0.0.1:8000/api/v1/gravitino/policies/masking \
  -H "X-API-Key: $KEY" -H "Content-Type: application/json" \
  -d '{"name": "pii_mask", "columns": ["phone", "email"], "function": "partial"}'
```

Policy creation is itself audited (see §5).

## 3. Preview Before Publishing

Verify a rule against real data without committing it:

```bash
curl -X POST http://127.0.0.1:8000/api/v1/datasets/customers/quality/mask-preview \
  -H "X-API-Key: $KEY" -H "Content-Type: application/json" \
  -d '{"columns": ["phone"], "function": "partial"}'
```

Returns before/after pairs for the first rows:

```json
{"phone": {"before": ["13812345678"], "after": ["138****5678"]}}
```

The preview is **ADMIN-only** (not `EDITOR`) to prevent bypassing column ACLs,
and column names are validated against an identifier whitelist to refuse SQL
injection.

## 4. Enforcement and Fail-Closed

Once a policy targets a dataset, the RBAC layer applies it transparently on read
for VIEWER roles. Enforcement is **fail-closed**: if the masking engine raises
(masking error, missing key in `hash`, unknown function), the query returns an
**empty table** rather than leaking the unmasked source. An unknown function name
raises `ValueError` at policy time, so misconfiguration surfaces early.

## 5. Audit

Policy creation is recorded through the Lance audit trail (zero new tables):

```bash
curl "http://127.0.0.1:8000/api/v1/audit/query?event_type=masking_policy_created" \
  -H "X-API-Key: $KEY"
```
