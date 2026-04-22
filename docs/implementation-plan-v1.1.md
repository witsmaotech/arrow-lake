# Arrow Lake v1.1 实施规划 — 系统稳定 + 生产级可用 + DuckDB 高可用

**版本**: v1.1-plan | **日期**: 2026-04-21
**基于**: v1.0 发布，9 个 E2E 示例验证通过，BMAD 四方评审完成
**状态**: 待审批
**视角**: 架构师(Winston) + 开发(Amelia) + 产品经理(John)

---

## Context

v1.0 已实现功能完备的数据湖平台（24,650 行，140 文件，36+ API surface，187 测试文件）。但代码质量 8/10 而生产就绪度仅 5/10。v1.1 聚焦三大目标：

1. **系统稳定** — 修复已知 bug，建立结构化错误体系，消除静默失败
2. **生产级可用** — 可观测性落地，备份恢复加固，度量体系完善
3. **DuckDB 高可用** — 连接池优化，查询治理，资源隔离，故障恢复

**不做的（v1.2+）**：多租户 RBAC、新查询功能、知识图谱增强、Ray 运行时重构

---

## Phase 1: 系统稳定（Sprint 1-2）

### 1.1 S3 后端 Bug 修复

**问题**: `list_tags()` 和 `read_at_tag()` 使用本地路径 `self._lance_dir(name)` 而非 S3 URI，S3 后端完全不可用。

**修复文件**: `arrow_lake/ingest/storage.py`

| Bug | 行号 | 修复方案 |
|-----|------|----------|
| `list_tags()` 本地路径 | 445-473 | `lance.dataset(str(lance_dir))` → `lance.dataset(self.dataset_uri(name), storage_options=self._storage_options)` |
| `read_at_tag()` 本地路径 + `is_dir()` 检查 | 499-532 | 移除 `is_dir()` 检查（S3 无意义），改用 `dataset_uri()` + `storage_options` |

**正确参考模式**（已在 `read_dataset()` 中使用）:
```python
# arrow_lake/ingest/storage.py:152-198 (已正确)
lance_uri = self.dataset_uri(name)
ds = lance.dataset(lance_uri, version=version, storage_options=self._storage_options)
```

**验证**: 修复后运行 `examples/s3_minio/06_incremental_data_lifecycle.py` 的 tag 相关步骤，确认 `list_tags()` 和 `read_at_tag()` 在 MinIO 下通过。

**回归测试**: 在 `tests/integration/` 新增 S3 tag 操作测试。

---

### 1.2 结构化错误体系加固

**现状**: `arrow_lake/exceptions.py` 已有 270 行错误体系（12 个异常类，132 个错误码），但 43 个文件中仍有 114 个 `except Exception` 裸捕获。

**策略**: 不重构 exceptions.py（已有良好结构），而是**逐文件替换裸捕获为具体异常类型**。

**优先文件**（按 `except Exception` 密度排序）:

| 文件 | 裸捕获数 | 替换为 |
|------|---------|--------|
| `storage/blob_store.py` | 9 | `botocore.exceptions.ClientError` / `ConnectionError` → `StorageError(ErrorCode.STORAGE_CONNECTION_FAILED)` |
| `ops/backup.py` | 7 | `lance.LanceError` / `StorageError` → `BackupError` (新增子类) |
| `catalog/actor.py` | 6 | `duckdb.Error` 子类 → `CatalogError(ErrorCode.CATALOG_QUERY_FAILED)` |
| `workflow/rollback.py` | 6 | 保持部分静默（清理操作可吞错），关键路径用 `WorkflowError` |

**新增异常子类**（在 `exceptions.py` 中添加）:
```python
class BackupError(WorkflowError): ...    # 备份/恢复专用
class SchemaEvolutionError(CatalogError): ...  # Schema 变更专用
class DuckDBError(QueryError): ...       # DuckDB 引擎错误
```

**验收**: 全局 `grep -c "except Exception" arrow_lake/**/*.py` 降至 <20（仅保留真正需要兜底的场景），100% 现有测试通过。

---

### 1.3 备份恢复关键 Bug 修复

**文件**: `arrow_lake/ops/backup.py`

| Bug | 行号 | 影响 | 修复 |
|-----|------|------|------|
| 分页 token 重置为 None | 453-474, 504-522, 546-563, 589-608 | 大规模备份可能丢失对象 | 正确传递 `continuation_token` 到下一页 |
| 非原子 manifest 上传 | 228-232 | 中断后备份不完整但看起来有效 | 先写临时 manifest，最后 rename/move |
| 无校验和验证 | 全文件 | 无法检测备份损坏 | 添加 MD5/ETag 比对 |

**新增验证测试**: `tests/integration/test_backup_restore.py` — 创建 >1000 对象的 dataset，备份后恢复，验证行数和校验和一致。

---

## Phase 2: 生产级可观测性（Sprint 2-3）

### 2.1 Prometheus 指标埋点落地

**现状**: 19 个指标已定义（`arrow_lake/core/metrics.py`），但只有 1 个（`rate_limit_rejected_total`）实际使用。

**埋点计划**:

| 指标 | 埋点位置 | 说明 |
|------|---------|------|
| `query_total{query_type}` | `Lake` 门面方法入口（olap_query, search, hybrid_search 等） | 查询计数 |
| `query_latency_seconds{query_type}` | 同上，用 `Histogram.time()` 上下文管理器 | 查询延迟分布 |
| `query_results_total{query_type}` | 查询返回后 | 结果行数 |
| `ingestion_rows_total{source}` | `create_dataset()`, `append_dataset()` | 入库行数 |
| `ingestion_duration_seconds{source}` | 同上 | 入库耗时 |
| `ingestion_errors_total{source,error_type}` | 异常捕获处 | 入库错误 |
| `processing_embeddings_total{model}` | 嵌入生成后 | 嵌入计数 |
| `processing_quality_rejects_total{filter_name}` | quality_filter 返回后 | 质量过滤拒绝数 |
| `catalog_tables_total` | catalog 变更时 | 注册表数量 |
| `http_request_duration_seconds` | API 中间件层 | HTTP 请求延迟（已有定义，需激活） |

**实现方式**: 在 `Lake.__init__` 中初始化 metrics，在各门面方法中用装饰器或上下文管理器埋点。保持 `ObservabilityConfig.metrics_enabled` 开关。

**验证**: 启动服务后访问 `/metrics`，确认所有 19 个指标有值。

---

### 2.2 OpenTelemetry 追踪激活

**现状**: `arrow_lake/api/telemetry.py` 已实现 OTLP 导出 + FastAPI 自动埋点，但默认关闭且无手动 span。

**激活步骤**:

1. **手动 span 埋点**（核心查询路径）:
   - `Lake.olap_query()` — span "olap_query" + 属性 {dataset, sql_hash, rows_returned}
   - `Lake.search()` — span "vector_search" + 属性 {dataset, top_k, latency}
   - `Lake.create_dataset()` — span "ingest" + 属性 {dataset, rows, bytes}
   - `Lake.quality_filter()` — span "quality_filter" + 属性 {dataset, passed, rejected}

2. **依赖提升**: `opentelemetry-api`, `opentelemetry-sdk`, `opentelemetry-exporter-otlp` 从 optional 移到 `pyproject.toml` 的 `dependencies`（或保持 optional 但文档化安装步骤）

3. **Docker Compose 集成**: `deploy/docker-compose.monitoring.yml` 添加 Jaeger 或 Tempo 作为 OTLP 后端

**验收**: 启动 OTel 后端，执行查询，在追踪 UI 中看到完整的 ingest→query 链路。

---

### 2.3 Grafana 仪表盘更新

**现状**: 6 个仪表盘 JSON 已存在但数据源绑定可能需要更新。

**更新内容**:
- 确认所有面板绑定到新激活的 19 个 Prometheus 指标
- 添加 DuckDB 连接池使用率面板（Phase 3 新增指标）
- 添加 S3 操作延迟面板（Phase 3 新增指标）
- 添加备份状态面板（Phase 1 新增指标）

---

## Phase 3: DuckDB 高可用（Sprint 3-5）

### 3.1 连接池统一与优化

**现状分析**:
- `DuckDBConnectionPool`（`catalog/connection_pool.py`）: 信号量限流（max=5），仅 catalog actor 使用
- `create_duckdb_session()`（`query/_db.py`）: 每次查询创建新连接，用完即关
- `_async.py`: ThreadPoolExecutor（4 workers）+ Semaphore（4）限流
- 配置: `max_concurrent_queries=4`, `max_query_memory_mb=512`, `query_timeout_seconds=300`

**问题**: 两套连接管理机制（pool vs per-query），无全局并发控制，查询间无优先级。

**改造方案**:

#### 3.1.1 统一 DuckDBSessionManager

新建 `arrow_lake/query/session_manager.py`，统一管理所有 DuckDB 连接:

```
DuckDBSessionManager
├── _pool: DuckDBConnectionPool (复用已有)
├── _semaphore: asyncio.Semaphore(max_concurrent_queries)
├── _active_queries: dict[str, QueryContext] (查询追踪)
├── acquire() → DuckDBSession (上下文管理器)
├── get_stats() → SessionPoolStats (指标导出)
└── shutdown() (优雅关闭)
```

**关键设计**:
- 池大小 = `max_concurrent_queries`（默认 4，可配）
- 超出池大小的查询排队等待（timeout 可配）
- 每个连接配置独立的 memory_limit 和 statement_timeout
- 空闲连接自动回收（idle_timeout=60s）

#### 3.1.2 查询治理

在 `DuckDBSessionManager.acquire()` 中添加治理逻辑:

| 治理项 | 实现 | 配置 |
|--------|------|------|
| 内存限制 | `SET memory_limit='{per_conn_mb}MB'` | `max_query_memory_mb=512`（总内存 / 池大小） |
| 查询超时 | `SET statement_timeout='{timeout}s'` | `query_timeout_seconds=300` |
| 结果集限制 | SQL 追加 `LIMIT` 或流式读取 | `max_result_rows=100_000` |
| 并发控制 | Semaphore + 排队 | `max_concurrent_queries=4` |
| 慢查询日志 | 超过阈值自动记录 | `slow_query_threshold_ms=5000` |

#### 3.1.3 故障恢复

| 场景 | 恢复策略 |
|------|----------|
| 连接超时 | 丢弃当前连接，从池中获取新连接，重试 1 次 |
| OOM (内存溢出) | 记录错误，标记连接为不可用，创建新连接 |
| 查询挂起 | statement_timeout 兜底，自动取消 |
| 池耗尽 | 返回 503 + `QueryError(ErrorCode.TIMEOUT)` |
| DuckDB 进程崩溃 | 僵尸连接检测 + 自动重建 |

**验收测试**:
- 并发 10 个查询，验证只有 4 个同时执行，其余排队
- 发送 OOM 查询（`SELECT * FROM huge CROSS JOIN huge`），验证优雅降级
- 模拟连接池耗尽，验证 503 响应和错误码

---

### 3.2 DuckDB 架构决策文档 (ADR-07)

**文件**: `docs/adr-07-duckdb-high-availability.md`

记录以下决策:
- DuckDB 作为 v1.1 唯一 OLAP 引擎（不做 ClickHouse/StarRocks 抽象层）
- 连接池统一方案选型
- 内存隔离策略（per-connection limit）
- 未来扩展路径：v1.2 考虑 DuckDB 多进程 / v2.0 考虑外部 OLAP 引擎

---

### 3.3 配置模块拆分

**文件**: `arrow_lake/config.py`（1,191 行）→ `arrow_lake/config/` 包

**拆分方案**（保持 `config.py` 作为 re-export facade，向后兼容）:

| 新文件 | 内容 | 约行数 |
|--------|------|--------|
| `config/__init__.py` | re-export 所有公开类 | 30 |
| `config/_enums.py` | StorageBackend, LogLevel 等 10 个枚举 | 80 |
| `config/storage.py` | StorageConfig + S3 helpers | 102 |
| `config/compute.py` | ComputeConfig + ObservabilityConfig | 45 |
| `config/media.py` | HttpConfig, MediaConfig, EmbeddingConfig | 57 |
| `config/search.py` | VectorSearchConfig, FTSConfig, HybridConfig | 85 |
| `config/quality.py` | QualityConfig | 50 |
| `config/olap.py` | OlapConfig | 70 |
| `config/workflow.py` | WorkflowConfig, ArgoConfig, AutoscaleConfig | 122 |
| `config/lifecycle.py` | LifecycleConfig, ExportConfig, LineageConfig | 127 |
| `config/api.py` | AuditConfig, ApiConfig, AuthConfig, RateLimitConfig | 155 |
| `config/rag.py` | RAGConfig, HugeGraphConfig, LLMConfig | 104 |
| `config/otel.py` | OpenTelemetryConfig | 25 |
| `config/main.py` | ArrowLakeConfig 主类 + from_yaml() | 130 |

**向后兼容**: 原 `arrow_lake/config.py` 改为:
```python
from arrow_lake.config._enums import *
from arrow_lake.config.storage import StorageConfig
from arrow_lake.config.main import ArrowLakeConfig
# ... 所有公开符号 re-export
```

**验收**: 所有现有 import 路径不变，`mypy arrow_lake/config/` 通过，测试全绿。

---

### 3.4 大文件定向重构

仅重构与 DuckDB 高可用直接相关的文件:

| 文件 | 当前行数 | 重构 | 说明 |
|------|---------|------|------|
| `query/_db.py` | ~167 | 扩展 | 新增 SessionManager 集成，保持 <300 行 |
| `query/_async.py` | ~80 | 修改 | 改用 SessionManager 替代独立 ThreadPoolExecutor |
| `catalog/connection_pool.py` | ~203 | 保留 | 作为 SessionManager 内部组件复用 |

**不做**（留给 v1.2）: `blob_store.py`（736 行）、`ingest/storage.py`（652 行）、`backup.py`（618 行）的拆分 — 这些文件在 Phase 1 修复 bug 后保持稳定即可。

---

## Phase 4: 质量保障（贯穿全程）

### 4.1 类型标注补全

**范围**: Phase 1-3 涉及的所有文件必须 100% 类型标注。

**工具**: `mypy --strict` 对 `arrow_lake/query/`, `arrow_lake/config/`, `arrow_lake/exceptions.py`, `arrow_lake/ops/backup.py` 运行。

**目标**: 消除 36 个 `# type: ignore` 中的 20+ 个（保留确实需要的第三方库兼容 suppressions）。

### 4.2 回归测试

每个 Phase 完成后运行:
```bash
pytest tests/unit/ -x                          # 单元测试全通过
pytest tests/integration/ -x                   # 集成测试全通过
pytest tests/e2e/ -x                           # E2E 测试全通过
uv run python examples/s3_minio/06_*.py        # S3 示例验证
uv run python examples/s3_minio/07_*.py
uv run python examples/s3_minio/08_*.py
```

### 4.3 性能基准

Phase 3 完成后建立 DuckDB 基准:
- 单表查询延迟（1K/10K/100K 行）
- 4 表 JOIN 延迟
- 并发 4 查询吞吐量
- 内存使用峰值

记录到 `docs/benchmarks/duckdb-v1.1-baseline.md`，作为后续性能回归对比基准。

---

## 实施时间线

```
Week 1-2:  Phase 1 (系统稳定)
  ├─ Sprint 1: 1.1 S3 Bug 修复 + 1.3 备份 Bug 修复
  └─ Sprint 2: 1.2 错误体系加固 + 回归测试

Week 3-4:  Phase 2 (可观测性)
  ├─ Sprint 3: 2.1 Prometheus 埋点 + 2.2 OTel 追踪激活
  └─ Sprint 4: 2.3 Grafana 仪表盘 + 验证

Week 5-8:  Phase 3 (DuckDB 高可用)
  ├─ Sprint 5: 3.1 连接池统一 + 3.3 Config 拆分
  ├─ Sprint 6: 3.1 查询治理 + 3.4 大文件重构
  └─ Sprint 7: 3.2 ADR-07 + 3.1 故障恢复 + 性能基准

Week 8:     Phase 4 收尾 + v1.1 发布
```

---

## 关键文件清单

| 阶段 | 文件 | 操作 |
|------|------|------|
| Phase 1 | `arrow_lake/ingest/storage.py` | 修改（S3 URI 修复） |
| Phase 1 | `arrow_lake/ops/backup.py` | 修改（分页/原子 manifest/校验和） |
| Phase 1 | `arrow_lake/exceptions.py` | 修改（新增 3 个子类） |
| Phase 1 | `storage/blob_store.py` | 修改（异常类型细化） |
| Phase 1 | `catalog/actor.py` | 修改（异常类型细化） |
| Phase 1 | `workflow/rollback.py` | 修改（异常类型细化） |
| Phase 2 | `arrow_lake/core/metrics.py` | 修改（无，已完备） |
| Phase 2 | `arrow_lake/__init__.py` / Lake 门面 | 修改（埋点） |
| Phase 2 | `arrow_lake/api/telemetry.py` | 修改（手动 span） |
| Phase 2 | `deploy/grafana/*.json` | 修改（面板更新） |
| Phase 2 | `deploy/docker-compose.monitoring.yml` | 修改（Jaeger/Tempo） |
| Phase 3 | `arrow_lake/query/session_manager.py` | **新建** |
| Phase 3 | `arrow_lake/query/_db.py` | 修改（集成 SessionManager） |
| Phase 3 | `arrow_lake/query/_async.py` | 修改（改用 SessionManager） |
| Phase 3 | `arrow_lake/config.py` → `arrow_lake/config/` | **重构**（拆分为包） |
| Phase 3 | `docs/adr-07-duckdb-high-availability.md` | **新建** |
| Phase 3 | `docs/benchmarks/duckdb-v1.1-baseline.md` | **新建** |

---

## 验证计划

### 端到端验证

```bash
# 1. 基础功能回归
pytest tests/unit/ tests/integration/ tests/e2e/ -x

# 2. S3/MinIO 全链路
docker compose --profile core up -d minio minio-init
uv run python examples/s3_minio/06_incremental_data_lifecycle.py
uv run python examples/s3_minio/07_quality_governance_and_materialization.py
uv run python examples/s3_minio/08_complex_lineage_and_governance_olap.py

# 3. DuckDB 高可用验证
uv run python -m pytest tests/unit/test_duckdb_session.py -v
# 并发压测: 10 并发查询，验证池限流和故障恢复

# 4. 可观测性验证
docker compose --profile monitoring up -d prometheus grafana jaeger
uv run arrow-lake serve  # 启动服务
curl http://localhost:8000/metrics  # 确认 19 指标有值
# 执行查询后在 Jaeger UI 验证 trace 链路

# 5. 备份恢复验证
# 创建 >1000 行 dataset → 备份 → 恢复 → 校验行数和内容一致
```

### 度量指标

| 指标 | v1.0 基线 | v1.1 目标 |
|------|----------|----------|
| `except Exception` 数量 | 114 | <20 |
| S3 后端可用 API | ~90% | 100% |
| Prometheus 指标活跃数 | 1/19 | 19/19 |
| OTel 追踪覆盖 | 0 span | 4+ 核心 span |
| DuckDB 连接管理 | 双轨（pool + per-query） | 统一 SessionManager |
| Config 模块最大文件 | 1,191 行 | <160 行 |
| 备份完整性 | 无校验 | MD5/ETag 验证 |
| `# type: ignore` 数量 | 36 | <15 |
