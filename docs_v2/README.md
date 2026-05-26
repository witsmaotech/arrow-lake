# Arrow Lake Documentation v2

> 🟢 Starter · 🟡 Professional · 🔴 Enterprise

**Arrow Lake** — End-to-end unified multimodal data lake platform + knowledge engineering.

Choose your role to get started:

| Role | Entry Point | Core Questions |
|------|------------|----------------|
| **Data Engineer** | [Data Plane Guide](data-plane/README.md) | How does data flow in? How is it stored? How do I query it? |
| **AI / ML Engineer** | [Knowledge Plane Guide](knowledge-plane/README.md) | How do I build RAG? How do I tune retrieval quality? |
| **Platform SRE** | [Compute Plane Guide](compute-plane/README.md) | How do I deploy? How do I monitor? How do I scale? |

## Quick Start

```bash
pip install arrow-lake
arrow-lake demo
```

→ [5-Minute Quickstart](quickstart.md)

## Architecture

[Three-Layer + Dual-Plane Architecture](concepts/architecture.md)

```
Application Layer (CLI · SDK · REST API)
    ↓
Service Layer (Data Plane ‖ Knowledge Plane)
    ↓
Kernel Layer (LanceDB · DuckDB · Daft · Ray)
```

## Documentation Map

```
docs_v2/
├── data-plane/          # Data lifecycle — ingest, store, query, export
├── knowledge-plane/     # Knowledge engineering — RAG, KG, embeddings, LLM
├── compute-plane/       # Platform ops — deploy, observe, scale
├── concepts/            # Cross-cutting — architecture, security, glossary
├── api/                 # OpenAPI reference (auto-generated)
└── reference/           # Configuration reference
```

## Legacy Docs

The previous documentation structure is preserved in `docs/` for reference during migration.
