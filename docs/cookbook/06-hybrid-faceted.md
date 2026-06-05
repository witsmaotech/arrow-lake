# Hybrid Search & Faceted Search

> RRF fusion of vector search + full-text search for hybrid retrieval, DuckDB CUBE for
> faceted navigation, and weighted RRF for multi-column ensemble search.

***

## 1. Hybrid Search

```python
"""Minimal hybrid search example"""
from arrow_lake import Lake
import pyarrow as pa
import numpy as np

lake = Lake(base_uri="./lake_demo")

# Ingest a dataset with both text and embeddings
np.random.seed(42)
titles = ["Lightweight Running Shoes", "Pro Basketball High-Tops", "Casual Leather Shoes",
          "Trail Running Shoes - Grip", "Summer Breathable Sandals", "Women's Yoga Sneakers"]
embeddings = np.random.randn(len(titles), 128).astype(np.float32).tolist()

products = pa.table({
    "id": list(range(1, len(titles) + 1)),
    "title": titles,
    "category": ["running", "basketball", "casual", "running", "casual", "fitness"],
    "brand": ["Nike", "Adidas", "Clarks", "Salomon", "Teva", "Lululemon"],
    "text_content": [f"{t}, brand: {b}" for t, b in zip(titles,
        ["Nike", "Adidas", "Clarks", "Salomon", "Teva", "Lululemon"])],
    "text_embedding": embeddings,
})
lake.create_dataset("products", products)

# Build indexes
lake.create_vector_index("products", vector_column="text_embedding")
lake.create_fts_index("products", fts_column="text_content")

# Hybrid search
query_vec = np.random.randn(128).astype(np.float32).tolist()
result = lake.hybrid_search(
    "products",
    query_vector=query_vec,
    query_text="lightweight running shoes",
    top_k=5,
)
print(f"Hybrid search -> {result.row_count} results (rrf_k={result.rrf_k})")
for i in range(result.table.num_rows):
    doc_id = result.table.column("id")[i].as_py()
    title = result.table.column("title")[i].as_py()
    score = result.table.column("_rrf_score")[i].as_py()
    print(f"  [{doc_id}] {title}  (rrf_score={score:.6f})")

lake.shutdown()
```

***

## 2. How RRF Fusion Works

Reciprocal Rank Fusion is implemented in `HybridSearchBridge._rrf_fuse()`:

```text
score(doc) = SUM( 1 / (rank(doc, list_i) + k) )
```

* `rank(doc, list_i)`: the document's rank in the i-th ranked list (0-indexed)
* `k`: smoothing constant, default 60 (the value recommended in the original paper)

```text
Vector search top 3:         Full-text search top 3:
  rank 0: Trail Runners        rank 0: Lightweight Running Shoes
  rank 1: Hiking Boots         rank 1: Kids Cushion Runners
  rank 2: Basketball Shoes     rank 2: Summer Breathable Sandals

           +-- RRF fusion (k=60) --+
                      |
  rank 0: Lightweight Running Shoes  (1/(0+60) + 1/(0+60) = 0.0333)
  rank 1: Trail Runners             (1/(0+60) + 0 = 0.0167)
```

| rrf\_k           | Effect                              | Recommended For           |
| ---------------- | ----------------------------------- | ------------------------- |
| 30-50            | Higher weight on top-ranked results | Emphasis on exact matches |
| **60** (default) | Balanced fusion                     | General-purpose use       |
| 100-200          | Ranking differences are flattened   | Emphasis on diversity     |

***

## 3. Hybrid Search API

```python
def hybrid_search(
    self,
    dataset_name: str,
    query_vector: list[float],          # Query embedding vector (positional)
    query_text: str,                    # Query text for FTS (positional)
    *,
    top_k: int | None = None,           # Number of results
    vector_column: str = "text_embedding",  # Vector column name
    fts_column: str | None = None,      # FTS column name
    where: str | None = None,           # Metadata filter
    version: int | None = None,         # Dataset version for time-travel queries
) -> HybridSearchResult: ...
```

### Return Type: HybridSearchResult

```python
@dataclass(frozen=True)
class HybridSearchResult:
    table: pa.Table               # Result table with _rrf_score column
    row_count: int                # Number of results
    query_text: str               # FTS query text
    query_vector_dim: int         # Vector dimensionality
    top_k: int                    # Maximum results requested
    rrf_k: int                    # RRF constant
    max_rrf_score: float | None   # Highest RRF score
```

### Configuration

```python
from arrow_lake.config import HybridSearchConfig

config = HybridSearchConfig(
    rrf_k=60,                    # RRF smoothing constant
    default_top_k=10,             # Final number of results
    vector_top_k_multiplier=3,    # Vector candidate pool = top_k * 3
    fts_top_k_multiplier=3,       # FTS candidate pool = top_k * 3
)
```

Arrow Lake automatically selects the execution path: it prefers DuckDB native
`lance_hybrid_search()`, falling back to sub-bridges that search independently
and then fuse the results.

***

## 4. Faceted Search

`faceted_search` builds on vector search by computing per-dimension facet counts
via DuckDB `GROUP BY CUBE`. It is designed for e-commerce and content platform
category navigation.

```python
query_vec = encoder.embed_text("running shoes")

result = lake.faceted_search(
    "products",
    query_vector=query_vec,
    facets=["category", "brand"],
    top_k=10,
)

# Search results
print(f"Search results: {result.row_count}")
for i in range(result.table.num_rows):
    print(f"  - {result.table.column('title')[i].as_py()}")

# Facet counts
facet_dict: dict[str, dict[str, int]] = {}
for f in result.facets:
    facet_dict.setdefault(f.name, {})[f.value] = f.count

for dim, values in facet_dict.items():
    print(f"\n  [{dim}]")
    for val, cnt in sorted(values.items(), key=lambda x: -x[1]):
        print(f"    {val}: {cnt}")
# Output:
#   [category]
#     running: 2
#     casual: 2
#     ...
#   [brand]
#     Nike: 1
#     Adidas: 1
#     ...
```

### API Signature

```python
def faceted_search(
    self,
    dataset_name: str,
    query_vector: list[float],       # Query embedding vector (positional)
    *,
    facets: list[str] | None = None, # Facet dimension column names
    top_k: int = 10,
    vector_column: str = "embedding",
    where: str | None = None,        # Metadata filter
    version: int | None = None,      # Dataset version for time-travel queries
) -> FacetedSearchResult: ...
```

### Return Types

```python
@dataclass(frozen=True)
class FacetCount:
    name: str     # Facet dimension (e.g. "category")
    value: str    # Facet value (e.g. "running")
    count: int    # Record count

@dataclass(frozen=True)
class FacetedSearchResult:
    table: pa.Table               # Vector search results
    row_count: int
    facets: list[FacetCount]      # List of facet counts
    total_facets: int             # Total number of facet values
    query_vector_dim: int
    top_k: int
```

### Frontend Integration

The core use case for faceted search is "search results + category filter navigation":

```python
# 1. User searches -> display facet options (sidebar)
result = lake.faceted_search("products", query_vector=query_vec,
                              facets=["category", "brand"])

# 2. User clicks "running" filter -> facet counts update automatically
result = lake.faceted_search("products", query_vector=query_vec,
                              facets=["category", "brand"],
                              where="category = 'running'")
```

***

## 5. Ensemble Multi-Column Search

`ensemble_search` runs vector searches across multiple embedding columns and fuses
them with weighted RRF. It is designed for multi-modal embedding scenarios.

```python
# Assume "products" has both text_embedding and image_embedding columns
result = lake.ensemble_search(
    "products",
    query_vector=query_vec,
    columns=["text_embedding", "image_embedding"],
    weights={"text_embedding": 0.7, "image_embedding": 0.3},
    top_k=10,
)
print(f"Columns searched: {result.columns_searched}, Fusion: {result.fusion_method}")

for i in range(result.table.num_rows):
    doc_id = result.table.column("id")[i].as_py()
    score = result.table.column("_ensemble_score")[i].as_py()
    title = result.table.column("title")[i].as_py()
    print(f"  [{doc_id}] {title}  (score={score:.6f})")
```

### API Signature

```python
def ensemble_search(
    self,
    dataset_name: str,
    query_vector: list[float],              # Query vector (same dim for all columns)
    *,
    columns: list[str] | None = None,       # Embedding column names
    weights: dict[str, float] | None = None,# Per-column weights
    top_k: int | None = None,               # Number of results
    where: str | None = None,               # Metadata filter
    version: int | None = None,             # Dataset version for time-travel queries
) -> EnsembleSearchResult: ...
```

When `columns` is not specified, it auto-detects all `fixed_size_list` columns
whose dimensionality matches the query vector.

Weighted RRF formula: `score(doc) = SUM( weight_i / (rank(doc, list_i) + k) )`

***

## 6. Search Strategy Selection Guide

```text
Need to search?
  |
  +-- Exact keyword match    -----> text_search()
  +-- Semantic similarity    -----> search() (vector)
  +-- Category filter nav    -----> faceted_search()
  +-- Multiple embeddings    -----> ensemble_search()
  +-- Semantic + keywords    -----> hybrid_search()
```

### Strategy Comparison

| Strategy         | API                      | Input                  | Best For                                         |
| ---------------- | ------------------------ | ---------------------- | ------------------------------------------------ |
| Vector search    | `lake.search()`          | Embedding vector       | Semantic retrieval, RAG, similar recommendations |
| Full-text search | `lake.text_search()`     | Text string            | Exact keyword matches, identifier lookup         |
| Hybrid search    | `lake.hybrid_search()`   | Vector + text          | Balancing semantic and keyword relevance         |
| Faceted search   | `lake.faceted_search()`  | Vector + facet columns | E-commerce / content category navigation         |
| Ensemble         | `lake.ensemble_search()` | Vector + multi-column  | Multi-modal embedding fusion                     |

### Scenario Recommendations

| Use Case                  | Recommended Strategy | Reason                                    |
| ------------------------- | -------------------- | ----------------------------------------- |
| Document Q\&A (RAG)       | `hybrid_search`      | Semantic + keyword improves recall        |
| E-commerce product search | `faceted_search`     | Vector recall + brand category navigation |
| Log / error code search   | `text_search`        | Exact match on codes and identifiers      |
| Multi-modal search        | `ensemble_search`    | Fuses text and image embeddings           |
| Technical docs site       | `hybrid_search`      | Title exact match + content semantics     |

***

## 7. REST API Reference

```bash
# Hybrid search
curl -X POST http://localhost:8000/api/v1/datasets/products/search/hybrid \
  -H "Content-Type: application/json" \
  -d '{"query_vector": [0.1, 0.2], "query_text": "lightweight running shoes", "top_k": 10}'

# Faceted search
curl -X POST http://localhost:8000/api/v1/datasets/products/search/faceted \
  -H "Content-Type: application/json" \
  -d '{"query_vector": [0.1, 0.2], "facets": ["category", "brand"]}'

# Ensemble search
curl -X POST http://localhost:8000/api/v1/datasets/products/search/ensemble \
  -H "Content-Type: application/json" \
  -d '{"query_vector": [0.1, 0.2], "columns": ["text_embedding", "image_embedding"],
       "weights": {"text_embedding": 0.7, "image_embedding": 0.3}}'
```

| Endpoint                       | Request Model           | Response Model           |
| ------------------------------ | ----------------------- | ------------------------ |
| `POST /{name}/search/hybrid`   | `HybridSearchRequest`   | `HybridSearchResponse`   |
| `POST /{name}/search/faceted`  | `FacetedSearchRequest`  | `FacetedSearchResponse`  |
| `POST /{name}/search/ensemble` | `EnsembleSearchRequest` | `EnsembleSearchResponse` |
