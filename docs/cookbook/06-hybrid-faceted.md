# Hybrid Search & Faceted Search

> RRF fusion of vector search + full-text search for hybrid retrieval, DuckDB CUBE for
> faceted navigation, and weighted RRF for multi-column ensemble search.

> **Running dataset.** We continue with the `papers` research library from [04](./04-vector-search.md) / [05](./05-fulltext-search.md) — now combining semantic vector and BM25 retrieval, and slicing it by `category` / `venue` / `year` facets.

***

## 1. Hybrid Search

```python
"""Minimal hybrid search example"""
from arrow_lake import Lake
import numpy as np

lake = Lake(base_uri="./lake_demo")

# Ingest our papers research library (text_content is auto-embedded → text_embedding)
lake.ingest("papers", ["datas/papers/metadata.csv"])

# Build indexes
lake.create_vector_index("papers", vector_column="text_embedding")
lake.create_fts_index("papers", fts_column="text_content")

# Hybrid search (semantic vector + keyword BM25, fused via RRF)
query_vec = np.random.randn(1024).astype(np.float32).tolist()  # replace with a real query embedding
result = lake.hybrid_search(
    "papers",
    query_vector=query_vec,
    query_text="attention mechanism",
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

* `rank(doc, list_i)`: the document's rank in the i-th ranked list (1-indexed)
* `k`: smoothing constant, default 60 (the value recommended in the original paper)

```text
Vector search top 3:                Full-text search top 3:
  rank 1: Attention Is All You Need   rank 1: Attention Is All You Need
  rank 2: FlashAttention              rank 2: FlashAttention
  rank 3: LoRA                        rank 3: BERT

           +-- RRF fusion (k=60) --+
                      |
  rank 1: Attention Is All You Need  (1/(1+60) + 1/(1+60) = 0.0328)
  rank 2: FlashAttention             (1/(1+61) + 1/(1+61) = 0.0323)
  rank 3: LoRA                       (1/(1+62) + 0         = 0.0159)
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
    reranker_type="none",         # Reranker: none / cross_encoder (default none, RRF rough-rank is final)
    reranker_model="BAAI/bge-reranker-v2-m3",  # cross-encoder fine-rank model
)
```

Arrow Lake automatically selects the execution path: it prefers DuckDB native
`lance_hybrid_search()`, falling back to sub-bridges that search independently
and then fuse the results.

> **Reranking is config-driven, not a request parameter**: `reranker_type` / `reranker_model` are set globally in `HybridSearchConfig`; the search endpoint (`POST /{name}/search/hybrid`) accepts no per-request reranker arguments. When set to `cross_encoder`, the `reranker_model` (default `BAAI/bge-reranker-v2-m3`) fine-ranks the RRF rough-ranked results with continuous scores.

***

## 4. Faceted Search

`faceted_search` builds on vector search by computing per-dimension facet counts
via DuckDB `GROUP BY CUBE`. It is designed for e-commerce and content platform
category navigation.

```python
query_vec = encoder.embed_text("attention")

result = lake.faceted_search(
    "papers",
    query_vector=query_vec,
    facets=["category", "venue", "year"],
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
#     NLP: 116
#     Computer Vision: 121
#     ...
#   [venue]
#     JMLR: 92
#     SIGMOD: 91
#     ...
#   [year]
#     2024: 251
#     2023: 249
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
    vector_column: str = "embedding",  # SDK default; pass "text_embedding" for auto-embedded columns
    where: str | None = None,        # Metadata filter
    version: int | None = None,      # Dataset version for time-travel queries
) -> FacetedSearchResult: ...
```

### Return Types

```python
@dataclass(frozen=True)
class FacetCount:
    name: str     # Facet dimension (e.g. "venue")
    value: str    # Facet value (e.g. "NeurIPS")
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
result = lake.faceted_search("papers", query_vector=query_vec,
                              facets=["category", "venue"])

# 2. User clicks "NLP" filter -> facet counts update automatically
result = lake.faceted_search("papers", query_vector=query_vec,
                              facets=["category", "venue"],
                              where="category = 'NLP'")
```

### Scalar Index Acceleration

Building scalar indexes on the facet dimension columns significantly speeds up the `GROUP BY CUBE` aggregation. `FacetedSearchConfig.scalar_index_type_map` auto-selects the index type per column by cardinality (low-cardinality like `category`/`venue`/`year` → `BITMAP`, others → `BTREE`). Build indexes in bulk:

```python
# Build scalar indexes on default facet columns (BTREE/BITMAP per scalar_index_type_map)
lake.create_facet_indexes("papers")
# Or build an index on a single column
lake.create_scalar_index("papers", column="category")
```

***

## 5. Ensemble Multi-Column Search

`ensemble_search` runs vector searches across multiple embedding columns and fuses
them with weighted RRF. It is designed for multi-modal embedding scenarios.

```python
# Hypothetical: if papers also had an image_embedding column (e.g. figure embeddings)
result = lake.ensemble_search(
    "papers",
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

### Multimodal Image Search

CLIP embeddings map text and images into the same vector space, enabling "text-to-image" and "image-to-image" search. `Lake.encode_text_clip()` encodes the query text, producing embeddings in the same space (L2-normalized) as those returned by `POST /api/v1/embed/image`, ready for direct vector search:

```python
# Text -> image embedding, same space as /embed/image
# (requires an image_embedding column, e.g. paper figures embedded via CLIP)
query_vec = lake.encode_text_clip("neural network architecture diagram")
results = lake.search("papers", query_vector=query_vec, vector_column="image_embedding")
```

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
  -d '{"query_vector": [0.1, 0.2], "query_text": "attention mechanism", "top_k": 10}'

# Faceted search
curl -X POST http://localhost:8000/api/v1/datasets/products/search/faceted \
  -H "Content-Type: application/json" \
  -d '{"query_vector": [0.1, 0.2], "facets": ["category", "venue"]}'

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
