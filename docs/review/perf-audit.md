# Arrow Lake v1.2 — 性能评估复盘

**日期**: 2026-04-27
**范围**: 全项目性能瓶颈、资源管理、并发模式、可扩展性

---

## 总览评分

| 维度 | 评分 | 说明 |
|------|------|------|
| 连接池管理 | 8/10 | DuckDB session pool 完善，S3 连接池缺失 |
| 异步模式 | 5/10 | 仅 OLAP 查询做了 executor 包装，搜索/入库全部阻塞事件循环 |
| 内存管理 | 6/10 | FTS chunk 循环无显式清理，RRF fusion 大量 dict 拷贝 |
| 批处理 | 4/10 | 入库/搜索/Gremlin/实体提取全为顺序执行 |
| 缓存策略 | 3/10 | 仅 metrics 条件检查+embedding 模型缓存，无查询结果缓存 |
| 超时保护 | 6/10 | DuckDB 层+OLAP API 层有，搜索端点缺失 |
| **综合** | **5.3/10** | 并发处理是最大短板 |

---

## CRITICAL — 事件循环阻塞 (影响并发吞吐)

所有 Lake 方法都是同步的，但在 API 端点中大部分 **未用 `run_in_executor` 包装**。

### 已正确处理 (3 个端点)

| 文件 | 端点 | 状态 |
|------|------|------|
| `api/routers/query.py:20` | OLAP 查询 | ✅ `run_in_executor` + `wait_for` |
| `api/routers/query.py:36` | 元数据查询 | ✅ 同上 |
| `api/routers/query.py:53` | Daft 查询 | ✅ 同上 |
| `api/routers/knowledge_graph.py` | 全部 KG 端点 | ✅ facade 层已是 async |

### 未处理 (30+ 个端点)

| 文件 | 端点 | 影响 |
|------|------|------|
| `api/routers/search.py:34` | vector search | 向量搜索阻塞 |
| `api/routers/search.py:64` | text search | FTS 搜索阻塞 |
| `api/routers/search.py:92` | hybrid search | 双搜索阻塞 |
| `api/routers/search.py:123` | faceted search | CUBE 查询阻塞 |
| `api/routers/search.py:158` | ensemble search | 多列搜索阻塞 |
| `api/routers/datasets.py:35` | ingest files | 文件 I/O 阻塞 |
| `api/routers/datasets.py:48` | ingest HTTP | 网络下载阻塞 |
| `api/routers/datasets.py:61` | ingest images | 图像处理阻塞 |
| `api/routers/datasets.py:74` | ingest videos | 视频处理阻塞 |
| `api/routers/quality.py` | quality filter | 数据过滤阻塞 |
| `api/routers/lineage.py` | lineage 查询 | DuckDB 查询阻塞 |
| `api/routers/audit.py` | audit 查询 | 存储查询阻塞 |

**影响**: 在并发请求下，单个慢请求会阻塞整个事件循环，导致所有其他请求等待。

**建议**: 创建统一的 `run_sync` helper，在所有调用同步 Lake 方法的端点中使用。

---

## HIGH — 顺序处理瓶颈

### 1. 文件入库全顺序执行

**文件**: `ingest/ingestor.py`

| 方法 | 行号 | 问题 |
|------|------|------|
| `ingest` | 111-117 | 逐文件 for 循环，无并行 |
| `ingest_http` | 141-147 | 逐 URL 下载，无并发 |
| `ingest_images` | 171-185 | 逐图像处理 |
| `ingest_videos` | 209-219 | 逐视频关键帧提取 |

**影响**: 1000 个文件 = 1000 个顺序 I/O 操作。

### 2. 搜索桥接顺序调用

**文件**: `query/hybrid.py:308-324`

Vector search 和 FTS search 顺序执行，可以 `asyncio.gather` 并行。

**文件**: `query/ensemble.py:137-152`

多列向量搜索逐列执行，N 列 = N 次顺序搜索。

### 3. KG 实体提取全顺序

**文件**: `knowledge_graph/builder.py:200-263`

每个 chunk 顺序触发：LLM 调用 → vertex 插入 → edge 插入。N 个 chunk = N 次 LLM + 2N 次 HTTP。

**文件**: `knowledge_graph/retriever.py:91-123`

实体查找和邻居展开全部顺序 O(N) HTTP 调用。

### 4. HTTP 入库未并发

**文件**: `ingest/ingestor.py:141-147`

URL 下载逐个执行，应使用 `asyncio.gather` + `httpx.AsyncClient`。

---

## HIGH — 内存管理问题

### 1. FTS 分块循环无显式清理

**文件**: `query/fts.py:231-248`

```python
for offset in range(0, row_count, _chunk_size):
    batch = ds.to_table(columns=[source_column], offset=offset, limit=_chunk_size)
    # ... 处理 ...
    chunk_table = batch.append_column(segmented_column, new_col)
```

`batch` 和 `chunk_table` 在循环中创建但未显式释放，依赖 GC。

### 2. RRF Fusion 大量 dict 拷贝

**文件**: `query/hybrid.py:368-385`

```python
id_to_row: dict[str, dict[str, Any]] = {}
for i, doc_id in enumerate(ids):
    row: dict[str, Any] = {}
    for col_name in table.column_names:
        row[col_name] = table.column(col_name)[i].as_py()
    id_to_row[id_str] = row
```

top_k=10000 → 10000 个 dict 对象，每个含所有列的 Python 对象拷贝。应直接操作 Arrow Table。

### 3. 大文件全量加载

**文件**: `ingest/ingestor.py:408-443`

`_read_file` 将整个文件加载为 Arrow Table。100MB+ 文件 × 并发数 = 内存压力。

---

## MEDIUM — 缓存缺失

| 层 | 现状 | 建议 |
|----|------|------|
| 查询结果 | 无缓存 | 对高频查询（faceted counts, metadata）添加 TTL 缓存 |
| DuckDB plan | 无 | Prepared statement 缓存 |
| Vector index | 每次搜索打开数据集 | 惰加载+缓存 dataset handle |
| Faceted CUBE | 每次全量计算 | 物化视图或增量更新 |
| KG 路径查询 | 无缓存 | 对高频实体添加内存缓存 |
| Embedding 模型 | 惰加载 ✅ | 可加 warm-up endpoint |

---

## MEDIUM — 超时保护缺失

| 端点 | DuckDB 超时 | API 超时 |
|------|-------------|---------|
| OLAP 查询 | ✅ 300s | ✅ 300s |
| Daft 查询 | ❌ | ✅ 300s |
| Vector search | ❌ | ❌ |
| FTS search | ❌ | ❌ |
| Hybrid search | ❌ | ❌ |
| Faceted search | ❌ | ❌ |
| Ingest | ❌ | ❌ |
| KG build | ❌ | ❌ |

---

## MEDIUM — 死信队列 I/O 开销

**文件**: `ingest/dead_letter.py:164-171`

- 每次 add 失败项 → 获取锁 → 文件写入
- 每次 save → **重写整个队列文件**
- 高失败率场景下锁竞争和 I/O 开销显著

---

## LOW — 其他发现

| # | 文件 | 问题 | 影响 |
|---|------|------|------|
| 1 | `api/routers/datasets.py:110` | `list_datasets` 无分页 | 大量数据集时 OOM |
| 2 | `api/routers/audit.py:67` | `audit_query` 无 limit | 可返回百万条记录 |
| 3 | `rag/session.py:79-90` | session 驱逐 O(n) 排序 | 大量 session 时开销 |
| 4 | `knowledge_graph/vermeer_client.py:152` | 固定 1s 轮询间隔 | 应使用指数退避 |
| 5 | `embed/ray_serve_encoder.py:131` | 顺序 batch remote 调用 | 应并行发送 |
| 6 | `storage/blob_store.py:503-537` | 前缀删除先加载所有对象 | 大量对象时内存压力 |

---

## 建议优先级

### P0 — 立即修复 (影响并发稳定性)

1. **搜索端点添加 `run_in_executor` + 超时** — 影响所有搜索 API 的并发能力
2. **入库端点添加 `run_in_executor`** — 防止长时间入库阻塞其他请求

### P1 — 短期优化 (1-2 周)

3. **Hybrid search 并行化** — vector + FTS 用 `asyncio.gather`
4. **FTS 分块循环显式 `del`** — 防止大索引构建时 OOM
5. **RRF fusion 改用 Arrow Table 操作** — 避免 Python 对象拷贝

### P2 — 中期优化 (1 个月)

6. **入库并行化** — `asyncio.Semaphore` + `run_in_executor`
7. **KG 实体提取并行** — chunk 级并发 LLM 调用
8. **查询结果缓存** — faceted/metadata 等高频查询

### P3 — 长期优化

9. **分布式 session 存储** — Lance-backed 替代内存
10. **KG Gremlin 查询批量 API**
11. **Embedding 模型 warm-up endpoint**
