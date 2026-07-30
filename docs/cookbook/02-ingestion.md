# Data Ingestion Guide

> Arrow Lake supports ingestion from multiple data sources and modalities: local files, remote HTTP
> downloads, SQL databases, Kafka streams, Iceberg/Delta Lake tables, images, videos, PDF documents,
> and direct writes from Arrow Tables.

***

## 1. Local File Ingestion

Four formats are supported — CSV, JSON, JSONL, and Parquet — all through the unified `lake.ingest()` interface.

```python
from arrow_lake import Lake

lake = Lake(base_uri="./data_lake")

# Ingest multiple files — the first file creates the dataset, the rest are appended automatically
report = lake.ingest(
    "sales",
    ["examples/data/transactions/sales_2024.csv"],
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

## 2. Batch Ingestion

Use `ingest_batch()` for optimized bulk loading of same-type files via Daft `write_lance`:

```python
report = lake.ingest_batch(
    "sales",
    ["examples/data/transactions/sales_2024.csv",
     "examples/data/transactions/sales_2025.csv"],
)
print(f"Batch ingestion: {report.total_rows} rows")
```

***

## 3. Remote HTTP Ingestion

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

## 4. SQL Database Ingestion

Ingest data from external SQL databases via JDBC/SQLAlchemy connection URLs:

```python
report = lake.ingest_sql(
    "pg_orders",
    sql="SELECT * FROM orders WHERE year = 2024",
    connection_url="postgresql://user:pass@localhost:5432/mydb",
)
print(f"SQL ingestion: {report.total_rows} rows")
# Note: Requires SQLAlchemy + database driver, e.g.:
#   pip install sqlalchemy psycopg2-binary    # PostgreSQL
#   pip install sqlalchemy pymysql             # MySQL
#   pip install sqlalchemy pyodbc              # SQL Server
```

***

## 5. Kafka Stream Ingestion

Ingest data from Kafka topics in real time:

```python
report = lake.ingest_kafka(
    "clickstream",
    topics=["user_clicks", "page_views"],
    bootstrap_servers="localhost:9092",
    group_id="arrow_lake_ingest",
)
print(f"Kafka ingestion: {report.total_rows} rows")
# Note: Requires confluent-kafka: pip install confluent-kafka
# ingest_kafka() consumes until the consumer reaches the latest offset
# (i.e. catches up), then returns the IngestionReport.
```

***

## 6. Iceberg & Delta Lake Ingestion

Read from Apache Iceberg or Delta Lake tables by table URI:

```python
# Iceberg
report = lake.ingest_iceberg("iceberg_copy", table_uri="s3://warehouse/db.table")
# Note: Requires pyiceberg: pip install pyiceberg[pyarrow,s3fs]

# Delta Lake
report = lake.ingest_deltalake("delta_copy", table_uri="s3://warehouse/delta/table")
# Note: Requires deltalake: pip install deltalake
# For S3 URIs, configure credentials via StorageConfig or environment variables
# (see 03-configuration.md StorageConfig section).
```

***

## 7. Multi-Modal Ingestion — Images & Video

### Image Ingestion

Images are automatically processed to generate thumbnails, previews, and EXIF metadata during ingestion.

```python
from arrow_lake import Lake

lake = Lake(base_uri="./data_lake")

report = lake.ingest_images(
    "photos",
    ["examples/data/photos/sunset_landscape.jpg",
     "examples/data/photos/mountain_view.jpg"],
)
print(f"Image ingestion: {report.total_rows} rows")

# Columns written: image_data, image_thumbnail, image_preview,
#                 image_width, image_height, exif_make, exif_model
```

> **Image-to-image search (v1.9.2)**: after ingesting images, encode a query image to a CLIP/SigLIP vector via
> `POST /embed/image`, then `POST /datasets/{name}/search/vector` for similar images; text-to-image uses SDK
> `lake.encode_text_clip()`.

### Video Ingestion

Videos are automatically processed to extract key frames during ingestion.

```python
report = lake.ingest_videos(
    "videos",
    ["examples/data/videos/lecture_demo.mp4",
     "examples/data/videos/interview_clip.mp4"],
)
print(f"Video ingestion: {report.total_rows} rows")

# Columns written: video_data (keyframe JPEGs), keyframe_count, video_duration_ms
```

***

## 8. Mixed-Modal Ingestion

Use `ingest_mixed()` to combine different modalities into a single dataset in one call.

```python
from arrow_lake import Lake

lake = Lake(base_uri="./data_lake")

# Ingest multiple modalities at once — writes to a unified table
report = lake.ingest_mixed(
    "multi_modal_dataset",
    {
        "files": ["examples/data/transactions/sales_2024.csv"],
        "urls": ["https://example.com/extra_data.csv"],
        "images": ["examples/data/photos/sunset_landscape.jpg"],
        "videos": ["examples/data/videos/lecture_demo.mp4"],
    },
)
print(f"Mixed ingestion: {report.total_rows} rows, {report.total_files} files")
```

Internally, `UnifiedTableManager` creates a unified schema, then calls
`ingest()` → `ingest_http()` → `ingest_images()` → `ingest_videos()` in sequence.

***

## 9. Document Ingestion (PDF / Word / Markdown / HTML / Email … 17 types)

Documents are parsed into text chunks and written to a Lance dataset, ready for full-text search and RAG. The `/ingest/documents` REST endpoint accepts 17 document types (PDF/DOCX/PPTX/XLSX/MD/HTML/TXT/EPUB/email/images, etc.) and supports `append=true` to append to an existing dataset (incremental).

```python
from arrow_lake import Lake

lake = Lake(base_uri="./data_lake")

# Basic ingestion — Kreuzberg parser + default chunking
report = lake.ingest_documents(
    "research_papers",
    ["examples/data/papers/full_text/p001_attention_is_all_you_need.pdf",
     "examples/data/papers/full_text/p009_clip.pdf"],
    doc_config=None,
)
print(f"Document ingestion: {report.total_rows} text chunks")

# Columns written: text_content, page_number, chunk_index, document_id, blob_key, doc_type
```

### Custom Document Configuration

```python
from arrow_lake.config import DocumentConfig
from arrow_lake.config import ChunkStrategy, PdfParseMode

doc_config = DocumentConfig(
    chunk_strategy=ChunkStrategy.RECURSIVE,     # page / paragraph / recursive / semchunk
                                                 # / chonkie_token / chonkie_semantic / chonkie_sdpm
                                                 # / docling_hybrid (Docling HybridChunker, token-level)
    chunk_size=512,
    chunk_overlap=64,
    chunk_tokenizer="",                         # Tokenizer for semchunk (empty = char-based)
    semantic_embedding_model="",                 # HuggingFace model for chonkie semantic/sdpm
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
    "papers",
    ["examples/data/papers/full_text/p014_gpt4_technical_report.pdf"],
    doc_config=doc_config,
)
```

Document ingestion pipeline: `PDF/Office/HTML → parse (Kreuzberg / TurboOCR / Docling) → BlobStore (optional) → Chunker → Lance persistence`

> **SDK vs REST indexing gap (v1.9.5, common pitfall)**: the SDK `lake.ingest_documents()` only chunks + stores;
> it does **not** build retrieval indexes. The REST `POST /ingest/documents` path runs `ingest_documents_and_index`
> (parse → store → embed → FTS → vector end-to-end), auto-building IVF_PQ when rows ≥256 and skipping with a warning
> otherwise (vector brute-force still works). SDK users who need retrieval must call `lake.create_vector_index()` +
> `lake.create_fts_index()` afterward, or use `lake.ingest_documents_and_index()`.

> **Ingest-as-governance**: field comments are captured at ingest time (v1.9.3; editable via
> `POST /datasets/{name}/schema/annotate`); every write records lineage via `_lineage_after_ingest` and threads
> the authenticated `actor` (v1.9.4).

***

## 10. Dead Letter Queue

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

Status transitions: `pending` -> `retrying` -> (success) `resolved` | (failure) `pending` | `permanent`

***

## 11. Direct Arrow Table Writes

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

### Upsert, Delete, Update

```python
# Upsert — merge rows by key column
lake.upsert("documents", updated_table, on="id")

# Delete rows matching a condition
lake.delete_rows("documents", where="category = 'expired'")

# Update specific columns on matching rows
lake.update_rows("documents", where="id = 'doc_0001'", updates={"category": "reviewed"})
```

### Export

```python
from arrow_lake import Lake

lake = Lake(base_uri="./data_lake")

# Export a dataset to Parquet, CSV, or another Lance URI
result = lake.export_to("documents", target_uri="s3://backup/documents")
print(f"Exported {result.row_count} rows to {result.target_uri}")
```

### Error Handling

```python
from arrow_lake.exceptions import StorageError, ValidationError

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

## 12. Embedding and Ingest

Compute vector embeddings and ingest in one step:

```python
# Ingest data and compute embeddings for a text column
report = lake.ingest_and_embed(
    "articles",
    ["examples/data/articles.json"],
    embed_column="text_content",
)
print(f"Embedded ingestion: {report.total_rows} rows with vectors")
```

Or add embeddings to an existing dataset:

```python
# Embed texts and add vectors to an existing dataset
lake.embed_and_add(
    "documents",
    texts=["New document text to embed"],
    ids=["doc_0200"],
    metadata=[{"source": "api"}],
)
```

***

## 13. Data Quality and Deduplication

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

## 14. Ingestion Best Practices

```python
from pathlib import Path
from arrow_lake import Lake

lake = Lake(base_uri="./data_lake")

# Use glob to collect files and batch-ingest them
csv_files = sorted(Path("examples/data/transactions").glob("**/*.csv"))
all_files = [str(f) for f in csv_files]

if all_files:
    report = lake.ingest("sales", all_files)
    print(f"Batch ingestion complete: {report.total_rows} rows")
```

> **The Ingestor is not thread-safe.** When ingesting into different datasets concurrently,
> create separate Lake instances. Concurrent writes to the same dataset require external synchronization.

***

## Ingestion API Quick Reference

| Method                  | Purpose                                        |
| ----------------------- | ---------------------------------------------- |
| `ingest()`              | Ingest local files (CSV, JSON, JSONL, Parquet) |
| `ingest_batch()`        | Optimized bulk ingestion of same-type files    |
| `ingest_http()`         | Download and ingest from HTTP(S) URLs          |
| `ingest_sql()`          | Ingest from SQL databases                      |
| `ingest_kafka()`        | Ingest from Kafka topics                       |
| `ingest_iceberg()`      | Read from Apache Iceberg tables                |
| `ingest_deltalake()`    | Read from Delta Lake tables                    |
| `ingest_images()`       | Ingest images with thumbnails and EXIF         |
| `ingest_videos()`       | Ingest videos with keyframe extraction         |
| `ingest_mixed()`        | Combine multiple modalities in one call        |
| `ingest_documents()`    | Parse and chunk PDF documents                  |
| `ingest_and_embed()`    | Ingest data and compute embeddings             |
| `embed_and_add()`       | Add embeddings to an existing dataset          |
| `create_dataset()`      | Create dataset from a PyArrow Table            |
| `append_dataset()`      | Append rows from a PyArrow Table               |
| `upsert()`              | Merge rows by key column                       |
| `delete_rows()`         | Delete rows matching a condition               |
| `update_rows()`         | Update columns on matching rows                |
| `export_to()`           | Export dataset to external storage              |
| `quality_filter()`      | Run quality filters on a dataset               |
| `deduplicate()`         | Detect and handle duplicate content            |
