# Full-Text Search (BM25)

> BM25 retrieval powered by LanceDB native FTS (ICU) full-text indexing with jieba Chinese tokenization.

> **Running dataset.** We continue with the `aigc_articles` AIGC article library introduced in [04 - Vector Search](./04-vector-search.md) — now indexed for keyword (BM25) retrieval instead of semantic vectors.

***

## 1. Quick Start

```python
"""Minimal full-text search example"""
from arrow_lake import Lake
import pyarrow as pa

lake = Lake(base_uri="./lake_demo")

# Ingest our aigc_articles AIGC article library (FTS needs no vector column)
import pyarrow.csv as pacsv
aigc_articles = pacsv.read_csv("datas/reports/aigc_articles.csv")
lake.create_dataset("aigc_articles", aigc_articles)

# Create a full-text index (uses jieba Chinese tokenization by default)
lake.create_fts_index("aigc_articles", fts_column="text_content")

# Execute a full-text search
result = lake.text_search("aigc_articles", query="attention mechanism", top_k=10)
for i in range(result.table.num_rows):
    doc_id = result.table.column("id")[i].as_py()
    score = result.table.column("_score")[i].as_py()
    title = result.table.column("title")[i].as_py()
    print(f"  [{doc_id}] {title}  (score={score:.4f})")

lake.shutdown()
```

***

## 2. Creating FTS Indexes

```python
# Create index on the default column (text_content)
lake.create_fts_index("aigc_articles")

# Specify the index column
lake.create_fts_index("aigc_articles", fts_column="title")

# Force rebuild an existing index
lake.create_fts_index("aigc_articles", fts_column="text_content", replace=True)
```

### API Signature

```python
def create_fts_index(
    self,
    dataset_name: str,
    *,
    fts_column: str | None = None,   # Text column name, defaults from config
    replace: bool = True,             # Whether to replace an existing index
) -> None: ...
```

When `tokenizer_type` is `"jieba"` (the default), `create_index` will:

1. Call `segment_text()` to tokenize each row of text
2. Write the tokenized results into a `_fts_segmented` column
3. Build a native FTS (ICU) BM25 index on that column

***

## 3. Executing Full-Text Searches

```python
# Basic search
result = lake.text_search("aigc_articles", query="retrieval augmented generation")
print(f"Query: {result.query}, Results: {result.row_count}, Top score: {result.max_score:.4f}")

# Limit the number of results
result = lake.text_search("aigc_articles", query="diffusion", top_k=5)

# Search a specific column
result = lake.text_search("aigc_articles", query="attention", fts_column="title")
```

### API Signature

```python
def text_search(
    self,
    dataset_name: str,
    query: str,
    *,
    top_k: int | None = None,      # Number of results (defaults from config)
    fts_column: str | None = None,  # Column name to search
    where: str | None = None,       # Metadata filter expression
    version: int | None = None,     # Dataset version for time-travel queries
    offset: int = 0,                # Number of results to skip (pagination)
) -> FullTextSearchResult: ...
```

### Async Full-Text Search

```python
# Async variant (v1.8.0 #17): keeps the event loop responsive for concurrent async handlers
result = await lake.text_search_async("aigc_articles", query="transformer", top_k=10)
```

`text_search_async` has the same signature as `text_search`. It is wrapped via
`asyncio.to_thread` (lancedb has no native async FTS path, so it is delegated
non-blockingly) and returns the same `FullTextSearchResult`.

### Return Type: FullTextSearchResult

```python
@dataclass(frozen=True)
class FullTextSearchResult:
    table: pa.Table           # Arrow table containing a _score relevance column
    row_count: int            # Number of results returned
    query: str                # The search query string
    top_k: int                # Maximum number of results requested
    fts_column: str           # The text column that was searched
    max_score: float | None   # Highest relevance score
```

### Iterating Over Results

```python
result = lake.text_search("aigc_articles", query="low-rank adaptation", top_k=3)
ids = result.table.column("id").to_pylist()
scores = result.table.column("_score").to_pylist()
titles = result.table.column("title").to_pylist()
for doc_id, title, score in zip(ids, titles, scores):
    print(f"  [{doc_id}] {title}  (score={score:.4f})")

# Convert to Pandas
df = result.table.to_pandas()
```

***

## 4. Chinese Tokenization: jieba Integration

The tokenization logic lives in the `arrow_lake.query._chinese_tokenizer` module.

```python
from arrow_lake.query._chinese_tokenizer import segment_text, segment_query

# At index time: tokenize documents
print(segment_text("自然语言处理利用深度学习模型实现文本分类"))
# "自然 语言 处理 利用 深度 学习 模型 实现 文本 分类"

# At search time: tokenize the query
print(segment_query("深度学习入门"))
# "深度 学习 入门"
```

### Custom Dictionary

```text
# custom_dict.txt — one term per line
机器学习
深度学习
自然语言处理
推荐系统
```

```python
from arrow_lake import Lake
from arrow_lake.config import FullTextSearchConfig

fts_config = FullTextSearchConfig(
    fts_column="text_content",
    tokenizer_type="jieba",
    jieba_user_dict="./custom_dict.txt",
)
lake = Lake(base_uri="./lake", fts=fts_config)
lake.create_fts_index("aigc_articles")
```

***

## 5. Search Configuration

```python
from arrow_lake.config import FullTextSearchConfig

config = FullTextSearchConfig(
    default_top_k=10,          # Default number of results (>= 1)
    fts_column="text_content", # Default indexed text column
    stem=True,                 # Stemming (English: running -> run)
    remove_stop_words=True,    # Remove stop words (the, is, 的，了)
    lower_case=True,           # Convert to lowercase
    tokenizer_type="jieba",    # "jieba" (recommended for Chinese) | "default" (built-in)
    jieba_user_dict=None,      # Path to jieba custom dictionary
    with_position=False,       # Store token positions → enables phrase queries (quoted "..."), larger index
    use_inverted=False,        # v1.7.1: use lance native INVERTED index instead of tantivy (experimental)
)
```

| Parameter           | Type          | Default          | Description               |
| ------------------- | ------------- | ---------------- | ------------------------- |
| `default_top_k`     | `int`         | `10`             | Default number of results |
| `fts_column`        | `str`         | `"text_content"` | Default index column      |
| `stem`              | `bool`        | `True`           | English stemming          |
| `remove_stop_words` | `bool`        | `True`           | Remove stop words         |
| `lower_case`        | `bool`        | `True`           | Convert to lowercase      |
| `tokenizer_type`    | `str`         | `"jieba"`        | `"jieba"` or `"default"`  |
| `jieba_user_dict`   | `str \| None` | `None`           | Path to custom dictionary |
| `with_position`     | `bool`        | `False`          | Store token positions, enables phrase queries (quoted `"..."`); larger index |
| `use_inverted`      | `bool`        | `False`          | v1.7.1: use lance native INVERTED index instead of tantivy (experimental) |

***

## 6. Vector Search vs. Full-Text Search

| Dimension        | Vector Search                                 | Full-Text Search                  |
| ---------------- | --------------------------------------------- | --------------------------------- |
| **Index column** | `float[]` (embedding)                         | `string` (text)                   |
| **Index type**   | IVF-PQ                                        | native FTS (ICU) BM25                      |
| **Matching**     | Semantic similarity (cosine/l2/dot)           | Keyword matching + BM25 scoring   |
| **Query input**  | Embedding vector                              | Natural language string           |
| **Exact match**  | Weak                                          | Strong                            |
| **Fuzzy match**  | Strong                                        | Weak                              |
| **Best for**     | Semantic search, RAG, similar recommendations | Keyword search, identifier lookup |

**Choose vector search** when: semantic retrieval, "find articles about quantum computing", RAG retrieval.
**Choose full-text search** when: exact keywords, error code lookup, terminology search.
**Choose hybrid search** when: you need both -- see [06 - Hybrid & Faceted Search](./06-hybrid-faceted.md)

***

## 7. Metadata Filtering (where parameter)

```python
# Single-condition filter
result = lake.text_search("aigc_articles", query="attention", where="category = '大语言模型'")

# Numeric filter
result = lake.text_search("aigc_articles", query="diffusion", where="word_count > 180")

# Combined conditions
result = lake.text_search("aigc_articles", query="transformer",
                          where="category = '大语言模型' AND year >= 2023")

# OR conditions
result = lake.text_search("aigc_articles", query="reinforcement",
                          where="venue = 'NeurIPS' OR venue = 'ICLR'")
```

The `where` clause is safely validated by `validate_where_clause`, which blocks SQL injection and data modification statements. Invalid expressions raise a `QueryError`.

***

## 8. FTS Index Management

```python
from arrow_lake import Lake

lake = Lake(base_uri="./data")

# Delete the FTS index
lake.delete_fts_index("aigc_articles")

# Get FTS index information
info = lake.get_fts_index_info("aigc_articles")
if info is not None:
    print(f"FTS index: {info['name']}, columns: {info['columns']}")
else:
    print("No FTS index found")
```

***

## 9. REST API

```bash
# Create an FTS index
curl -X POST http://localhost:8000/api/v1/datasets/docs/index/fts \
  -H "Content-Type: application/json" \
  -d '{"fts_column": "text_content", "replace": true}'

# Full-text search
curl -X POST http://localhost:8000/api/v1/datasets/docs/search/fts \
  -H "Content-Type: application/json" \
  -d '{"query": "attention mechanism", "top_k": 10}'
```

| Endpoint                  | Request Model           | Response Model           |
| ------------------------- | ----------------------- | ------------------------ |
| `POST /{name}/index/fts`  | `FtsIndexRequest`       | `FtsIndexResponse`       |
| `POST /{name}/search/fts` | `FullTextSearchRequest` | `FullTextSearchResponse` |
| `POST /embed/text`        | `TextEmbedRequest`      | `EmbeddingResponse`      |
| `POST /embed/image`       | `ImageEmbedRequest`     | `EmbeddingResponse`      |
| `POST /embed/clip-text`   | `ClipTextEmbedRequest`  | `EmbeddingResponse`      |
