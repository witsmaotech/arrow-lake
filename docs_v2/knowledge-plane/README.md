# Knowledge Plane

> You are an **AI/ML engineer** who builds retrieval pipelines, tunes embedding quality, and delivers RAG-powered applications over structured and unstructured data.

## Knowledge Flow

```
Data Plane (Lance datasets)
  --> Chunk & Embed (sentence-transformers / OpenAI)
    --> Index (Vector / FTS / Hybrid)
      --> Retrieve (semantic / keyword / rerank)
        --> Generate (LLM with context)
          --> Serve (multi-turn conversation / GraphRAG)
```

**5 search modes**: vector similarity, full-text (BM25), hybrid (RRF fusion), faceted (count + vector), ensemble (multi-column weighted RRF).

**5 LLM providers**: OpenAI, Anthropic, vLLM, Ollama, DeepSeek — unified via `BaseLLMProvider`.

---

## Index Setup

Before searching, create indexes on your dataset.

### Vector Index

```bash
# CLI — create IVF-PQ vector index
arrow-lake index vector my_dataset --column text_embedding --metric cosine --type IVF_PQ

# View index info
arrow-lake index info-vector my_dataset --column text_embedding

# Rebuild (after data changes)
arrow-lake index rebuild-vector my_dataset --column text_embedding

# List all vector indexes
arrow-lake index list-vector my_dataset
```

```python
# SDK
info = lake.create_vector_index("my_dataset", metric="cosine",
                                vector_column="text_embedding",
                                index_type="IVF_PQ",
                                num_partitions=256, num_sub_vectors=16)
```

**Index types**: `IVF_PQ` (default, memory-efficient), `IVF_FLAT`, `IVF_HNSW_PQ` (higher recall).

### Full-Text Index

```bash
# CLI — create Tantivy FTS index (jieba CJK tokenizer)
arrow-lake index fts my_dataset --column text_content

# Info / delete
arrow-lake index info-fts my_dataset
arrow-lake index delete-fts my_dataset
```

```python
# SDK
lake.create_fts_index("my_dataset", fts_column="text_content")
```

---

## Search

### Vector Similarity

```bash
arrow-lake search vector my_dataset --query "machine learning" --top-k 5 --column text_embedding
```

```python
result = lake.search("my_dataset", query_vector, top_k=10,
                     vector_column="text_embedding",
                     where="category = 'ml'",    # metadata filter
                     nprobes=20)                 # ANN probe count
# result.row_count, result.table (PyArrow table with _distance column)
```

### Full-Text Search (BM25)

```bash
arrow-lake search fts my_dataset --query "knowledge graph" --top-k 10 --column text_content
```

```python
result = lake.text_search("my_dataset", "knowledge graph",
                          top_k=10, fts_column="text_content",
                          where="year > 2023")
```

### Hybrid Search (Vector + FTS via RRF)

```bash
arrow-lake search hybrid my_dataset --query "transformer architecture" \
  --top-k 10 --vector-column text_embedding --fts-column text_content
```

```python
result = lake.hybrid_search("my_dataset",
    query_vector=vec, query_text="transformer architecture",
    top_k=10, vector_column="text_embedding", fts_column="text_content")
```

**RRF formula**: `score = sum(1 / (k + rank_i))` across vector and FTS result lists. `k=60` by default.

### Faceted Search (Vector + Facet Counts)

```bash
arrow-lake search faceted my_dataset --query "deep learning" \
  --facets category,year --top-k 10
```

```python
result = lake.faceted_search("my_dataset", query_vector,
                             facets=["category", "year"], top_k=10)
# result.facets = {"category": {"ml": 42, "dl": 18}, "year": {"2024": 30}}
# result.vector_results = VectorSearchResult(...)
```

### Ensemble Search (Multi-Column Weighted RRF)

```bash
arrow-lake search ensemble my_dataset --query "neural networks" \
  --columns title_embedding,abstract_embedding --weights '{"title_embedding": 0.7, "abstract_embedding": 0.3}'
```

```python
result = lake.ensemble_search("my_dataset", query_vector,
    columns=["title_embedding", "abstract_embedding"],
    weights={"title_embedding": 0.7, "abstract_embedding": 0.3})
```

### Decision: Which Search Mode?

| Use Case | Mode | Reason |
|----------|------|--------|
| Semantic meaning, cross-language | Vector | Embedding captures semantics |
| Exact keyword, CJK terms | FTS | BM25 with jieba tokenization |
| Best of both | Hybrid | RRF combines semantic + keyword |
| Need category breakdown | Faceted | Vector results + facet counts |
| Multiple embedding columns | Ensemble | Weighted RRF across columns |

---

## RAG Pipeline

End-to-end: retrieve -> rerank -> generate. Supports streaming, multi-turn sessions, feedback, and batch queries.

### Single Query

```bash
# CLI
arrow-lake rag query my_dataset "What is retrieval-augmented generation?" \
  --top-k 5 --strategy hybrid --session-id my-session
```

```python
# SDK (async)
response = await lake.rag_query(
    "What is RAG?", "my_dataset",
    top_k=5, strategy="hybrid", session_id="my-session")
print(response.answer)
print(response.citations)  # source chunks with scores
```

### Streaming

```bash
arrow-lake rag stream my_dataset "Explain vector databases" --strategy hybrid
```

```python
async for chunk in lake.rag_query_stream("Explain vector databases", "my_dataset",
                                          strategy="hybrid"):
    print(chunk, end="", flush=True)
```

### Multi-Turn Sessions

Sessions maintain conversation history in Redis, with configurable history injection:

```python
# First turn
r1 = await lake.rag_query("What is Lance?", "docs", session_id="s1")

# Second turn (history injected automatically)
r2 = await lake.rag_query("How does it compare to Parquet?", "docs", session_id="s1")

# View history
history = lake.rag_get_history("s1")

# Provide feedback
lake.rag_feedback("s1", turn_id=1, rating="positive", comment="Accurate answer")
```

### Batch Queries

```python
responses = await lake.rag_batch_query(
    ["What is RAG?", "How does FTS work?"], "docs",
    strategy="hybrid", concurrency=5)
```

### Extraction Mode

```python
# Extract structured information from a dataset
response = await lake.rag_extract("docs",
    text_column="text_content", top_k=20,
    template_name="extraction")
```

### Query Transformation & Reranking

Configured via environment variables:

```bash
# Reranking (CrossEncoder or LLM-based)
ARROW_LAKE__RAG__RERANKER=cross_encoder
ARROW_LAKE__RAG__RERANKER_MODEL=cross-encoder/ms-marco-MiniLM-L-6-v2
ARROW_LAKE__RAG__RERANKER_TOP_N=5

# Query transformation: identity, hyde, multi_query
ARROW_LAKE__RAG__QUERY_TRANSFORM=hyde
ARROW_LAKE__RAG__HYDE_MAX_TOKENS=128
ARROW_LAKE__RAG__MULTI_QUERY_VARIANTS=3

# History injection
ARROW_LAKE__RAG__HISTORY_INJECTION_ENABLED=true
ARROW_LAKE__RAG__HISTORY_BUDGET_RATIO=0.3
ARROW_LAKE__RAG__HISTORY_MAX_TURNS=10
```

### LLM Providers

| Provider | Config | Default Base URL |
|----------|--------|-----------------|
| OpenAI | `ARROW_LAKE__LLM__PROVIDER=openai` | `https://api.openai.com/v1` |
| Anthropic | `ARROW_LAKE__LLM__PROVIDER=anthropic` | `https://api.anthropic.com` |
| vLLM | `ARROW_LAKE__LLM__PROVIDER=vllm` | `http://localhost:8000/v1` |
| Ollama | `ARROW_LAKE__LLM__PROVIDER=ollama` | `http://localhost:11434/v1` |
| DeepSeek | `ARROW_LAKE__LLM__PROVIDER=deepseek` | `https://api.deepseek.com` |

All providers support streaming. Circuit breaker + retry (3 attempts, exponential backoff) built in.

---

## Knowledge Graph & GraphRAG

When `ARROW_LAKE__HUGEGRAPH__HOST` is set, the RAG pipeline automatically augments retrieval with HugeGraph traversal results.

### Build & Query

```bash
# Build KG from dataset
arrow-lake kg build my_dataset

# Check build status
arrow-lake kg status <task_id>

# Graph statistics
arrow-lake kg stats

# Gremlin query
arrow-lake kg query "g.V().hasLabel('Entity').limit(10)"

# Get entity neighbors
arrow-lake kg neighbors entity_001
```

### Graph Algorithms

```bash
arrow-lake kg algo pagerank --iterations 20 --damping 0.85
arrow-lake kg algo louvain --resolution 1.0
arrow-lake kg algo wcc
arrow-lake kg algo triangle-count
arrow-lake kg algo degree-centrality
arrow-lake kg algo betweenness-centrality
arrow-lake kg algo k-core --k 3
```

### Graph Traversers

```bash
# Shortest paths
arrow-lake kg traverser all-shortest-paths entity_A entity_B
arrow-lake kg traverser weighted-shortest entity_A entity_B --weight-prop weight

# Neighborhood exploration
arrow-lake kg traverser rays entity_A --max-depth 3
arrow-lake kg traverser rings entity_A --max-degree 100
```

### GraphRAG Fusion

When KG is enabled, RAG results are augmented with graph traversal. The fusion happens automatically:

1. RAG retrieves chunks via vector/FTS/hybrid
2. GraphRAG extracts entities from the query and traverses the knowledge graph
3. Graph context is fused with vector/FTS results via RRF
4. Combined context is sent to the LLM

---

## Embedding

```bash
# Text embedding (default: Qwen3-Embedding-0.6B)
arrow-lake embed text "Hello world" --model Qwen/Qwen3-Embedding-0.6B --source huggingface

# Image embedding
arrow-lake embed image ./photo.jpg
```

```python
# Batch embed and add to dataset
count = lake.embed_and_add("my_dataset",
    text_column="text_content",
    embedding_column="text_embedding",
    batch_size=256)
```

Configuration:

```bash
ARROW_LAKE__EMBEDDING__MODEL_SOURCE=huggingface    # or modelscope
ARROW_LAKE__EMBEDDING__API_BASE=                    # optional, for remote models
```

---

## Next Steps

- **Need to ingest raw data first?** -> [Data Plane](../data-plane/README.md) to load and index source files.
- **Scaling to production?** -> [Compute Plane](../compute-plane/README.md) for GPU management, HPA, and OTel tracing.
- **Architecture details?** -> [Architecture](../concepts/architecture.md) for how Knowledge and Data planes share the Kernel layer.
