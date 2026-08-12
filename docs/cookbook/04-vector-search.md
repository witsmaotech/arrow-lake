# Vector Search and Indexing

Vector search is the core retrieval capability in Arrow Lake. This guide covers the full pipeline from data ingestion and embedding generation through index creation to similarity search.

> **The running dataset.** Chapters 04–09 all build on one `aigc_articles` AIGC article library (`datas/reports/aigc_articles.csv` — 144 AIGC articles with `title`, `text_content`, `category`, `year`, `venue`, `authors`, `word_count`). This chapter introduces it; later chapters view the same corpus through full-text, hybrid, OLAP, RAG, and knowledge-graph lenses.

```python
from arrow_lake import Lake
from arrow_lake.config import ArrowLakeConfig, StorageConfig, StorageBackend

# Initialize a Lake instance
config = ArrowLakeConfig()
config.storage = StorageConfig(backend=StorageBackend.LOCAL, base_uri="./data")
lake = Lake(base_uri="./data", config=config)

# 1. Ingest the AIGC article library — text_content is auto-embedded into text_embedding
report = lake.ingest("aigc_articles", ["datas/reports/aigc_articles.csv"])
print(f"Ingested {report.total_rows} rows")

# 2. Create a vector index
from arrow_lake.config import DistanceMetric, VectorIndexType
info = lake.create_vector_index("aigc_articles", metric="cosine", index_type="IVF_PQ")
print(f"Index type: {info.index_type}, distance metric: {info.distance_type}")
print(f"Indexed rows: {info.num_indexed_rows}")

# 3. Execute a vector search
import numpy as np
query_vec = np.random.randn(1024).tolist()  # Replace with a real query vector
result = lake.search("aigc_articles", query_vec, top_k=5)
print(f"Returned {result.row_count} results, metric: {result.metric}")

for i in range(result.row_count):
    row = result.table.to_pylist()[i]
    distance = row["_distance"]
    print(f"  [{i}] distance={distance:.4f}")
```

***

## 1. Embedding Generation

Arrow Lake automatically generates embeddings when ingesting text data. Embedding behavior is controlled via `EmbeddingConfig`:

```python
from arrow_lake.config import ArrowLakeConfig, EmbeddingConfig, EmbeddingBackend, ModelSource

config = ArrowLakeConfig()

# Use a local HuggingFace model for embeddings
config.embedding = EmbeddingConfig(
    model="Qwen/Qwen3-Embedding-0.6B",
    model_source=ModelSource.HUGGINGFACE,
    backend=EmbeddingBackend.LOCAL,
    batch_size=128,
)

# Use the OpenAI API for embeddings
config.embedding = EmbeddingConfig(
    backend=EmbeddingBackend.OPENAI,
    api_key="sk-...",
    api_base="https://api.openai.com/v1",
)

from arrow_lake import Lake
lake = Lake(base_uri="./data", config=config)
```

During ingestion, text from the `text_content` column is automatically encoded into a `text_embedding` vector column. Use the `expected_dim` field to explicitly specify the expected dimension for validation.

***

## 2. Creating Vector Indexes

Before running efficient searches, you need to create a vector index. Arrow Lake supports seven index types:

```python
from arrow_lake import Lake

lake = Lake(base_uri="./data")

# --- Basic usage: defaults ---
info = lake.create_vector_index("aigc_articles")
# Defaults: metric=cosine, index_type=IVF_PQ

# --- Specify metric and index type ---
info = lake.create_vector_index(
    "aigc_articles",
    metric="cosine",          # Distance metric: cosine / l2 / dot
    index_type="IVF_PQ",      # Index type
)

# --- Fine-grained index parameter control ---
info = lake.create_vector_index(
    "aigc_articles",
    metric="l2",
    vector_column="text_embedding",  # Vector column name
    index_type="IVF_FLAT",           # More precise but slower
    num_partitions=512,              # Number of IVF partitions
    num_sub_vectors=24,              # PQ sub-vectors (must be a multiple of 8; 24 recommended for 1024-dim)
    replace=True,                    # Replace existing index
)
```

`create_vector_index` returns an `IndexInfo` object:

```python
info = lake.create_vector_index("aigc_articles", metric="cosine", index_type="IVF_PQ")
print(f"Index: {info.index_type}, metric: {info.distance_type}")
print(f"Indexed: {info.num_indexed_rows}, unindexed: {info.num_unindexed_rows}")
print(f"Columns: {info.columns}")
```

### Index Type Comparison

| Type          | Description                               | Best For                          | Notes                               |
| ------------- | ----------------------------------------- | --------------------------------- | ----------------------------------- |
| `IVF_PQ`      | IVF inverted index + product quantization | Large datasets (>10K rows)        | Default choice, low memory usage    |
| `IVF_FLAT`    | IVF inverted index + exact distance       | Medium datasets needing precision | No quantization loss, higher memory |
| `IVF_HNSW_PQ` | IVF + HNSW + PQ                           | Large datasets + low latency      | High build cost                     |
| `IVF_HNSW_SQ` | IVF + HNSW + scalar quantization          | Large + low latency + higher precision | Memory/precision trade-off      |
| `IVF_SQ`      | IVF + scalar quantization                 | Medium-large, more precise than PQ | Smaller quantization loss than PQ  |
| `IVF_RQ`      | IVF + residual quantization               | Very large datasets, extreme compression | Smallest memory, larger precision loss |
| `HNSW`        | Pure HNSW graph index                     | Small-medium, lowest latency      | High memory, no IVF coarse filter   |

> **Indexing notes (v1.9.6)**:
> - **Minimum 256 rows**: quantized indexes (IVF_PQ etc.) need ≥256 training rows (`_PQ_MIN_TRAINING_ROWS`), otherwise `VECTOR_INDEX_TOO_FEW_ROWS` is raised; below that, vector search degrades to brute-force (still usable). Auto-index WARN-skips datasets with <256 rows.
> - **`lance_scan_mode: pyarrow_fallback`**: in production, if RAG/vector search hits a DuckDB lance vector stream Rust panic (worker crash / 502), set this to bypass it (see [12-deployment](./12-deployment.md)).
> - **Multimodal image search**: embed images with CLIP/SigLIP (`POST /embed/image` or SDK `lake.encode_text_clip()` for text→image), then `search(vector_column="image_embedding")`.

***

## 3. Vector Similarity Search

Use `lake.search()` to perform vector similarity search. The system uses a dual-path strategy: it prefers DuckDB's native `lance_vector_search()`, falling back to the LanceDB SDK on failure.

### API Signature

```python
def search(
    self,
    dataset_name: str,
    query_vector: list[float],          # Query embedding vector (positional)
    *,
    top_k: int = 10,                    # Number of results
    metric: str | None = None,          # Distance metric: cosine / l2 / dot
    vector_column: str = "text_embedding",  # Vector column name
    where: str | None = None,           # Metadata filter expression
    nprobes: int | None = None,         # IVF partitions to probe
    version: int | None = None,         # Dataset version for time-travel queries
) -> VectorSearchResult: ...
```

### Basic Usage

```python
from arrow_lake import Lake
import numpy as np

lake = Lake(base_uri="./data")

# Prepare a query vector (dimension must match the dataset's vector column)
query_vector = np.random.randn(1024).tolist()

# Basic search
result = lake.search("aigc_articles", query_vector, top_k=5)

# With explicit metric and column
result = lake.search(
    "aigc_articles",
    query_vector,
    top_k=10,
    metric="cosine",
    vector_column="text_embedding",
)

# With metadata filtering
result = lake.search(
    "aigc_articles",
    query_vector,
    top_k=5,
    where="category = '大语言模型'",
)

# Time-travel query (search a specific dataset version)
result = lake.search("aigc_articles", query_vector, top_k=5, version=3)
```

### Return Type: VectorSearchResult

```python
result = lake.search("aigc_articles", query_vector, top_k=5)
print(f"Rows: {result.row_count}, dimension: {result.query_vector_dim}")
print(f"Metric: {result.metric}, max distance: {result.max_distance}")

for row in result.table.to_pylist():
    print(f"  score={row['_distance']:.4f} | {row.get('text_content', '')[:80]}...")
```

> If no vector index exists, LanceDB automatically falls back to brute-force search. Search works without an index, but performance degrades linearly with data size.

***

## 4. Metadata Filtering

The `where` parameter accepts SQL-style filter expressions that pre-filter metadata columns before the vector search:

```python
# Equality filter
result = lake.search("aigc_articles", qv, where="category = '大语言模型'")

# Numeric range + compound condition
result = lake.search("aigc_articles", qv, where="category = '大语言模型' AND year >= 2023")

# IN operator
result = lake.search("aigc_articles", qv, where="venue IN ('NeurIPS', 'ICLR')")

# String pattern matching
result = lake.search("aigc_articles", qv, where="title LIKE '%transformer%'")
```

> **Security**: Arrow Lake internally checks for dangerous SQL keywords, but you should never interpolate unsanitized user input directly into `where` expressions.

***

## 5. Index Parameter Tuning

### 5.1 num\_partitions -- IVF Partition Count

IVF (Inverted File) divides the vector space into `num_partitions` cluster partitions. During search, only a subset of partitions is scanned (controlled by `nprobes`).

```python
# Arrow Lake auto-adjustment strategy:
#   < 65,536 rows: min(sqrt(rows) * 4, 256)  — avoids empty cluster warnings
#   65K - 1M rows:  uses configured value (default 256)
#   >= 1M rows:     min(sqrt(rows), 4096)       — scales with data volume

# Usually no need to set manually; pass None to let the system choose
info = lake.create_vector_index("aigc_articles", num_partitions=None)
```

### 5.2 num\_sub\_vectors -- PQ Sub-Vector Count

PQ splits high-dimensional vectors into multiple sub-vectors that are quantized independently. `num_sub_vectors` must be a multiple of 8.

```python
from arrow_lake.config import VectorSearchConfig

# Embedding dimension 1024, split into 24 sub-vectors
# Each sub-vector is 1024/24 ~ 42 dimensions
config = ArrowLakeConfig()
config.vector.num_sub_vectors = 24  # 1024 / 24 ~ 42 dims per sub-vector
```

**Tuning recommendations:**

| Embedding Dim | Recommended num\_sub\_vectors | Sub-Vector Dim |
| ------------- | ----------------------------- | -------------- |
| 512           | 16                            | 32             |
| 768           | 24                            | 32             |
| 1024          | 24                            | \~42           |
| 1536          | 32                            | 48             |
| 2048          | 48                            | \~42           |

> More sub-vectors means finer quantization, but also longer index build times and higher memory usage.

### 5.3 nprobes -- Partitions to Probe During Search

`nprobes` controls how many IVF partitions are actually scanned during search. Higher values improve recall at the cost of increased latency.

```python
# Fast search (lower recall)
result = lake.search("aigc_articles", qv, top_k=10, nprobes=5)

# Balanced mode (default)
result = lake.search("aigc_articles", qv, top_k=10, nprobes=20)

# High-recall search
result = lake.search("aigc_articles", qv, top_k=10, nprobes=128)

# Note: nprobes is capped at max_nprobes (default 256)
```

**Relationship between nprobes and num\_partitions:**

* `nprobes = 1`: Scan only the nearest 1 partition -- fastest but lowest recall
* `nprobes = num_partitions`: Scan all partitions -- equivalent to brute-force search
* Recommended starting point: `nprobes = num_partitions // 10` (scan 10% of partitions)

***

## 6. Supported Distance Metrics

Arrow Lake supports three distance metrics via the `DistanceMetric` enum:

```python
from arrow_lake.config import DistanceMetric, VectorSearchConfig

# Cosine similarity — directional similarity, range [0, 2], lower is more similar
config = VectorSearchConfig(metric=DistanceMetric.COSINE)

# L2 distance — Euclidean distance, range [0, +inf), lower is more similar
config = VectorSearchConfig(metric=DistanceMetric.L2)

# Dot product — for normalized vectors, higher is more similar
config = VectorSearchConfig(metric=DistanceMetric.DOT)
```

| Metric   | Use Case                                     | Range        | Better When |
| -------- | -------------------------------------------- | ------------ | ----------- |
| `cosine` | Text semantic search, RAG                    | \[0, 2]      | Lower       |
| `l2`     | Image feature search, recommendation systems | \[0, +inf)   | Lower       |
| `dot`    | Pre-normalized vectors, contrastive learning | (-inf, +inf) | Higher      |

Choosing a metric: use `cosine` when unsure (insensitive to vector length); use `dot` for already-normalized vectors (fastest computation); use `l2` when spatial distance is meaningful.

***

## 7. Index Management

### 7.1 Query Index Information

```python
from arrow_lake import Lake

lake = Lake(base_uri="./data")

# Get info for a specific vector index
info = lake.get_vector_index_info("aigc_articles", vector_column="text_embedding")
if info is None:
    print("No vector index found; brute-force search will be used")
else:
    print(f"Index: {info.index_type}, metric: {info.distance_type}")
    print(f"Indexed: {info.num_indexed_rows}, unindexed: {info.num_unindexed_rows}")
```

### 7.2 List All Indexes

```python
# List all vector indexes on a dataset
indexes = lake.list_vector_indexes("aigc_articles")
for idx in indexes:
    print(f"  {idx.index_type} on {idx.columns}, metric={idx.distance_type}")
```

### 7.3 Rebuild an Index

Rebuilding drops the existing index and creates a new one with updated parameters:

```python
# Rebuild with the same parameters (useful after data changes)
info = lake.rebuild_vector_index("aigc_articles", vector_column="text_embedding")

# Rebuild with new parameters
info = lake.rebuild_vector_index(
    "aigc_articles",
    metric="cosine",
    vector_column="text_embedding",
    index_type="IVF_PQ",
    num_partitions=512,
    num_sub_vectors=24,              # 24 recommended for 1024-dim
)
print(f"Rebuilt: {info.index_type}, {info.num_indexed_rows} rows")
```

### 7.4 Delete an Index

```python
# Delete a vector index by name
lake.delete_vector_index("aigc_articles", "aigc_articles_text_embedding_idx")
```

### 7.5 FTS Index Management

```python
# Delete the full-text search index
lake.delete_fts_index("aigc_articles")

# Get FTS index information
fts_info = lake.get_fts_index_info("aigc_articles")
if fts_info is not None:
    print(f"FTS index: {fts_info['name']}, columns: {fts_info['columns']}")
```

***

## 8. REST API

```bash
# Create a vector index
curl -X POST http://localhost:8000/api/v1/datasets/docs/index/vector \
  -H "Content-Type: application/json" \
  -d '{"metric": "cosine", "index_type": "IVF_PQ", "vector_column": "text_embedding"}'

# Vector search
curl -X POST http://localhost:8000/api/v1/datasets/docs/search/vector \
  -H "Content-Type: application/json" \
  -d '{"query_vector": [0.1, 0.2, ...], "top_k": 10, "metric": "cosine"}'

# Embed text (compute embeddings via the API)
curl -X POST http://localhost:8000/api/v1/embed/text \
  -H "Content-Type: application/json" \
  -d '{"texts": ["efficient attention mechanism", "low-rank adaptation"]}'
```

| Endpoint                        | Method | Description           |
| ------------------------------- | ------ | --------------------- |
| `/{name}/index/vector`          | POST   | Create a vector index |
| `/{name}/search/vector`         | POST   | Vector similarity search |
| `/embed/text`                   | POST   | Compute text embeddings |
| `/embed/image`                  | POST   | Compute image embeddings |

***

## 9. Complete Example: End-to-end Vector Search

This example ingests the `aigc_articles` AIGC article library, builds an IVF_PQ index, and runs a filtered similarity search — the same `aigc_articles` corpus threaded through chapters 04–09.

```python
import numpy as np
from arrow_lake import Lake
from arrow_lake.config import ArrowLakeConfig, StorageConfig, StorageBackend

# 1. Configuration
config = ArrowLakeConfig()
config.storage = StorageConfig(backend=StorageBackend.LOCAL, base_uri="./data")
lake = Lake(base_uri="./data", config=config)

# 2. Ingest the AIGC article library (144 rows; text_content → text_embedding automatically)
report = lake.ingest("aigc_articles", ["datas/reports/aigc_articles.csv"])
print(f"Ingested {report.total_rows} rows")

# 3. Create an IVF_PQ vector index (the corpus has ≥256 rows, so PQ training is valid)
info = lake.create_vector_index("aigc_articles", metric="cosine", index_type="IVF_PQ")
print(f"Index created: {info.index_type}, {info.num_indexed_rows} rows")

# 4. Search — semantically similar articles, filtered to the 大语言模型 category
query_vec = np.random.randn(1024).tolist()  # replace with a real query embedding
result = lake.search("aigc_articles", query_vec, top_k=3, where="category = '大语言模型'")

# 5. Output results
for row in result.table.to_pylist():
    print(f"  [{row['_distance']:.4f}] {row['title']}")

# 6. Cleanup
lake.shutdown()
```
