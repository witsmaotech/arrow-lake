# Overview — What is Arrow Lake?

> Start here. A 5-minute mental model of the platform before you run any code.

## The one-paragraph pitch

Arrow Lake is a **production-grade, multimodal data lakehouse**: vector search, full-text search, SQL analytics, a knowledge graph, and a RAG engine — all behind **one `Lake` facade**, over **one storage layer**, governed by **one identity/audit plane**. No glue code stitching five tools together. It is self-hosted first (your data, your models, Apache-2.0).

## The mental model: one facade, six pillars

Everything you do goes through one Python object:

```python
from arrow_lake import Lake
lake = Lake("./my_lake")   # local FS — no MinIO, no Docker needed to start
```

Six first-class pillars share that one facade — the same dataset, the same identity, the same audit trail:

| Pillar | What it does | Entry point |
|---|---|---|
| 🗄️ **Unified Lakehouse** | One Lance store for vectors, text, images, structured fields | `lake.create_dataset()` |
| 🔎 **Hybrid Search** | Vector + Tantivy full-text + RRF fusion (**hybrid is the default**) | `lake.search()` / `lake.hybrid_search()` |
| 🕸️ **GraphRAG & KG** | Per-dataset knowledge graphs + GraphRAG with `relation_type` enrichment | `lake.kg_build()` / `lake.kg_query()` |
| 💬 **Production RAG** | Multi-provider, hybrid-by-default, reranking, anti-hallucination, citations | `lake.rag_query()` |
| 🖼️ **Document AI** | Docling parsing, multimodal embeddings (CLIP), OCR | `lake.ingest_documents()` |
| 📊 **Analytics** | DuckDB OLAP + Daft distributed DataFrame | `lake.olap_query()` / `lake.daft_query()` |

Each pillar is a subsystem, not a thin wrapper. They all operate over the **same Lance dataset** — you stop maintaining five clients and five auth models.

## The data flow

```
ingest → index → { search | SQL | RAG | GraphRAG } → export / govern
```

1. **Ingest** multimodal data (CSV / PDF / images / …) into a Lance dataset.
2. **Index** it (vector IVF_PQ, full-text BM25) — automatic for most cases.
3. **Query** across four modes over the *same* dataset: semantic search, SQL analytics, RAG Q&A, or GraphRAG multi-hop.
4. **Govern** with RBAC, audit, lineage, masking — all built-in.

<p align="center">
  <img src="../architecture-design/diagrams/01-layered-architecture.svg" alt="Arrow Lake layered architecture" width="780">
</p>

## What makes it different

- **Unified, not assembled** — vector + FTS + SQL + graph + RAG share one facade, one store, one governance plane.
- **Native GraphRAG** — HugeGraph + template-driven extraction; answers on entity/relation-dense questions are richer than pure vector RAG.
- **Truly multimodal** — text, images, audio, video, documents; not just text embeddings.
- **Production by default** — RBAC, JWT, audit, Helm chart, observability. Not a dev toy.

## Where to go next

| You want to... | Go to |
|---|---|
| Run something working in 5 minutes | [01 Quickstart](./01-quickstart.md) |
| **See the whole platform end-to-end (recipes)** | [19 REST Recipes](./19-rest-recipes.md) |
| Ingest your own data | [02 Ingestion](./02-ingestion.md) |
| Vector / full-text / hybrid search | [04](./04-vector-search.md) · [05](./05-fulltext-search.md) · [06](./06-hybrid-faceted.md) |
| OLAP SQL analytics | [07 OLAP](./07-olap-analytics.md) |
| RAG & GraphRAG | [08 RAG](./08-rag-pipeline.md) · [09 KG](./09-knowledge-graph.md) |
| Configure / deploy | [03 Config](./03-configuration.md) · [12 Deployment](./12-deployment.md) |
| **Ship a high-quality dataset (v1.11.4)** | [20 HQ Pipeline](./20-hq-dataset.md) |

> **New here?** The fastest path to "I understand what this platform does": [01 Quickstart](./01-quickstart.md) → [19 REST Recipes](./19-rest-recipes.md) (end-to-end) → dive into any pillar above.

For the authoritative deep dive (layers, facades, data flow, 8 diagrams), see the [Architecture document](../architecture-design/ARCHITECTURE.md).
