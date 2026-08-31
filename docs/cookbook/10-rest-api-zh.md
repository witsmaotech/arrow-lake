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
| `POST` | `/api/v1/datasets/{name}/search/faceted`  | 分面搜索                            |
| `POST` | `/api/v1/datasets/{name}/search/ensemble` | 集成搜索（向量 + FTS + 分面）       |

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
  -d '{"file_paths": ["datas/reports/aigc_industry_report.pdf"]}'
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
    result = await ingest_files("aigc_articles", ["datas/reports/aigc_articles.csv"])
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
# => {"success": true, "data": [{"name": "aigc_articles"}, {"name": "ontime"}],
#     "error": null, "metadata": {"total": 2}}

# 获取表详情（列和属性）
curl http://localhost:8000/metadata/tables/articles \
  -H "X-API-Key: your-key"
# => {"success": true, "data": {"name": "aigc_articles",
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
    resp = httpx.post(
        f"{BASE_URL}/metadata/tags",
        headers=HEADERS,
        json={"name": name, "comment": comment},
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
    resp = httpx.post(
        f"{BASE_URL}/metadata/policies/retention",
        headers=HEADERS,
        json={"name": name, "days": days},
        timeout=10,
    )
    resp.raise_for_status()
    return resp.json()


def create_masking_policy(name: str, columns: list[str]) -> dict:
    """创建列脱敏策略。"""
    resp = httpx.post(
        f"{BASE_URL}/metadata/policies/masking",
        headers=HEADERS,
        json={"name": name, "columns": columns},
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
  "version": "1.11.4",
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
  "version": "1.11.4",
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

```text
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

### 存储生命周期 API（已移除）

> **已废弃**：`/api/v1/lifecycle/*` 端点（`apply`/`status`/`restore`/`rules`/`estimate`）
> 在 v1.9.x 中**已从 REST 服务中移除**——代码库中不再注册对应路由器。归档/过期/恢复等
> 生命周期策略现通过 CLI（`arrow-lake lifecycle ...`）和后台维护调度器
> （`/admin/maintenance/*`）管理。调用旧端点将返回 404。

***

## v1.9.x 新增端点

以下端点在 v1.6–v1.9.6 中新增。当前 v1.10.0 共暴露 **~190 条路由、23 个 router**（system、
datasets、search、query、export、quality、cleaning、embedding、embed、lineage、materialized、
audit、backup、rag、kg、extraction_templates、doc_type_categories、auth、admin、maintenance、
gravitino、async_tasks、user_state）。涵盖用户态、用户与令牌管理、多模态嵌入、物化视图、
字段注释、质量增强、索引管理、清洗、异步任务、KG 模板/版本、RAG 增强等。v1.10.0 新增的
抽取模板管理与 doc-type 字典见下一节。

### 用户态 API（`/api/v1/me/*`）

当前用户的个人状态端点。**需要 personal token**（通过 `X-API-Key` 或 Bearer 传递个人令牌，
非 JWT 访问令牌；JWT 调用 `/me/*` 会返回 401）。角色要求 VIEWER。

| 方法      | 端点                                       | 说明                         |
| ------- | ---------------------------------------- | -------------------------- |
| `POST`  | `/api/v1/me/saved-queries`               | 保存查询                       |
| `GET`   | `/api/v1/me/saved-queries`               | 列出已保存查询                    |
| `DELETE`| `/api/v1/me/saved-queries/{qid}`         | 删除已保存查询                    |
| `GET`   | `/api/v1/me/notifications`               | 列出通知                       |
| `POST`  | `/api/v1/me/notifications/read`          | 标记通知已读（`?notification_id=`） |
| `GET`   | `/api/v1/me/preferences`                 | 获取偏好设置                     |
| `PUT`   | `/api/v1/me/preferences`                 | 更新偏好设置                     |
| `POST`  | `/api/v1/me/dashboards`                  | 保存仪表盘布局                    |
| `GET`   | `/api/v1/me/dashboards`                  | 列出仪表盘                      |
| `DELETE`| `/api/v1/me/dashboards/{dashboard_id}`   | 删除仪表盘                      |
| `POST`  | `/api/v1/me/favorites`                   | 添加收藏（幂等）                   |
| `GET`   | `/api/v1/me/favorites`                   | 列出收藏                       |
| `DELETE`| `/api/v1/me/favorites/{target_type}/{target_id}` | 移除收藏                  |

```bash
# 标记单条通知已读
curl -X POST "http://localhost:8000/api/v1/me/notifications/read?notification_id=42" \
  -H "X-API-Key: <personal-token>"
```

### 用户与令牌管理（admin 扩展）

对 v1.4.0 行/列 ACL 端点的补充。**需要 ADMIN 角色**。

| 方法      | 端点                                        | 说明                          |
| ------- | ----------------------------------------- | --------------------------- |
| `GET`   | `/api/v1/admin/users`                     | 列出全部用户                      |
| `POST`  | `/api/v1/admin/users`                     | 创建用户（`CreateUserRequest`）   |
| `PUT`   | `/api/v1/admin/users/{user_id}`           | 更新用户字段                      |
| `DELETE`| `/api/v1/admin/users/{user_id}`           | 停用用户（软删除）                   |
| `GET`   | `/api/v1/admin/roles`                     | 列出角色及权限矩阵                   |
| `POST`  | `/api/v1/admin/users/{user_id}/tokens`    | 签发 personal token           |
| `GET`   | `/api/v1/admin/users/{user_id}/tokens`    | 列出某用户的令牌                    |
| `DELETE`| `/api/v1/admin/users/{user_id}/tokens/{token_id}` | 撤销令牌                  |

```bash
# 创建用户
curl -X POST http://localhost:8000/api/v1/admin/users \
  -H "Authorization: Bearer $ADMIN_TOKEN" -H "Content-Type: application/json" \
  -d '{"username": "alice", "email": "alice@example.com", "role": "viewer", "password": "change-me-12"}'

# 为某用户签发 personal token（响应中的 token 即可用于 /me/* 端点）
curl -X POST http://localhost:8000/api/v1/admin/users/3/tokens \
  -H "Authorization: Bearer $ADMIN_TOKEN"
```

### 多模态嵌入（`/api/v1/embed/*`）

`embed_router` 提供独立的文本/图像嵌入计算，用于跨模态检索（文搜图、以图搜图）。

| 方法     | 端点                     | 说明                                     |
| ------ | ---------------------- | -------------------------------------- |
| `POST` | `/api/v1/embed/text`   | 文本嵌入（本地模型或外部 API）                       |
| `POST` | `/api/v1/embed/image`  | 图像嵌入（CLIP/SigLIP；JSON body `{"images":["<base64>"]}`） |
| `POST` | `/api/v1/embed/clip-text` | CLIP 文本嵌入（文搜图：文本向量与图像向量同空间）          |

```bash
# 文搜图：用文本 query 生成向量，再对图像数据集做向量检索
curl -X POST http://localhost:8000/api/v1/embed/clip-text \
  -H "X-API-Key: your-key" -H "Content-Type: application/json" \
  -d '{"texts": ["a red car"]}'
```

### 物化视图（`/api/v1/materialized/*`）

DuckLake 物化视图管理。**当 `ducklake_enabled=false`（默认）时所有端点返回 503**。
所有端点要求 ADMIN。

| 方法      | 端点                          | 说明              |
| ------- | --------------------------- | --------------- |
| `GET`   | `/api/v1/materialized`      | 列出所有物化视图        |
| `DELETE`| `/api/v1/materialized/{view}` | 删除指定物化视图（不存在返回 404） |
| `POST`  | `/api/v1/materialized/cleanup` | 清理失效物化视图      |

### 字段注释（Schema 注解）

在数据集 schema 的 Arrow 字段元数据上写入人类可读注释（ingest 钩子与 annotate 端点共用同一 key）。

| 方法     | 端点                                        | 说明                       | 角色    |
| ------ | ----------------------------------------- | ------------------------ | ----- |
| `GET`  | `/api/v1/datasets/{name}/schema`          | 获取 schema（含字段 comment）   | VIEWER |
| `POST` | `/api/v1/datasets/{name}/schema/annotate` | 设置某字段注释（body：`field`+`comment`） | ADMIN |

```bash
curl -X POST http://localhost:8000/api/v1/datasets/users/schema/annotate \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"field": "email", "comment": "用户登录邮箱（PII）"}'
```

### 质量增强端点

对 v1.4.0 `quality/rules` 端点的补充（完整质量管线）。

| 方法     | 端点                                          | 说明                    | 角色     |
| ------ | ------------------------------------------- | --------------------- | ------ |
| `POST` | `/api/v1/datasets/{name}/quality/filter`    | 按表达式过滤行               | EDITOR |
| `GET`  | `/api/v1/datasets/{name}/quality/report`    | 生成质量报告                | VIEWER |
| `POST` | `/api/v1/datasets/{name}/quality/deduplicate` | 去重（相似度/精确匹配）        | EDITOR |
| `GET`  | `/api/v1/datasets/{name}/quality/profile`   | 列分布画像                 | VIEWER |
| `POST` | `/api/v1/datasets/{name}/quality/llm_label` | LLM 标注（分类/打标）         | EDITOR |
| `POST` | `/api/v1/datasets/{name}/quality/extract`   | LLM 结构化抽取             | EDITOR |
| `POST` | `/api/v1/datasets/{name}/quality/mask-preview` | 脱敏预览（读前 5 行，返 before/after） | EDITOR |

```bash
# 脱敏预览：不写回，仅展示前 5 行的脱敏前后对比
curl -X POST http://localhost:8000/api/v1/datasets/users/quality/mask-preview \
  -H "Authorization: Bearer $TOKEN"
```

### 索引管理端点

| 方法      | 端点                                            | 说明                          | 角色     |
| ------- | --------------------------------------------- | --------------------------- | ------ |
| `POST`  | `/api/v1/datasets/{name}/index/vector`        | 创建向量索引（IVF_PQ，≥256 行自动建）    | EDITOR |
| `POST`  | `/api/v1/datasets/{name}/index/fts`           | 创建全文索引（BM25 + 可选 jieba 分词列） | EDITOR |
| `POST`  | `/api/v1/datasets/{name}/index/scalar`        | 创建标量索引（BTREE/BITMAP）        | EDITOR |
| `POST`  | `/api/v1/datasets/{name}/index/facets`        | 创建分面索引                      | EDITOR |
| `GET`   | `/api/v1/datasets/{name}/index`               | 列出全部索引                      | VIEWER |
| `DELETE`| `/api/v1/datasets/{name}/index/{index_name}`  | 删除索引                        | EDITOR |

### 清洗（语义写回）

将声明式清洗步骤编译为 DuckDB SQL，经 `restore_dataset` 写回 Lance（结构化数据集）。

| 方法     | 端点                              | 说明                                | 角色     |
| ------ | ------------------------------- | --------------------------------- | ------ |
| `POST` | `/api/v1/datasets/{name}/clean` | 语义清洗（body：`steps`/`filters`/`write_back`/`limit`） | EDITOR |

```bash
curl -X POST http://localhost:8000/api/v1/datasets/users/clean \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"steps": [{"op": "trim", "column": "name"},
                   {"op": "lowercase", "column": "email"}], "write_back": true}'
```

### 异步任务（`/api/v1/tasks/*`）

长耗时操作（摄取/索引/备份）的异步变体。所有端点要求 VIEWER（创建类要求对应操作的角色）。

| 方法     | 端点                                              | 说明               |
| ------ | ----------------------------------------------- | ---------------- |
| `GET`  | `/api/v1/tasks`                                 | 列出任务（含历史，支持状态过滤） |
| `GET`  | `/api/v1/tasks/{task_id}/status`                | 查询任务状态           |
| `POST` | `/api/v1/datasets/{name}/ingest/async`          | 异步摄取本地文件         |
| `POST` | `/api/v1/datasets/{name}/ingest/documents/async`| 异步摄取 PDF/文档      |
| `POST` | `/api/v1/datasets/{name}/index/vector/async`    | 异步建向量索引          |
| `POST` | `/api/v1/datasets/{name}/index/fts/async`       | 异步建全文索引          |
| `POST` | `/api/v1/backup/create/async`                   | 异步创建备份           |
| `POST` | `/api/v1/backup/restore/async`                  | 异步恢复备份           |

### 知识图谱模板与版本管理

对第 4 节 KG 端点的补充。模板路径暴露了 hyper-extract 的多模板能力；KA 版本支持增量、回滚与清理。

| 方法      | 端点                                  | 说明                              | 角色     |
| ------- | ----------------------------------- | ------------------------------- | ------ |
| `GET`   | `/api/v1/kg/doc-types`             | 列出可用文档类型（解析为模板）                 | VIEWER |
| `GET`   | `/api/v1/kg/templates`             | 列出全部模板                          | VIEWER |
| `GET`   | `/api/v1/kg/templates/{template_path}` | 获取模板详情（`{template_path:path}`） | VIEWER |
| `GET`   | `/api/v1/kg/ka-versions/{dataset}` | 列出某数据集的 KA 版本                   | VIEWER |
| `POST`  | `/api/v1/kg/ka-rollback`           | 回滚到指定 KA 版本                     | ADMIN  |
| `POST`  | `/api/v1/kg/ka-prune`              | 清理旧版本 KA dump                   | ADMIN  |
| `POST`  | `/api/v1/kg/build`                 | body 支持 `incremental:true`（增量；无 KA dump 时回退全量） | ADMIN  |
| `POST`  | `/api/v1/kg/ask/stream`            | KG 问答流式（SSE）                    | VIEWER |
| `POST`  | `/api/v1/kg/query/graphrag`        | GraphRAG 问答（body：`question`+`dataset`） | VIEWER |
| `POST`  | `/api/v1/kg/search`                | KG 实体检索                         | VIEWER |
| `POST`  | `/api/v1/kg/rebuild-index`         | 重建 KA 嵌入索引                      | ADMIN  |
| `POST`  | `/api/v1/kg/export-obsidian`       | 导出为 Obsidian 知识库                | VIEWER |

```bash
# 增量构建 KG（只处理新增 chunk，复用已有 KA dump）
curl -X POST http://localhost:8000/api/v1/kg/build \
  -H "X-API-Key: your-key" -H "Content-Type: application/json" \
  -d '{"dataset_name": "docs", "incremental": true}'
```

### RAG 增强（`/api/v1/rag/*`）

RAG 端点请求体字段为 **`question`** 与 **`dataset_name`**（非 `query`/`dataset`）。
`use_kg: bool`（默认 `true`）支持 per-query 控制 GraphRAG 增强与否——无需关闭全局 `hugegraph.enabled`。

| 方法     | 端点                       | 说明                                            |
| ------ | ------------------------ | --------------------------------------------- |
| `POST` | `/api/v1/rag/query`      | RAG 问答（body：`question`/`dataset_name`/`top_k`/`retrieval_strategy`/`use_kg`） |
| `POST` | `/api/v1/rag/query/stream` | 流式 RAG（SSE，先 citation 再内容）                  |
| `POST` | `/api/v1/rag/extract`    | RAG 抽取（仅返回检索+抽取结果，不生成答案）                      |
| `GET`  | `/api/v1/rag/templates`  | 列出可用 prompt 模板                                |

```bash
# 关闭 GraphRAG，做纯向量/混合检索对比
curl -X POST http://localhost:8000/api/v1/rag/query \
  -H "X-API-Key: your-key" -H "Content-Type: application/json" \
  -d '{"question": "架构是什么？", "dataset_name": "docs", "use_kg": false, "retrieval_strategy": "hybrid"}'
```

### v1.5.2 安全加固说明

v1.5.2 中应用了以下安全加固措施：

| 领域         | 加固内容                                       |
| ---------- | ------------------------------------------ |
| **JWT**    | 空 `jwt_secret_key` 现在会阻止服务器启动            |
| **Kerberos** | 认证提供程序中的命令注入漏洞已消除                    |
| **SQL**    | 所有面向用户的查询均使用参数化执行                        |
| **Redis**  | 移除默认密码；必须显式配置                             |
| **网络**     | 所有端口默认绑定到 `127.0.0.1`                    |
| **SSRF**   | `ingest_http` 和预签名端点增加 URL 校验           |
| **Admin**  | 角色枚举取代基于字符串的管理员绕过                        |
| **令牌**     | Refresh Token 轮换，支持撤销                     |
| **Gremlin** | `kg_query` 端点增加输入净化                      |

这些加固措施自动生效，无需配置更改。

***

## v1.10.0 新增端点

v1.10.0 新增动态**抽取模板管理**面与 **category ↔ doc_type 字典**，以及模板感知的 KG 构建。
模板动态加载——无需 rebuild 或重启（`reset_gallery_cache` 自动拾取新 preset），所有状态存于
system_db（libSQL）。

### 抽取模板管理（`/api/v1/admin/extraction-templates/*`）

仅管理员（Role.ADMIN）。包含 CRUD、AI 生成、试运行、数据集绑定，以及**质量验证 harness**
（建临时数据集 → 摄取样本文档 → 构建 KG → 跑 RAG → 清理）。

| 方法      | 端点                                              | 说明                                  |
| ------- | ----------------------------------------------- | ----------------------------------- |
| `GET`    | `/api/v1/admin/extraction-templates`            | 列出模板（可选 `?category=`）              |
| `GET`    | `/api/v1/admin/extraction-templates/{name}`     | 模板详情                                |
| `POST`   | `/api/v1/admin/extraction-templates`            | 创建模板（201）                           |
| `PUT`    | `/api/v1/admin/extraction-templates/{name}`     | 更新模板                                |
| `DELETE` | `/api/v1/admin/extraction-templates/{name}`     | 删除模板                                |
| `POST`   | `/api/v1/admin/extraction-templates/validate`   | 落盘前校验 YAML schema                    |
| `POST`   | `/api/v1/admin/extraction-templates/generate`   | 根据样本文档 + doc_type 用 AI 生成模板         |
| `POST`   | `/api/v1/admin/extraction-templates/dry-run`    | 试运行抽取（不持久化）                         |
| `POST`   | `/api/v1/admin/extraction-templates/{name}/quality/doc`    | 质量 harness：生成样本文档          |
| `POST`   | `/api/v1/admin/extraction-templates/{name}/quality/build`   | 质量 harness：建图 + 可视化 + RAG |
| `DELETE` | `/api/v1/admin/extraction-templates/quality/{temp_dataset}` | 质量 harness：清理临时数据集        |
| `GET`    | `/api/v1/admin/extraction-templates/{name}/quality/history` | 质量运行历史                    |
| `PUT`    | `/api/v1/admin/extraction-templates/default`    | 设置默认模板                              |
| `GET`    | `/api/v1/admin/extraction-templates/{name}/usage` | 查询模板绑定在哪里                         |
| `GET`    | `/api/v1/admin/extraction-templates/bindings/{dataset}`    | 查询数据集的绑定                    |
| `PUT`    | `/api/v1/admin/extraction-templates/bindings/{dataset}`    | 将数据集绑定到模板                  |
| `DELETE` | `/api/v1/admin/extraction-templates/bindings/{dataset}`    | 清除数据集的绑定                   |

```bash
# 绑定数据集到模板，再用它构建（构建时模板自动解析）
curl -X PUT http://localhost:8000/api/v1/admin/extraction-templates/bindings/aigc_report \
  -H "Authorization: Bearer $TOKEN" -H "X-API-Key: $KEY" \
  -H "Content-Type: application/json" -d '{"template": "project_concept_graph"}'

curl -X POST http://localhost:8000/api/v1/kg/build \
  -H "Authorization: Bearer $TOKEN" -H "X-API-Key: $KEY" \
  -H "Content-Type: application/json" -d '{"dataset": "aigc_report"}'   # 模板从绑定解析
```

### Doc-Type 字典（`/api/v1/admin/doc-type-categories`）

仅管理员。管理动态 category → doc_type 字典，支撑 `GET /api/v1/kg/doc-types`。
`DOC_TYPE_ALIASES` 内置 10 个规范键（paper/report/manual/biography/finance/legal/medicine/
industry/tcm/general）；`project` 及自定义键通过本端点添加。模板的 `category` 为必填且必须存在于字典。

| 方法      | 端点                                  | 说明          |
| ------- | ----------------------------------- | ----------- |
| `GET`   | `/api/v1/admin/doc-type-categories` | 列出所有 category |
| `POST`  | `/api/v1/admin/doc-type-categories` | 创建 category（201） |
| `DELETE`| `/api/v1/admin/doc-type-categories/{name}` | 删除 category  |

### 模板感知的 KG 构建与 GraphRAG

| 方法    | 端点                          | 说明                                          |
| ----- | --------------------------- | ------------------------------------------- |
| `POST`| `/api/v1/kg/build`          | 构建 KG；body 增加 `template`（覆盖 doc_type 路由）与 `incremental` |
| `GET` | `/api/v1/kg/build/{task_id}/status` | 轮询构建进度（chunks/entities/relations）       |
| `GET` | `/api/v1/kg/doc-types`      | 动态 doc_type 列表（规范键 + 字典 + 解析出的模板）           |
| `POST`| `/api/v1/kg/query/graphrag` | GraphRAG 问答（body：`question` + `dataset`）    |

> 端到端流程见 cookbook 示例:SDK [`examples/46_template_management.py`](examples/46_template_management.py)、[`examples/48_graphrag_relation_qa.py`](examples/48_graphrag_relation_qa.py);REST [`examples_api/34_extraction_templates_api.py`](examples_api/34_extraction_templates_api.py)、[`examples_api/36_graphrag_relation_qa_api.py`](examples_api/36_graphrag_relation_qa_api.py)。
