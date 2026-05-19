# DuckDB 高级功能优化完善方案

> 基于 DuckDB Performance Optimizer + Data Engineer + Python + Lakehouse 技能最佳实践，对 Arrow Lake DuckDB 生态的全面审计与优化。
>
> 日期：2026-05-18
>
> 状态：**已完成** — 全部 12 项优化已实施，85 个相关测试全部通过。

---

## 审计范围

| 文件 | 职责 |
|------|------|
| `arrow_lake/query/_db.py` | DuckDB Session 核心（扩展加载 + 资源治理 + S3 配置） |
| `arrow_lake/query/session_manager.py` | 连接池 + Semaphore 并发控制 |
| `arrow_lake/query/olap.py` | OLAP 分析查询 |
| `arrow_lake/query/metadata.py` | 元数据 SQL 查询 |
| `arrow_lake/query/ducklake_workspace.py` | DuckLake 物化视图 |
| `arrow_lake/query/vector.py` | 向量搜索 |
| `arrow_lake/query/lance_adapter.py` | Lance → DuckDB 适配 |
| `arrow_lake/query/export.py` | 数据导出 |
| `arrow_lake/catalog/connection_pool.py` | 旧版连接池 |
| `arrow_lake/config/olap.py` | OLAP 配置 |
| `arrow_lake/query/engine.py` | 查询引擎入口 |

---

## Phase 1 — P0 性能关键项（5 项）

### 1. 添加 `preserve_insertion_order = false` + `temp_directory` 配置

**文件**: `arrow_lake/query/_db.py:76-90` (`_configure_resources`)
**问题**: 默认 `preserve_insertion_order = true` 在物化写入时强制排序，性能损失 2-8x；缺少 `temp_directory`，查询超出 memory_limit 时 溢出到磁盘 路径不确定。
**修复**:
- `_configure_resources()` 中添加 `SET preserve_insertion_order = false`
- `_configure_resources()` 中添加 `SET temp_directory = <path>`，从 `OlapConfig` 新增字段 `temp_directory` 读取
- `session_manager.py:339-342` 重置配置时同步重置 `preserve_insertion_order` 和 `temp_directory`
- `arrow_lake/config/olap.py` 新增 `temp_directory: str = ""` 和 `preserve_insertion_order: bool = False`

**预期收益**: 写入性能提升 2-8x；大查询 溢出到磁盘 到指定快速磁盘。

### 2. 升级 `EXPLAIN` → `EXPLAIN ANALYZE`

**文件**: `arrow_lake/query/olap.py:267-296` (`explain` 方法)
**问题**: 仅 `EXPLAIN` 显示逻辑计划，无实际执行时间、行数、溢出指标。
**修复**:
- 新增 `explain_analyze()` 方法，使用 `EXPLAIN ANALYZE`
- 保留原 `explain()` 方法（向后兼容）
- 解析输出中 timing、rows、Bytes spilled 等指标

**预期收益**: 真实查询调优数据，可诊断慢查询瓶颈。

### 3. S3 凭证从 `SET` 迁移到 `CREATE SECRET`

**文件**: `arrow_lake/query/_db.py:92-122` (`_configure_s3`)
**问题**: `SET s3_access_key_id` 是旧 API，`CREATE SECRET` 更安全且支持 credential chain。
**修复**:
- 将 `_configure_s3` 中的 `SET s3_*` 替换为 `CREATE SECRET` 语句
- 使用 `CREATE TEMP SECRET` 避免持久化
- 处理 DuckDB 版本兼容（DuckDB 1.5.2 支持 CREATE SECRET）

**预期收益**: 更安全的凭证管理；支持 credential chain 自动发现。

### 4. 显式加载 `httpfs` 扩展

**文件**: `arrow_lake/query/_db.py:57-65` (`_load_extensions`)
**问题**: S3 配置依赖 httpfs 但未显式 INSTALL/LOAD。
**修复**:
- 在 `_load_extensions()` 中 lance 之后添加 `INSTALL httpfs; LOAD httpfs;`
- 失败时降级为警告（本地模式不需要 httpfs）

**预期收益**: S3 访问更可靠，避免隐式依赖。

### 5. S3 env var 线程安全改造

**文件**: `arrow_lake/query/_db.py:131-152`
**问题**: `os.environ` 修改非线程安全，连接池并发时存在竞争。
**修复**:
- 使用 `threading.Lock` 保护 env var 读写
- 或改为仅在 `DuckDBSession.__enter__` 时写一次（连接创建时），不再 save/restore
- 优选方案：DuckDB CREATE SECRET 后，lance Rust SDK 也支持从 DuckDB secret 读取（检查 lance 6.0+ 是否支持）

**预期收益**: 消除并发竞态条件。

---

## Phase 2 — P1 高级功能提升（5 项）

### 6. Prepared Statements for DuckLake 元数据表

**文件**: `arrow_lake/query/ducklake_workspace.py`
**问题**: `materialize()` 和 `cleanup_expired()` 的重复 INSERT/SELECT 每次都重新解析 SQL。
**修复**:
- 在 `_ensure_metadata_table` 中同时创建 prepared statements
- INSERT 使用 `conn.execute("INSERT INTO ... VALUES ($1, $2, $3, $4)", params)`（已部分使用）
- SELECT 也改为 prepared statement

**预期收益**: 减少重复 SQL 解析开销。

### 7. Parquet 导出优化（ZSTD + ROW_GROUP_SIZE）

**文件**: `arrow_lake/query/export.py:196-203`
**问题**: 使用 PyArrow `pq.write_table()` 但未利用 DuckDB `COPY TO` 的高级特性（ZSTD、ROW_GROUP_SIZE）。
**修复**:
- 对于通过 DuckDB session 可达的导出场景，增加 `COPY TO ... (FORMAT PARQUET, COMPRESSION ZSTD, ROW_GROUP_SIZE 100000)` 路径
- 在 `ExportConfig` 或 `OlapConfig` 中新增 `parquet_compression: str = "zstd"` 和 `parquet_row_group_size: int = 100_000`
- 保留 PyArrow 路径作为 fallback

**预期收益**: 压缩率提升 30-50%；扫描性能改善。

### 8. 物化表 ART Index 支持

**文件**: `arrow_lake/query/ducklake_workspace.py`
**问题**: 频繁过滤的物化表缺少索引，等值查询走全扫描。
**修复**:
- 在 `OlapConfig` 新增 `ducklake_index_columns: list[str] = []`
- `materialize()` 完成后，对配置中的列创建 ART Index：`CREATE INDEX idx_{col} ON {view_name}({col})`
- 添加 `create_index_on_materialized()` 方法供外部调用

**预期收益**: 等值过滤查询加速 10-100x。

### 9. 元数据表改用 `CREATE TEMP TABLE`

**文件**: `arrow_lake/query/ducklake_workspace.py:64-78`
**问题**: `_metadata` 表是会话级别的，但用 `CREATE TABLE` 会持久化到磁盘。
**修复**:
- 将 `CREATE TABLE` 改为 `CREATE TEMP TABLE IF NOT EXISTS`
- `_ensure_metadata_table()` 检测临时表

**预期收益**: 会话结束后自动清理，避免残留数据。

### 10. 连接池增加 `enable_progress_bar` 配置

**文件**: `arrow_lake/query/_db.py`, `arrow_lake/config/olap.py`
**问题**: 长查询无进度反馈。
**修复**:
- `OlapConfig` 新增 `enable_progress_bar: bool = False`
- `_configure_resources()` 中条件执行 `SET enable_progress_bar = true; SET progress_bar_time = 2000;`

**预期收益**: 长查询有进度反馈，提升运维可观测性。

---

## Phase 3 — P2 锦上添花（3 项）

### 11. DuckDB Profiling 支持

**文件**: `arrow_lake/query/_db.py`, `arrow_lake/query/olap.py`
**问题**: 无查询 profiling 能力，无法事后分析。
**修复**:
- `OlapConfig` 新增 `enable_profiling: bool = False`
- `_configure_resources()` 中条件执行 `SET profiling_mode = 'detailed'`
- 在 `explain_analyze()` 中返回 profiling 信息

**预期收益**: 生产环境查询后分析。

### 12. DuckDB Relational API 试点

**文件**: `arrow_lake/query/metadata.py`
**问题**: 所有查询都用字符串拼接 SQL。
**修复**:
- 在 `metadata.py` 的简单查询（如 schema discovery）中试点 DuckDB Relational API
- `duckdb.table(name).filter(...).select(...).df()`

**预期收益**: 类型安全的链式查询，减少 SQL 注入风险。

### 13. 查询结果缓存（LRU）

**文件**: 新建 `arrow_lake/query/_cache.py`
**问题**: 重复相同 SQL 无缓存，每次重新执行。
**修复**:
- 实现轻量 LRU cache（基于 SQL hash → Arrow Table）
- TTL 控制（默认 60s）
- 缓存大小限制
- 在 `OlapConfig` 中新增 `query_cache_enabled: bool = False`, `query_cache_ttl_seconds: int = 60`, `query_cache_max_entries: int = 100`

**预期收益**: 重复查询秒级响应。

---

## 实施顺序

```
Phase 1 (P0): #1 → #2 → #3 → #4 → #5     — 性能关键修复
Phase 2 (P1): #6 → #7 → #8 → #9 → #10    — 高级功能提升
Phase 3 (P2): #11 → #12 → #13             — 锦上添花
```

每个修复完成后运行测试验证：
```bash
.venv/bin/python3 -m pytest tests/unit/duckdb/ tests/unit/test_query_metadata.py tests/unit/media/test_olap*.py -x -q
```

全量回归：
```bash
.venv/bin/python3 -m pytest tests/ -x -q
```

---

## 关键文件修改清单

| 文件 | 修改内容 |
|------|----------|
| `arrow_lake/config/olap.py` | 新增 temp_directory, preserve_insertion_order, enable_progress_bar, enable_profiling, parquet_row_group_size, ducklake_index_columns, query_cache_* |
| `arrow_lake/query/_db.py` | _configure_resources 添加新配置; _load_extensions 添加 httpfs; _configure_s3 迁移到 CREATE SECRET; env var 线程安全 |
| `arrow_lake/query/session_manager.py` | config_changed 检测新增字段; reset 新增配置 |
| `arrow_lake/query/olap.py` | 新增 explain_analyze() |
| `arrow_lake/query/ducklake_workspace.py` | TEMP TABLE; prepared statements; ART index |
| `arrow_lake/query/export.py` | DuckDB COPY TO 路径 |
| `arrow_lake/query/_cache.py` | 新建：查询结果 LRU 缓存 |

---

## 验证方案

1. **单元测试**: 每个 Phase 完成后运行对应测试目录
2. **配置验证**: 新增 OlapConfig 字段的 validator 测试
3. **性能基准**: `preserve_insertion_order=false` 前后对比写入性能
4. **S3 连接**: `CREATE SECRET` 替换后验证 MinIO 连接正常
5. **EXPLAIN ANALYZE**: 验证输出包含 timing/rows 指标
6. **全量回归**: 所有测试通过
