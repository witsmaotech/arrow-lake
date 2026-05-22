# Apache Gravitino 与 Arrow Lake 深度集成可行性分析报告

> 日期: 2026-05-20 | 基线: v1.4.0 | 目标版本: v1.4.1 | Gravitino: 1.2.1 | 分析目标: 元数据治理 + 数据治理深度集成

---

## 1. 执行摘要

Apache Gravitino 作为统一的元数据湖（Metalake），能够为 Arrow Lake 当前的 **DuckDB + Lance + Daft** 技术栈提供缺失的跨数据源元数据联邦、统一权限模型、标签治理、ML 模型版本化等能力。两者在架构层面高度互补，集成可行性评估为 **高**。

### 核心结论

| 维度 | 评估 | 说明 |
|------|------|------|
| 架构兼容性 | **高** | Gravitino 四层模型可直接映射到 Arrow Lake 现有分层 |
| Daft 集成 | **原生支持** | Gravitino 已有 Daft Connector (`GravitinoCatalog`) |
| Lance 集成 | **原生支持** | Lance REST Catalog 专用服务 + lakehouse-generic 双路径 |
| DuckDB 集成 | **保持独立** | DuckDB 负责 OLAP 查询，Gravitino 负责元数据联邦 |
| 安全增强 | **高** | RBAC + DAC 双模型可替代当前简单角色矩阵 |
| 治理能力 | **高** | Tags + Policies + Statistics 填补当前空白 |

---

## 2. 当前 Arrow Lake 元数据架构分析

### 2.1 现有元数据存储

```
┌─────────────────────────────────────────────────────────┐
│                    Arrow Lake 元数据层                    │
├─────────────────────────────────────────────────────────┤
│                                                         │
│  DuckDB (catalog.db)          Lance (_lineage_events)   │
│  ┌─────────────────────┐     ┌───────────────────────┐  │
│  │ catalog_tables      │     │ LineageEvent          │  │
│  │  - name (PK)        │     │  - event_id           │  │
│  │  - schema_json      │     │  - dataset_name       │  │
│  │  - location         │     │  - operation          │  │
│  │  - status           │     │  - source_datasets    │  │
│  │  - created/updated  │     │  - transform_type     │  │
│  └─────────────────────┘     │  - lance_version      │  │
│                               └───────────────────────┘  │
│                                                         │
│  内存模型 (frozen dataclass)                              │
│  ┌─────────────────────┐                                │
│  │ CatalogEntry        │   HealthInfo                   │
│  │ CatalogResult       │                                │
│  └─────────────────────┘                                │
└─────────────────────────────────────────────────────────┘
```

### 2.2 现有治理能力

| 能力 | 当前实现 | 缺失 |
|------|---------|------|
| 元数据存储 | DuckDB catalog.db | 无跨数据源联邦 |
| 数据血缘 | Lance 事件溯源 + BFS 图遍历 | 无标准协议，不可跨平台 |
| 访问控制 | API Key + JWT + RBAC (VIEWER/EDITOR/ADMIN) | 无细粒度行列级权限 |
| 质量管理 | QualityFilterRegistry (AND/OR 链) | 无数据分类/敏感标签 |
| Schema 管理 | SchemaCompatibilityChecker | 无跨表 Schema 联邦 |
| 模型管理 | 无 | 无 ML 模型版本化 |

### 2.3 关键限制

1. **元数据孤岛**: DuckDB catalog.db 仅管理 Lance 数据集，无法统一管理外部数据源（MySQL、PostgreSQL、Kafka 等）
2. **权限粒度不足**: 三角色矩阵（VIEWER/EDITOR/ADMIN）+ 每数据集 ACL，缺少列级/行级权限
3. **无数据分类**: 缺少 Tags/Policies 对数据进行敏感度分类和合规策略管理
4. **血缘不可互操作**: 自定义 Lance 格式存储血缘，无法与外部工具（OpenLineage、DataHub 等）互操作
5. **无模型版本化**: 嵌入模型、RAG 模型缺少版本管理和 URI 追踪

---

## 3. Gravitino 能力映射

### 3.1 四层模型到 Arrow Lake 映射

```
Gravitino                          Arrow Lake (映射后)
─────────                          ──────────────────
Metalake                           arrow-lake (组织级)
  └── Catalog                      ┌── lance-catalog (Lance 数据集)
  │     type: RELATIONAL           │   provider: lakehouse-generic / 自定义
  │     └── Schema                 │   └── Schema: default, analytics, ...
  │           └── Table            │       └── Table: documents, images, ...
  │                                │
  └── Catalog                      └── duckdb-catalog (DuckDB OLAP)
  │     type: RELATIONAL           │   provider: jdbc-待开发 / REST
  │     └── Schema                 │   └── Schema: olap, cache, ...
  │                                │
  └── Catalog                      └── minio-fileset (MinIO 对象)
  │     type: FILESET              │   provider: fileset
  │     └── Schema                 │   └── Schema: uploads, exports, backups
  │           └── Fileset          │       └── Fileset: raw/, processed/, ...
  │                                │
  └── Catalog                      └── ml-models (嵌入/RAG 模型)
  │     type: MODEL                │   provider: model
  │     └── Schema                 │   └── Schema: embeddings, llm
  │           └── Model            │       └── Model: bge-small, qwen2...
  │                                │
  └── Catalog                      └── kafka-source (Kafka 数据源)
        type: MESSAGING            │   provider: kafka
        └── Schema                 │   └── Schema: topics
              └── Topic            │       └── Topic: ingest-events, ...
```

### 3.2 Gravitino 能力 vs Arrow Lake 需求

| Gravitino 能力 | Arrow Lake 需求 | 匹配度 | 说明 |
|---------------|----------------|--------|------|
| **Metalake 多租户** | 多项目/多环境隔离 | 高 | 当前无租户隔离 |
| **14 种 Catalog** | 统一管理多数据源 | 高 | 当前仅 Lance 数据集 |
| **Daft Connector** | 原生 Daft 集成 | **原生** | `GravitinoCatalog` 直接可用 |
| **Tags 标签系统** | 数据分类/敏感度 | 高 | 当前无标签系统 |
| **Policies 策略** | 数据合规/保留 | 高 | 当前无策略引擎 |
| **RBAC + DAC** | 细粒度权限控制 | 高 | 替代当前三角色矩阵 |
| **Statistics 统计** | 查询优化/成本估算 | 中 | 可增强 DuckDB 优化器 |
| **Model Catalog** | ML 模型版本化 | 高 | 管理嵌入模型和 LLM 版本 |
| **Fileset Catalog** | MinIO 文件管理 | 中 | 可替代当前 BlobStoreManager |
| **UDF 管理** | 自定义函数注册 | 低 | 优先级较低 |

---

## 4. 集成架构设计

### 4.1 目标架构

```
┌──────────────────────────────────────────────────────────────────┐
│                         客户端层                                  │
│  FastAPI Router  │  CLI (Click)  │  Python SDK  │  Grafana       │
├──────────────────────────────────────────────────────────────────┤
│                    Arrow Lake Facade (Lake)                       │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────────────┐    │
│  │ Query    │ │ Ingest   │ │ Search   │ │ RAG / KG         │    │
│  │ Mixin    │ │ Mixin    │ │ Mixin    │ │ Mixin            │    │
│  └────┬─────┘ └────┬─────┘ └────┬─────┘ └────┬─────────────┘    │
│       │            │            │              │                   │
├───────┼────────────┼────────────┼──────────────┼──────────────────┤
│       ▼            ▼            ▼              ▼                   │
│  ┌─────────────────────────────────────────────────────────┐     │
│  │            Gravitino 元数据联邦层 (新增)                   │     │
│  │  ┌────────────┐ ┌──────────┐ ┌──────────┐ ┌─────────┐  │     │
│  │  │ Catalog    │ │ Tag      │ │ Policy   │ │ Model   │  │     │
│  │  │ Federation │ │ Service  │ │ Engine   │ │ Registry│  │     │
│  │  └────────────┘ └──────────┘ └──────────┘ └─────────┘  │     │
│  │  ┌────────────┐ ┌──────────┐ ┌──────────┐              │     │
│  │  │ Lineage    │ │ Access   │ │ Stats    │              │     │
│  │  │ Bridge     │ │ Control  │ │ Collector│              │     │
│  │  └────────────┘ └──────────┘ └──────────┘              │     │
│  └──────────────────────────┬──────────────────────────────┘     │
│                              │ Gravitino REST API (:8090)        │
├──────────────────────────────┼───────────────────────────────────┤
│                    存储与计算层 │                                   │
│  ┌───────┐ ┌───────┐ ┌──────┴──┐ ┌──────────┐ ┌──────────┐     │
│  │ Lance │ │ DuckDB│ │Gravitino│ │ HugeGraph│ │ MinIO    │     │
│  │ Data  │ │ OLAP  │ │ Server  │ │ KG       │ │ S3       │     │
│  └───────┘ └───────┘ └─────────┘ └──────────┘ └──────────┘     │
│                      (新容器)                                    │
└──────────────────────────────────────────────────────────────────┘
```

### 4.2 集成层次

| 层次 | 集成点 | 改动范围 |
|------|--------|---------|
| **L1: 部署集成** | docker-compose 添加 Gravitino 1.2.1 + Lance REST Catalog 容器 | 低 |
| **L2: 元数据同步** | CatalogActor → Gravitino Catalog 双向同步 | 中 |
| **L3: Daft 联邦** | DaftQueryEngine 使用 GravitinoCatalog | 中 |
| **L4: 安全增强** | Gravitino RBAC + DAC 替代当前权限模型 | 高 |
| **L5: 治理增强** | Tags + Policies + Statistics | 中 |
| **L6: 模型管理** | Model Catalog 管理嵌入/LLM 版本 | 低 |

> 以上所有层次均纳入 **v1.4.1** 统一实施。

---

## 5. 深度集成方案

### 5.1 部署集成 + 元数据同步

#### Docker Compose 集成

```yaml
# deploy/docker-compose.prod.yml 新增 (v1.4.1)
gravitino:
  image: apache/gravitino:1.2.1
  container_name: arrow-lake-gravitino
  restart: unless-stopped
  networks:
    - arrow-lake-net
  ports:
    - "${GRAVITINO_PORT:-8090}:8090"
  environment:
    GRAVITINO_AUTHENTICATORS: "simple"
    GRAVITINO_AUTHORIZATION_ENABLE: "true"
  volumes:
    - gravitino-data:/root/gravitino/data
  deploy:
    resources:
      limits:
        memory: ${GRAVITINO_MEMORY_LIMIT:-1G}
        cpus: "${GRAVITINO_CPU_LIMIT:-1.0}"
  healthcheck:
    test: ["CMD-SHELL", "curl -sf http://localhost:8090/api/metalakes"]
    interval: 15s
    timeout: 5s
    retries: 5

lance-rest:
  image: apache/gravitino-lance:1.2.1
  container_name: arrow-lake-lance-rest
  restart: unless-stopped
  networks:
    - arrow-lake-net
  ports:
    - "${LANCE_REST_PORT:-9002}:9002"
  environment:
    gravitino.lance-rest.uri: "http://lance-rest:9002"
    gravitino.lance-rest.store: "lance"
    gravitino.lance-rest.lance.db.path: "/data/lance"
    AWS_ACCESS_KEY_ID: "${MINIO_ROOT_USER}"
    AWS_SECRET_ACCESS_KEY: "${MINIO_ROOT_PASSWORD}"
    AWS_ENDPOINT_URL: "http://minio:9000"
    AWS_REGION: "${AWS_REGION:-us-east-1}"
  volumes:
    - lake-data:/data/lance
  depends_on:
    minio:
      condition: service_healthy
  deploy:
    resources:
      limits:
        memory: ${LANCE_REST_MEMORY_LIMIT:-512M}
        cpus: "${LANCE_REST_CPU_LIMIT:-0.5}"
  healthcheck:
    test: ["CMD-SHELL", "curl -sf http://localhost:9002/v1/namespaces"]
    interval: 15s
    timeout: 5s
    retries: 5
```

#### 元数据双向同步

```python
# arrow_lake/catalog/gravitino_bridge.py (新增)
class GravitinoBridge:
    """双向同步: DuckDB catalog_tables ↔ Gravitino Catalog"""

    def __init__(self, uri: str, metalake: str) -> None:
        self._client = GravitinoClient(uri=uri, metalake_name=metalake)

    def register_dataset(self, entry: CatalogEntry) -> None:
        """Arrow Lake 新数据集 → Gravitino Table 注册"""

    def sync_from_gravitino(self) -> list[CatalogEntry]:
        """Gravitino 外部表变更 → Arrow Lake 目录更新"""

    def get_statistics(self, table: str) -> TableStatistics:
        """获取 Gravitino 统计信息用于查询优化"""
```

**NO_PROXY 更新**: 添加 `gravitino` 到 NO_PROXY 列表。

### 5.2 Daft 联邦查询

```python
# arrow_lake/query/daft_api.py 增强
from daft.io import GravitinoCatalog

class DaftQueryEngine:
    def __init__(self, config: ArrowLakeConfig) -> None:
        # 新增: Gravitino 作为 Catalog 后端
        if config.gravitino.enabled:
            self._gravitino = GravitinoCatalog(
                name=f"{config.gravitino.metalake}.{config.gravitino.catalog}",
                uri=config.gravitino.uri,
            )

    def query_federated(self, sql: str) -> DaftDataFrame:
        """跨 Catalog 联邦查询: Lance + Iceberg + JDBC"""
```

**价值**: Arrow Lake 的 Daft 查询引擎可访问 Gravitino 管理的所有数据源（MySQL、PostgreSQL、Iceberg、Hive 等），突破当前仅查询 Lance 数据集的限制。

### 5.3 安全增强

```
当前: API Key + JWT + 三角色矩阵 (VIEWER/EDITOR/ADMIN)
  ↓ 增强
目标: Gravitino RBAC + DAC + 15+ 细粒度权限

Gravitino 权限映射:
  SELECT_TABLE  → VIEWER 查询权限
  INSERT_TABLE  → EDITOR 摄取权限
  CREATE_TABLE  → EDITOR 创建权限
  DELETE_TABLE  → ADMIN 删除权限
  USE_CATALOG   → 数据集可见性
```

```python
# arrow_lake/api/rbac.py 增强
class GravitinoRBACBridge:
    """委托 Gravitino 进行访问控制决策"""

    def check_permission(self, user: str, resource: str, action: str) -> bool:
        role_privileges = self._client.get_role_privileges(user)
        return self._evaluate(resource, action, role_privileges)
```

### 5.4 Tags + Policies 治理

```python
# arrow_lake/quality/gravitino_tags.py (新增)
class GravitinoTagService:
    """通过 Gravitino Tags 实现数据分类"""

    def tag_sensitive(self, table: str, columns: list[str]) -> None:
        """标记敏感列 (PII, 金融数据等)"""

    def get_compliance_policy(self, tag: str) -> Policy:
        """获取合规策略 (GDPR, 数据保留等)"""

    def audit_access(self, user: str, table: str) -> bool:
        """检查用户是否有权访问带标签的数据"""
```

**价值**: 当前的 `QualityFilterRegistry` 可与 Gravitino Tags 联动——带 `sensitive` 标签的列自动脱敏，带 `expires:30d` 标签的数据自动触发生命周期管理。

### 5.5 模型版本管理

```python
# 利用 Gravitino Model Catalog 管理嵌入模型
gravitino_client.register_model(
    "bge-small-zh-v1.5",
    comment="BGE 中文嵌入模型",
    properties={"dimensions": "512", "normalize": "true"}
)
gravitino_client.add_model_version(
    "bge-small-zh-v1.5",
    uri="s3://arrow-lake/models/bge-small-zh/v1.5/model.onnx",
    aliases=["production", "latest"]
)
```

**价值**: 当前 `config/media.py` 中嵌入模型配置是静态的，通过 Gravitino Model Catalog 可实现模型版本化管理、A/B 测试、一键回滚。

---

## 6. 风险与挑战

### 6.1 技术风险

| 风险 | 影响 | 缓解措施 |
|------|------|---------|
| Gravitino 无 DuckDB Catalog Provider | 无法直接管理 DuckDB 元数据 | DuckDB 保持独立 OLAP 角色，Gravitino 管理数据集元数据 |
| 元数据同步一致性 | 双写不一致风险 | Gravitino 作为唯一元数据源，DuckDB 降级为缓存 |
| Gravitino Server 单点 | 元数据不可用 | Docker healthcheck + 持久化卷 + 后续 HA 部署 |
| Daft Connector 成熟度 | 生产稳定性不确定 | 当前 Daft 版本验证 + 降级到原生 Daft 查询 |
| Lance REST Catalog 版本兼容 | 与 LanceDB 版本可能不匹配 | 锁定 Gravitino 1.2.1 + Lance 版本矩阵测试 |

### 6.2 Lance 集成方案 (原生支持)

Gravitino **原生支持 Lance 格式**，提供两条一等集成路径：

**路径 A: Lance REST Catalog Service (推荐 — 专用服务)**

Gravitino 提供独立的 Lance REST Catalog 服务，原生管理 Lance 数据集：

```bash
# Docker 一键部署
docker run -itd --name lance-rest -p 9002:9002 apache/gravitino-lance:1.2.1
```

提供完整 REST API，三级命名空间 `Namespace → Dataset → Version`：

| 操作 | 方法 | 端点 |
|------|------|------|
| 创建命名空间 | POST | `/v1/namespaces` |
| 列出命名空间 | GET | `/v1/namespaces` |
| 创建数据集 | POST | `/v1/namespaces/{ns}/datasets` |
| 获取数据集 | GET | `/v1/namespaces/{ns}/datasets/{ds}` |
| 更新数据集 | PATCH | `/v1/namespaces/{ns}/datasets/{ds}` |
| 删除数据集 | DELETE | `/v1/namespaces/{ns}/datasets/{ds}` |

配置：

```properties
gravitino.lance-rest.uri = http://localhost:9002
gravitino.lance-rest.store = lance
gravitino.lance-rest.lance.db.path = /data/lance
```

**路径 B: lakehouse-generic Catalog (元数据联邦)**

官方文档明确说明：

> "Currently, Gravitino fully supports the **Lance** lakehouse format, with plans to extend support to additional formats in the future."

通过 `lakehouse-generic` Provider 在 Gravitino 主服务中统一管理 Lance 数据集元数据：

```json
{
  "name": "lance-catalog",
  "type": "RELATIONAL",
  "provider": "lakehouse-generic",
  "properties": {
    "location": "s3a://arrow-lake/"
  }
}
```

**Arrow Lake 集成建议**: 两条路径组合使用——Lance REST Catalog 作为 Lance 数据集的专用管理接口（版本、Schema、统计信息），lakehouse-generic 作为 Gravitino 主服务中 Lance 元数据的联邦入口，实现跨 Catalog 查询。

### 6.3 DuckDB 集成方案

**推荐方案: 保持 DuckDB 独立 + Gravitino 元数据联邦**

```
Arrow Lake → DuckDB (OLAP 查询引擎，不变)
           → Gravitino Lance REST Catalog (Lance 数据集元数据管理)
           → Gravitino lakehouse-generic (跨 Catalog 元数据联邦)
```

DuckDB 继续负责 OLAP 查询和缓存，Gravitino 负责元数据联邦和治理。无需迁移 DuckDB。

---

## 7. v1.4.1 实施清单

### 7.1 基础设施部署

- [ ] docker-compose 添加 Gravitino Server 1.2.1 容器 (port 8090)
- [ ] docker-compose 添加 Lance REST Catalog 1.2.1 容器 (port 9002)
- [ ] 创建 `arrow-lake` Metalake
- [ ] 创建 `lance-catalog` (lakehouse-generic, 原生 Lance 支持)
- [ ] 创建 `minio-fileset` (Fileset Catalog)
- [ ] Lance REST Catalog 连接到 MinIO Lance 数据路径
- [ ] NO_PROXY / 网络配置更新 (添加 gravitino, lance-rest)
- [ ] 健康检查端点集成

### 7.2 元数据同步

- [ ] `GravitinoBridge` 实现 (双向同步)
- [ ] CatalogActor 集成: 新数据集自动注册到 Gravitino
- [ ] Schema 信息同步: PyArrow Schema → Gravitino Column 定义
- [ ] Lineage Bridge: Arrow Lake 血缘事件 → Gravitino 关联
- [ ] Daft 联邦查询: `GravitinoCatalog` 集成

### 7.3 安全与治理

- [ ] Gravitino RBAC + DAC 替代/增强当前权限模型
- [ ] Tags 系统: 数据分类、敏感度标记
- [ ] Policies: 数据保留、合规策略
- [ ] Statistics: 查询优化统计信息收集

### 7.4 高级特性

- [ ] Model Catalog: 嵌入/LLM 模型版本管理
- [ ] 跨 Catalog 联邦查询 (Lance + Iceberg + JDBC)
- [ ] Gravitino Web UI 集成到 Grafana dashboard
- [ ] Python SDK 封装: 统一 `gravitino-python-client`
- [ ] `gravitino >= 0.7.0` 依赖添加到 pyproject.toml

---

## 8. 资源估算

### 基础设施增量

| 组件 | CPU | 内存 | 存储 | 说明 |
|------|-----|------|------|------|
| Gravitino Server 1.2.1 | 1 核 | 1 GB | 5 GB | 元数据联邦服务 |
| Lance REST Catalog 1.2.1 | 0.5 核 | 512 MB | 共享 lake-data | Lance 数据集管理 |
| **增量合计** | **1.5 核** | **1.5 GB** | **5 GB** | |

### 依赖新增

```toml
# pyproject.toml
gravitino = ">=0.7.0"   # Python SDK (Gravitino 1.2.1 兼容)
```

---

## 9. 结论与建议

### 核心判断

1. **Gravitino 是 Arrow Lake 元数据治理的最佳补充**: 当前平台在元数据联邦、数据分类、细粒度权限方面有明显空白，Gravitino 的四层模型 + Tags + Policies + RBAC/DAC 恰好填补这些空白。

2. **Lance 原生支持是最大优势**: Gravitino 1.2.1 提供 Lance REST Catalog 专用服务 (`apache/gravitino-lance:1.2.1`) 和 `lakehouse-generic` 双路径原生支持 Lance 格式。Lance 是 Gravitino 的一等公民，无需中间层。

3. **Daft 原生支持进一步降低成本**: Gravitino 已有 Daft Connector，配合 Lance 原生支持，Arrow Lake 的 Daft + Lance 技术栈可实现零适配集成。

4. **v1.4.1 一版本聚合交付**: 部署、元数据同步、安全治理、模型管理统一纳入 v1.4.1，减少版本碎片。

### 版本路径

```
v1.4.0 (当前)
  │
  └─ v1.4.1: Gravitino 1.2.1 全栈集成
               ├─ Gravitino Server + Lance REST Catalog 部署
               ├─ 元数据双向同步 + Daft 联邦查询
               ├─ RBAC/DAC 安全增强 + Tags/Policies 治理
               └─ Model Catalog + 跨源联邦查询
```

---

## 附录 A: Gravitino Catalog 类型与 Arrow Lake 映射详表

| Gravitino Catalog | Provider | Arrow Lake 用途 |
|-------------------|----------|----------------|
| Lance 数据集 | lakehouse-generic | 主数据存储 |
| Lance REST | lance-rest (port 9002) | Lance 专用管理接口 |
| MinIO 文件 | fileset | 上传/导出/备份 |
| DuckDB 元数据 | (保持独立) | OLAP 元数据 |
| Kafka Topic | kafka | 实时数据源 |
| ML 模型 | model | 嵌入/LLM 版本管理 |
| Iceberg 表 | lakehouse-iceberg | 外部湖仓集成 |
| MySQL | jdbc-mysql | 外部数据源联邦 |
| PostgreSQL | jdbc-postgresql | 外部数据源联邦 |

## 附录 B: 配置模板

```yaml
# config/gravitino.yml (新增配置节)
gravitino:
  enabled: true
  uri: "http://gravitino:8090"
  metalake: "arrow-lake"
  auth:
    type: "simple"  # simple | oauth | kerberos
    # oauth:
    #   server_uri: ""
    #   client_id: ""
    #   jwks_uri: ""
  catalogs:
    lance:
      name: "lance-catalog"
      type: "RELATIONAL"
      provider: "lakehouse-generic"
    fileset:
      name: "minio-fileset"
      type: "FILESET"
      provider: "fileset"
      location: "s3a://arrow-lake/"
    model:
      name: "ml-models"
      type: "MODEL"
      provider: "model"
  sync:
    interval_seconds: 30
    direction: "bidirectional"  # outbound | inbound | bidirectional
```

## 附录 C: API 端点映射

| Arrow Lake 端点 | Gravitino 1.2.1 对应 | 集成方式 |
|-----------------|----------------------|---------|
| `GET /datasets` | `GET /api/metalakes/{m}/catalogs/{c}/schemas/{s}/tables` | 双向同步 |
| `POST /datasets` | `POST /.../tables` | 双写 |
| `DELETE /datasets/{name}` | `DELETE /.../tables/{t}` | 同步 |
| `GET /lineage/history` | Lance REST `/v1/namespaces/{ns}/datasets/{ds}` + Tags | 增强 |
| `GET /health` | Gravitino health + Lance REST health + 本地 health | 聚合 |
| RBAC 权限检查 | `GET /api/metalakes/{m}/users/{u}/roles` | 委托 |
