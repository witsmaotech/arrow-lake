# 数据摄取指南

> Arrow Lake 支持多种数据源和模态的摄取：本地文件、HTTP 远程下载、SQL 数据库、Kafka 流、
> Iceberg/Delta Lake 表、图像、视频、PDF 文档，以及直接从 Arrow Table 写入。

***

## 1. 本地文件摄取

支持 CSV、JSON、JSONL、Parquet 四种格式，通过 `lake.ingest()` 统一接口摄入。

```python
from arrow_lake import Lake

lake = Lake(base_uri="./data_lake")

# 摄取多个文件 — 第一个文件创建 dataset，后续自动追加
report = lake.ingest(
    "ontime",
    ["datas/ontime/ontime_2022.parquet"],
)

# IngestionReport 包含详细的摄取统计
print(f"摄取完成：{report.total_rows} 行，{report.total_files} 文件")
for src in report.sources:
    print(f"  {src.path}: {src.row_count} 行")
```

| 格式      | 扩展名        | 说明                        |
| ------- | ---------- | ------------------------- |
| CSV     | `.csv`     | 标准逗号分隔，Daft 解析            |
| JSON    | `.json`    | JSON 数组格式                 |
| JSONL   | `.jsonl`   | JSON Lines (每行一个 JSON 对象) |
| Parquet | `.parquet` | 列式存储，适合大数据量               |

> 多文件摄取时，第一个文件决定 dataset schema，后续文件列必须是子集或类型兼容。

***

## 2. 批量摄取

使用 `ingest_batch()` 通过 Daft `write_lance` 优化加载同类型文件：

```python
report = lake.ingest_batch(
    "ontime",
    ["datas/ontime/ontime_2022.parquet"],
)
# 批量摄入适合多个同构文件（如按年份切分的 ontime_2018.parquet、ontime_2019.parquet 等），
# 第一个文件决定 schema，后续自动追加。
print(f"批量摄取：{report.total_rows} 行")
```

***

## 3. HTTP 远程摄取

从 HTTP(S) URL 下载文件并直接写入 Lance dataset，无需手动下载。

```python
from arrow_lake import Lake

lake = Lake(base_uri="./data_lake")

# 从远程 URL 摄取 — 自动检测文件格式
report = lake.ingest_http(
    "external_data",
    [
        "https://example.com/dataset/sales.csv",
        "https://example.com/dataset/inventory.json",
    ],
)
print(f"远程摄取：{report.total_rows} 行，{report.total_files} 文件")
```

内置安全机制：SSRF 防护 (阻止私有 IP)、仅允许 http/https 协议、
tenacity 指数退避自动重试 (429/5xx)、可配置超时。

***

## 4. SQL 数据库摄取

通过 JDBC/SQLAlchemy 连接 URL 从外部 SQL 数据库摄取数据：

```python
report = lake.ingest_sql(
    "pg_orders",
    sql="SELECT * FROM orders WHERE year = 2024",
    connection_url="postgresql://user:pass@localhost:5432/mydb",
)
print(f"SQL 摄取：{report.total_rows} 行")
# 注意：需要 SQLAlchemy + 数据库驱动，例如：
#   pip install sqlalchemy psycopg2-binary    # PostgreSQL
#   pip install sqlalchemy pymysql             # MySQL
#   pip install sqlalchemy pyodbc              # SQL Server
```

***

## 5. Kafka 流摄取

实时从 Kafka 主题摄取数据：

```python
report = lake.ingest_kafka(
    "clickstream",
    topics=["user_clicks", "page_views"],
    bootstrap_servers="localhost:9092",
    group_id="arrow_lake_ingest",
)
print(f"Kafka 摄取：{report.total_rows} 行")
# 注意：需要 confluent-kafka：pip install confluent-kafka
# ingest_kafka() 持续消费直到 consumer 到达最新 offset（追平），然后返回 IngestionReport。
```

***

## 6. Iceberg 与 Delta Lake 摄取

通过表 URI 读取 Apache Iceberg 或 Delta Lake 表：

```python
# Iceberg
report = lake.ingest_iceberg("iceberg_copy", table_uri="s3://warehouse/db.table")
# 注意：需要 pyiceberg：pip install pyiceberg[pyarrow,s3fs]

# Delta Lake
report = lake.ingest_deltalake("delta_copy", table_uri="s3://warehouse/delta/table")
# 注意：需要 deltalake：pip install deltalake
# S3 URI 需要通过 StorageConfig 或环境变量配置凭证
# (参见 03-configuration-zh.md StorageConfig 章节)。
```

***

## 7. 多模态摄取 — 图像与视频

### 图像摄取

摄取图像时自动生成缩略图、预览图并提取 EXIF 元数据。

```python
from arrow_lake import Lake

lake = Lake(base_uri="./data_lake")

report = lake.ingest_images(
    "photos",
    ["datas/photos/sunset_landscape.jpg",
     "datas/photos/mountain_view.jpg"],
)
print(f"图像摄取：{report.total_rows} 行")

# 写入的列：image_data, image_thumbnail, image_preview,
#           image_width, image_height, exif_make, exif_model
```

> **以图搜图（v1.9.2）**：图像摄入后，用 `POST /embed/image` 把查询图编码为 CLIP/SigLIP 向量，
> 再 `POST /datasets/{name}/search/vector` 检索相似图像；文搜图用 SDK `lake.encode_text_clip()`。

### 视频摄取

摄取视频时自动提取关键帧。

```python
report = lake.ingest_videos(
    "videos",
    ["datas/videos/lecture_demo.mp4",
     "datas/videos/interview_clip.mp4"],
)
print(f"视频摄取：{report.total_rows} 行")

# 写入的列：video_data (关键帧 JPEG), keyframe_count, video_duration_ms
```

***

## 8. 混合模态摄取

`ingest_mixed()` 将不同模态的数据源统一摄取到同一个 dataset 中。

```python
from arrow_lake import Lake

lake = Lake(base_uri="./data_lake")

# 一次性摄取多种模态 — 写入统一表
report = lake.ingest_mixed(
    "multi_modal_dataset",
    {
        "files": ["datas/ontime/ontime_2022.parquet"],
        "urls": ["https://example.com/extra_data.csv"],
        "images": ["datas/photos/sunset_landscape.jpg"],
        "videos": ["datas/videos/lecture_demo.mp4"],
    },
)
print(f"混合摄取：{report.total_rows} 行，{report.total_files} 文件")
```

内部流程：`UnifiedTableManager` 创建统一 schema，然后依次调用
`ingest()` -> `ingest_http()` -> `ingest_images()` -> `ingest_videos()`。

***

## 9. 文档摄取（PDF / Word / Markdown / HTML / 邮件 … 17 种）

将文档解析为文本块 (chunk) 并写入 Lance dataset，供全文搜索和 RAG 使用。`/ingest/documents`
REST 端点已放开 17 种文档类型（PDF/DOCX/PPTX/XLSX/MD/HTML/TXT/EPUB/邮件/图片等），且支持
`append=true` 追加到已存数据集（增量）。

```python
from arrow_lake import Lake

lake = Lake(base_uri="./data_lake")

# 基础摄取 — Kreuzberg 解析 + 默认分块
report = lake.ingest_documents(
    "aigc_report",
    ["datas/reports/aigc_industry_report.pdf"],
    doc_config=None,
)
print(f"文档摄取：{report.total_rows} 个文本块")

# 写入的列：text_content, page_number, chunk_index, document_id, blob_key, doc_type
```

### 自定义文档配置

```python
from arrow_lake.config import DocumentConfig
from arrow_lake.config import ChunkStrategy, PdfParseMode

doc_config = DocumentConfig(
    chunk_strategy=ChunkStrategy.RECURSIVE,     # page / paragraph / recursive / semchunk
                                                 # / chonkie_token / chonkie_semantic / chonkie_sdpm
                                                 # / docling_hybrid（Docling HybridChunker，token 级）
    chunk_size=512,
    chunk_overlap=64,
    chunk_tokenizer="",                         # semchunk 分词器（空 = 字符级）
    semantic_embedding_model="",                 # chonkie semantic/sdpm 用的 HuggingFace 模型
    semantic_similarity_threshold=0.5,
    semantic_min_chunk_size=100,
    pdf_parse_mode=PdfParseMode.AUTO,           # auto / text / ocr
    ocr_backend="kreuzberg",                    # kreuzberg / turbo_ocr / docling
    ocr_endpoint="http://localhost:8002",
    max_file_size_mb=100,
    store_raw_pdf=True,
    blob_prefix="documents/",
)

report = lake.ingest_documents(
    "aigc_report",
    ["datas/reports/aigc_industry_report.pdf"],
    doc_config=doc_config,
)
```

文档摄取流水线：`PDF/Office/HTML → 解析 (Kreuzberg / TurboOCR / Docling) → BlobStore (可选) → Chunker 分块 → Lance 持久化`

> **SDK 与 REST 的建索引差异（v1.9.5，高频踩坑）**：SDK `lake.ingest_documents()` 只分块 + 存储，
> **不建检索索引**；REST `POST /ingest/documents` 走 `ingest_documents_and_index`
> （parse → store → embed → FTS → vector 一条龙），行数 ≥256 自动建 IVF_PQ，<256 跳过并告警
> （vector 仍可暴力搜索）。SDK 用户若要检索，摄入后需手动 `lake.create_vector_index()` +
> `lake.create_fts_index()`，或改用 `lake.ingest_documents_and_index()`。

> **摄取即治理**：摄入时自动捕获字段注释（v1.9.3，可经 `POST /datasets/{name}/schema/annotate`
> 编辑）；所有写入经 `_lineage_after_ingest` 记录血缘并透传认证 `actor`（v1.9.4）。

***

## 10. 死信队列 (Dead Letter Queue)

摄取失败的文件记录到 `IngestDeadLetterQueue`，支持重试、解决和清理。

```python
from arrow_lake.ingest.dead_letter import IngestDeadLetterQueue

dlq = IngestDeadLetterQueue(base_dir="./data_lake")

# 查看统计
print(dlq.stats)  # {"pending": 3, "resolved": 1, "total": 4}

# 列出失败项
for item in dlq.list_items(status="pending"):
    print(f"  {item.file_path}: {item.error} (尝试 {item.attempt_count} 次)")

# 重试失败的摄取
dlq.retry("data/broken.csv")

# 手动标记为已解决
dlq.resolve("data/broken.csv")

# 标记为永久失败
dlq.mark_permanent("data/corrupted.parquet", reason="文件头损坏")

# 清理已解决和永久失败的项目
removed = dlq.purge(resolved=True, permanent=True)
print(f"已清理 {removed} 条记录")
```

状态流转：`pending` -> `retrying` -> (成功) `resolved` | (失败) `pending` | `permanent`

***

## 11. Arrow Table 直接写入

对于程序化数据，可以直接从 PyArrow Table 创建或追加 dataset。

```python
from arrow_lake import Lake
import pyarrow as pa
import numpy as np

lake = Lake(base_uri="./data_lake")

# 创建带向量列的 dataset
n, dim = 100, 128
vectors = np.random.randn(n, dim).astype(np.float32)

table = pa.table({
    "id": [f"doc_{i:04d}" for i in range(n)],
    "text_content": [f"示例文本 {i}" for i in range(n)],
    "category": ["ml", "nlp", "cv", "rl"] * 25,
    "text_embedding": pa.FixedSizeListArray.from_arrays(vectors.ravel(), dim),
})
lake.create_dataset("documents", table)

# 追加数据 — schema 必须匹配
new_table = pa.table({
    "id": [f"doc_{i:04d}" for i in range(100, 150)],
    "text_content": [f"新文本 {i}" for i in range(100, 150)],
    "category": ["ml", "nlp", "cv", "rl"] * 12 + ["ml"],
    "text_embedding": pa.FixedSizeListArray.from_arrays(
        np.random.randn(50, dim).astype(np.float32).ravel(), dim
    ),
})
lake.append_dataset("documents", new_table)
```

### Upsert、删除、更新

```python
# Upsert — 按键列合并行
lake.upsert("documents", updated_table, on="id")

# 按条件删除行
lake.delete_rows("documents", where="category = 'expired'")

# 更新匹配行的指定列
lake.update_rows("documents", where="id = 'doc_0001'", updates={"category": "reviewed"})
```

### 导出

```python
from arrow_lake import Lake

lake = Lake(base_uri="./data_lake")

# 导出 dataset 到 Parquet、CSV 或其他 Lance URI
result = lake.export_to("documents", target_uri="s3://backup/documents")
print(f"已导出 {result.row_count} 行到 {result.target_uri}")
```

### 错误处理

```python
from arrow_lake.exceptions import StorageError, ValidationError

try:
    lake.create_dataset("existing", data)
except StorageError:
    pass  # dataset 已存在或名称无效 (须匹配 ^[a-zA-Z_][a-zA-Z0-9_-]*$)

try:
    lake.append_dataset("nonexistent", data)
except StorageError:
    pass  # dataset 不存在或 schema 不匹配
```

***

## 12. 嵌入与摄取

一步完成向量嵌入计算和摄取：

```python
# 摄取数据并计算文本列的嵌入
report = lake.ingest_and_embed(
    "articles",
    ["datas/articles.json"],
    embed_column="text_content",
)
print(f"嵌入摄取：{report.total_rows} 行含向量")
```

或向已有 dataset 添加嵌入：

```python
# 嵌入文本并将向量添加到已有 dataset
lake.embed_and_add(
    "documents",
    texts=["新文档文本内容"],
    ids=["doc_0200"],
    metadata=[{"source": "api"}],
)
```

***

## 13. 数据质量与去重

```python
from arrow_lake import Lake

lake = Lake(base_uri="./data_lake")

# 运行质量过滤器
quality_report = lake.quality_filter("documents")
print(f"通过：{quality_report.passed_count}, 拒绝：{quality_report.rejected_count}")

# 指定活跃过滤器 (AND 模式)
report = lake.quality_filter("documents", active_filters="text_length", mode="all")

# 内容去重
dedup_result = lake.deduplicate(
    "documents",
    strategy="both",        # "exact" | "perceptual" | "both"
    action="flag",          # "flag" | "remove"
    perceptual_threshold=10,  # pHash 汉明距离阈值
)
```

***

## 14. 摄取最佳实践

```python
from pathlib import Path
from arrow_lake import Lake

lake = Lake(base_uri="./data_lake")

# 使用 glob 收集文件，批量摄取
pq_files = sorted(Path("datas/ontime").glob("**/*.parquet"))
all_files = [str(f) for f in pq_files]

if all_files:
    report = lake.ingest("ontime", all_files)
    print(f"批量摄取完成：{report.total_rows} 行")
```

> **Ingestor 不是线程安全的**。并发摄取到不同 dataset 时请创建独立实例，
> 同一 dataset 的并发写入需要外部同步。

***

## 摄取 API 速查表

| 方法                    | 用途                          |
| --------------------- | --------------------------- |
| `ingest()`            | 摄取本地文件 (CSV, JSON, JSONL, Parquet) |
| `ingest_batch()`      | 优化的同类型文件批量摄取                |
| `ingest_http()`       | 从 HTTP(S) URL 下载并摄取         |
| `ingest_sql()`        | 从 SQL 数据库摄取                 |
| `ingest_kafka()`      | 从 Kafka 主题摄取                |
| `ingest_iceberg()`    | 读取 Apache Iceberg 表         |
| `ingest_deltalake()`  | 读取 Delta Lake 表             |
| `ingest_images()`     | 摄取图像 (缩略图 + EXIF)           |
| `ingest_videos()`     | 摄取视频 (关键帧提取)                |
| `ingest_mixed()`      | 一次调用组合多种模态                  |
| `ingest_documents()`  | 解析和分块 PDF 文档                |
| `ingest_and_embed()`  | 摄取数据并计算嵌入                   |
| `embed_and_add()`     | 向已有 dataset 添加嵌入            |
| `create_dataset()`    | 从 PyArrow Table 创建 dataset  |
| `append_dataset()`    | 从 PyArrow Table 追加行          |
| `upsert()`            | 按键列合并行                      |
| `delete_rows()`       | 按条件删除行                      |
| `update_rows()`       | 更新匹配行的指定列                   |
| `export_to()`         | 导出 dataset 到外部存储            |
| `quality_filter()`    | 对 dataset 运行质量过滤器           |
| `deduplicate()`       | 检测和处理重复内容                   |
