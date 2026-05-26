# Gravitino Metadata Governance (Experimental)

> **Status: Experimental** — Arrow Lake v1.4.4 provides Gravitino integration for
> metadata storage, browsing, and deep governance. v1.4.2 introduced FQN injection prevention,
> Gravitino configuration via pydantic-settings, and enhanced security validation.
> See the capability matrix below for what works today vs. what's planned.
>
> This chapter documents the current state and shows how to use the available metadata APIs.
>
> Prerequisites: Arrow Lake v1.4.4 with `gravitino.enabled: true` and Docker Compose prod profile
> running (`gravitino` + `lance-rest` containers healthy).

### Current Capability Matrix

| Feature | Storage | Enforcement | Status |
|---------|---------|-------------|--------|
| Catalog/Table browsing | Gravitino | — | **Working** |
| Tag creation & association | Gravitino | No access control driven by tags | **Metadata only** |
| Retention policies | Gravitino | No automatic cleanup | **Metadata only** |
| Masking policies | Gravitino | No query result masking | **Metadata only** |
| Table statistics | Gravitino | Not consumed by query planner | **Metadata only** |
| Model versions | Gravitino | Not connected to embed/rag | **Metadata only** |
| RBAC bridge | Gravitino SDK | Falls back to local RBAC | **Fallback path** |
| Lineage integration | Table property | No cross-system lineage graph | **Shallow** |
| Federated queries | Path construction | No metadata-driven reads | **Path prefix only** |

***

## 1. Architecture Overview

### Proxy Architecture

Arrow Lake acts as a proxy for Gravitino metadata operations. Clients call `/metadata/*` endpoints on
the Arrow Lake API, which delegates to Gravitino via the REST API or Python SDK:

```text
Client → Arrow Lake API (/metadata/*)
            ├── GravitinoBridge (REST)      ← catalogs, tables, stats
            ├── GravitinoTagService (SDK)   ← tags
            ├── GravitinoPolicyService (SDK)← policies
            └── GravitinoModelRegistry (SDK)← models
                    ↓
            Apache Gravitino Server (:8090)
            Apache Lance REST Catalog (:9002)
```

### Metadata Hierarchy

```text
Metalake: arrow-lake
  ├── Catalog: lance-catalog     (RELATIONAL, lakehouse-generic)
  │     └── Schema: arrow_lake
  │           └── Tables: articles, sales, ...
  ├── Catalog: minio-fileset     (FILESET)
  │     └── Schema: arrow_lake
  │           └── Filesets: dataset paths
  └── Catalog: ml-models         (MODEL)
        └── Schema: default
              └── Models: text-embedder, image-classifier, ...
```

### Configuration

```yaml
# config.yaml
gravitino:
  enabled: true
  uri: "http://gravitino:8090"
  metalake: "arrow-lake"
  lance_rest_enabled: true
  lance_rest_uri: "http://lance-rest:9002"
  auth_type: simple
  sync_direction: bidirectional
  sync_interval_seconds: 30   # range: 5–300
```

All Gravitino calls wrap in `try/except` — if Gravitino is unavailable, Arrow Lake continues to
operate normally with local DuckDB catalog.

***

## 2. Scenario A — Data Discovery & Catalog Browsing

**Persona**: A new data engineer joining the team, exploring what datasets exist in the lake.

### Step 1: Check System Health

```bash
curl http://localhost:8000/health -H "X-API-Key: your-key"
```

```json
{
  "status": "ok",
  "version": "1.4.1",
  "storage": "accessible",
  "gravitino": "healthy",
  "lance_rest": "healthy"
}
```

When Gravitino is enabled, the health response includes `gravitino` and `lance_rest` fields.

### Step 2: Browse Catalogs

```bash
curl http://localhost:8000/metadata/catalogs -H "X-API-Key: your-key"
```

```json
{
  "success": true,
  "data": [
    {"name": "lance-catalog"},
    {"name": "minio-fileset"},
    {"name": "ml-models"}
  ],
  "error": null,
  "metadata": {"total": 3}
}
```

Three catalogs serve different purposes:
- **lance-catalog**: Relational tables backed by Lance datasets
- **minio-fileset**: File-level access to MinIO objects
- **ml-models**: ML model version registry

### Step 3: List Tables

```bash
curl http://localhost:8000/metadata/tables -H "X-API-Key: your-key"
```

```json
{
  "success": true,
  "data": [
    {"name": "articles"},
    {"name": "sales"},
    {"name": "transactions"}
  ],
  "error": null,
  "metadata": {"total": 3}
}
```

### Step 4: Inspect Table Schema

```bash
curl http://localhost:8000/metadata/tables/articles -H "X-API-Key: your-key"
```

```json
{
  "success": true,
  "data": {
    "name": "articles",
    "columns": [
      {"name": "id", "type": "long"},
      {"name": "title", "type": "string"},
      {"name": "text_content", "type": "string"},
      {"name": "text_embedding", "type": "binary"},
      {"name": "published_at", "type": "timestamp"}
    ],
    "properties": {
      "format": "lance",
      "owner": "data-team"
    }
  },
  "error": null,
  "metadata": {}
}
```

### Python (httpx)

```python
import httpx

BASE = "http://localhost:8000"
H = {"X-API-Key": "your-key"}

# Browse catalogs → tables → detail
for cat in httpx.get(f"{BASE}/metadata/catalogs", headers=H).json()["data"]:
    print(f"Catalog: {cat['name']}")

for tbl in httpx.get(f"{BASE}/metadata/tables", headers=H).json()["data"]:
    detail = httpx.get(f"{BASE}/metadata/tables/{tbl['name']}", headers=H).json()
    cols = detail["data"]["columns"]
    print(f"  {tbl['name']}: {len(cols)} columns")
```

### Key Takeaway

You can discover the full metadata hierarchy without knowing table names in advance — browse catalogs,
then list tables, then inspect individual schemas.

***

## 3. Scenario B — Data Classification with Tags

**Persona**: A data steward tagging datasets for GDPR compliance, ensuring PII columns are
properly classified.

> **Note**: Tags are currently stored in Gravitino but **do not drive access control or automatic
> masking**. They serve as metadata labels for discovery and auditing. Enforcement was enhanced in v1.4.2.

### Predefined Tags

`GravitinoTagService` ships with common governance tags:

| Tag | Purpose |
|-----|---------|
| `sensitive` | Contains sensitive information |
| `pii` | Personal identity information |
| `financial` | Financial or billing data |
| `expires:30d` | 30-day data retention marker |

### Step 1: Create Custom Tags

```bash
# Create a tag for GDPR-regulated data
curl -X POST "http://localhost:8000/metadata/tags?body=%7B%22name%22%3A%22gdpr_subject%22%2C%22comment%22%3A%22Data%20subject%20under%20GDPR%22%7D" \
  -H "X-API-Key: your-key"
```

### Step 2: Associate Tags with Tables (Python SDK)

Tag-table and tag-column associations require the Python SDK directly (no REST endpoint yet):

```python
from arrow_lake.config import GravitinoConfig
from arrow_lake.quality.gravitino_tags import GravitinoTagService

cfg = GravitinoConfig(enabled=True, uri="http://localhost:8090", metalake="arrow-lake")
tags = GravitinoTagService(cfg)

# Tag an entire table
tags.tag_table("users", ["pii", "sensitive"])

# Tag specific columns
tags.tag_column("users", "email", ["pii"])
tags.tag_column("users", "phone", ["pii"])

# Discover all tables with a tag
pii_tables = tags.get_tables_by_tag("pii")
# → ["users", "customers", ...]
```

### Step 3: List Tags via REST

```bash
# List all tags
curl http://localhost:8000/metadata/tags -H "X-API-Key: your-key"

# List tags for a specific table
curl "http://localhost:8000/metadata/tags?table=users" -H "X-API-Key: your-key"
```

```json
{
  "success": true,
  "data": [{"name": "pii"}, {"name": "sensitive"}],
  "error": null,
  "metadata": {"total": 2}
}
```

### Tag Governance Workflow

```text
1. Create tags (define classification taxonomy)
2. Tag tables/columns (apply classification)
3. List tags per table (audit classification)
4. Get tables by tag (discover governed assets)
```

### Key Takeaway

Tags provide a lightweight classification system. Use column-level tagging for fine-grained governance
(e.g., marking individual PII columns) and table-level tagging for broad categories (e.g., "financial").

***

## 4. Scenario C — Compliance Policies: Retention & Masking

**Persona**: A compliance officer enforcing data retention rules and column-level masking.

> **Note**: Policies are currently stored in Gravitino but **not automatically enforced**. Creating a
> retention policy does not trigger data deletion; creating a masking policy does not transform query
> results. Enforcement was enhanced in v1.4.2.

### Step 1: Create Retention Policy

```bash
# Retain log data for 90 days only
curl -X POST "http://localhost:8000/metadata/policies/retention?body=%7B%22name%22%3A%22log_retention_90d%22%2C%22days%22%3A90%7D" \
  -H "X-API-Key: your-key"
```

```json
{"success": true, "data": {"name": "log_retention_90d", "days": 90}, "error": null, "metadata": {}}
```

### Step 2: Create Masking Policy

```bash
# Mask email and phone columns
curl -X POST "http://localhost:8000/metadata/policies/masking?body=%7B%22name%22%3A%22email_mask%22%2C%22columns%22%3A%5B%22email%22%2C%22phone%22%5D%7D" \
  -H "X-API-Key: your-key"
```

```json
{"success": true, "data": {"name": "email_mask", "columns": ["email", "phone"]}, "error": null, "metadata": {}}
```

### Step 3: Apply Policy to a Table (Python SDK)

```python
from arrow_lake.quality.gravitino_policies import GravitinoPolicyService

svc = GravitinoPolicyService(cfg)
svc.apply_policy("log_retention_90d", "access_logs")
svc.apply_policy("email_mask", "users")
```

### Step 4: List All Policies

```bash
curl http://localhost:8000/metadata/policies -H "X-API-Key: your-key"
```

```json
{
  "success": true,
  "data": [
    {"name": "log_retention_90d"},
    {"name": "email_mask"}
  ],
  "error": null,
  "metadata": {"total": 2}
}
```

### Compliance Checklist Pattern

```python
import httpx

H = {"X-API-Key": "your-key"}
BASE = "http://localhost:8000"

# Verify all PII tables have masking policies
pii_tables = ["users", "customers", "orders"]
for table in pii_tables:
    resp = httpx.get(f"{BASE}/metadata/policies", headers=H).json()
    has_masking = any("mask" in p["name"] for p in resp.get("data", []))
    status = "OK" if has_masking else "MISSING"
    print(f"  {table}: masking policy {status}")
```

### Key Takeaway

Policies separate governance intent from enforcement. Define retention and masking rules declaratively,
then apply them to tables. The policy engine handles cleanup and data transformation.

***

## 5. Scenario D — ML Model Lifecycle Management

**Persona**: An ML engineer managing model versions for production deployments.

> **Note**: The model registry is currently a standalone metadata store. The `embed/` and `rag/`
> modules do **not** yet read model versions from Gravitino. Model hot-swap requires manual
> integration. Full ML pipeline integration was enhanced in v1.4.2.

### Step 1: Register a Model

```python
from arrow_lake.catalog.gravitino_models import GravitinoModelRegistry

registry = GravitinoModelRegistry(cfg)

# Register a new embedding model
registry.register_model(
    name="text-embedder",
    comment="Text embedding model for RAG pipeline",
    properties={"framework": "sentence-transformers", "dimension": "768"},
)
```

### Step 2: Add Versions and Promote

```python
# Add version 1
registry.add_version(
    name="text-embedder",
    uri="s3://models/text-embedder/v1",
    aliases=["latest"],
)

# Add version 2 (improved model)
registry.add_version(
    name="text-embedder",
    uri="s3://models/text-embedder/v2",
    aliases=["latest"],
)

# Promote version 2 to production
registry.add_version(
    name="text-embedder",
    uri="s3://models/text-embedder/v2",
    aliases=["production"],
)
```

### Step 3: Query Model Versions via REST

```bash
curl http://localhost:8000/metadata/models -H "X-API-Key: your-key"
```

```json
{"success": true, "data": [{"name": "text-embedder"}], "error": null, "metadata": {"total": 1}}
```

```bash
curl http://localhost:8000/metadata/models/text-embedder/versions -H "X-API-Key: your-key"
```

```json
{
  "success": true,
  "data": [
    {"version": 2, "uri": "s3://models/text-embedder/v2", "aliases": ["latest"], "tier": "latest"},
    {"version": 2, "uri": "s3://models/text-embedder/v2", "aliases": ["production"], "tier": "production"}
  ],
  "error": null,
  "metadata": {"model": "text-embedder", "total": 2}
}
```

### Hot-Swap Pattern

```python
# In your application startup code:
latest = registry.get_latest_version("text-embedder")
prod = registry.get_production_version("text-embedder")

# Use production for serving, latest for canary testing
serving_uri = prod.uri       # → s3://models/text-embedder/v2
canary_uri = latest.uri      # → s3://models/text-embedder/v2
```

To hot-swap: update the `production` alias in Gravitino. Next application restart picks up the new version.

### Key Takeaway

The Model Catalog separates version management from model serving. Use aliases (`latest`, `production`)
to control which version is used where — update the alias, not the code.

***

## 6. Scenario E — Statistics-Driven Query Optimization

**Persona**: A performance engineer collecting table statistics to improve query plans.

> **Note**: Statistics are collected and stored in Gravitino but **not consumed by DuckDB's query
> planner**. They serve as metadata for monitoring. Query planner integration was enhanced in v1.4.2.

### Step 1: Collect Statistics

```bash
curl -X POST http://localhost:8000/metadata/statistics/articles \
  -H "X-API-Key: your-key"
```

```json
{
  "success": true,
  "data": {
    "name": "articles",
    "row_count": 50000,
    "column_count": 8,
    "size_mb": 125.4,
    "columns": [
      {"name": "id", "type": "long"},
      {"name": "title", "type": "string"},
      {"name": "text_content", "type": "string"}
    ]
  },
  "error": null,
  "metadata": {}
}
```

### Step 2: How Stats Work Internally

`GravitinoStatsCollector` runs DuckDB queries against the table:

```python
from arrow_lake.catalog.gravitino_stats import GravitinoStatsCollector

collector = GravitinoStatsCollector(cfg)
stats = collector.collect_table_stats("articles", duckdb_connection)
# Stats registered as Gravitino table properties with "stats." prefix
collector.register_stats("articles", stats)
```

Statistics are stored as table properties in Gravitino (prefixed `stats.*`). Query engines can read
these to make better join ordering and filter pushdown decisions.

### Scheduled Collection

The background `GravitinoSyncScheduler` can be configured to periodically collect statistics:

```yaml
gravitino:
  sync_interval_seconds: 300   # Collect stats every 5 minutes
```

Stats collection triggers automatically after data ingestion via the `CatalogActor` integration.

### Key Takeaway

Statistics bridge the gap between metadata and query performance. Collect them regularly (especially
after large ingests) so query planners have accurate row counts and cardinality estimates.

***

## 7. Scenario F — Health & Graceful Degradation

**Persona**: An SRE verifying system resilience when Gravitino is unavailable.

### Degradation Matrix

| Feature | Gravitino UP | Gravitino DOWN |
|---------|-------------|----------------|
| Data ingestion | Normal + Gravitino sync | Normal (local DuckDB only) |
| Vector/FTS search | Normal | Normal |
| OLAP queries | Normal + federated | Normal |
| `/metadata/*` endpoints | Full data | 503 Service Unavailable |
| Tags & Policies | Full CRUD | 503 or empty results |
| Model registry | Full CRUD | 503 |
| Health check | Shows `gravitino: healthy` | Shows `gravitino: unhealthy` |

### Health Check

```python
import httpx

resp = httpx.get("http://localhost:8000/health").json()
if resp.get("gravitino") != "healthy":
    print("WARNING: Gravitino unavailable — metadata features degraded")
    print("All core features (ingest, search, query) remain functional.")
```

### Application-Level Degradation

```python
# Safe metadata access pattern
def safe_get_table_detail(client: ArrowLakeClient, name: str) -> dict | None:
    """Get table detail, gracefully handling Gravitino unavailability."""
    resp = client.metadata_get_table(name)
    if resp.get("success"):
        return resp["data"]
    if resp.get("status") == 503:
        print(f"  Gravitino unavailable, using local catalog for {name}")
        return client.get_dataset(name)  # Fallback to DuckDB
    return None
```

### Key Takeaway

Arrow Lake is designed for **graceful degradation**: Gravitino is an enhancement layer, not a hard
dependency. Core operations always work; metadata governance features degrade to 503 when unavailable.

***

## 8. Background Sync & Bidirectional Reconciliation

`GravitinoSyncScheduler` runs as a background daemon thread within the Arrow Lake API process:

```text
┌──────────────────────────────────────────────────┐
│           GravitinoSyncScheduler                  │
│                                                   │
│  Every sync_interval_seconds:                     │
│    1. sync_outbound: DuckDB → Gravitino Tables    │
│       (push local catalog entries as Gravitino    │
│        tables + filesets)                         │
│    2. sync_inbound: Gravitino → DuckDB            │
│       (pull external filesets into local catalog) │
│                                                   │
│  Thread-safe via GravitinoBridge.lock             │
└──────────────────────────────────────────────────┘
```

### Sync Direction Configuration

| Direction | Behavior |
|-----------|----------|
| `outbound` | DuckDB → Gravitino only |
| `inbound` | Gravitino → DuckDB only |
| `bidirectional` | Both directions (default) |

The scheduler starts and stops with the API server lifecycle (via FastAPI `lifespan`).

### Sync Example

```python
from arrow_lake.catalog.gravitino_bridge import GravitinoBridge

bridge = GravitinoBridge(cfg)

# Push local catalog to Gravitino
entries = catalog_actor.list_all()
synced = bridge.sync_outbound(entries)
print(f"Synced {synced} tables to Gravitino")

# Pull external tables from Gravitino
external = bridge.sync_inbound()
print(f"Discovered {len(external)} external tables")
```

***

## 9. Security & RBAC Bridge

### Authentication Types

| Type | Use Case |
|------|----------|
| `simple` | Development / testing (default) |
| `oauth` | Production with OAuth 2.0 provider |
| `kerberos` | Enterprise Hadoop environments |

### Permission Mapping

`GravitinoRBACBridge` maps Arrow Lake actions to Gravitino privileges:

| Arrow Lake Action | Gravitino Privilege |
|-------------------|---------------------|
| `read` | `SELECT_TABLE` |
| `write` | `INSERT_TABLE` |
| `create` | `CREATE_TABLE` |
| `delete` | `DELETE_TABLE` |
| `admin` | `CREATE_CATALOG` |

### Fallback Behavior

When Gravitino RBAC check fails (network error, service down), the bridge returns `None`, signaling
Arrow Lake to fall back to the local JWT/RBAC system. This ensures access control is always enforced,
even during Gravitino outages.

```python
from arrow_lake.api.rbac import GravitinoRBACBridge

rbac = GravitinoRBACBridge(cfg)
result = rbac.check_permission("user@example.com", "articles", "read")
# result: True (allowed), False (denied), None (fallback to local RBAC)
```

***

## 10. Best Practices & Anti-Patterns

### Tag Governance

| Practice | Guideline |
|----------|-----------|
| Naming | Lowercase, underscore, domain prefix: `pii`, `fin_revenue`, `gdpr_subject` |
| Granularity | Tag columns, not just tables — enables fine-grained masking |
| Discovery | Use `get_tables_by_tag()` for compliance audits |
| Avoid | Creating a tag for every column (tag explosion) |

### Policy Management

| Practice | Guideline |
|----------|-----------|
| Naming | `{domain}_{type}_{scope}`: `gdpr_retention_90d`, `fin_mask_email` |
| Retention | Use policies instead of ad-hoc DELETE for compliance |
| Masking | Apply to PII columns before granting analyst access |
| Review | Audit policies quarterly — remove stale ones |

### Model Registry

| Practice | Guideline |
|----------|-----------|
| Aliases | Always maintain `production` and `latest` aliases |
| Hot-swap | Update alias in Gravitino, not in application code |
| Versioning | Never reuse version numbers — always increment |
| URI | Use immutable URIs (e.g., `s3://models/name/v3`, not `s3://models/name/latest`) |

### Performance

| Practice | Guideline |
|----------|-----------|
| Stats | Collect after large ingests, schedule off-peak |
| Sync | 30s default is sufficient; don't set below 5s |
| Health | Check Gravitino health before batch operations |
| Degradation | Design clients to handle 503 gracefully |

### Common Anti-Patterns

- **Skipping health checks**: Always verify Gravitino availability before batch governance operations.
- **Real-time sync expectations**: Background sync is eventual (5–300s lag). Don't rely on it for
  real-time consistency.
- **Tagging everything**: Over-tagging makes governance harder, not easier. Focus on compliance-relevant
  classifications.
- **Ignoring 503 responses**: Treat 503 from `/metadata/*` as "feature unavailable," not an error
  requiring retry.

***

## Quick Reference

### Endpoint Summary

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/metadata/catalogs` | List Gravitino catalogs |
| `GET` | `/metadata/tables` | List tables in Lance catalog |
| `GET` | `/metadata/tables/{name}` | Table details (columns, properties) |
| `GET` | `/metadata/tags` | List tags (optional `?table=`) |
| `POST` | `/metadata/tags` | Create tag |
| `GET` | `/metadata/policies` | List policies |
| `POST` | `/metadata/policies/retention` | Create retention policy |
| `POST` | `/metadata/policies/masking` | Create masking policy |
| `POST` | `/metadata/statistics/{name}` | Collect table statistics |
| `GET` | `/metadata/models` | List ML models |
| `GET` | `/metadata/models/{name}/versions` | Model version info |

### Runnable Example

```bash
# Full Gravitino governance workflow (12 steps)
python docs/cookbook/examples_api/33_gravitino_metadata_governance.py
```

***

## v1.4.2 — Deep Governance Integration

> The following capabilities close the governance loop: policies are enforced at query time,
> statistics drive query routing, model versions are resolved from Gravitino, and lineage is
> modeled as table properties.

### Retention Policy Enforcement

Retention policies are enforced by a background `RetentionEnforcer` thread that periodically
reads policies from Gravitino and calls `LanceDataset.cleanup_old_versions()`:

```bash
# Manual trigger (dry-run first)
curl -X POST "http://localhost:8000/metadata/policies/enforce?dry_run=true" \
  -H "X-API-Key: your-key"

# Actual enforcement for a specific table
curl -X POST "http://localhost:8000/metadata/policies/enforce?table=access_logs" \
  -H "X-API-Key: your-key"
```

Configuration: `retention_enforce_interval_seconds: 3600` (default hourly).

### Column-Level Masking at Query Time

When a masking policy is applied to a table, the `MaskingEngine` intercepts query results in
`apply_table_filter()`. Non-admin roles see masked values automatically:

```python
# In rbac.py apply_table_filter():
# 1. Column/row ACL filtering
# 2. MaskingEngine.apply_masking(table, dataset, role)
#    - redact: replace all chars with *
#    - hash: SHA-256 truncated to 16 chars
#    - partial: keep first/last 2 chars
#    - nullify: replace with null

# Viewer querying a table with email masking sees:
# email: "user@test.com" → "*************"
# name: "Alice" → "Alice" (not masked)
```

### Tag-Driven Access Control

`TagAwareACLResolver` periodically syncs Gravitino column tags to local ACLs:

```yaml
# config.yaml
gravitino:
  tag_access_rules:
    pii:       {visible_to: ["admin"]}
    sensitive: {visible_to: ["admin", "editor"]}
```

When column `email` is tagged `pii`, non-admin roles automatically have that column excluded
from query results via the existing `PermissionChecker` pipeline.

### Statistics-Driven Query Routing

`StatsInjector` reads table statistics from Gravitino and provides hints:

```python
from arrow_lake.query.stats_injector import StatsInjector

injector = StatsInjector(config.gravitino)
hints = injector.get_hints("large_table")
# QueryHints(estimated_rows=5_000_000, column_count=12, size_mb=250.0)

if hints.estimated_rows > config.gravitino.stats_auto_route_threshold:
    # Auto-route to DuckDB OLAP (streaming) instead of Daft (in-memory)
    pass
```

### Model Registry Resolution

`RegistryModelResolver` bridges Gravitino Model Catalog to embed/rag modules:

```python
from arrow_lake.embed.registry_resolver import RegistryModelResolver

resolver = RegistryModelResolver(config.gravitino)
model_path = resolver.resolve_model_path("text-embedder")
# → "s3://models/text-embedder/v2" (from Gravitino production version)

# In encoder.py: LocalEmbeddingEncoder can use this instead of hardcoded model_name
```

### Lineage as Table Properties

Lineage events now write rich metadata to Gravitino table properties:

```bash
curl http://localhost:8000/metadata/lineage/articles -H "X-API-Key: your-key"
```

```json
{
  "success": true,
  "data": {
    "table": "articles",
    "operation": "ingest",
    "timestamp": "2026-05-22T10:30:00",
    "sources": ["raw/articles.csv"],
    "outputs": ["articles"],
    "lance_version": "5"
  }
}
```

### Federated Query with Metadata Resolution

`FederatedQueryEngine` resolves table metadata (format, location) from Gravitino before reading:

```python
from arrow_lake.query.federated_engine import FederatedQueryEngine

engine = FederatedQueryEngine(config.gravitino)
resolution = engine.resolve_table("hive-catalog.default.orders")
# → TableResolution(format="parquet", location="s3://warehouse/orders")

df = engine.load_dataset("hive-catalog.default.orders")
# → daft.read_parquet("s3://warehouse/orders")  (auto-detected from metadata)
```

