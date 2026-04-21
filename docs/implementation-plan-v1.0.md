# Arrow Lake v1.0 实施计划

**版本**: v1.0-impl | **日期**: 2026-04-20
**基于**: `docs/architecture-v1.0_draft_up.md` + 三方评审反馈
**状态**: 待用户审批

---

## Context

架构设计文档 `architecture-v1.0_draft_up.md` 已完成并通过架构师、全栈开发、QA 三方评审。评审发现 1 CRITICAL + 8 HIGH 级问题需要在 M0 阶段解决。本计划将 M0 拆分为 M0a (基础设施) + M0b (查询迁移) 两个子阶段，每个约 5 个工作日。

**代码库现状**:
- `arrow_lake/__init__.py` — 1049 行, 72 方法 (God Class)
- `arrow_lake/query/_db.py` — 43 行, 裸 context manager (无扩展/治理)
- `arrow_lake/query/_base.py` — 39 行, `SearchBridge` Protocol
- `arrow_lake/config.py` — 826 行, 27 配置类
- `arrow_lake/query/` — 14 文件, 2633 行
- `tests/conftest.py` — 空文件

---

## 里程碑概览

```
M0a (5天) → M0b (5天) → M1 (2周) → M2 (4周) → M3 (4周) → M4 (4周)
                  ↑              ↑
               并行可启动      M2/M3 并行
```

---

## M0a: 基础设施 (~5天)

**目标**: 构建所有后续迁移依赖的基础设施。不迁移任何查询模块。

### Day 1: DuckDBSession 重写 + StorageConfig 增强

**Task 1.1: 重写 `arrow_lake/query/_db.py`** (43行 → ~120行)

当前: 裸 `contextmanager`, `duckdb.connect()` / `close()`, 无扩展加载。

目标:
- `DuckDBSession` 类: `__init__(max_memory_mb, timeout_seconds, load_ducklake, olap_config)`
- `_load_extensions(conn)` — `INSTALL lance; LOAD lance; INSTALL ducklake; LOAD ducklake`, 失败 `RuntimeError`
- `_configure_resources(conn)` — `SET memory_limit`, `threads`, `statement_timeout`
- `_configure_s3(conn, storage_config)` — 从 `StorageConfig.to_duckdb_s3_config()` 执行 SET
- 保留向后兼容: `DuckDBSession()` 无参数调用仍然工作
- 模块级 `create_duckdb_session(config, storage_config)` 工厂函数

**Task 1.2: 增强 `StorageConfig`** (`config.py` line 97)

新增方法:
- `to_storage_options() -> dict[str, str] | None`
- `to_duckdb_s3_config() -> list[str]`
- `s3_uri` property
- `from_env()` classmethod

**Task 1.3: 统一 OlapConfig** (`config.py` line 361)

在现有 5 字段基础上新增 7 字段: `lance_scan_mode`, `max_query_memory_mb`, `max_concurrent_queries`, `query_timeout_seconds`, `ducklake_enabled`, `ducklake_ttl_days`, `ducklake_max_join_rows`

**修改文件**:
- `arrow_lake/query/_db.py` — 重写
- `arrow_lake/config.py` — StorageConfig + OlapConfig 增强

**测试**:
- `tests/unit/test_duckdb_session.py` — 扩展加载/资源治理/S3配置/向后兼容
- `tests/unit/test_storage_config.py` — to_storage_options/to_duckdb_s3_config/from_env

---

### Day 2: LanceScanAdapter + 异常码

**Task 2.1: 创建 `arrow_lake/query/lance_adapter.py`** (~120行, 新文件)

> 评审共识: 独立文件, 不放在 `_base.py` (避免与 `SearchBridge` 职责冲突)

- `LanceScanAdapter` ABC: `scan()`, `create_view()`, `is_available()`
- `NativeLanceScanAdapter`:
  - `scan()` — `conn.execute("SELECT * FROM __lance_scan(?, explain_verbose := false)", [uri])`
  - `is_available()` — try/except (非空字符串探针), 区分 "扩展未加载" vs "路径无效"
- `PyArrowFallbackAdapter`:
  - **`dataset.scanner().to_reader()` 流式** (修复评审 CRITICAL: `to_table()` 全量加载 OOM)
  - `conn.register()` 注册 reader
- `create_lance_scan_adapter(conn, mode)` 工厂: `"auto"|"native"|"pyarrow_fallback"`

**Task 2.2: 新增异常码** (`exceptions.py`)

```python
LANCE_EXTENSION_ERROR = "LANCE_EXTENSION_ERROR"
LANCE_SCAN_FAILED = "LANCE_SCAN_FAILED"
DUCKLAKE_EXTENSION_ERROR = "DUCKLAKE_EXTENSION_ERROR"
```

**修改文件**:
- `arrow_lake/query/lance_adapter.py` — 新建
- `arrow_lake/exceptions.py` — 新增 3 错误码

**测试**:
- `tests/unit/test_lance_adapter.py` — native/fallback/工厂/流式验证
- `tests/unit/test_duckdb_extensions.py` — 扩展安装/加载/基本SQL/列发现 (评审 HIGH #15)

---

### Day 3: 共享测试 Fixtures + DuckLake 集成

**Task 3.1: 填充 `tests/conftest.py`** (当前空文件)

共享 fixtures: `tmp_lance_dir`, `storage`, `sample_table`, `sample_vector_table`, `duckdb_session`, `lance_scan_adapter`

**Task 3.2: 创建 `arrow_lake/query/ducklake_workspace.py`** (~100行, 新文件)

- `DuckLakeWorkspace`: attach/detach, materialize (行数预算检查), `_metadata` 表建表 schema
- `_metadata` schema: `table_name VARCHAR, created_at TIMESTAMP, expires_at TIMESTAMP, row_count BIGINT`
- `cleanup_expired()` + `list_tables()`

> 修复评审 HIGH: `_metadata` 表未定义 schema

**修改文件**:
- `tests/conftest.py` — 填充
- `arrow_lake/query/ducklake_workspace.py` — 新建

**测试**:
- `tests/unit/test_ducklake_workspace.py` — attach/materialize/quota/TTL/cleanup

---

### Day 4: 回归测试 + 异步 Thread Pool

**Task 4.1: 创建 `tests/regression/`** (评审 HIGH #14)

- `test_lake_facade_api.py` — 验证所有 `Lake` 方法签名不变 (`inspect.signature()`)
- `test_query_bridge_api.py` — 验证所有 bridge 类 API 不变
- `test_config_backward_compat.py` — `ArrowLakeConfig()` 无参数默认值不变

**Task 4.2: 创建 `arrow_lake/query/_async.py`** (~30行, 新文件)

> 修复评审 CRITICAL: DuckDB 阻塞 FastAPI 事件循环

```python
_query_executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="duckdb")
_query_semaphore = asyncio.Semaphore(4)

async def run_duckdb_query(func, *args, **kwargs):
    async with _query_semaphore:
        return await loop.run_in_executor(_query_executor, lambda: func(*args, **kwargs))
```

**修改文件**:
- `tests/regression/` — 3 个新文件
- `arrow_lake/query/_async.py` — 新建

**测试**:
- `tests/regression/` — 3 个回归测试文件
- `tests/unit/test_async_query.py` — thread pool + semaphore

---

### Day 5: v0.2 数据迁移验证 + M0a Gate

**Task 5.1: v0.2 迁移验证** (`tests/integration/test_v02_migration.py`)

> 修复评审 HIGH: 缺少现有数据迁移策略

- 用 v0.2 `LanceStorageManager` 创建数据集
- 验证 `__lance_scan()` 可读取
- 验证 `CREATE VIEW` + `information_schema` 列发现
- 验证 `NativeLanceScanAdapter.scan()` 与 `storage.read_dataset()` 数据一致
- 验证 NULL 跨格式保留

**Task 5.2: M0a Gate**

```bash
uv run ruff check .
uv run ruff format --check .
uv run mypy arrow_lake/
uv run pytest tests/unit/ --cov=arrow_lake --cov-report=term-missing
uv run pytest tests/integration/ -v
uv run pytest tests/regression/ -v
```

**M0a 阻断标准**: 以上全部通过才进入 M0b。

---

## M0b: 查询迁移 (~5天)

### Day 1: Lake Facade Mixin 拆分

**Task 1.1: 拆分 `arrow_lake/__init__.py`** (1049行 → ~100行)

| 新文件 | Mixin | 方法数 | 说明 |
|--------|-------|--------|------|
| `_lake_ingest.py` | `_LakeIngestMixin` | ~11 | ingest/quality_filter/deduplicate/create/append_dataset |
| `_lake_search.py` | `_LakeSearchMixin` | ~13 | search/text_search/hybrid/olap/query/faceted/ensemble/export/daft_query/create_index |
| `_lake_admin.py` | `_LakeAdminMixin` | ~6 | catalog/list/delete/version/list_flows/get_flow_info |
| `_lake_lineage.py` | `_LakeLineageMixin` | ~3 | lineage_record/history/query |
| `_lake_audit.py` | `_LakeAuditMixin` | ~4 | audit_record/verify/query/export |

`__init__.py` 仅保留: `Lake` 类 (多继承), `__init__`, `from_yaml`, dataclasses, `__all__`

**验证**: `tests/regression/test_lake_facade_api.py` 全通过 + 每文件 < 300行

---

### Day 2: 迁移 OLAP + Metadata + Faceted → LanceScanAdapter

**模式**: `storage.read_dataset()` + `conn.register()` → `adapter.create_view()`

迁移文件:
- `arrow_lake/query/olap.py` — 替换 read_dataset/register 为 adapter.create_view
- `arrow_lake/query/metadata.py` — 同上
- `arrow_lake/query/faceted.py` — 同上

每个 bridge 新增 `storage_config` 参数, 使用 `create_duckdb_session()`。
保留 `lance_scan_mode="pyarrow_fallback"` 时走旧路径的向后兼容。

---

### Day 3: 迁移 vector + fts + hybrid → DuckDB Lance SQL

**双路径策略**: DuckDB native (首选) + LanceDB SDK (fallback)

- `vector.py` — 新增 `_search_via_duckdb()` (`lance_vector_search()` SQL), 保留 `_search_via_lancedb()`
- `fts.py` — 新增 `_search_via_duckdb()` (`lance_fts()` SQL)
- `hybrid.py` — 新增 `_search_via_duckdb()` (`lance_hybrid_search()` 原生 RRF)
- `create_index()` **始终走 LanceDB SDK** (DuckDB 不能建索引)

---

### Day 4: S3 storage_options 接通

**Task 4.1: `arrow_lake/ingest/storage.py` 增强**

- `__init__` 接收 `storage_config: StorageConfig | None`
- `_write_lance()` / `_open_lance()` / `scan_dataset()` 传递 `storage_options`
- S3 路径处理 (无 `.is_dir()`, 用 `lancedb` 存在性检查)
- 向后兼容: `storage_config=None` 行为不变

---

### Day 5: DuckLake ETL + M0 Gate

**Task 5.1**: `OlapSearchBridge` 新增 DuckLake 物化路径

**Task 5.2**: 端到端 DuckLake + Lance 跨存储 JOIN 测试

**M0 Gate** — 12 项验收标准全部通过 (见架构文档 Section 十一)

---

## M1: 生产存储 (~2周)

### Week 1: S3/MinIO 完整集成
- `arrow_lake/storage/blob_store.py` — BlobStoreManager (upload/download/presigned_url/delete)
- `ingest/media.py` — 原始媒体上传 MinIO, S3 URI 存 Lance
- CI workflow 更新: MinIO service container
- `tests/integration/test_s3_storage.py`

### Week 2: 备份恢复
- `arrow_lake/ops/backup.py` — BackupManager (Lance + MinIO + DuckLake)
- REST 端点: backup/create, backup/restore, backup/list
- `tests/integration/test_backup_restore.py`

---

## M2: RAG Pipeline (~4周, 含 NO-GO)

### Week 1: Spike + NO-GO
- LLM API 延迟基准 (OpenAI/vLLM)
- SSE 流式原型
- **NO-GO**: P95 < 5s, ≥1 provider E2E 可用, SSE 正常

### Week 2-3: RAG 核心
- `arrow_lake/rag/provider.py` — LLM 抽象 (OpenAI/Anthropic/vLLM/Ollama)
- `arrow_lake/rag/context.py` — token 预算 + 去重 + 引用追踪
- `arrow_lake/rag/prompt.py` — Jinja2 模板
- `arrow_lake/rag/pipeline.py` — RAG 编排 (检索→组装→生成)
- LLM 限速 (评审 MEDIUM #18)

### Week 4: API + 集成
- `arrow_lake/api/routers/rag.py` — /api/v2/rag/*
- `tests/integration/test_rag_e2e.py`

---

## M3: 知识图谱 + GraphRAG (~4周, 含 NO-GO)

### Week 1: Spike + NO-GO
- HugeGraph 7 天稳定性
- 实体抽取准确率 > 70% (50 样本, 标注数据集)
- 图遍历延迟 P95 < 1s (2跳 BFS)
- **NO-GO**: 任一不满足 → 推迟到 v1.1

### Week 2-3: KG 核心
- `arrow_lake/knowledge_graph/` — client/schema/extractor/builder/retriever/queries
- HugeGraph 连接池 + 重试/超时 (评审 MEDIUM #19)
- KG 构建作为 Ray 任务

### Week 4: GraphRAG + API
- `arrow_lake/rag/graph_rag.py` — 三路 RRF 融合
- `arrow_lake/api/routers/knowledge_graph.py` — /api/v2/kg/*
- Docker Compose 集成 HugeGraph

---

## M4: 生产就绪 (~4周)

### Week 1-2: CI/CD + 可观测性
- GitHub Actions 增强: MinIO service + bandit + Docker build
- OpenTelemetry traces (`arrow_lake/core/tracing.py`)
- `/health/live` + `/health/ready` 分离

### Week 2-3: RBAC + 安全
- `arrow_lake/security/` — rbac/jwt_auth/middleware
- 双模式认证: JWT + API Key
- 数据集级权限

### Week 3-4: 性能基线 + OMTM
- OLAP 性能基线 + 回滚 trigger 定义
- OMTM 跟踪: "1 小时从零到 hybrid search API"
- CatalogActor SPOF 恢复文档

---

## 关键文件清单

| 文件 | M0 操作 | 说明 |
|------|---------|------|
| `arrow_lake/query/_db.py` | 重写 | 43→~120行, 扩展加载+资源治理 |
| `arrow_lake/query/lance_adapter.py` | 新建 | ~120行, LanceScanAdapter 抽象层 |
| `arrow_lake/query/_async.py` | 新建 | ~30行, thread pool 避免事件循环阻塞 |
| `arrow_lake/query/ducklake_workspace.py` | 新建 | ~100行, DuckLake 工作区管理 |
| `arrow_lake/config.py` | 修改 | StorageConfig+OlapConfig 增强 |
| `arrow_lake/exceptions.py` | 修改 | +3 错误码 |
| `arrow_lake/__init__.py` | 修改 | 1049→~100行, Mixin 拆分 |
| `arrow_lake/_lake_ingest.py` | 新建 | IngestMixin |
| `arrow_lake/_lake_search.py` | 新建 | SearchMixin |
| `arrow_lake/_lake_admin.py` | 新建 | AdminMixin |
| `arrow_lake/_lake_lineage.py` | 新建 | LineageMixin |
| `arrow_lake/_lake_audit.py` | 新建 | AuditMixin |
| `arrow_lake/ingest/storage.py` | 修改 | storage_options 接通 |
| `arrow_lake/query/olap.py` | 修改 | 迁移到 LanceScanAdapter |
| `arrow_lake/query/metadata.py` | 修改 | 迁移到 LanceScanAdapter |
| `arrow_lake/query/faceted.py` | 修改 | 迁移到 LanceScanAdapter |
| `arrow_lake/query/vector.py` | 修改 | 双路径 (DuckDB+LanceDB) |
| `arrow_lake/query/fts.py` | 修改 | 双路径 (DuckDB+LanceDB) |
| `arrow_lake/query/hybrid.py` | 修改 | 双路径 (DuckDB+LanceDB) |
| `tests/conftest.py` | 修改 | 填充共享 fixtures |

---

## 验证方式

```bash
# M0a Gate
uv run pytest tests/unit/ tests/regression/ tests/integration/ -v --cov=arrow_lake

# M0 Gate (全量)
uv run pytest tests/ -v --ignore=tests/benchmark

# OMTM 验证 (M4)
uv run python -c "
from arrow_lake import Lake
lake = Lake.from_yaml('configs/prod.yaml')
lake.ingest_mixed('test_data/')
result = lake.hybrid_search('test', query_vector=[0.1]*384, query_text='test')
print(f'Results: {len(result.to_arrow())} rows')
"
```
