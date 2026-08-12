# RAG Question-Answering Pipeline

> Version: 1.10.4

Arrow Lake includes a built-in RAG (Retrieval-Augmented Generation) pipeline that
supports multiple retrieval strategies, streaming output, multi-turn conversations,
and knowledge graph augmentation. It returns a `RAGResponse` containing the answer,
cited sources, and performance metrics.

> Prerequisites: install the RAG extra with `pip install arrow-lake[rag]`, configure an
> LLM provider, and ensure the target dataset has a vector index.

> **Running dataset.** For RAG we ingest the **full text** of an AIGC industry report (`datas/reports/aigc_industry_report.pdf`) — the same domain as chapters 04–07, now queried in natural language.

***

## 0. Prerequisites: Setting Up Vector Index

RAG queries require a vector index on the target dataset. If you have not yet created one,
run the following steps before calling `rag_query()`:

```python
import asyncio
from arrow_lake import Lake

lake = Lake(base_uri="./data")

# 1. Ingest the full text of the AIGC industry report.
#    ingest_documents_and_index = parse -> chunk -> embed -> FTS + vector index,
#    so the dataset is RAG-ready in a single call (text_embedding is auto-generated).
report = lake.ingest_documents_and_index("aigc_report", [
    "datas/reports/aigc_industry_report.pdf",  # AIGC industry report
])
print(f"Ingested {report.total_rows} chunks")

# 2. Now RAG is ready
response = asyncio.run(lake.rag_query("What is retrieval-augmented generation?", "aigc_report"))
```

> **Note**: If no vector index exists, `rag_query()` will fall back to FTS-only retrieval
> when available, or raise `RAGError` if no retrieval strategy can be applied.

***

## 1. Basic RAG Query

`Lake.rag_query()` runs the full retrieve-augment-generate pipeline and returns a
structured answer with citations.

```python
import asyncio
from arrow_lake import Lake

lake = Lake(base_uri="./data")

response = asyncio.run(
    lake.rag_query("How does retrieval-augmented generation reduce hallucinations?", "aigc_report")
)

# If running inside an existing event loop (Jupyter, FastAPI, etc.),
# use `await` directly instead of asyncio.run():
#   response = await lake.rag_query("...", "aigc_report")

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
| `verification`    | `dict \| None`            | Faithfulness result (v1.9.6; needs `enable_verification`; holds support_ratio/sentences/valid_refs) |
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
        "What are the typical enterprise applications of AIGC?", "aigc_report"
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
    r1 = await lake.rag_query("What problem does retrieval-augmented generation solve?", "aigc_report",
                              session_id=session_id)
    print(f"A1: {r1.answer}\n")

    # Turn 2 -- context carries forward
    r2 = await lake.rag_query("How does it compare to fine-tuning?", "aigc_report",
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
    question = "What datasets evaluate retrieval-augmented generation?"

    r_fts = await lake.rag_query(question, "aigc_report", strategy="fts")
    print(f"[FTS]    Retrieved {r_fts.retrieval_count} chunks, {r_fts.latency_ms} ms")

    r_vec = await lake.rag_query(question, "aigc_report", strategy="vector")
    print(f"[Vector] Retrieved {r_vec.retrieval_count} chunks, {r_vec.latency_ms} ms")

    r_hybrid = await lake.rag_query(question, "aigc_report", strategy="hybrid")
    print(f"[Hybrid] Retrieved {r_hybrid.retrieval_count} chunks, {r_hybrid.latency_ms} ms")

asyncio.run(compare_strategies())
```

The default strategy is controlled by the `rag.default_retrieval_strategy` config (default `"hybrid"`,
truly effective since v1.9.5 — previously the strategy only selected the score column name and retrieval
actually only used FTS, leaving the vector index idle):

```python
from arrow_lake.config import ArrowLakeConfig

config = ArrowLakeConfig()
config.rag.default_retrieval_strategy = "hybrid"  # "fts" | "vector" | "hybrid"
config.rag.default_top_k = 10                      # Default number of documents to retrieve
```

**`use_kg` per-query switch** (v1.9.5): `rag_query(..., use_kg=False)` downgrades a single query to pure
vector/FTS — **no need to disable `hugegraph.enabled`** for comparison; defaults to True, auto-injecting
graph context when hugegraph is enabled.

**Deployment note — `lance_scan_mode=pyarrow_fallback`**: on IVF_PQ datasets the DuckDB lance scanner's
async vector stream can trigger a Rust panic (worker dies → HTTP 502 on hybrid/vector RAG). Set
`ARROW_LAKE__OLAP__LANCE_SCAN_MODE=pyarrow_fallback` in the api container environment to route vector
search through the sync sub-bridge; RAG stays stable at a small latency cost. This is the default in
the shipped `prod_minimal` compose.

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
    lake.rag_query("Describe the retrieval mechanism in retrieval-augmented generation", "aigc_report", top_k=15)
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

Select a Jinja2 prompt template via the `template` parameter.

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

r1 = await lake.rag_query("What components make up a retrieval-augmented generation system?", "aigc_report", template="default_qa")
r2 = await lake.rag_query("How do the retriever and generator interact?", "aigc_report", template="graph_qa")
r3 = await lake.rag_extract(
    text="AIGC core technologies cover the Transformer architecture, large-scale pretraining, RLHF alignment, diffusion models, and multimodal fusion, enabling content creation, customer service, and code generation.",
    schema={"core_technologies": "str", "applications": "list[str]", "benefit": "str"},
    dataset_name="aigc_report",
)
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

### Two-Stage Independent LLM (v1.9.5)

The RAG framework supports **different models** for extraction/reranking vs answer generation
(`rag.extract_llm` / `rag.qa_llm`, both `LLMConfig`; either None falls back to the global `llm`).
A typical pairing: a lightweight `qwen-turbo` for extraction (fast), a flagship `qwen-plus@16384`
for generation (≈ qwen-max quality, ~4.8× cheaper). Bailian (dashscope) must go through the proxy —
do not put it in `NO_PROXY`.

```yaml
rag:
  extract_llm: { provider: openai, api_base: "https://dashscope.aliyuncs.com/compatible-mode/v1",
                 api_key: "sk-...", model: qwen-turbo }
  qa_llm:      { provider: openai, api_base: "https://dashscope.aliyuncs.com/compatible-mode/v1",
                 api_key: "sk-...", model: qwen-plus, max_tokens: 16384, temperature: 0.3 }
```

> **Query transformation** (optional): `rag.query_transform` (`none`/`hyde`/`multi_query`) +
> `multi_query_variants=3` runs parallel multi-variant retrieval for complex multi-facet questions,
> then merges and dedups — improving recall.

***

## 8. Batch Queries

`Lake.rag_batch_query()` processes multiple questions in a single call, returning a list
of `RAGResponse` objects.

```python
import asyncio
from arrow_lake import Lake

lake = Lake(base_uri="./data")


async def batch_example():
    requests = [
        "What is retrieval-augmented generation?",
        "What are the typical enterprise applications of AIGC?",
        "What retrieval strategies does the pipeline support?",
    ]
    results = await lake.rag_batch_query(requests, "aigc_report")
    for q, r in zip(requests, results):
        print(f"Q: {q}")
        print(f"A: {r.answer[:100]}...\n")


asyncio.run(batch_example())
```

***

## 9. Structured Extraction

`Lake.rag_extract()` extracts structured data from free text using a provided schema,
leveraging the same LLM backend as RAG queries.

```python
import asyncio
from arrow_lake import Lake

lake = Lake(base_uri="./data")


async def extract_example():
    text = (
        "The AIGC industry report estimates China's AIGC market at roughly 14.3 billion yuan. "
        "Core technologies span Transformer, pretraining, RLHF, and diffusion models, "
        "with key players including OpenAI, Baidu, Alibaba, Tencent, and ByteDance."
    )
    schema = {
        "market_size": "str",
        "core_technologies": "str",
        "key_companies": "str",
    }
    response = await lake.rag_extract(text, schema, dataset_name="aigc_report")
    print(response.answer)


asyncio.run(extract_example())
```

***

## 10. Feedback & Session Management

### Submitting Feedback

`Lake.rag_feedback()` records user feedback for a specific turn in a session. This is
useful for collecting ratings to evaluate and improve RAG quality.

```python
lake.rag_feedback(
    session_id="user-123-session-abc",
    turn_id="turn-001",
    rating=5,              # 1-5 scale
    comment="Accurate and concise answer",
)
```

### Retrieving Feedback

```python
feedback_list = lake.rag_get_feedback("user-123-session-abc")
for fb in feedback_list:
    print(f"Turn {fb['turn_id']}: rating={fb['rating']}, comment={fb.get('comment', '')}")
```

### Cleaning Up Expired Sessions

`Lake.rag_cleanup_expired_sessions()` removes sessions that have exceeded their TTL,
returning the number of sessions removed.

```python
removed = lake.rag_cleanup_expired_sessions()
print(f"Cleaned up {removed} expired sessions")
```

***

## 11. Error Handling

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

***

## 12. Reranking

First-stage retrieval recalls candidate chunks; a **reranker** sharpens the order so
the most relevant evidence reaches the LLM first. The RAG pipeline's reranker is configured via
**flat fields** under `rag.*` (note: flat fields, not a nested object):

| Field | Default | Description |
|---|---|---|
| `rag.reranker` | `"ollama"` | Strategy: `ollama` / `cross-encoder` / `llm` / `noop` |
| `rag.reranker_model` | `"dengcao/Qwen3-Reranker-0.6B:F16"` | ollama model tag; or HF model for cross-encoder |
| `rag.reranker_base_url` | `""` | Remote endpoint (empty = derived from embedding api_base) |
| `rag.reranker_top_n` | `10` | Chunks kept after reranking |
| `rag.reranker_device` | `"auto"` | cross-encoder device: auto / cpu / cuda (v1.9.6 P0-2) |
| `rag.reranker_warmup_on_init` | `True` | Warm up at startup, no first-query penalty |

> **The default is `ollama`, not `cross-encoder`** (established after the v1.9.5 dead-config fix).
> `ollama` uses Qwen3-Reranker as a yes/no binary judge (limited discrimination); for continuous-score
> precision reranking, switch to `reranker="cross-encoder"` + `reranker_model="BAAI/bge-reranker-v2-m3"`
> (loaded from `HF_HOME`; pre-download for air-gapped deployments). Note the **search endpoints** (not RAG)
> rerank via `hybrid.reranker_model` (which defaults to bge-reranker-v2-m3). If the configured reranker cannot
> load at runtime, the pipeline transparently falls back to `noop` and logs a warning — retrieval never
> hard-fails on a reranker misconfiguration.

```yaml
# configs/rag.yaml — flat fields (not a nested object)
rag:
  reranker: ollama                      # or cross-encoder / llm / noop
  reranker_model: dengcao/Qwen3-Reranker-0.6B:F16
  reranker_top_n: 10
  reranker_device: auto                 # effective for cross-encoder
  reranker_warmup_on_init: true
```

***

## 13. Faithfulness Verification (Anti-Hallucination)

When enabled, the `[n]` citation refs in the generated answer are validated against the retrieved
context range, and each sentence is labeled `supported` (carries a valid `[n]` ref) or `unverified`
(no ref). The response carries a `verification` block with `support_ratio` and per-sentence detail,
so callers can flag ungrounded claims instead of trusting them silently.

```yaml
rag:
  enable_verification: true      # opt-in; off by default
```

The current implementation is the **lightweight mode** (pure stdlib, zero extra cost): `[n]` ref
validation + per-sentence labels. Embedding-cosine mode and LLM-judge mode are planned extensions
(see the trailing comment in `arrow_lake/rag/verifier.py`), not yet implemented.

```python
response = await lake.rag_query("Summarize the core technical evolution of AIGC", "aigc_report")
print(response.answer)
v = response.verification          # dict or None (when disabled)
if v:
    print(f"Support ratio: {v['support_ratio']}")     # 0.0 – 1.0
    print(f"Valid refs: {v['valid_refs']}, invalid: {v['invalid_refs']}")
    # v['sentences']: per-sentence detail [{text, label: "supported"|"unverified", refs}]
```

For streaming responses, the **final frame** carries the `verification` block (along
with `citations` and `latency`), so a streaming UI can surface the support ratio once
generation completes.

***

## 14. GraphRAG (Knowledge-Graph Augmentation)

When `hugegraph.enabled=true` and the dataset has been `kg_build`-ed, the RAG pipeline auto-upgrades to
`GraphRAGPipeline`: extract entities from the question → parallel retrieval (vector + graph triples +
neighbors) → RRF fusion → generate with the `graph_qa` template. Degrades gracefully to pure vector RAG
when KG is unavailable (built into `graph_rag.py`).

- **Three-way parallel** (v1.9.6 P0-4): `_graphrag_retrieve` uses `asyncio.gather` to run vector / search_ka /
  neighbor concurrently, cutting latency 40~50% vs sequential; `QuestionEntityCache` uses a monotonic clock
  to prevent NTP jumps from invalidating TTLs en masse.
- **Relation-type enrichment** (v1.9.11): the neighbor context now reads the edge `relation_type` property
  (e.g. `depends_on`, `authored_by`) instead of collapsing every edge to a generic `related_to`, so the LLM
  receives semantically meaningful triples. When an entity name from the question does not match a graph
  vertex exactly, a **char-overlap fallback** recovers candidate entities by character-level overlap
  (zero embedding cost) before falling back to KA lookup — keeping citation coverage high on paraphrased names.
- **per-query `use_kg`**: pass `use_kg=False` to bypass KG for a single query (degrades to `super().query()`),
  no need to disable hugegraph.
- **Latency tuning**: entity extraction / query variants use `extract_llm=qwen-turbo` (fast); `qa_llm` uses
  qwen-plus (generation). Optimal QA pairing: `qwen-plus@16384` (≈ qwen-max quality, ~4.8× cheaper).

```python
# GraphRAG (hugegraph enabled + dataset has a built KG)
r = await lake.rag_query("Which components does the retriever depend on?", "aigc_report")           # use_kg defaults True
r2 = await lake.rag_query("Same question, pure-vector comparison", "aigc_report", use_kg=False)  # bypass KG once
```

The dedicated REST endpoint `POST /api/v1/kg/query/graphrag` (body uses `question` + `dataset`).
