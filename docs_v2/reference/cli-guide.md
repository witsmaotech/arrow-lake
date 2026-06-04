# CLI Guide — Scene-Based Navigation

**Maturity:** 🟢 Starter | **Last Updated:** 2026-05-26

Arrow Lake CLI (v1.5.3+) organizes commands by **user goal**, not technical module. Run `arrow-lake --help` to see the full list.

---

## Quick Start

```bash
arrow-lake demo                          # Interactive demo (~15s)
arrow-lake serve --port 8000             # Start REST API server
arrow-lake version                      # Show version + dependencies
```

## Scene-Based Navigation

### 🔍 search — Find information

```bash
arrow-lake search vector <dataset> --query "embedding vector"    # Vector similarity
arrow-lake search fts <dataset> --query "keyword search"         # Full-text search
arrow-lake search hybrid <dataset> --query "best of both"        # Hybrid (RRF fusion)
arrow-lake search faceted <dataset> --query "filterable"         # Faceted search
arrow-lake search ensemble <dataset> --query "multi-column"      # Cross-column RRF
```

### 📚 knowledge — Build knowledge base

```bash
arrow-lake knowledge                   # Show knowledge building commands
arrow-lake rag query "your question"   # Ask a question (RAG)
arrow-lake rag stream "your question"  # Stream RAG response
arrow-lake rag history                 # View session history
arrow-lake kg build <dataset>          # Build knowledge graph from dataset
arrow-lake kg status <task_id>         # Check KG build status
arrow-lake kg stats                    # Show knowledge graph statistics
arrow-lake kg query <gremlin_query>    # Execute a Gremlin query
arrow-lake kg neighbors <entity_id>    # Get entity neighbors (--depth N)
arrow-lake kg export [--output FILE]   # Export graph as JSON
arrow-lake kg import <file_path>       # Import graph from JSON file
arrow-lake kg delete [--yes]           # Delete all graph data (irreversible)
arrow-lake index vector <dataset>      # Build vector index
arrow-lake index fts <dataset>         # Build FTS index
```

#### kg traverser — Graph traversal algorithms

```bash
arrow-lake kg traverser all-shortest-paths <source> <target>  # All shortest paths
arrow-lake kg traverser weighted-shortest <source> <target>   # Weighted shortest path
arrow-lake kg traverser single-source-shortest <source>       # Single-source shortest path
arrow-lake kg traverser multi-node-shortest --sources '[...]' --targets '[...]'  # Multi-pair shortest
arrow-lake kg traverser rays <source>                         # Non-cyclic paths from source
arrow-lake kg traverser rings <source>                        # Cyclic paths from source
arrow-lake kg traverser crosspoints <source> <target>         # Vertices on paths between pair
arrow-lake kg traverser customized <source> --steps '[...]'   # Custom multi-step traversal
```

#### kg algo — Graph OLAP algorithms (Vermeer)

```bash
arrow-lake kg algo pagerank                # PageRank importance ranking
arrow-lake kg algo louvain                 # Louvain community detection
arrow-lake kg algo label-propagation       # Label Propagation communities
arrow-lake kg algo wcc                     # Weakly Connected Components
arrow-lake kg algo triangle-count          # Triangle counting
arrow-lake kg algo degree-centrality       # Degree centrality
arrow-lake kg algo closeness-centrality    # Closeness centrality
arrow-lake kg algo betweenness-centrality  # Betweenness centrality
arrow-lake kg algo k-core                  # K-core decomposition (--k 3)
```

### 🔗 connect — Connect data sources

```bash
arrow-lake connect                     # Show data connection commands
arrow-lake ingest files <dataset> <paths>...   # Ingest files
arrow-lake ingest create <dataset> <file>       # Create from CSV/Parquet/JSON
arrow-lake ingest documents <dataset> <paths>   # Ingest PDFs with OCR
arrow-lake ingest images <dataset> <paths>      # Ingest images
arrow-lake catalog list                # List all datasets
arrow-lake catalog info <dataset>      # Dataset details
```

### 📊 analyze — Analyze data

```bash
arrow-lake analyze                     # Show analysis commands
arrow-lake query sql <dataset> --sql "SELECT category, COUNT(*) FROM docs GROUP BY category"
arrow-lake query daft <dataset>        # Daft DataFrame view
arrow-lake export <dataset>            # Export to Parquet/CSV
arrow-lake quality dedup <dataset>     # Deduplicate records
arrow-lake lifecycle status <dataset>  # Check lifecycle policy
arrow-lake lifecycle estimate --size-gb 100 --target-tier GLACIER  # Cost savings estimate
arrow-lake lifecycle rules [--prefix PREFIX]    # Preview lifecycle rules
arrow-lake lifecycle config                     # Show current lifecycle config
```

### 🛡️ govern — Govern data

```bash
arrow-lake govern                      # Show governance commands
arrow-lake audit record                # Record audit entry
arrow-lake audit verify                # Verify HMAC-SHA256 audit trail
arrow-lake audit query                 # Query audit history
arrow-lake lineage record              # Record data lineage
arrow-lake maintenance status          # Check maintenance status
arrow-lake backup create               # Create backup
```

### ⚙️ Technical Commands (preserved)

All original technical commands remain available:

| Command | Purpose |
|---------|---------|
| `ingest` | Data ingestion (files, documents, images, videos, create, append) |
| `search` | Search (vector, FTS, hybrid, faceted, ensemble) |
| `query` | Query (SQL, Daft DataFrame, metadata, materialized views) |
| `index` | Index management (vector, FTS, rebuild, delete) |
| `rag` | RAG pipeline (query, stream, sessions, templates, feedback) |
| `kg` | Knowledge graph (build, query, traverser, algorithms) |
| `catalog` | Dataset management (list, info, delete, rename, copy) |
| `embed` | Embedding (text, image) |
| `quality` | Data quality (dedup, filter) |
| `audit` | Audit trail (record, verify, query, export) |
| `lineage` | Data lineage (record, history, query) |
| `backup` | Backup/restore (create, list, restore, delete) |
| `lifecycle` | Lifecycle management (apply, status, restore) |
| `config` | Configuration (show, init) |
| `maintenance` | Maintenance (status, run) |

---

## Error Messages (v1.5.2)

The CLI translates common low-level errors into actionable messages:

| What Happened | What You See |
|-------------|-------------|
| Column name typo | `Column 'embedding_vector' doesn't exist. Available: [id, text, metadata].` |
| No search index | `No search index found. Run: arrow-lake index vector <dataset>` |
| Dataset doesn't exist | `Dataset 'my_data' does not exist. Run: arrow-lake catalog list` |
| Redis down | `Cannot connect to redis. Check: docker compose ps redis` |
| Unsupported file format | `File format '.xlsx' is not supported. Use .csv, .json, .jsonl, .parquet` |

---

## Global Options

```bash
--base-uri TEXT            Lake base URI (default: ./data/lake, env: ARROW_LAKE_BASE_URI)
--config TEXT              Path to YAML config file
-v, --verbose              Increase verbosity (-v, -vv)
-q, --quiet                Suppress non-error output
--format [table|json|csv]  Output format (default: table)
--version                  Show version
--help                     Show help
```
