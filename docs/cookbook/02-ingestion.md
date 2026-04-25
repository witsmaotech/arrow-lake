# Data Ingestion Guide

> Arrow Lake supports ingestion from multiple data sources and modalities: local files, remote HTTP
> downloads, images, videos, PDF documents, and direct writes from Arrow Tables.

***

## 1. Local File Ingestion

Four formats are supported — CSV, JSON, JSONL, and Parquet — all through the unified `lake.ingest()` interface.

```python
from arrow_lake import Lake

lake = Lake(base_uri="./data_lake")

# Ingest multiple files — the first file creates the dataset, the rest are appended automatically
report = lake.ingest(
    "sales",
    ["docs/cookbook/datas/transactions/sales_2024.csv"],
)

# IngestionReport contains detailed statistics
print(f"Ingestion complete: {report.total_rows} rows, {report.total_files} files")
for src in report.sources:
    print(f"  {src.path}: {src.row_count} rows")
```

| Format  | Extension  | Notes                                      |
| ------- | ---------- | ------------------------------------------ |
| CSV     | `.csv`     | Standard comma-separated, parsed by Daft   |
| JSON    | `.json`    | JSON array format                          |
| JSONL   | `.jsonl`   | JSON Lines (one JSON object per line)      |
| Parquet | `.parquet` | Columnar storage, ideal for large datasets |

> When ingesting multiple files, the first file determines the dataset schema.
> Subsequent files must have a compatible column subset and matching types.

***

## 2. Remote HTTP Ingestion

Download files from HTTP(S) URLs and write them directly into a Lance dataset — no manual download needed.

```python
from arrow_lake import Lake

lake = Lake(base_uri="./data_lake")

# Ingest from remote URLs — file format is auto-detected
report = lake.ingest_http(
    "external_data",
    [
        "https://example.com/dataset/sales.csv",
        "https://example.com/dataset/inventory.json",
    ],
)
print(f"Remote ingestion: {report.total_rows} rows, {report.total_files} files")
```

Built-in safety mechanisms include: SSRF protection (blocks private IPs), HTTP/HTTPS protocol enforcement,
tenacity-based exponential backoff with auto-retry on 429/5xx errors, and configurable timeouts.

***

## 3. Multi-Modal Ingestion — Images & Video

### Image Ingestion

Images are automatically processed to generate thumbnails, previews, and EXIF metadata during ingestion.

```python
from arrow_lake import Lake

lake = Lake(base_uri="./data_lake")

report = lake.ingest_images(
    "photos",
    ["docs/cookbook/datas/photos/sunset_landscape.jpg",
     "docs/cookbook/datas/photos/mountain_view.jpg"],
)
print(f"Image ingestion: {report.total_rows} rows")

# Columns written: image_data, image_thumbnail, image_preview,
#                 image_width, image_height, exif_make, exif_model
```

### Video Ingestion

Videos are automatically processed to extract key frames during ingestion.

```python
report = lake.ingest_videos(
    "videos",
    ["docs/cookbook/datas/videos/lecture_demo.mp4",
     "docs/cookbook/datas/videos/interview_clip.mp4"],
)
print(f"Video ingestion: {report.total_rows} rows")

# Columns written: video_data (keyframe JPEGs), keyframe_count, video_duration_ms
```

***

## 4. Mixed-Modal Ingestion

Use `ingest_mixed()` to combine different modalities into a single dataset in one call.

```python
from arrow_lake import Lake

lake = Lake(base_uri="./data_lake")

# Ingest multiple modalities at once — writes to a unified table
report = lake.ingest_mixed(
    "multi_modal_dataset",
    {
        "files": ["docs/cookbook/datas/transactions/sales_2024.csv"],
        "urls": ["https://example.com/extra_data.csv"],
        "images": ["docs/cookbook/datas/photos/sunset_landscape.jpg"],
        "videos": ["docs/cookbook/datas/videos/lecture_demo.mp4"],
    },
)
print(f"Mixed ingestion: {report.total_rows} rows, {report.total_files} files")
```

Internally, `UnifiedTableManager` creates a unified schema, then calls
`ingest()` → `ingest_http()` → `ingest_images()` → `ingest_videos()` in sequence.

***

## 5. PDF Document Ingestion

PDFs are parsed into text chunks and written to a Lance dataset, ready for full-text search and RAG.

```python
from arrow_lake import Lake

lake = Lake(base_uri="./data_lake")

# Basic ingestion — Kreuzberg parser + default chunking
report = lake.ingest_documents(
    "research_papers",
    ["docs/cookbook/datas/papers/full_text/p001_attention_is_all_you_need.pdf",
     "docs/cookbook/datas/papers/full_text/p009_clip.pdf"],
    doc_config=None,
)
print(f"Document ingestion: {report.total_rows} text chunks")

# Columns written: text, page_number, chunk_index, document_id, blob_key
```

### Custom Document Configuration

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

report = lake.ingest_documents("papers", ["docs/cookbook/datas/papers/full_text/p014_gpt4_technical_report.pdf"], doc_config=doc_config)
```

Document ingestion pipeline: `PDF → Kreuzberg parse (+ TurboOCR fallback) → BlobStore (optional) → Chunker → Lance persistence`

***

## 6. Dead Letter Queue

Failed ingestion files are recorded in `IngestDeadLetterQueue`, with support for retry, resolution, and cleanup.

```python
from arrow_lake.ingest.dead_letter import IngestDeadLetterQueue

dlq = IngestDeadLetterQueue(base_dir="./data_lake")

# View statistics
print(dlq.stats)  # {"pending": 3, "resolved": 1, "total": 4}

# List failed items
for item in dlq.list_items(status="pending"):
    print(f"  {item.file_path}: {item.error} (attempt {item.attempt_count})")

# Retry a failed ingestion
dlq.retry("data/broken.csv")

# Manually mark as resolved
dlq.resolve("data/broken.csv")

# Mark as permanently failed
dlq.mark_permanent("data/corrupted.parquet", reason="Corrupted file header")

# Purge resolved and permanently failed items
removed = dlq.purge(resolved=True, permanent=True)
print(f"Purged {removed} records")
```

Status transitions: `pending` → `retrying` → (success) `resolved` | (failure) `pending` | `permanent`

***

## 7. Direct Arrow Table Writes

For programmatic data, you can create or append datasets directly from PyArrow Tables.

```python
from arrow_lake import Lake
import pyarrow as pa
import numpy as np

lake = Lake(base_uri="./data_lake")

# Create a dataset with a vector column
n, dim = 100, 128
vectors = np.random.randn(n, dim).astype(np.float32)

table = pa.table({
    "id": [f"doc_{i:04d}" for i in range(n)],
    "text_content": [f"sample text {i}" for i in range(n)],
    "category": ["ml", "nlp", "cv", "rl"] * 25,
    "text_embedding": pa.FixedSizeListArray.from_arrays(vectors.ravel(), dim),
})
lake.create_dataset("documents", table)

# Append data — schema must match
new_table = pa.table({
    "id": [f"doc_{i:04d}" for i in range(100, 150)],
    "text_content": [f"new text {i}" for i in range(100, 150)],
    "category": ["ml", "nlp", "cv", "rl"] * 12 + ["ml"],
    "text_embedding": pa.FixedSizeListArray.from_arrays(
        np.random.randn(50, dim).astype(np.float32).ravel(), dim
    ),
})
lake.append_dataset("documents", new_table)
```

### Error Handling

```python
from arrow_lake.exceptions import StorageError, TypeError

try:
    lake.create_dataset("existing", data)
except StorageError:
    pass  # Dataset already exists or name is invalid (must match ^[a-zA-Z_][a-zA-Z0-9_-]*$)

try:
    lake.append_dataset("nonexistent", data)
except StorageError:
    pass  # Dataset does not exist or schema mismatch
```

***

## 8. Data Quality and Deduplication

```python
from arrow_lake import Lake

lake = Lake(base_uri="./data_lake")

# Run quality filters
quality_report = lake.quality_filter("documents")
print(f"Passed: {quality_report.passed_count}, Rejected: {quality_report.rejected_count}")

# Specify active filters (AND mode)
report = lake.quality_filter("documents", active_filters="text_length", mode="all")

# Content deduplication
dedup_result = lake.deduplicate(
    "documents",
    strategy="both",        # "exact" | "perceptual" | "both"
    action="flag",          # "flag" | "remove"
    perceptual_threshold=10,  # pHash Hamming distance threshold
)
```

***

## 9. Ingestion Best Practices

```python
from pathlib import Path
from arrow_lake import Lake

lake = Lake(base_uri="./data_lake")

# Use glob to collect files and batch-ingest them
csv_files = sorted(Path("docs/cookbook/datas/transactions").glob("**/*.csv"))
all_files = [str(f) for f in csv_files]

if all_files:
    report = lake.ingest("sales", all_files)
    print(f"Batch ingestion complete: {report.total_rows} rows")
```

> **The Ingestor is not thread-safe.** When ingesting into different datasets concurrently,
> create separate Lake instances. Concurrent writes to the same dataset require external synchronization.
