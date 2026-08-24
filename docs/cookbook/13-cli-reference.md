# Arrow Lake CLI Complete Reference Manual

> Covers all 100+ commands, parameter descriptions, example output, and Python SDK equivalents. Includes 5 end-to-end practical scenarios, from local development to S3/MinIO production deployment.

**Sample Data**: The data files used in all practical scenarios in this tutorial are located in the [`datas/`](datas/README.md) directory and can be run directly. Includes AIGC article metadata CSV, flight parquet, AIGC industry report PDF, and other real-world examples.

---

## Global Options

All subcommands inherit two global options from the main command:

```bash
arrow-lake --base-uri ./data/lake --config prod.yaml <subcommand>
```

| Option | Default | Environment Variable | Description |
|--------|---------|----------------------|-------------|
| `--base-uri` | `./data/lake` | `ARROW_LAKE_BASE_URI` | Data lake storage root path (local path or bucket prefix) |
| `--config` | None | — | YAML configuration file path |
| `--verbose` / `-v` | `0` | — | Increase output verbosity (stackable: -v, -vv, -vvv) |
| `--quiet` / `-q` | No | — | Show only error output |
| `--format` | `table` | — | Output format: `table`, `json`, `csv` |

> **Note**: Global options must be placed **before** the subcommand. `arrow-lake --base-uri ./lake status` is correct; `arrow-lake status --base-uri ./lake` is incorrect.

---

## Part 1: Command Reference

### 1. Top-Level Commands

#### `arrow-lake serve` — Start API Server

```bash
arrow-lake serve --host 0.0.0.0 --port 8000
arrow-lake serve --reload              # Development mode, auto-reload on code changes
```

| Parameter | Default | Description |
|-----------|---------|-------------|
| `--host` | `0.0.0.0` | Bind address |
| `--port` | `8000` | Listen port |
| `--reload` | No | Enable hot reload |

After starting, visit `http://localhost:8000/docs` to view Swagger documentation.

#### `arrow-lake version` — View Version Information

```bash
arrow-lake version
```

Example output:

```text
┏━━━━━━━━━━━━┳━━━━━━━━━┓
┃ Component  ┃ Version ┃
┡━━━━━━━━━━━━╇━━━━━━━━━┩
│ arrow-lake │ 1.10.7  │
│ python     │ 3.11.9  │
│ pyarrow    │ 23.0.1  │
│ duckdb     │ 1.5.5   │
│ lancedb    │ 0.36.0  │
└────────────┴─────────┘
```

#### `arrow-lake status` — List Datasets

`status` is a shortcut alias for `catalog list`:

```bash
arrow-lake status                     # Use default path
arrow-lake --base-uri ./my_lake status
```

#### `arrow-lake demo` / `arrow-lake multimodal-demo` — Interactive Demo

```bash
arrow-lake demo                      # Synthetic data, demo vector/SQL/FTS queries
arrow-lake demo --no-cleanup          # Keep demo data without cleanup
arrow-lake multimodal-demo            # Multimodal demo (images + text + structured data)
```

---

### 2. `arrow-lake catalog` — Dataset Management

Manage the lifecycle of datasets: list, view details, delete.

#### `catalog list` — List All Datasets

```bash
arrow-lake catalog list
arrow-lake catalog list --json        # JSON format output
```

Example output:

```text
┏━━━┳━━━━━━━━━━━━━━━━┓
┃ # ┃ Name            ┃
┡━━━╇━━━━━━━━━━━━━━━━┩
│ 1 │ aigc_articles   │
│ 2 │ images          │
│ 3 │ ontime          │
└───┴────────────────┘
```

**SDK equivalent:**

```python
from arrow_lake import Lake
lake = Lake("./data")
datasets = lake.list_datasets()  # -> ['aigc_articles', 'images', 'ontime']
```

#### `catalog info <name>` — View Dataset Details

```bash
arrow-lake catalog info aigc_articles
```

Example output:

```text
Dataset: aigc_articles
┏━━━━━━━━━┳━━━━━━━━━━━━━━┓
┃ Property ┃ Value          ┃
┡━━━━━━━━━╇━━━━━━━━━━━━━━┩
│ Rows     │ 12580          │
│ Columns  │ 8              │
│ Version  │ 3              │
└─────────┴────────────────┘

Schema
┏━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━┓
┃ Column        ┃ Type               ┃ Nullable ┃
┡━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━┩
│ id           │ string             │ true     │
│ title        │ string             │ false    │
│ text_content │ string             │ true     │
│ category     │ string             │ true     │
│ word_count   │ int64              │ true     │
│ text_embedding│ fixed_size_list[1024][float32]│ true│
└──────────────┴────────────────────┴─────────┘
```

#### `catalog delete <name>` — Delete Dataset

```bash
arrow-lake catalog delete old_data          # Interactive confirmation
arrow-lake catalog delete old_data --yes    # Skip confirmation
```

> **Warning**: Deletion is irreversible. It is recommended to run `backup create` first.

#### `catalog rename <name> <new_name>` — Rename Dataset

```bash
arrow-lake catalog rename old_name new_name
```

**SDK equivalent:**

```python
lake.rename_dataset("old_name", "new_name")
```

#### `catalog copy <name> <new_name>` — Copy Dataset

```bash
arrow-lake catalog copy documents documents_backup
```

**SDK equivalent:**

```python
lake.copy_dataset("documents", "documents_backup")
```

#### `catalog merge --sources <src1,src2,...> <target>` — Merge Datasets

All source datasets must have the same schema.

```bash
arrow-lake catalog merge --sources "q1_2024,q2_2024,q3_2024" yearly_sales
```

| Parameter | Default | Description |
|-----------|---------|-------------|
| `--sources` | — (**Required**) | Comma-separated source dataset names |
| `target` | — (**Positional argument**) | Target dataset name |

#### `catalog health` — System Health Check

```bash
arrow-lake catalog health
```

Checks storage accessibility, DuckDB session pool, uptime, etc.

#### `catalog inspect <name>` — View Dataset Metadata (catalog view)

```bash
arrow-lake catalog inspect documents
arrow-lake catalog inspect documents --json
```

---

### 3. `arrow-lake ingest` — Data Ingestion

Supports ingestion from multiple data sources, including files, remote URLs, images, PDFs, and videos. Also includes dataset-level operation commands such as create/append/upsert/delete-rows/update-rows.

#### `ingest files <dataset> <paths...>` — Local File Ingestion

Supported formats: CSV, JSON, JSONL, Parquet.

```bash
# Single file
arrow-lake ingest files ontime datas/ontime/ontime_2022.parquet

# Multiple files (mixed formats)
arrow-lake ingest files logs ./logs/api.jsonl ./logs/service.json

# Wildcards
arrow-lake ingest files raw_data ./csv/*.csv ./parquet/*.parquet
```

Example output:

```text
Ingestion: 3 file(s) -> ontime
┏━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━┓
┃ Metric          ┃ Value        ┃
┡━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━┩
│ Rows ingested   │ 15000        │
│ Dataset         │ ontime       │
│ Files processed │ 3            │
│ Duration (s)    │ 1.23         │
└────────────────┴─────────────┘
```

**SDK equivalent:**

```python
lake.ingest("ontime", ["./data/ontime_2022.parquet"])
```

#### `ingest http <dataset> <urls...>` — Remote URL Ingestion

```bash
arrow-lake ingest http aigc_report \
    https://example.com/reports/aigc_industry_report.pdf \
    https://example.com/reports/aigc_survey_2024.pdf
```

**SDK equivalent:**

```python
lake.ingest_http("aigc_report", ["https://example.com/reports/aigc_industry_report.pdf"])
```

#### `ingest images <dataset> <paths...>` — Image Ingestion

Automatically extracts thumbnails and EXIF metadata.

```bash
arrow-lake ingest images photos ./photos/vacation/*.jpg ./photos/portrait/*.png
```

**SDK equivalent:**

```python
lake.ingest_images("photos", ["./photos/vacation/*.jpg"])
```

#### `ingest documents <dataset> <paths...>` — PDF Document Ingestion

Automatically parses PDF, OCR recognition, and text chunking.

```bash
arrow-lake ingest documents aigc_report datas/reports/aigc_industry_report.pdf
```

**SDK equivalent:**

```python
lake.ingest_documents("aigc_report", ["datas/reports/aigc_industry_report.pdf"])
```

#### `ingest videos <dataset> <paths...>` — Video Ingestion

Automatically extracts key frames.

```bash
arrow-lake ingest videos frames ./videos/lecture.mp4 ./videos/interview.mp4
```

**SDK equivalent:**

```python
lake.ingest_videos("frames", ["./videos/lecture.mp4"])
```

#### `ingest create <name> --data <file>` — Create Dataset from File

```bash
arrow-lake ingest create ontime --data ontime_2022.parquet
```

#### `ingest append <name> --data <file>` — Append Data

```bash
arrow-lake ingest append ontime --data new_flights.parquet
```

#### `ingest upsert <dataset> --data <file> --on <column>` — Update or Insert

```bash
arrow-lake ingest upsert products --data updated.csv --on product_id
```

#### `ingest delete-rows <dataset> --where <expr>` — Delete by WHERE Clause

```bash
arrow-lake ingest delete-rows ontime --where "ArrDelay > 60"
```

#### `ingest update-rows <dataset> --where <expr> --set <json>` — Update by WHERE Clause

```bash
arrow-lake ingest update-rows products \
    --where "category = 'electronics'" \
    --set '{"price": 99.99}'
```

---

### 4. `arrow-lake search` — Search

Five search modes covering vector retrieval, full-text retrieval, hybrid retrieval, faceted search, and ensemble search.

#### `search vector <dataset>` — Vector Similarity Search

First encodes the query text into a vector using an embedding model, then performs ANN search.

```bash
arrow-lake search vector aigc_articles \
    --query "transformer attention mechanism" \
    --top-k 5 \
    --column text_embedding \
    --model Qwen/Qwen3-Embedding-0.6B
```

| Parameter | Default | Description |
|-----------|---------|-------------|
| `--query` | — (**Required**) | Search text |
| `--top-k` | `10` | Number of results to return |
| `--column` | `text_embedding` | Vector column name |
| `--model` | `Qwen/Qwen3-Embedding-0.6B` | Embedding model |

Example output:

```text
Results (5 rows)
┏━━━┳━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━┳━━━━━━━━━┳━━━━━━━━━┓
┃ # ┃ ID                  ┃ Category ┃ Distance ┃ Text     ┃
┡━━━╇━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━╇━━━━━━━━━╇━━━━━━━━━┩
│ 1 │ doc_0042            │ ml       │ 0.1234   │ Attention...│
│ 2 │ doc_0187            │ dl       │ 0.1567   │ Transfor...│
│ 3 │ doc_0091            │ ml       │ 0.1890   │ Self-att...│
└───┴────────────────────┴──────────┴─────────┴──────────┘
```

**SDK equivalent:**

```python
from arrow_lake.embed.encoder import LocalEmbeddingEncoder

encoder = LocalEmbeddingEncoder()
vec = encoder._load_model().encode(["transformer attention mechanism"])[0].tolist()
result = lake.search("aigc_articles", vec, top_k=5, vector_column="text_embedding")
```

#### `search fts <dataset>` — Full-Text Search (BM25)

BM25-based full-text retrieval. Requires an FTS index to be created first.

```bash
arrow-lake search fts aigc_articles \
    --query "attention mechanism" \
    --top-k 10
```

| Parameter | Default | Description |
|-----------|---------|-------------|
| `--query` | — (**Required**) | Search text |
| `--top-k` | `10` | Number of results to return |
| `--column` | None (uses config default) | Full-text index column name |

> Chinese text is automatically tokenized using jieba before indexing.

**SDK equivalent:**

```python
result = lake.text_search("aigc_articles", "attention mechanism", top_k=10)
```

#### `search hybrid <dataset>` — Hybrid Search (RRF Fusion)

Fuses vector and full-text search results using the Reciprocal Rank Fusion (RRF) algorithm.

```bash
arrow-lake search hybrid aigc_articles \
    --query "attention mechanism" \
    --top-k 10 \
    --vector-column text_embedding \
    --fts-column text_content
```

| Parameter | Default | Description |
|-----------|---------|-------------|
| `--query` | — (**Required**) | Search text |
| `--top-k` | `10` | Number of results to return |
| `--vector-column` | None (uses config default) | Vector column name |
| `--fts-column` | None (uses config default) | Full-text index column name |
| `--model` | `Qwen/Qwen3-Embedding-0.6B` | Embedding model |

**SDK equivalent:**

```python
result = lake.hybrid_search("aigc_articles", vec, "attention mechanism",
                            top_k=10, vector_column="text_embedding")
```

#### `search faceted <dataset>` — Faceted Search (v1.2)

Vector search + grouped statistics. Suitable for filtering-oriented scenarios.

```bash
arrow-lake search faceted products \
    --query "laptop" \
    --facets "category,brand" \
    --top-k 20
```

| Parameter | Default | Description |
|-----------|---------|-------------|
| `--query` | — (**Required**) | Search text |
| `--facets` | None | Comma-separated facet columns |
| `--top-k` | `10` | Number of results to return |
| `--column` | `text_embedding` | Vector column name |
| `--model` | `Qwen/Qwen3-Embedding-0.6B` | Embedding model |

The output includes two tables: search results and facet counts.

**SDK equivalent:**

```python
result = lake.faceted_search("products", vec, facets=["category", "brand"], top_k=20)
```

#### `search ensemble <dataset>` — Ensemble Search (v1.2)

Weighted fusion search across multiple embedding columns.

```bash
arrow-lake search ensemble aigc_articles \
    --query "transformer architecture" \
    --columns "text_embedding,title_embedding" \
    --weights '{"text_embedding": 0.7, "title_embedding": 0.3}' \
    --top-k 10
```

| Parameter | Default | Description |
|-----------|---------|-------------|
| `--columns` | — (**Required**) | Comma-separated embedding column names |
| `--weights` | None | JSON-formatted column weight dictionary |
| `--query` | — (**Required**) | Search text |
| `--top-k` | `10` | Number of results to return |

---

### 5. `arrow-lake index` — Index Management

#### `index vector <dataset>` — Create Vector Index

```bash
arrow-lake index vector aigc_articles \
    --column text_embedding \
    --metric l2 \
    --type IVF_PQ \
    --replace
```

| Parameter | Default | Description |
|-----------|---------|-------------|
| `--column` | None (uses config default) | Vector column name |
| `--metric` | None (uses config default) | Distance metric: `l2`, `cosine`, `dot` |
| `--type` | None (uses config default) | Index type: `IVF_PQ`, `IVF_FLAT`, `IVF_HNSW_PQ` |
| `--replace/--no-replace` | replace | Whether to replace existing index |

**SDK equivalent:**

```python
lake.create_vector_index("aigc_articles", metric="l2", index_type="IVF_PQ")
```

#### `index fts <dataset>` — Create Full-Text Search Index

```bash
arrow-lake index fts aigc_articles --column text_content
```

> Chinese text is automatically tokenized using jieba before indexing.

**SDK equivalent:**

```python
lake.create_fts_index("aigc_articles", fts_column="text_content")
```

#### `index scalar <dataset>` — Create Scalar Index

Build a scalar index on a single column to speed up filtering and facet aggregation (BITMAP for low-cardinality columns, BTREE otherwise).

```bash
arrow-lake index scalar aigc_articles --column category
```

| Parameter | Default | Description |
|-----------|---------|-------------|
| `--column` | None (**required**) | Target column name |
| `--type` | auto | Index type: `BTREE`, `BITMAP` |
| `--name` | auto | Index name |
| `--replace/--no-replace` | `replace` | Whether to replace an existing index |

**SDK equivalent:**

```python
lake.create_scalar_index("aigc_articles", column="category")
```

#### `index facets <dataset>` — Bulk-Create Facet Indexes

Bulk-build scalar indexes on the default facet columns according to `FacetedSearchConfig.scalar_index_type_map`.

```bash
arrow-lake index facets aigc_articles
```

**SDK equivalent:**

```python
lake.create_facet_indexes("aigc_articles")
```

#### `index list-vector <dataset>` — List Vector Indexes (v1.2)

```bash
arrow-lake index list-vector aigc_articles
```

#### `index info-vector <dataset>` — View Vector Index Info (v1.2)

```bash
arrow-lake index info-vector aigc_articles
```

#### `index rebuild-vector <dataset>` — Rebuild Vector Index (v1.2)

```bash
arrow-lake index rebuild-vector aigc_articles --column text_embedding
```

#### `index delete-vector <dataset> <index_name>` — Delete Vector Index (v1.2)

```bash
arrow-lake index delete-vector aigc_articles text_embedding_idx
```

| Parameter | Description |
|-----------|-------------|
| `dataset` | (**Positional argument**) Dataset name |
| `index_name` | (**Positional argument**) Vector index name |

#### `index info-fts <dataset>` — View Full-Text Index Info (v1.2)

```bash
arrow-lake index info-fts aigc_articles
```

#### `index delete-fts <dataset>` — Delete Full-Text Index (v1.2)

```bash
arrow-lake index delete-fts aigc_articles
```

---

### 6. `arrow-lake query` — SQL Queries

#### `query sql <dataset>` — DuckDB SQL Query

Execute SQL analytical queries via DuckDB. Supports aggregation, window functions, JOINs, etc.

```bash
arrow-lake query sql ontime \
    --sql "SELECT Reporting_Airline, COUNT(*) as cnt, AVG(ArrDelay) as avg_arr_delay
           FROM ontime GROUP BY Reporting_Airline ORDER BY cnt DESC" \
    --max-rows 50
```

| Parameter | Default | Description |
|-----------|---------|-------------|
| `--sql` | — (**Required**) | SQL query statement |
| `--max-rows` | `100` | Maximum display rows |

Example output:

```text
Query Result (5 rows)
┏━━━━━━━━━━━━━━━━━━━━┳━━━━━━┳━━━━━━━━━━━━━━┓
┃ Reporting_Airline   ┃ cnt  ┃ avg_arr_delay┃
┡━━━━━━━━━━━━━━━━━━━━╇━━━━━━╇━━━━━━━━━━━━━━┩
│ WN                  │ 5420 │ 12.56        │
│ AA                  │ 3210 │ 9.12         │
│ DL                  │ 2870 │ 5.78         │
│ UA                  │ 2150 │ 8.23         │
│ OO                  │ 1350 │ 14.89        │
└─────────────────────┴─────┴──────────────┘
```

**SDK equivalent:**

```python
result = lake.olap_query("ontime", sql, max_rows=50)
```

#### `query materialize <dataset>` — Materialized View

Persists SQL query results as reusable materialized views.

```bash
arrow-lake query materialize ontime \
    --sql "SELECT Reporting_Airline, COUNT(*) as cnt FROM ontime GROUP BY Reporting_Airline" \
    --name airline_summary \
    --ttl-days 30
```

| Parameter | Default | Description |
|-----------|---------|-------------|
| `--sql` | — (**Required**) | SQL query statement |
| `--name` | — (**Required**) | Materialized view name |
| `--ttl-days` | Unlimited | Retention days |

**SDK equivalent:**

```python
row_count = lake.materialize("ontime", sql, view_name="airline_summary", ttl_days=30)
```

#### `query meta <dataset>` — Dataset Metadata Query (v1.2)

```bash
arrow-lake query meta aigc_articles --sql "SELECT * FROM aigc_articles LIMIT 5"
```

| Parameter | Default | Description |
|-----------|---------|-------------|
| `--sql` | — (**Required**) | Metadata SQL query statement |
| `--max-rows` | `100` | Maximum display rows |

#### `query cleanup-materialized` — Cleanup Expired Materialized Views (v1.2)

```bash
arrow-lake query cleanup-materialized
arrow-lake query cleanup-materialized --ttl-days 30
```

| Parameter | Default | Description |
|-----------|---------|-------------|
| `--ttl-days` | `7` | Cleanup materialized views older than specified days |

#### `query daft <dataset>` — Daft DataFrame Query (v1.2)

Loads the dataset as a Daft DataFrame and displays it.

```bash
arrow-lake query daft aigc_articles --columns id,title --limit 10
```

| Parameter | Default | Description |
|-----------|---------|-------------|
| `--columns` | All columns | Comma-separated column names |
| `--limit` | `50` | Maximum display rows |

---

### 7. `arrow-lake export` — Data Export

```bash
arrow-lake export aigc_articles --output result.parquet --format parquet
arrow-lake export aigc_articles --output result.csv --format csv
arrow-lake export aigc_articles --output subset.parquet --columns id,title,text_content
```

| Parameter | Default | Description |
|-----------|---------|-------------|
| `--output` | — (**Required**) | Output file path |
| `--format` | Auto-detected | Output format: `parquet` or `csv` |
| `--columns` | All columns | Comma-separated column names |

**SDK equivalent:**

```python
lake.export("aigc_articles", "result.parquet", format="parquet", columns=["id", "title"])
```

---

### 8. `arrow-lake embed` — Vector Generation

Standalone embedding model for vector generation, independent of datasets.

#### `embed text <text>` — Text Vector Generation

```bash
arrow-lake embed text "transformer attention mechanism" \
    --model Qwen/Qwen3-Embedding-0.6B \
    --source huggingface
```

| Parameter | Default | Description |
|-----------|---------|-------------|
| `--model` | `Qwen/Qwen3-Embedding-0.6B` | Embedding model name |
| `--source` | `huggingface` | Model source: `huggingface` or `modelscope` (China mirror) |

Example output:

```text
Loading model Qwen/Qwen3-Embedding-0.6B... done
Encoding... done
  Dimension: 1024
  Norm: 1.000000
  First 5 values: [0.0234, -0.0567, 0.0891, -0.0123, 0.0456]
```

#### `embed image <path>` — Image Vector Generation

```bash
arrow-lake embed image ./photos/cat.jpg --model openai/clip-vit-base-patch32
```

---

### 9. `arrow-lake quality` — Data Quality

#### `quality dedup <dataset>` — Data Deduplication

```bash
# Exact deduplication (identical content)
arrow-lake quality dedup ontime --strategy exact --action remove

# Perceptual hash deduplication (near-duplicate images/text)
arrow-lake quality dedup photos --strategy perceptual --action flag --threshold 10

# Combine both
arrow-lake quality dedup aigc_articles --strategy both --action flag
```

| Parameter | Default | Description |
|-----------|---------|-------------|
| `--strategy` | — (**Required**) | Deduplication strategy: `exact`, `perceptual`, `both` |
| `--action` | — (**Required**) | Action: `flag` (mark) or `remove` (delete) |
| `--threshold` | `10` | Perceptual hash Hamming distance threshold |

**SDK equivalent:**

```python
result = lake.deduplicate("photos", strategy="perceptual", action="flag", perceptual_threshold=10)
```

#### `quality filter <dataset>` — Quality Filtering

```bash
arrow-lake quality filter aigc_articles --filters "null_check,min_length" --mode all
```

| Parameter | Default | Description |
|-----------|---------|-------------|
| `--filters` | — (**Required**) | Comma-separated filter names |
| `--mode` | `all` | Filter mode: `all` (all must pass) or `any` (any must pass) |

---

### 10. `arrow-lake backup` — Backup and Restore

#### `backup create` — Create Backup

```bash
# Backup specific datasets
arrow-lake backup create --datasets aigc_articles images

# Backup all datasets + custom ID
arrow-lake backup create --backup-id daily-2024-04-24
```

#### `backup list` — List Backups

```bash
arrow-lake backup list
```

Example output:

```text
Backups
┏━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━┳━━━━━━━━━━┳━━━━━━━━━━┓
┃ Backup ID           ┃ Created          ┃ Datasets   ┃ Size     ┃
┡━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━╇━━━━━━━━━━╇━━━━━━━━━━┩
│ daily-2024-04-24    │ 2024-04-24 10:30 │ aigc_art... │ 256 MB   │
│ daily-2024-04-23    │ 2024-04-23 10:30 │ aigc_art... │ 248 MB   │
└─────────────────────┴─────────────────┴────────────┴─────────┘
```

#### `backup restore <id>` — Restore Backup

```bash
arrow-lake backup restore daily-2024-04-24
arrow-lake backup restore daily-2024-04-24 --datasets aigc_articles
```

#### `backup delete <id>` — Delete Backup

```bash
arrow-lake backup delete daily-2024-04-24
```

> When data is stored on S3/MinIO, backups are also stored in the object storage under the `backups/` prefix. For local storage, backups are placed in the `{base_uri}/.backups/` directory.

---

### 11. `arrow-lake kg` — Knowledge Graph

> All KG commands are asynchronous operations and require the HugeGraph service to be running.
>
> **Per-dataset isolated graphs (v1.8.6+)**: Each dataset maps to its own HugeGraph graph `kg_{dataset}`. Subcommands `query` / `stats` / `neighbors` / `export` / `traverser` / `algo` accept `--dataset <name>` to target a dataset (inferred from config when omitted).

#### `kg build <dataset>` — Build Knowledge Graph

```bash
arrow-lake kg build aigc_report                 # default: full build
arrow-lake kg build aigc_report --incremental   # incremental: only feed chunks new since the last build
arrow-lake kg build aigc_report --template project_concept_graph   # v1.10.0: specify extraction template, overriding doc_type routing
```

| Parameter | Default | Description |
|-----------|---------|-------------|
| `--incremental` | no (full by default) | Incremental mode processes only new chunks (falls back to full if no KA dump exists or the template changed). Use `--incremental` after appending data; use the default full rebuild after re-ingest/delete or a template change |
| `--template` | none (doc_type routing) | v1.10.0: knowledge extraction template name (e.g. `project_concept_graph`), overriding the doc_type 3-tier router; pair with templates managed online via the console `extraction-templates.html` |

Returns a `task_id` for querying build progress.

#### `kg list-doc-types` — List Document Types

```bash
arrow-lake kg list-doc-types
```

Lists the supported doc_types and their mapped extraction templates (from `HugeGraphConfig.he_doc_type_templates`).

#### `kg list-templates` — List Extraction Templates

```bash
arrow-lake kg list-templates                  # all templates
arrow-lake kg list-templates --category general  # filter by category
```

#### `kg describe-template <path>` — Show Template Detail

```bash
arrow-lake kg describe-template general/concept_graph
```

Displays the full schema of the specified template (node/edge types, required fields, constraints, etc.).

#### `kg status <task_id>` — View Build Progress

```bash
arrow-lake kg status task_abc123
```

#### `kg stats` — Graph Statistics

```bash
arrow-lake kg stats
```

Example output:

```text
Knowledge Graph Stats
┏━━━━━━━━━━━━━━┳━━━━━━━━━━━┓
┃ Metric        ┃ Value      ┃
┡━━━━━━━━━━━━━━╇━━━━━━━━━━━┩
│ vertex_count  │ 12580      │
│ edge_count    │ 34520      │
│ relation_types│ 12         │
└──────────────┴───────────┘
```

#### `kg query <gremlin>` — Gremlin Query

```bash
arrow-lake kg query "g.V().has('type','report').limit(10)"
```

#### `kg neighbors <entity_id>` — Neighbor Traversal

```bash
arrow-lake kg neighbors "report:aigc_001" --depth 2
```

| Parameter | Default | Description |
|-----------|---------|-------------|
| `--depth` | `1` | Traversal depth |

#### `kg delete` — Delete Graph

```bash
arrow-lake kg delete --yes
```

> **Warning**: Irreversible. Requires rebuild.

#### `kg export` — Export Knowledge Graph

```bash
arrow-lake kg export --output graph.json
```

#### `kg import` — Import Knowledge Graph

```bash
arrow-lake kg import graph.json
```

#### `kg traverser` — Graph Traversal Algorithm Subgroup (v1.2)

8 traversal algorithms:

```bash
# All shortest paths
arrow-lake kg traverser all-shortest-paths v1 v2

# Weighted shortest path
arrow-lake kg traverser weighted-shortest v1 v2

# Single-source shortest path
arrow-lake kg traverser single-source-shortest v1

# Multi-node shortest path
arrow-lake kg traverser multi-node-shortest --sources '["v1","v2"]' --targets '["v3","v4"]'

# Rays (acyclic paths)
arrow-lake kg traverser rays v1 --max-depth 5

# Ring detection
arrow-lake kg traverser rings v1 --max-depth 5

# Crosspoints
arrow-lake kg traverser crosspoints v1 v2

# Custom multi-step traversal
arrow-lake kg traverser customized v1 \
    --steps '[{"labels":["person"],"direction":"OUT"},{"labels":["software"],"direction":"OUT"}]'
```

**Traverser Subcommand Parameter Table:**

| Subcommand | Parameter | Default | Description |
|------------|-----------|---------|-------------|
| `all-shortest-paths` | `--direction` | `OUT` | Traversal direction: `OUT`, `BOTH`, `IN` |
| | `--max-depth` | `10` | Maximum search depth |
| `weighted-shortest` | `--direction` | `OUT` | Traversal direction: `OUT`, `BOTH`, `IN` |
| | `--weight-prop` | `weight` | Weight property name |
| | `--max-degree` | `10000` | Maximum traversal degree |
| `single-source-shortest` | `--direction` | `OUT` | Traversal direction: `OUT`, `BOTH`, `IN` |
| | `--weight-prop` | `weight` | Weight property name |
| | `--max-degree` | `10000` | Maximum traversal degree |
| `multi-node-shortest` | `--sources` | — (**Required**) | Source node JSON array |
| | `--targets` | — (**Required**) | Target node JSON array |
| | `--direction` | `OUT` | Traversal direction |
| | `--weight-prop` | `weight` | Weight property name |
| | `--max-degree` | `10000` | Maximum traversal degree |
| `rays` | `--direction` | `OUT` | Traversal direction: `OUT`, `BOTH`, `IN` |
| | `--max-depth` | `5` | Maximum search depth |
| `rings` | `--direction` | `OUT` | Traversal direction: `OUT`, `BOTH`, `IN` |
| | `--max-depth` | `5` | Maximum search depth |
| `crosspoints` | `--direction` | `OUT` | Traversal direction: `OUT`, `BOTH`, `IN` |
| | `--max-depth` | `10` | Maximum search depth |
| `customized` | `--steps` | — (**Required**) | JSON-formatted multi-step traversal definition |
| | `--with-vertex` | No | Include vertex info in results |
| | `--with-edge` | No | Include edge info in results |

#### `kg algo` — Graph OLAP Algorithm Subgroup (v1.2)

9 algorithms:

```bash
# PageRank — Identify important nodes
arrow-lake kg algo pagerank

# Louvain — Community detection
arrow-lake kg algo louvain

# Label Propagation — Community detection
arrow-lake kg algo label-propagation

# WCC — Weakly connected components
arrow-lake kg algo wcc

# Triangle count
arrow-lake kg algo triangle-count

# Degree centrality
arrow-lake kg algo degree-centrality

# Closeness centrality
arrow-lake kg algo closeness-centrality

# K-core decomposition
arrow-lake kg algo k-core --k 3

# Betweenness centrality
arrow-lake kg algo betweenness-centrality
```

**Algo Subcommand Parameter Table:**

| Subcommand | Parameter | Default | Description |
|------------|-----------|---------|-------------|
| `pagerank` | `--iterations` | `20` | Maximum iterations |
| | `--damping` | `0.85` | Damping factor |
| `louvain` | `--resolution` | `1.0` | Resolution parameter |
| `degree-centrality` | — | — | No additional parameters |
| `closeness-centrality` | — | — | No additional parameters |
| `betweenness-centrality` | — | — | No additional parameters |
| `wcc` | — | — | No additional parameters |
| `triangle-count` | — | — | No additional parameters |
| `k-core` | `--k` | `3` | K-core level |

---

### 12. `arrow-lake rag` — RAG Q&A

#### `rag query <dataset> <question>` — RAG Q&A

```bash
arrow-lake rag query aigc_report \
    "How does the self-attention mechanism in Transformer work?" \
    --top-k 5 \
    --strategy hybrid \
    --session-id session_001
```

| Parameter | Default | Description |
|-----------|---------|-------------|
| `--top-k` | `5` | Number of context chunks to retrieve |
| `--strategy` | None (uses config default) | Retrieval strategy: `vector`, `fts`, `hybrid` |
| `--template` | None (uses config default) | Prompt template: `default_qa`, `graph_qa` |
| `--session-id` | None | Session ID (for multi-turn conversations) |

Example output:

```text
Running RAG query...

Answer:
The self-attention mechanism in Transformer is implemented through the Query-Key-Value triplet...

Citations: (3 sources)
  1. doc_0042 — GPT-4 Technical Capability Evaluation
  2. doc_0187 — Diffusion Model Survey
  3. doc_0091 — Agent Framework Research

Latency: 1234.5ms
Context tokens: 2048
```

#### `rag templates` — List Prompt Templates

```bash
arrow-lake rag templates
```

Built-in templates:

| Template Name | Type | Usage |
|---------------|------|-------|
| `default_qa` | QA | General Q&A |
| `graph_qa` | QA | Knowledge graph-enhanced Q&A |
| `summarize` | SUMMARY | Text summarization |
| `entity_extract` | EXTRACT | Entity extraction |
| `entity_extract_from_question` | EXTRACT | Extract entities from questions |

#### `rag stream <dataset> <question>` — Streaming Output (v1.2)

Outputs RAG answers chunk by chunk. Suitable for interactive scenarios.

```bash
arrow-lake rag stream aigc_report "What is RAG?" --top-k 5
```

#### `rag batch` — Batch Query (v1.2)

Submit multiple questions for concurrent querying at once.

```bash
arrow-lake rag batch aigc_report --questions '["Question 1","Question 2","Question 3"]' --top-k 5
```

| Parameter | Default | Description |
|-----------|---------|-------------|
| `--questions` | — (**Required**) | JSON array format question list |
| `--top-k` | `5` | Context chunk count per query |
| `--strategy` | None | Retrieval strategy |
| `--concurrency` | `5` | Maximum concurrency |

#### `rag extract` — Entity Extraction (v1.2)

```bash
arrow-lake rag extract aigc_report --top-k 20
```

#### `rag feedback` — Submit Feedback (v1.2)

```bash
arrow-lake rag feedback s1 0 positive
arrow-lake rag feedback s1 0 negative --comment "Answer not detailed enough"
```

| Parameter | Description |
|-----------|-------------|
| `session_id` | (**Positional argument**) Session ID |
| `turn_id` | (**Positional argument**, int) Turn number |
| `rating` | (**Positional argument**) Rating: `positive`, `negative`, `neutral` |
| `--comment` | Additional comment |

#### `rag history` — View Session History (v1.2)

```bash
arrow-lake rag history s1
```

| Parameter | Description |
|-----------|-------------|
| `session_id` | (**Positional argument**) Session ID |

#### `rag cleanup-sessions` — Cleanup Expired Sessions (v1.2)

```bash
arrow-lake rag cleanup-sessions
```

#### `rag get-feedback` — Get Session Feedback (v1.2)

```bash
arrow-lake rag get-feedback s1
```

| Parameter | Description |
|-----------|-------------|
| `session_id` | (**Positional argument**) Session ID |

---

### 13. `arrow-lake maintenance` — System Maintenance

#### `maintenance status` — View Maintenance Scheduler Status

```bash
arrow-lake maintenance status
```

Outputs the current maintenance scheduler status, last execution time, next scheduled execution time, etc.

#### `maintenance run` — Execute a Full Maintenance Cycle

```bash
arrow-lake maintenance run
arrow-lake maintenance run --json    # JSON format output
```

Executes a full maintenance cycle, including data compaction and version cleanup.

| Parameter | Default | Description |
|-----------|---------|-------------|
| `--json` | No | Output execution results in JSON format |

---

### 14. `arrow-lake config` — Configuration Management

#### `config show` — Display Current Configuration

```bash
arrow-lake config show
arrow-lake --config prod.yaml config show
```

Outputs the complete JSON of the default configuration (all 30 configuration sections).

> The `config` group provides only the `show` and `init` subcommands (no `dump` / `validate`).

#### `config init` — Generate Configuration Template

```bash
arrow-lake config init                    # Default: arrow-lake.yaml
arrow-lake config init --output prod.yaml  # Custom filename
```

The generated configuration file includes all configurable items with comments and can be edited for immediate use.

---

### 15. `arrow-lake audit` — Audit Trail (v1.2)

Complete audit logging, HMAC integrity verification, and anomaly detection.

#### `audit record <event_type>` — Record Audit Event

```bash
arrow-lake audit record dataset_ingested --dataset aigc_articles --actor admin \
    --payload '{"rows": 500, "format": "parquet"}'
```

| Parameter | Default | Description |
|-----------|---------|-------------|
| `--dataset` | None | Associated dataset |
| `--actor` | `cli` | Operator |
| `--payload` | None | JSON-formatted additional data |

#### `audit verify <audit_id>` — Verify Integrity

```bash
arrow-lake audit verify audit-20260426-001
```

#### `audit query` — Query Audit Log

```bash
arrow-lake audit query --dataset aigc_articles --start 2026-01-01 --end 2026-04-01
arrow-lake audit query --event-type dataset_ingested
```

| Parameter | Default | Description |
|-----------|---------|-------------|
| `--dataset` | None | Filter by dataset |
| `--start` | None | Start time (ISO) |
| `--end` | None | End time (ISO) |
| `--event-type` | None | Filter by event type |

#### `audit export <dataset>` — Export Audit Log

```bash
arrow-lake audit export aigc_articles --output audit_trail.json
```

#### `audit analyze` — Anomaly Detection (v1.2)

Automatically runs z-score anomaly detection to identify frequency spikes and operator anomalies.

```bash
arrow-lake audit analyze
```

Output includes anomaly types, severity levels, and number of affected events.

---

### 16. `arrow-lake lineage` — Data Lineage (v1.2)

#### `lineage record <dataset> <operation>` — Record Lineage Event

```bash
arrow-lake lineage record ontime merge \
    --sources "raw_ontime,cleaned_ontime" \
    --transform-type etl \
    --actor pipeline
```

| Parameter | Default | Description |
|-----------|---------|-------------|
| `--sources` | None | Comma-separated source datasets |
| `--transform-type` | None | Transformation type description |
| `--actor` | `cli` | Operator |
| `--metadata` | None | JSON-formatted additional metadata |

#### `lineage history <dataset>` — View Lineage History

```bash
arrow-lake lineage history ontime
```

#### `lineage query <sql>` — SQL Query on Lineage

```bash
arrow-lake lineage query "SELECT * FROM lineage WHERE dataset_name = 'ontime'"
```

| Parameter | Default | Description |
|-----------|---------|-------------|
| `sql` | — (**Required**) | SQL query statement (positional argument) |
| `--max-rows` | `100` | Maximum rows to return |

---

### 17. `arrow-lake lifecycle` — Blob Lifecycle (v1.2)

S3/MinIO object storage tiering, Glacier restoration, and cost estimation.

#### `lifecycle config` — View Current Configuration

```bash
arrow-lake lifecycle config
```

Outputs the current lifecycle configuration: transition days, excluded prefixes, Glacier retrieval type.

#### `lifecycle rules [--prefix]` — Preview Rules

```bash
arrow-lake lifecycle rules
arrow-lake lifecycle rules --prefix data/archive/
```

Previews the S3 lifecycle rules that would be applied without actually executing them.

#### `lifecycle apply [--prefix]` — Apply Rules

```bash
arrow-lake lifecycle apply
arrow-lake lifecycle apply --prefix data/archive/
```

#### `lifecycle status [--prefix]` — View Storage Tiering

```bash
arrow-lake lifecycle status
arrow-lake lifecycle status --prefix data/
```

Outputs each object's key, current tier (STANDARD/STANDARD_IA/GLACIER/DEEP_ARCHIVE), and size.

#### `lifecycle restore <key>` — Restore Glacier Object

```bash
arrow-lake lifecycle restore data/old-file.parquet --days 7
arrow-lake lifecycle restore archive/backup.parquet --days 30
```

| Parameter | Default | Description |
|-----------|---------|-------------|
| `--days` | `7` | Temporary copy retention days |

#### `lifecycle estimate --size-gb N --target-tier TIER` — Cost Estimation

```bash
arrow-lake lifecycle estimate --size-gb 1000 --target-tier STANDARD_IA
arrow-lake lifecycle estimate --size-gb 500 --target-tier GLACIER
arrow-lake lifecycle estimate --size-gb 2000 --target-tier DEEP_ARCHIVE
```

| Parameter | Default | Description |
|-----------|---------|-------------|
| `--size-gb` | — (**Required**) | Total data size (GB) |
| `--target-tier` | `STANDARD_IA` | Target tier: `STANDARD_IA`, `GLACIER`, `DEEP_ARCHIVE` |

---

### 18. Scenario Navigation Aliases (v1.5.0+)

| Alias | Equivalent Command Group | Description |
|-------|-------------------------|-------------|
| `arrow-lake knowledge` | rag + kg | Knowledge building and management navigation |
| `arrow-lake connect` | ingest + catalog | Data connection and ingestion navigation |
| `arrow-lake analyze` | query + search + export | Data analysis and retrieval navigation |
| `arrow-lake govern` | audit + lineage + backup + maintenance | Data governance and operations navigation |

---

## Part 2: Storage Configuration

### Local Storage (Default)

No additional configuration needed. Use directly:

```bash
arrow-lake --base-uri ./my_lake catalog list
arrow-lake --base-uri ./my_lake ingest files my_data data.csv
```

Data is stored in the `./my_lake/` directory, with one subdirectory per dataset.

### S3 / MinIO Remote Storage

Arrow Lake supports storing data on S3 or MinIO. CLI commands **do not need to change** — simply specify S3 connection information via configuration file or environment variables.

**Core Principle**: `--base-uri` in S3 mode is a **bucket prefix**, not a full path. The actual S3 path is automatically assembled by the system:

```text
Actual path = s3://{s3_bucket}/{base_uri}/{dataset}.lance
```

For example, `--base-uri ./data` + `s3_bucket=arrow-lake` → dataset stored at `s3://arrow-lake/data/aigc_articles.lance`.

#### Configuration Method 1: YAML File (Recommended)

Create configuration file `minio.yaml`:

```yaml
storage:
  backend: minio
  s3_endpoint: "http://localhost:9000"
  s3_access_key: "minioadmin"
  s3_secret_key: "minioadmin"
  s3_bucket: "arrow-lake"
  s3_region: "us-east-1"
```

Usage:

```bash
arrow-lake --config minio.yaml --base-uri ./data status
arrow-lake --config minio.yaml --base-uri ./data ingest files aigc_articles data.csv
arrow-lake --config minio.yaml --base-uri ./data search fts aigc_articles --query "AI"
arrow-lake --config minio.yaml --base-uri ./data export aigc_articles --output result.parquet
```

#### Configuration Method 2: Environment Variables (ARROW_LAKE__ Prefix)

```bash
export ARROW_LAKE__STORAGE__BACKEND=minio
export ARROW_LAKE__STORAGE__S3_ENDPOINT=http://localhost:9000
export ARROW_LAKE__STORAGE__S3_ACCESS_KEY=minioadmin
export ARROW_LAKE__STORAGE__S3_SECRET_KEY=minioadmin
export ARROW_LAKE__STORAGE__S3_BUCKET=arrow-lake
export ARROW_LAKE__STORAGE__S3_REGION=us-east-1

arrow-lake --base-uri ./data status
```

#### Configuration Method 3: AWS Standard Environment Variables

```bash
export S3_ENDPOINT=http://localhost:9000
export AWS_ACCESS_KEY_ID=minioadmin
export AWS_SECRET_ACCESS_KEY=minioadmin
export S3_BUCKET=arrow-lake
export AWS_REGION=us-east-1

arrow-lake --config minio.yaml status
```

#### Configuration Method 4: Using AWS Credentials (No Secret Key Configuration Needed)

```yaml
storage:
  backend: s3
  s3_bucket: "my-prod-bucket"
  s3_region: "us-east-1"
  # s3_access_key and s3_secret_key left empty, use IAM Role / EC2 instance profile
```

#### Complete MinIO YAML Template

The template generated by `arrow-lake config init` already includes the `storage` section. Here is a complete example:

```yaml
# arrow-lake.yaml
storage:
  backend: minio              # minio | s3 | gcs | local
  base_uri: "./data"         # Bucket prefix
  s3_endpoint: "http://localhost:9000"
  s3_access_key: ""          # Leave empty to use AWS credential chain
  s3_secret_key: ""
  s3_bucket: "arrow-lake"
  s3_region: "us-east-1"

# Search configuration
vector:
  metric: cosine
  default_top_k: 10
  default_index_type: IVF_PQ

fts:
  default_top_k: 10
  fts_column: "text_content"
  tokenizer_type: "jieba"     # Chinese tokenization

# Embedding model configuration
embedding:
  model: "Qwen/Qwen3-Embedding-0.6B"
  model_source: huggingface  # huggingface | modelscope

# OLAP query configuration
olap:
  max_result_rows: 100000
  query_timeout_seconds: 300

# API configuration
api:
  host: "0.0.0.0"
  port: 8000
  docs_enabled: true

# RAG configuration
rag:
  enabled: true
  default_retrieval_strategy: hybrid
  default_top_k: 10

# Knowledge graph configuration
hugegraph:
  enabled: false
  host: "localhost"
  port: 8089
  graph_name: "arrow_lake_kg"
```

#### Credential Detection Mechanism

The system determines whether to use S3 through the following logic:

```python
has_real_creds = (
    backend != LOCAL
    and s3_access_key != ""          # Has key
    and not s3_access_key.startswith("<")  # Not a placeholder
)
```

S3 configuration is only passed to the Lance engine when conditions are met; otherwise, it falls back to local storage. Even if `backend: minio` is configured, no error occurs if keys are empty.

#### StorageConfig Field Reference

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `backend` | enum | `minio` | Storage backend: `minio`, `s3`, `gcs`, `local` |
| `base_uri` | str | `./data` | Storage root path / bucket prefix |
| `s3_endpoint` | str | `http://localhost:9000` | S3-compatible endpoint |
| `s3_access_key` | str | `""` | Access key |
| `s3_secret_key` | str | `""` | Secret key |
| `s3_bucket` | str | `arrow-lake` | Default bucket name |
| `s3_region` | str | `us-east-1` | Region |

---

## Part 3: Practical Scenarios

### Scenario 1: AIGC Article and Report Management (Local Storage)

Build an AIGC article and report dataset from scratch, completing the full workflow of ingestion, indexing, search, and export.

**Sample Data**:
- `datas/reports/aigc_articles.csv` — 144 AIGC article metadata entries (LLM, multimodal, diffusion, agents, RAG, etc.)
- `datas/reports/aigc_industry_report.pdf` — single AIGC industry report PDF (for document parsing / RAG / KG)

**Step 1: Create Dataset and Ingest Data**

```bash
# Ingest AIGC article metadata
arrow-lake --base-uri ./aigc_lake ingest files aigc_articles datas/reports/aigc_articles.csv

# Ingest AIGC industry report PDF
arrow-lake --base-uri ./aigc_lake ingest documents aigc_report datas/reports/aigc_industry_report.pdf
```

**Step 2: View Dataset**

```bash
arrow-lake --base-uri ./aigc_lake catalog info aigc_articles
```

**Step 3: Create Indexes**

```bash
# Full-text search index (Chinese text auto-tokenized with jieba)
arrow-lake --base-uri ./aigc_lake index fts aigc_articles --column text_content

# Vector index (accelerates vector search)
arrow-lake --base-uri ./aigc_lake index vector aigc_articles \
    --column text_embedding --type IVF_PQ
```

**Step 4: Search Articles**

```bash
# Full-text search
arrow-lake --base-uri ./aigc_lake search fts aigc_articles \
    --query "attention mechanism" --top-k 5

# Vector search (semantic similarity)
arrow-lake --base-uri ./aigc_lake search vector aigc_articles \
    --query "how does self-attention work" --top-k 10

# Hybrid search (combined ranking)
arrow-lake --base-uri ./aigc_lake search hybrid aigc_articles \
    --query "transformer architecture"

# Chinese full-text search (jieba auto-tokenization)
arrow-lake --base-uri ./aigc_lake search fts aigc_articles \
    --query "知识图谱 大模型" --top-k 5
```

**Step 5: SQL Analysis**

```bash
arrow-lake --base-uri ./aigc_lake query sql aigc_articles \
    --sql "SELECT category, COUNT(*) as cnt, MIN(year) as earliest, MAX(year) as latest
           FROM aigc_articles GROUP BY category ORDER BY cnt DESC"
```

**Step 6: Export Results**

```bash
arrow-lake --base-uri ./aigc_lake export aigc_articles \
    --output aigc_library.parquet --columns id,title,authors,year
```

---

### Scenario 2: Multimedia Data Lake (Local Storage)

Manage image and video data, enabling cross-modal search.

**Sample Data**: `datas/photos/` directory already contains 6 sample images. Videos need to be placed in `datas/videos/` manually.

**Step 1: Ingest Multimedia Data**

```bash
# Image ingestion (auto-extract thumbnails + EXIF)
arrow-lake --base-uri ./media_lake ingest files photos datas/photos/*.jpg datas/photos/*.png

# Video ingestion (auto-extract key frames)
arrow-lake --base-uri ./media_lake ingest videos clips datas/videos/lecture_demo.mp4 datas/videos/interview_clip.mp4
```

**Step 2: Generate Embedding Vectors**

```bash
# Single image vector
arrow-lake embed image datas/photos/sunset.jpg --model openai/clip-vit-base-patch32

# Single text vector
arrow-lake embed text "golden hour landscape photography"
```

**Step 3: Create Indexes and Search**

```bash
# Vector index
arrow-lake --base-uri ./media_lake index vector photos --column image_embedding

# Semantic image search
arrow-lake --base-uri ./media_lake search vector photos \
    --query "sunset over the ocean" --column image_embedding
```

---

### Scenario 3: Data Analysis Workflow (Local Storage)

A complete workflow from raw data through quality control to analytical reports.

**Sample Data**:
- `datas/ontime/ontime_2022.parquet` — 1.6M flight records (Reporting_Airline, Origin, Dest, Month, ArrDelay, DepDelay, Cancelled, Distance, CarrierDelay)

**Step 1: Ingest Raw Data**

```bash
# Flight ontime data
arrow-lake --base-uri ./analytics_lake ingest files ontime datas/ontime/ontime_2022.parquet
```

**Step 2: Quality Check**

```bash
# Deduplication
arrow-lake --base-uri ./analytics_lake quality dedup ontime \
    --strategy both --action flag

# Quality filtering
arrow-lake --base-uri ./analytics_lake quality filter ontime \
    --filters "null_check,range_check" --mode all
```

**Step 3: SQL Analysis**

```bash
# Monthly flight trends
arrow-lake --base-uri ./analytics_lake query sql ontime \
    --sql "SELECT Month,
           COUNT(*) as flight_count,
           SUM(Cancelled) as cancelled,
           AVG(ArrDelay) as avg_arr_delay
           FROM ontime
           GROUP BY Month ORDER BY Month DESC"

# Route analysis: top Origin-Dest pairs by delay
arrow-lake --base-uri ./analytics_lake query sql ontime \
    --sql "SELECT Origin, Dest, COUNT(*) as flight_count,
           AVG(ArrDelay) as avg_arr_delay
           FROM ontime GROUP BY Origin, Dest
           ORDER BY avg_arr_delay DESC LIMIT 20"
```

**Step 4: Materialize Common Reports**

```bash
arrow-lake --base-uri ./analytics_lake query materialize ontime \
    --sql "SELECT Reporting_Airline, COUNT(*) as flight_count, AVG(ArrDelay) as avg_delay
           FROM ontime GROUP BY Reporting_Airline" \
    --name airline_summary \
    --ttl-days 7
```

**Step 5: Backup**

```bash
arrow-lake --base-uri ./analytics_lake backup create \
    --datasets ontime --backup-id pre-cleanup
```

---

### Scenario 4: RAG Q&A System (Local Storage)

Build a knowledge graph-enhanced RAG Q&A system.

**Sample Data**:
- `datas/reports/aigc_articles.csv` — 144 AIGC article entries (LLM, multimodal, diffusion, agents, RAG, etc.; reused from Scenario 1)

**Step 1: Ingest Article Data**

```bash
# Ingest AIGC article metadata as the knowledge source
arrow-lake --base-uri ./rag_lake ingest files aigc_articles datas/reports/aigc_articles.csv
```

**Step 2: Create Indexes**

```bash
# Vector index
arrow-lake --base-uri ./rag_lake index vector aigc_articles --column text_embedding

# Full-text index
arrow-lake --base-uri ./rag_lake index fts aigc_articles --column text_content
```

**Step 3: Build Knowledge Graph**

```bash
# Start build (async, returns task_id)
arrow-lake --base-uri ./rag_lake kg build aigc_articles

# Check progress
arrow-lake --base-uri ./rag_lake kg status <task_id>

# View statistics
arrow-lake --base-uri ./rag_lake kg stats

# Graph query
arrow-lake --base-uri ./rag_lake kg query "g.V().has('type','concept').limit(20)"
```

**Step 4: RAG Q&A**

```bash
# Single-turn Q&A
arrow-lake --base-uri ./rag_lake rag query aigc_articles \
    "What is the difference between a diffusion model and an autoregressive LLM?"

# Chinese knowledge base Q&A
arrow-lake --base-uri ./rag_lake rag query aigc_articles \
    "What are the key challenges in multimodal agent frameworks?"

# Multi-turn conversation
arrow-lake --base-uri ./rag_lake rag query aigc_articles \
    "What evaluation metrics does it cover?" \
    --session-id sess_001
```

**Step 5: View Prompt Templates**

```bash
arrow-lake --base-uri ./rag_lake rag templates
```

---

### Scenario 5: MinIO Production Deployment

All CLI commands work with zero changes in S3/MinIO environments. Only one-time configuration is needed.

**Step 1: Prepare Configuration File**

```bash
arrow-lake config init --output prod.yaml
# Edit prod.yaml, fill in MinIO connection information
```

Edit `prod.yaml`:

```yaml
storage:
  backend: minio
  s3_endpoint: "http://minio.example.com:9000"
  s3_access_key: "prod-access-key"
  s3_secret_key: "prod-secret-key"
  s3_bucket: "company-data"
  s3_region: "us-east-1"
  base_uri: "./lake"

rag:
  enabled: true
  default_retrieval_strategy: hybrid

hugegraph:
  enabled: true
  host: "hugegraph.internal"
  port: 8089
```

**Step 2: Complete Data Workflow**

```bash
# All commands only need --config prod.yaml added; --base-uri is the bucket prefix

# Ingest data
arrow-lake --config prod.yaml --base-uri ./datasets ingest files reports ./reports/*.csv

# View data
arrow-lake --config prod.yaml --base-uri ./datasets catalog info reports

# Create indexes
arrow-lake --config prod.yaml --base-uri ./datasets index vector reports
arrow-lake --config prod.yaml --base-uri ./datasets index fts reports --column text_content

# Search
arrow-lake --config prod.yaml --base-uri ./datasets search hybrid reports \
    --query "Q4 revenue analysis" --top-k 5

# SQL analysis
arrow-lake --config prod.yaml --base-uri ./datasets query sql reports \
    --sql "SELECT region, SUM(revenue) FROM reports GROUP BY region"

# Export for downstream teams
arrow-lake --config prod.yaml --base-uri ./datasets export reports \
    --output /tmp/q4_summary.parquet --columns region,revenue,department

# Backup to S3
arrow-lake --config prod.yaml --base-uri ./datasets backup create \
    --datasets reports --backup-id q4-2024-snapshot

# RAG Q&A
arrow-lake --config prod.yaml --base-uri ./datasets rag query reports \
    "How does revenue compare across regions last quarter?" --top-k 10

# Knowledge graph
arrow-lake --config prod.yaml --base-uri ./datasets kg build reports
```

> **All commands are exactly the same**, just with `--config prod.yaml` added. Data is actually stored at `s3://company-data/lake/datasets/reports.lance`.

---

## Appendix

### A. Command Quick Reference

| Scenario | Command |
|----------|---------|
| View datasets | `arrow-lake status` |
| Ingest files | `arrow-lake ingest files <ds> <paths...>` |
| Ingest images | `arrow-lake ingest images <ds> <images...>` |
| Ingest PDFs | `arrow-lake ingest documents <ds> <pdfs...>` |
| Remote ingestion | `arrow-lake ingest http <ds> <urls...>` |
| Vector search | `arrow-lake search vector <ds> --query <text>` |
| Full-text search | `arrow-lake search fts <ds> --query <text>` |
| Hybrid search | `arrow-lake search hybrid <ds> --query <text>` |
| Faceted search | `arrow-lake search faceted <ds> --query <text> --facets <cols>` |
| Ensemble search | `arrow-lake search ensemble <ds> --columns <cols> --questions <json>` |
| Create vector index | `arrow-lake index vector <ds>` |
| Create FTS index | `arrow-lake index fts <ds>` |
| SQL query | `arrow-lake query sql <ds> --sql <sql>` |
| Materialized view | `arrow-lake query materialize <ds> --sql <sql> --name <n>` |
| Export data | `arrow-lake export <ds> --output <path>` |
| Generate vectors | `arrow-lake embed text <text>` |
| Data deduplication | `arrow-lake quality dedup <ds> --strategy <s> --action <a>` |
| Quality filtering | `arrow-lake quality filter <ds> --filters <names>` |
| Create backup | `arrow-lake backup create --datasets <ds...>` |
| Restore backup | `arrow-lake backup restore <id>` |
| Build knowledge graph | `arrow-lake kg build <ds>` |
| Graph query | `arrow-lake kg query <gremlin>` |
| RAG Q&A | `arrow-lake rag query <ds> <question>` |
| RAG streaming | `arrow-lake rag stream <ds> <question>` |
| RAG batch | `arrow-lake rag batch <ds> --questions <json>` |
| Audit record | `arrow-lake audit record <event>` |
| Data lineage | `arrow-lake lineage record <ds> <op>` |
| Lifecycle rules | `arrow-lake lifecycle rules --prefix <prefix>` |
| Lifecycle restore | `arrow-lake lifecycle restore <key>` |
| Maintenance status | `arrow-lake maintenance status` |
| Run maintenance cycle | `arrow-lake maintenance run` |
| Generate configuration | `arrow-lake config init --output <file>` |
| Start server | `arrow-lake serve` |
| Version info | `arrow-lake version` |

### B. Configuration Priority

```text
YAML configuration file (highest) > Environment variables (ARROW_LAKE__*) > .env file > Code defaults
```

### C. Environment Variable Reference

| YAML Field | ARROW_LAKE__ Prefix | AWS Standard Variable |
|------------|-------------------|----------------------|
| `storage.backend` | `ARROW_LAKE__STORAGE__BACKEND` | — |
| `storage.base_uri` | `ARROW_LAKE__STORAGE__BASE_URI` | — |
| `storage.s3_endpoint` | `ARROW_LAKE__STORAGE__S3_ENDPOINT` | `S3_ENDPOINT` / `S3_ENDPOINT_URL` |
| `storage.s3_access_key` | `ARROW_LAKE__STORAGE__S3_ACCESS_KEY` | `AWS_ACCESS_KEY_ID` |
| `storage.s3_secret_key` | `ARROW_LAKE__STORAGE__S3_SECRET_KEY` | `AWS_SECRET_ACCESS_KEY` |
| `storage.s3_bucket` | `ARROW_LAKE__STORAGE__S3_BUCKET` | `S3_BUCKET` |
| `storage.s3_region` | `ARROW_LAKE__STORAGE__S3_REGION` | `AWS_REGION` / `AWS_DEFAULT_REGION` |

### D. FAQ

**Q: Can `--base-uri` be written directly as `s3://bucket/prefix`?**

No. `--base-uri` is always a local path or bucket prefix. S3 connection information is provided separately via `--config` or environment variables. The system internally assembles it as `s3://{bucket}/{base_uri}/{dataset}.lance`.

**Q: What happens if S3 is configured but the secret key is left empty?**

It silently falls back to local storage. When the system detects that `s3_access_key` is empty or starts with `<`, it does not pass S3 configuration to the Lance engine.

**Q: How does the backup command behave differently in S3 mode?**

For local storage, backups are placed in the `{base_uri}/.backups/` directory. For S3/MinIO, backups are stored in the object storage under the `backups/{backup_id}/` prefix, and the data is also on S3.

**Q: When both `--config` and environment variables are present, which takes precedence?**

The YAML file specified by `--config` has the highest priority and overrides environment variables with the same name.

**Q: Do I need to change CLI commands when switching storage backends?**

No. All CLI commands are storage-backend agnostic. Switching only requires changing the configuration.

**Q: What does it mean when many parameter defaults show "None (uses config default)"?**

CLI parameters like `--column`, `--metric`, `--strategy` have a default value of `None`. In this case, they fall back to the default values shown in the YAML configuration file or `arrow-lake config show`. To override, explicitly specify via command-line parameters.

**Q: Why do `rag batch` and `rag feedback` use JSON / positional arguments instead of regular options?**

`--questions` accepts a JSON array to support any number of questions; `rag feedback` uses positional arguments for session_id, turn_id, and rating to simplify the most common feedback submission operation, avoiding verbose `--session-id --turn --rating` prefixes.
