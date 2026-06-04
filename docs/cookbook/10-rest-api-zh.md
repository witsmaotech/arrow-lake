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
curl -X POST http://localhost:8000/api/v1/auth/token \
  -H "X-API-Key: your-secret-api-key-here" \
  -H "Content-Type: application/json" \
  -d '{"role": "editor"}'
# => {"access_token": "eyJ...", "refresh_token": "eyJ...", "token_type": "bearer"}

# 携带 Bearer Token 访问受保护端点
curl -X POST http://localhost:8000/api/v1/rag/query \
  -H "Authorization: Bearer eyJhbGciOiJIUzI1NiIs..." \
  -H "Content-Type: application/json" \
  -d '{"question": "...", "dataset_name": "docs"}'

# 刷新令牌
curl -X POST http://localhost:8000/api/v1/auth/refresh \
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
| `POST` | `/api/v1/rag/query`                  | RAG 问答      | -      |
| `POST` | `/api/v1/rag/query/stream`           | 流式 RAG      | -      |
| `POST` | `/api/v1/kg/build`                   | 构建知识图谱      | ADMIN  |
| `GET`  | `/api/v1/kg/build/{task_id}/status`  | 构建状态        | -      |
| `POST` | `/api/v1/kg/query`                   | Gremlin 查询  | EDITOR |
| `GET`  | `/api/v1/kg/entities/{id}/neighbors` | 邻居遍历        | -      |
| `POST` | `/api/v1/kg/query/graphrag`          | GraphRAG 问答 | VIEWER |

### 认证 (v2)

| 方法     | 端点                     | 说明             |
| ------ | ---------------------- | -------------- |
| `POST` | `/api/v1/auth/token`   | API Key 换取 JWT |
| `POST` | `/api/v1/auth/refresh` | 刷新令牌           |
| `GET`  | `/api/v1/auth/me`      | 当前用户信息         |

***

## 5. curl 示例

### 摄取文件

```bash
curl -X POST http://localhost:8000/api/v1/datasets/docs/ingest \
  -H "X-API-Key: your-secret-api-key-here" \
  -H "Content-Type: application/json" \
  -d '{"file_paths": ["examples/data/papers/full_text/zh001_大语言模型知识图谱构建综述.pdf", "examples/data/papers/full_text/zh002_向量数据库技术选型与实践.pdf"]}'
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
curl -X POST http://localhost:8000/api/v1/rag/query \
  -H "X-API-Key: your-secret-api-key-here" \
  -H "Content-Type: application/json" \
  -d '{"question": "RAG 管线是如何工作的？", "dataset_name": "docs", "top_k": 5, "retrieval_strategy": "hybrid"}'
```

### 构建知识图谱

```bash
curl -X POST http://localhost:8000/api/v1/kg/build \
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
    result = await ingest_files("docs", ["examples/data/kb/knowledge_zh.jsonl"])
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

***

## 4.5 RBAC 角色矩阵（续）

超过 30 个 API 端点通过 `require_role()` 依赖项执行基于角色的访问控制。
角色层级为 **ADMIN > EDITOR > VIEWER** —— 每个高级角色继承其下所有角色的权限。

### 各角色端点访问权限

| 能力类别           | VIEWER        | EDITOR                        | ADMIN                          |
| ---------------- | ------------- | ----------------------------- | ------------------------------ |
| **搜索与查询**    | search/\*     | （继承 VIEWER）               | （继承所有）                   |
| **RAG**          | rag/query/\*  | （继承 VIEWER）               | （继承所有）                   |
| **GraphRAG**     | graphrag      | （继承 VIEWER）               | （继承所有）                   |
| **数据摄取**     | -             | ingest/\*, datasets DELETE    | （继承所有）                   |
| **向量嵌入**     | -             | embedding/\*                  | （继承所有）                   |
| **质量与去重**   | -             | quality/\*, dedup/\*          | （继承所有）                   |
| **血缘与审计**   | -             | lineage 写入                  | audit 导出                     |
| **导出**         | -             | export/\*                     | （继承所有）                   |
| **备份**         | -             | -                             | backup 创建 / 恢复 / 删除     |
| **知识图谱构建** | -             | kg/query                      | kg/build, admin/\*             |
| **数据集 ACL 管理** | -          | -                             | 授予 / 撤销数据集访问权限     |

### 速查参考

- **VIEWER**：`search/*`、`rag/query`、`kg/query/graphrag`、`kg/entities/*/neighbors`、`kg/build/*/status`
- **EDITOR**：所有 VIEWER 端点 + `ingest/*`、`datasets/{name} DELETE`、`embedding/*`、`quality/*`、`export/*`、`kg/query`、`lineage 写入`
- **ADMIN**：所有 EDITOR 端点 + `kg/build`、`backup/*`、`admin/*`、`audit/export`、数据集 ACL 管理

`PermissionChecker` 支持按数据集的 ACL 覆盖 —— ADMIN 可向 VIEWER 授予特定数据集的写入权限，而无需更改其全局角色。完整权限矩阵实现详见 `arrow_lake.api.rbac`。

***

## v1.4.0 新增端点

### 血缘图谱 API

```bash
# 获取数据集的完整血缘图谱
curl http://localhost:8000/api/v1/lineage/graph/articles \
  -H "X-API-Key: your-key"

# 影响分析：某个变更会影响哪些下游数据集
curl -X POST http://localhost:8000/api/v1/lineage/impact \
  -H "X-API-Key: your-key" \
  -H "Content-Type: application/json" \
  -d '{"dataset_name": "articles"}'

# 血缘统计信息
curl http://localhost:8000/api/v1/lineage/stats \
  -H "X-API-Key: your-key"
```

### 质量规则 API

```bash
# 对数据集应用声明式质量规则
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

# 响应：
# {"success": true, "applied_rules": 3, "results": [...], "total_affected_rows": 42}
```

### 行/列级 ACL 管理 API

```bash
# 设置列级 ACL（viewer 只能看到 title 和 summary）
curl -X PUT http://localhost:8000/api/v1/admin/acl/articles \
  -H "X-API-Key: admin-key" \
  -H "Content-Type: application/json" \
  -d '{"role": "viewer", "visible_columns": ["title", "summary"]}'

# 设置行级 ACL（viewer 只能看到 US 区域数据）
curl -X PUT http://localhost:8000/api/v1/admin/acl/sales \
  -H "X-API-Key: admin-key" \
  -H "Content-Type: application/json" \
  -d '{"role": "viewer", "row_filter": "region == US"}'

# 列出数据集的所有 ACL
curl http://localhost:8000/api/v1/admin/acl/articles \
  -H "X-API-Key: admin-key"

# 删除 ACL
curl -X DELETE http://localhost:8000/api/v1/admin/acl/articles/viewer \
  -H "X-API-Key: admin-key"
```

ACL 过滤会自动应用于所有查询和搜索端点 —— 客户端无需改动。

### 全文搜索分页（Offset）

```bash
# 使用 offset 进行全文搜索分页
curl -X POST http://localhost:8000/api/v1/datasets/articles/search/fts \
  -H "X-API-Key: your-key" \
  -H "Content-Type: application/json" \
  -d '{"query": "machine learning", "top_k": 10, "offset": 20}'

# 获取查询 "machine learning" 的第 21-30 条结果
```

### OLAP 查询流式传输（SSE）

对于大型结果集（>10,000 行），启用 SSE 流式传输可分批接收结果：

```bash
# 以 SSE 事件流式传输 OLAP 结果
curl -N -X POST http://localhost:8000/api/v1/datasets/sales/query/olap \
  -H "X-API-Key: your-key" \
  -H "Content-Type: application/json" \
  -d '{"sql": "SELECT * FROM sales", "stream": true, "batch_size": 1000}'

# 响应为 SSE 流：
# data: {"type": "schema", "columns": [...], "row_count": 50000}
# data: {"type": "batch", "rows": 1000, "data": "<base64-arrow-ipc>"}
# data: {"type": "batch", "rows": 1000, "data": "<base64-arrow-ipc>"}
# ...
# data: {"type": "done", "total_rows": 50000}
```

每个 `batch` 事件包含一个 base64 编码的 Arrow IPC 流，包含 `batch_size` 行（默认 1000）。

***

## v1.4.1 Gravitino 元数据端点

Arrow Lake 集成了 **Apache Gravitino** 实现集中化元数据治理。当 Gravitino 启用时（配置中设置 `gravitino.enabled: true`），`/metadata/*` 端点会代理来自 Gravitino metalake 的 catalog、table、tag、policy、statistics 和 model 信息。

所有元数据端点需要 API Key 认证（`X-API-Key` 请求头）。当 Gravitino 未配置时，这些端点返回 **503 Service Unavailable**。

### 端点参考

| 方法      | 端点                                | 说明                                      |
| ------- | --------------------------------- | --------------------------------------- |
| `GET`   | `/metadata/catalogs`              | 列出所有 Gravitino catalog                |
| `GET`   | `/metadata/tables`                | 列出 Lance catalog 中的表                   |
| `GET`   | `/metadata/tables/{name}`         | 获取表详情（列、属性）                            |
| `GET`   | `/metadata/tags`                  | 列出标签（可选 `?table=` 过滤）                |
| `POST`  | `/metadata/tags`                  | 创建新标签                                   |
| `GET`   | `/metadata/policies`              | 列出所有策略                                  |
| `POST`  | `/metadata/policies/retention`    | 创建数据保留策略                                |
| `POST`  | `/metadata/policies/masking`      | 创建列脱敏策略                                 |
| `POST`  | `/metadata/statistics/{name}`     | 收集并注册表统计信息                              |
| `GET`   | `/metadata/models`                | 列出所有已注册的 ML 模型                         |
| `GET`   | `/metadata/models/{name}/versions` | 获取模型版本信息（latest/production）          |

### curl 示例

```bash
# 列出 Gravitino metalake 中的所有 catalog
curl http://localhost:8000/metadata/catalogs \
  -H "X-API-Key: your-key"
# => {"success": true, "data": [{"name": "lance-catalog"}, {"name": "hive-catalog"}],
#     "error": null, "metadata": {"total": 2}}

# 列出 Lance catalog 中的表
curl http://localhost:8000/metadata/tables \
  -H "X-API-Key: your-key"
# => {"success": true, "data": [{"name": "articles"}, {"name": "sales"}],
#     "error": null, "metadata": {"total": 2}}

# 获取表详情（列和属性）
curl http://localhost:8000/metadata/tables/articles \
  -H "X-API-Key: your-key"
# => {"success": true, "data": {"name": "articles",
#     "columns": [{"name": "id", "type": "int"}, ...],
#     "properties": {"format": "lance", "owner": "data-team"}},
#     "error": null, "metadata": {}}

# 列出指定表的标签
curl "http://localhost:8000/metadata/tags?table=articles" \
  -H "X-API-Key: your-key"
# => {"success": true, "data": [{"name": "sensitive"}, {"name": "pii"}],
#     "error": null, "metadata": {"total": 2}}

# 创建新标签
curl -X POST "http://localhost:8000/metadata/tags?body=%7B%22name%22%3A%22sensitive%22%2C%22comment%22%3A%22Contains%20PII%20data%22%7D" \
  -H "X-API-Key: your-key"
# => {"success": true, "data": {"name": "sensitive"}, "error": null, "metadata": {}}

# 列出所有策略
curl http://localhost:8000/metadata/policies \
  -H "X-API-Key: your-key"
# => {"success": true, "data": [{"name": "pii_retention"}, {"name": "email_mask"}],
#     "error": null, "metadata": {"total": 2}}

# 创建保留策略（保留数据 90 天）
curl -X POST "http://localhost:8000/metadata/policies/retention?body=%7B%22name%22%3A%22log_retention%22%2C%22days%22%3A90%7D" \
  -H "X-API-Key: your-key"
# => {"success": true, "data": {"name": "log_retention", "days": 90}, "error": null, "metadata": {}}

# 为特定列创建脱敏策略
curl -X POST "http://localhost:8000/metadata/policies/masking?body=%7B%22name%22%3A%22email_mask%22%2C%22columns%22%3A%5B%22email%22%2C%22phone%22%5D%7D" \
  -H "X-API-Key: your-key"
# => {"success": true, "data": {"name": "email_mask", "columns": ["email", "phone"]},
#     "error": null, "metadata": {}}

# 收集并注册表的统计信息
curl -X POST http://localhost:8000/metadata/statistics/articles \
  -H "X-API-Key: your-key"
# => {"success": true, "data": {"row_count": 10000, "columns": 8, ...},
#     "error": null, "metadata": {}}

# 列出已注册的 ML 模型
curl http://localhost:8000/metadata/models \
  -H "X-API-Key: your-key"
# => {"success": true, "data": [{"name": "text-embedder"}, {"name": "image-classifier"}],
#     "error": null, "metadata": {"total": 2}}

# 获取模型版本详情
curl http://localhost:8000/metadata/models/text-embedder/versions \
  -H "X-API-Key: your-key"
# => {"success": true, "data": [
#       {"version": 3, "uri": "s3://models/text-embedder/v3", "aliases": ["latest"], "tier": "latest"},
#       {"version": 2, "uri": "s3://models/text-embedder/v2", "aliases": ["production"], "tier": "production"}
#     ], "error": null, "metadata": {"model": "text-embedder", "total": 2}}
```

### Python 客户端示例

```python
import httpx

BASE_URL = "http://localhost:8000"
HEADERS = {"X-API-Key": "your-secret-api-key-here", "Content-Type": "application/json"}


def list_metadata_catalogs() -> dict:
    """列出所有 Gravitino catalog。"""
    resp = httpx.get(f"{BASE_URL}/metadata/catalogs", headers=HEADERS, timeout=10)
    resp.raise_for_status()
    return resp.json()


def list_metadata_tables() -> dict:
    """列出 Lance catalog 中的表。"""
    resp = httpx.get(f"{BASE_URL}/metadata/tables", headers=HEADERS, timeout=10)
    resp.raise_for_status()
    return resp.json()


def get_table_detail(name: str) -> dict:
    """获取表详情，包括列和属性。"""
    resp = httpx.get(f"{BASE_URL}/metadata/tables/{name}", headers=HEADERS, timeout=10)
    resp.raise_for_status()
    return resp.json()


def list_tags(table: str | None = None) -> dict:
    """列出标签，可按表过滤。"""
    params = {"table": table} if table else {}
    resp = httpx.get(f"{BASE_URL}/metadata/tags", headers=HEADERS, params=params, timeout=10)
    resp.raise_for_status()
    return resp.json()


def create_tag(name: str, comment: str = "") -> dict:
    """在 Gravitino 中创建新标签。"""
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
    """列出所有治理策略。"""
    resp = httpx.get(f"{BASE_URL}/metadata/policies", headers=HEADERS, timeout=10)
    resp.raise_for_status()
    return resp.json()


def create_retention_policy(name: str, days: int = 30) -> dict:
    """创建数据保留策略。"""
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
    """创建列脱敏策略。"""
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
    """收集并注册表统计信息。"""
    resp = httpx.post(
        f"{BASE_URL}/metadata/statistics/{table_name}",
        headers=HEADERS,
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()


def list_models() -> dict:
    """列出所有已注册的 ML 模型。"""
    resp = httpx.get(f"{BASE_URL}/metadata/models", headers=HEADERS, timeout=10)
    resp.raise_for_status()
    return resp.json()


def get_model_versions(model_name: str) -> dict:
    """获取模型版本信息（latest 和 production）。"""
    resp = httpx.get(
        f"{BASE_URL}/metadata/models/{model_name}/versions",
        headers=HEADERS,
        timeout=10,
    )
    resp.raise_for_status()
    return resp.json()
```

### Gravitino 配置

```yaml
# config.yaml — 启用 Gravitino 集成
gravitino:
  enabled: true
  uri: "http://localhost:8090"
  metalake: "arrow_lake"
  lance_rest_enabled: true
  lance_rest_uri: "http://localhost:8888"
  sync_interval_seconds: 300    # 后台同步间隔
```

***

## v1.5.x 新增端点

以下端点在 v1.5.x 中新增，涵盖审计追踪、备份/恢复、维护、系统健康、文件上传、扩展摄取、模式迁移、高级查询、血缘扩展、知识图谱扩展、导出和管理 ACL 管理。

### 审计追踪 API

| 方法     | 端点                                       | 说明                   | 认证     |
| ------ | ---------------------------------------- | -------------------- | ------ |
| `POST` | `/api/v1/audit/record`                   | 记录审计事件              | EDITOR |
| `POST` | `/api/v1/audit/verify?audit_id=...`      | 验证审计条目完整性           | VIEWER |
| `GET`  | `/api/v1/audit/query`                    | 带过滤条件的审计追踪查询        | VIEWER |
| `POST` | `/api/v1/audit/export?dataset_name=...`  | 导出数据集的审计追踪          | ADMIN  |

#### 记录审计事件

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

**响应：**

```json
{"success": true, "audit_id": "aud_abc123"}
```

#### 验证审计条目

```bash
curl -X POST "http://localhost:8000/api/v1/audit/verify?audit_id=aud_abc123" \
  -H "Authorization: Bearer $TOKEN"
```

**响应：**

```json
{"success": true, "intact": true}
```

#### 查询审计追踪

```bash
curl -G http://localhost:8000/api/v1/audit/query \
  -H "Authorization: Bearer $TOKEN" \
  --data-urlencode "dataset_name=users" \
  --data-urlencode "start=2025-01-01" \
  --data-urlencode "end=2025-12-31" \
  --data-urlencode "event_type=read"
```

**响应：**

```json
{"success": true, "entries": [{"event_type": "read", "actor": "admin", "timestamp": "..."}]}
```

#### 导出审计追踪

```bash
curl -X POST "http://localhost:8000/api/v1/audit/export?dataset_name=users" \
  -H "Authorization: Bearer $TOKEN"
```

**响应：**

```json
{"success": true, "export": {"dataset_name": "users", "entries": [...]}}
```

### 备份与恢复 API

| 方法      | 端点                                        | 说明             | 认证    |
| ------- | ----------------------------------------- | -------------- | ----- |
| `POST`  | `/api/v1/backup/create`                   | 创建备份           | ADMIN |
| `POST`  | `/api/v1/backup/restore?backup_id=...`    | 按 ID 恢复备份     | ADMIN |
| `GET`   | `/api/v1/backup/list`                     | 列出所有备份         | ADMIN |
| `DELETE`| `/api/v1/backup/{backup_id}`              | 删除备份           | ADMIN |

#### 创建备份

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

**响应：**

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

#### 恢复备份

```bash
curl -X POST "http://localhost:8000/api/v1/backup/restore?backup_id=backup-2025-06-01" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "dataset_names": ["users"],
    "overwrite": true
  }'
```

**响应：**

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

#### 列出备份

```bash
curl http://localhost:8000/api/v1/backup/list \
  -H "Authorization: Bearer $TOKEN"
```

**响应：**

```json
{
  "backups": [
    {"backup_id": "backup-2025-06-01", "created_at": "...", "datasets": [...], "blob_prefixes": [], "total_size_bytes": 5242880, "status": "completed"}
  ],
  "count": 1
}
```

#### 删除备份

```bash
curl -X DELETE http://localhost:8000/api/v1/backup/backup-2025-06-01 \
  -H "Authorization: Bearer $TOKEN"
```

**响应：**

```json
{"message": "Backup 'backup-2025-06-01' deleted"}
```

### 维护 API

| 方法     | 端点                                    | 说明          | 认证    |
| ------ | ------------------------------------- | ----------- | ----- |
| `GET`  | `/api/v1/admin/maintenance/status`    | 获取调度器状态    | ADMIN |
| `POST` | `/api/v1/admin/maintenance/run`       | 触发维护周期     | ADMIN |

#### 获取维护状态

```bash
curl http://localhost:8000/api/v1/admin/maintenance/status \
  -H "Authorization: Bearer $TOKEN"
```

**响应：**

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

#### 触发维护运行

```bash
curl -X POST http://localhost:8000/api/v1/admin/maintenance/run \
  -H "Authorization: Bearer $TOKEN"
```

**响应：**

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

### 系统与健康 API

| 方法    | 端点                    | 说明                            | 认证     |
| ----- | --------------------- | ----------------------------- | ------ |
| `GET` | `/health/live`        | 存活探针                          | -      |
| `GET` | `/health/ready`       | 就绪探针（检查存储 + 依赖）              | -      |
| `GET` | `/health`             | 健康检查（向后兼容）                    | -      |
| `GET` | `/metrics`            | Prometheus 指标                 | -      |
| `GET` | `/api/v1/version`     | 版本及依赖信息                       | VIEWER |

#### 存活探针

```bash
curl http://localhost:8000/health/live
```

**响应：**

```json
{"status": "ok"}
```

#### 就绪探针

```bash
curl http://localhost:8000/health/ready
```

**响应：**

```json
{
  "status": "ok",
  "version": "1.5.3",
  "storage": "accessible",
  "gravitino": "healthy",
  "duckdb_pool": {"pool_size": 5, "active_sessions": 1, "queued_requests": 0, "total_queries": 142, "total_errors": 0}
}
```

#### 版本信息

```bash
curl http://localhost:8000/api/v1/version \
  -H "Authorization: Bearer $TOKEN"
```

**响应：**

```json
{
  "version": "1.5.3",
  "python": "3.12.4",
  "fastapi": "0.115.0",
  "uvicorn": "0.30.0",
  "pyarrow": "17.0.0",
  "duckdb": "1.0.0",
  "daft": "0.3.0",
  "httpx": "0.27.0"
}
```

### 文件上传 API

| 方法      | 端点                                           | 说明                    | 认证     |
| ------- | -------------------------------------------- | --------------------- | ------ |
| `POST`  | `/api/v1/datasets/{name}/upload`             | 上传文件到 MinIO（代理模式）    | EDITOR |
| `POST`  | `/api/v1/datasets/{name}/upload/presign`     | 生成预签名 PUT URL        | EDITOR |
| `DELETE`| `/api/v1/datasets/{name}/upload/cleanup`     | 删除数据集的上传 Blob       | EDITOR |

#### 上传文件（代理模式）

```bash
curl -X POST http://localhost:8000/api/v1/datasets/my-data/upload \
  -H "Authorization: Bearer $TOKEN" \
  -F "files=@data.csv" \
  -F "files=@report.pdf"
```

**响应：**

```json
{
  "success": true,
  "blobs": [
    {"key": "uploads/my-data/a1b2c3d4_data.csv", "size_bytes": 4096, "content_type": "text/csv"},
    {"key": "uploads/my-data/e5f6g7h8_report.pdf", "size_bytes": 20480, "content_type": "application/pdf"}
  ]
}
```

#### 生成预签名上传 URL

```bash
curl -X POST http://localhost:8000/api/v1/datasets/my-data/upload/presign \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"filenames": ["data.csv", "report.pdf"]}'
```

**响应：**

```json
{
  "success": true,
  "uploads": [
    {"key": "uploads/my-data/a1b2c3d4_data.csv", "upload_url": "http://minio:9000/arrow-lake/uploads/...?X-Amz-Signature=..."},
    {"key": "uploads/my-data/e5f6g7h8_report.pdf", "upload_url": "http://minio:9000/arrow-lake/uploads/...?X-Amz-Signature=..."}
  ]
}
```

#### 清理上传的 Blob

```bash
curl -X DELETE http://localhost:8000/api/v1/datasets/my-data/upload/cleanup \
  -H "Authorization: Bearer $TOKEN"
```

**响应：**

```json
{"success": true, "deleted_count": 3}
```

### 扩展摄取端点

| 方法     | 端点                                            | 说明                      | 认证     |
| ------ | --------------------------------------------- | ----------------------- | ------ |
| `POST` | `/api/v1/datasets/{name}/ingest/sql`          | 从 SQL 数据库摄取            | EDITOR |
| `POST` | `/api/v1/datasets/{name}/ingest/kafka`        | 从 Kafka 主题摄取           | EDITOR |
| `POST` | `/api/v1/datasets/{name}/ingest/iceberg`      | 从 Apache Iceberg 摄取    | EDITOR |
| `POST` | `/api/v1/datasets/{name}/ingest/deltalake`    | 从 Delta Lake 摄取        | EDITOR |
| `POST` | `/api/v1/datasets/{name}/ingest/videos`       | 摄取视频文件（含关键帧）          | EDITOR |
| `POST` | `/api/v1/datasets/{name}/ingest/mixed`        | 摄取多模态混合来源              | EDITOR |

#### 从 SQL 数据库摄取

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

**响应：**

```json
{"success": true, "total_rows": 10000, "total_files": 4, "sources": [{"path": "sql://public.orders", "row_count": 10000, "file_count": 4}]}
```

#### 从 Kafka 摄取

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

**响应：**

```json
{"success": true, "total_rows": 50000, "total_files": 2, "sources": [{"path": "kafka://user-events", "row_count": 30000, "file_count": 1}]}
```

#### 从 Apache Iceberg 摄取

```bash
curl -X POST http://localhost:8000/api/v1/datasets/iceberg_data/ingest/iceberg \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "table_uri": "s3://warehouse/db.table",
    "transforms": null
  }'
```

**响应：**

```json
{"success": true, "total_rows": 20000, "total_files": 1, "sources": [{"path": "s3://warehouse/db.table", "row_count": 20000, "file_count": 1}]}
```

#### 从 Delta Lake 摄取

```bash
curl -X POST http://localhost:8000/api/v1/datasets/delta_data/ingest/deltalake \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "table_uri": "s3://delta-lake/sales",
    "version": 5
  }'
```

**响应：**

```json
{"success": true, "total_rows": 15000, "total_files": 1, "sources": [{"path": "s3://delta-lake/sales", "row_count": 15000, "file_count": 1}]}
```

#### 摄取视频

```bash
curl -X POST http://localhost:8000/api/v1/datasets/video-clips/ingest/videos \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "file_paths": ["videos/intro.mp4", "videos/demo.webm"],
    "blob_keys": []
  }'
```

**响应：**

```json
{"success": true, "total_rows": 24, "total_files": 2, "sources": [{"path": "videos/intro.mp4", "row_count": 12, "file_count": 1}]}
```

#### 摄取多模态混合来源

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

**响应：**

```json
{"success": true, "total_rows": 120, "total_files": 4, "sources": [{"path": "data/report.csv", "row_count": 100, "file_count": 1}]}
```

### 模式迁移 API

| 方法     | 端点                                        | 说明                 | 认证    |
| ------ | ----------------------------------------- | ------------------ | ----- |
| `POST` | `/api/v1/datasets/{name}/schema/migrate`  | 验证/应用模式迁移        | ADMIN |

#### 迁移数据集模式

```bash
# 试运行：仅验证（默认）
curl -X POST http://localhost:8000/api/v1/datasets/users/schema/migrate \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "actions": [
      {"operation": "add_column", "column_name": "region", "sql_expr": "'\''unknown'\''"},
      {"operation": "alter_column", "column_name": "score", "new_type": "float64"},
      {"operation": "drop_column", "column_name": "legacy_field"}
    ],
    "dry_run": true
  }'
```

**响应（dry_run）：**

```json
{"success": true, "dry_run": true, "issues": [], "applied_count": 0}
```

```bash
# 应用迁移
curl -X POST http://localhost:8000/api/v1/datasets/users/schema/migrate \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "actions": [
      {"operation": "add_column", "column_name": "region", "sql_expr": "'\''unknown'\''"},
      {"operation": "alter_column", "column_name": "score", "new_type": "float64"}
    ],
    "dry_run": false
  }'
```

**响应：**

```json
{"success": true, "dry_run": false, "issues": [], "applied_count": 2}
```

### 导出 API

| 方法     | 端点                                                | 说明                  | 认证     |
| ------ | ------------------------------------------------- | ------------------- | ------ |
| `POST` | `/api/v1/datasets/{name}/export`                  | 导出为 Parquet/CSV（异步） | EDITOR |
| `GET`  | `/api/v1/datasets/{name}/export/{task_id}/status` | 检查导出任务状态           | VIEWER |
| `GET`  | `/api/v1/datasets/{name}/export/{task_id}/download` | 下载导出的文件          | VIEWER |
| `POST` | `/api/v1/datasets/{name}/export-to`               | 导出到外部目标（同步）       | EDITOR |

#### 导出数据集（异步）

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

**响应：**

```json
{"success": true, "task_id": "exp_abc123", "dataset_name": "users", "status": "pending", "message": "Export task queued"}
```

#### 检查导出状态

```bash
curl http://localhost:8000/api/v1/datasets/users/export/exp_abc123/status \
  -H "Authorization: Bearer $TOKEN"
```

**响应：**

```json
{"success": true, "task_id": "exp_abc123", "status": "completed", "progress": 1.0, "created_at": "2025-06-01T12:00:00Z", "completed_at": "2025-06-01T12:00:05Z", "error": null, "result": {"file_size_bytes": 102400}}
```

#### 下载导出文件

```bash
curl -O http://localhost:8000/api/v1/datasets/users/export/exp_abc123/download \
  -H "Authorization: Bearer $TOKEN"
```

返回二进制文件下载（`application/octet-stream` 或 `text/csv`）。

#### 导出到外部目标（同步）

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

**响应：**

```json
{"success": true, "rows_exported": 10000}
```

支持的导出格式：`parquet`、`csv`、`json`、`iceberg`、`clickhouse`。

### 血缘 API（扩展）

以下血缘端点是对 v1.4.0 章节中所记录端点的补充。

| 方法     | 端点                                         | 说明                     | 认证     |
| ------ | ------------------------------------------ | ---------------------- | ------ |
| `POST` | `/api/v1/lineage/record`                   | 记录血缘事件                | EDITOR |
| `GET`  | `/api/v1/lineage/history/{dataset_name}`   | 获取血缘历史                | VIEWER |
| `POST` | `/api/v1/lineage/query`                    | 通过 SQL 查询血缘           | VIEWER |
| `GET`  | `/api/v1/lineage/graph/{dataset_name}`     | 获取血缘图谱（json/mermaid/dot） | VIEWER |
| `POST` | `/api/v1/lineage/impact`                   | 下游影响分析                | VIEWER |
| `GET`  | `/api/v1/lineage/stats`                    | 血缘追踪统计               | VIEWER |

#### 记录血缘事件

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

**响应：**

```json
{"success": true, "message": "Lineage event recorded for dataset 'orders_enriched'"}
```

#### 获取血缘历史

```bash
curl http://localhost:8000/api/v1/lineage/history/orders_enriched \
  -H "Authorization: Bearer $TOKEN"
```

**响应：**

```json
{
  "success": true,
  "dataset_name": "orders_enriched",
  "events": [{"operation": "transform", "source_datasets": ["orders", "users"], "timestamp": "..."}]
}
```

#### 通过 SQL 查询血缘

```bash
curl -X POST http://localhost:8000/api/v1/lineage/query \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"sql": "SELECT * FROM lineage_events WHERE operation = '\''join'\''"}'
```

**响应：**

```json
{"success": true, "data": [{"dataset_name": "orders_enriched", "operation": "transform"}]}
```

#### 获取血缘图谱

```bash
# JSON 格式（默认）
curl http://localhost:8000/api/v1/lineage/graph/orders_enriched \
  -H "Authorization: Bearer $TOKEN"

# Mermaid 格式
curl "http://localhost:8000/api/v1/lineage/graph/orders_enriched?format=mermaid&max_depth=5" \
  -H "Authorization: Bearer $TOKEN"

# Graphviz DOT 格式
curl "http://localhost:8000/api/v1/lineage/graph/orders_enriched?format=dot" \
  -H "Authorization: Bearer $TOKEN"
```

**响应（JSON）：**

```json
{
  "success": true,
  "dataset_name": "orders_enriched",
  "nodes": [{"id": "orders", "depth": 0, "type": "source"}, {"id": "orders_enriched", "depth": 1, "type": "target"}],
  "edges": [{"from": "orders", "to": "orders_enriched", "operation": "transform", "transform_type": "join"}],
  "stats": {"total_nodes": 3, "total_edges": 2, "max_depth": 2}
}
```

查询参数：`max_depth`（1-20，默认 10）、`format`（`json`|`mermaid`|`dot`，默认 `json`）。

#### 下游影响分析

```bash
curl -X POST http://localhost:8000/api/v1/lineage/impact \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"dataset_name": "orders"}'
```

**响应：**

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

#### 血缘统计

```bash
curl http://localhost:8000/api/v1/lineage/stats \
  -H "Authorization: Bearer $TOKEN"
```

**响应：**

```json
{"success": true, "total_datasets_tracked": 12, "total_events": 47}
```

### 知识图谱 API（扩展）

以下端点是对第 4 节中已记录的 KG 端点的补充。

| 方法      | 端点                  | 说明                   | 认证     |
| ------- | ------------------- | -------------------- | ------ |
| `GET`   | `/api/v1/kg/schema` | 获取图谱模式（顶点/边标签）      | VIEWER |
| `GET`   | `/api/v1/kg/stats`  | 获取图谱统计信息            | VIEWER |
| `DELETE`| `/api/v1/kg/graph`  | 删除所有图谱数据            | ADMIN  |

#### 获取图谱模式

```bash
curl http://localhost:8000/api/v1/kg/schema \
  -H "Authorization: Bearer $TOKEN"
```

**响应：**

```json
{"vertex_labels": ["Entity", "Concept", "Document"], "edge_labels": ["RELATED_TO", "MENTIONS", "DERIVED_FROM"]}
```

#### 获取图谱统计

```bash
curl http://localhost:8000/api/v1/kg/stats \
  -H "Authorization: Bearer $TOKEN"
```

**响应：**

```json
{"total_vertices": 1024, "total_edges": 3580, "graph_enabled": true}
```

#### 删除图谱数据

```bash
curl -X DELETE http://localhost:8000/api/v1/kg/graph \
  -H "Authorization: Bearer $TOKEN"
```

**响应：**

```json
{"status": "ok", "message": "Graph data deleted"}
```

### 查询 API（OLAP / 元数据 / Daft）

| 方法     | 端点                                         | 说明                            | 认证     |
| ------ | ------------------------------------------ | ----------------------------- | ------ |
| `POST` | `/api/v1/datasets/{name}/query/olap`       | 通过 DuckDB 执行 OLAP SQL（支持 SSE 流式传输） | EDITOR |
| `POST` | `/api/v1/datasets/{name}/query/metadata`   | 元数据 SQL 查询（语义别名）           | EDITOR |
| `POST` | `/api/v1/datasets/{name}/query/daft`       | Daft DataFrame 链式操作          | VIEWER |

#### OLAP 查询

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

**响应：**

```json
{"success": true, "format": "json", "row_count": 5, "column_count": 2, "meta": {"sql": "SELECT ..."}, "rows": [{"region": "US", "total": 150000}]}
```

#### 带 SSE 流式传输的 OLAP 查询

```bash
curl -N -X POST http://localhost:8000/api/v1/datasets/sales/query/olap \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"sql": "SELECT * FROM sales", "stream": true, "batch_size": 1000}'
```

流式传输 SSE 事件：

```
data: {"type": "schema", "columns": ["id", "region", "amount"], "row_count": 50000}
data: {"type": "batch", "rows": 1000, "data": "<base64-arrow-ipc>"}
data: {"type": "done", "total_rows": 50000}
```

#### 元数据 SQL 查询

```bash
curl -X POST http://localhost:8000/api/v1/datasets/sales/query/metadata \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"sql": "SELECT column_name, data_type FROM information_schema.columns", "format": "json"}'
```

**响应：**

```json
{"success": true, "format": "json", "row_count": 8, "column_count": 2, "meta": {"sql": "SELECT ..."}, "rows": [...]}
```

#### Daft DataFrame 查询

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

**响应：**

```json
{"success": true, "format": "json", "row_count": 5, "column_count": 2, "rows": [{"region": "US", "amount": 150000}], "warnings": []}
```

支持的管道操作（按顺序执行）：`sort` -> `filters` -> `groupby` -> `sql` -> `pivot` -> `explode` -> `sample` -> `distinct` -> `columns` -> `offset` -> `limit`。

### 认证 API（扩展）

| 方法     | 端点                      | 说明              | 认证     |
| ------ | ----------------------- | --------------- | ------ |
| `POST` | `/api/v1/auth/token`    | 凭证换取 JWT       | -      |
| `POST` | `/api/v1/auth/refresh`  | 刷新访问令牌        | -      |
| `GET`  | `/api/v1/auth/me`       | 获取当前用户信息      | VIEWER |
| `POST` | `/api/v1/auth/logout`   | 撤销当前令牌        | VIEWER |

#### 登出 / 撤销令牌

```bash
curl -X POST http://localhost:8000/api/v1/auth/logout \
  -H "Authorization: Bearer $TOKEN"
```

**响应：**

```json
{"message": "Token revoked"}
```

### 管理 ACL 管理（扩展）

以下端点是对 v1.4.0 中记录的行/列级 ACL 端点的补充。

#### 模式级 ACL

| 方法      | 端点                                           | 说明           | 认证    |
| ------- | -------------------------------------------- | ------------ | ----- |
| `PUT`   | `/api/v1/admin/acl/schema/{schema_name}`     | 设置模式级 ACL    | ADMIN |
| `GET`   | `/api/v1/admin/acl/schema/{schema_name}`     | 列出模式级 ACL   | ADMIN |
| `DELETE`| `/api/v1/admin/acl/schema/{schema_name}/{role}` | 删除模式级 ACL | ADMIN |

##### 设置模式级 ACL

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

**响应：**

```json
{"schema_name": "analytics", "role": "viewer", "allowed_actions": ["read", "search"], "denied_actions": ["delete", "export"]}
```

##### 列出模式级 ACL

```bash
curl http://localhost:8000/api/v1/admin/acl/schema/analytics \
  -H "Authorization: Bearer $TOKEN"
```

**响应：**

```json
{
  "schema_name": "analytics",
  "acls": [
    {"schema_name": "analytics", "role": "viewer", "allowed_actions": ["read", "search"], "denied_actions": ["delete", "export"]}
  ]
}
```

##### 删除模式级 ACL

```bash
curl -X DELETE http://localhost:8000/api/v1/admin/acl/schema/analytics/viewer \
  -H "Authorization: Bearer $TOKEN"
```

**响应：**

```json
{"schema_name": "analytics", "role": "viewer", "allowed_actions": [], "denied_actions": []}
```

#### 显式拒绝规则

| 方法      | 端点                                  | 说明              | 认证    |
| ------- | ----------------------------------- | --------------- | ----- |
| `PUT`   | `/api/v1/admin/deny/{dataset}`      | 添加操作的显式拒绝      | ADMIN |
| `DELETE`| `/api/v1/admin/deny/{dataset}/{action}` | 移除显式拒绝      | ADMIN |
| `GET`   | `/api/v1/admin/deny/{dataset}`      | 列出数据集的拒绝操作    | ADMIN |

##### 添加显式拒绝

```bash
curl -X PUT http://localhost:8000/api/v1/admin/deny/sensitive_data \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"action": "export"}'
```

**响应：**

```json
{"dataset": "sensitive_data", "action": "export", "denied": true}
```

##### 移除显式拒绝

```bash
curl -X DELETE http://localhost:8000/api/v1/admin/deny/sensitive_data/export \
  -H "Authorization: Bearer $TOKEN"
```

**响应：**

```json
{"dataset": "sensitive_data", "action": "export", "denied": false}
```

##### 列出拒绝操作

```bash
curl http://localhost:8000/api/v1/admin/deny/sensitive_data \
  -H "Authorization: Bearer $TOKEN"
```

**响应：**

```json
{"dataset": "sensitive_data", "denied_actions": ["delete", "export"]}
```
