# REST API Guide

Arrow Lake ships with a built-in FastAPI REST server that exposes HTTP interfaces for the full
range of platform capabilities — data ingestion, vector search, RAG question answering, and
knowledge graph management. It supports API Key authentication and a dual-mode JWT + RBAC system.

> Prerequisites: Install dependencies with `pip install arrow-lake[api]` and configure your
> authentication credentials.

***

## 1. Starting the Server

```bash
# Start with default settings (binds to 0.0.0.0:8000)
arrow-lake serve --host 0.0.0.0 --port 8000

# Start using a YAML configuration file
arrow-lake serve --config /path/to/config.yaml
```

Once running, access the Swagger UI at `http://localhost:8000/docs` or the ReDoc at
`http://localhost:8000/redoc`.

***

## 2. API Key Authentication

After setting `api.api_key` in your configuration, all non-documentation endpoints require
authentication via the `X-API-Key` request header.

```yaml
# config.yaml
api:
  enabled: true
  api_key: "your-secret-api-key-here"
  api_key_header: "X-API-Key"
```

```bash
curl -X GET http://localhost:8000/api/v1/datasets \
  -H "X-API-Key: your-secret-api-key-here"
```

Requests without an API Key receive a 401 response: `{"detail": "Missing or invalid API key"}`

***

## 3. JWT Authentication & RBAC

The authentication mode is controlled by `auth.auth_mode`, which accepts `api_key`, `jwt`, or `both`.

### Role Hierarchy

| Role     | Description   | Scope                                                       |
| -------- | ------------- | ----------------------------------------------------------- |
| `ADMIN`  | Administrator | All operations, including graph builds and dataset deletion |
| `EDITOR` | Editor        | Data ingestion, search, Gremlin queries                     |
| `VIEWER` | Viewer        | Read-only search and RAG question answering                 |

### Obtaining and Using Tokens

```bash
# In "both" mode, exchange an API Key for a JWT token pair
curl -X POST http://localhost:8000/api/v1/auth/token \
  -H "X-API-Key: your-secret-api-key-here" \
  -H "Content-Type: application/json" \
  -d '{"role": "editor"}'
# => {"access_token": "eyJ...", "refresh_token": "eyJ...", "token_type": "bearer"}

# Access protected endpoints with a Bearer token
curl -X POST http://localhost:8000/api/v1/rag/query \
  -H "Authorization: Bearer eyJhbGciOiJIUzI1NiIs..." \
  -H "Content-Type: application/json" \
  -d '{"question": "...", "dataset_name": "docs"}'

# Refresh the token
curl -X POST http://localhost:8000/api/v1/auth/refresh \
  -H "Content-Type: application/json" \
  -d '{"refresh_token": "eyJ..."}'
```

### JWT Configuration Reference

| Option                          | Default        | Description                                     |
| ------------------------------- | -------------- | ----------------------------------------------- |
| `auth.auth_mode`                | `"api_key"`    | Authentication mode: `api_key` / `jwt` / `both` |
| `auth.jwt_secret_key`           | `""`           | JWT signing key                                 |
| `auth.jwt_algorithm`            | `"HS256"`      | Signing algorithm                               |
| `auth.jwt_access_token_minutes` | `30`           | Access token lifetime in minutes                |
| `auth.jwt_refresh_token_days`   | `7`            | Refresh token lifetime in days                  |
| `auth.jwt_issuer`               | `"arrow-lake"` | JWT issuer claim                                |

***

## 4. Core Endpoint Reference

### Datasets & Ingestion (v1)

| Method   | Endpoint                                   | Description              | Role   |
| -------- | ------------------------------------------ | ------------------------ | ------ |
| `GET`    | `/api/v1/datasets`                         | List all datasets        | -      |
| `GET`    | `/api/v1/datasets/{name}`                  | Dataset details          | -      |
| `DELETE` | `/api/v1/datasets/{name}`                  | Delete a dataset         | EDITOR |
| `POST`   | `/api/v1/datasets/{name}/ingest`           | Ingest local files       | EDITOR |
| `POST`   | `/api/v1/datasets/{name}/ingest/http`      | Ingest from a remote URL | EDITOR |
| `POST`   | `/api/v1/datasets/{name}/ingest/images`    | Ingest images            | EDITOR |
| `POST`   | `/api/v1/datasets/{name}/ingest/documents` | Ingest PDF documents     | EDITOR |

### Search (v1)

| Method | Endpoint                                 | Description      | Role   |
| ------ | ---------------------------------------- | ---------------- | ------ |
| `POST` | `/api/v1/datasets/{name}/search/vector`  | Vector search    | VIEWER |
| `POST` | `/api/v1/datasets/{name}/search/fts`     | Full-text search | VIEWER |
| `POST` | `/api/v1/datasets/{name}/search/hybrid`  | Hybrid search    | VIEWER |
| `POST` | `/api/v1/datasets/{name}/search/faceted` | Faceted search   | VIEWER |

### RAG & Knowledge Graph (v2)

| Method | Endpoint                             | Description                 | Role   |
| ------ | ------------------------------------ | --------------------------- | ------ |
| `POST` | `/api/v1/rag/query`                  | RAG question answering      | -      |
| `POST` | `/api/v1/rag/query/stream`           | Streaming RAG               | -      |
| `POST` | `/api/v1/kg/build`                   | Build the knowledge graph   | ADMIN  |
| `GET`  | `/api/v1/kg/build/{task_id}/status`  | Build task status           | -      |
| `POST` | `/api/v1/kg/query`                   | Execute a Gremlin query     | EDITOR |
| `GET`  | `/api/v1/kg/entities/{id}/neighbors` | Neighbor traversal          | -      |
| `POST` | `/api/v1/kg/query/graphrag`          | GraphRAG question answering | VIEWER |

### Authentication (v2)

| Method | Endpoint               | Description              |
| ------ | ---------------------- | ------------------------ |
| `POST` | `/api/v1/auth/token`   | Exchange API Key for JWT |
| `POST` | `/api/v1/auth/refresh` | Refresh a token          |
| `GET`  | `/api/v1/auth/me`      | Current user information |

***

## 4.5 RBAC Role Matrix (continued)

Over 30 API endpoints enforce role-based access control via the `require_role()` dependency.
The role hierarchy is **ADMIN > EDITOR > VIEWER** — each higher role inherits the permissions
of all roles below it.

### Endpoint Access by Role

| Capability Category          | VIEWER          | EDITOR                          | ADMIN                            |
| ---------------------------- | --------------- | ------------------------------- | -------------------------------- |
| **Search & Query**           | search/\*       | (inherits VIEWER)               | (inherits all)                   |
| **RAG**                      | rag/query/\*    | (inherits VIEWER)               | (inherits all)                   |
| **GraphRAG**                 | graphrag        | (inherits VIEWER)               | (inherits all)                   |
| **Data Ingestion**           | -               | ingest/\*, datasets DELETE      | (inherits all)                   |
| **Embedding**                | -               | embedding/\*                    | (inherits all)                   |
| **Quality & Dedup**          | -               | quality/\*, dedup/\*            | (inherits all)                   |
| **Lineage & Audit**          | -               | lineage write                   | audit export                      |
| **Export**                   | -               | export/\*                       | (inherits all)                   |
| **Backup**                   | -               | -                               | backup create / restore / delete  |
| **Knowledge Graph Build**    | -               | kg/query                        | kg/build, admin/\*               |
| **Dataset ACL Management**   | -               | -                               | grant / revoke dataset access    |

### Quick Reference

- **VIEWER**: `search/*`, `rag/query`, `kg/query/graphrag`, `kg/entities/*/neighbors`, `kg/build/*/status`
- **EDITOR**: All VIEWER endpoints + `ingest/*`, `datasets/{name} DELETE`, `embedding/*`, `quality/*`, `export/*`, `kg/query`, `lineage write`
- **ADMIN**: All EDITOR endpoints + `kg/build`, `backup/*`, `admin/*`, `audit/export`, dataset ACL management

The `PermissionChecker` supports per-dataset ACL overrides — an ADMIN can grant a VIEWER write
access to a specific dataset without changing their global role. See `arrow_lake.api.rbac` for
the full permission matrix implementation.

## 5. curl Examples

### Ingest Files

```bash
curl -X POST http://localhost:8000/api/v1/datasets/docs/ingest \
  -H "X-API-Key: your-secret-api-key-here" \
  -H "Content-Type: application/json" \
  -d '{"file_paths": ["examples/data/papers/full_text/p001_attention_is_all_you_need.pdf", "examples/data/papers/full_text/p002_bert_pretraining.pdf"]}'
# => {"success": true, "total_rows": 156, "total_files": 2, "sources": [...]}
```

### Vector Search

```bash
curl -X POST http://localhost:8000/api/v1/datasets/docs/search/vector \
  -H "X-API-Key: your-secret-api-key-here" \
  -H "Content-Type: application/json" \
  -d '{"query_vector": [0.1, 0.2, 0.3, ...], "top_k": 5}'
```

### RAG Question Answering

```bash
curl -X POST http://localhost:8000/api/v1/rag/query \
  -H "X-API-Key: your-secret-api-key-here" \
  -H "Content-Type: application/json" \
  -d '{"question": "How does the RAG pipeline work?", "dataset_name": "docs", "top_k": 5, "retrieval_strategy": "hybrid"}'
```

### Build Knowledge Graph

```bash
curl -X POST http://localhost:8000/api/v1/kg/build \
  -H "X-API-Key: your-secret-api-key-here" \
  -H "Content-Type: application/json" \
  -d '{"dataset_name": "docs"}'
```

***

## 6. Async Python Client (httpx)

```python
import asyncio
import httpx

BASE_URL = "http://localhost:8000"
API_KEY = "your-secret-api-key-here"
HEADERS = {"X-API-Key": API_KEY, "Content-Type": "application/json"}


async def ingest_files(dataset: str, paths: list[str]) -> dict:
    async with httpx.AsyncClient(timeout=300) as client:
        resp = await client.post(
            f"{BASE_URL}/api/v1/datasets/{dataset}/ingest",
            headers=HEADERS, json={"file_paths": paths},
        )
        resp.raise_for_status()
        return resp.json()


async def vector_search(dataset: str, query_vector: list[float], top_k: int = 5) -> dict:
    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.post(
            f"{BASE_URL}/api/v1/datasets/{dataset}/search/vector",
            headers=HEADERS, json={"query_vector": query_vector, "top_k": top_k},
        )
        resp.raise_for_status()
        return resp.json()


async def rag_query(question: str, dataset: str, strategy: str = "hybrid") -> dict:
    async with httpx.AsyncClient(timeout=120) as client:
        resp = await client.post(
            f"{BASE_URL}/api/v1/rag/query",
            headers=HEADERS,
            json={"question": question, "dataset_name": dataset,
                  "top_k": 5, "retrieval_strategy": strategy},
        )
        resp.raise_for_status()
        return resp.json()


async def build_kg(dataset: str) -> dict:
    async with httpx.AsyncClient(timeout=300) as client:
        resp = await client.post(
            f"{BASE_URL}/api/v1/kg/build",
            headers=HEADERS, json={"dataset_name": dataset},
        )
        resp.raise_for_status()
        task_id = resp.json()["task_id"]
        for _ in range(60):
            await asyncio.sleep(3)
            resp = await client.get(
                f"{BASE_URL}/api/v1/kg/build/{task_id}/status",
                headers=HEADERS,
            )
            status = resp.json()
            if status["status"] in ("completed", "failed"):
                return status
        return {"status": "timeout"}


async def main():
    result = await ingest_files("docs", ["examples/data/kb/knowledge.jsonl"])
    print(f"Ingestion complete: {result['total_rows']} rows")

    results = await vector_search("docs", [0.1] * 128)
    for item in results.get("results", [])[:3]:
        print(f"  [{item.get('score', 0):.3f}] {item.get('content', '')[:80]}...")

    answer = await rag_query("What is the architecture of Arrow Lake?", "docs")
    print(f"RAG answer: {answer.get('answer', '')[:200]}")

    kg_status = await build_kg("docs")
    print(f"KG build: {kg_status}")


asyncio.run(main())
```

***

## 7. Error Response Format

All error responses use a consistent JSON envelope:

```json
{
  "success": false,
  "error": "kg_build_failed",
  "message": "Knowledge graph build failed: connection refused",
  "context": {}
}
```

| Field     | Type     | Description                      |
| --------- | -------- | -------------------------------- |
| `success` | `bool`   | Always `false`                   |
| `error`   | `str`    | Machine-readable error code      |
| `message` | `str`    | Human-readable error description |
| `context` | `object` | Optional additional context      |

Common HTTP status codes: `400` validation failure, `401` not authenticated, `403` insufficient
permissions, `404` resource not found, `413` request body too large, `429` rate limited,
`500` internal server error.

***

## 8. Advanced Configuration

```yaml
# Rate limiting
rate_limit:
  enabled: true
  default_requests_per_minute: 60
  default_burst: 10

# CORS configuration
api:
  cors_origins:
    - "https://app.example.com"
    - "http://localhost:3000"

# Security response headers
  security_headers_enabled: true
  frame_options: "DENY"
```

Every request automatically receives an `X-Request-ID` header for distributed tracing. Clients
can also supply their own request ID (set `auto_generate_request_id: false` to disable automatic
generation).

---

## v1.4.0 New Endpoints

### Lineage Graph API

```bash
# Get full lineage graph for a dataset
curl http://localhost:8000/api/v1/lineage/graph/articles \
  -H "X-API-Key: your-key"

# Impact analysis: what downstream datasets are affected by a change
curl -X POST http://localhost:8000/api/v1/lineage/impact \
  -H "X-API-Key: your-key" \
  -H "Content-Type: application/json" \
  -d '{"dataset_name": "articles"}'

# Lineage statistics
curl http://localhost:8000/api/v1/lineage/stats \
  -H "X-API-Key: your-key"
```

### Quality Rules API

```bash
# Apply declarative quality rules to a dataset
curl -X POST http://localhost:8000/api/v1/datasets/articles/quality/rules \
  -H "X-API-Key: your-key" \
  -H "Content-Type: application/json" \
  -d '{
    "rules": [
      {"name": "min_len", "column": "text_content", "check": "length", "params": {"min": 10}, "action": "reject"},
      {"name": "no_dupes", "column": "text_content", "check": "duplicate", "action": "remove"},
      {"name": "score_range", "column": "score", "check": "range", "params": {"min": 0.0, "max": 1.0}, "action": "flag"}
    ]
  }'

# Response:
# {"success": true, "applied_rules": 3, "results": [...], "total_affected_rows": 42}
```

### Row/Column ACL Admin API

```bash
# Set column-level ACL (viewer can only see title and summary)
curl -X PUT http://localhost:8000/api/v1/admin/acl/articles \
  -H "X-API-Key: admin-key" \
  -H "Content-Type: application/json" \
  -d '{"role": "viewer", "visible_columns": ["title", "summary"]}'

# Set row-level ACL (viewer only sees US region data)
curl -X PUT http://localhost:8000/api/v1/admin/acl/sales \
  -H "X-API-Key: admin-key" \
  -H "Content-Type: application/json" \
  -d '{"role": "viewer", "row_filter": "region == US"}'

# List all ACLs for a dataset
curl http://localhost:8000/api/v1/admin/acl/articles \
  -H "X-API-Key: admin-key"

# Delete an ACL
curl -X DELETE http://localhost:8000/api/v1/admin/acl/articles/viewer \
  -H "X-API-Key: admin-key"
```

ACL filtering is automatically applied to all query and search endpoints — no client changes needed.

### FTS Pagination with Offset

```bash
# Full-text search with offset for pagination
curl -X POST http://localhost:8000/api/v1/datasets/articles/search/fts \
  -H "X-API-Key: your-key" \
  -H "Content-Type: application/json" \
  -d '{"query": "machine learning", "top_k": 10, "offset": 20}'

# Get results 21-30 for the query "machine learning"
```

### OLAP Query Streaming (SSE)

For large result sets (>10,000 rows), enable SSE streaming to receive results in batches:

```bash
# Stream OLAP results as SSE events
curl -N -X POST http://localhost:8000/api/v1/datasets/sales/query/olap \
  -H "X-API-Key: your-key" \
  -H "Content-Type: application/json" \
  -d '{"sql": "SELECT * FROM sales", "stream": true, "batch_size": 1000}'

# Response is SSE stream:
# data: {"type": "schema", "columns": [...], "row_count": 50000}
# data: {"type": "batch", "rows": 1000, "data": "<base64-arrow-ipc>"}
# data: {"type": "batch", "rows": 1000, "data": "<base64-arrow-ipc>"}
# ...
# data: {"type": "done", "total_rows": 50000}
```

Each `batch` event contains a base64-encoded Arrow IPC stream with `batch_size` rows (default 1000).

***

## v1.4.1 Gravitino 元数据端点

Arrow Lake integrates with **Apache Gravitino** for centralized metadata governance. When Gravitino is
enabled (`gravitino.enabled: true` in config), the `/metadata/*` endpoints proxy catalog, table, tag,
policy, statistics, and model information from the Gravitino metalake.

All metadata endpoints require API key authentication (`X-API-Key` header). When Gravitino is not
configured, these endpoints return **503 Service Unavailable**.

### Endpoint Reference

| Method   | Endpoint                            | Description                                |
| -------- | ----------------------------------- | ------------------------------------------ |
| `GET`    | `/metadata/catalogs`                | List all Gravitino catalogs                |
| `GET`    | `/metadata/tables`                  | List tables in the Lance catalog           |
| `GET`    | `/metadata/tables/{name}`           | Get table details (columns, properties)    |
| `GET`    | `/metadata/tags`                    | List tags (optional `?table=` filter)      |
| `POST`   | `/metadata/tags`                    | Create a new tag                           |
| `GET`    | `/metadata/policies`                | List all policies                          |
| `POST`   | `/metadata/policies/retention`      | Create a data retention policy             |
| `POST`   | `/metadata/policies/masking`        | Create a column masking policy             |
| `POST`   | `/metadata/statistics/{name}`       | Collect and register table statistics      |
| `GET`    | `/metadata/models`                  | List all registered ML models              |
| `GET`    | `/metadata/models/{name}/versions`  | Get model version info (latest/production) |

### curl Examples

```bash
# List all catalogs in the Gravitino metalake
curl http://localhost:8000/metadata/catalogs \
  -H "X-API-Key: your-key"
# => {"success": true, "data": [{"name": "lance-catalog"}, {"name": "hive-catalog"}],
#     "error": null, "metadata": {"total": 2}}

# List tables in the Lance catalog
curl http://localhost:8000/metadata/tables \
  -H "X-API-Key: your-key"
# => {"success": true, "data": [{"name": "articles"}, {"name": "sales"}],
#     "error": null, "metadata": {"total": 2}}

# Get table details (columns and properties)
curl http://localhost:8000/metadata/tables/articles \
  -H "X-API-Key: your-key"
# => {"success": true, "data": {"name": "articles",
#     "columns": [{"name": "id", "type": "int"}, ...],
#     "properties": {"format": "lance", "owner": "data-team"}},
#     "error": null, "metadata": {}}

# List tags for a specific table
curl "http://localhost:8000/metadata/tags?table=articles" \
  -H "X-API-Key: your-key"
# => {"success": true, "data": [{"name": "sensitive"}, {"name": "pii"}],
#     "error": null, "metadata": {"total": 2}}

# Create a new tag
curl -X POST "http://localhost:8000/metadata/tags?body=%7B%22name%22%3A%22sensitive%22%2C%22comment%22%3A%22Contains%20PII%20data%22%7D" \
  -H "X-API-Key: your-key"
# => {"success": true, "data": {"name": "sensitive"}, "error": null, "metadata": {}}

# List all policies
curl http://localhost:8000/metadata/policies \
  -H "X-API-Key: your-key"
# => {"success": true, "data": [{"name": "pii_retention"}, {"name": "email_mask"}],
#     "error": null, "metadata": {"total": 2}}

# Create a retention policy (retain data for 90 days)
curl -X POST "http://localhost:8000/metadata/policies/retention?body=%7B%22name%22%3A%22log_retention%22%2C%22days%22%3A90%7D" \
  -H "X-API-Key: your-key"
# => {"success": true, "data": {"name": "log_retention", "days": 90}, "error": null, "metadata": {}}

# Create a masking policy for specific columns
curl -X POST "http://localhost:8000/metadata/policies/masking?body=%7B%22name%22%3A%22email_mask%22%2C%22columns%22%3A%5B%22email%22%2C%22phone%22%5D%7D" \
  -H "X-API-Key: your-key"
# => {"success": true, "data": {"name": "email_mask", "columns": ["email", "phone"]},
#     "error": null, "metadata": {}}

# Collect and register statistics for a table
curl -X POST http://localhost:8000/metadata/statistics/articles \
  -H "X-API-Key: your-key"
# => {"success": true, "data": {"row_count": 10000, "columns": 8, ...},
#     "error": null, "metadata": {}}

# List registered ML models
curl http://localhost:8000/metadata/models \
  -H "X-API-Key: your-key"
# => {"success": true, "data": [{"name": "text-embedder"}, {"name": "image-classifier"}],
#     "error": null, "metadata": {"total": 2}}

# Get model version details
curl http://localhost:8000/metadata/models/text-embedder/versions \
  -H "X-API-Key: your-key"
# => {"success": true, "data": [
#       {"version": 3, "uri": "s3://models/text-embedder/v3", "aliases": ["latest"], "tier": "latest"},
#       {"version": 2, "uri": "s3://models/text-embedder/v2", "aliases": ["production"], "tier": "production"}
#     ], "error": null, "metadata": {"model": "text-embedder", "total": 2}}
```

### Python Client Examples

```python
import httpx

BASE_URL = "http://localhost:8000"
HEADERS = {"X-API-Key": "your-secret-api-key-here", "Content-Type": "application/json"}


def list_metadata_catalogs() -> dict:
    """List all Gravitino catalogs."""
    resp = httpx.get(f"{BASE_URL}/metadata/catalogs", headers=HEADERS, timeout=10)
    resp.raise_for_status()
    return resp.json()


def list_metadata_tables() -> dict:
    """List tables in the Lance catalog."""
    resp = httpx.get(f"{BASE_URL}/metadata/tables", headers=HEADERS, timeout=10)
    resp.raise_for_status()
    return resp.json()


def get_table_detail(name: str) -> dict:
    """Get table details including columns and properties."""
    resp = httpx.get(f"{BASE_URL}/metadata/tables/{name}", headers=HEADERS, timeout=10)
    resp.raise_for_status()
    return resp.json()


def list_tags(table: str | None = None) -> dict:
    """List tags, optionally filtered by table."""
    params = {"table": table} if table else {}
    resp = httpx.get(f"{BASE_URL}/metadata/tags", headers=HEADERS, params=params, timeout=10)
    resp.raise_for_status()
    return resp.json()


def create_tag(name: str, comment: str = "") -> dict:
    """Create a new tag in Gravitino."""
    import json
    body = json.dumps({"name": name, "comment": comment})
    resp = httpx.post(
        f"{BASE_URL}/metadata/tags",
        headers=HEADERS,
        params={"body": body},
        timeout=10,
    )
    resp.raise_for_status()
    return resp.json()


def list_policies() -> dict:
    """List all governance policies."""
    resp = httpx.get(f"{BASE_URL}/metadata/policies", headers=HEADERS, timeout=10)
    resp.raise_for_status()
    return resp.json()


def create_retention_policy(name: str, days: int = 30) -> dict:
    """Create a data retention policy."""
    import json
    body = json.dumps({"name": name, "days": days})
    resp = httpx.post(
        f"{BASE_URL}/metadata/policies/retention",
        headers=HEADERS,
        params={"body": body},
        timeout=10,
    )
    resp.raise_for_status()
    return resp.json()


def create_masking_policy(name: str, columns: list[str]) -> dict:
    """Create a column masking policy."""
    import json
    body = json.dumps({"name": name, "columns": columns})
    resp = httpx.post(
        f"{BASE_URL}/metadata/policies/masking",
        headers=HEADERS,
        params={"body": body},
        timeout=10,
    )
    resp.raise_for_status()
    return resp.json()


def collect_statistics(table_name: str) -> dict:
    """Collect and register table statistics."""
    resp = httpx.post(
        f"{BASE_URL}/metadata/statistics/{table_name}",
        headers=HEADERS,
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()


def list_models() -> dict:
    """List all registered ML models."""
    resp = httpx.get(f"{BASE_URL}/metadata/models", headers=HEADERS, timeout=10)
    resp.raise_for_status()
    return resp.json()


def get_model_versions(model_name: str) -> dict:
    """Get model version info (latest and production)."""
    resp = httpx.get(
        f"{BASE_URL}/metadata/models/{model_name}/versions",
        headers=HEADERS,
        timeout=10,
    )
    resp.raise_for_status()
    return resp.json()
```

### Gravitino Configuration

```yaml
# config.yaml — enable Gravitino integration
gravitino:
  enabled: true
  uri: "http://localhost:8090"
  metalake: "arrow_lake"
  lance_rest_enabled: true
  lance_rest_uri: "http://localhost:8888"
  sync_interval_seconds: 300    # Background sync interval
```
