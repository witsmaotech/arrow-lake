# Glossary — Unified Terminology

**Last Updated:** 2026-05-26

This glossary establishes canonical terms for Arrow Lake to eliminate ambiguity across documentation, code, and conversations.

---

## Core Concepts

| Canonical Term | Aliases / Conflicts | Definition |
|---------------|---------------------|------------|
| **Dataset** | table, collection | A named set of records stored in Lance format within a Lake instance. The primary data unit. |
| **Lake** | lake instance, workspace | A logical container for datasets, managed by the `Lake` facade class. Maps to a base URI on local filesystem or S3. |
| **Base URI** | lake path, root path, data dir | The filesystem or S3 path where a Lake stores its datasets. |
| **Schema** | table schema, data schema | The column definitions (names + types) of a dataset. Managed by `UnifiedTableManager`. |
| **Schema Evolution** | schema migration | Changing a dataset's schema (add/drop columns, change types) while preserving existing data. |

## Data Plane

| Canonical Term | Aliases / Conflicts | Definition |
|---------------|---------------------|------------|
| **Ingestion** | ingest, import, load | The process of adding data to a dataset. Includes file parsing, chunking, and embedding. |
| **Chunk** | segment, slice, fragment, passage | A contiguous text segment extracted from a document during ingestion. Stored as a row in a dataset. |
| **Chunking** | segmentation, splitting | Breaking a document into smaller pieces. Strategies: fixed-size, sentence, semantic, etc. |
| **Version** | data version, lance version | An immutable snapshot of a dataset at a point in time. Managed by Lance's native versioning. |
| **Compaction** | cleanup, optimization | Merging small data fragments into larger ones for better query performance. |
| **Quality Gate** | validation rule, check | A rule applied during ingestion to validate data quality (schema match, null ratio, dedup threshold). |
| **Deduplication** | dedup | Removing duplicate records. Strategies: exact hash (text), perceptual hash (images), MinHash (near-duplicate). |
| **Export** | download, extract | Writing dataset contents to external formats (Parquet, CSV) with optional version/column selection. |

## Knowledge Plane

| Canonical Term | Aliases / Conflicts | Definition |
|---------------|---------------------|------------|
| **Embedding** | vector, embedding vector, encoding | A dense numerical representation of data (text, image) in a high-dimensional space. |
| **Vector Index** | ANN index, vector search index | A data structure (IVF_PQ, IVF_FLAT, IVF_HNSW_PQ) for fast approximate nearest neighbor search. |
| **Full-Text Search (FTS)** | text search, keyword search | Text matching using Tantivy engine with jieba CJK tokenizer, stemming, and stop-word removal. |
| **Hybrid Search** | combined search | Combining vector similarity and FTS scores using Reciprocal Rank Fusion (RRF). |
| **RRF** | reciprocal rank fusion | Score combination method: `score = Σ 1/(k + rank_i)` across multiple retrieval results. |
| **Reranking** | re-ranking, cross-encoder rerank | A second-pass scoring of retrieval results using CrossEncoder or LLM for higher precision. |
| **Query Transformation** | query rewrite | Modifying the user's query before retrieval. Strategies: HyDE, MultiQuery, Identity (no-op). |
| **RAG** | retrieval-augmented generation | The full pipeline: retrieve relevant chunks → build context → generate answer via LLM. |
| **GraphRAG** | graph-enhanced RAG | RAG augmented with knowledge graph traversal results, fused via RRF with vector/FTS scores. |
| **Context Window** | context, rag context | The assembled set of retrieved chunks (with scores) passed to the LLM for answer generation. |
| **Session** | conversation, chat session | A multi-turn conversation context with history, managed by `RAGSession`. |
| **Knowledge Graph** | graph, KG | Structured entity-relationship data stored in HugeGraph, queried via Gremlin. |
| **LLM Provider** | model provider, AI backend | A supported LLM service: OpenAI, Anthropic, vLLM, Ollama, DeepSeek. Abstracted by `LLMProvider`. |

## Compute Plane

| Canonical Term | Aliases / Conflicts | Definition |
|---------------|---------------------|------------|
| **Profile** | compose profile, deployment profile | A Docker Compose service group: `api`, `minio`, `redis`, `ray-head`, `ray-worker`, `jupyter`, `turbo-ocr`, `gravitino`. |
| **HPA** | horizontal pod autoscaler | Kubernetes autoscaling based on CPU/GPU utilization. |
| **PDB** | pod disruption budget | Kubernetes policy ensuring minimum available pods during disruptions. |
| **OTel** | OpenTelemetry | Distributed tracing and metrics collection framework. |
| **SLO** | service level objective | A target reliability metric (e.g., RAG P95 < 2s). |
| **SRE** | site reliability engineer | The role responsible for platform deployment, monitoring, and incident response. |

## Metadata & Governance

| Canonical Term | Aliases / Conflicts | Definition |
|---------------|---------------------|------------|
| **Catalog** | metadata catalog, Gravitino catalog | A Gravitino-managed collection of schemas and tables. Arrow Lake syncs DuckDB ↔ Gravitino bidirectionally. |
| **Metalake** | gravitino metalake | The top-level Gravitino namespace containing catalogs. |
| **Tag** | label, annotation | A Gravitino-managed label attached to tables/columns for classification and governance. |
| **Policy** | governance policy | A Gravitino-managed rule controlling data access or lifecycle. |
| **Lineage** | data lineage, provenance | Tracking the origin and transformation history of data. |
| **Audit Trail** | audit log, tamper-evident log | Immutable, HMAC-SHA256-signed record of all data operations. |

## Security

| Canonical Term | Aliases / Conflicts | Definition |
|---------------|---------------------|------------|
| **RBAC** | role-based access control | Permission model with three roles: VIEWER (read), EDITOR (read+write), ADMIN (all). |
| **JWT** | JSON web token | Stateless authentication token. Blacklisted in Redis for revocation. |
| **FQN** | fully qualified name | A dataset identifier like `catalog.schema.table`. Validated against injection via regex whitelist. |

---

## Deprecation Notes

| Deprecated Term | Use Instead | Reason |
|----------------|-------------|--------|
| segment | **chunk** | Consistency with RAG literature |
| slice | **chunk** | Consistency with RAG literature |
| collection | **dataset** | Match codebase (`create_dataset`, not `create_collection`) |
| workspace | **lake** or **lake instance** | Avoid confusion with Gravitino workspace |
| metadata | **catalog** (governance context) | "metadata" is overloaded — use specific term |
