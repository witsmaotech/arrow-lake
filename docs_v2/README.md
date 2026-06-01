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

→ [5-Minute Quickstart](../docs/quickstart.md)

## CLI Navigation (v1.5.2)

The CLI organizes commands by user goal:

```bash
arrow-lake knowledge    # Build knowledge base (RAG + knowledge graph)
arrow-lake connect      # Connect data sources (ingest + import)
arrow-lake analyze      # Analyze data (SQL OLAP)
arrow-lake search       # Find information (vector/FTS/hybrid)
arrow-lake govern       # Govern data (RBAC + metadata + audit)
```

→ [CLI Guide — full command reference with examples](reference/cli-guide.md)

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
├── compute-plane/       # Platform ops — deploy, observe, scale, SLO
├── concepts/            # Cross-cutting — architecture, security, glossary
├── api/                 # OpenAPI reference (78 endpoints, auto-generated)
└── reference/           # Configuration + CLI guide
```

## Key References

| Document | Description |
|----------|-------------|
| [OpenAPI 3.1 Spec](api/openapi.yaml) | 78 REST API endpoints, 119 schemas |
| [Configuration Reference](reference/configuration.md) | 28 config sections, env vars, YAML examples |
| [CLI Guide](reference/cli-guide.md) | Scene-based command reference + error messages |
| [Security Whitepaper](concepts/security.md) | STRIDE threat model, RBAC, injection prevention |
| [Glossary](concepts/glossary.md) | 30+ canonical terms, deprecated aliases |
| [SLO & Dependency Criticality](compute-plane/slo-and-criticality.md) | SLO targets, dependency tiers, degradation |
| [Dependency Matrix](../DEPENDENCY_COMPATIBILITY_MATRIX.md) | Tested version combinations, upgrade risk |

## Legacy Docs

The previous documentation structure is preserved in `docs/` for reference during migration.
