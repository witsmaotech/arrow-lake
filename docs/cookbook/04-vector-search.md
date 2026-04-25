# Vector Search and Indexing

Vector search is the core retrieval capability in Arrow Lake. This guide covers the full pipeline from data ingestion and embedding generation through index creation to similarity search.

```python
from arrow_lake import Lake
from arrow_lake.config import ArrowLakeConfig, StorageConfig, StorageBackend

# Initialize a Lake instance
config = ArrowLakeConfig()
config.storage = StorageConfig(backend=StorageBackend.LOCAL, base_uri="./data")
lake = Lake(base_uri="./data", config=config)

# 1. Ingest data — text columns automatically generate embeddings
report = lake.ingest("docs", ["article.txt"])
print(f"Ingested {report.total_rows} rows")

# 2. Create a vector index
from arrow_lake.config import DistanceMetric, VectorIndexType
info = lake.create_vector_index("docs", metric="cosine", index_type="IVF_PQ")
print(f"Index type: {info.index_type}, distance metric: {info.distance_type}")
print(f"Indexed rows: {info.num_indexed_rows}")

# 3. Execute a vector search
import numpy as np
query_vec = np.random.randn(1024).tolist()  # Replace with a real query vector
result = lake.search("docs", query_vector=query_vec, top_k=5)
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

Before running efficient searches, you need to create a vector index. Arrow Lake supports three index types:

```python
from arrow_lake import Lake

lake = Lake(base_uri="./data")

# --- Basic usage: defaults ---
info = lake.create_vector_index("docs")
# Defaults: metric=cosine, index_type=IVF_PQ

# --- Specify metric and index type ---
info = lake.create_vector_index(
    "docs",
    metric="cosine",          # Distance metric: cosine / l2 / dot
    index_type="IVF_PQ",      # Index type
)

# --- Fine-grained index parameter control ---
info = lake.create_vector_index(
    "docs",
    metric="l2",
    vector_column="text_embedding",  # Vector column name
    index_type="IVF_FLAT",           # More precise but slower
    num_partitions=512,              # Number of IVF partitions
    num_sub_vectors=32,              # PQ sub-vectors (IVF_PQ only)
    replace=True,                    # Replace existing index
)
```

`create_vector_index` returns an `IndexInfo` object:

```python
info = lake.create_vector_index("docs", metric="cosine", index_type="IVF_PQ")
print(f"Index: {info.index_type}, metric: {info.distance_type}")
print(f"Indexed: {info.num_indexed_rows}, unindexed: {info.num_unindexed_rows}")
print(f"Columns: {info.columns}")
```

### Index Type Comparison

| Type          | Description                               | Best For                          | Notes                               |
| ------------- | ----------------------------------------- | --------------------------------- | ----------------------------------- |
| `IVF_PQ`      | IVF inverted index + product quantization | Large datasets (>10K rows)        | Default choice, low memory usage    |
| `IVF_FLAT`    | IVF inverted index + exact distance       | Medium datasets needing precision | No quantization loss, higher memory |
| `IVF_HNSW_PQ` | IVF + HNSW + PQ                           | Large datasets + low latency      | Highest build cost                  |

***

## 3. Vector Similarity Search

Use `lake.search()` to perform vector similarity search. The system uses a dual-path strategy: it prefers DuckDB's native `lance_vector_search()`, falling back to the LanceDB SDK on failure.

```python
from arrow_lake import Lake
import numpy as np

lake = Lake(base_uri="./data")

# Prepare a query vector (dimension must match the dataset's vector column)
query_vector = np.random.randn(1024).tolist()

# Basic search
result = lake.search("docs", query_vector=query_vector, top_k=5)

# With explicit metric
result = lake.search(
    "docs",
    query_vector=query_vector,
    top_k=10,
    metric="cosine",
    vector_column="text_embedding",
)

# With metadata filtering
result = lake.search(
    "docs",
    query_vector=query_vector,
    top_k=5,
    where="category = 'tech'",
)
```

Search returns a `VectorSearchResult` (containing a PyArrow Table):

```python
result = lake.search("docs", query_vector=query_vector, top_k=5)
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
result = lake.search("docs", query_vector=qv, where="category = 'AI'")

# Numeric range + compound condition
result = lake.search("docs", query_vector=qv, where="category = 'AI' AND year >= 2023")

# IN operator
result = lake.search("docs", query_vector=qv, where="status IN ('published', 'reviewed')")

# String pattern matching
result = lake.search("docs", query_vector=qv, where="title LIKE '%machine learning%'")
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
info = lake.create_vector_index("docs", num_partitions=None)
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
result = lake.search("docs", query_vector=qv, top_k=10, nprobes=5)

# Balanced mode (default)
result = lake.search("docs", query_vector=qv, top_k=10, nprobes=20)

# High-recall search
result = lake.search("docs", query_vector=qv, top_k=10, nprobes=128)

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

## 7. Querying Index Information

```python
from arrow_lake import Lake
from arrow_lake.query.vector import VectorSearchBridge

lake = Lake(base_uri="./data")
info = lake.create_vector_index("docs", metric="cosine")
print(info)
# IndexInfo(name='...', index_type='IVF_PQ', distance_type='cosine', ...)

# Query an existing index via the low-level bridge
bridge = VectorSearchBridge(lake._get_storage())
info = bridge.get_index_info("docs", vector_column="text_embedding")
if info is None:
    print("No vector index found; brute-force search will be used")
```

***

## 8. Complete Example: Vector Search from Scratch

```python
import pyarrow as pa
import numpy as np
from arrow_lake import Lake
from arrow_lake.config import ArrowLakeConfig, StorageConfig, StorageBackend

# 1. Configuration
config = ArrowLakeConfig()
config.storage = StorageConfig(backend=StorageBackend.LOCAL, base_uri="./demo_data")
lake = Lake(base_uri="./demo_data", config=config)

# 2. Prepare data (simulated embeddings)
texts = ["Introduction to Machine Learning", "Deep Learning and Neural Networks",
         "Natural Language Processing", "Computer Vision Fundamentals",
         "Reinforcement Learning Principles"]
vectors = np.random.randn(5, 1024).tolist()

table = pa.table({
    "text_content": texts,
    "text_embedding": vectors,
    "category": ["AI", "AI", "AI", "AI", "AI"],
    "year": [2024, 2024, 2023, 2023, 2024],
})

# 3. Write the dataset
lake.create_dataset("articles", table)

# 4. Create an index
info = lake.create_vector_index("articles", metric="cosine", index_type="IVF_PQ")
print(f"Index created: {info.index_type}, {info.num_indexed_rows} rows")

# 5. Search
query_vec = np.random.randn(1024).tolist()
result = lake.search("articles", query_vector=query_vec, top_k=3, where="year = 2024")

# 6. Output results
for row in result.table.to_pylist():
    print(f"  [{row['_distance']:.4f}] {row['text_content']}")

# 7. Cleanup
lake.shutdown()
```
