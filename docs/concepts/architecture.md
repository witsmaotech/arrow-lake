# Architecture — Three-Layer + Dual-Plane

**Version:** v1.5.2 | **Updated:** 2026-05-26

---

## System Overview

```mermaid
graph TB
    subgraph Application["Application Layer"]
        CLI["CLI<br/>Click · 16 Groups"]
        SDK["Python SDK<br/>Lake Facade · 8 Mixins"]
        API["REST API<br/>FastAPI · 17 Routers"]
    end

    subgraph Service["Service Layer"]
        direction LR
        subgraph DP["Data Plane"]
            ING["Ingest Pipeline"]
            QRY["SQL Analytics<br/>DuckDB"]
            EXP["Export"]
            QLT["Quality Gates"]
        end
        subgraph KP["Knowledge Plane"]
            RAG["RAG Engine"]
            KG["Knowledge Graph<br/>HugeGraph"]
            EMB["Embedding Service"]
            LLM["LLM Gateway"]
        end
    end

    subgraph Kernel["Kernel Layer"]
        STM["LanceStorageManager<br/>CRUD · Versioning · Indexing"]
        BSM["BlobStoreManager<br/>MinIO / S3"]
        DQB["DuckDB Session<br/>OLAP Catalog"]
        DFT["Daft Engine<br/>DataFrame · Ray"]
        SCH["Schema Manager<br/>UnifiedTableManager"]
    end

    subgraph Infra["Infrastructure"]
        LDB[("LanceDB<br/>Vector + FTS")]
        DDB[("DuckDB<br/>OLAP")]
        MINIO[("MinIO / S3<br/>Blob")]
        REDIS[("Redis<br/>Session · JWT · Lock")]
        HG[("HugeGraph<br/>Graph DB")]
        GRAV[("Gravitino<br/>Metadata")]
    end

    CLI --> DP
    CLI --> KP
    SDK --> DP
    SDK --> KP
    API --> DP
    API --> KP

    ING --> STM
    QRY --> DQB
    EXP --> STM
    QLT --> STM

    RAG --> EMB
    RAG --> LLM
    RAG --> STM
    KG --> HG
    EMB --> STM
    LLM --> STM

    STM --> LDB
    BSM --> MINIO
    DQB --> DDB
    DFT --> LDB
    SCH --> LDB

    DP -.->|Gravitino<br/>Metadata Bridge| KP
    LDB -.-> GRAV
    DDB -.-> GRAV
```

---

## Three-Layer Architecture

| Layer | Responsibility | Key Modules |
|-------|---------------|-------------|
| **Application** | User-facing interfaces | `cli/`, `__init__.py` (Lake), `api/routers/`, `server.py` |
| **Service** | Business logic, domain services | `ingest/`, `query/`, `rag/`, `knowledge_graph/`, `embed/`, `quality/`, `catalog/` |
| **Kernel** | Storage, compute, schema primitives | `storage/`, `ingest/storage.py`, `ingest/schema.py`, `query/session_manager.py`, `ray_runtime/` |

**Dependency rule:** Application → Service → Kernel (one direction only, no bypass).

---

## Dual-Plane Architecture

### Data Plane

Handles structured/unstructured data lifecycle — ingestion, storage, analytics, export, quality.

| Component | Module | Role |
|-----------|--------|------|
| Ingest Pipeline | `ingest/` | File/API/stream ingestion, chunking, OCR |
| SQL Analytics | `query/` | DuckDB OLAP, Daft DataFrame, federated query |
| Export | `ingest/storage.py` | Parquet/CSV export with version selection |
| Quality Gates | `quality/` | Schema validation, dedup, null detection |
| Lifecycle | `storage/lifecycle.py` | TTL, retention, compaction |

### Knowledge Plane

Handles knowledge extraction, embedding, retrieval, and generation — RAG pipeline + knowledge graph.

| Component | Module | Role |
|-----------|--------|------|
| RAG Engine | `rag/` | Multi-provider LLM, reranking, query transform, multi-turn |
| Knowledge Graph | `knowledge_graph/` | HugeGraph integration, GraphRAG |
| Embedding Service | `embed/` | SentenceTransformers, multi-model support |
| LLM Gateway | `rag/provider.py` | OpenAI/Anthropic/vLLM/Ollama/DeepSeek abstraction |

**Orthogonality:** Data Plane and Knowledge Plane share Kernel Layer interfaces but do not import each other's internal modules. They communicate through Gravitino metadata bridge for schema/catalog coordination.

---

## SDK Facade — Lake + 8 Mixins

```
Lake (Facade)
├── _LakeIngestMixin     # Dataset CRUD, file/batch ingestion
├── _LakeSearchMixin     # Vector/FTS/hybrid/faceted/ensemble search
├── _LakeQueryMixin      # SQL OLAP, Daft DataFrame, federated query
├── _LakeAdminMixin      # Maintenance, compaction, index rebuild
├── _LakeLineageMixin    # Data lineage tracking
├── _LakeAuditMixin      # HMAC-SHA256 audit trail
├── _LakeRAGMixin        # RAG pipeline, sessions, streaming
└── _LakeKGMixin         # Knowledge graph CRUD, Gremlin query
```

---

## Data Flow: Ingestion to Answer

```mermaid
graph LR
    SRC["Data Sources"] --> ING["Ingestion"]
    ING --> CHN["Chunking"]
    CHN --> EMB["Embedding"]
    EMB --> LDB[("LanceDB")]

    LDB --> VEC["Vector Search"]
    LDB --> FTS["Full-Text Search"]
    VEC --> RRF["RRF Fusion"]
    FTS --> RRF

    RRF --> RER["Reranking"]
    RER --> CTX["Context Window"]
    CTX --> LLM["LLM Generation"]
    LLM --> ANS["Answer"]

    KG[("HugeGraph")] -->|GraphRAG| RRF
```
