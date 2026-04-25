# Data Quality & Deduplication

> Use Arrow Lake's quality filtering and content deduplication pipelines to ensure the integrity
> and uniqueness of ingested data.

***

## 1. Quality Filters

`quality_filter()` runs all registered quality filters against a dataset and returns an
aggregated report.

```python
from arrow_lake import Lake

lake = Lake(base_uri="./data")

# Run all registered filters (AND mode)
report = lake.quality_filter("articles", mode="all")
print(f"Passed: {report.passed}, Rejected: {report.rejected}")

# Example output:
# Passed: 9420, Rejected: 580

# View the overall pass rate
rate = report.overall_pass_rate()       # 94.2
print(f"Pass rate: {rate:.1f}%")

# View per-filter breakdown
for detail in report.per_filter_breakdown():
    print(f"  {detail['filter_name']}: "
          f"passed {detail['passed_count']}, "
          f"rejected {detail['rejected_count']}")

# Export to JSON (compatible with Metaflow Cards)
payload = report.to_json()
# {"total_rows": 10000, "passed_rows": 9420, ...}
```

### Parameter Reference

| Parameter        | Type  | Default           | Description                                         |
| ---------------- | ----- | ----------------- | --------------------------------------------------- |
| `dataset_name`   | `str` | (required)        | Dataset name                                        |
| `active_filters` | `str` | value from config | Comma-separated filter names; empty string runs all |
| `mode`           | `str` | `"all"`           | `"all"` = AND (all filters must pass), `"any"` = OR |

`QualityReport` is a frozen dataclass with the following fields:

* `total`: Total number of input rows
* `passed`: Number of rows that passed all filters
* `rejected`: Number of rows rejected by at least one filter
* `filter_results`: Tuple of `FilterResult` objects, one per filter
* `schema_rejected`: Number of rows rejected by schema validation
* `duration_seconds`: Time spent on quality filtering (seconds)

***

## 2. Built-in Filters

Arrow Lake registers two built-in filters by default. You can adjust their thresholds in the
`quality` section of `configs/dev.yaml`:

### TextLengthFilter

Removes rows whose text content does not meet length requirements. Expects the dataset to contain
a `text_content` column.

```yaml
# configs/dev.yaml
quality:
  text_min_chars: 1           # Minimum character count
  text_max_chars: null        # Maximum character count (null = no limit)
```

```python
# Run only the text length filter
report = lake.quality_filter(
    "articles",
    active_filters="text_length",
    mode="all",
)
```

### ImageResolutionFilter

Removes rows whose image resolution falls below a threshold. Expects a column named `image_data`
containing decodable image bytes.

```yaml
# configs/dev.yaml
quality:
  image_min_width: 64         # Minimum width in pixels
  image_min_height: 64        # Minimum height in pixels
```

```python
# Run only the image resolution filter
report = lake.quality_filter(
    "photos",
    active_filters="image_resolution",
    mode="all",
)
```

Before running any filters, schema validation is performed first: `lenient` (default, silently
drops unknown columns) or `strict` (rejects unknown columns and type mismatches).

***

## 3. Exact Deduplication (SHA-256)

`deduplicate()` uses SHA-256 hashing for exact deduplication by default, suitable for both text
and binary data.

```python
# Exact deduplication: remove identical rows
result = lake.deduplicate("articles", strategy="exact", action="remove")
print(f"Unique: {result.unique_rows}, Duplicates: {result.duplicates_found}")
print(f"Total input: {result.total_rows}")
print(f"Strategy: {result.strategy}, Action: {result.action}")

# Example output:
# Unique: 8750, Duplicates: 1250
# Total input: 10000
# Strategy: exact, Action: remove
```

### The `action` Parameter

| Value      | Description                                                                   |
| ---------- | ----------------------------------------------------------------------------- |
| `"flag"`   | Keep all rows; add a boolean `is_duplicate` column to mark duplicates         |
| `"remove"` | Remove duplicate rows from the result table and return the deduplicated table |

`DedupResult` is a frozen dataclass with a `table` field for direct access to the results:

```python
# flag mode: inspect the markers
result = lake.deduplicate("articles", strategy="exact", action="flag")
dup_table = result.table
print(dup_table.column("is_duplicate").to_pylist())
# [False, False, True, False, True, ...]
```

***

## 4. Perceptual Deduplication (pHash)

For image datasets, perceptual hashing (pHash) detects visually similar images that are not
byte-for-byte identical.

```python
# Perceptual deduplication: based on pHash Hamming distance
result = lake.deduplicate(
    "photos",
    strategy="perceptual",
    perceptual_threshold=10,
    action="remove",
)

# Use both exact and perceptual deduplication together
result = lake.deduplicate(
    "photos",
    strategy="both",
    perceptual_threshold=10,
    action="flag",
)
```

### pHash Parameters

* `strategy`: `"exact"` / `"perceptual"` / `"both"`
* `perceptual_threshold`: Hamming distance threshold. Lower values are stricter:
  * `0`: Identical images
  * `5`: Minor crop / compression variants
  * `10`: (default) Noticeable scaling / watermarks / color shifts
  * `20`: Lenient mode, allows larger visual differences

Under the hood, this uses the `imagehash` library to compute `phash` and then compares Hamming
distances.

***

## 5. NeMo Curator Integration (GPU-Accelerated MinHash LSH)

For large-scale text deduplication, Arrow Lake supports GPU-accelerated approximate deduplication
via NeMo Curator's MinHash LSH implementation.

```python
from arrow_lake.quality.nemo_curator import NeMoDeduplicator

deduper = NeMoDeduplicator(
    ngram_size=5,          # Characters per n-gram
    num_hashes=128,        # Number of MinHash hash functions
    threshold=0.8,         # Jaccard similarity threshold
    text_column="text_content",
)

# Automatically uses MinHash LSH when a GPU is available; falls back to SHA-256 otherwise
unique_table, dup_table = deduper.deduplicate(table)
print(f"GPU accelerated: {deduper.using_gpu}")

# NeMo Curator quality scoring
from arrow_lake.quality.nemo_curator import NeMoCuratorFilter

scorer = NeMoCuratorFilter(
    classifiers=("quality",),         # Enabled classifiers
    threshold=0.5,                    # Quality threshold
    batch_size=64,
)
passed_table, rejected_table = scorer.filter(table)
```

Enable GPU deduplication in the YAML configuration:

```yaml
# configs/dev.yaml
quality:
  nemo_curator_enabled: true
  nemo_curator_model: "nemo/quality-scorer"
  nemo_curator_threshold: 0.5
  nemo_curator_batch_size: 64
```

For Docker deployments with GPU support, use the GPU overlay:

```bash
docker compose -f deploy/docker-compose.yml \
              -f deploy/docker-compose.gpu.yml up -d
```

***

## 6. Dead Letter Queue

Rows rejected by quality filtering or schema validation are routed to a Dead Letter Queue (DLQ),
which supports retrying, resolving, and purging failed records.

```python
from arrow_lake.ingest.dead_letter import IngestDeadLetterQueue

dlq = IngestDeadLetterQueue(base_dir="./data")

# View queue statistics
print(dlq.stats)
# {"pending": 12, "resolved": 3, "permanent": 1, "total": 16}

# List pending failed items
items = dlq.list_items(status="pending")
for item in items:
    print(f"{item.file_path}: {item.last_error}")

# Retry a record (increments attempt_count)
success = dlq.retry("s3://raw/broken_doc.pdf")
# True if the item exists and can_retry is True

# Manually mark as resolved
dlq.resolve("s3://raw/broken_doc.pdf")

# Mark as permanently failed (no further retries)
dlq.mark_permanent("s3://raw/corrupt.bin", reason="Corrupted file header, cannot be repaired")

# Filter by dataset
dataset_items = dlq.list_items(dataset="articles")

# Purge resolved and permanently failed records
removed = dlq.purge(resolved=True, permanent=True)
print(f"Purged {removed} records")
```

State transitions: `pending` -> `retrying` -> `pending` (on failure) / `resolved` (on fix);
or directly `pending` -> `permanent`.

Each `DeadLetterItem` contains fields for `file_path`, `error`, `dataset`, `attempt_count`,
`status`, and timestamps.

***

## 7. Quality Configuration Reference

Complete quality filtering and deduplication configuration (maps to `QualityConfig`):

```yaml
quality:
  enabled: true
  filter_mode: all                    # all = AND, any = OR
  active_filters: ""                  # Empty = use all registered filters
  schema_validation: lenient          # lenient | strict

  # Built-in filter thresholds
  text_min_chars: 1
  text_max_chars: null
  image_min_width: 64
  image_min_height: 64

  # NeMo Curator GPU quality scoring
  nemo_curator_enabled: false
  nemo_curator_model: "nemo/quality-scorer"
  nemo_curator_threshold: 0.5
  nemo_curator_batch_size: 64

  # Content deduplication
  dedup_enabled: true
  dedup_strategy: exact               # exact | perceptual | both
  dedup_action: flag                  # flag | remove
  dedup_perceptual_threshold: 10

  # Dead letter queue
  dead_letter_enabled: true
```

***

## 8. Best Practices

### Choosing the Right Deduplication Strategy

| Scenario                            | Recommended Strategy           | Notes                                                                   |
| ----------------------------------- | ------------------------------ | ----------------------------------------------------------------------- |
| Articles / News / Code              | `exact`                        | Identical content produces the same SHA-256 — precise and efficient     |
| Product image dedup                 | `perceptual` (threshold 5-10)  | Filters the same product shown at different sizes or compression levels |
| Social media images                 | `perceptual` (threshold 10-15) | Allows for filter and crop differences                                  |
| Large-scale text corpora (>1M rows) | `NeMoDeduplicator` (GPU)       | MinHash LSH approximate deduplication, far faster than exact matching   |
| Mixed datasets (text + images)      | `both`                         | Runs exact deduplication first, then perceptual — a two-stage pipeline  |

### Typical Data Quality Pipeline

```python
from arrow_lake import Lake

lake = Lake.from_yaml("configs/dev.yaml")

# Step 1: Quality filtering
report = lake.quality_filter("articles")
print(f"Quality pass rate: {report.overall_pass_rate():.1f}%")

# Step 2: Deduplication (flag mode — review before deleting)
result = lake.deduplicate("articles", strategy="exact", action="flag")
print(f"Found {result.duplicates_found} duplicates")

# Step 3: After review, confirm deletion
if result.duplicates_found > 0:
    confirmed = lake.deduplicate("articles", strategy="exact", action="remove")
    print(f"Deduplication complete, retained {confirmed.unique_rows} rows")

# Step 4: Check the dead letter queue
from arrow_lake.ingest.dead_letter import IngestDeadLetterQueue
dlq = IngestDeadLetterQueue()
print(f"Failed items: {dlq.stats}")
```

### Performance Tips

* Quality filtering and deduplication perform full-table scans — schedule them during off-peak hours
* For datasets larger than 1 million rows, enable NeMo Curator GPU acceleration
* The `perceptual` strategy requires decoding image bytes to compute pHash, which is CPU-intensive
* Setting `text_max_chars` can filter out overly long text early, reducing downstream processing load
