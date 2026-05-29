# Data Plane

> You are a **data engineer** who moves, stores, transforms, and governs structured and unstructured data across the lake.

## Data Flow

```
Ingest (CLI / SDK / REST API)
  --> Validate & Quality Gate
    --> Store (Lance columnar format)
      --> Index (Vector / FTS / Scalar)
        --> Query (DuckDB SQL / Daft DataFrame)
          --> Export or Serve downstream
```

**15 ingestion sources**: local files, HTTP URLs, SQL databases, Kafka, Iceberg, Delta Lake, images (EXIF + thumbnails), videos (keyframe extraction), PDF documents (OCR), and mixed-modality batches.

**4 storage backends**: `local` (filesystem), `minio`, `s3`, `gcs`. Configured via `ARROW_LAKE__STORAGE__BACKEND`.

---

## Ingestion

### CLI

```bash
# Create a dataset from a local file
arrow-lake ingest create my_dataset --data data.csv

# Ingest files into an existing dataset
arrow-lake ingest files my_dataset ./docs/*.pdf ./data.parquet

# Ingest from HTTP URLs
arrow-lake ingest http my_dataset https://example.com/data.jsonl

# Multimodal: images, videos, documents
arrow-lake ingest images my_dataset ./photos/*.jpg
arrow-lake ingest videos my_dataset ./clips/*.mp4
arrow-lake ingest documents my_dataset ./papers/*.pdf

# Append, upsert, delete, update
arrow-lake ingest append my_dataset --data new_data.parquet
arrow-lake ingest upsert my_dataset --data updates.csv --on id
arrow-lake ingest delete-rows my_dataset --where "category = 'deprecated'"
arrow-lake ingest update-rows my_dataset --where "id = 'doc_001'" --set '{"status": "archived"}'
```

### SDK

```python
from arrow_lake import Lake
import pyarrow as pa

lake = Lake(base_uri="./data/lake")

# Create dataset from PyArrow table
table = pa.table({"id": ["doc_001"], "text": ["Hello world"], "category": ["greeting"]})
lake.create_dataset("my_dataset", table)

# Ingest files (auto-detects format)
report = lake.ingest("my_dataset", ["data.csv", "data.jsonl"])
print(f"Ingested {report.total_rows} rows")

# Ingest from SQL database
lake.ingest_sql("my_dataset", sql="SELECT * FROM users",
                connection_url="postgresql://user:pass@host/db")

# Ingest from Kafka
lake.ingest_kafka("my_dataset", bootstrap_servers="localhost:9092",
                  topics=["events"])

# Ingest from Iceberg / Delta Lake
lake.ingest_iceberg("my_dataset", table_uri="s3://bucket/warehouse/db.table")
lake.ingest_deltalake("my_dataset", table_uri="s3://bucket/delta/table")

# Upsert on key column
lake.upsert("my_dataset", new_data, on="id")

# Ingest + embed in one call
lake.ingest_and_embed("my_dataset", ["docs.pdf"],
                      text_column="text_content",
                      embedding_column="text_embedding")
```

### REST API

```bash
# Upload files to MinIO, then ingest
curl -X POST http://localhost:8000/api/v1/datasets/my_dataset/upload \
  -H "X-API-Key: $KEY" -F "files=@data.csv"

curl -X POST http://localhost:8000/api/v1/datasets/my_dataset/ingest \
  -H "X-API-Key: $KEY" -H "Content-Type: application/json" \
  -d '{"blob_keys": ["data.csv"]}'

# Ingest from external sources
curl -X POST .../ingest/sql   -d '{"sql": "SELECT ...", "connection_url": "..."}'
curl -X POST .../ingest/kafka -d '{"bootstrap_servers": "...", "topics": ["events"]}'
curl -X POST .../ingest/http  -d '{"urls": ["https://example.com/data.jsonl"]}'
```

---

## SQL Analytics

Arrow Lake uses **DuckDB** for in-process OLAP. Queries run directly over Lance datasets without loading everything into memory.

### Supported SQL

`SELECT` with `GROUP BY`, aggregation functions (`COUNT`, `AVG`, `SUM`, `MIN`, `MAX`), window functions, `HAVING`, `ORDER BY`, `LIMIT`, and `JOIN`. SQL injection is prevented by a three-layer defense (keyword blocklist, semicolon rejection, literal escaping).

### CLI

```bash
# Basic analytics
arrow-lake query sql my_dataset --sql "SELECT category, COUNT(*) AS cnt, AVG(word_count) AS avg_words FROM my_dataset GROUP BY category ORDER BY cnt DESC"

# Materialize as persistent view (DuckLake table) with TTL
arrow-lake query materialize my_dataset --sql "SELECT category, COUNT(*) AS cnt FROM my_dataset GROUP BY category" --name category_stats --ttl-days 30

# Metadata query (catalog-level, lighter weight)
arrow-lake query meta my_dataset --sql "SELECT name, row_count, version FROM information_schema.tables"

# Daft DataFrame (lazy evaluation)
arrow-lake query daft my_dataset --columns id,text,category --limit 100
```

### SDK

```python
# OLAP query
result = lake.olap_query("my_dataset",
    "SELECT category, COUNT(*) as cnt FROM my_dataset GROUP BY category")
for i in range(result.row_count):
    print(result.table.column("category")[i], result.table.column("cnt")[i])

# JOIN with another table
result = lake.olap_query("my_dataset",
    "SELECT a.id, a.text, b.label FROM my_dataset a JOIN labels b ON a.id = b.id",
    tables={"labels": label_table})

# Materialized view
lake.materialize("my_dataset", "SELECT * FROM my_dataset WHERE quality_score > 0.8",
                 view_name="high_quality", ttl_days=7)

# Daft DataFrame (lazy)
df = lake.daft_query("my_dataset", columns=["id", "text", "category"])
result_df = df.filter(daft.col("category") == "ml").sort(daft.col("id"))
```

### Decision: DuckDB vs Daft vs Lance Direct

| Scenario | Use | Reason |
|----------|-----|--------|
| Aggregation, GROUP BY, JOIN | DuckDB (`olap_query`) | Columnar OLAP optimized |
| Lazy filter/map, large datasets | Daft (`daft_query`) | Lazy evaluation, streaming |
| Point reads, version access | Lance direct (`read_dataset`) | Fastest for single-row lookups |
| Cross-dataset JOIN | DuckDB with `tables` param | DuckDB handles multi-table |

---

## Catalog & Dataset Management

```bash
# List datasets
arrow-lake catalog list

# Dataset details (rows, columns, schema, version)
arrow-lake catalog info my_dataset

# Copy, rename, merge
arrow-lake catalog copy my_dataset my_dataset_backup
arrow-lake catalog rename my_dataset new_name
arrow-lake catalog merge --sources a,b,c merged_dataset

# Health check
arrow-lake catalog health

# Delete (with confirmation)
arrow-lake catalog delete my_dataset --yes
```

---

## Quality Gates

Three-stage pipeline on every ingestion: **schema validation** -> **content filtering** -> **quality scoring**. Rejected rows go to a dead-letter queue for replay.

### CLI

```bash
# Deduplicate
arrow-lake quality dedup my_dataset --strategy exact --action remove
arrow-lake quality dedup my_dataset --strategy perceptual --action flag --threshold 10

# Run quality filters
arrow-lake quality filter my_dataset --filters text_length,image_resolution --mode all
```

### Declarative Rules (SDK)

```python
from arrow_lake.quality.rules import QualityRuleEngine

engine = QualityRuleEngine()
engine.add_rule({"name": "min_length", "column": "text_content",
                 "check": "length", "params": {"min": 10},
                 "action": "reject", "message": "Text too short"})
engine.add_rule({"name": "no_nulls", "column": "category",
                 "check": "regex", "params": {"pattern": "^[a-z]+$"},
                 "action": "flag"})

result = engine.evaluate(table)
clean_table, report = engine.apply(table)
```

### Built-in Filters

| Filter | Checks | Key Params |
|--------|--------|------------|
| `TextLengthFilter` | `text_content` length | `min_chars`, `max_chars` |
| `ImageResolutionFilter` | `image_width`/`image_height` minimums | `min_width`, `min_height` |

---

## Export

```bash
# CLI — export to Parquet or CSV
arrow-lake export my_dataset --output ./export.parquet --format parquet
arrow-lake export my_dataset --output ./export.csv --columns id,text,category

# SDK — export with version selection and compression
result = lake.export("my_dataset", "./export.parquet",
                     version=3, compression="zstd", overwrite=True)
```

### Multi-Target Export (REST API)

```bash
# Export to multiple targets via Daft
curl -X POST .../export-to -d '{
  "target_uri": "s3://bucket/exports/",
  "format": "parquet"
}'
# Supported formats: parquet, csv, json, iceberg, clickhouse
```

---

## Lifecycle & Retention

```bash
# View lifecycle config
arrow-lake lifecycle config

# Apply lifecycle rules (standard -> IA -> Glacier)
arrow-lake lifecycle apply --prefix "archive/"

# Estimate cost savings
arrow-lake lifecycle estimate --size-gb 500 --target-tier GLACIER

# Restore Glacier-tiered object
arrow-lake lifecycle restore archived/file.lance --days 7

# Preview rules without applying
arrow-lake lifecycle rules
```

Configuration (env vars):

```bash
ARROW_LAKE__LIFECYCLE__STANDARD_TO_IA_DAYS=90
ARROW_LAKE__LIFECYCLE__IA_TO_GLACIER_DAYS=180
ARROW_LAKE__LIFECYCLE__GLACIER_EXPIRATION_DAYS=365
```

---

## Enterprise: Gravitino Metadata Federation

When `ARROW_LAKE__GRAVITINO__ENABLED=true`, Arrow Lake syncs with Apache Gravitino for:

- **Federated catalog**: DuckDB metadata <-> Gravitino bidirectional sync
- **Tag-based governance**: classify tables/columns with Gravitino tags
- **Column masking**: Gravitino policies applied after ACL filtering
- **Multi-engine query**: Trino, Spark, Flink can query via Gravitino

See [Gravitino cookbook](../../docs/cookbook/15-gravitino-metadata.md) for setup.

---

## Next Steps

- **Build knowledge on top of your data?** -> [Knowledge Plane](../knowledge-plane/README.md) for vector search, RAG, and knowledge graphs.
- **Deploy to production?** -> [Compute Plane](../compute-plane/README.md) for Docker Compose, Helm, and monitoring.
- **Architecture deep-dive?** -> [Three-Layer Architecture](../concepts/architecture.md).
