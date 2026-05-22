# Gravitino 元数据治理（实验性）

> **状态：实验性** — v1.4.1 提供基础的 Gravitino 元数据存储和浏览能力。当前实现将元数据（标签、策略、统计、模型版本）存储到 Gravitino，但**尚未形成治理闭环**——策略不会自动执行、标签不驱动访问控制、统计不被查询规划器消费。详见下方能力矩阵。
>
> 本章记录当前可用功能及 API 用法。深度治理集成计划在 v1.4.2 完成。

### 当前能力矩阵

| 功能 | 存储 | 执行 | 状态 |
|------|------|------|------|
| Catalog/Table 浏览 | Gravitino | — | **可用** |
| 标签创建与关联 | Gravitino | 不驱动访问控制 | **仅元数据** |
| 保留策略 | Gravitino | 不自动清理 | **仅元数据** |
| 脱敏策略 | Gravitino | 不转换查询结果 | **仅元数据** |
| 表统计信息 | Gravitino | 查询规划器不消费 | **仅元数据** |
| 模型版本 | Gravitino | 未接入 embed/rag | **仅元数据** |
| RBAC 桥接 | Gravitino SDK | 降级到本地 RBAC | **降级路径** |
| 血缘集成 | 表属性 | 无跨系统血缘图 | **浅层** |
| 联邦查询 | 路径拼接 | 无元数据驱动读取 | **仅路径前缀** |

***

## 1. 架构概览

Arrow Lake 作为 Gravitino 元数据操作的代理层，客户端调用 `/metadata/*` 端点，由 Arrow Lake 委托给 Gravitino：

```text
Client → Arrow Lake API (/metadata/*)
            ├── GravitinoBridge (REST)       ← 目录、表、统计
            ├── GravitinoTagService (SDK)    ← 标签
            ├── GravitinoPolicyService (SDK) ← 策略
            └── GravitinoModelRegistry (SDK) ← 模型
                    ↓
            Apache Gravitino Server (:8090)
```

### 配置

```yaml
gravitino:
  enabled: true
  uri: "http://gravitino:8090"
  metalake: "arrow-lake"
  lance_rest_enabled: true
  lance_rest_uri: "http://lance-rest:9002"
  sync_direction: bidirectional
  sync_interval_seconds: 30
```

所有 Gravitino 调用均有 `try/except` 保护——Gravitino 不可用时 Arrow Lake 使用本地 DuckDB 目录正常运行。

***

## 2. 场景 A — 数据发现与目录浏览

**角色**：新入职数据工程师，探索数据湖中有哪些数据集。

```bash
# 健康检查
curl http://localhost:8000/health -H "X-API-Key: your-key"
# → {"status":"ok", "gravitino":"healthy", "lance_rest":"healthy"}

# 浏览目录
curl http://localhost:8000/metadata/catalogs -H "X-API-Key: your-key"
# → lance-catalog, minio-fileset, ml-models

# 列出表
curl http://localhost:8000/metadata/tables -H "X-API-Key: your-key"
# → articles, sales, transactions ...

# 查看表结构
curl http://localhost:8000/metadata/tables/articles -H "X-API-Key: your-key"
# → columns: [id:long, title:string, text_content:string, ...]
```

**关键收获**：无需预先知道表名，通过 catalogs → tables → detail 逐层探索即可发现完整元数据。

***

## 3. 场景 B — 数据分类与标签治理

**角色**：数据管家为 GDPR 合规打标签，确保 PII 列正确分类。

> **注意**：标签当前仅存储在 Gravitino 中，**不驱动访问控制或自动脱敏**。用于发现和审计。执行能力计划在 v1.4.2 实现。

### 预置标签

| 标签 | 用途 |
|------|------|
| `sensitive` | 敏感信息 |
| `pii` | 个人身份信息 |
| `financial` | 金融/账单数据 |
| `expires:30d` | 30 天保留标记 |

### 创建标签 (REST)

```bash
curl -X POST "http://localhost:8000/metadata/tags?body=%7B%22name%22%3A%22gdpr_subject%22%2C%22comment%22%3A%22GDPR%22%7D" \
  -H "X-API-Key: your-key"
```

### 关联标签到表/列 (Python SDK)

标签关联需要 Python SDK（暂无 REST 端点）：

```python
from arrow_lake.quality.gravitino_tags import GravitinoTagService

tags = GravitinoTagService(cfg)
tags.tag_table("users", ["pii", "sensitive"])
tags.tag_column("users", "email", ["pii"])
pii_tables = tags.get_tables_by_tag("pii")
```

### 查询标签 (REST)

```bash
curl "http://localhost:8000/metadata/tags?table=users" -H "X-API-Key: your-key"
# → [{"name": "pii"}, {"name": "sensitive"}]
```

**关键收获**：列级标签实现精细治理（标记 PII 列），表级标签实现粗粒度分类（标记数据域）。

***

## 4. 场景 C — 合规策略：保留与脱敏

**角色**：合规官配置数据保留规则和列级脱敏策略。

> **注意**：策略当前仅存储在 Gravitino 中，**不自动执行**。创建保留策略不会删除数据，创建脱敏策略不会转换查询结果。执行能力计划在 v1.4.2 实现。

```bash
# 创建保留策略（90 天）
curl -X POST "http://localhost:8000/metadata/policies/retention?body=%7B%22name%22%3A%22log_retention_90d%22%2C%22days%22%3A90%7D" \
  -H "X-API-Key: your-key"

# 创建脱敏策略
curl -X POST "http://localhost:8000/metadata/policies/masking?body=%7B%22name%22%3A%22email_mask%22%2C%22columns%22%3A%5B%22email%22%2C%22phone%22%5D%7D" \
  -H "X-API-Key: your-key"

# 列出策略
curl http://localhost:8000/metadata/policies -H "X-API-Key: your-key"
```

应用策略到表（Python SDK）：

```python
from arrow_lake.quality.gravitino_policies import GravitinoPolicyService

svc = GravitinoPolicyService(cfg)
svc.apply_policy("log_retention_90d", "access_logs")
svc.apply_policy("email_mask", "users")
```

**关键收获**：策略将治理意图与执行分离——声明式定义规则，再应用到表，策略引擎负责清理和脱敏。

***

## 5. 场景 D — ML 模型生命周期管理

**角色**：ML 工程师管理模型版本，实现生产环境热切换。

> **注意**：模型注册表当前是独立的元数据存储，`embed/` 和 `rag/` 模块**未**从 Gravitino 读取模型版本。模型热切换需手动集成。计划在 v1.4.2 完成。

```python
from arrow_lake.catalog.gravitino_models import GravitinoModelRegistry

registry = GravitinoModelRegistry(cfg)

# 注册模型
registry.register_model("text-embedder", "文本嵌入模型", {"dimension": "768"})

# 添加版本
registry.add_version("text-embedder", "s3://models/text-embedder/v1", aliases=["latest"])
registry.add_version("text-embedder", "s3://models/text-embedder/v2", aliases=["latest"])

# 上线到 production
registry.add_version("text-embedder", "s3://models/text-embedder/v2", aliases=["production"])
```

查询版本信息：

```bash
curl http://localhost:8000/metadata/models/text-embedder/versions -H "X-API-Key: your-key"
# → [{"version":2, "aliases":["latest"], "tier":"latest"},
#    {"version":2, "aliases":["production"], "tier":"production"}]
```

**热切换**：在 Gravitino 中更新 `production` alias 指向新版本，应用下次启动自动使用新模型。

***

## 6. 场景 E — 统计信息驱动查询优化

**角色**：性能工程师采集表统计信息，改善查询规划。

> **注意**：统计信息当前存储在 Gravitino 但**不被 DuckDB 查询规划器消费**，仅用于监控。查询规划器集成计划在 v1.4.2 实现。

```bash
# 采集统计信息
curl -X POST http://localhost:8000/metadata/statistics/articles \
  -H "X-API-Key: your-key"
# → {"row_count":50000, "column_count":8, "size_mb":125.4, "columns":[...]}
```

统计信息以 `stats.*` 前缀存储为 Gravitino 表属性，查询引擎据此优化 join 排序和过滤下推。

建议在大量摄取后和业务低峰期定期采集。

***

## 7. 场景 F — 健康检查与优雅降级

**角色**：SRE 验证 Gravitino 不可用时的系统行为。

### 降级矩阵

| 功能 | Gravitino 正常 | Gravitino 不可用 |
|------|---------------|-----------------|
| 数据摄取 | 正常 + 同步到 Gravitino | 正常（仅 DuckDB） |
| 向量/全文搜索 | 正常 | 正常 |
| OLAP 查询 | 正常 + 联邦查询 | 正常 |
| `/metadata/*` 端点 | 完整数据 | 503 |
| 标签与策略 | 完整 CRUD | 503 或空结果 |
| 健康检查 | `gravitino: healthy` | `gravitino: unhealthy` |

```python
# 安全的元数据访问模式
resp = client.metadata_get_table(name)
if resp.get("status") == 503:
    # 降级到本地 DuckDB 目录
    detail = client.get_dataset(name)
```

**关键收获**：Arrow Lake 核心功能不依赖 Gravitino——它是增强层，不是硬依赖。

***

## 8. 后台同步与双向对账

`GravitinoSyncScheduler` 作为守护线程运行在 API 进程中：

- **outbound**：DuckDB 目录 → Gravitino Tables + Filesets
- **inbound**：Gravitino 外部 Filesets → DuckDB 目录
- **bidirectional**（默认）：双向同步

同步间隔 `sync_interval_seconds` 范围 5–300 秒，默认 30 秒。随 API 进程生命周期启停。

***

## 9. 安全与 RBAC 桥接

### 权限映射

| Arrow Lake 操作 | Gravitino 权限 |
|-----------------|---------------|
| `read` | `SELECT_TABLE` |
| `write` | `INSERT_TABLE` |
| `create` | `CREATE_TABLE` |
| `delete` | `DELETE_TABLE` |
| `admin` | `CREATE_CATALOG` |

Gravitino RBAC 检查失败时返回 `None`，Arrow Lake 降级到本地 JWT/RBAC——确保访问控制始终生效。

***

## 10. 最佳实践与常见陷阱

### 标签治理

- 命名规范：小写 + 下划线 + 域前缀，如 `pii`、`fin_revenue`
- 优先列级标签，实现精细治理
- 定期用 `get_tables_by_tag()` 做合规审计
- 避免为每列创建标签（标签爆炸）

### 策略管理

- 命名规范：`{域}_{类型}_{范围}`，如 `gdpr_retention_90d`
- 用策略替代 ad-hoc DELETE 做合规
- 季度审查策略，移除过时规则

### 模型管理

- 始终维护 `production` 和 `latest` 别名
- 热切换：更新 Gravitino 中的 alias，不改代码
- URI 使用不可变路径（`s3://models/name/v3`，不用 `latest`）

### 性能

- 大量摄取后采集统计信息
- 同步间隔不低于 5 秒
- 批量操作前检查 Gravitino 健康
- 客户端设计处理 503 为"功能不可用"，而非需要重试的错误

***

## 快速参考

| 方法 | 端点 | 说明 |
|------|------|------|
| `GET` | `/metadata/catalogs` | 列出 Catalog |
| `GET` | `/metadata/tables` | 列出表 |
| `GET` | `/metadata/tables/{name}` | 表详情 |
| `GET` | `/metadata/tags` | 列出标签 |
| `POST` | `/metadata/tags` | 创建标签 |
| `GET` | `/metadata/policies` | 列出策略 |
| `POST` | `/metadata/policies/retention` | 创建保留策略 |
| `POST` | `/metadata/policies/masking` | 创建脱敏策略 |
| `POST` | `/metadata/statistics/{name}` | 采集统计 |
| `GET` | `/metadata/models` | 列出模型 |
| `GET` | `/metadata/models/{name}/versions` | 模型版本 |

```bash
# 完整治理流程（12 步）
python docs/cookbook/examples_api/33_gravitino_metadata_governance.py
```
