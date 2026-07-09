# LanceDB 优化路线图（#1–#8）

> **状态**：规划文档（2026-07-09 产出），基于本会话对 `arrow_lake` 代码的实际审查 + 本地 `lancedb` skill（基准 2026-06）对照。
> **范围**：识别 LanceDB 在产品中的可优化点，给出分级、方案、收益、风险与验证清单。**本文档本身不改代码**；实施由各改进项独立推进。
> **关联**：`v1.7.1-lance-duckdb-stack-optimization-plan.md`、`duckdb-optimization-plan.md`、`lance-optimization-plan.md`。

---

## 1. 背景与现状

### 1.1 Lance vs LanceDB（先区分两层）

| 层 | 包 | 职责 | 产品中的角色 |
|---|---|---|---|
| **Lance（格式）** | `pylance>=7.0.0` | 列式格式 / 版本 / 分支 / 索引底层 | 数据湖仓存储格式 |
| **LanceDB（SDK）** | `lancedb==0.33.0` | 建在 Lance 之上的向量库 SDK（connect/create_table/search/FTS/merge_insert） | 写入、FTS、向量 fallback、备份的访问层 |

二者绑定同一 Lance 格式，是两个访问层。**产品两层都在用**，且 `lancedb` SDK 是核心依赖。

### 1.2 产品中 LanceDB 的 6 处应用

| 场景 | 角色 | 位置 | 是否主路径 |
|---|---|---|---|
| 数据集写入 / CRUD | `connect` / `create_table` / `open_table` | `ingest/storage.py:128`、`ingest/_storage_crud.py:128` | ✅ 主 |
| 全文检索 FTS | LanceDB SDK + jieba 预分词（`_fts_segmented` 列） | `query/fts.py:361`、`_lake_search.py:259` | ✅ 主 |
| 向量检索 | LanceDB SDK（双路径之一） | `query/vector.py:82` | ⚠️ fallback（主走 DuckDB `lance_vector_search`） |
| HuggingFace 数据集 | `hf://` scheme 直读 | `_lake_ingest.py:48` | 专用 |
| 备份 | `lancedb.connect` 遍历表 | `ops/backup.py:338` | 专用 |
| 标量索引 | `create_scalar_index` BTREE/BITMAP | `ingest/_storage_indexing.py:125` | ✅ v1.7.1 #3 |

依赖锁定：`pyproject.toml:20-21`（`lancedb==0.33.0`、`pylance>=7.0.0`）。

### 1.3 已经做对的（肯定现状）

下列能力已落地，**不在本路线图改造范围**，仅记录以免误改：

- **向量检索双路径**：DuckDB 原生 `lance_vector_search` 为主，LanceDB SDK 为 fallback（`query/vector.py` `VectorSearchBridge`）。
- **原生 async 向量入口**：`search_async` 走 `lancedb.connect_async`（v1.7.1 #9，`query/vector.py:359`）。
- **标量索引按基数自动选型**：低基数→BITMAP，有序/数值→BTREE（`_storage_indexing.py:110-123`）。
- **后台 compaction**：`maintenance_scheduler.py` 自动压缩 + 版本清理。
- **自研 weighted-RRF + cross-encoder 精排**：多列 ensemble 融合（`query/ensemble.py`、`query/hybrid.py`、`rag/reranker.py`，v1.8.0 #5）。

---

## 2. 改进项总览

| # | 标题 | 分级 | ROI | 成本 | 状态 | 主要文件 |
|---|---|---|---|---|---|---|
| 1 | async 连接池 | 🟥 P0 | 高 | 低 | 待实施 | `query/vector.py` + 新模块 |
| 2 | merge_insert 幂等 | 🟥 P0 | 高 | 中 | 待实施 | `ingest/storage.py` |
| 3 | HNSW 索引选项 | 🟧 P1 | 中 | 低(改默认)+benchmark | 待评估 | `ingest/_storage_indexing.py` |
| 4 | FTS `with_position` 短语查询 | 🟧 P1 | 中 | 低 | 待实施 | `query/fts.py` |
| 5 | hybrid 对接原生 `.hybrid()` | 🟧 P1 | 中 | 中 | 待评估 | `query/hybrid.py`、`ensemble.py` |
| 6 | tags/branches 版本管理 | 🟨 P2 | 低 | 低 | 待实施 | 版本读取路径 |
| 7 | `num_sub_vectors` 自动推算 | 🟨 P2 | 低 | 低 | 待实施 | 索引配置 |
| 8 | async FTS 迁移 | 🔬 已验证 | 高 | 中 | ⏳ 另一会话进行中 | `query/fts.py`、`hybrid.py`、`faceted.py` |

**ROI 推荐顺序**：**#1 → #8 → #2 → #3/#4**。

---

## 3. 🟥 高价值 · 明确技术债（P0）

### #1 async 连接池

**现状**
`query/vector.py:359-426` 的 `search_async` 每次调用都新建连接 + 打开表，代码注释自己承认无池化：

```python
# vector.py:372（注释原文）
# connection is opened per call (no pooling) — for production throughput,
# pair with an async connection pool and load-test before relying on it.
```

```python
# vector.py:408-421（现状核心）
import lancedb
async_db = await lancedb.connect_async(base_uri, storage_options=...)
table = await async_db.open_table(dataset_name)
q = table.search(query_vector, vector_column_name=vector_column).limit(effective_top_k)
if where is not None:
    q = q.where(where)
q = q.nprobes(nprobes or self._config.nprobes)
q = q.refine_factor(self._config.refine_factor)
return await q.to_arrow()
```

**问题**
- 高并发 RAG 下，`connect_async` + `open_table` 反复建立，是吞吐与尾延迟瓶颈。
- 带 `where` 过滤的查询**强制走此 SDK 路径**（DuckDB `lance_vector_search` 无 filter 参数，见 `vector.py:375` 注释），无法回避。

**方案**：进程级 `AsyncConnection` 单例 + per-dataset `AsyncTable` 句柄缓存（带 schema 版本失效）。

- **复用**：参考项目现有池化思路 `arrow_lake/catalog/connection_pool.py`（已有 `asyncio.to_thread` acquire/release 模式）。
- 新增模块 `arrow_lake/query/async_conn_pool.py`：

```python
# after（骨架）
from functools import lru_cache
import lancedb

_conn_cache: dict[str, "AsyncConnection"] = {}
_table_cache: dict[tuple[str, str], "AsyncTable"] = {}

async def get_async_table(base_uri: str, name: str, storage_options: dict | None):
    # 1) 复用 AsyncConnection
    conn = _conn_cache.get(base_uri)
    if conn is None:
        conn = await lancedb.connect_async(base_uri, storage_options=storage_options)
        _conn_cache[base_uri] = conn
    # 2) 复用 AsyncTable 句柄
    key = (base_uri, name)
    table = _table_cache.get(key)
    if table is None:
        table = await conn.open_table(name)
        _table_cache[key] = table
    return table

def invalidate_async_table(name: str, base_uri: str | None = None):
    """schema 变更 / 重建索引后调用，丢弃对应表句柄。"""
    ...
```

- `vector.py` 改造后：

```python
# after（search_async 核心）
from arrow_lake.query.async_conn_pool import get_async_table
table = await get_async_table(base_uri, dataset_name,
                              getattr(self._storage, "_storage_options", None))
q = table.search(query_vector, vector_column_name=vector_column).limit(effective_top_k)
...  # where/nprobes/refine_factor 不变
return await q.to_arrow()
```

**收益预估**：消除 per-call 连接建立；高并发 QPS 提升、p99 下降（需 benchmark 量化，见验证）。

**风险 / 回滚**
- 风险：表 schema 变更或重建索引后，缓存的 `AsyncTable` 句柄可能过期 → 需在 `rebuild_vector_index` / `add_columns` 等变更点调 `invalidate_async_table`。
- 线程安全：`AsyncConnection`/`AsyncTable` 设计为并发读安全（见 `hybrid.py:61` 既有注释）；写操作不在本路径。
- 回滚：退回 per-call 连接，行为等价，零风险回退。

**验证**
- 单元：`tests/unit/query/` 新增「重复调用复用同一连接/表句柄」「invalidate 后重建」断言。
- 基准：前后对比并发向量搜索 QPS / p99（可用现有压测脚本或 `pytest-benchmark`）。
- 回归：`.venv/bin/python3 -m pytest -q tests/unit/query/`。

---

### #2 merge_insert 幂等

**现状**
`ingest/storage.py:309` 用 `table.add(data)` 追加写入；去重仅靠上游 content-hash 缓存，Lance 层无幂等保证。

```python
# storage.py:308-319（现状）
table.add(data)
# Apply write optimization via compaction when configured
...
table.optimize()
```

**问题**
- 重跑 / 部分失败重试 → Lance 层产生**重复行**，污染检索结果。
- 上游哈希缓存若失效或绕过，重复行无法在存储层兜底。

**方案**：加 `content_hash` 列 + `merge_insert` 幂等 upsert。

```python
# after（骨架）
if "content_hash" in table.schema.names:
    (table.merge_insert(on=["content_hash"])
           .when_matched_update_all()
           .when_not_matched_insert_all()
           .execute(data))
else:
    table.add(data)  # 兼容旧表（无 content_hash 列）
```

**成本 / 风险 / 回滚**
- 成本：schema 迁移（新增 `content_hash` 列）+ 改写入路径 + 历史数据回填 `content_hash`。
- 风险：`merge_insert` 写入比 `add` 略慢（需查 key）；大表批量回填需分批。
- 回滚：保留 `add` 分支开关（见上 `else`），随时退回。

**验证**
- 集成：对同一 ingest 数据跑两遍，断言行数不变、无重复 `content_hash`。
- 回归：`tests/unit/ingest/`。

---

## 4. 🟧 中价值 · 检索质量 / 代码简化（P1）

### #3 HNSW 索引选项

**现状**
`ingest/_storage_indexing.py:53` 向量索引类型写死 `index_type="IVF_PQ"`。

**问题**
- IVF_PQ 需训练、有量化损失；对**中小规模**知识库（业务 PDF 场景，常 < 100 万行）召回和延迟未必最优。
- HNSW 免训练、查询低延迟、高召回，但索引体积大、构建慢——没有暴露这个选项。

**方案**：在 CLI / 配置暴露 `index_type` 可选 `HNSW` / `IVF_HNSW_SQ` / `IVF_PQ`，附选型表：

| 数据量 | 维度 | 推荐 | 理由 |
|---|---|---|---|
| < 100 万行 | 任意（含 bge-m3 1024） | **HNSW** | 免训练、低延迟、高召回；内存可接受 |
| 100 万–1000 万 | 中高维 | IVF_HNSW_SQ / IVF_PQ | 内存/召回平衡 |
| > 1000 万 | 高维 | IVF_PQ（大 `num_partitions`） | 内存受限；LanceDB 暂无 DiskANN |

**成本**：改默认/暴露参数（低）+ benchmark（中）。
**风险**：HNSW 索引体积约为原数据的 1.5–2×；构建时间更长。
**验证**：`recall@k` benchmark（HNSW vs IVF_PQ，同查询集）。

---

### #4 FTS `with_position` 短语查询

**现状**
`query/fts.py` 的 `create_fts_index` 走默认参数（未显式开 `with_position`）。中文已用 jieba 预分词写 `_fts_segmented` 列绕过 tokenizer。

**问题**
- 默认不支持**短语 / 邻近匹配**（如「知识图谱」作为整体命中，而非「知识」「图谱」两词 OR）。

**方案**：建 FTS 索引时 `with_position=True`，配合现有 jieba 列。

```python
# after（骨架）
table.create_fts_index("_fts_segmented", with_position=True, replace=True)
```

**成本**：低（参数 + 重建一次 FTS 索引）。
**风险**：索引体积略增；短语查询语法需在 query 层透传（如 `"知识图谱"` 带引号）。
**验证**：短语查询命中率对比（开 vs 不开 `with_position`）。

---

### #5 hybrid 对接原生 `.hybrid()`

**现状**
`query/hybrid.py` + `query/ensemble.py` 手搓 weighted-RRF（`score = Σ w_i/(rank+1+k)`）+ `rag/reranker.py` 自研 cross-encoder 精排。

**问题**
- 自维护 id 对齐 / 列清洗 / 融合代码；LanceDB 原生 `AsyncHybridQuery` + 自定义 `Reranker` 接口可减少这部分代码面。

**方案**：评估常规「向量+FTS」路径切原生 `.hybrid()`，把现有 cross-encoder 实现 `Reranker` 接口接入。

**取舍（不一定迁）**：自研 ensemble 支持**多列加权**（比原生灵活），多列场景保留自研；仅纯向量+FTS 常规路径考虑迁原生。

**成本**：中（设计 Reranker 适配 + 等价性验证）。
**验证**：等价性测试（自研 vs 原生返回 row_id 集合一致）。

---

## 5. 🟨 低价值 / 增量（P2）

### #6 tags / branches 版本管理
**现状**：`open_dataset_versioned` 用 version 整数读历史版本。
**方案**：LanceDB 支持 Git 式 tag/branch（skill 14 章），给「已验收快照」打 tag（如 `verified-2026-07`），比 version int 对业务更友好。
**成本**：低。**验证**：tag 读取等价于对应 version。

### #7 `num_sub_vectors` 自动推算
**现状**：bge-m3 1024 维手动配 `num_sub_vectors=32`（见业务 PDF 端到端记忆），换 embedding 维度易踩坑。
**方案**：自动推算（`dim / sub_vectors` 需整除，每段 ~32 维启发式）+ 整除性校验，换模型时免手动。
**成本**：低。

---

## 6. 🔬 已验证 · 待实施：#8 async FTS 迁移

### 验证结论（2026-07-09，运行时内省 + `.pyi` 存根）

**`AsyncTable` 在 0.33.0 原生支持 FTS 和 hybrid**，`query/fts.py:405` 注释「AsyncTable lacks FTS」**过时 / 错误**，**无需升级 lancedb**。

**复现命令**（可独立验证）：

```bash
.venv/bin/python3 -c "
from lancedb.table import AsyncTable, AsyncFTSQuery, AsyncHybridQuery
print('AsyncTable FTS-related:',
      [m for m in dir(AsyncTable) if not m.startswith('_') and any(k in m for k in ['search','query'])])
print('AsyncFTSQuery:', [m for m in dir(AsyncFTSQuery) if not m.startswith('_')])
"
```

**API 证据**：
- `AsyncTable.search(query, query_type="fts", fts_columns=...)` → `AsyncFTSQuery`
- `AsyncTable.search(query, query_type="hybrid", fts_columns=...)` → `AsyncHybridQuery`
- `AsyncFTSQuery` / `AsyncHybridQuery` 有完整 async 方法：`to_arrow / to_batches / where / limit / rerank / nearest_to`

### 3 处 `to_thread` 待迁

| 文件:行 | 现状 | 迁移后（骨架） |
|---|---|---|
| `query/fts.py:411` | `await asyncio.to_thread(self._search_via_lancedb, table, query, top_k, col)` | `async_table.search(query, query_type="fts", fts_columns=col).limit(top_k).to_arrow()` |
| `query/hybrid.py:235` | `to_thread` 跑子 bridge fusion | 原生 async vector + async FTS（或原生 `AsyncHybridQuery`） |
| `query/faceted.py:234` | `to_thread` | 视子查询类型走原生 async |

**before（fts.py 现状）**：
```python
return await asyncio.to_thread(
    self._search_via_lancedb, table, effective_query, effective_top_k, search_column
)
```

**after（原生 async）**：
```python
async_db = await lancedb.connect_async(base_uri, storage_options=...)
async_table = await async_db.open_table(dataset_name)
result = await async_table.search(
    effective_query, query_type="fts", fts_columns=search_column
).limit(effective_top_k).to_arrow()
```

### 迁移要点
- **jieba 预分词 + `_fts_segmented` 列逻辑保留**，只换执行层（索引 / query 构造与 sync|async 正交）。
- async 读的是**同一个** `create_fts_index` 建的索引，召回与同步路径一致。
- 同步 `_search_via_lancedb` 可保留作 fallback，渐进迁移。

### 验证
- **等价性测试**：同一 dataset + query，断言 async FTS 与同步 `_search_via_lancedb` 返回**相同 row_id 集合与排序**，再替换调用点。
- 顺手修正 `fts.py:405` 过时注释。
- `.venv/bin/python3 -m pytest -q tests/unit/query/`。

**状态**：⏳ 另一会话进行中。

---

## 7. 推荐路线

**ROI 排序**：**#1 → #8 → #2 → #3/#4**

| 批次 | 项 | 说明 |
|---|---|---|
| P0 | #1, #2 | 高 ROI 技术债；#1 纯局部，#2 涉及 schema |
| P1 | #3, #4, #5 | 检索质量 / 代码简化；需 benchmark / 等价性验证 |
| P2 | #6, #7 | 增量优化 |
| 并行 | #8 | 另一会话，随其进度 |

**并发约束（避免冲突）**：
- #1 务 `query/vector.py` + 新增 `async_conn_pool.py`
- #8 务 `query/fts.py` / `hybrid.py` / `faceted.py`
- **两者文件不重叠，可并行推进。**

---

## 8. 附录

### skill 交叉引用
- `lancedb` skill：09 章（搜索写法）、04 章（reranker）、05 章（DuckDB-Lance SQL）
- `lance-org` skill：04 章（索引选型权威：IVF/HNSW/PQ/SQ/RQ）

### 相关历史文档
- `v1.7.1-lance-duckdb-stack-optimization-plan.md`（lancedb 0.30→0.33 / pylance→7.0 升级）
- `duckdb-optimization-plan.md`、`lance-optimization-plan.md`

### 文档自身验证
- 所有 `file:line` 引用基于 2026-07-09 实际审查的代码。
- #8 API 证据来自 `.venv`（lancedb 0.33.0）运行时内省 + `_lancedb.pyi` 类型存根，附复现命令可独立校验。
