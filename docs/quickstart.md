# Arrow Lake — Quickstart

## Installation

```bash
uv sync
```

## Generate Seed Data

```bash
uv run python data/seed/generate_seed_data.py
```

This creates:
- `data/seed/users.parquet` — 1000 rows (id, name, age, department, city, salary)
- `data/seed/documents.jsonl` — 500 rows (id, title, category, language, source, word_count, created_at)

## Usage

### Create & Query Datasets

```python
from pathlib import Path
import pyarrow as pa
from arrow_lake.ingest.storage import LanceStorageManager

manager = LanceStorageManager(base_uri="./data/lake")

# Create a dataset
manager.create_dataset("users", pa.table({"id": [1, 2], "name": ["Alice", "Bob"]}))

# Append data
manager.append_dataset("users", pa.table({"id": [3], "name": ["Carol"]}))

# Read latest version
data = manager.read_dataset("users")
print(data.num_rows)  # 3

# List all datasets
print(manager.list_datasets())  # ["users"]
```

### Versioning & Time Travel

```python
# Check current version
print(manager.get_version("users"))  # 2

# List all versions with metadata
for v in manager.list_versions("users"):
    print(f"  v{v['version']} @ {v['timestamp']}")

# Read a specific version
v1_data = manager.read_dataset("users", version=1)
print(v1_data.num_rows)  # 2
```

### Named Tags

```python
# Tag a version
manager.create_tag("users", "v1", version=1)

# Read at tag
tagged = manager.read_at_tag("users", "v1")

# List tags
print(manager.list_tags("users"))  # {"v1": 1}

# Delete a tag
manager.delete_tag("users", "v1")
```

### Version Diff

```python
from arrow_lake.ingest.diff import VersionDiffer

differ = VersionDiffer(manager)
diff = differ.diff("users", 1, 2)

print(f"Added: {diff.added_rows}, Removed: {diff.removed_rows}")
print(f"Schema changes: {diff.schema_changes}")
```

### Schema Migration

```python
# Add a column (SQL expression)
manager.add_column("users", "score", "CAST(0 AS DOUBLE)")

# Change column type
manager.alter_column("users", "score", pa.float64())

# Drop a column
manager.drop_column("users", "score")
```

### File Ingestion

```python
from arrow_lake.ingest.ingestor import Ingestor

ingestor = Ingestor(manager)

# Ingest from local files
report = ingestor.ingest("users", [
    "data/seed/users.parquet",
    "data/seed/documents.jsonl",
])
print(f"Ingested {report.total_rows} rows from {report.total_files} files")
```

### Connectors

```python
from arrow_lake.ingest.connectors import LocalConnector, S3Connector

# Local filesystem
local = LocalConnector(base_path="./data/seed")
result = local.list_files(extensions=[".csv", ".parquet"])
print(result.paths)

# S3 / MinIO
s3 = S3Connector(bucket="my-bucket", prefix="data/", endpoint_url="http://localhost:9000")
result = s3.list_files(extensions=[".parquet"])
```

### Catalog (Ray Actor)

```python
import ray
from arrow_lake.catalog.actor import CatalogActor

ray.init()
handle = CatalogActor.options(name="catalog").remote()

# Register a dataset
ray.get(handle.register_table.remote(
    "users",
    '{"id": "int64", "name": "string"}',
    "./data/lake",
))

# List registered tables
tables = ray.get(handle.list_tables.remote())
for t in tables:
    print(t["name"])
```

### Compaction

```python
stats = manager.compact("users")
print(f"Compacted: v{stats.version_before} -> v{stats.version_after}")
```

### Dataset Lifecycle

```python
# Archive (hide from default list)
ray.get(handle.archive_dataset.remote("users"))

# Restore
ray.get(handle.restore_dataset.remote("users"))

# Delete with cascade (removes Lance data + catalog entry)
ray.get(handle.delete_table.remote("users", cascade=True, base_uri="./data/lake"))
```

### Quality Filtering

```python
from arrow_lake import Lake

lake = Lake(base_uri="./data/lake")
report = lake.quality_filter("documents", active_filters="text_length")
print(f"Passed: {report.passed}/{report.total}")  # Passed: 480/500
print(report.to_json())  # {"total_rows": 500, "passed_rows": 480, ...}
```

### Built-in Filters

```python
import pyarrow as pa
from arrow_lake.quality.builtin import TextLengthFilter, ImageResolutionFilter

table = pa.table({"text_content": ["hello", "world", ""]})
text_filter = TextLengthFilter(min_chars=1, max_chars=100)
passed, rejected = text_filter.filter(table)
print(passed.num_rows)    # 2
print(rejected.num_rows)  # 1 (empty string rejected)

img_table = pa.table({"image_width": [64, 128], "image_height": [64, 128]})
img_filter = ImageResolutionFilter(min_width=128, min_height=128)
passed, rejected = img_filter.filter(img_table)
print(rejected.num_rows)  # 1 (64x64 below threshold)
```

### Custom Filter Registration

```python
from arrow_lake.quality.base import QualityFilterRegistry
from arrow_lake.quality.builtin import TextLengthFilter, ImageResolutionFilter

registry = QualityFilterRegistry()
registry.register(TextLengthFilter(min_chars=5))
registry.register(ImageResolutionFilter(min_width=64, min_height=64))

# AND mode (default): rows must pass ALL filters
report = registry.apply_all(table, active_filters="text_length,image_resolution")
print(registry.list_filters())  # ["image_resolution", "text_length"]

# OR mode: rows must pass ANY filter
report_or = registry.apply_all(table, active_filters="text_length,image_resolution", mode="any")
```

### Schema Validation Gate

```python
from arrow_lake.quality.schema_validation import SchemaValidationGate
import pyarrow as pa

schema = pa.schema([("id", pa.int64()), ("text", pa.string())])

# Lenient mode: drop unknown columns, safe-cast types
gate = SchemaValidationGate(mode="lenient")
rows = [{"id": 1, "text": "hello", "extra": "dropped"}]
valid, rejected = gate.validate(rows, schema)
print(len(valid))  # 1 (extra column dropped)

# Strict mode: reject unknown columns and type mismatches
gate_strict = SchemaValidationGate(mode="strict")
valid, rejected = gate_strict.validate(rows, schema)
print(len(rejected))  # 1 (unknown column "extra")
```

### Quality Scoring

```python
from arrow_lake.quality.scoring import compute_quality_scores

# score_column defaults to "quality_score"
scored = compute_quality_scores(table, report, rejected_table=rejected)
scores = scored.column("quality_score").to_pylist()
# Passed rows: 1.0, Rejected rows: max(0.0, 1.0 - 0.2 * num_filters)

# Custom column name
scored = compute_quality_scores(table, report, score_column="data_quality")
```

### Dead-Letter Handling

```python
from arrow_lake.quality.dead_letter import DeadLetterWriter

writer = DeadLetterWriter(storage=manager)
written = writer.write(
    "documents",
    rejected_table=rejected,
    filter_name="text_length",
    parent_version="v3",
)
# Writes to "documents_dead_letter" with extra columns:
# _rejection_reason, _filter_name, _parent_version, _rejected_at
```

## Data Testing Assertions

```python
from arrow_lake.testing import (
    assert_table_has_schema,
    assert_row_count,
    assert_column_values_unique,
    assert_column_within_range,
    assert_dataset_version,
)

data = manager.read_dataset("users")

assert_row_count(data, expected=1000)
assert_column_values_unique(data, "id")
assert_column_within_range(data, "age", min_val=22, max_val=65)
```

## Running Tests

```bash
# Unit tests
uv run pytest tests/unit/ -q

# Integration tests (requires Ray)
uv run pytest tests/integration/ -q

# All tests with coverage
uv run pytest tests/ --cov=arrow_lake --cov-report=term-missing -q

# Lint + format + typecheck
uv run ruff check arrow_lake/ tests/
uv run ruff format --check arrow_lake/ tests/
uv run mypy arrow_lake/
```

## Tech Stack

| Component | Version |
|-----------|---------|
| Python | 3.11 |
| LanceDB | 0.30.2 |
| PyArrow | 23.0.1 |
| Daft | 0.7.8 |
| Ray | 2.54.1 |
| DuckDB | 1.5.1 |
| Pydantic | 2.12.5 |
