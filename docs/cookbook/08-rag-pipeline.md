# RAG Question-Answering Pipeline

Arrow Lake includes a built-in RAG (Retrieval-Augmented Generation) pipeline that
supports multiple retrieval strategies, streaming output, multi-turn conversations,
and knowledge graph augmentation. It returns a `RAGResponse` containing the answer,
cited sources, and performance metrics.

> Prerequisites: install the RAG extra with `pip install arrow-lake[rag]`, configure an
> LLM provider, and ensure the target dataset has a vector index.

***

## 1. Basic RAG Query

`Lake.rag_query()` runs the full retrieve-augment-generate pipeline and returns a
structured answer with citations.

```python
import asyncio
from arrow_lake import Lake

lake = Lake(base_uri="./data")

response = asyncio.run(
    lake.rag_query("What is the core architecture of Arrow Lake?", "docs")
)

print(response.answer)
print(f"Documents retrieved: {response.retrieval_count}")
print(f"Context tokens: {response.context_tokens}")
print(f"Latency: {response.latency_ms} ms")

for citation in response.citations:
    print(
        f"  Source: [{citation.chunk_index}] "
        f"dataset={citation.dataset}, "
        f"row_id={citation.row_id}, "
        f"relevance={citation.score:.2f}"
    )
    print(f"    Excerpt: {citation.text_excerpt[:80]}...")
```

`RAGResponse` field reference:

| Field             | Type                      | Description                         |
| ----------------- | ------------------------- | ----------------------------------- |
| `answer`          | `str`                     | LLM-generated answer text           |
| `citations`       | `tuple[RAGCitation, ...]` | List of cited sources               |
| `retrieval_count` | `int`                     | Number of document chunks retrieved |
| `context_tokens`  | `int \| None`             | Tokens used in the context window   |
| `llm_usage`       | `dict \| None`            | LLM token usage statistics          |
| `latency_ms`      | `float \| None`           | End-to-end latency (milliseconds)   |
| `session_id`      | `str \| None`             | Session identifier                  |

***

## 2. Streaming Responses

`Lake.rag_query_stream()` yields generated content token by token, ideal for real-time
display scenarios. It uses the SSE (Server-Sent Events) protocol under the hood.

```python
import asyncio
from arrow_lake import Lake

lake = Lake(base_uri="./data")


async def stream_rag_response():
    """Stream a RAG response, printing chunks as they arrive."""
    full_answer = []
    async for chunk in lake.rag_query_stream(
        "Explain how DuckLake materialized views work", "docs"
    ):
        full_answer.append(chunk)
        print(chunk, end="", flush=True)

    print(f"\n\nTotal answer length: {sum(len(c) for c in full_answer)} characters")


asyncio.run(stream_rag_response())
```

**Returning SSE from FastAPI**: wrap the async generator in a `StreamingResponse`,
set `media_type="text/event-stream"`, and `yield f"data: {chunk}\n\n"` for each chunk.

***

## 3. Multi-Turn Conversations

Enable session history via the `session_id` parameter. The pipeline automatically
persists each Q\&A round so that subsequent queries can reference prior context.

```python
import asyncio
from arrow_lake import Lake

lake = Lake(base_uri="./data")
session_id = "user-123-session-abc"

async def multi_turn():
    # Turn 1
    r1 = await lake.rag_query("What vector indexes does Arrow Lake support?", "docs",
                              session_id=session_id)
    print(f"A1: {r1.answer}\n")

    # Turn 2 -- context carries forward
    r2 = await lake.rag_query("Which one is best for million-scale datasets?", "docs",
                              session_id=session_id)
    print(f"A2: {r2.answer}\n")

    # Inspect session history
    history = lake.rag_get_history(session_id)
    print(f"Total conversation turns: {len(history)}")

asyncio.run(multi_turn())
```

> Session history persistence requires configuring `rag.history_dataset`, which defaults
> to `_rag_sessions`. Without session storage configured, `rag_get_history()` returns
> an empty list.

***

## 4. Retrieval Strategies

The `strategy` parameter controls how documents are retrieved. Three strategies are
supported:

| Strategy         | Value      | Description                                              |
| ---------------- | ---------- | -------------------------------------------------------- |
| Full-text search | `"fts"`    | Keyword matching, best for exact term queries            |
| Vector search    | `"vector"` | Semantic similarity, best for natural language questions |
| Hybrid search    | `"hybrid"` | RRF fusion for balanced relevance (default)              |

```python
import asyncio
from arrow_lake import Lake

lake = Lake(base_uri="./data")

async def compare_strategies():
    question = "How does Arrow Lake handle data versioning?"

    r_fts = await lake.rag_query(question, "docs", strategy="fts")
    print(f"[FTS]    Retrieved {r_fts.retrieval_count} chunks, {r_fts.latency_ms} ms")

    r_vec = await lake.rag_query(question, "docs", strategy="vector")
    print(f"[Vector] Retrieved {r_vec.retrieval_count} chunks, {r_vec.latency_ms} ms")

    r_hybrid = await lake.rag_query(question, "docs", strategy="hybrid")
    print(f"[Hybrid] Retrieved {r_hybrid.retrieval_count} chunks, {r_hybrid.latency_ms} ms")

asyncio.run(compare_strategies())
```

The default strategy is controlled by the `rag.default_retrieval_strategy` config:

```python
from arrow_lake.config import ArrowLakeConfig

config = ArrowLakeConfig()
config.rag.default_retrieval_strategy = "hybrid"  # "fts" | "vector" | "hybrid"
config.rag.default_top_k = 10                      # Default number of documents to retrieve
```

***

## 5. Context Window Management

The RAG pipeline manages the token budget through `ContextWindow` to stay within the
LLM's context window limits.

| Parameter               | Config Key                  | Default   | Description                        |
| ----------------------- | --------------------------- | --------- | ---------------------------------- |
| `top_k`                 | `rag.default_top_k`         | `10`      | Number of documents to retrieve    |
| `max_context_chunks`    | `rag.max_context_chunks`    | `20`      | Maximum chunks in context          |
| `context_budget_ratio`  | `rag.context_budget_ratio`  | `0.75`    | Fraction of LLM window for context |
| `context_window_tokens` | `llm.context_window_tokens` | `128,000` | Total LLM context window           |

```python
import asyncio
from arrow_lake.config import ArrowLakeConfig
from arrow_lake import Lake

config = ArrowLakeConfig()
config.rag.default_top_k = 15
config.rag.max_context_chunks = 20
config.rag.context_budget_ratio = 0.75       # 75% reserved for retrieval context
config.llm.context_window_tokens = 128_000    # Total LLM window

lake = Lake(base_uri="./data", config=config)

response = asyncio.run(
    lake.rag_query("Describe Arrow Lake's storage layer design in detail", "docs", top_k=15)
)

# Effective context tokens = budget_ratio * context_window_tokens
print(f"Context tokens: {response.context_tokens}")
print(f"Chunks retrieved: {response.retrieval_count}")
```

**How it works**: the pipeline retrieves `top_k` documents, adds each chunk to the
`ContextWindow` one by one with automatic deduplication, truncates when the token
budget is exceeded, and stops adding chunks when `max_context_chunks` is reached.

***

## 6. Prompt Templates

Select a Jinja2 prompt template via the `template_name` parameter.

| Template         | Type    | Description                                                   |
| ---------------- | ------- | ------------------------------------------------------------- |
| `default_qa`     | QA      | Default question-answering template, answers based on context |
| `entity_extract` | EXTRACT | Extracts named entities from text                             |
| `summarize`      | SUMMARY | Summarization template, preserves key facts                   |
| `graph_qa`       | QA      | QA template that combines documents with knowledge graph      |

```python
import asyncio
from arrow_lake import Lake
lake = Lake(base_uri="./data")

r1 = await lake.rag_query("What security mechanisms exist?", "docs", template_name="default_qa")
r2 = await lake.rag_query("Component dependency relationships?", "docs", template_name="graph_qa")
r3 = await lake.rag_extract("docs", template_name="entity_extract")
```

**Listing and registering custom templates**:

```python
from arrow_lake.rag.prompt import PromptTemplate, PromptType, PromptRegistry

registry = PromptRegistry()

# List all templates
print(registry.list_templates())

# Filter by type
for t in registry.list_by_type(PromptType.QA):
    print(f"  {t.name}: {t.description}")

# Register a custom template
registry.register(PromptTemplate(
    name="strict_qa",
    type=PromptType.QA,
    description="Answer strictly from context, refuse to fabricate",
    template=(
        "Answer the question using only the context below. "
        "If the information is insufficient, say so explicitly.\n\n"
        "Context:\n{{ context }}\n\n"
        "Question: {{ question }}\n\nAnswer:"
    ),
))
```

***

## 7. LLM Provider Configuration

Arrow Lake supports OpenAI, Anthropic, Ollama, and vLLM. All communicate via the
OpenAI-compatible interface (except Anthropic, which uses its native SDK).

### OpenAI

```python
from arrow_lake.config import ArrowLakeConfig

config = ArrowLakeConfig()
config.llm.provider = "openai"
config.llm.model = "gpt-4o-mini"
config.llm.api_key = "sk-..."
config.llm.temperature = 0.7
config.llm.max_tokens = 2048
config.llm.context_window_tokens = 128_000
```

### Anthropic

```python
config.llm.provider = "anthropic"
config.llm.model = "claude-sonnet-4-20250514"
config.llm.api_key = "sk-ant-..."
config.llm.max_tokens = 4096
config.llm.context_window_tokens = 200_000
```

> Anthropic extracts `system` messages into the top-level `system` field of its API.

### Ollama (Local Models)

```python
config.llm.provider = "ollama"
config.llm.model = "qwen3:8b"
config.llm.api_base = "http://localhost:11434/v1"
config.llm.timeout_seconds = 120.0
```

> Ollama automatically disables extended thinking for qwen3.x models to avoid
> thinking tokens exhausting the budget.

### vLLM

```python
config.llm.provider = "vllm"
config.llm.model = "Qwen/Qwen2.5-7B-Instruct"
config.llm.api_base = "http://localhost:8000/v1"
```

### YAML Configuration

```yaml
# configs/rag.yaml
llm:
  provider: openai
  model: gpt-4o-mini
  temperature: 0.7
  max_tokens: 2048
  context_window_tokens: 128000
rag:
  enabled: true
  default_retrieval_strategy: hybrid
  default_top_k: 10
  max_context_chunks: 20
  enable_citations: true
```

```python
from arrow_lake import Lake
lake = Lake.from_yaml("configs/rag.yaml", base_uri="./data")
```

Environment variable override: `ARROW_LAKE__LLM__PROVIDER=openai`,
`ARROW_LAKE__RAG__DEFAULT_TOP_K=10`, etc.

***

## 8. Error Handling

```python
import asyncio
from arrow_lake import Lake, RAGError

lake = Lake(base_uri="./data")


async def safe_rag_query():
    try:
        response = await lake.rag_query("test question", "nonexistent_dataset")
    except RAGError as e:
        print(f"RAG error [{e.error_code.name}]: {e.message}")
        print(f"Context: {e.context}")
    except Exception as e:
        print(f"Unexpected error: {type(e).__name__}: {e}")


asyncio.run(safe_rag_query())
```

Common error codes: `RAG_PROVIDER_ERROR` (LLM call failed), `RAG_CONTEXT_EMPTY`
(retrieval returned no results).
