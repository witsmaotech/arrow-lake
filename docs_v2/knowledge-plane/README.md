# Knowledge Plane

> You are an **AI/ML engineer** who builds retrieval pipelines, tunes embedding quality, and delivers RAG-powered applications over structured and unstructured data.

Your knowledge flows through this path:

```
Data Plane (Lance datasets)
  --> Chunk & Embed (sentence-transformers / OpenAI)
    --> Index (Vector / FTS / Hybrid)
      --> Retrieve (semantic / keyword / rerank)
        --> Generate (LLM with context)
          --> Serve (multi-turn conversation / GraphRAG)
```

## Core Tasks

### 🟢 Starter

| Task | Description |
|------|-------------|
| [Index Setup](indexing/README.md) | Create vector indexes over existing Lance datasets; configure IVF-PQ parameters and metric type |
| [Semantic Search](retrieval/semantic.md) | Query by vector similarity, set top-k, and filter by metadata predicates |
| [Full-Text & Hybrid Search](retrieval/hybrid.md) | Combine keyword (Tantivy) and vector search with reciprocal rank fusion (RRF) |

### 🟡 Professional

| Task | Description |
|------|-------------|
| [Chunking Strategies](context/chunking.md) | Split documents by semantic boundaries, fixed size, or sentence -- choose the right strategy per content type |
| [Embedding Pipeline](context/embedding.md) | Configure local (sentence-transformers) or remote (OpenAI) embedding models; manage batch sizing and dimension alignment |
| [Reranking & Query Transform](retrieval/reranking.md) | Apply cross-encoder reranking, query expansion, and HyDE to improve retrieval precision |
| [RAG Pipeline](context/rag-pipeline.md) | Wire retrieval + LLM generation end-to-end; manage context windows, prompt templates, and citation tracing |

### 🔴 Enterprise

| Task | Description |
|------|-------------|
| [Knowledge Graph & GraphRAG](context/knowledge-graph.md) | Build entity-relation graphs in HugeGraph; combine graph traversal with vector retrieval for multi-hop reasoning |
| [Multi-Turn Conversation](context/conversation.md) | Maintain session state in Redis, manage conversation history, and handle follow-up queries with context accumulation |
| [Quality Benchmarking](quality/rag-eval.md) | Measure retrieval recall, answer faithfulness, and latency; set up automated regression tests for RAG quality |

## Next Steps

- **Need to ingest raw data first?** Start with the [Data Plane](../data-plane/README.md) to load and index your source files.
- **Scaling retrieval to production?** See the [Compute Plane](../compute-plane/README.md) for GPU management, HPA autoscaling, and OTel tracing.
- **Understanding the dual-plane model?** Read [Architecture Concepts](../concepts/architecture.md) for how Knowledge and Data planes share the Kernel layer.
