# Lance 优化完善计划

> 基于 Lance Performance Optimizer + Data Engineer 技能最佳实践，对 Arrow Lake 核心 Lance 代码的全面审计与优化方案。
>
> 日期：2026-05-18

---

## 审计范围

| 文件 | 职责 |
|------|------|
| `arrow_lake/ingest/storage.py` | LanceStorageManager 主类 |
| `arrow_lake/ingest/_storage_crud.py` | CRUD 操作 |
| `arrow_lake/ingest/_storage_advanced.py` | compaction/schema/scan |
| `arrow_lake/ingest/_storage_indexing.py` | 向量索引管理 |
| `arrow_lake/ingest/_storage_versioning.py` | 版本/标签管理 |
| `arrow_lake/query/vector.py` | 向量搜索 |
| `arrow_lake/query/fts.py` | 全文搜索 |
| `arrow_lake/query/hybrid.py` | 混合搜索 |
| `arrow_lake/query/lance_adapter.py` | Lance → DuckDB 适配 |
| `arrow_lake/config/search.py` | 搜索配置 |

---

## P0 — 性能关键问题（必须修复）

### 1. FTS 分词列全表覆写

**文件**: `arrow_lake/query/fts.py:220-287`
**问题**: `_add_segmented_column` 加载全表到内存，覆写整个数据集。大数据集 OOM 风险高，且丢失版本历史。
"chunked" 路径（第 257 行）仍调用 `ds.to_table()` 加载全量数据。
**修复**: 使用 `lance.dataset().add_columns()` 以流式方式追加列，避免全量覆写。
**预期收益**: 内存降低 80%+，保护版本历史。

### 2. read_dataset 走向量搜索管线

**文件**: `arrow_lake/ingest/_storage_crud.py:79-83`
**问题**: `table.search().select(columns).to_arrow()` 走了向量搜索 API，不必要的管线开销。
**修复**: 使用 `table.to_arrow()` 直接读取。
**预期收益**: 读性能提升 2-5x。

### 3. 写入无优化参数

**文件**: `arrow_lake/ingest/storage.py:256-265`
**问题**: `_write_lance` 未传递 `max_rows_per_file`、`max_rows_per_group`、`compression` 参数。
Fragment 大小不受控，无压缩。
**修复**: 添加写入优化参数，fragment 大小控制在 128MB-1GB 区间。
**预期收益**: 减少 fragment 碎片，改善存储和 I/O。

---

## P1 — 召回率与连接效率

### 4. nprobes 默认值过低

**文件**: `arrow_lake/query/vector.py:405`, `arrow_lake/query/hybrid.py:258`
**问题**: DuckDB 路径 `nprobes := 1`，只搜 1 个 IVF 分区，召回率极低（~30%）。
**修复**: 使用 `VectorSearchConfig.nprobes`（默认 20）替代硬编码 1。
**预期收益**: 向量搜索召回率从 ~30% 提升到 ~90%+。

### 5. refine_factor 硬编码为 1

**文件**: `arrow_lake/query/vector.py:403`, `arrow_lake/query/hybrid.py:257`
**问题**: `refine_factor := 1` 不做重排序，ANN 结果精度低。
**修复**: 在 `VectorSearchConfig` 增加 `refine_factor` 字段（默认 5），DuckDB 路径使用该值。
**预期收益**: 向量搜索精度显著提升。

### 6. dataset_exists 每次新建连接

**文件**: `arrow_lake/ingest/_storage_crud.py:140-157`
**问题**: 每次 `dataset_exists()` 和 `list_datasets()` 调用 `lancedb.connect()` 创建新连接。
**修复**: 使用已有的 `self._get_db()` 缓存连接。
**预期收益**: 减少 S3/MinIO 连接开销。

---

## P2 — 功能完善

### 7. 添加 HNSW 索引类型

**文件**: `arrow_lake/config/_enums.py`
**问题**: `VectorIndexType` 缺少 `HNSW`。HNSW 是实时查询场景最佳选择（<10ms 延迟）。
**修复**: 添加 `HNSW = "HNSW"` 枚举值，`create_index` 支持 `M` 和 `ef_construction` 参数。
**预期收益**: 支持低延迟实时搜索场景。

### 8. compaction 后清理旧版本

**文件**: `arrow_lake/ingest/_storage_advanced.py:41`
**问题**: `table.optimize()` 后旧版本数据仍在，浪费磁盘。
**修复**: 添加 `table.cleanup_old_versions()` 调用。
**预期收益**: 回收磁盘空间。

### 9. Hybrid 搜索复用 bridge 实例

**文件**: `arrow_lake/query/hybrid.py:296-307`
**问题**: 每次 `search()` 创建新的 `VectorSearchBridge` 和 `FullTextSearchBridge`。
**修复**: 在 `__init__` 中初始化并复用 bridge 实例。
**预期收益**: 减少对象创建开销。

### 10. 添加 Lance 读缓存配置

**文件**: `arrow_lake/config/storage.py`, `arrow_lake/ingest/storage.py`
**问题**: 未配置 `cache_size`，重复读取无缓存加速。
**修复**: 在 `StorageConfig` 添加 `lance_cache_size` 参数，在 `open_dataset_versioned` 中使用。
**预期收益**: 重复读取加速。

### 11. compact() 使用 get_fragments() 统计碎片

**文件**: `arrow_lake/ingest/_storage_advanced.py:34-46`
**问题**: 用 `glob("*.lance")` 统计碎片文件数，不够精确。
**修复**: 使用 `lance.dataset().get_fragments()` API。
**预期收益**: 精确的碎片统计。

---

## 实施顺序

```
Phase 1 (P0):  #1 → #2 → #3    — 性能关键修复
Phase 2 (P1):  #4 → #5 → #6    — 召回率和连接效率
Phase 3 (P2):  #7 → #8 → #9 → #10 → #11  — 功能完善
```

每个修复完成后运行测试验证：
```bash
.venv/bin/python3 -m pytest tests/ -x -q
```

---

## 参考资源

- Lance Performance Optimizer Skill — Fragment 大小、索引策略、压缩配置
- Lance Data Engineer Skill — merge_insert、分布式写入、云存储集成
- [Lance Performance Guide](https://lance.readthedocs.io/en/latest/guides/performance/)
- [LanceDB Vector Search](https://lancedb.github.io/lancedb/concepts/indexing/)
