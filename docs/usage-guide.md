# Arrow Lake 使用指南

本文档通过实际示例介绍 Arrow Lake 平台的核心功能。

---

## 目录

1. [环境准备](#1-环境准备)
2. [快速上手：10 分钟入门](#2-快速上手10-分钟入门)
3. [数据集管理](#3-数据集管理)
4. [SQL 查询](#4-sql-查询)
5. [向量搜索](#5-向量搜索)
6. [全文搜索](#6-全文搜索)
7. [混合搜索](#7-混合搜索)
8. [内容去重](#8-内容去重)
9. [数据导出](#9-数据导出)
10. [版本管理与标签](#10-版本管理与标签)
11. [数据质量检查](#11-数据质量检查)
12. [数据血缘](#12-数据血缘)
13. [审计日志](#13-审计日志)
14. [配置管理](#14-配置管理)
15. [CLI 命令行](#15-cli-命令行)
16. [HTTP 服务](#16-http-服务)

---

## 1. 环境准备

### 1.1 安装依赖

```bash
# 克隆项目
git clone <repo-url> && cd wits-infra-dintellihub

# 创建虚拟环境并安装全部依赖
uv venv && source .venv/bin/activate
uv sync

# 可选：安装感知哈希去重支持
uv sync --extra dedup
```

### 1.2 验证安装

```bash
uv run python -c "from arrow_lake import Lake; print(f'Arrow Lake {Lake(base_uri=\"./tmp\").version()}')"
# 输出: Arrow Lake 0.1.0
```

### 1.3 基础设施（可选）

如需使用 MinIO 对象存储：

```bash
docker compose -f deploy/docker-compose.yml up -d
```

---

## 2. 快速上手：10 分钟入门

下面这个例子覆盖了最常用的操作：创建数据集、SQL 查询、去重、导出。

```python
import pyarrow as pa
from arrow_lake import Lake
from arrow_lake.ingest.storage import LanceStorageManager

# --- 初始化 ---
lake = Lake(base_uri="./my_lake")
storage = lake._get_storage()

# --- 写入数据 ---
table = pa.table({
    "id": ["d001", "d002", "d003", "d004", "d005"],
    "title": ["机器学习入门", "深度学习实战", "数据分析基础", "机器学习入门", "Python 教程"],
    "category": ["ml", "dl", "data", "ml", "dev"],  # d001 和 d004 重复
    "word_count": [5000, 8000, 3000, 5000, 6000],
})

storage.create_dataset("articles", table)
print(f"已创建数据集，共 {table.num_rows} 行")

# --- SQL 查询 ---
result = lake.olap_query(
    "articles",
    "SELECT category, COUNT(*) as cnt, AVG(word_count) as avg_words "
    "FROM articles GROUP BY category ORDER BY cnt DESC",
)
for i in range(result.table.num_rows):
    cat = result.table.column("category")[i].as_py()
    cnt = result.table.column("cnt")[i].as_py()
    avg = result.table.column("avg_words")[i].as_py()
    print(f"  {cat}: {cnt} 篇, 平均字数 {avg:.0f}")

# --- 去重（基于 title） ---
# 注意：默认基于 image_data 列。对文本列需要先将 title 放到 image_data，
# 或直接使用底层 ContentDeduplicator
from arrow_lake.quality.dedup import ContentDeduplicator

ds = storage.read_dataset("articles")
dedup = ContentDeduplicator(strategy="exact", action="flag")
# 手动构造 hash 列
dedup_result = dedup.deduplicate(ds)  # 如果有 image_data 列

# --- 导出 ---
lake.export("articles", "./output/articles.parquet", overwrite=True)
print(f"已导出到 ./output/articles.parquet")

# --- 清理 ---
storage.delete_dataset("articles")
```

---

## 3. 数据集管理

### 3.1 创建数据集

```python
import pyarrow as pa
from arrow_lake.ingest.storage import LanceStorageManager

storage = LanceStorageManager(base_uri="./data")

# 基本数据集
table = pa.table({
    "id": [1, 2, 3],
    "name": ["Alice", "Bob", "Carol"],
    "score": [95.0, 87.5, 92.3],
})
storage.create_dataset("users", table)
```

**命名规则**：数据集名称必须匹配 `^[a-zA-Z_][a-zA-Z0-9_-]*$`（字母或下划线开头，仅含字母、数字、下划线、连字符）。

### 3.2 读取数据

```python
# 读取全部数据
table = storage.read_dataset("users")
print(f"行数: {table.num_rows}, 列: {table.column_names}")

# 读取指定列
table = storage.read_dataset("users", columns=["id", "name"])

# 读取特定版本
table = storage.read_dataset("users", version=1)
```

### 3.3 追加数据

```python
new_rows = pa.table({
    "id": [4, 5],
    "name": ["Dave", "Eve"],
    "score": [88.0, 91.5],
})
storage.append_dataset("users", new_rows)
```

### 3.4 列操作

```python
# 添加新列（SQL 表达式）
storage.add_column("users", "status", "CAST('active' AS VARCHAR)")

# 修改列类型
import pyarrow as pa
storage.alter_column("users", "score", pa.float64())

# 删除列
storage.drop_column("users", "status")
```

### 3.5 列出和删除

```python
# 列出所有数据集
datasets = storage.list_datasets()  # → ["users", "documents"]

# 检查是否存在
storage.dataset_exists("users")  # → True

# 删除数据集
storage.delete_dataset("old_dataset")
```

### 3.6 压缩优化

多次追加后会产生碎片文件，可以合并优化：

```python
stats = storage.compact("users")
print(f"版本: {stats.version_before} → {stats.version_after}")
print(f"文件数: {stats.fragments_before} → {stats.fragments_after}")
```

---

## 4. SQL 查询

Arrow Lake 提供两种 SQL 查询接口，都基于 DuckDB 引擎。

### 4.1 OLAP 查询 (`lake.olap_query`)

适合聚合分析，支持 GROUP BY、HAVING、窗口函数、JOIN。

```python
from arrow_lake import Lake

lake = Lake(base_uri="./data")

# 基本聚合
result = lake.olap_query("users",
    "SELECT category, COUNT(*) as cnt FROM users GROUP BY category")
print(result.table)

# HAVING 过滤
result = lake.olap_query("users",
    "SELECT city, AVG(salary) as avg_sal FROM users "
    "GROUP BY city HAVING AVG(salary) > 10000")

# 窗口函数
result = lake.olap_query("users",
    "SELECT name, department, salary, "
    "ROW_NUMBER() OVER (PARTITION BY department ORDER BY salary DESC) as dept_rank "
    "FROM users")

# LIMIT 限制返回行数
result = lake.olap_query("users",
    "SELECT * FROM users ORDER BY salary DESC LIMIT 10")

# 限制最大返回行数
result = lake.olap_query("users",
    "SELECT * FROM users", max_rows=100)
```

**重要**：`FROM` 后面的表名必须与数据集名称完全一致。例如数据集叫 `users`，SQL 就是 `FROM users`。

### 4.2 多表 JOIN

```python
# 自连接
result = lake.olap_query("orders",
    "SELECT a.id, b.id as related_id FROM orders a "
    "JOIN orders b ON a.customer_id = b.customer_id "
    "WHERE a.id < b.id")

# 关联外部 Arrow 表
import pyarrow as pa
extra_table = pa.table({"tag_id": [1, 2], "tag_name": ["vip", "normal"]})

result = lake.olap_query(
    "users",
    "SELECT users.name, tags.tag_name FROM users "
    "JOIN tags ON users.id = tags.tag_id",
    tables={"tags": extra_table},
)
```

### 4.3 元数据查询 (`lake.query`)

```python
# 与 olap_query 功能相同，语义别名
result = lake.query("users",
    "SELECT department, MIN(salary) as min_sal, MAX(salary) as max_sal "
    "FROM users GROUP BY department")
```

### 4.4 SQL 安全限制

- 仅允许 `SELECT` 语句
- 禁止 `INSERT / UPDATE / DELETE / DROP / ALTER / CREATE / TRUNCATE` 等危险关键字
- 禁止分号（`;`）
- 禁止 `UNION / EXCEPT / INTERSECT`

```python
# 这些会抛出 QueryError:
lake.olap_query("users", "DROP TABLE users")
lake.olap_query("users", "SELECT * FROM users; INSERT INTO users VALUES (1)")
```

---

## 5. 向量搜索

### 5.1 创建向量索引

```python
lake = Lake(base_uri="./data")

# 数据集需要有向量列（FixedSizeList[float] 类型）
# 先创建数据集
import numpy as np
import pyarrow as pa

n, dim = 1000, 128
rng = np.random.RandomState(42)
vectors = rng.randn(n, dim).astype(np.float32)
norms = np.linalg.norm(vectors, axis=1, keepdims=True)
vectors = vectors / np.where(norms == 0, 1, norms)

table = pa.table({
    "id": [f"doc_{i:04d}" for i in range(n)],
    "text_content": [f"Document {i} about AI and data science" for i in range(n)],
    "category": [f"cat_{i % 10}" for i in range(n)],
    "text_embedding": pa.FixedSizeListArray.from_arrays(vectors.ravel(), dim),
})

from arrow_lake.ingest.storage import LanceStorageManager
storage = lake._get_storage()
storage.create_dataset("docs", table)

# 创建向量索引
# num_sub_vectors 必须能整除向量维度 (128 / 8 = 16)
info = lake.create_vector_index(
    "docs",
    vector_column="text_embedding",
    metric="cosine",           # cosine / l2 / dot
    num_sub_vectors=8,
    replace=True,              # 覆盖已有索引
)
print(f"索引类型: {info.index_type}, 已索引行: {info.num_indexed_rows}")
```

**配置项说明** (在 `configs/dev.yaml` 的 `vector` 部分)：

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `metric` | cosine | 距离度量 (cosine / l2 / dot) |
| `default_index_type` | IVF_PQ | 索引类型 |
| `num_partitions` | 256 | IVF 分区数 |
| `num_sub_vectors` | 24 | PQ 子向量数 (必须整除向量维度) |
| `nprobes` | 20 | 搜索时探测的分区数 |
| `default_top_k` | 10 | 默认返回结果数 |

### 5.2 执行向量搜索

```python
# 取一个查询向量
query_vector = vectors[0].tolist()

result = lake.search(
    "docs",
    query_vector,
    top_k=5,
    vector_column="text_embedding",
    metric="cosine",
    nprobes=20,
)

# 结果是 VectorSearchResult
print(f"返回 {result.row_count} 条结果")
for i in range(result.table.num_rows):
    doc_id = result.table.column("id")[i].as_py()
    text = result.table.column("text_content")[i].as_py()
    distance = result.table.column("_distance")[i].as_py()
    print(f"  {doc_id} (距离={distance:.4f}): {text}")
```

### 5.3 带过滤条件的搜索

```python
result = lake.search(
    "docs",
    query_vector,
    top_k=5,
    where="category = 'cat_3'",  # 过滤条件
)
```

---

## 6. 全文搜索

### 6.1 创建 FTS 索引

```python
# 创建全文搜索索引（基于 Tantivy/BM25）
lake.create_fts_index(
    "docs",
    fts_column="text_content",  # 要索引的文本列
    replace=True,
)
```

### 6.2 执行全文搜索

```python
result = lake.text_search(
    "docs",
    query="data science AI",
    top_k=5,
    fts_column="text_content",
)

# 结果是 FullTextSearchResult
print(f"返回 {result.row_count} 条结果, 最高分: {result.max_score}")
for i in range(result.table.num_rows):
    doc_id = result.table.column("id")[i].as_py()
    score = result.table.column("_score")[i].as_py()
    text = result.table.column("text_content")[i].as_py()
    print(f"  {doc_id} (BM25={score:.4f}): {text}")
```

---

## 7. 混合搜索

混合搜索将向量相似度和全文 BM25 分数通过 RRF (Reciprocal Rank Fusion) 融合。

```python
query_vector = vectors[0].tolist()

result = lake.hybrid_search(
    "docs",
    query_vector=query_vector,     # 向量查询
    query_text="machine learning",  # 文本查询
    top_k=10,
    vector_column="text_embedding",
    fts_column="text_content",
    where="category = 'cat_3'",     # 可选过滤
)

# 结果包含 _rrf_score 融合分
for i in range(result.table.num_rows):
    doc_id = result.table.column("id")[i].as_py()
    rrf = result.table.column("_rrf_score")[i].as_py()
    print(f"  {doc_id} (RRF={rrf:.6f})")
```

**RRF 公式**：`score = 1 / (k + rank)`，其中 `k` 默认为 60（可在 `configs/dev.yaml` 的 `hybrid.rrf_k` 调整）。

---

## 8. 内容去重

### 8.1 精确去重（SHA-256）

基于二进制内容的 SHA-256 哈希值进行精确匹配。

```python
# 准备数据（有重复的 image_data）
import pyarrow as pa

table = pa.table({
    "id": ["img_001", "img_002", "img_003", "img_004", "img_005"],
    "image_data": [
        b"photo_content_A",  # 唯一
        b"photo_content_B",  # 唯一
        b"photo_content_A",  # 重复 img_001
        b"photo_content_C",  # 唯一
        b"photo_content_B",  # 重复 img_002
    ],
})

storage.create_dataset("images", table)

# 模式 1: 移除重复行
result = lake.deduplicate("images", strategy="exact", action="remove")
print(f"总行数: {result.total_rows}")         # 5
print(f"唯一行: {result.unique_rows}")         # 3 (A, B, C)
print(f"重复行: {result.duplicates_found}")    # 2
# result.table 包含去重后的 3 行

# 模式 2: 标记重复行（保留所有行，添加 is_duplicate 列）
result = lake.deduplicate("images", strategy="exact", action="flag")
print(f"结果行数: {result.table.num_rows}")   # 5（全部保留）
flags = result.table.column("is_duplicate").to_pylist()
print(f"标记: {flags}")  # [False, False, True, False, True]
```

### 8.2 感知哈希去重（pHash）

用于检测视觉上相似但不完全相同的图片（需要 `imagehash` 库）。

```python
result = lake.deduplicate(
    "images",
    strategy="perceptual",
    action="remove",
    perceptual_threshold=10,  # Hamming 距离阈值，越小越严格
)
```

### 8.3 组合策略

```python
# 先精确去重，再对剩余行做感知去重
result = lake.deduplicate("images", strategy="both", action="remove")
```

### 8.4 增量去重（跨批次）

适用于流式数据处理场景——每个新批次去重时参考历史哈希。

```python
from arrow_lake.quality.dedup import ContentDeduplicator

dedup = ContentDeduplicator(strategy="exact", action="remove")

# 第一批
batch1 = pa.table({
    "id": ["a", "b"],
    "image_data": [b"content_X", b"content_Y"],
})
result1, seen_hashes = dedup.deduplicate_incremental(batch1)
print(f"批次1: {result1.unique_rows} 唯一, {result1.duplicates_found} 重复")
# seen_hashes: {"sha256_X": "a", "sha256_Y": "b"}

# 第二批（包含与批次1重复的内容）
batch2 = pa.table({
    "id": ["c", "d", "e"],
    "image_data": [b"content_X", b"content_Z", b"content_Y"],
})
result2, seen_hashes = dedup.deduplicate_incremental(batch2, existing_sha256=seen_hashes)
print(f"批次2: {result2.unique_rows} 唯一, {result2.duplicates_found} 重复")
# 只有 content_Z 是新的
```

---

## 9. 数据导出

### 9.1 导出到 Parquet

```python
# 基本导出
result = lake.export("docs", "./output/docs.parquet", overwrite=True)
print(f"格式: {result.format}, 行数: {result.row_count}, 大小: {result.file_size_bytes} bytes")

# 指定压缩格式
result = lake.export("docs", "./output/docs_gz.parquet",
    compression="gzip", overwrite=True)

# 支持的压缩格式: snappy, gzip, brotli, zstd, lz4, none

# 导出指定列
result = lake.export("docs", "./output/docs_subset.parquet",
    columns=["id", "category"], overwrite=True)

# 导出特定版本
result = lake.export("docs", "./output/docs_v1.parquet", version=1, overwrite=True)
```

### 9.2 导出到 CSV

```python
result = lake.export("docs", "./output/docs.csv", overwrite=True)
print(f"导出 {result.column_count} 列（二进制列自动排除）")
```

**CSV 导出会自动排除以下二进制列**：`image_data`、`video_data`、`image_thumbnail`、`image_preview`，并在日志中记录警告。

### 9.3 直接导出 Arrow 表

不需要经过 Lance 数据集，可以直接导出内存中的 Arrow 表：

```python
from arrow_lake.query.export import ExportBridge

bridge = ExportBridge(storage=None)
table = pa.table({"a": [1, 2, 3], "b": ["x", "y", "z"]})

result = bridge.export_table(table, "./output/direct.parquet", overwrite=True)
```

### 9.4 格式自动检测

根据文件后缀自动选择格式，无需显式指定 `format` 参数：

- `.parquet` → Parquet
- `.csv` → CSV

---

## 10. 版本管理与标签

Lance 数据集每次写入（创建、追加、修改 schema）都会自动递增版本号。

### 10.1 查看版本

```python
storage = lake._get_storage()

# 当前版本
ver = storage.get_version("docs")
print(f"当前版本: v{ver}")  # → v1

# 追加数据后版本递增
storage.append_dataset("docs", new_data)
ver = storage.get_version("docs")
print(f"追加后版本: v{ver}")  # → v2

# 查看所有版本历史
versions = storage.list_versions("docs")
for v in versions:
    print(f"  v{v['version']}: {v['timestamp']}")
```

### 10.2 创建标签

```python
# 给当前版本打标签
storage.create_tag("docs", "release_v1")

# 给指定版本打标签
storage.create_tag("docs", "snapshot_before_fix", version=3)

# 列出所有标签
tags = storage.list_tags("docs")
# → {"release_v1": 2, "snapshot_before_fix": 3}

# 读取标签对应版本的数据
table = storage.read_at_tag("docs", "release_v1")

# 删除标签
storage.delete_tag("docs", "snapshot_before_fix")
```

### 10.3 版本回滚

```python
# 读取旧版本数据，删除后重建
old_data = storage.read_dataset("docs", version=1)
storage.restore_dataset("docs", old_data)
print(f"回滚完成，版本: v{storage.get_version('docs')}")
```

---

## 11. 数据质量检查

### 11.1 运行质量过滤器

```python
result = lake.quality_filter("docs")

# result 是 QualityReport
print(f"总行数: {result.total_rows}")
print(f"通过行数: {result.passed_rows}")
print(f"失败行数: {result.failed_rows}")
```

内置过滤器：
- **TextLengthFilter**: 文本长度检查（`text_min_chars` / `text_max_chars`）
- **ImageResolutionFilter**: 图片分辨率检查（`image_min_width` / `image_min_height`）

配置（`configs/dev.yaml`）：

```yaml
quality:
  enabled: true
  filter_mode: all          # all = 所有过滤器都要通过, any = 任一通过即可
  text_min_chars: 1
  image_min_width: 64
  image_min_height: 64
```

### 11.2 指定激活的过滤器

```python
result = lake.quality_filter("docs", active_filters="TextLengthFilter")
```

---

## 12. 数据血缘

记录数据的来源和变换历史，支持 SQL 查询。

### 12.1 记录血缘事件

```python
# 记录数据创建事件
lake.lineage_record_event(
    "analytics_report",
    operation="create",
    source_datasets=["raw_events", "user_profiles"],
    transform_type="aggregation",
    actor="pipeline_v2",
)

# 记录数据追加事件
lake.lineage_record_event(
    "analytics_report",
    operation="append",
    source_datasets=["daily_events"],
    transform_type="upsert",
)
```

### 12.2 查询血缘历史

```python
# 获取数据集的全部历史事件
history = lake.lineage_history("analytics_report")
for event in history:
    print(f"  [{event.timestamp}] {event.operation}: sources={event.source_datasets}")

# 查询上游依赖（谁提供了数据给 analytics_report）
upstream = lake.lineage_query(
    "SELECT * FROM lineage WHERE source_datasets LIKE '%analytics_report%'"
)

# 查询下游消费（谁依赖了 raw_events）
# （需要使用 LineageQueryBridge.trace_downstream）
from arrow_lake.catalog.lineage import LineageQueryBridge, LineageStore
from arrow_lake.ingest.storage import LanceStorageManager

store = LineageStore(LanceStorageManager("./data"))
bridge = LineageQueryBridge(store)
downstream = bridge.trace_downstream("raw_events")
for event in downstream:
    print(f"  → {event.dataset_name}: {event.operation}")
```

### 12.3 SQL 查询血缘

```python
result = lake.lineage_query(
    "SELECT dataset_name, operation, COUNT(*) as event_count "
    "FROM lineage GROUP BY dataset_name, operation ORDER BY event_count DESC"
)
print(result)
```

---

## 13. 审计日志

### 13.1 记录审计条目

```python
audit_id = lake.audit_record(
    event_type="data_export",
    dataset_name="docs",
    actor="user_alice",
    metaflow_run_id="flow_12345",
    payload={"rows_exported": 1000, "format": "parquet"},
)
print(f"审计 ID: {audit_id}")
```

### 13.2 验证完整性

每个审计条目都带有 HMAC 签名，可以验证是否被篡改。

```python
is_valid = lake.audit_verify(audit_id)
print(f"完整性: {is_valid}")  # True = 未被篡改
```

**重要**：生产环境必须设置 `ARROW_LAKE__AUDIT__HMAC_SECRET_KEY` 环境变量，否则 HMAC 验证无效（密钥为空时只记录警告）。

### 13.3 查询审计记录

```python
# 按数据集过滤
entries = lake.audit_query(dataset_name="docs")

# 按时间范围过滤
entries = lake.audit_query(
    dataset_name="docs",
    start="2026-04-01T00:00:00",
    end="2026-04-15T23:59:59",
)

# 按事件类型过滤
entries = lake.audit_query(event_type="data_export")

# 导出数据集的全部审计记录
export = lake.audit_export("docs")
```

---

## 14. 配置管理

### 14.1 配置层级

Arrow Lake 使用 Pydantic Settings，支持 4 层覆盖（优先级从高到低）：

1. 环境变量
2. `.env` 文件
3. YAML 配置文件
4. 代码默认值

### 14.2 使用 YAML 配置

```python
from arrow_lake import Lake, ArrowLakeConfig

# 从 YAML 文件加载配置
config = ArrowLakeConfig.from_yaml("configs/dev.yaml")

lake = Lake(base_uri="./data", config=config)
```

### 14.3 环境变量覆盖

```bash
# 环境变量格式: ARROW_LAKE__{SECTION}__{KEY}
export ARROW_LAKE__STORAGE__BASE_URI="./data"
export ARROW_LAKE__VECTOR__DEFAULT_TOP_K=20
export ARROW_LAKE__QUALITY__DEDUP_STRATEGY="exact"
```

### 14.4 主要配置项

| Section | Key | 默认值 | 说明 |
|---------|-----|--------|------|
| `vector` | `metric` | cosine | 向量距离度量 |
| `vector` | `num_sub_vectors` | 24 | PQ 子向量数 |
| `vector` | `default_top_k` | 10 | 默认返回结果数 |
| `fts` | `default_top_k` | 10 | 全文搜索默认返回数 |
| `hybrid` | `rrf_k` | 60 | RRF 融合参数 |
| `quality` | `dedup_strategy` | exact | 去重策略 |
| `quality` | `dedup_action` | flag | 去重动作 |
| `olap` | `max_result_rows` | 100000 | OLAP 最大返回行数 |
| `export` | `default_format` | parquet | 默认导出格式 |
| `export` | `parquet_compression` | snappy | Parquet 压缩算法 |

完整配置参考 `configs/dev.yaml`。

---

## 15. CLI 命令行

```bash
# 查看平台状态
arrow-lake status

# 数据摄取
arrow-lake ingest --source data.parquet --dataset my_data

# 导出
arrow-lake export --dataset my_data --format parquet --output result.parquet

# 查看 CLI 帮助
arrow-lake --help
```

---

## 16. HTTP 服务

Arrow Lake 内置轻量 WSGI 服务，提供健康检查和 Prometheus 指标。

### 16.1 启动服务

```bash
# 直接启动
uv run python -m arrow_lake.server --port 8000

# 使用 gunicorn
uv run gunicorn arrow_lake.server:app --bind 0.0.0.0:8000
```

### 16.2 端点

| 端点 | 说明 | 响应 |
|------|------|------|
| `GET /health` | 健康检查 | JSON `{"status": "ok", "storage": "accessible", ...}` |
| `GET /metrics` | Prometheus 指标 | Prometheus 文本格式 |
| 其他路径 | — | 404 Not Found |

```bash
curl http://localhost:8000/health
# {"status":"ok","storage":"accessible","catalog":"available"}

curl http://localhost:8000/metrics
# # HELP arrow_lake_ingestion_rows_total ...
```

### 16.3 环境变量控制

```bash
# 自定义存储路径
export ARROW_LAKE__STORAGE__BASE_URI="/data/lake"

# 禁用 metrics 端点
export ARROW_LAKE__OBSERVABILITY__METRICS_ENABLED=false

# 自定义 metrics 路径
export ARROW_LAKE__OBSERVABILITY__METRICS_PATH="/custom-metrics"
```

---

## 附录：数据模型速查

### Arrow Table 常用列名约定

| 列名 | 类型 | 说明 |
|------|------|------|
| `id` | string / int64 | 主键 |
| `text_content` | string | 文本内容（FTS 索引列） |
| `text_embedding` | fixed_size_list[float] | 文本向量（搜索列） |
| `image_data` | binary | 图片二进制数据（去重列） |
| `image_thumbnail` | binary | 缩略图 |
| `video_data` | binary | 视频二进制数据 |
| `category` | string | 分类字段 |
| `score` | float | 分数 |
| `is_duplicate` | bool | 去重标记列（flag 模式输出） |
| `_distance` | float | 向量搜索距离（搜索结果列） |
| `_score` | float | BM25 相关度（FTS 结果列） |
| `_rrf_score` | float | RRF 融合分（混合搜索结果列） |
