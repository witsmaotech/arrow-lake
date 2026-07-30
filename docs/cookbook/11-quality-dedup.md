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

> **Dependency note**: Perceptual deduplication requires `imagehash`. Install with `pip install arrow-lake[dedup]`.

***

## 5. NeMo Curator Integration (GPU-Accelerated MinHash LSH)

For large-scale text deduplication, Arrow Lake supports GPU-accelerated approximate deduplication
via NeMo Curator's MinHash LSH implementation.

> **Dependency note**: NeMo Curator integration requires `nemo-curator`. Install with `pip install arrow-lake[nemo-curator]`.

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
  dedup_enabled: false                # default false (config/media.py:124) — dedup is opt-in
  dedup_strategy: exact               # exact | perceptual | both (YAML rejects minhash, see §12)
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
| Mid-scale text near-dups (rewrites / light edits) | `minhash` (CPU, programmatic, see §12) | Jaccard-similarity near-dups, no GPU needed                  |
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

---

## 9. Quality Rules Engine (v1.4.0)

The declarative `QualityRuleEngine` replaces hard-coded filters with configurable rules
loaded from JSON, YAML, or the REST API.

### 9.1 Programmatic Usage

```python
from arrow_lake.quality.rules import QualityRuleEngine, RuleDefinition
import pyarrow as pa

# Create table with mixed quality data
table = pa.table({
    "text_content": ["good article", "hi", "another good one", "x"],
    "score": [0.9, 0.1, 0.85, 0.05],
})

# Configure rules
engine = QualityRuleEngine()
engine.add_rule(RuleDefinition(
    name="reject_short_text",
    column="text_content",
    check="length",
    params={"min": 3},
    action="reject",
    message="Text too short (min={min} chars)",
))
engine.add_rule(RuleDefinition(
    name="flag_low_score",
    column="score",
    check="range",
    params={"min": 0.5},
    action="flag",
))
engine.add_rule(RuleDefinition(
    name="dedup_content",
    column="text_content",
    check="duplicate",
    action="remove",
))

# Evaluate without modifying data
results = engine.evaluate(table)
for r in results:
    print(f"{r.rule_name}: {r.affected_count} rows ({r.action}) — {r.message}")

# Apply: removes reject/remove rows, keeps flag rows
filtered, results = engine.apply(table)
print(f"Original: {table.num_rows} rows → Filtered: {filtered.num_rows} rows")
```

### 9.2 Check Types

| Check | Parameters | Description |
|-------|-----------|-------------|
| `length` | `min`, `max` | String length bounds |
| `range` | `min`, `max` | Numeric value bounds |
| `regex` | `pattern`, `invert` | Regex match (invert=True matches non-matching) |
| `duplicate` | — | Exact hash duplicate detection |

### 9.3 Action Types

| Action | Effect in `evaluate()` | Effect in `apply()` |
|--------|----------------------|---------------------|
| `reject` | Reports violation count | Removes violating rows |
| `remove` | Reports violation count | Removes violating rows (same as reject) |
| `flag` | Reports violation count | Keeps rows (informational only) |

### 9.4 Load from JSON

```json
{
  "rules": [
    {"name": "min_text", "column": "text_content", "check": "length", "params": {"min": 10}, "action": "reject"},
    {"name": "valid_score", "column": "score", "check": "range", "params": {"min": 0.0, "max": 1.0}, "action": "flag"},
    {"name": "email_format", "column": "email", "check": "regex", "params": {"pattern": "^.+@.+$", "invert": true}, "action": "reject"},
    {"name": "dedup", "column": "text_content", "check": "duplicate", "action": "remove"}
  ]
}
```

```python
engine = QualityRuleEngine()
engine.load_from_json("rules.json")
```

### 9.5 REST API

```bash
# Apply rules via API
curl -X POST http://localhost:8000/api/v1/datasets/articles/quality/rules \
  -H "X-API-Key: your-key" \
  -H "Content-Type: application/json" \
  -d '{
    "rules": [
      {"name": "min_len", "column": "text_content", "check": "length", "params": {"min": 10}, "action": "reject"},
      {"name": "no_dupes", "column": "text_content", "check": "duplicate", "action": "remove"}
    ]
  }'
```

---

## 10. Row/Column Access Control (v1.4.0)

Row and column-level ACL restrict what data each role can see in query and search results.

### 10.1 Setting ACL Rules

```bash
# Viewer can only see "title" and "summary" columns
curl -X PUT http://localhost:8000/api/v1/admin/acl/articles \
  -H "X-API-Key: admin-key" \
  -H "Content-Type: application/json" \
  -d '{"role": "viewer", "visible_columns": ["title", "summary"]}'

# Viewer can only see rows where region == US
curl -X PUT http://localhost:8000/api/v1/admin/acl/sales \
  -H "X-API-Key: admin-key" \
  -H "Content-Type: application/json" \
  -d '{"role": "viewer", "row_filter": "region == US"}'

# Combined: column pruning + row filtering
curl -X PUT http://localhost:8000/api/v1/admin/acl/hr_data \
  -H "X-API-Key: admin-key" \
  -H "Content-Type: application/json" \
  -d '{"role": "viewer", "visible_columns": ["name", "department"], "row_filter": "department == Engineering"}'
```

### 10.2 Listing and Deleting ACL

```bash
# List all ACLs for a dataset
curl http://localhost:8000/api/v1/admin/acl/articles -H "X-API-Key: admin-key"

# Delete an ACL
curl -X DELETE http://localhost:8000/api/v1/admin/acl/articles/viewer -H "X-API-Key: admin-key"
```

### 10.3 How It Works

- **Column pruning**: Invisible columns are removed from query/search results before response serialization
- **Row filtering**: Simple `column op value` expressions (`==`, `!=`, `<`, `<=`, `>`, `>=`) filter rows from results
- **Admin bypass**: The `admin` role (via Role enum since v1.5.2) always sees all data, regardless of ACL configuration
- **No ACL = no filtering**: If no ACL is configured for a role+dataset, results pass through unchanged
- **Applied automatically**: All query (OLAP/metadata/Daft) and search (vector/FTS/hybrid/faceted/ensemble) endpoints apply ACL

***

## 11. Gravitino Tags & Policies (v1.4.1)

Arrow Lake integrates with **Apache Gravitino** for metadata-driven data governance. The
`GravitinoTagService` and `GravitinoPolicyService` provide data classification, retention management,
and column masking — all managed through the REST API or programmatically.

### 11.1 GravitinoTagService — Data Classification

The `GravitinoTagService` wraps the Gravitino Tag API for classifying tables and columns. It degrades
gracefully when Gravitino is unavailable (returns empty lists instead of errors).

```python
from arrow_lake.quality.gravitino_tags import GravitinoTagService

tag_svc = GravitinoTagService(config.gravitino)

# Predefined tag constants
print(GravitinoTagService.SENSITIVE)   # "sensitive"
print(GravitinoTagService.PII)         # "pii"
print(GravitinoTagService.FINANCIAL)   # "financial"
print(GravitinoTagService.EXPIRES_30D) # "expires:30d"

# Create a custom tag
tag_svc.create_tag("internal_only", comment="Internal use only — not for external sharing")

# Tag a table
tag_svc.tag_table("hr_data", ["sensitive", "pii"])

# Tag a specific column
tag_svc.tag_column("hr_data", "ssn", ["pii"])

# List tags for a table
tags = tag_svc.list_tags("hr_data")
print(tags)  # ["sensitive", "pii"]

# Find all tables with a given tag
tables = tag_svc.get_tables_by_tag("pii")
print(tables)  # ["hr_data", "customer_records"]
```

#### Predefined Tags

| Constant              | Value           | Purpose                                          |
| --------------------- | --------------- | ------------------------------------------------ |
| `SENSITIVE`           | `"sensitive"`   | General sensitive data marker                    |
| `PII`                 | `"pii"`         | Personally identifiable information              |
| `FINANCIAL`           | `"financial"`   | Financial or payment-related data                |
| `EXPIRES_30D`         | `"expires:30d"` | Data that should be purged after 30 days         |

### 11.2 GravitinoPolicyService — Retention & Masking

The `GravitinoPolicyService` manages retention and masking policies for automated data lifecycle
governance.

```python
from arrow_lake.quality.gravitino_policies import GravitinoPolicyService

policy_svc = GravitinoPolicyService(config.gravitino)

# Create a retention policy — data retained for 90 days
policy_svc.create_retention_policy("log_retention", days=90)

# Create a masking policy — redact specified columns
policy_svc.create_masking_policy("email_mask", columns=["email", "phone"])

# Apply a policy to a table
policy_svc.apply_policy("email_mask", "customer_data")

# List all policies
policies = policy_svc.list_policies()
print(policies)  # ["log_retention", "email_mask"]
```

### 11.3 REST API for Tag & Policy Management

Tags and policies can also be managed through the `/api/v1/metadata/*` REST endpoints. All endpoints
require the `X-API-Key` header and return 503 when Gravitino is not configured.

```bash
# --- Tags ---

# List tags (optionally filtered by table)
curl "http://localhost:8000/api/v1/metadata/tags?table=articles" \
  -H "X-API-Key: your-key"
# => {"success": true, "data": [{"name": "sensitive"}], "error": null, "metadata": {"total": 1}}

# Create a tag (JSON body)
curl -X POST http://localhost:8000/api/v1/metadata/tags \
  -H "X-API-Key: your-key" -H "Content-Type: application/json" \
  -d '{"name": "pii", "comment": "PII data"}'
# => {"success": true, "data": {"name": "pii"}, "error": null, "metadata": {}}

# --- Policies ---

# List all policies
curl http://localhost:8000/api/v1/metadata/policies \
  -H "X-API-Key: your-key"
# => {"success": true, "data": [{"name": "log_retention"}], "error": null, "metadata": {"total": 1}}

# Create a retention policy (JSON body)
curl -X POST http://localhost:8000/api/v1/metadata/policies/retention \
  -H "X-API-Key: your-key" -H "Content-Type: application/json" \
  -d '{"name": "log_retention", "days": 90}'
# => {"success": true, "data": {"name": "log_retention", "days": 90}, "error": null, "metadata": {}}

# Create a masking policy (JSON body; function required: redact|hash|partial|nullify)
curl -X POST http://localhost:8000/api/v1/metadata/policies/masking \
  -H "X-API-Key: your-key" -H "Content-Type: application/json" \
  -d '{"name": "email_mask", "columns": ["email"], "function": "partial"}'
# => {"success": true, "data": {"name": "email_mask", "columns": ["email"], "function": "partial"}, "error": null, "metadata": {}}
```

### 11.4 Enabling Gravitino

```yaml
# config.yaml
gravitino:
  enabled: true
  uri: "http://localhost:8090"        # Gravitino server URI
  metalake: "arrow_lake"              # Metalake name
  lance_rest_enabled: true            # Enable Lance REST Catalog
  lance_rest_uri: "http://localhost:8888"
  sync_interval_seconds: 300          # Background catalog sync interval
```

When `gravitino.enabled` is `false` (the default), all `/api/v1/metadata/*` endpoints return 503 and
the `GravitinoTagService`/`GravitinoPolicyService` constructors complete silently without
connecting. Existing quality filtering, deduplication, and ACL features are unaffected.

***

## 12. MinHash Near-Duplicate Dedup (CPU datasketch)

In addition to exact SHA-256 and perceptual pHash, `ContentDeduplicator` ships **MinHash LSH**
near-duplicate dedup (`quality/dedup.py:70,91-96,132`), backed by the CPU `datasketch` package. It
detects **semantic near-duplicates** (rewrites, lightly-edited variants) without a GPU.

```python
from arrow_lake.quality.dedup import ContentDeduplicator

deduper = ContentDeduplicator(
    strategy="minhash",          # MinHash LSH near-duplicate dedup
    action="flag",
    text_column="text_content",  # required when strategy="minhash" (dedup.py:95-96)
    ngram_size=5,                # character n-gram shingle size
    num_hashes=128,              # number of MinHash permutations (num_perm)
    threshold=0.8,               # Jaccard similarity threshold (0.0–1.0) for near-duplicate
)
result = deduper.deduplicate(table)
# DedupResult(strategy="minhash", ...)
```

> **Trap (YAML rejects minhash)**: the `QualityConfig.dedup_strategy` validator only allows
> `exact`/`perceptual`/`both` (`config/media.py:129-134`); writing `minhash` raises a
> `ValidationError`. MinHash is **programmatic only** — call
> `ContentDeduplicator(strategy="minhash", ...)` directly; you cannot set `dedup_strategy: minhash`.
> For large corpora (>1M rows) needing a GPU, use the `NeMoDeduplicator` from §5.

| Parameter | Default | Notes |
|-----------|---------|-------|
| `strategy` | `exact` | `minhash` takes an isolated path (`dedup.py:132`) |
| `text_column` | (required) | text column to MinHash |
| `ngram_size` | `5` | character n-gram |
| `num_hashes` | `128` | MinHash permutations (precision/cost trade-off) |
| `threshold` | `0.8` | Jaccard similarity threshold |

***

## 13. Quality Profiling & Scoring (QualityProfiler)

`QualityProfiler` (`quality/profiler.py:39`) produces a holistic dataset quality profile as a
`DatasetQualityProfile`, including an `overall_quality_score` (0.0–1.0, `profiler.py:34`) and
per-dimension statistics. The matching REST endpoint is
`GET /api/v1/datasets/{name}/quality/profile` (`routers/quality.py:156`).

```python
from arrow_lake.quality.profiler import QualityProfiler

profiler = QualityProfiler()
profile = profiler.profile(table, dataset_name="articles")
print(profile.overall_quality_score)   # 0.0–1.0
# DatasetQualityProfile also carries null rate, cardinality, distribution, etc.
```

```bash
# REST: fetch the dataset quality profile
curl http://localhost:8000/api/v1/datasets/articles/quality/profile -H "X-API-Key: your-key"
```

***

## 14. Quality REST API Panorama

Endpoints exposed by `routers/quality.py` (prefix `/api/v1/datasets/{name}/quality`):

| Method | Endpoint | Description | Source |
|--------|----------|-------------|--------|
| `POST` | `/quality/filter` | Run quality filters, return aggregated report | quality.py:46 |
| `GET` | `/quality/report` | Fetch the last quality-filter report | quality.py:64 |
| `POST` | `/quality/deduplicate` | Dedup a dataset (exact/perceptual/both) | quality.py:81 |
| `POST` | `/quality/rules` | Apply the declarative rule engine (see §9.5) | quality.py:105 |
| `GET` | `/quality/profile` | Quality profile & score (see §13) | quality.py:156 |
| `POST` | `/quality/llm_label` | LLM enrichment: tag rows (**async 202**) | quality.py:213 |
| `POST` | `/quality/extract` | LLM enrichment: extract structured fields (**async 202**) | quality.py:252 |
| `POST` | `/quality/mask-preview` | Preview masking (function/columns) | quality.py:291 |

`llm_label` and `extract` are fire-and-forget async tasks (`status_code=202`): they return a `task_id`
immediately, and you poll `GET /api/v1/tasks/{task_id}/status` for the result (see the TaskManager
chapter in `14-workflow-orchestration.md`).

```bash
# Dedup (REST)
curl -X POST http://localhost:8000/api/v1/datasets/articles/quality/deduplicate \
  -H "X-API-Key: your-key" -H "Content-Type: application/json" \
  -d '{"strategy": "exact", "action": "flag"}'

# mask-preview (function required: redact|hash|partial|nullify)
curl -X POST http://localhost:8000/api/v1/datasets/hr_data/quality/mask-preview \
  -H "X-API-Key: your-key" -H "Content-Type: application/json" \
  -d '{"columns": ["ssn", "email"], "function": "partial"}'
```

***

## 15. Data Preparation: Cleaning & LLM Enrichment

### 15.1 Structured Cleaning `POST /clean`

`POST /api/v1/datasets/{name}/clean` (`routers/cleaning.py:222`) compiles declarative cleaning steps
(DuckDB semantics) into SQL, then writes the result back to Lance via `restore_dataset`. Column-wise
chained operators are supported.

```bash
curl -X POST http://localhost:8000/api/v1/datasets/sales/clean \
  -H "X-API-Key: your-key" -H "Content-Type: application/json" \
  -d '{"steps": [
        {"column": "revenue", "op": "fill_null", "value": 0},
        {"column": "region",  "op": "trim"},
        {"column": "email",   "op": "lowercase"}
      ]}'
```

### 15.2 LLM Enrichment (llm_label / extract, async)

`quality/llm_enrich.py:105,156` provides two LLM enrichment operations, both async (202 + task-id poll):

- **`llm_label`** (quality.py:213): runs an LLM classifier over each row's text, adding a label column
  (sentiment, topic, intent, ...).
- **`extract`** (quality.py:252): extracts structured fields (entities, key-value pairs) from
  unstructured text into new columns.

```bash
# Async LLM labeling (returns a task_id immediately)
curl -X POST http://localhost:8000/api/v1/datasets/articles/quality/llm_label \
  -H "X-API-Key: your-key" -H "Content-Type: application/json" \
  -d '{"text_column": "text_content", "label_column": "sentiment", "prompt": "positive|negative|neutral"}'
# => 202 {"task_id": "...", "status": "pending"}

# Poll for the result
curl http://localhost:8000/api/v1/tasks/<task_id>/status -H "X-API-Key: your-key"
```

### 15.3 Field Comments (v1.9.3)

`ingest/field_comments.py` supports attaching human-readable comments to dataset fields (reads
parquet/CSV sidecars via PyArrow, writes into the Lance schema `comment` metadata). The matching
endpoint is `POST /api/v1/datasets/{name}/schema/annotate`.

```bash
# Annotate a field
curl -X POST http://localhost:8000/api/v1/datasets/articles/schema/annotate \
  -H "X-API-Key: your-key" -H "Content-Type: application/json" \
  -d '{"field": "text_content", "comment": "Article body (cleaned, HTML stripped)"}'

# View the annotated schema
curl http://localhost:8000/api/v1/datasets/articles/schema -H "X-API-Key: your-key"
```

Field comments persist in the Lance schema as `SchemaField.comment`; they are captured automatically
by the `_write_table` hook on ingest and echoed back by `GET /schema`.
