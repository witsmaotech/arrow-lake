# Arrow Lake v1.1 — 全链路测试报告

**日期**: 2026-04-22
**范围**: 9 个 S3/MinIO E2E 示例 + 1816 个单元测试
**环境**: MinIO (localhost:9000), 96GB RAM, asyncio_mode=strict

---

## 一、测试总览

### Unit 测试: 1816 passed, 6 skipped, 0 warnings

| 模块 | 文件数 | 测试数 | 状态 |
|------|--------|--------|------|
| config | 5 | 199 | PASS |
| auth | 7 | 66 | PASS |
| storage | 9 | 153 | PASS |
| duckdb | 7 | 92 | PASS |
| search | 6 | 145 | PASS |
| kg | 13 | 157 | PASS |
| rag | 9 | 133 | PASS |
| ingest | 14 | 228 | PASS |
| workflow | 12 | 216 | PASS |
| facade | 5 | 131 | PASS |
| infra | 10 | 119 | PASS |
| media (含backup) | 14 | 177 | PASS |
| **合计** | **111** | **1816** | **PASS** |

### E2E 示例: 9/9 完成

| 示例 | 业务场景 | 步骤 | 状态 |
|------|----------|------|------|
| 01 | 科研论文智能分析 | 多步骤 | 完成 (search 有 WARN) |
| 02 | 电商商品发现 | 8/8 | PASS |
| 03 | 法律文档合规 | 6/6 | PASS (lineage SQL WARN) |
| 04 | RAG 智能问答 | 8/8 | PASS (FTS 列名 WARN) |
| 05 | 知识图谱发现 | 8/8 | PASS (coroutine WARN) |
| 06 | 增量数据生命周期 | 10/10 | PASS |
| 07 | 质量治理与物化 | 10/10 | PASS (OLAP 表名已修) |
| 08 | 跨域供应链溯源 | 8/8 | PASS (OLAP+search 已修) |
| 09 | 视频智能分析 | 10/10 | PASS |

---

## 二、已修复问题 (本次会话)

### P0 — 系统级（已修复）

| # | 问题 | 根因 | 修复文件 | 影响 |
|---|------|------|----------|------|
| 1 | **test_backup.py 内存炸弹 89GB** | `list_blobs` mock 缺少 `next_token=None`，导致 `while True` 死循环 | `tests/unit/media/test_backup.py` | 系统卡死 |
| 2 | **asyncio_mode=auto 13x CPU 开销** | auto 模式给所有测试（含 sync）安装 event loop | `pyproject.toml` | 测试极慢 |
| 3 | **Docker 无 CPU 限制** | 20 个容器无资源上限 | 多个 docker-compose 文件 | 系统负载 400+ |

### P1 — 核心库 Bug（已修复）

| # | 问题 | 根因 | 修复文件 | 影响 |
|---|------|------|----------|------|
| 4 | **vector.py NameError: `effective_top_k` 未定义** | `_search_via_duckdb()` 引用了外层方法 `search()` 的局部变量 | `arrow_lake/query/vector.py:375` | DuckDB vector search 失败 |
| 5 | **hybrid.py NULL fts_column 导致 BinderException** | `_search_via_duckdb()` hardcoded `NULL` 而非传入 `fts_column` | `arrow_lake/query/hybrid.py:237` | DuckDB hybrid search 失败 |
| 6 | **S3 后端 list_tags/read_at_tag 用本地路径** | `_lance_dir()` 返回本地路径而非 S3 URI | `arrow_lake/ingest/storage.py:445-473` | S3 tag 操作不可用 |

### P2 — 测试质量（已修复）

| # | 问题 | 修复文件 | 影响 |
|---|------|----------|------|
| 7 | JWT 密钥 <32 字节触发 InsecureKeyLengthWarning | `test_auth_service.py`, `test_security_headers.py` | 6 个警告 |
| 8 | coroutine never awaited (RuntimeWarning) | `test_async_query.py` | 1 个警告 |
| 9 | PosixPath 传给 setenv | `test_metrics_endpoint.py` | 1 个警告 |
| 10 | 废弃 WSGI `arrow_lake.server` 导入 | `test_metrics_endpoint.py` → 迁移到 ASGI | 1 个废弃警告 |

### P3 — 示例代码（已修复）

| # | 问题 | 修复文件 | 影响 |
|---|------|----------|------|
| 11 | OLAP SQL 用 `FROM t` 而非实际表名 | `07_quality_*.py`, `08_complex_*.py` | OLAP 查询全部失败 |
| 12 | `faceted_search()` 参数名 `query=` / `dimensions=` | `08_complex_*.py` | 参数错误 |
| 13 | `create_vector_index()` 参数名 `column=` | `08_complex_*.py` | 参数错误 |
| 14 | `hybrid_search()` 参数名 `query=` | `08_complex_*.py` | 参数错误 |

---

## 三、待优化问题（未修复）

### HIGH — 核心库

| # | 问题 | 文件 | 优先修复原因 |
|---|------|------|-------------|
| H1 | **FTS DuckDB fallback 读 AWS 而非 MinIO** | `query/fts.py:294` | DuckDB lance_fts() 没有传 storage_options，直接读 AWS S3，导致 403 Forbidden |
| H2 | **Vector DuckDB fallback 同样读 AWS** | `query/vector.py:375` | 同 H1，lance_vector_search() 也缺少 storage_options |
| H3 | **lineage SQL 查询 `_lineage_events` 表不存在** | 多个示例 | LineageStore 写入 Lance 文件但查询走 DuckDB SQL，未注册表 |
| H4 | **CSV export 不支持 fixed_size_list 列** | 示例 09 | Arrow CSV writer 不处理 embedding 向量列 |
| H5 | **GraphRAG pipeline coroutine 未 awaited** | `query/kg/rag_pipeline.py` | `_retrieve_graph_context` 是 async 但调用处未 await |
| H6 | **示例 04 FTS text_search 列名 `text_content` 应为 `content`** | `04_rag_intelligent_qa.py` | FTS 创建用正确列名，但 text_search 调用硬编码了错误列名 |

### MEDIUM — 示例/配置

| # | 问题 | 建议 |
|---|------|------|
| M1 | Daft 查询用本地路径而非 S3 URI | Daft 0.7.8 不支持 SQL，保持现状 |
| M2 | DuckLake 物化视图需 `ducklake_enabled=true` | 配置文档化 |
| M3 | SchemaValidationGate 拒绝所有行（含正常行） | 检查 strict 模式匹配逻辑 |
| M4 | 审计 HMAC secret key 为空 | 配置文件添加默认 key |
| M5 | 示例 01 缺少显式步骤完成计数 | 统一输出格式 |

### LOW — 改进建议

| # | 建议 |
|---|------|
| L1 | `_components` 缓存无驱逐策略 → 长期运行内存泄漏 |
| L2 | config.py 1,191 行 → 拆分为包（v1.2 计划已有） |
| L3 | blob_store.py (736 行), backup.py (618 行) → 文件过大 |
| L4 | DuckDB 单点依赖 → v1.2 考虑 MotherDuck 水平扩展 |
| L5 | `except Exception` 114 处 → 逐步替换为具体异常类型（v1.1 已降至 <20 的目标需重新统计） |

---

## 四、修复优先级建议

### 立即修复（本周）— 解决 DuckDB 搜索在 S3 下完全失效

1. **H1 + H2**: 在 `_search_via_duckdb` 中传递 `storage_options` 给 DuckDB lance 扩展
2. **H5**: GraphRAG pipeline await 修复
3. **H6**: 示例 04 FTS 列名修复

### 短期（v1.1 收尾）— 消除剩余 HIGH

4. **H3**: LineageStore SQL 查询适配（注册 Lance 文件为 DuckDB 表或改用 SDK 查询）
5. **H4**: CSV export 处理 vector 列（排除或转为 JSON 字符串）

### 中期（v1.2）

6. M1-M5 示例/配置改进
7. L1-L5 架构优化

---

## 五、资源消耗基线

| 指标 | 值 |
|------|-----|
| Unit 测试总耗时 | 1:48 (1816 tests) |
| Unit 测试内存峰值 | ~10G (稳定) |
| E2E 示例总耗时 | ~10 min (9 examples) |
| E2E 示例内存峰值 | ~10G (稳定) |
| 系统无卡死 |
