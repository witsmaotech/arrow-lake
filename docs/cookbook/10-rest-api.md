# REST API Guide

Arrow Lake ships with a built-in FastAPI REST server that exposes HTTP interfaces for the full
range of platform capabilities — data ingestion, vector search, RAG question answering, and
knowledge graph management. It supports API Key authentication and a dual-mode JWT + RBAC system.

> Prerequisites: Install dependencies with `pip install arrow-lake[api]` and configure your
> authentication credentials.

***

## 1. Starting the Server

```bash
# Start with default settings (binds to 127.0.0.1:8000 since v1.5.2)
arrow-lake serve --host 0.0.0.0 --port 8000

# Start using a YAML configuration file
arrow-lake serve --config /path/to/config.yaml
```

> **Note (v1.5.2)**: The default bind address changed from `0.0.0.0` to `127.0.0.1` for security.
> To accept remote connections, explicitly set `--host 0.0.0.0` or configure `api.host: "0.0.0.0"`
> in your YAML config.

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
| `POST` | `/api/v1/datasets/{name}/search/ensemble` | Ensemble search (vector + FTS + facets) | VIEWER |

### RAG & Knowledge Graph (v2)

| Method | Endpoint                             | Description                 | Role   |
| ------ | ------------------------------------ | --------------------------- | ------ |
| `POST` | `/api/v1/rag/query`                  | RAG question answering      | -      |
| `POST` | `/api/v1/rag/query/stream`           | Streaming RAG               | -      |
| `POST` | `/api/v1/kg/build`                   | Build the knowledge graph (auto per-dataset) | ADMIN  |
| `GET`  | `/api/v1/kg/build/{task_id}/status`  | Build task status           | -      |
| `POST` | `/api/v1/kg/query`                   | Execute a Gremlin query     | EDITOR |
| `GET`  | `/api/v1/kg/entities/{id}/neighbors` | Neighbor traversal (`?dataset=`) | VIEWER |
| `GET`  | `/api/v1/kg/stats`                   | Graph statistics (`?dataset=`) | VIEWER |
| `DELETE` | `/api/v1/kg/graph`                 | Clear graph data (`?dataset=`) | ADMIN |
| `POST` | `/api/v1/kg/query/graphrag`          | GraphRAG question answering | VIEWER |
| `POST` | `/api/v1/kg/traversers/rays`         | Rays — non-cyclic paths (`dataset` in body) | VIEWER |
| `POST` | `/api/v1/kg/traversers/rings`        | Rings — cyclic paths        | VIEWER |
| `POST` | `/api/v1/kg/traversers/crosspoints`  | Crosspoints between pair    | VIEWER |
| `POST` | `/api/v1/kg/traversers/all-shortest-paths` | All shortest paths    | VIEWER |
| `POST` | `/api/v1/kg/traversers/weighted-shortest`  | Weighted shortest path | VIEWER |
| `POST` | `/api/v1/kg/traversers/single-source`      | Single-source shortest | VIEWER |
| `POST` | `/api/v1/kg/traversers/multi-node`         | Multi-node shortest    | VIEWER |
| `POST` | `/api/v1/kg/traversers/customized`         | Customized multi-step  | VIEWER |

### Authentication (v2)

| Method | Endpoint               | Description              |
| ------ | ---------------------- | ------------------------ |
| `POST` | `/api/v1/auth/token`   | Exchange API Key for JWT |
| `POST` | `/api/v1/auth/refresh` | Refresh a token          |
| `GET`  | `/api/v1/auth/me`      | Current user information |

***

## 4.5 RBAC Role Matrix (continued)

Over 180 of the 186 routes enforce role-based access control via the `require_role()` dependency.
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
  -d '{"file_paths": ["datas/reports/aigc_industry_report.pdf"]}'
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
    result = await ingest_files("aigc_articles", ["datas/reports/aigc_articles.csv"])
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
# => {"success": true, "data": [{"name": "aigc_articles"}, {"name": "ontime"}],
#     "error": null, "metadata": {"total": 2}}

# Get table details (columns and properties)
curl http://localhost:8000/metadata/tables/articles \
  -H "X-API-Key: your-key"
# => {"success": true, "data": {"name": "aigc_articles",
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
    resp = httpx.post(
        f"{BASE_URL}/metadata/tags",
        headers=HEADERS,
        json={"name": name, "comment": comment},
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
    resp = httpx.post(
        f"{BASE_URL}/metadata/policies/retention",
        headers=HEADERS,
        json={"name": name, "days": days},
        timeout=10,
    )
    resp.raise_for_status()
    return resp.json()


def create_masking_policy(name: str, columns: list[str]) -> dict:
    """Create a column masking policy."""
    resp = httpx.post(
        f"{BASE_URL}/metadata/policies/masking",
        headers=HEADERS,
        json={"name": name, "columns": columns},
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

***

## v1.5.x New Endpoints

The following endpoints were added in v1.5.x. They cover audit trail, backup/restore, maintenance,
system health, file upload, extended ingestion, schema migration, advanced query, lineage extensions,
knowledge graph extensions, export, and admin ACL management.

### Audit Trail API

| Method | Endpoint | Description | Auth |
|--------|----------|-------------|------|
| `POST` | `/api/v1/audit/record` | Record an audit event | EDITOR |
| `POST` | `/api/v1/audit/verify?audit_id=...` | Verify integrity of an audit entry | VIEWER |
| `GET` | `/api/v1/audit/query` | Query audit trail with filters | VIEWER |
| `POST` | `/api/v1/audit/export?dataset_name=...` | Export audit trail for a dataset | ADMIN |

#### Record Audit Event

```bash
curl -X POST http://localhost:8000/api/v1/audit/record \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "event_type": "read",
    "dataset_name": "users",
    "actor": "admin",
    "lance_version": 3,
    "metaflow_run_id": "run-42",
    "metaflow_tags": {"team": "data"},
    "payload": {"rows_affected": 100}
  }'
```

**Response:**

```json
{"success": true, "audit_id": "aud_abc123"}
```

#### Verify Audit Entry

```bash
curl -X POST "http://localhost:8000/api/v1/audit/verify?audit_id=aud_abc123" \
  -H "Authorization: Bearer $TOKEN"
```

**Response:**

```json
{"success": true, "intact": true}
```

#### Query Audit Trail

```bash
curl -G http://localhost:8000/api/v1/audit/query \
  -H "Authorization: Bearer $TOKEN" \
  --data-urlencode "dataset_name=users" \
  --data-urlencode "start=2025-01-01" \
  --data-urlencode "end=2025-12-31" \
  --data-urlencode "event_type=read"
```

**Response:**

```json
{"success": true, "entries": [{"event_type": "read", "actor": "admin", "timestamp": "..."}]}
```

#### Export Audit Trail

```bash
curl -X POST "http://localhost:8000/api/v1/audit/export?dataset_name=users" \
  -H "Authorization: Bearer $TOKEN"
```

**Response:**

```json
{"success": true, "export": {"dataset_name": "users", "entries": [...]}}
```

### Backup & Restore API

| Method | Endpoint | Description | Auth |
|--------|----------|-------------|------|
| `POST` | `/api/v1/backup/create` | Create a backup | ADMIN |
| `POST` | `/api/v1/backup/restore?backup_id=...` | Restore a backup by ID | ADMIN |
| `GET` | `/api/v1/backup/list` | List all backups | ADMIN |
| `DELETE` | `/api/v1/backup/{backup_id}` | Delete a backup | ADMIN |

#### Create Backup

```bash
curl -X POST http://localhost:8000/api/v1/backup/create \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "dataset_names": ["users", "orders"],
    "blob_prefixes": ["uploads/users/"],
    "backup_id": "backup-2025-06-01"
  }'
```

**Response:**

```json
{
  "backup_id": "backup-2025-06-01",
  "created_at": "2025-06-01T12:00:00Z",
  "datasets": ["users", "orders"],
  "blob_prefixes": ["uploads/users/"],
  "total_size_bytes": 5242880,
  "status": "completed"
}
```

#### Restore Backup

```bash
curl -X POST "http://localhost:8000/api/v1/backup/restore?backup_id=backup-2025-06-01" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "dataset_names": ["users"],
    "overwrite": true
  }'
```

**Response:**

```json
{
  "backup_id": "backup-2025-06-01",
  "created_at": "2025-06-01T12:00:00Z",
  "datasets": ["users"],
  "blob_prefixes": [],
  "total_size_bytes": 2097152,
  "status": "restored"
}
```

#### List Backups

```bash
curl http://localhost:8000/api/v1/backup/list \
  -H "Authorization: Bearer $TOKEN"
```

**Response:**

```json
{
  "backups": [
    {"backup_id": "backup-2025-06-01", "created_at": "...", "datasets": [...], "blob_prefixes": [], "total_size_bytes": 5242880, "status": "completed"}
  ],
  "count": 1
}
```

#### Delete Backup

```bash
curl -X DELETE http://localhost:8000/api/v1/backup/backup-2025-06-01 \
  -H "Authorization: Bearer $TOKEN"
```

**Response:**

```json
{"message": "Backup 'backup-2025-06-01' deleted"}
```

### Maintenance API

| Method | Endpoint | Description | Auth |
|--------|----------|-------------|------|
| `GET` | `/api/v1/admin/maintenance/status` | Get scheduler status | ADMIN |
| `POST` | `/api/v1/admin/maintenance/run` | Trigger a maintenance cycle | ADMIN |

#### Get Maintenance Status

```bash
curl http://localhost:8000/api/v1/admin/maintenance/status \
  -H "Authorization: Bearer $TOKEN"
```

**Response:**

```json
{
  "enabled": true,
  "last_run": "2025-06-01T06:00:00Z",
  "next_run": "2025-06-01T18:00:00Z",
  "interval_seconds": 43200,
  "last_report": {
    "datasets_compacted": 3,
    "datasets_cleaned": 5,
    "total_fragments_before": 120,
    "total_fragments_after": 45,
    "total_versions_removed": 80,
    "duration_seconds": 12.5
  }
}
```

#### Trigger Maintenance Run

```bash
curl -X POST http://localhost:8000/api/v1/admin/maintenance/run \
  -H "Authorization: Bearer $TOKEN"
```

**Response:**

```json
{
  "success": true,
  "data": {
    "datasets_compacted": 2,
    "datasets_cleaned": 1,
    "total_fragments_before": 40,
    "total_fragments_after": 15,
    "total_versions_removed": 25,
    "duration_seconds": 8.3
  }
}
```

### System & Health API

| Method | Endpoint | Description | Auth |
|--------|----------|-------------|------|
| `GET` | `/health/live` | Liveness probe | - |
| `GET` | `/health/ready` | Readiness probe (checks storage + deps) | - |
| `GET` | `/health` | Health check (backward compatible) | - |
| `GET` | `/metrics` | Prometheus metrics | - |
| `GET` | `/api/v1/version` | Version and dependency info | VIEWER |

#### Liveness Probe

```bash
curl http://localhost:8000/health/live
```

**Response:**

```json
{"status": "ok"}
```

#### Readiness Probe

```bash
curl http://localhost:8000/health/ready
```

**Response:**

```json
{
  "status": "ok",
  "version": "1.10.7",
  "storage": "accessible",
  "gravitino": "healthy",
  "duckdb_pool": {"pool_size": 5, "active_sessions": 1, "queued_requests": 0, "total_queries": 142, "total_errors": 0}
}
```

#### Version Info

```bash
curl http://localhost:8000/api/v1/version \
  -H "Authorization: Bearer $TOKEN"
```

**Response:**

```json
{
  "version": "1.10.7",
  "python": "3.12.4",
  "fastapi": "0.115.0",
  "uvicorn": "0.30.0",
  "pyarrow": "17.0.0",
  "duckdb": "1.0.0",
  "daft": "0.3.0",
  "httpx": "0.27.0"
}
```

### File Upload API

| Method | Endpoint | Description | Auth |
|--------|----------|-------------|------|
| `POST` | `/api/v1/datasets/{name}/upload` | Upload files to MinIO (proxy mode) | EDITOR |
| `POST` | `/api/v1/datasets/{name}/upload/presign` | Generate presigned PUT URLs | EDITOR |
| `DELETE` | `/api/v1/datasets/{name}/upload/cleanup` | Delete uploaded blobs for a dataset | EDITOR |

#### Upload Files (Proxy Mode)

```bash
curl -X POST http://localhost:8000/api/v1/datasets/my-data/upload \
  -H "Authorization: Bearer $TOKEN" \
  -F "files=@data.csv" \
  -F "files=@report.pdf"
```

**Response:**

```json
{
  "success": true,
  "blobs": [
    {"key": "uploads/my-data/a1b2c3d4_data.csv", "size_bytes": 4096, "content_type": "text/csv"},
    {"key": "uploads/my-data/e5f6g7h8_report.pdf", "size_bytes": 20480, "content_type": "application/pdf"}
  ]
}
```

#### Generate Presigned Upload URLs

```bash
curl -X POST http://localhost:8000/api/v1/datasets/my-data/upload/presign \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"filenames": ["data.csv", "report.pdf"]}'
```

**Response:**

```json
{
  "success": true,
  "uploads": [
    {"key": "uploads/my-data/a1b2c3d4_data.csv", "upload_url": "http://minio:9000/arrow-lake/uploads/...?X-Amz-Signature=..."},
    {"key": "uploads/my-data/e5f6g7h8_report.pdf", "upload_url": "http://minio:9000/arrow-lake/uploads/...?X-Amz-Signature=..."}
  ]
}
```

#### Cleanup Uploaded Blobs

```bash
curl -X DELETE http://localhost:8000/api/v1/datasets/my-data/upload/cleanup \
  -H "Authorization: Bearer $TOKEN"
```

**Response:**

```json
{"success": true, "deleted_count": 3}
```

### Extended Ingestion Endpoints

| Method | Endpoint | Description | Auth |
|--------|----------|-------------|------|
| `POST` | `/api/v1/datasets/{name}/ingest/sql` | Ingest from SQL database | EDITOR |
| `POST` | `/api/v1/datasets/{name}/ingest/kafka` | Ingest from Kafka topics | EDITOR |
| `POST` | `/api/v1/datasets/{name}/ingest/iceberg` | Ingest from Apache Iceberg | EDITOR |
| `POST` | `/api/v1/datasets/{name}/ingest/deltalake` | Ingest from Delta Lake | EDITOR |
| `POST` | `/api/v1/datasets/{name}/ingest/videos` | Ingest video files with keyframes | EDITOR |
| `POST` | `/api/v1/datasets/{name}/ingest/mixed` | Ingest mixed-modality sources | EDITOR |

#### Ingest from SQL Database

```bash
curl -X POST http://localhost:8000/api/v1/datasets/orders/ingest/sql \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "sql": "SELECT * FROM public.orders WHERE created_at >= '\''2025-01-01'\''",
    "connection_url": "postgresql://user:pass@db:5432/mydb",
    "partition_col": "created_at",
    "num_partitions": 4,
    "transforms": [{"op": "select", "columns": ["id", "amount", "created_at"]}]
  }'
```

**Response:**

```json
{"success": true, "total_rows": 10000, "total_files": 4, "sources": [{"path": "sql://public.orders", "row_count": 10000, "file_count": 4}]}
```

#### Ingest from Kafka

```bash
curl -X POST http://localhost:8000/api/v1/datasets/events/ingest/kafka \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "bootstrap_servers": "kafka:9092",
    "topics": ["user-events", "order-events"],
    "start": "earliest",
    "end": "latest",
    "json_decode": true
  }'
```

**Response:**

```json
{"success": true, "total_rows": 50000, "total_files": 2, "sources": [{"path": "kafka://user-events", "row_count": 30000, "file_count": 1}]}
```

#### Ingest from Apache Iceberg

```bash
curl -X POST http://localhost:8000/api/v1/datasets/iceberg_data/ingest/iceberg \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "table_uri": "s3://warehouse/db.table",
    "transforms": null
  }'
```

**Response:**

```json
{"success": true, "total_rows": 20000, "total_files": 1, "sources": [{"path": "s3://warehouse/db.table", "row_count": 20000, "file_count": 1}]}
```

#### Ingest from Delta Lake

```bash
curl -X POST http://localhost:8000/api/v1/datasets/delta_data/ingest/deltalake \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "table_uri": "s3://delta-lake/sales",
    "version": 5
  }'
```

**Response:**

```json
{"success": true, "total_rows": 15000, "total_files": 1, "sources": [{"path": "s3://delta-lake/sales", "row_count": 15000, "file_count": 1}]}
```

#### Ingest Videos

```bash
curl -X POST http://localhost:8000/api/v1/datasets/video-clips/ingest/videos \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "file_paths": ["videos/intro.mp4", "videos/demo.webm"],
    "blob_keys": []
  }'
```

**Response:**

```json
{"success": true, "total_rows": 24, "total_files": 2, "sources": [{"path": "videos/intro.mp4", "row_count": 12, "file_count": 1}]}
```

#### Ingest Mixed-Modality Sources

```bash
curl -X POST http://localhost:8000/api/v1/datasets/multimodal/ingest/mixed \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "sources": {
      "files": ["data/report.csv"],
      "urls": ["https://example.com/data.json"],
      "images": ["images/photo.jpg"],
      "videos": ["videos/clip.mp4"]
    },
    "blob_keys": {}
  }'
```

**Response:**

```json
{"success": true, "total_rows": 120, "total_files": 4, "sources": [{"path": "data/report.csv", "row_count": 100, "file_count": 1}]}
```

### Schema Migration API

| Method | Endpoint | Description | Auth |
|--------|----------|-------------|------|
| `POST` | `/api/v1/datasets/{name}/schema/migrate` | Validate/apply schema migration | ADMIN |

#### Migrate Dataset Schema

```bash
# Dry-run: validate only (default)
curl -X POST http://localhost:8000/api/v1/datasets/users/schema/migrate \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "actions": [
      {"operation": "add_column", "column_name": "region", "sql_expr": "\'\''unknown'\''"},
      {"operation": "alter_column", "column_name": "score", "new_type": "float64"},
      {"operation": "drop_column", "column_name": "legacy_field"}
    ],
    "dry_run": true
  }'
```

**Response (dry_run):**

```json
{"success": true, "dry_run": true, "issues": [], "applied_count": 0}
```

```bash
# Apply the migration
curl -X POST http://localhost:8000/api/v1/datasets/users/schema/migrate \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "actions": [
      {"operation": "add_column", "column_name": "region", "sql_expr": "\'\''unknown'\''"},
      {"operation": "alter_column", "column_name": "score", "new_type": "float64"}
    ],
    "dry_run": false
  }'
```

**Response:**

```json
{"success": true, "dry_run": false, "issues": [], "applied_count": 2}
```

### Export API

| Method | Endpoint | Description | Auth |
|--------|----------|-------------|------|
| `POST` | `/api/v1/datasets/{name}/export` | Export to Parquet/CSV (async) | EDITOR |
| `GET` | `/api/v1/datasets/{name}/export/{task_id}/status` | Check export task status | VIEWER |
| `GET` | `/api/v1/datasets/{name}/export/{task_id}/download` | Download exported file | VIEWER |
| `POST` | `/api/v1/datasets/{name}/export-to` | Export to external target (sync) | EDITOR |

#### Export Dataset (Async)

```bash
curl -X POST http://localhost:8000/api/v1/datasets/users/export \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "output_path": "users_export.parquet",
    "format": "parquet",
    "columns": ["id", "name", "email"],
    "compression": "zstd",
    "overwrite": false
  }'
```

**Response:**

```json
{"success": true, "task_id": "exp_abc123", "dataset_name": "users", "status": "pending", "message": "Export task queued"}
```

#### Check Export Status

```bash
curl http://localhost:8000/api/v1/datasets/users/export/exp_abc123/status \
  -H "Authorization: Bearer $TOKEN"
```

**Response:**

```json
{"success": true, "task_id": "exp_abc123", "status": "completed", "progress": 1.0, "created_at": "2025-06-01T12:00:00Z", "completed_at": "2025-06-01T12:00:05Z", "error": null, "result": {"file_size_bytes": 102400}}
```

#### Download Exported File

```bash
curl -O http://localhost:8000/api/v1/datasets/users/export/exp_abc123/download \
  -H "Authorization: Bearer $TOKEN"
```

Returns the file as a binary download (`application/octet-stream` or `text/csv`).

#### Export to External Target (Sync)

```bash
curl -X POST http://localhost:8000/api/v1/datasets/users/export-to \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "target_uri": "s3://warehouse/exports/users/",
    "format": "parquet",
    "options": {"compression": "zstd"}
  }'
```

**Response:**

```json
{"success": true, "rows_exported": 10000}
```

Supported export formats: `parquet`, `csv`, `json`, `iceberg`, `clickhouse`.

### Lineage API (Extended)

The lineage endpoints below supplement those documented in the v1.4.0 section.

| Method | Endpoint | Description | Auth |
|--------|----------|-------------|------|
| `POST` | `/api/v1/lineage/record` | Record a lineage event | EDITOR |
| `GET` | `/api/v1/lineage/history/{dataset_name}` | Get lineage history | VIEWER |
| `POST` | `/api/v1/lineage/query` | Query lineage via SQL | VIEWER |
| `GET` | `/api/v1/lineage/graph/{dataset_name}` | Get lineage graph (json/mermaid/dot) | VIEWER |
| `POST` | `/api/v1/lineage/impact` | Downstream impact analysis | VIEWER |
| `GET` | `/api/v1/lineage/stats` | Lineage tracking statistics | VIEWER |

#### Record Lineage Event

```bash
curl -X POST "http://localhost:8000/api/v1/lineage/record?dataset_name=orders_enriched" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "operation": "transform",
    "source_datasets": ["orders", "users"],
    "transform_type": "join",
    "actor": "etl-pipeline",
    "metadata": {"pipeline": "daily-enrich"}
  }'
```

**Response:**

```json
{"success": true, "message": "Lineage event recorded for dataset 'orders_enriched'"}
```

#### Get Lineage History

```bash
curl http://localhost:8000/api/v1/lineage/history/orders_enriched \
  -H "Authorization: Bearer $TOKEN"
```

**Response:**

```json
{
  "success": true,
  "dataset_name": "orders_enriched",
  "events": [{"operation": "transform", "source_datasets": ["orders", "users"], "timestamp": "..."}]
}
```

#### Query Lineage via SQL

```bash
curl -X POST http://localhost:8000/api/v1/lineage/query \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"sql": "SELECT * FROM lineage_events WHERE operation = '\''join'\''"}'
```

**Response:**

```json
{"success": true, "data": [{"dataset_name": "orders_enriched", "operation": "transform"}]}
```

#### Get Lineage Graph

```bash
# JSON format (default)
curl http://localhost:8000/api/v1/lineage/graph/orders_enriched \
  -H "Authorization: Bearer $TOKEN"

# Mermaid format
curl "http://localhost:8000/api/v1/lineage/graph/orders_enriched?format=mermaid&max_depth=5" \
  -H "Authorization: Bearer $TOKEN"

# Graphviz DOT format
curl "http://localhost:8000/api/v1/lineage/graph/orders_enriched?format=dot" \
  -H "Authorization: Bearer $TOKEN"
```

**Response (JSON):**

```json
{
  "success": true,
  "dataset_name": "orders_enriched",
  "nodes": [{"id": "orders", "depth": 0, "type": "source"}, {"id": "orders_enriched", "depth": 1, "type": "target"}],
  "edges": [{"from": "orders", "to": "orders_enriched", "operation": "transform", "transform_type": "join"}],
  "stats": {"total_nodes": 3, "total_edges": 2, "max_depth": 2}
}
```

Query parameters: `max_depth` (1-20, default 10), `format` (`json`|`mermaid`|`dot`, default `json`).

#### Downstream Impact Analysis

```bash
curl -X POST http://localhost:8000/api/v1/lineage/impact \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"dataset_name": "orders"}'
```

**Response:**

```json
{
  "success": true,
  "source_dataset": "orders",
  "impacted_datasets": [
    {"dataset": "orders_enriched", "depth": 1, "operation": "transform", "transform_type": "join"},
    {"dataset": "daily_report", "depth": 2, "operation": "aggregate", "transform_type": "groupby"}
  ]
}
```

#### Lineage Statistics

```bash
curl http://localhost:8000/api/v1/lineage/stats \
  -H "Authorization: Bearer $TOKEN"
```

**Response:**

```json
{"success": true, "total_datasets_tracked": 12, "total_events": 47}
```

### Knowledge Graph API (Extended)

The following endpoints supplement the KG endpoints documented in Section 4.

| Method | Endpoint | Description | Auth |
|--------|----------|-------------|------|
| `GET` | `/api/v1/kg/schema` | Get graph schema (vertex/edge labels) | VIEWER |
| `GET` | `/api/v1/kg/stats` | Get graph statistics | VIEWER |
| `DELETE` | `/api/v1/kg/graph` | Delete all graph data | ADMIN |

#### Get Graph Schema

```bash
curl http://localhost:8000/api/v1/kg/schema \
  -H "Authorization: Bearer $TOKEN"
```

**Response:**

```json
{"vertex_labels": ["Entity", "Concept", "Document"], "edge_labels": ["RELATED_TO", "MENTIONS", "DERIVED_FROM"]}
```

#### Get Graph Statistics

```bash
curl http://localhost:8000/api/v1/kg/stats \
  -H "Authorization: Bearer $TOKEN"
```

**Response:**

```json
{"total_vertices": 1024, "total_edges": 3580, "graph_enabled": true}
```

#### Delete Graph Data

```bash
curl -X DELETE http://localhost:8000/api/v1/kg/graph \
  -H "Authorization: Bearer $TOKEN"
```

**Response:**

```json
{"status": "ok", "message": "Graph data deleted"}
```

### Query API (OLAP / Metadata / Daft)

| Method | Endpoint | Description | Auth |
|--------|----------|-------------|------|
| `POST` | `/api/v1/datasets/{name}/query/olap` | OLAP SQL via DuckDB (with SSE streaming) | EDITOR |
| `POST` | `/api/v1/datasets/{name}/query/metadata` | Metadata SQL query (semantic alias) | EDITOR |
| `POST` | `/api/v1/datasets/{name}/query/daft` | Daft DataFrame chained operations | VIEWER |

#### OLAP Query

```bash
curl -X POST http://localhost:8000/api/v1/datasets/sales/query/olap \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "sql": "SELECT region, SUM(amount) AS total FROM sales GROUP BY region ORDER BY total DESC",
    "max_rows": 10000,
    "format": "json",
    "stream": false
  }'
```

**Response:**

```json
{"success": true, "format": "json", "row_count": 5, "column_count": 2, "meta": {"sql": "SELECT ..."}, "rows": [{"region": "US", "total": 150000}]}
```

#### OLAP Query with SSE Streaming

```bash
curl -N -X POST http://localhost:8000/api/v1/datasets/sales/query/olap \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"sql": "SELECT * FROM sales", "stream": true, "batch_size": 1000}'
```

Streams SSE events:

```text
data: {"type": "schema", "columns": ["id", "region", "amount"], "row_count": 50000}
data: {"type": "batch", "rows": 1000, "data": "<base64-arrow-ipc>"}
data: {"type": "done", "total_rows": 50000}
```

#### Metadata SQL Query

```bash
curl -X POST http://localhost:8000/api/v1/datasets/sales/query/metadata \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"sql": "SELECT column_name, data_type FROM information_schema.columns", "format": "json"}'
```

**Response:**

```json
{"success": true, "format": "json", "row_count": 8, "column_count": 2, "meta": {"sql": "SELECT ..."}, "rows": [...]}
```

#### Daft DataFrame Query

```bash
curl -X POST http://localhost:8000/api/v1/datasets/sales/query/daft \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "columns": ["region", "amount"],
    "filters": [{"column": "amount", "op": "gt", "value": 100}],
    "sort": {"column": "amount", "desc": true},
    "groupby": {"columns": ["region"], "agg": "sum"},
    "limit": 100,
    "format": "json"
  }'
```

**Response:**

```json
{"success": true, "format": "json", "row_count": 5, "column_count": 2, "rows": [{"region": "US", "amount": 150000}], "warnings": []}
```

Supported pipeline operations (applied in order): `sort` -> `filters` -> `groupby` -> `sql` -> `pivot` -> `explode` -> `sample` -> `distinct` -> `columns` -> `offset` -> `limit`.

### Authentication API (Extended)

| Method | Endpoint | Description | Auth |
|--------|----------|-------------|------|
| `POST` | `/api/v1/auth/token` | Exchange credentials for JWT | - |
| `POST` | `/api/v1/auth/refresh` | Refresh access token | - |
| `GET` | `/api/v1/auth/me` | Get current user info | VIEWER |
| `POST` | `/api/v1/auth/logout` | Revoke current token | VIEWER |

#### Logout / Revoke Token

```bash
curl -X POST http://localhost:8000/api/v1/auth/logout \
  -H "Authorization: Bearer $TOKEN"
```

**Response:**

```json
{"message": "Token revoked"}
```

### Admin ACL Management (Extended)

The following endpoints supplement the row/column ACL endpoints documented in v1.4.0.

#### Schema-Level ACL

| Method | Endpoint | Description | Auth |
|--------|----------|-------------|------|
| `PUT` | `/api/v1/admin/acl/schema/{schema_name}` | Set schema-level ACL | ADMIN |
| `GET` | `/api/v1/admin/acl/schema/{schema_name}` | List schema-level ACLs | ADMIN |
| `DELETE` | `/api/v1/admin/acl/schema/{schema_name}/{role}` | Delete schema-level ACL | ADMIN |

##### Set Schema-Level ACL

```bash
curl -X PUT http://localhost:8000/api/v1/admin/acl/schema/analytics \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "role": "viewer",
    "allowed_actions": ["read", "search"],
    "denied_actions": ["export", "delete"]
  }'
```

**Response:**

```json
{"schema_name": "analytics", "role": "viewer", "allowed_actions": ["read", "search"], "denied_actions": ["delete", "export"]}
```

##### List Schema-Level ACLs

```bash
curl http://localhost:8000/api/v1/admin/acl/schema/analytics \
  -H "Authorization: Bearer $TOKEN"
```

**Response:**

```json
{
  "schema_name": "analytics",
  "acls": [
    {"schema_name": "analytics", "role": "viewer", "allowed_actions": ["read", "search"], "denied_actions": ["delete", "export"]}
  ]
}
```

##### Delete Schema-Level ACL

```bash
curl -X DELETE http://localhost:8000/api/v1/admin/acl/schema/analytics/viewer \
  -H "Authorization: Bearer $TOKEN"
```

**Response:**

```json
{"schema_name": "analytics", "role": "viewer", "allowed_actions": [], "denied_actions": []}
```

#### Explicit Deny Rules

| Method | Endpoint | Description | Auth |
|--------|----------|-------------|------|
| `PUT` | `/api/v1/admin/deny/{dataset}` | Add explicit deny for an action | ADMIN |
| `DELETE` | `/api/v1/admin/deny/{dataset}/{action}` | Remove explicit deny | ADMIN |
| `GET` | `/api/v1/admin/deny/{dataset}` | List denied actions for a dataset | ADMIN |

##### Add Explicit Deny

```bash
curl -X PUT http://localhost:8000/api/v1/admin/deny/sensitive_data \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"action": "export"}'
```

**Response:**

```json
{"dataset": "sensitive_data", "action": "export", "denied": true}
```

##### Remove Explicit Deny

```bash
curl -X DELETE http://localhost:8000/api/v1/admin/deny/sensitive_data/export \
  -H "Authorization: Bearer $TOKEN"
```

**Response:**

```json
{"dataset": "sensitive_data", "action": "export", "denied": false}
```

##### List Denied Actions

```bash
curl http://localhost:8000/api/v1/admin/deny/sensitive_data \
  -H "Authorization: Bearer $TOKEN"
```

**Response:**

```json
{"dataset": "sensitive_data", "denied_actions": ["delete", "export"]}
```

### Storage Lifecycle API (Removed)

> **Deprecated**: The `/api/v1/lifecycle/*` endpoints (`apply`/`status`/`restore`/`rules`/`estimate`)
> were **removed from the REST server in v1.9.x** — no corresponding router is registered in the
> codebase anymore. Archive/expire/restore lifecycle policies are now managed via the CLI
> (`arrow-lake lifecycle ...`) and the background maintenance scheduler (`/admin/maintenance/*`).
> Calls to the old endpoints return 404.

***

## v1.9.x New Endpoints

The endpoints below were added in v1.6–v1.9.6. The current v1.10.0 surface exposes **~190 routes
across 23 routers** (system, datasets, search, query, export, quality, cleaning, embedding, embed,
lineage, materialized, audit, backup, rag, kg, extraction_templates, doc_type_categories, auth,
admin, maintenance, gravitino, async_tasks, user_state). They cover user state, user & token
management, multimodal embedding, materialized views, field annotation, quality enhancements, index
management, cleaning, async tasks, KG templates/versions, and RAG enhancements. The v1.10.0
additions (extraction-template management, doc-type categories) are documented in the next section.

### User-State API (`/api/v1/me/*`)

Personal-state endpoints for the current user. **Requires a personal token** (passed via `X-API-Key`
or Bearer as a personal token, *not* a JWT access token; JWT calls to `/me/*` return 401).
Role requirement: VIEWER.

| Method  | Endpoint                                       | Description                          |
| ------- | ---------------------------------------------- | ------------------------------------ |
| `POST`  | `/api/v1/me/saved-queries`                     | Save a query                         |
| `GET`   | `/api/v1/me/saved-queries`                     | List saved queries                   |
| `DELETE`| `/api/v1/me/saved-queries/{qid}`               | Delete a saved query                 |
| `GET`   | `/api/v1/me/notifications`                     | List notifications                   |
| `POST`  | `/api/v1/me/notifications/read`                | Mark notifications read (`?notification_id=`) |
| `GET`   | `/api/v1/me/preferences`                       | Get preferences                      |
| `PUT`   | `/api/v1/me/preferences`                       | Update preferences                   |
| `POST`  | `/api/v1/me/dashboards`                        | Save a dashboard layout              |
| `GET`   | `/api/v1/me/dashboards`                        | List dashboards                      |
| `DELETE`| `/api/v1/me/dashboards/{dashboard_id}`         | Delete a dashboard                   |
| `POST`  | `/api/v1/me/favorites`                         | Add a favorite (idempotent)          |
| `GET`   | `/api/v1/me/favorites`                         | List favorites                       |
| `DELETE`| `/api/v1/me/favorites/{target_type}/{target_id}` | Remove a favorite                  |

```bash
# Mark a single notification read
curl -X POST "http://localhost:8000/api/v1/me/notifications/read?notification_id=42" \
  -H "X-API-Key: <personal-token>"
```

### User & Token Management (admin extensions)

Supplements the row/column ACL endpoints from v1.4.0. **Requires ADMIN role.**

| Method  | Endpoint                                        | Description                          |
| ------- | ----------------------------------------------- | ------------------------------------ |
| `GET`   | `/api/v1/admin/users`                           | List all users                       |
| `POST`  | `/api/v1/admin/users`                           | Create user (`CreateUserRequest`)    |
| `PUT`   | `/api/v1/admin/users/{user_id}`                 | Update user fields                   |
| `DELETE`| `/api/v1/admin/users/{user_id}`                 | Deactivate user (soft delete)        |
| `GET`   | `/api/v1/admin/roles`                           | List roles + permission matrix       |
| `POST`  | `/api/v1/admin/users/{user_id}/tokens`          | Issue a personal token               |
| `GET`   | `/api/v1/admin/users/{user_id}/tokens`          | List a user's tokens                 |
| `DELETE`| `/api/v1/admin/users/{user_id}/tokens/{token_id}` | Revoke a token                     |

```bash
# Create a user
curl -X POST http://localhost:8000/api/v1/admin/users \
  -H "Authorization: Bearer $ADMIN_TOKEN" -H "Content-Type: application/json" \
  -d '{"username": "alice", "email": "alice@example.com", "role": "viewer", "password": "change-me-12"}'

# Issue a personal token for a user (the returned token is usable on /me/* endpoints)
curl -X POST http://localhost:8000/api/v1/admin/users/3/tokens \
  -H "Authorization: Bearer $ADMIN_TOKEN"
```

### Multimodal Embedding (`/api/v1/embed/*`)

The `embed_router` provides standalone text/image embedding computation for cross-modal retrieval
(text-to-image, image-to-image search).

| Method | Endpoint                  | Description                                              |
| ------ | ------------------------- | -------------------------------------------------------- |
| `POST` | `/api/v1/embed/text`      | Text embeddings (local model or external API)            |
| `POST` | `/api/v1/embed/image`     | Image embeddings (CLIP/SigLIP; JSON body `{"images":["<base64>"]}`) |
| `POST` | `/api/v1/embed/clip-text` | CLIP text embeddings (text-to-image: shared vector space) |

```bash
# Text-to-image: embed the text query, then run vector search against an image dataset
curl -X POST http://localhost:8000/api/v1/embed/clip-text \
  -H "X-API-Key: your-key" -H "Content-Type: application/json" \
  -d '{"texts": ["a red car"]}'
```

### Materialized Views (`/api/v1/materialized/*`)

DuckLake materialized-view management. **All endpoints return 503 when `ducklake_enabled=false`
(default).** All endpoints require ADMIN.

| Method  | Endpoint                          | Description                                  |
| ------- | --------------------------------- | -------------------------------------------- |
| `GET`   | `/api/v1/materialized`            | List all materialized views                  |
| `DELETE`| `/api/v1/materialized/{view}`     | Drop a materialized view (404 if not found)  |
| `POST`  | `/api/v1/materialized/cleanup`    | Clean up stale materialized views            |

### Field Annotation (schema annotate)

Writes human-readable comments onto Arrow field metadata for a dataset's schema (the ingest hook and
the annotate endpoint share the same key).

| Method | Endpoint                                        | Description                              | Role   |
| ------ | ----------------------------------------------- | ---------------------------------------- | ------ |
| `GET`  | `/api/v1/datasets/{name}/schema`                | Get schema (includes field `comment`)    | VIEWER |
| `POST` | `/api/v1/datasets/{name}/schema/annotate`       | Set a field comment (body: `field`+`comment`) | ADMIN |

```bash
curl -X POST http://localhost:8000/api/v1/datasets/users/schema/annotate \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"field": "email", "comment": "User login email (PII)"}'
```

### Quality Enhancement Endpoints

Supplements the v1.4.0 `quality/rules` endpoint (full quality pipeline).

| Method | Endpoint                                         | Description                      | Role   |
| ------ | ------------------------------------------------ | -------------------------------- | ------ |
| `POST` | `/api/v1/datasets/{name}/quality/filter`         | Filter rows by expression        | EDITOR |
| `GET`  | `/api/v1/datasets/{name}/quality/report`         | Produce a quality report         | VIEWER |
| `POST` | `/api/v1/datasets/{name}/quality/deduplicate`    | Deduplicate (similarity/exact)   | EDITOR |
| `GET`  | `/api/v1/datasets/{name}/quality/profile`        | Column distribution profile      | VIEWER |
| `POST` | `/api/v1/datasets/{name}/quality/llm_label`      | LLM labeling (classification)    | EDITOR |
| `POST` | `/api/v1/datasets/{name}/quality/extract`        | LLM structured extraction        | EDITOR |
| `POST` | `/api/v1/datasets/{name}/quality/mask-preview`   | Masking preview (reads first 5 rows, returns before/after) | EDITOR |

```bash
# Masking preview: no write-back, just shows before/after for the first 5 rows
curl -X POST http://localhost:8000/api/v1/datasets/users/quality/mask-preview \
  -H "Authorization: Bearer $TOKEN"
```

### Index Management Endpoints

| Method  | Endpoint                                        | Description                              | Role   |
| ------- | ----------------------------------------------- | ---------------------------------------- | ------ |
| `POST`  | `/api/v1/datasets/{name}/index/vector`          | Create vector index (IVF_PQ; auto ≥256 rows) | EDITOR |
| `POST`  | `/api/v1/datasets/{name}/index/fts`             | Create full-text index (BM25 + optional jieba column) | EDITOR |
| `POST`  | `/api/v1/datasets/{name}/index/scalar`          | Create scalar index (BTREE/BITMAP)       | EDITOR |
| `POST`  | `/api/v1/datasets/{name}/index/facets`          | Create facet index                       | EDITOR |
| `GET`   | `/api/v1/datasets/{name}/index`                 | List all indices                         | VIEWER |
| `DELETE`| `/api/v1/datasets/{name}/index/{index_name}`    | Drop an index                            | EDITOR |

### Cleaning (semantic write-back)

Compiles declarative cleaning steps into DuckDB SQL and writes back via `restore_dataset`
(for structured datasets).

| Method | Endpoint                          | Description                                              | Role   |
| ------ | --------------------------------- | -------------------------------------------------------- | ------ |
| `POST` | `/api/v1/datasets/{name}/clean`   | Semantic clean (body: `steps`/`filters`/`write_back`/`limit`) | EDITOR |

```bash
curl -X POST http://localhost:8000/api/v1/datasets/users/clean \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"steps": [{"op": "trim", "column": "name"},
                   {"op": "lowercase", "column": "email"}], "write_back": true}'
```

### Async Tasks (`/api/v1/tasks/*`)

Async variants of long-running operations (ingest/index/backup). All endpoints require VIEWER
(creation endpoints require the role of the underlying operation).

| Method | Endpoint                                            | Description                          |
| ------ | --------------------------------------------------- | ------------------------------------ |
| `GET`  | `/api/v1/tasks`                                     | List tasks (includes history, status filter) |
| `GET`  | `/api/v1/tasks/{task_id}/status`                    | Query task status                    |
| `POST` | `/api/v1/datasets/{name}/ingest/async`              | Async ingest local files             |
| `POST` | `/api/v1/datasets/{name}/ingest/documents/async`    | Async ingest PDF/documents           |
| `POST` | `/api/v1/datasets/{name}/index/vector/async`        | Async create vector index            |
| `POST` | `/api/v1/datasets/{name}/index/fts/async`           | Async create full-text index         |
| `POST` | `/api/v1/backup/create/async`                       | Async create backup                  |
| `POST` | `/api/v1/backup/restore/async`                      | Async restore backup                 |

### Knowledge Graph Templates & Versions

Supplements the KG endpoints in Section 4. The template path exposes hyper-extract's multi-template
capability; KA versions support incremental builds, rollback, and pruning.

| Method  | Endpoint                                  | Description                                  | Role   |
| ------- | ----------------------------------------- | -------------------------------------------- | ------ |
| `GET`   | `/api/v1/kg/doc-types`                    | List available doc types (resolve to templates) | VIEWER |
| `GET`   | `/api/v1/kg/templates`                    | List all templates                           | VIEWER |
| `GET`   | `/api/v1/kg/templates/{template_path}`    | Get template detail (`{template_path:path}`) | VIEWER |
| `GET`   | `/api/v1/kg/ka-versions/{dataset}`        | List KA versions for a dataset               | VIEWER |
| `POST`  | `/api/v1/kg/ka-rollback`                  | Roll back to a specified KA version          | ADMIN  |
| `POST`  | `/api/v1/kg/ka-prune`                     | Prune old KA dumps                          | ADMIN  |
| `POST`  | `/api/v1/kg/build`                        | body supports `incremental:true` (incremental; falls back to full when no KA dump) | ADMIN |
| `POST`  | `/api/v1/kg/ask/stream`                   | Streaming KG Q&A (SSE)                      | VIEWER |
| `POST`  | `/api/v1/kg/query/graphrag`               | GraphRAG Q&A (body: `question`+`dataset`)    | VIEWER |
| `POST`  | `/api/v1/kg/search`                       | KG entity search                            | VIEWER |
| `POST`  | `/api/v1/kg/rebuild-index`                | Rebuild KA embedding index                  | ADMIN  |
| `POST`  | `/api/v1/kg/export-obsidian`              | Export to an Obsidian vault                 | VIEWER |

```bash
# Incremental KG build (process only new chunks, reuse existing KA dump)
curl -X POST http://localhost:8000/api/v1/kg/build \
  -H "X-API-Key: your-key" -H "Content-Type: application/json" \
  -d '{"dataset_name": "docs", "incremental": true}'
```

### RAG Enhancements (`/api/v1/rag/*`)

The RAG request body uses **`question`** and **`dataset_name`** (not `query`/`dataset`).
`use_kg: bool` (default `true`) enables per-query control of GraphRAG augmentation — no need to
disable the global `hugegraph.enabled` flag.

| Method | Endpoint                  | Description                                                                       |
| ------ | ------------------------- | --------------------------------------------------------------------------------- |
| `POST` | `/api/v1/rag/query`       | RAG Q&A (body: `question`/`dataset_name`/`top_k`/`retrieval_strategy`/`use_kg`)  |
| `POST` | `/api/v1/rag/query/stream`| Streaming RAG (SSE; citations first, then content)                               |
| `POST` | `/api/v1/rag/extract`     | RAG extract (returns retrieval + extraction only, no generated answer)            |
| `GET`  | `/api/v1/rag/templates`   | List available prompt templates                                                   |

```bash
# Disable GraphRAG for an A/B comparison with pure vector/hybrid retrieval
curl -X POST http://localhost:8000/api/v1/rag/query \
  -H "X-API-Key: your-key" -H "Content-Type: application/json" \
  -d '{"question": "What is the architecture?", "dataset_name": "docs", "use_kg": false, "retrieval_strategy": "hybrid"}'
```

### v1.5.2 Security Hardening Notes

The following security improvements were applied in v1.5.2:

| Area | Hardening |
|------|-----------|
| **JWT** | Empty `jwt_secret_key` now blocks server startup |
| **Kerberos** | Command injection vectors eliminated in auth provider |
| **SQL** | All user-facing queries use parameterized execution |
| **Redis** | Default password removed; must be explicitly configured |
| **Network** | All ports bind to `127.0.0.1` by default |
| **SSRF** | URL validation on `ingest_http` and presign endpoints |
| **Admin** | Role enum replaces string-based admin bypass |
| **Tokens** | Refresh token rotation with revocation support |
| **Gremlin** | Input sanitization on `kg_query` endpoint |

These hardening measures are enforced automatically — no configuration changes needed.

***

## v1.10.0 New Endpoints

v1.10.0 adds a dynamic **extraction-template management** surface and a **category ↔ doc_type
dictionary**, plus template-aware KG build. Templates are loaded dynamically — no rebuild or restart
needed (`reset_gallery_cache` picks up new presets); all state lives in the system_db (libSQL).

### Extraction-Template Management (`/api/v1/admin/extraction-templates/*`)

Admin-only (Role.ADMIN). CRUD, AI generation, dry-run, dataset binding, and a **quality-validation
harness** that builds a throwaway dataset → ingests a sample doc → builds the KG → runs RAG → cleans up.

| Method   | Endpoint                                              | Description                                              |
| -------- | ----------------------------------------------------- | -------------------------------------------------------- |
| `GET`    | `/api/v1/admin/extraction-templates`                  | List templates (optional `?category=`)                   |
| `GET`    | `/api/v1/admin/extraction-templates/{name}`           | Template detail                                          |
| `POST`   | `/api/v1/admin/extraction-templates`                  | Create a template (201)                                  |
| `PUT`    | `/api/v1/admin/extraction-templates/{name}`           | Update a template                                        |
| `DELETE` | `/api/v1/admin/extraction-templates/{name}`           | Delete a template                                        |
| `POST`   | `/api/v1/admin/extraction-templates/validate`         | Validate YAML schema before save                         |
| `POST`   | `/api/v1/admin/extraction-templates/generate`         | AI-generate a template from a doc sample + doc_type      |
| `POST`   | `/api/v1/admin/extraction-templates/dry-run`          | Dry-run extract on a sample (no persistence)             |
| `POST`   | `/api/v1/admin/extraction-templates/{name}/quality/doc`    | Quality harness: generate sample doc                |
| `POST`   | `/api/v1/admin/extraction-templates/{name}/quality/build`   | Quality harness: build graph + viz + RAG           |
| `DELETE` | `/api/v1/admin/extraction-templates/quality/{temp_dataset}` | Quality harness: cleanup throwaway dataset         |
| `GET`    | `/api/v1/admin/extraction-templates/{name}/quality/history` | Quality run history                                |
| `PUT`    | `/api/v1/admin/extraction-templates/default`          | Set the default template                                 |
| `GET`    | `/api/v1/admin/extraction-templates/{name}/usage`     | Where a template is bound                                |
| `GET`    | `/api/v1/admin/extraction-templates/bindings/{dataset}`    | Get a dataset's binding                            |
| `PUT`    | `/api/v1/admin/extraction-templates/bindings/{dataset}`    | Bind a dataset to a template                       |
| `DELETE` | `/api/v1/admin/extraction-templates/bindings/{dataset}`    | Clear a dataset's binding                          |

```bash
# Bind a dataset to a template, then build with it (template auto-resolves at build time)
curl -X PUT http://localhost:8000/api/v1/admin/extraction-templates/bindings/aigc_report \
  -H "Authorization: Bearer $TOKEN" -H "X-API-Key: $KEY" \
  -H "Content-Type: application/json" -d '{"template": "project_concept_graph"}'

curl -X POST http://localhost:8000/api/v1/kg/build \
  -H "Authorization: Bearer $TOKEN" -H "X-API-Key: $KEY" \
  -H "Content-Type: application/json" -d '{"dataset": "aigc_report"}'   # template resolved from binding
```

### Doc-Type Category Dictionary (`/api/v1/admin/doc-type-categories`)

Admin-only. Manages the dynamic category → doc_type dictionary that backs `GET /api/v1/kg/doc-types`.
`DOC_TYPE_ALIASES` ships 10 canonical keys (paper/report/manual/biography/finance/legal/medicine/
industry/tcm/general); `project` and custom keys are added through this endpoint. A template's
`category` is required and must exist in the dictionary.

| Method   | Endpoint                                  | Description                       |
| -------- | ----------------------------------------- | --------------------------------- |
| `GET`    | `/api/v1/admin/doc-type-categories`       | List all categories               |
| `POST`   | `/api/v1/admin/doc-type-categories`       | Create a category (201)           |
| `DELETE` | `/api/v1/admin/doc-type-categories/{name}` | Delete a category                |

### Template-Aware KG Build & GraphRAG

| Method | Endpoint                          | Description                                              |
| ------ | --------------------------------- | -------------------------------------------------------- |
| `POST` | `/api/v1/kg/build`                | Build KG; body adds `template` (overrides doc_type routing) and `incremental` |
| `GET`  | `/api/v1/kg/build/{task_id}/status` | Poll build progress (chunks/entities/relations)       |
| `GET`  | `/api/v1/kg/doc-types`            | Dynamic doc_type list (canonical + dictionary + resolved template) |
| `POST` | `/api/v1/kg/query/graphrag`       | GraphRAG Q&A (body: `question` + `dataset`)              |

> See cookbook examples: SDK [`examples/46_template_management.py`](examples/46_template_management.py), [`examples/48_graphrag_relation_qa.py`](examples/48_graphrag_relation_qa.py); REST [`examples_api/34_extraction_templates_api.py`](examples_api/34_extraction_templates_api.py), [`examples_api/36_graphrag_relation_qa_api.py`](examples_api/36_graphrag_relation_qa_api.py).
> for end-to-end SDK + REST flows.
