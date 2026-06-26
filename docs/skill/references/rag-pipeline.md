# RAG Pipeline — Providers, Citations, Streaming, Reranking

> Back-reference: [../SKILL.md](../SKILL.md) · parent: [architecture.md](architecture.md). Verified v1.7.0.

All RAG entry points on `Lake` are **`async`** (`_LakeRAGMixin`). They delegate to `RAGPipeline` (`rag/pipeline.py`), built lazily via `_get_rag_pipeline()`. GraphRAG is the same pipeline wired to a KG retriever.

## End-to-end flow

```
question → QueryTransformer (HyDE / MultiQuery) → Retriever (vector/FTS/hybrid)
        → Reranker (Noop / CrossEncoder / LLM) → Context builder
        → LLMProvider (OpenAI / Anthropic / DeepSeek / vLLM / Ollama)
        → answer + citations (+ optional SSE stream)
```

## Calling it

```python
import asyncio
from arrow_lake import Lake

async def main():
    lake = Lake("./data")              # Lake is sync; manage lifecycle yourself
    try:
        # Single-turn
        resp = await lake.rag_query(
            "What is RAG?", "docs",
            top_k=5, strategy="hybrid", template_name="default")

        # Streaming (SSE chunks)
        async for chunk in lake.rag_query_stream("Explain RAG", "docs"):
            print(chunk, end="", flush=True)

        # Batch + extraction
        await lake.rag_batch_query([...], "docs")
        await lake.rag_extract("docs", fields=["entities"])
    finally:
        lake.shutdown()

asyncio.run(main())
```

Sessions & feedback (sync helpers):
- `rag_get_history(session_id) -> list[dict]`
- `rag_feedback(...)`, `rag_get_feedback(session_id)`
- `rag_cleanup_expired_sessions() -> int`

## LLM Providers

`LLMProviderType` enum + `provider.py`. Default base URLs:

| Provider | Base URL | Notes |
|---|---|---|
| OpenAI | `https://api.openai.com/v1` | default |
| Anthropic | `https://api.anthropic.com` | sends `anthropic-version` |
| DeepSeek | `https://api.deepseek.com` | |
| vLLM | configurable (`LLMConfig.base_url`) | self-hosted |
| Ollama | configurable | local |

Each provider is behind a **circuit breaker** (`core/`) — when OPEN, the call fails fast with a clear `RAGError` instead of hanging. Set keys via env (`OPENAI_API_KEY`, etc.) or `LLMConfig`; the app rejects startup with empty credentials when auth is enabled.

## Query Transformation

`rag/query_transform.py` — `BaseQueryTransformer`:
- **`HyDETransformer`** — generate a hypothetical answer, embed it for retrieval (better semantic match than the raw question).
- **`MultiQueryTransformer`** — generate multiple query variants, retrieve per variant, merge (improves recall).

Select via `rag_query(..., strategy=...)` or `RAGConfig`.

## Rerankers

`rag/reranker.py` — `BaseReranker`:

| Reranker | When |
|---|---|
| `NoopReranker` | skip reranking (default fast path) |
| `CrossEncoderReranker` | precision boost, GPU/CPU cross-encoder |
| `LLMReranker` | LLM-as-judge scoring, highest quality, costly |

## Citations & sessions

Responses carry **citations** (source document + chunk references) — surface them in the UI; do not strip them. Multi-turn state is keyed by `session_id` and TTL-cleaned by `rag_cleanup_expired_sessions`.

## GraphRAG

`_create_graph_rag_pipeline(provider)` wires the same `RAGPipeline` to a **KG retriever** (entities/relations from HugeGraph) instead of pure vector retrieval. Invoke through the RAG surface with the graph strategy; the KG side is covered in [knowledge-graph.md](knowledge-graph.md).

## Common Mistakes

- **Missing `await`**: `rag_query` / `rag_query_stream` / `rag_batch_query` / `rag_extract` are coroutines — without `await` you get a coroutine object, not an answer.
- **Calling RAG on a sync `Lake` from an event loop incorrectly**: run with `asyncio.run(...)` or an existing loop; ensure `lake.shutdown()` in a `finally`.
- **Empty provider keys at startup**: with auth enabled, the app refuses to boot — set credentials before launch.
- **Stripping citations**: they are part of the response contract; downstream trust depends on them.
- **Ignoring circuit-breaker state**: a flapping provider trips the breaker; surface `RAGError` to the user rather than retrying blindly.
