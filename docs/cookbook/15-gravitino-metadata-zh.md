# Gravitino 元数据治理（可选）

> **状态：可选治理层** — Gravitino 是 Arrow Lake 的**可选**元数据治理组件（RBAC/tag/血缘/fileset
> 治理），**不在数据/查询热路径**上：数据集 CRUD、查询、KG、search、RAG 均不依赖 Gravitino。
> 卡死或不需要时，设置 `gravitino.enabled: false` 临时关闭即可，核心功能完全不受影响。
>
> 版本：本章对应 Arrow Lake v1.10.7。Gravitino server 已升级至 **1.3.0**，Python SDK pin 在
> `apache-gravitino==1.3.0`（`pyproject.toml:75`）。1.3.0 的 docker 布局变更：
> `GRAVITINO_HOME=/opt/gravitino`（非旧版 `/root/gravitino`），数据卷须挂 `/opt/gravitino/data`。
> Catalog S3 属性使用 **`s3.*`**（`s3.endpoint`/`s3.access-key-id`/`s3.secret-access-key`，
> `deploy/scripts/init-gravitino.sh:53-55`）——旧的 `fs.s3a.*` 在 1.3.0 fileset catalog 上不生效。
>
> 前提条件：`gravitino.enabled: true`（默认 `false`，见 `config/gravitino.py:32`），Docker Compose
> 生产环境 profile 正在运行（`gravitino` + `lance-rest` 容器健康）。

### 当前能力矩阵

| 功能 | 存储 | 执行 | 状态 |
|------|------|------|------|
| Catalog/Table 浏览 | Gravitino | — | **可用** |
| 标签创建与关联 | Gravitino | 不驱动访问控制 | **仅元数据** |
| 保留策略 | Gravitino | 不自动清理 | **仅元数据** |
| 脱敏策略 | Gravitino | 不转换查询结果 | **仅元数据** |
| 表统计信息 | Gravitino | 查询规划器不消费 | **仅元数据** |
| 模型版本 | Gravitino | 未接入 embed/rag | **仅元数据** |
| RBAC（访问控制） | **Turso/libSQL**（一等 store） | `RbacStore` fail-close 执行 | **生产可用** |
| Gravitino RBAC 桥接 | Gravitino SDK | 可选旁路，非主路径 | **可选** |
| 血缘集成 | 表属性 | 无跨系统血缘图 | **浅层** |
| 联邦查询 | 路径拼接 | 无元数据驱动读取 | **仅路径前缀** |

> **RBAC 说明（v1.9.0+）**：访问控制的**一等持久化后端是 Turso/libSQL**（`arrow_lake/system_db/stores/rbac.py`
> 的 `RbacStore`，fail-close，表 `dataset_acl_grants`/`schema_acls`/`acl_denies`/`role_permissions`）。
> `GravitinoRBACBridge` 仅是可选旁路；**不要高估 Gravitino 角色的作用**——Gravitino 关闭时 RBAC 照常生效。

***

## 1. 架构概览

### 代理架构

Arrow Lake 作为 Gravitino 元数据操作的代理层。客户端调用 Arrow Lake API 上的 `/api/v1/metadata/*` 端点，由 Arrow Lake 通过 REST API 或 Python SDK 委托给 Gravitino：

```text
Client → Arrow Lake API (/api/v1/metadata/*)
            ├── GravitinoBridge (REST)       ← catalogs, tables, stats
            ├── GravitinoTagService (SDK)    ← tags
            ├── GravitinoPolicyService (SDK) ← policies
            └── GravitinoModelRegistry (SDK) ← models
                    ↓
            Apache Gravitino Server (:8090)
            Apache Lance REST Catalog (:9002)
```

### 元数据层级

```text
Metalake: arrow-lake
  ├── Catalog: lance-catalog     (RELATIONAL, lakehouse-generic)
  │     └── Schema: arrow_lake
  │           └── Tables: articles, sales, ...
  ├── Catalog: minio-fileset     (FILESET)
  │     └── Schema: arrow_lake
  │           └── Filesets: dataset paths
  └── Catalog: ml-models         (MODEL)
        └── Schema: default
              └── Models: text-embedder, image-classifier, ...
```

### 配置

```yaml
# config.yaml
gravitino:
  enabled: true          # 默认 false（config/gravitino.py:32）——Gravitino 是可选治理层
  uri: "http://gravitino:8090"
  metalake: "arrow-lake"
  lance_rest_enabled: true
  lance_rest_uri: "http://lance-rest:9002"
  auth_type: simple    # simple | oauth | kerberos | null
  sync_direction: bidirectional
  sync_interval_seconds: 30   # 范围: 5–300
```

所有 Gravitino 调用均包装在 `try/except` 中——Gravitino 不可用时，Arrow Lake 使用本地 DuckDB/Lance 目录继续正常运行（数据面不依赖 Gravitino）。

***

## 2. 场景 A — 数据发现与目录浏览

**角色**：新入职数据工程师，探索数据湖中有哪些数据集。

### 步骤 1：检查系统健康

```bash
curl http://localhost:8000/health -H "X-API-Key: your-key"
```

```json
{
  "status": "ok",
  "version": "1.5.3",
  "storage": "accessible",
  "gravitino": "healthy",
  "lance_rest": "healthy"
}
```

当 Gravitino 启用时，健康响应会包含 `gravitino` 和 `lance_rest` 字段。

### 步骤 2：浏览目录

```bash
curl http://localhost:8000/api/v1/metadata/catalogs -H "X-API-Key: your-key"
```

```json
{
  "success": true,
  "data": [
    {"name": "lance-catalog"},
    {"name": "minio-fileset"},
    {"name": "ml-models"}
  ],
  "error": null,
  "metadata": {"total": 3}
}
```

三个目录各自服务不同用途：
- **lance-catalog**：由 Lance 数据集支撑的关系表
- **minio-fileset**：MinIO 对象的文件级访问
- **ml-models**：ML 模型版本注册表

### 步骤 3：列出表

```bash
curl http://localhost:8000/api/v1/metadata/tables -H "X-API-Key: your-key"
```

```json
{
  "success": true,
  "data": [
    {"name": "aigc_articles"},
    {"name": "ontime"},
    {"name": "aigc_articles"}
  ],
  "error": null,
  "metadata": {"total": 3}
}
```

### 步骤 4：查看表结构

```bash
curl http://localhost:8000/api/v1/metadata/tables/articles -H "X-API-Key: your-key"
```

```json
{
  "success": true,
  "data": {
    "name": "aigc_articles",
    "columns": [
      {"name": "id", "type": "long"},
      {"name": "title", "type": "string"},
      {"name": "text_content", "type": "string"},
      {"name": "text_embedding", "type": "binary"},
      {"name": "published_at", "type": "timestamp"}
    ],
    "properties": {
      "format": "lance",
      "owner": "data-team"
    }
  },
  "error": null,
  "metadata": {}
}
```

### Python (httpx)

```python
import httpx

BASE = "http://localhost:8000"
H = {"X-API-Key": "your-key"}

# 浏览 catalogs → tables → detail
for cat in httpx.get(f"{BASE}/api/v1/metadata/catalogs", headers=H).json()["data"]:
    print(f"Catalog: {cat['name']}")

for tbl in httpx.get(f"{BASE}/api/v1/metadata/tables", headers=H).json()["data"]:
    detail = httpx.get(f"{BASE}/api/v1/metadata/tables/{tbl['name']}", headers=H).json()
    cols = detail["data"]["columns"]
    print(f"  {tbl['name']}: {len(cols)} columns")
```

### 关键收获

无需预先知道表名即可发现完整的元数据层级——浏览目录、列出表、再查看单个表结构。

***

## 3. 场景 B — 数据分类与标签治理

**角色**：数据管家为 GDPR 合规打标签，确保 PII 列正确分类。

> **注意**：标签当前存储在 Gravitino 中，但**不驱动访问控制或自动脱敏**。它们作为元数据标签用于发现和审计。执行能力在 v1.4.2 中已增强。

### 预定义标签

`GravitinoTagService` 内置常用治理标签：

| 标签 | 用途 |
|------|------|
| `sensitive` | 敏感信息 |
| `pii` | 个人身份信息 |
| `financial` | 金融/账单数据 |
| `expires:30d` | 30 天保留标记 |

### 步骤 1：创建自定义标签

> **v1.9.6 安全加固**：写端点不再接受 `?body=` URL query（PII 会落入 URL/访问日志）。
> 改用 **JSON POST body**（`routers/gravitino.py:219-235`，`await request.json()`）。

```bash
# 创建 GDPR 监管数据的标签（JSON body，非 URL query）
curl -X POST http://localhost:8000/api/v1/metadata/tags \
  -H "X-API-Key: your-key" \
  -H "Content-Type: application/json" \
  -d '{"name": "gdpr_subject", "comment": "Data subject under GDPR"}'
```

### 步骤 2：关联标签到表/列 (Python SDK)

标签-表和标签-列关联需要直接使用 Python SDK（暂无 REST 端点）：

```python
from arrow_lake.config import GravitinoConfig
from arrow_lake.quality.gravitino_tags import GravitinoTagService

cfg = GravitinoConfig(enabled=True, uri="http://localhost:8090", metalake="arrow-lake")
tags = GravitinoTagService(cfg)

# 标记整个表
tags.tag_table("users", ["pii", "sensitive"])

# 标记特定列
tags.tag_column("users", "email", ["pii"])
tags.tag_column("users", "phone", ["pii"])

# 发现所有带有某标签的表
pii_tables = tags.get_tables_by_tag("pii")
# → ["users", "customers", ...]
```

### 步骤 3：通过 REST 列出标签

```bash
# 列出所有标签
curl http://localhost:8000/api/v1/metadata/tags -H "X-API-Key: your-key"

# 列出特定表的标签
curl "http://localhost:8000/api/v1/metadata/tags?table=users" -H "X-API-Key: your-key"
```

```json
{
  "success": true,
  "data": [{"name": "pii"}, {"name": "sensitive"}],
  "error": null,
  "metadata": {"total": 2}
}
```

### 标签治理工作流

```text
1. 创建标签（定义分类体系）
2. 标记表/列（应用分类）
3. 列出每个表的标签（审计分类）
4. 按标签查询表（发现受管资产）
```

### 关键收获

标签提供轻量级分类系统。列级标签实现精细治理（如标记 PII 列），表级标签实现粗粒度分类（如"financial"）。

***

## 4. 场景 C — 合规策略：保留与脱敏

**角色**：合规官执行数据保留规则和列级脱敏。

> **注意**：策略当前存储在 Gravitino 中，但**不自动执行**。创建保留策略不会触发数据删除；创建脱敏策略不会转换查询结果。执行能力在 v1.4.2 中已增强。

### 步骤 1：创建保留策略

```bash
# 日志数据仅保留 90 天（JSON body）
curl -X POST http://localhost:8000/api/v1/metadata/policies/retention \
  -H "X-API-Key: your-key" \
  -H "Content-Type: application/json" \
  -d '{"name": "log_retention_90d", "days": 90}'
```

```json
{"success": true, "data": {"name": "log_retention_90d", "days": 90}, "error": null, "metadata": {}}
```

### 步骤 2：创建脱敏策略

> **必填 `function` 字段**（`routers/gravitino.py:300-303`）：脱敏策略的 `function` 必须是
> `redact`/`hash`/`partial`/`nullify` 之一，否则返回 400。省略时默认 `redact`。

```bash
# 脱敏 email 和 phone 列（partial: 保留首尾各 2 字符）
curl -X POST http://localhost:8000/api/v1/metadata/policies/masking \
  -H "X-API-Key: your-key" \
  -H "Content-Type: application/json" \
  -d '{"name": "email_mask", "columns": ["email", "phone"], "function": "partial"}'
```

```json
{"success": true, "data": {"name": "email_mask", "columns": ["email", "phone"], "function": "partial"}, "error": null, "metadata": {}}
```

### 步骤 3：应用策略到表 (Python SDK)

```python
from arrow_lake.quality.gravitino_policies import GravitinoPolicyService

svc = GravitinoPolicyService(cfg)
svc.apply_policy("log_retention_90d", "access_logs")
svc.apply_policy("email_mask", "users")
```

### 步骤 4：列出所有策略

```bash
curl http://localhost:8000/api/v1/metadata/policies -H "X-API-Key: your-key"
```

```json
{
  "success": true,
  "data": [
    {"name": "log_retention_90d"},
    {"name": "email_mask"}
  ],
  "error": null,
  "metadata": {"total": 2}
}
```

### 合规检查清单模式

```python
import httpx

H = {"X-API-Key": "your-key"}
BASE = "http://localhost:8000"

# 验证所有 PII 表都有脱敏策略
pii_tables = ["users", "customers", "orders"]
for table in pii_tables:
    resp = httpx.get(f"{BASE}/api/v1/metadata/policies", headers=H).json()
    has_masking = any("mask" in p["name"] for p in resp.get("data", []))
    status = "OK" if has_masking else "MISSING"
    print(f"  {table}: masking policy {status}")
```

### 关键收获

策略将治理意图与执行分离。声明式定义保留和脱敏规则，再应用到表。策略引擎负责清理和数据转换。

***

## 5. 场景 D — ML 模型生命周期管理

**角色**：ML 工程师管理模型版本，用于生产部署。

> **注意**：模型注册表当前是独立的元数据存储。`embed/` 和 `rag/` 模块**尚未**从 Gravitino 读取模型版本。模型热切换需手动集成。完整的 ML 管道集成在 v1.4.2 中已增强。

### 步骤 1：注册模型

```python
from arrow_lake.catalog.gravitino_models import GravitinoModelRegistry

registry = GravitinoModelRegistry(cfg)

# 注册新的嵌入模型
registry.register_model(
    name="text-embedder",
    comment="Text embedding model for RAG pipeline",
    properties={"framework": "sentence-transformers", "dimension": "768"},
)
```

### 步骤 2：添加版本并提升

```python
# 添加版本 1
registry.add_version(
    name="text-embedder",
    uri="s3://models/text-embedder/v1",
    aliases=["latest"],
)

# 添加版本 2（改进模型）
registry.add_version(
    name="text-embedder",
    uri="s3://models/text-embedder/v2",
    aliases=["latest"],
)

# 将版本 2 提升到 production
registry.add_version(
    name="text-embedder",
    uri="s3://models/text-embedder/v2",
    aliases=["production"],
)
```

### 步骤 3：通过 REST 查询模型版本

```bash
curl http://localhost:8000/api/v1/metadata/models -H "X-API-Key: your-key"
```

```json
{"success": true, "data": [{"name": "text-embedder"}], "error": null, "metadata": {"total": 1}}
```

```bash
curl http://localhost:8000/api/v1/metadata/models/text-embedder/versions -H "X-API-Key: your-key"
```

```json
{
  "success": true,
  "data": [
    {"version": 2, "uri": "s3://models/text-embedder/v2", "aliases": ["latest"], "tier": "latest"},
    {"version": 2, "uri": "s3://models/text-embedder/v2", "aliases": ["production"], "tier": "production"}
  ],
  "error": null,
  "metadata": {"model": "text-embedder", "total": 2}
}
```

### 热切换模式

```python
# 在应用启动代码中：
latest = registry.get_latest_version("text-embedder")
prod = registry.get_production_version("text-embedder")

# 使用 production 用于服务，latest 用于金丝雀测试
serving_uri = prod.uri       # → s3://models/text-embedder/v2
canary_uri = latest.uri      # → s3://models/text-embedder/v2
```

热切换方法：在 Gravitino 中更新 `production` alias。下次应用重启时自动使用新版本。

### 关键收获

模型目录将版本管理与服务解耦。使用别名（`latest`、`production`）控制哪个版本用于何处——更新别名，而非代码。

***

## 6. 场景 E — 统计信息驱动查询优化

**角色**：性能工程师采集表统计信息，改善查询计划。

> **注意**：统计信息采集并存储在 Gravitino 中，但**不被 DuckDB 查询规划器消费**。它们仅作为监控元数据使用。查询规划器集成在 v1.4.2 中已增强。

### 步骤 1：采集统计信息

```bash
curl -X POST http://localhost:8000/api/v1/metadata/statistics/articles \
  -H "X-API-Key: your-key"
```

```json
{
  "success": true,
  "data": {
    "name": "aigc_articles",
    "row_count": 50000,
    "column_count": 8,
    "size_mb": 125.4,
    "columns": [
      {"name": "id", "type": "long"},
      {"name": "title", "type": "string"},
      {"name": "text_content", "type": "string"}
    ]
  },
  "error": null,
  "metadata": {}
}
```

### 步骤 2：统计信息内部工作原理

`GravitinoStatsCollector` 对表运行 DuckDB 查询：

```python
from arrow_lake.catalog.gravitino_stats import GravitinoStatsCollector

collector = GravitinoStatsCollector(cfg)
stats = collector.collect_table_stats("articles", duckdb_connection)
# 统计信息以 "stats." 前缀注册为 Gravitino 表属性
collector.register_stats("articles", stats)
```

统计信息以表属性形式存储在 Gravitino 中（前缀为 `stats.*`）。查询引擎可据此做出更好的 join 排序和过滤下推决策。

### 定期采集

后台 `GravitinoSyncScheduler` 可配置为定期采集统计信息：

```yaml
gravitino:
  sync_interval_seconds: 300   # 每 5 分钟采集统计信息
```

通过 `CatalogActor` 集成，数据摄取后统计信息采集自动触发。

### 关键收获

统计信息在元数据与查询性能之间架起桥梁。定期采集（特别是在大量摄取后），让查询规划器拥有准确的行数和基数估计。

***

## 7. 场景 F — 健康检查与优雅降级

**角色**：SRE 验证 Gravitino 不可用时的系统弹性。

### 降级矩阵

| 功能 | Gravitino 正常 | Gravitino 不可用 |
|------|---------------|-----------------|
| 数据摄取 | 正常 + 同步到 Gravitino | 正常（仅 DuckDB） |
| 向量/全文搜索 | 正常 | 正常 |
| OLAP 查询 | 正常 + 联邦查询 | 正常 |
| `/api/v1/metadata/*` 端点 | 完整数据 | 503 Service Unavailable |
| 标签与策略 | 完整 CRUD | 503 或空结果 |
| 模型注册表 | 完整 CRUD | 503 |
| 健康检查 | 显示 `gravitino: healthy` | 显示 `gravitino: unhealthy` |

### 健康检查

```python
import httpx

resp = httpx.get("http://localhost:8000/health").json()
if resp.get("gravitino") != "healthy":
    print("WARNING: Gravitino unavailable — metadata features degraded")
    print("All core features (ingest, search, query) remain functional.")
```

### 应用级降级

```python
# 安全的元数据访问模式
def safe_get_table_detail(client: ArrowLakeClient, name: str) -> dict | None:
    """获取表详情，优雅处理 Gravitino 不可用的情况。"""
    resp = client.metadata_get_table(name)
    if resp.get("success"):
        return resp["data"]
    if resp.get("status") == 503:
        print(f"  Gravitino unavailable, using local catalog for {name}")
        return client.get_dataset(name)  # 降级到 DuckDB
    return None
```

### 关键收获

Arrow Lake 设计为**优雅降级**：Gravitino 是增强层，不是硬依赖。核心操作始终可用；元数据治理功能在不可用时降级为 503。

***

## 8. 后台同步与双向对账

`GravitinoSyncScheduler` 作为后台守护线程运行在 Arrow Lake API 进程中：

```text
┌──────────────────────────────────────────────────┐
│           GravitinoSyncScheduler                  │
│                                                   │
│  每隔 sync_interval_seconds 执行:                  │
│    1. sync_outbound: DuckDB → Gravitino Tables    │
│       (将本地目录条目推送为 Gravitino               │
│        tables + filesets)                         │
│    2. sync_inbound: Gravitino → DuckDB            │
│       (将外部 filesets 拉入本地目录)                │
│                                                   │
│  通过 GravitinoBridge.lock 保证线程安全             │
└──────────────────────────────────────────────────┘
```

### 同步方向配置

| 方向 | 行为 |
|------|------|
| `outbound` | 仅 DuckDB → Gravitino |
| `inbound` | 仅 Gravitino → DuckDB |
| `bidirectional` | 双向同步（默认） |

调度器随 API 服务器生命周期启停（通过 FastAPI `lifespan`）。

### 熔断器（v1.9.6）

`GravitinoSyncScheduler` 内置熔断：连续 **5 次**同步周期失败后**主动停止**同步线程，而非无限重试
（`catalog/gravitino_sync.py:47,93-104`）。持续失败的 Gravitino（server 宕机/SDK 版本不匹配）
会在慢速远程调用期间持有目录/会话锁、阻塞请求处理线程；熔断确保治理层永远不会拖垮数据面。
触发熔断后日志输出 `gravitino_sync_circuit_open`，提示设置 `gravitino.enabled=false` 或修复
Gravitino server/版本后重启。

### 同步示例

```python
from arrow_lake.catalog.gravitino_bridge import GravitinoBridge

bridge = GravitinoBridge(cfg)

# 将本地目录推送到 Gravitino
entries = catalog_actor.list_all()
synced = bridge.sync_outbound(entries)
print(f"Synced {synced} tables to Gravitino")

# 从 Gravitino 拉取外部表
external = bridge.sync_inbound()
print(f"Discovered {len(external)} external tables")
```

***

## 9. 安全与 RBAC

> **v1.9.0 架构变更**：访问控制的**一等持久化后端是 Turso/libSQL**（`RbacStore`，
> `system_db/stores/rbac.py`），**不是 Gravitino**。Gravitino 是可选治理层，其 RBAC 桥接
> 仅作旁路。RBAC 相关的表（`dataset_acl_grants`/`dataset_row_col_acls`/`schema_acls`/
> `acl_denies`/`role_permissions`）由迁移脚本 `V001__init_rbac.sql` 创建，fail-close，
> 热路径有短 TTL 缓存。**不要高估 Gravitino 角色的作用——Gravitino 关闭时 RBAC 照常生效。**

### Gravitino 自身认证类型

| 类型 | 用途 |
|------|------|
| `simple` | 开发/测试（默认） |
| `oauth` | 使用 OAuth 2.0 提供商的生产环境 |
| `kerberos` | 企业 Hadoop 环境 |
| `null` | 无认证（不安全，仅限测试） |

### 可选的 GravitinoRBACBridge（旁路）

`GravitinoRBACBridge` 将 Arrow Lake 操作映射到 Gravitino 权限，是**可选旁路**而非主路径：

| Arrow Lake 操作 | Gravitino 权限 |
|-----------------|---------------|
| `read` | `SELECT_TABLE` |
| `write` | `INSERT_TABLE` |
| `create` | `CREATE_TABLE` |
| `delete` | `DELETE_TABLE` |
| `admin` | `CREATE_CATALOG` |

### 降级行为

当 Gravitino RBAC 检查失败时（网络错误、服务宕机），桥接返回 `None`，Arrow Lake 回退到
Turso/libSQL `RbacStore`（fail-close）。访问控制始终生效，即使 Gravitino 完全关闭。

```python
from arrow_lake.api.rbac import GravitinoRBACBridge

rbac = GravitinoRBACBridge(cfg)   # 可选旁路；主路径是 RbacStore（Turso/libSQL）
result = rbac.check_permission("user@example.com", "articles", "read")
# result: True（允许）、False（拒绝）、None（回退到 Turso/libSQL RbacStore）
```

***

## 10. 最佳实践与反模式

### 标签治理

| 实践 | 指南 |
|------|------|
| 命名 | 小写 + 下划线 + 域前缀：`pii`、`fin_revenue`、`gdpr_subject` |
| 粒度 | 标记列而非仅标记表——实现精细脱敏 |
| 发现 | 使用 `get_tables_by_tag()` 做合规审计 |
| 避免 | 为每列创建标签（标签爆炸） |

### 策略管理

| 实践 | 指南 |
|------|------|
| 命名 | `{域}_{类型}_{范围}`：`gdpr_retention_90d`、`fin_mask_email` |
| 保留 | 用策略替代 ad-hoc DELETE 做合规 |
| 脱敏 | 在授予分析师访问权限前对 PII 列应用脱敏 |
| 审查 | 季度审查策略——移除过时规则 |

### 模型注册表

| 实践 | 指南 |
|------|------|
| 别名 | 始终维护 `production` 和 `latest` 别名 |
| 热切换 | 在 Gravitino 中更新 alias，不在应用代码中更新 |
| 版本号 | 不复用版本号——始终递增 |
| URI | 使用不可变 URI（如 `s3://models/name/v3`，而非 `s3://models/name/latest`） |

### 性能

| 实践 | 指南 |
|------|------|
| 统计信息 | 大量摄取后采集，业务低峰期定期调度 |
| 同步 | 30 秒默认值足够；不低于 5 秒 |
| 健康 | 批量操作前检查 Gravitino 健康 |
| 降级 | 客户端设计为优雅处理 503 |

### 常见反模式

- **跳过健康检查**：批量治理操作前务必验证 Gravitino 可用性。
- **期望实时同步**：后台同步是最终一致的（5-300 秒延迟）。不要依赖它实现实时一致性。
- **过度打标签**：过度标记使治理变得更难而非更容易。聚焦于合规相关的分类。
- **忽略 503 响应**：将 `/api/v1/metadata/*` 返回的 503 视为"功能不可用"，而非需要重试的错误。

***

## 快速参考

### 端点概要

| 方法 | 端点 | 说明 |
|------|------|------|
| `GET` | `/api/v1/metadata/catalogs` | 列出 Gravitino 目录 |
| `GET` | `/api/v1/metadata/tables` | 列出 Lance 目录中的表 |
| `GET` | `/api/v1/metadata/tables/{name}` | 表详情（列、属性） |
| `GET` | `/api/v1/metadata/tags` | 列出标签（可选 `?table=`） |
| `POST` | `/api/v1/metadata/tags` | 创建标签 |
| `GET` | `/api/v1/metadata/policies` | 列出策略 |
| `POST` | `/api/v1/metadata/policies/retention` | 创建保留策略 |
| `POST` | `/api/v1/metadata/policies/masking` | 创建脱敏策略 |
| `POST` | `/api/v1/metadata/policies/enforce` | 执行保留策略（可选 `?dry_run=true`、`?table=`） |
| `POST` | `/api/v1/metadata/statistics/{name}` | 采集表统计信息 |
| `GET` | `/api/v1/metadata/models` | 列出 ML 模型 |
| `GET` | `/api/v1/metadata/models/{name}/versions` | 模型版本信息 |
| `GET` | `/api/v1/metadata/lineage/{name}` | 表血缘信息 |

### 可运行示例

```bash
# 完整 Gravitino 治理流程（12 步）
python docs/cookbook/examples_api/33_gravitino_metadata_governance.py
```

***

## v1.4.2 — 深度治理集成

> 以下能力让治理形成闭环：策略在查询时执行、统计信息驱动查询路由、模型版本从 Gravitino 解析、血缘建模为表属性。

### 保留策略执行

保留策略由后台 `RetentionEnforcer` 线程执行，周期读取 Gravitino 中的策略，调用 `LanceDataset.cleanup_old_versions()`：

```bash
# 手动触发（先 dry-run）
curl -X POST "http://localhost:8000/api/v1/metadata/policies/enforce?dry_run=true" \
  -H "X-API-Key: your-key"

# 对特定表实际执行
curl -X POST "http://localhost:8000/api/v1/metadata/policies/enforce?table=access_logs" \
  -H "X-API-Key: your-key"
```

配置：`retention_enforce_interval_seconds: 3600`（默认每小时）。

### 查询时列级脱敏

当脱敏策略应用到表时，`MaskingEngine` 在 `apply_table_filter()` 中拦截查询结果。非 admin 角色自动看到脱敏值：

```python
# 在 rbac.py 的 apply_table_filter() 中:
# 1. 列/行 ACL 过滤
# 2. MaskingEngine.apply_masking(table, dataset, role)
#    - redact: 全部替换为 *
#    - hash: SHA-256 截断到 16 字符
#    - partial: 保留首尾 2 字符
#    - nullify: 替换为 null

# Viewer 查询带有 email 脱敏的表时看到:
# email: "user@test.com" → "*************"
# name: "Alice" → "Alice"（未脱敏）
```

### 标签驱动访问控制

`TagAwareACLResolver` 周期将 Gravitino 列级标签同步为本地 ACL：

```yaml
# config.yaml
gravitino:
  tag_access_rules:
    pii:       {visible_to: ["admin"]}
    sensitive: {visible_to: ["admin", "editor"]}
```

当列 `email` 被标记为 `pii` 后，非 admin 角色通过现有 `PermissionChecker` 管道自动从查询结果中排除该列。

### 统计驱动查询路由

`StatsInjector` 从 Gravitino 读取表统计信息并提供提示：

```python
from arrow_lake.query.stats_injector import StatsInjector

injector = StatsInjector(config.gravitino)
hints = injector.get_hints("large_table")
# QueryHints(estimated_rows=5_000_000, column_count=12, size_mb=250.0)

if hints.estimated_rows > config.gravitino.stats_auto_route_threshold:
    # 自动路由到 DuckDB OLAP（流式）而非 Daft（内存）
    pass
```

### 模型注册中心解析

`RegistryModelResolver` 将 Gravitino 模型目录桥接到 embed/rag 模块：

```python
from arrow_lake.embed.registry_resolver import RegistryModelResolver

resolver = RegistryModelResolver(config.gravitino)
model_path = resolver.resolve_model_path("text-embedder")
# → "s3://models/text-embedder/v2"（来自 Gravitino production 版本）

# 在 encoder.py 中: LocalEmbeddingEncoder 可使用此路径替代硬编码的 model_name
```

### 血缘表属性

血缘事件现在将丰富的元数据写入 Gravitino 表属性：

```bash
curl http://localhost:8000/api/v1/metadata/lineage/articles -H "X-API-Key: your-key"
```

```json
{
  "success": true,
  "data": {
    "table": "articles",
    "operation": "ingest",
    "timestamp": "2026-05-22T10:30:00",
    "sources": ["raw/articles.csv"],
    "outputs": ["articles"],
    "lance_version": "5"
  }
}
```

### 联邦查询元数据驱动

`FederatedQueryEngine` 在读取前从 Gravitino 解析表元数据（格式、位置）：

```python
from arrow_lake.query.federated_engine import FederatedQueryEngine

engine = FederatedQueryEngine(config.gravitino)
resolution = engine.resolve_table("hive-catalog.default.orders")
# → TableResolution(format="parquet", location="s3://warehouse/orders")

df = engine.load_dataset("hive-catalog.default.orders")
# → daft.read_parquet("s3://warehouse/orders")  (从元数据自动检测)
```

***

## v1.9.6 — Gravitino 1.3.0 升级与可靠性修复

### 1.3.0 升级要点

- **server 与 SDK 均升 1.3.0**：server `apache/gravitino:1.3.0`；Python SDK pin
  `apache-gravitino==1.3.0`（`pyproject.toml:75`）。SDK 与 server REST 兼容，sync 实测零失败。
- **docker 布局变更**：`GRAVITINO_HOME=/opt/gravitino`（旧版 `/root/gravitino`），数据卷须挂
  `/opt/gravitino/data`，workdir 随之确认。升级走全新启动（重建空卷后重跑 `gravitino-init`，
  实际数据在 MinIO 不受影响）。
- **Catalog S3 属性用 `s3.*`**（`deploy/scripts/init-gravitino.sh:53-55`）：
  `s3.endpoint` / `s3.access-key-id` / `s3.secret-access-key`，location 用 `s3://`。
  旧的 `fs.s3a.*` 在 1.3.0 fileset catalog 上**不生效**（schema 建不出）；lance-catalog 一直是 `s3.*`。
- Web UI v2 已开（`GRAVITINO_USE_WEB_V2=true`）。
- 残余无害告警：`gravitino_list_column_tags_failed ... NoSuchTableException` = tag-ACL sync 对未在
  lance-catalog 注册为 table 的数据集查列标签，per-dataset warning，不致命不阻塞。

### 4-layer SDK 兼容性修复（v1.9.6）

Gravitino Python SDK 1.x 的 API 与旧文档/代码假设不一致，`GravitinoBridge` 已修四处：

1. **`as_schemas()`（非 `as_schema_catalog()`）**：SDK 1.x 删了 `Catalog.as_schema_catalog()`，
   改 `as_schemas()` 返回 `SupportsSchemas`（有 `create_schema`/`schema_exists`/`list_schemas`）。
   `catalog/gravitino_bridge.py:315,319` 已用 `c.as_schemas().schema_exists(...)` /
   `.create_schema(...)`。调用不存在的方法会报 `'RelationalCatalog' object has no attribute
   'as_schema_catalog'`，sync 每 30s 失败循环。
2. **1-level namespace**：`list_filesets(ns)` 的 `ns` 要 **1 级**（只 schema），
   `Namespace.of(metalake, catalog, schema)`（3 级）会报 `must have 1 level`。
   `gravitino_bridge.py:492` 已用 `Namespace.of(_DEFAULT_SCHEMA)`。
3. **`_ensure_schema` 幂等性**：Gravitino `createSchema` 先验 S3 location 再报
   `SchemaAlreadyExists`，对 s3a 配错的 catalog 重确保 schema 会冒 spurious 403。
   `gravitino_bridge.py:314-317` 已改为 `schema_exists(name)` 先查（读 Gravitino 自身元数据，
   不碰 S3）→ 存在则 skip。
4. **代理中和**：Docker daemon 会从 `~/.docker/config.json` 自动给容器注入 `HTTP_PROXY`，导致
   Gravitino 经代理走 → minio SigV4 被改 → 403。`core/http.py:52,65` 的
   `create_http_client` 设 `trust_env=False` + 显式按 `HTTPS_PROXY` 套代理，且 compose
   `gravitino` 服务显式置 `HTTP_PROXY/HTTPS_PROXY: ""` + `NO_PROXY: "*"` 覆盖（server 只连内部
   minio，本不需要代理）。

### 排查口诀

- 改 SDK 调用前先核实方法名：`docker exec api python -c "from gravitino import Catalog;print([m for m in dir(Catalog) if 'schema' in m or m.startswith('as_')])"`。
- sync 连续失败看熔断日志 `gravitino_sync_circuit_open`（5 次后停）。
- 403 多半是 s3a 误配（改 `s3.*`）或代理注入（compose 显式清空 proxy env）。
- Gravitino 卡死时最简办法：`gravitino.enabled=false` 临时关，核心功能不受影响。
