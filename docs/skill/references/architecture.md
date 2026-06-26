# Architecture — Facade, Mixins, Protocols, Config

> Back-reference: [../SKILL.md](../SKILL.md). Verified against `arrow_lake` v1.7.0 (2026-06-25).

## The `Lake` Facade

`Lake` (`arrow_lake/__init__.py`) is the single SDK entry point. It composes **9 mixins** via multiple inheritance and lazy-loads every heavy component through one thread-safe cache.

```python
class Lake(
    _LakeBaseMixin, _LakeIngestMixin, _LakeSearchMixin, _LakeQueryMixin,
    _LakeAdminMixin, _LakeLineageMixin, _LakeAuditMixin, _LakeRAGMixin, _LakeKGMixin,
):
    def __init__(self, base_uri: str = "./data", config: ArrowLakeConfig | None = None): ...
    def _get_component(self, key: str, factory: Callable) -> Any: ...   # lazy + RLock
    def shutdown(self) -> None: ...                                    # graceful
    def __enter__/__exit__/__del__: ...                                # context manager
    @classmethod
    def from_yaml(cls, path: str, *, base_uri=None) -> Lake: ...
```

**Lifecycle rules:**
- Prefer `with Lake(...) as lake:` — `__exit__` calls `shutdown()`, which closes sync clients, awaits async ones (`aclose()`), and clears the component cache.
- Forgetting `shutdown()` triggers `ResourceWarning` in `__del__`.
- `_get_component(key, factory)` is the only sanctioned way to obtain a component — it guarantees the `threading.RLock` guard. **v1.6.1 changed Lock→RLock** because nested calls (e.g. `_create_kg_builder` → `_get_kg_client` + `_get_kg_extractor`) deadlocked under a plain Lock.

## Mixin Method Reference (v1.7.0 signatures)

> `⚡` = `async` (must `await`). Return types shown where non-`None`.

### `_LakeBaseMixin`
Core lifecycle, config, storage, shared HTTP clients. `health()`/`version()` live in Admin (below).

### `_LakeSearchMixin` — all sync
```python
search(dataset_name, query_vector: list[float], *, top_k=10, metric=None,
       vector_column="text_embedding", where=None, ...)              # vector ANN
text_search(dataset_name, query: str, *, top_k=None, fts_column=None,
            where=None, version=None, ...)                           # Tantivy BM25
hybrid_search(dataset_name, query_vector, query_text, *, top_k=None,
              vector_column="text_embedding", fts_column=None, ...)  # RRF (vec + text)
faceted_search(...)                                                  # multi-col metadata
ensemble_search(...)                                                 # cross-column RRF
create_vector_index(...) / create_fts_index(...)
list_vector_indexes(dataset_name) -> list[IndexInfo]
get_vector_index_info(...) / get_fts_index_info(...)
rebuild_vector_index(...) / delete_vector_index(...) / delete_fts_index(...)
```

### `_LakeQueryMixin` — all sync
```python
olap_query(dataset_name, sql, *, max_rows=None, tables=None) -> OlapQueryResult   # DuckDB; NO params arg
sql_query(...)                          # semantic alias for olap_query
query(...)                              # generic query
materialize(...) / cleanup_materialized(ttl_days=None)   # DuckLake materialized views
export(...) / daft_query(...)
```

### `_LakeIngestMixin` — all sync
```python
create_dataset(name, data: pa.Table)              # primary write; name regex ^[a-zA-Z_][a-zA-Z0-9_-]*$
ingest(dataset_name, file_paths, *, transforms=None) -> IngestionReport
ingest_batch(...) / ingest_sql(...) / ingest_kafka(...) / ingest_iceberg(...)
ingest_deltalake(...) / ingest_http(...)
ingest_images(...) / ingest_videos(...) / ingest_mixed(...) / ingest_documents(...)
ingest_and_embed(...)
append_dataset(name, data)
upsert(dataset_name, data, *, on="id")
update_rows(dataset_name, where, values: dict) / delete_rows(...)
quality_filter(dataset_name, active_filters="", *, mode="all") -> QualityReport
deduplicate(dataset_name, *, strategy=None, action=None, perceptual_threshold=None) -> DedupResult
export_to(...)
```

### `_LakeRAGMixin` — ⚡ all async (except history/feedback)
```python
⚡ rag_query(question, dataset_name, *, top_k=None, strategy=None, template_name=None, ...)
⚡ rag_query_stream(...) / ⚡ rag_batch_query(...) / ⚡ rag_extract(...)
rag_get_history(session_id) -> list[dict]          # sync
rag_feedback(...) / rag_get_feedback(...)          # sync
rag_cleanup_expired_sessions() -> int              # sync
```

### `_LakeKGMixin` — ⚡ all async
```python
⚡ kg_build(dataset_name) -> str                    # fire-and-forget → task_id (v1.6.1)
⚡ kg_build_status(task_id) -> dict | None
⚡ kg_query(query: str, *, traversal_depth=None) -> list[dict]   # Gremlin
⚡ kg_get_neighbors(...) / ⚡ kg_stats(...) / ⚡ kg_graph_exists(...)
⚡ kg_ensure_graph(...) / ⚡ kg_delete_graph(...)
⚡ kg_all_shortest_paths(...) / ⚡ kg_weighted_shortest_path(...)
⚡ kg_single_source_shortest_path(...) / ⚡ kg_multi_node_shortest_path(...)
⚡ kg_rays(...) / ⚡ kg_rings(...)
```
Backed by two engines: `HugeGraphClient` (query/traverse) + `VermeerClient` (bulk build). Private factories: `_get_kg_client`, `_get_kg_builder`, `_get_kg_retriever`, `_get_kg_extractor`, `_get_vermeer_client`.

### `_LakeAdminMixin` — all sync
```python
catalog() -> CatalogResult
list_datasets() -> list[str] ; open_dataset(name) ; read_dataset(name, *, columns=None) ; scan_dataset(name, **kw)
delete_dataset(name) ; restore_dataset(name, data) ; rename_dataset(name, new_name)
copy_dataset(name, new_name) ; merge_datasets(source_names, target_name)
# versioning & tags
get_dataset_version(name) -> int ; list_dataset_versions(name) -> list[dict]
create_tag(ds, tag, version=None) ; read_at_tag(ds, tag) ; list_tags(ds) ; delete_tag(ds, tag)
# schema evolution
add_column(name, column_name, sql_expr) ; add_columns_table(name, columns)
alter_column(name, column_name, new_type) ; drop_column(name, column_name) ; compact_dataset(name)
# backup
backup_create(...) ; backup_restore(...) ; backup_list() -> list[BackupInfo] ; backup_delete(id)
# ops
health() -> HealthInfo ; version() -> str ; lifecycle_apply(prefix="") -> dict
# Metaflow
list_flows() -> list[str] ; get_flow_info(name) -> dict
```

### `_LakeLineageMixin` / `_LakeAuditMixin`
Lineage tracking; HMAC-SHA256 tamper-evident audit trail. See [deployment.md](deployment.md) for audit/security.

## Design Patterns in Use

### Facade + Mixin (why)
9 domain surfaces, one object. MRO resolves precedence; each mixin is independently unit-testable. `_get_component` centralizes lazy init + caching + locking — components are created once, on first use, never at construction.

### Bridge (why)
Query modes are isolated classes (`VectorSearchBridge`, `FullTextSearchBridge`, `HybridSearchBridge`, `FacetedSearchBridge`, `EnsembleSearchBridge`, `OlapQueryBridge`) sharing one `DuckDBSessionManager`. Adding a query mode = adding a bridge, not editing a god-object. See [query-layer.md](query-layer.md).

### Protocol (why, vs ABC)
`StorageProtocol`, `SearchBridge`, `QualityFilter` are `typing.Protocol` — structural typing. Test doubles need not inherit; any object with the right methods satisfies the interface. Swap storage/query/quality without touching call sites.

### Graceful degradation (resilience ladder)
| Missing dependency | Fallback |
|---|---|
| `ray` | local execution |
| `nemo_curator` | basic dedup / CPU scoring |
| HugeGraph / Gremlin | vector RAG; `export_graph` → REST API (v1.6.3) |
| native Lance op | PyArrow fallback |
All fallbacks log a warning — no silent degradation.

## Configuration

```python
from arrow_lake.config import ArrowLakeConfig, StorageBackend
```

**4-layer precedence** (later overrides earlier):
1. Code defaults in each Pydantic section
2. `.env` file
3. Environment variables, prefix `ARROW_LAKE__` (double underscore = nesting): `ARROW_LAKE__STORAGE__BACKEND=s3`
4. YAML overlay (highest): `ArrowLakeConfig.from_yaml("configs/prod.yaml")`

**30+ sections**, root = `ArrowLakeConfig`. Notable: `storage`, `compute`, `olap`, `vector_search`, `full_text_search`, `hybrid_search`, `embedding`, `media`, `quality`, `workflow`, `argo`, `autoscale`, `lifecycle`, `lineage`, `audit`, `api`, `auth`, `rate_limit`, `llm`, `rag`, `huge_graph`, `gravitino`, `redis` (incl. `task_key_prefix`, `task_ttl_seconds` — v1.6.2), `document`, `export`.

`OlapConfig.validate_memory_budget()` warns on impossible memory settings; `EmbeddingConfig` carries the Qwen3-VL whitelist + `known_dimension`/`is_multimodal`/`validate_dimension`.

## Exception Hierarchy

```
ArrowLakeError
├── StorageError, QueryError, IngestError, CatalogError, RayRuntimeError
├── ValidationError, HttpError, EmbeddingError, QualityError
├── WorkflowError, AuditError, RAGError, KGError, DocumentError
├── DuckDBError, ArgoError, BackupError, SchemaEvolutionError
```
`ErrorCode` enum: 200+ typed codes. All raised from the facades above; catch `ArrowLakeError` at boundaries.

## Entry Points

| Surface | Location | Run |
|---|---|---|
| Python SDK | `arrow_lake/__init__.py` (`Lake`) | `from arrow_lake import Lake` |
| REST API | `arrow_lake/api/app.py` (FastAPI factory) | `uvicorn arrow_lake.api.app:create_app --factory` |
| CLI | `arrow_lake/cli/__init__.py` (Click) | `arrow-lake <group> …` |
| Async task status | `GET /api/v1/tasks/{task_id}/status` | v1.6.1+ |

## Subsystem Map (15 packages)

`config/` · `core/` · `api/` · `ingest/` · `query/` · `quality/` · `catalog/` · `knowledge_graph/` · `rag/` · `embed/` · `workflow/` · `cli/` · `storage/` · `ops/` · `ray_runtime/`.
