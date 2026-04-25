# REST API 使用指南

Arrow Lake 内置 FastAPI REST 服务，提供数据摄取、向量搜索、RAG 问答、
知识图谱管理等全部功能的 HTTP 接口。支持 API Key 认证和 JWT + RBAC 双模式。

> 前置准备：安装依赖 `pip install arrow-lake[api]`，配置认证信息。

***

## 1. 启动服务器

```bash
# 默认配置启动 (绑定 0.0.0.0:8000)
arrow-lake serve --host 0.0.0.0 --port 8000

# 使用 YAML 配置文件启动
arrow-lake serve --config /path/to/config.yaml
```

启动后访问 Swagger UI: `http://localhost:8000/docs`，ReDoc: `http://localhost:8000/redoc`。

***

## 2. API Key 认证

在配置中设置 `api.api_key` 后，所有非文档端点需要通过 `X-API-Key` 请求头认证。

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

未携带 API Key 时返回 401：`{"detail": "Missing or invalid API key"}`

***

## 3. JWT 认证与 RBAC

认证模式通过 `auth.auth_mode` 配置，可选 `api_key`、`jwt` 或 `both`。

### 角色层级

| 角色       | 说明  | 权限范围               |
| -------- | --- | ------------------ |
| `ADMIN`  | 管理员 | 全部操作，包括图谱构建、数据集删除  |
| `EDITOR` | 编辑者 | 数据摄取、搜索、Gremlin 查询 |
| `VIEWER` | 查看者 | 只读搜索、RAG 问答        |

### 获取与使用令牌

```bash
# 在 both 模式下，用 API Key 换取 JWT 令牌对
curl -X POST http://localhost:8000/api/v2/auth/token \
  -H "X-API-Key: your-secret-api-key-here" \
  -H "Content-Type: application/json" \
  -d '{"role": "editor"}'
# => {"access_token": "eyJ...", "refresh_token": "eyJ...", "token_type": "bearer"}

# 携带 Bearer Token 访问受保护端点
curl -X POST http://localhost:8000/api/v2/rag/query \
  -H "Authorization: Bearer eyJhbGciOiJIUzI1NiIs..." \
  -H "Content-Type: application/json" \
  -d '{"question": "...", "dataset_name": "docs"}'

# 刷新令牌
curl -X POST http://localhost:8000/api/v2/auth/refresh \
  -H "Content-Type: application/json" \
  -d '{"refresh_token": "eyJ..."}'
```

### JWT 配置参考

| 配置项                             | 默认值            | 说明                               |
| ------------------------------- | -------------- | -------------------------------- |
| `auth.auth_mode`                | `"api_key"`    | 认证模式：`api_key` / `jwt` / `both` |
| `auth.jwt_secret_key`           | `""`           | JWT 签名密钥                         |
| `auth.jwt_algorithm`            | `"HS256"`      | 签名算法                             |
| `auth.jwt_access_token_minutes` | `30`           | Access Token 有效期（分钟）             |
| `auth.jwt_refresh_token_days`   | `7`            | Refresh Token 有效期（天）             |
| `auth.jwt_issuer`               | `"arrow-lake"` | JWT issuer 声明                    |

***

## 4. 核心端点速查表

### 数据集与摄取 (v1)

| 方法       | 端点                                         | 说明       | 角色     |
| -------- | ------------------------------------------ | -------- | ------ |
| `GET`    | `/api/v1/datasets`                         | 列出数据集    | -      |
| `GET`    | `/api/v1/datasets/{name}`                  | 数据集详情    | -      |
| `DELETE` | `/api/v1/datasets/{name}`                  | 删除数据集    | EDITOR |
| `POST`   | `/api/v1/datasets/{name}/ingest`           | 摄取本地文件   | EDITOR |
| `POST`   | `/api/v1/datasets/{name}/ingest/http`      | 摄取远程 URL | EDITOR |
| `POST`   | `/api/v1/datasets/{name}/ingest/images`    | 摄取图片     | EDITOR |
| `POST`   | `/api/v1/datasets/{name}/ingest/documents` | 摄取 PDF   | EDITOR |

### 搜索 (v1)

| 方法     | 端点                                       | 说明   |
| ------ | ---------------------------------------- | ---- |
| `POST` | `/api/v1/datasets/{name}/search/vector`  | 向量搜索 |
| `POST` | `/api/v1/datasets/{name}/search/fts`     | 全文搜索 |
| `POST` | `/api/v1/datasets/{name}/search/hybrid`  | 混合搜索 |
| `POST` | `/api/v1/datasets/{name}/search/faceted` | 分面搜索 |

### RAG 与知识图谱 (v2)

| 方法     | 端点                                   | 说明          | 角色     |
| ------ | ------------------------------------ | ----------- | ------ |
| `POST` | `/api/v2/rag/query`                  | RAG 问答      | -      |
| `POST` | `/api/v2/rag/query/stream`           | 流式 RAG      | -      |
| `POST` | `/api/v2/kg/build`                   | 构建知识图谱      | ADMIN  |
| `GET`  | `/api/v2/kg/build/{task_id}/status`  | 构建状态        | -      |
| `POST` | `/api/v2/kg/query`                   | Gremlin 查询  | EDITOR |
| `GET`  | `/api/v2/kg/entities/{id}/neighbors` | 邻居遍历        | -      |
| `POST` | `/api/v2/kg/query/graphrag`          | GraphRAG 问答 | VIEWER |

### 认证 (v2)

| 方法     | 端点                     | 说明             |
| ------ | ---------------------- | -------------- |
| `POST` | `/api/v2/auth/token`   | API Key 换取 JWT |
| `POST` | `/api/v2/auth/refresh` | 刷新令牌           |
| `GET`  | `/api/v2/auth/me`      | 当前用户信息         |

***

## 5. curl 示例

### 摄取文件

```bash
curl -X POST http://localhost:8000/api/v1/datasets/docs/ingest \
  -H "X-API-Key: your-secret-api-key-here" \
  -H "Content-Type: application/json" \
  -d '{"file_paths": ["docs/cookbook/datas/papers/full_text/zh001_大语言模型知识图谱构建综述.pdf", "docs/cookbook/datas/papers/full_text/zh002_向量数据库技术选型与实践.pdf"]}'
# => {"success": true, "dataset_name": "docs", "total_rows": 156, "total_files": 2, ...}
```

### 向量搜索

```bash
curl -X POST http://localhost:8000/api/v1/datasets/docs/search/vector \
  -H "X-API-Key: your-secret-api-key-here" \
  -H "Content-Type: application/json" \
  -d '{"query": "Arrow Lake 支持哪些向量索引类型？", "top_k": 5}'
```

### RAG 问答

```bash
curl -X POST http://localhost:8000/api/v2/rag/query \
  -H "X-API-Key: your-secret-api-key-here" \
  -H "Content-Type: application/json" \
  -d '{"question": "RAG 管线是如何工作的？", "dataset_name": "docs", "top_k": 5, "retrieval_strategy": "hybrid"}'
```

### 构建知识图谱

```bash
curl -X POST http://localhost:8000/api/v2/kg/build \
  -H "X-API-Key: your-secret-api-key-here" \
  -H "Content-Type: application/json" \
  -d '{"dataset_name": "docs"}'
```

***

## 6. Python httpx 异步调用

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
    result = await ingest_files("docs", ["docs/cookbook/datas/kb/knowledge_zh.jsonl"])
    print(f"摄取完成：{result['total_rows']} 行")

    results = await vector_search("docs", "向量索引类型")
    for item in results.get("results", [])[:3]:
        print(f"  [{item.get('score', 0):.3f}] {item.get('content', '')[:80]}...")

    answer = await rag_query("Arrow Lake 的架构是什么？", "docs")
    print(f"RAG 回答：{answer.get('answer', '')[:200]}")

    kg_status = await build_kg("docs")
    print(f"图谱构建：{kg_status}")


asyncio.run(main())
```

***

## 7. 错误响应格式

所有错误响应使用统一的 JSON 信封：

```json
{
  "success": false,
  "error": "kg_build_failed",
  "message": "Knowledge graph build failed: connection refused",
  "context": {}
}
```

| 字段        | 类型       | 说明          |
| --------- | -------- | ----------- |
| `success` | `bool`   | 始终为 `false` |
| `error`   | `str`    | 错误码（机器可读）   |
| `message` | `str`    | 人类可读的错误描述   |
| `context` | `object` | 可选附加上下文     |

常见 HTTP 状态码：`400` 参数校验失败、`401` 未认证、`403` 权限不足、
`404` 资源不存在、`413` 请求体过大、`429` 速率限制、`500` 服务内部异常。

***

## 8. 高级配置

```yaml
# 速率限制
rate_limit:
  enabled: true
  default_requests_per_minute: 60
  default_burst: 10

# CORS 跨域
api:
  cors_origins:
    - "https://app.example.com"
    - "http://localhost:3000"

# 安全响应头
  security_headers_enabled: true
  frame_options: "DENY"
```

每个请求自动生成 `X-Request-ID` 头用于链路追踪。
客户端也可自行传入请求 ID（设置 `auto_generate_request_id: false` 可关闭自动生成）。
