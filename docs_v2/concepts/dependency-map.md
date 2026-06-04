# Dependency Map — Framework Responsibilities & Compatibility

**Version:** v1.5.3 | **Updated:** 2026-06-04

---

## Framework Responsibility Boundaries

```mermaid
graph TB
    subgraph DARMU["DARMU Stack"]
        DAFT["Daft 0.7.8<br/>Data Transformation<br/>DataFrame · Lazy Eval · Multimodal"]
        ARGO["Argo Workflows ≥3.5<br/>Production Workflow<br/>K8s CronWorkflow · DAG"]
        RAY["Ray 2.54.1<br/>Distributed Compute<br/>Serve · Data · Actor"]
        META["Metaflow 2.19.22<br/>ML Orchestration<br/>User-Facing Workflows"]
        UV["uv<br/>Dependency Management<br/>Lock · Resolve · Cache"]
    end

    subgraph Extension["Extension Layer"]
        LANCE["LanceDB 0.30.2<br/>Vector + FTS Storage<br/>Columnar · Versioned · Zero-Copy"]
        DUCK["DuckDB 1.5.2<br/>OLAP Catalog<br/>SQL Analytics · Metadata"]
        NEMO["NeMo Curator ≥1.1.0<br/>Data Quality<br/>Dedup · Score · GPU Accel"]
    end

    subgraph External["External Services"]
        MINIO["MinIO / S3"]
        REDIS["Redis 5.x"]
        HGRAPH["HugeGraph"]
        GRAV["Gravitino 1.2.1"]
    end

    DAFT -->|"DataFrame ops"| LANCE
    DAFT -->|"SQL fallback"| DUCK
    RAY -->|"Distributed inference"| LANCE
    META -->|"ML pipeline"| RAY
    LANCE --> MINIO
    DUCK --> GRAV
    LANCE --> GRAV
```

## Responsibility Matrix

| Framework | Primary Role | Boundary | Does NOT Do |
|-----------|-------------|----------|-------------|
| **Daft** | Data transformation, multimodal DataFrame | Data pipeline ETL | Workflow orchestration, model serving |
| **Ray** | Distributed compute, inference parallelization | Scale-out compute | Data transformation, user workflow |
| **Metaflow** | ML workflow orchestration (user-facing) | End-to-end ML pipelines | Data transformation, serving |
| **Argo** | Production K8s workflow | Cron jobs, DAG workflows | ML logic, data processing |
| **LanceDB** | Vector + FTS storage | Index + query | OLAP analytics, metadata governance |
| **DuckDB** | OLAP analytics + metadata catalog | SQL queries, schema metadata | Vector search, file storage |

## Overlap Analysis

| Overlap Area | Frameworks | Resolution |
|-------------|-----------|------------|
| Distributed data processing | Daft vs Ray Data | Daft for ETL/transform, Ray for inference/serving |
| Workflow orchestration | Metaflow vs Argo | Metaflow for ML dev, Argo for K8s production |
| SQL query | Daft SQL vs DuckDB | DuckDB for analytics, Daft for in-pipeline filtering |

---

## Dependency Topology

```mermaid
graph TD
    APP["arrow-lake (Application)"]
    SDK["arrow_lake (SDK)"]

    APP --> CLI["click + rich"]
    APP --> API["fastapi + uvicorn"]
    APP --> SDK

    SDK --> RAG["rag/"]
    SDK --> ING["ingest/"]
    SDK --> QR["query/"]
    SDK --> KG["knowledge_graph/"]
    SDK --> EMBED["embed/"]

    RAG --> PROVIDER["LLM Providers"]
    RAG --> LANCE_LIB["lancedb + pylance"]
    RAG --> SENTENCE["sentence-transformers"]
    RAG --> HTTPX["httpx"]

    ING --> LANCE_LIB
    ING --> PIL["Pillow + PyAV"]
    ING --> BOTO["boto3"]

    QR --> DUCKDB["duckdb"]
    QR --> DAFT["daft"]

    KG --> HUGE["hugegraph-client"]

    EMBED --> SENTENCE

    SDK --> REDIS_LIB["redis"]
    SDK --> STRUCTLOG["structlog"]
    SDK --> PYDANTIC["pydantic + pydantic-settings"]
    SDK --> TENACITY["tenacity"]
    SDK --> PROM["prometheus-client"]

    LANCE_LIB --> PYARROW["pyarrow"]
    DAFT --> PYARROW
```
