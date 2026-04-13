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
