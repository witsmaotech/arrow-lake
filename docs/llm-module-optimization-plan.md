# LLM Module Optimization & Improvement Plan

> Based on full code review of `arrow_lake/rag/`, `arrow_lake/embed/`, `arrow_lake/knowledge_graph/`, and related config modules.

## Overview

Arrow Lake implements a production-ready, multi-modal RAG pipeline with multiple embedding backends, flexible chunking strategies, GraphRAG augmentation, and session management. The following analysis identifies optimization opportunities focused on **quality** and **extensibility**.

---

## 1. Provider Layer — Extensibility

### 1.1 Anthropic Missing Circuit Breaker

**File**: `arrow_lake/rag/provider.py:399-406`

`AnthropicProvider.generate()` calls `_request()` directly without circuit breaker protection, unlike `OpenAICompatibleProvider` (line 252-258). API failures will cause continuous requests with no backoff.

**Fix**: Wrap `_request()` call in `self._circuit_breaker()` with `allow_request()` / `record_failure()` / `record_success()`.

### 1.2 No Model Fallback Chain

Current design: one provider, one model. Production requires:

```
gpt-4o → rate limit → gpt-4o-mini → timeout → deepseek-v4
```

**Proposal**: Add to `LLMConfig`:

```python
fallback_models: list[str] = []          # same provider, different models
fallback_provider: LLMProviderType | None = None  # different provider entirely
```

Implement in `BaseLLMProvider.generate()` with cascading fallback on `RAG_PROVIDER_ERROR`.

### 1.3 No Plugin-Based Provider Registry

Current `create_llm_provider()` uses a hardcoded `match` statement. Adding new providers (Gemini, Cohere, Mistral) requires modifying the factory.

**Proposal**: Switch to registration pattern:

```python
_PROVIDER_REGISTRY: dict[LLMProviderType, type[BaseLLMProvider]] = {}

def register_provider(provider_type: LLMProviderType):
    def decorator(cls):
        _PROVIDER_REGISTRY[provider_type] = cls
        return cls
    return decorator
```

This allows third-party or project-specific providers to be registered without touching core code.

### 1.4 Streaming Loses Usage Statistics

**File**: `arrow_lake/rag/provider.py:296-330`

SSE streaming does not enable OpenAI's `stream_options: {"include_usage": true}`, so `batch_query_stream` and streaming endpoints cannot track token consumption for cost accounting.

**Fix**: Add `stream_options` to `_build_body()` when `stream=True` for OpenAI-compatible providers, and parse the final `usage` chunk.

### 1.5 No Structured Output Support

Entity extraction (`knowledge_graph/extractor.py`) relies on prompt-level JSON constraints plus 8 regex repair patterns for broken LLM output. No `response_format: {"type": "json_object"}` or Function Calling abstraction exists.

**Proposal**: Add optional `response_format` and `tools` parameters to `BaseLLMProvider.generate()`, with provider-specific translation in each implementation.

---

## 2. Session Management — Persistence & Multi-turn

### 2.1 In-Memory Only, Lost on Restart

**File**: `arrow_lake/rag/session.py`

Comment says "Designed to be swappable with a Lance-backed implementation later" but it's never implemented. Production restarts lose all session history.

**Proposal**: Add `PersistentSessionStore` backend:

- Write-through to Lance dataset or Redis Stream
- In-memory hot cache for active sessions
- Background flush for feedback entries
- Keep `SessionStore` as interface/protocol for swappability

### 2.2 Session History Not Injected into LLM Context

**File**: `arrow_lake/rag/pipeline.py:84-110`

`_build_messages()` only constructs system + user messages. Multi-turn conversation history saved by `SessionStore` is **never read back** for subsequent queries.

This means follow-up questions like "tell me more about the second point" have zero context.

**Fix**: In `query()`, when `session_id` is provided:

1. Read history from `SessionStore.get_history(session_id)`
2. Convert recent turns to `LLMMessage(role="assistant"/"user")` pairs
3. Insert before the current user message
4. Respect `context_window_tokens` budget for history truncation

### 2.3 Memory Eviction O(n) Efficiency

**File**: `arrow_lake/rag/session.py:81-91`

Global session eviction uses `min()` scanning all sessions to find the oldest. Each eviction round is O(n).

With a 10,000 session limit this is tolerable, but could be optimized with `OrderedDict` or a min-heap keyed by first-turn timestamp.

---

## 3. Embedding Layer — Performance & Quality

### 3.1 No Embedding Cache

Same text is re-encoded without caching. Common scenarios:
- Rebuilding index on the same dataset
- Query-time embedding of similar questions

**Proposal**: Add LRU cache layer (text hash → embedding vector), with TTL that invalidates on model change.

### 3.2 ApiEmbeddingEncoder Is Synchronous

**File**: `arrow_lake/embed/encoder.py:204-380`

API encoder uses synchronous `httpx.Client`. In async FastAPI handlers, direct calls block the event loop. The RAG pipeline works around this via `run_in_executor`, but embedding API endpoints call it directly.

**Proposal**: Add `AsyncApiEmbeddingEncoder` using `httpx.AsyncClient`, or make `encode()` return an awaitable.

### 3.3 Fallback Cache Not Thread-Safe

**File**: `arrow_lake/embed/encoder.py:221`

`_fallback_cache: ClassVar[dict]` is a class-level mutable dict with no lock. Multiple workers may simultaneously load the same model.

**Fix**: Use `threading.Lock` or `functools.lru_cache` for thread-safe model caching.

### 3.4 No Model Hot-Swap / Multi-Version

Embedding model config is global and static. Production needs:
- Upgrade model version while old vectors remain queryable
- Multiple embedding models coexisting (different dimensions for different datasets)

**Proposal**: Store `embedding_model_version` in Lance dataset metadata. Support per-dataset model configuration. During search, use the model that produced the stored vectors.

---

## 4. RAG Pipeline — Quality

### 4.1 Missing Reranking Stage

**File**: `arrow_lake/rag/pipeline.py:146-207`

Retrieval results go directly into the context window without reranking. Top-k vector results may include many low-relevance chunks.

**Proposal**: Add a reranking stage between retrieval and context assembly:

- **Cross-encoder reranker** (e.g., `BAAI/bge-reranker-v2-m3`) — best cost/quality tradeoff
- Or **LLM-based rerank** — LLM scores each chunk 1-10
- Make reranker a pluggable callback: `RerankerFunc = Callable[[str, list[ContextChunk]], list[ContextChunk]]`

This is typically the single largest quality improvement for RAG systems.

### 4.2 No Query Transformation / Expansion

User's raw query goes directly to vector search without:
- **Query decomposition**: Break compound questions into sub-queries
- **HyDE (Hypothetical Document Embedding)**: Generate a hypothetical answer, use it for retrieval
- **Multi-query**: Generate multiple query variants, retrieve for each, merge results

**Proposal**: Add `QueryTransformer` protocol:

```python
class QueryTransformer(Protocol):
    async def transform(self, question: str) -> list[str]: ...
```

With implementations: `IdentityTransformer`, `HyDETransformer`, `MultiQueryTransformer`, `DecompositionTransformer`.

### 4.3 Context Window No Importance Ordering

**File**: `arrow_lake/rag/context.py:124-175`

Chunks are added to context window in **retrieval order** (first-come-first-served). Low-relevance chunks may consume budget before high-relevance chunks arrive.

**Fix**: Collect all candidate chunks first, sort by score descending, then fill budget. This ensures the most relevant content always gets included.

### 4.4 Token Truncation Too Coarse

**File**: `arrow_lake/rag/context.py:148-168`

When a chunk exceeds remaining budget, it's truncated via `chunk.text[: remaining * 4]` — a raw character cut that may break mid-sentence.

**Fix**: Truncate at sentence boundaries:

```python
import re
sentences = re.split(r'(?<=[.!?。！？])\s+', truncated_text)
# Keep sentences that fit within remaining budget
```

### 4.5 No Answer Grounding Verification

After LLM generation, there's no verification that the answer is **grounded in the provided context**. Hallucinated claims go undetected.

**Proposal**: Add optional grounding check:

- Compare answer claims against context chunks
- Flag ungrounded statements with confidence score
- Include `grounding_score` in `RAGResponse`

---

## 5. GraphRAG — Efficiency

### 5.1 Question Entity Extraction Not Cached

**File**: `arrow_lake/rag/graph_rag.py:84-101`

Every query runs LLM entity extraction on the question. Similar/duplicate questions waste tokens.

**Proposal**: Add semantic cache (question embedding → extracted entities) with configurable TTL.

### 5.2 `graph_weight` Defined But Unused

**File**: `arrow_lake/rag/graph_rag.py:59`

`graph_weight: float = 0.3` is defined in `__init__` but never used in `query()`. Graph context and vector context are simply concatenated without weighted fusion.

**Proposal**: Implement RRF (Reciprocal Rank Fusion) between graph triplets and vector results, using `graph_weight` to balance contributions.

### 5.3 Graph Fallback Exception Catch Too Narrow

**File**: `arrow_lake/rag/graph_rag.py:219`

`except (AttributeError, TypeError, ValueError, OSError, RuntimeError)` misses `KeyError`, `IndexError`, and other common exceptions from JSON parsing or API response handling.

**Fix**: Change to `except Exception` with explicit `CancelledError` re-raise (already done at line 217).

---

## 6. Observability & Configuration

### 6.1 No RAG Quality Metrics

Missing end-to-end quality measurements:
- **Retrieval hit rate**: How many retrieved chunks were actually cited by LLM
- **Context utilization**: `actual_tokens / budget_tokens` ratio
- **Answer faithfulness**: Overlap between answer and context

**Proposal**: Add `quality_metrics` field to `RAGResponse`:

```python
@dataclass(frozen=True)
class RAGQualityMetrics:
    context_utilization: float      # 0.0-1.0
    cited_chunk_ratio: float        # chunks cited / chunks retrieved
    latency_retrieval_ms: float
    latency_llm_ms: float
```

### 6.2 No Latency Budget Breakdown

**File**: `arrow_lake/rag/pipeline.py:176`

Only total latency is recorded. No breakdown of **retrieval time** vs **LLM generation time** vs **context assembly time**.

**Fix**: Add timing around each stage:

```python
t_retrieve = time.perf_counter()
# ... retrieval ...
t_context = time.perf_counter()
# ... context assembly ...
t_llm = time.perf_counter()
# ... LLM call ...
t_end = time.perf_counter()

metrics = RAGQualityMetrics(
    latency_retrieval_ms=(t_context - t_retrieve) * 1000,
    latency_llm_ms=(t_end - t_llm) * 1000,
    ...
)
```

### 6.3 Single Global Model Configuration

`LLMConfig` is one global model. Different tasks benefit from different models:

| Task | Ideal Model |
|---|---|
| Entity extraction | Fast model (gpt-4o-mini) |
| GraphRAG generation | Reasoning model (gpt-4o / deepseek-v4) |
| Summarization | Balanced model |
| Complex analysis | Thinking model (deepseek-v4 with thinking) |

**Proposal**: Add per-task model override in `RAGConfig`:

```python
task_model_overrides: dict[str, LLMConfig] = {}
# e.g., {"entity_extract": LLMConfig(model="gpt-4o-mini"), "complex_qa": LLMConfig(provider="deepseek")}
```

---

## Priority Matrix

| Priority | Item | Impact |
|---|---|---|
| **P0** | 2.2 Multi-turn history injection | Core feature gap — follow-up questions broken |
| **P0** | 4.1 Reranking stage | Largest single quality improvement for RAG |
| **P0** | 1.1 Anthropic circuit breaker | Production stability |
| **P1** | 4.2 Query transformation/expansion | Significant quality improvement |
| **P1** | 2.1 Session persistence | Production necessity |
| **P1** | 4.3 Context importance ordering | Quality improvement |
| **P1** | 6.2 Latency budget breakdown | Observability |
| **P2** | 1.2 Model fallback chain | Reliability |
| **P2** | 1.4 Streaming usage statistics | Cost tracking |
| **P2** | 3.1 Embedding cache | Performance |
| **P2** | 6.3 Multi-task model configuration | Flexibility |
| **P2** | 1.5 Structured output support | Code simplification |
| **P2** | 4.5 Answer grounding verification | Quality assurance |
| **P3** | 1.3 Plugin provider registry | Extensibility |
| **P3** | 3.2 Async API encoder | Architecture consistency |
| **P3** | 3.3 Thread-safe fallback cache | Concurrency safety |
| **P3** | 3.4 Model hot-swap | Operational flexibility |
| **P3** | 4.4 Token truncation improvement | Edge case quality |
| **P3** | 5.1 Entity extraction cache | Performance |
| **P3** | 5.2 Graph weight implementation | Feature completion |
| **P3** | 5.3 Graph fallback exception scope | Robustness |
| **P3** | 6.1 RAG quality metrics | Observability |
| **P3** | 2.3 Eviction efficiency | Scalability |

---

## Implementation Notes

- **Backward compatibility**: All changes should maintain existing API contracts. New features added as optional configuration with sensible defaults.
- **Graceful degradation**: Following the existing pattern — every new component should work when unavailable, falling back to current behavior.
- **Testing**: Each improvement should include:
  - Unit tests with mocked providers/backends
  - Integration tests against real API (optional, CI-flagged)
  - Benchmark comparison for quality-related changes
- **Incremental delivery**: P0 items can be shipped independently without blocking each other.
