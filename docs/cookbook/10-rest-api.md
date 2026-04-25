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
curl -X POST http://localhost:8000/api/v2/auth/token \
  -H "X-API-Key: your-secret-api-key-here" \
  -H "Content-Type: application/json" \
  -d '{"role": "editor"}'
# => {"access_token": "eyJ...", "refresh_token": "eyJ...", "token_type": "bearer"}

# Access protected endpoints with a Bearer token
curl -X POST http://localhost:8000/api/v2/rag/query \
  -H "Authorization: Bearer eyJhbGciOiJIUzI1NiIs..." \
  -H "Content-Type: application/json" \
  -d '{"question": "...", "dataset_name": "docs"}'

# Refresh the token
curl -X POST http://localhost:8000/api/v2/auth/refresh \
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

| Method | Endpoint                                 | Description      |
| ------ | ---------------------------------------- | ---------------- |
| `POST` | `/api/v1/datasets/{name}/search/vector`  | Vector search    |
| `POST` | `/api/v1/datasets/{name}/search/fts`     | Full-text search |
| `POST` | `/api/v1/datasets/{name}/search/hybrid`  | Hybrid search    |
| `POST` | `/api/v1/datasets/{name}/search/faceted` | Faceted search   |

### RAG & Knowledge Graph (v2)

| Method | Endpoint                             | Description                 | Role   |
| ------ | ------------------------------------ | --------------------------- | ------ |
| `POST` | `/api/v2/rag/query`                  | RAG question answering      | -      |
| `POST` | `/api/v2/rag/query/stream`           | Streaming RAG               | -      |
| `POST` | `/api/v2/kg/build`                   | Build the knowledge graph   | ADMIN  |
| `GET`  | `/api/v2/kg/build/{task_id}/status`  | Build task status           | -      |
| `POST` | `/api/v2/kg/query`                   | Execute a Gremlin query     | EDITOR |
| `GET`  | `/api/v2/kg/entities/{id}/neighbors` | Neighbor traversal          | -      |
| `POST` | `/api/v2/kg/query/graphrag`          | GraphRAG question answering | VIEWER |

### Authentication (v2)

| Method | Endpoint               | Description              |
| ------ | ---------------------- | ------------------------ |
| `POST` | `/api/v2/auth/token`   | Exchange API Key for JWT |
| `POST` | `/api/v2/auth/refresh` | Refresh a token          |
| `GET`  | `/api/v2/auth/me`      | Current user information |

***

## 5. curl Examples

### Ingest Files

```bash
curl -X POST http://localhost:8000/api/v1/datasets/docs/ingest \
  -H "X-API-Key: your-secret-api-key-here" \
  -H "Content-Type: application/json" \
  -d '{"file_paths": ["docs/cookbook/datas/papers/full_text/p001_attention_is_all_you_need.pdf", "docs/cookbook/datas/papers/full_text/p002_bert_pretraining.pdf"]}'
# => {"success": true, "dataset_name": "docs", "total_rows": 156, "total_files": 2, ...}
```

### Vector Search

```bash
curl -X POST http://localhost:8000/api/v1/datasets/docs/search/vector \
  -H "X-API-Key: your-secret-api-key-here" \
  -H "Content-Type: application/json" \
  -d '{"query": "What vector index types does Arrow Lake support?", "top_k": 5}'
```

### RAG Question Answering

```bash
curl -X POST http://localhost:8000/api/v2/rag/query \
  -H "X-API-Key: your-secret-api-key-here" \
  -H "Content-Type: application/json" \
  -d '{"question": "How does the RAG pipeline work?", "dataset_name": "docs", "top_k": 5, "retrieval_strategy": "hybrid"}'
```

### Build Knowledge Graph

```bash
curl -X POST http://localhost:8000/api/v2/kg/build \
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


async def vector_search(dataset: str, query: str, top_k: int = 5) -> dict:
    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.post(
            f"{BASE_URL}/api/v1/datasets/{dataset}/search/vector",
            headers=HEADERS, json={"query": query, "top_k": top_k},
        )
        resp.raise_for_status()
        return resp.json()


async def rag_query(question: str, dataset: str, strategy: str = "hybrid") -> dict:
    async with httpx.AsyncClient(timeout=120) as client:
        resp = await client.post(
            f"{BASE_URL}/api/v2/rag/query",
            headers=HEADERS,
            json={"question": question, "dataset_name": dataset,
                  "top_k": 5, "retrieval_strategy": strategy},
        )
        resp.raise_for_status()
        return resp.json()


async def build_kg(dataset: str) -> dict:
    async with httpx.AsyncClient(timeout=300) as client:
        resp = await client.post(
            f"{BASE_URL}/api/v2/kg/build",
            headers=HEADERS, json={"dataset_name": dataset},
        )
        resp.raise_for_status()
        task_id = resp.json()["task_id"]
        for _ in range(60):
            await asyncio.sleep(3)
            resp = await client.get(
                f"{BASE_URL}/api/v2/kg/build/{task_id}/status",
                headers=HEADERS,
            )
            status = resp.json()
            if status["status"] in ("completed", "failed"):
                return status
        return {"status": "timeout"}


async def main():
    result = await ingest_files("docs", ["docs/cookbook/datas/kb/knowledge.jsonl"])
    print(f"Ingestion complete: {result['total_rows']} rows")

    results = await vector_search("docs", "vector index types")
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
