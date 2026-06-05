# Full-Text Search (BM25)

> BM25 retrieval powered by LanceDB Tantivy full-text indexing with jieba Chinese tokenization.

***

## 1. Quick Start

```python
"""Minimal full-text search example"""
from arrow_lake import Lake
import pyarrow as pa

lake = Lake(base_uri="./lake_demo")

# Ingest a dataset with text columns
docs = pa.table({
    "id": [1, 2, 3, 4, 5],
    "title": ["Introduction to Machine Learning", "Deep Learning & Neural Networks",
              "NLP in Practice", "Python Data Analysis", "Recommendation Systems Explained"],
    "text_content": [
        "Machine learning is a core branch of AI covering supervised and unsupervised learning",
        "Deep learning enables automatic feature extraction through multi-layer neural networks",
        "NLP leverages deep learning models for text classification, sentiment analysis, and translation",
        "Python offers rich data analysis libraries such as Pandas and NumPy",
        "Recommendation systems combine collaborative filtering and content-based approaches",
    ],
    "category": ["AI", "AI", "AI", "Data", "AI"],
})
lake.create_dataset("docs", docs)

# Create a full-text index (uses jieba Chinese tokenization by default)
lake.create_fts_index("docs", fts_column="text_content")

# Execute a full-text search
result = lake.text_search("docs", query="machine learning", top_k=10)
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
lake.create_fts_index("docs")

# Specify the index column
lake.create_fts_index("docs", fts_column="title")

# Force rebuild an existing index
lake.create_fts_index("docs", fts_column="text_content", replace=True)
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
3. Build a Tantivy BM25 index on that column

***

## 3. Executing Full-Text Searches

```python
# Basic search
result = lake.text_search("docs", query="deep learning models")
print(f"Query: {result.query}, Results: {result.row_count}, Top score: {result.max_score:.4f}")

# Limit the number of results
result = lake.text_search("docs", query="Python", top_k=5)

# Search a specific column
result = lake.text_search("docs", query="recommendation algorithms", fts_column="title")
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
result = lake.text_search("docs", query="natural language processing", top_k=3)
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
lake.create_fts_index("docs")
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

***

## 6. Vector Search vs. Full-Text Search

| Dimension        | Vector Search                                 | Full-Text Search                  |
| ---------------- | --------------------------------------------- | --------------------------------- |
| **Index column** | `float[]` (embedding)                         | `string` (text)                   |
| **Index type**   | IVF-PQ                                        | Tantivy BM25                      |
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
result = lake.text_search("docs", query="deep learning", where="category = 'AI'")

# Numeric filter
result = lake.text_search("docs", query="NLP", where="quality_score > 0.8")

# Combined conditions
result = lake.text_search("docs", query="machine learning",
                          where="category = 'AI' AND year >= 2023")

# OR conditions
result = lake.text_search("docs", query="data analysis",
                          where="category = 'AI' OR category = 'Data'")
```

The `where` clause is safely validated by `validate_where_clause`, which blocks SQL injection and data modification statements. Invalid expressions raise a `QueryError`.

***

## 8. FTS Index Management

```python
from arrow_lake import Lake

lake = Lake(base_uri="./data")

# Delete the FTS index
lake.delete_fts_index("docs")

# Get FTS index information
info = lake.get_fts_index_info("docs")
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
  -d '{"query": "machine learning", "top_k": 10}'
```

| Endpoint                  | Request Model           | Response Model           |
| ------------------------- | ----------------------- | ------------------------ |
| `POST /{name}/index/fts`  | `FtsIndexRequest`       | `FtsIndexResponse`       |
| `POST /{name}/search/fts` | `FullTextSearchRequest` | `FullTextSearchResponse` |
| `POST /embed/text`        | `TextEmbedRequest`      | `EmbeddingResponse`      |
