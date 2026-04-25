# 数据摄取指南

> Arrow Lake 支持多种数据源和模态的摄取：本地文件、HTTP 远程下载、图像、视频、PDF 文档，
> 以及直接从 Arrow Table 写入。

***

## 1. 本地文件摄取

支持 CSV、JSON、JSONL、Parquet 四种格式，通过 `lake.ingest()` 统一接口摄入。

```python
from arrow_lake import Lake

lake = Lake(base_uri="./data_lake")

# 摄取多个文件 — 第一个文件创建 dataset，后续自动追加
report = lake.ingest(
    "sales",
    ["docs/cookbook/datas/transactions/sales_2024_cn.csv"],
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

## 2. HTTP 远程摄取

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

## 3. 多模态摄取 — 图像与视频

### 图像摄取

摄取图像时自动生成缩略图、预览图并提取 EXIF 元数据。

```python
from arrow_lake import Lake

lake = Lake(base_uri="./data_lake")

report = lake.ingest_images(
    "photos",
    ["docs/cookbook/datas/photos/sunset_landscape.jpg",
     "docs/cookbook/datas/photos/mountain_view.jpg"],
)
print(f"图像摄取：{report.total_rows} 行")

# 写入的列：image_data, image_thumbnail, image_preview,
#           image_width, image_height, exif_make, exif_model
```

### 视频摄取

摄取视频时自动提取关键帧。

```python
report = lake.ingest_videos(
    "videos",
    ["docs/cookbook/datas/videos/lecture_demo.mp4",
     "docs/cookbook/datas/videos/interview_clip.mp4"],
)
print(f"视频摄取：{report.total_rows} 行")

# 写入的列：video_data (关键帧 JPEG), keyframe_count, video_duration_ms
```

***

## 4. 混合模态摄取

`ingest_mixed()` 将不同模态的数据源统一摄取到同一个 dataset 中。

```python
from arrow_lake import Lake

lake = Lake(base_uri="./data_lake")

# 一次性摄取多种模态 — 写入统一表
report = lake.ingest_mixed(
    "multi_modal_dataset",
    {
        "files": ["docs/cookbook/datas/transactions/sales_2024_cn.csv"],
        "urls": ["https://example.com/extra_data.csv"],
        "images": ["docs/cookbook/datas/photos/sunset_landscape.jpg"],
        "videos": ["docs/cookbook/datas/videos/lecture_demo.mp4"],
    },
)
print(f"混合摄取：{report.total_rows} 行，{report.total_files} 文件")
```

内部流程：`UnifiedTableManager` 创建统一 schema，然后依次调用
`ingest()` → `ingest_http()` → `ingest_images()` → `ingest_videos()`。

***

## 5. PDF 文档摄取

将 PDF 解析为文本块 (chunk) 并写入 Lance dataset，供全文搜索和 RAG 使用。

```python
from arrow_lake import Lake

lake = Lake(base_uri="./data_lake")

# 基础摄取 — Kreuzberg 解析 + 默认分块
report = lake.ingest_documents(
    "research_papers",
    ["docs/cookbook/datas/papers/full_text/p001_attention_is_all_you_need.pdf",
     "docs/cookbook/datas/papers/full_text/p009_clip.pdf"],
    doc_config=None,
)
print(f"文档摄取：{report.total_rows} 个文本块")

# 写入的列：text, page_number, chunk_index, document_id, blob_key
```

### 自定义文档配置

```python
from arrow_lake.config.document import DocumentConfig
from arrow_lake.config._enums import ChunkStrategy

doc_config = DocumentConfig(
    chunk_strategy=ChunkStrategy.SEMANTIC,    # fixed / sentence / semantic
    chunk_size=512,
    chunk_overlap=64,
    chunk_tokenizer="cl100k_base",
    semantic_embedding_model="text-embedding-3-small",
    semantic_similarity_threshold=0.5,
    semantic_min_chunk_size=100,
    pdf_parse_mode="auto",                    # auto / text_only / ocr
    ocr_endpoint="http://localhost:8002",
    max_file_size_mb=100,
    store_raw_pdf=True,
    blob_prefix="documents/",
)

report = lake.ingest_documents("papers", ["docs/cookbook/datas/papers/full_text/zh001_大语言模型知识图谱构建综述.pdf"], doc_config=doc_config)
```

文档摄取流水线：`PDF → Kreuzberg 解析 (+ TurboOCR 回退) → BlobStore (可选) → Chunker 分块 → Lance 持久化`

***

## 6. 死信队列 (Dead Letter Queue)

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

状态流转：`pending` → `retrying` → (成功) `resolved` | (失败) `pending` | `permanent`

***

## 7. Arrow Table 直接写入

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

### 错误处理

```python
from arrow_lake.exceptions import StorageError, TypeError

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

## 8. 数据质量与去重

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

## 9. 摄取最佳实践

```python
from pathlib import Path
from arrow_lake import Lake

lake = Lake(base_uri="./data_lake")

# 使用 glob 收集文件，批量摄取
csv_files = sorted(Path("docs/cookbook/datas/transactions").glob("**/*.csv"))
all_files = [str(f) for f in csv_files]

if all_files:
    report = lake.ingest("sales", all_files)
    print(f"批量摄取完成：{report.total_rows} 行")
```

> **Ingestor 不是线程安全的**。并发摄取到不同 dataset 时请创建独立实例，
> 同一 dataset 的并发写入需要外部同步。
