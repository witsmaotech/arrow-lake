# Ingestion & Data Quality

> Back-reference: [../SKILL.md](../SKILL.md) · parent: [architecture.md](architecture.md). Verified v1.7.0.

`_LakeIngestMixin` (all **sync**) ingests many sources into Lance, chunks documents, embeds, and runs a 3-stage quality gate. `create_dataset(name, pa.Table)` is the primary programmatic write entry; the `ingest_*` family handles external sources.

## Multi-source ingestion

| Source | Method | Notes |
|---|---|---|
| Local files | `ingest(ds, file_paths, *, transforms=None)` → `IngestionReport` | Daft transforms optional |
| Batch | `ingest_batch(...)` | |
| SQL DB | `ingest_sql(...)` | |
| Kafka | `ingest_kafka(...)` | streaming source |
| Iceberg | `ingest_iceberg(...)` | table format |
| Delta Lake | `ingest_deltalake(...)` | table format |
| HTTP | `ingest_http(...)` | remote fetch |
| Images / Video / Mixed | `ingest_images` / `ingest_videos` / `ingest_mixed` | multimodal |
| Documents (PDF/HTML) | `ingest_documents(...)` | Kreuzberg parse → chunk → embed |
| Embed inline | `ingest_and_embed(...)` | ingest + embedding in one call |

Row-level writes: `append_dataset`, `upsert(ds, data, *, on="id")`, `update_rows(ds, where, values)`, `delete_rows`. Export: `export_to(...)`.

**Dataset name rule:** must match `^[a-zA-Z_][a-zA-Z0-9_-]*$`. Re-embed on `upsert`/`update_rows` of text columns to avoid embedding drift.

## Document chunking — 5 strategies (verified)

`ingest/chunker.py` — `chunk(pages: list[tuple[int, str]]) -> list[Chunk]`:

| Strategy | Deps | Behavior |
|---|---|---|
| `page` | built-in | one chunk per page |
| `paragraph` | built-in | split on double-newlines (`_split_by_paragraph`) |
| `recursive` | built-in, **zero deps** | sentence-aware recursive split (`_split_recursive`) — splits on sentences first, then words |
| `semchunk` | optional (`semchunk`) | multi-level hierarchical splitting (`_chunk_with_semchunk`) |
| `chonkie_token` | optional (`chonkie`) | token-based splitting (`_chunk_with_chonkie`) |

> Note: an older memory listed strategies like `fixed_size` / `semantic` / `sentence` / `token` and claimed `_chunk_with_semchunk` was deleted. **Both are outdated** — the real set is the 5 above, and `_chunk_with_semchunk` exists and works in v1.6.3.

## Data quality — 3-stage gate

`quality/` runs schema validation → quality filters → scoring before rows land in Lance.

### Filter protocol & registry
```python
from arrow_lake.quality.base import QualityFilter, QualityFilterRegistry
# QualityFilter is a typing.Protocol; implement apply() and register it.
```

### Built-in & optional filters
| Filter | File | Purpose |
|---|---|---|
| `TextLengthFilter` | `builtin.py` | drop too-short/too-long text |
| `ImageResolutionFilter` | `builtin.py` | drop low-res images |
| `NeMoCuratorFilter` | `nemo_curator.py` | NeMo quality scoring (optional dep → graceful CPU fallback) |
| Exact-hash dedup | `dedup.py` | exact duplicate removal |
| Perceptual-hash dedup | `dedup.py` | near-duplicate removal (configurable `perceptual_threshold`) |
| Schema validation | `schema_validation.py` | type + null enforcement |
| Gravitino policies/tags | `gravitino_policies.py`, `gravitino_tags.py` | tag-driven ACL / retention |
| Masking | `masking_engine.py` | column masking |
| Retention | `retention_enforcer.py` | lifecycle enforcement |

Supporting modules: `gate.py` (orchestrates the 3 stages), `scoring.py` (row scores), `profiler.py`, `rules.py`, `dead_letter.py` (rejected rows), `models.py` (`QualityReport`, `FilterResult`).

## Calling quality from the facade

```python
# Run registered filters on an existing dataset → QualityReport
report = lake.quality_filter("docs", active_filters="dedup,null_check", mode="all")

# Dedup explicitly → DedupResult
res = lake.deduplicate("docs", strategy="exact")           # or perceptual
# res exposes counts; perceptual_threshold tunes near-dup sensitivity
```

`mode`: `"all"` (every filter must pass) vs first-match behavior. `active_filters` is a comma-separated list of registered filter names.

## Document pipeline (PDF → Lance → RAG)

```
PDF → BlobStore (raw) → DocumentParser (Kreuzberg; marker-pdf/TurboOCR/pypdf cascade)
    → DocumentChunker (5 strategies) → EmbeddingEngine (Qwen3-VL / CLIP)
    → LanceStorageManager (text + embedding + blob_key + page_number) → RAG
```
- **marker-pdf** is invoked via `subprocess` only (GPL-3.0 license isolation — keeps Arrow Lake MIT).
- **TurboOCR** is the scanned-PDF fallback (isolated Docker network, `expose` not `ports`).
- **pypdf** is the last-resort text-only path.
- Embedding standard: **Qwen3-VL-Embedding** (2B default, 2048-dim, multimodal; 8B optional, 4096-dim).

## Common Mistakes

- **Citing the wrong chunker set**: it's 5 strategies (`page`/`paragraph`/`recursive`/`semchunk`/`chonkie_token`), not the older 7-name list.
- **Skipping quality_filter on ingest**: raw data with dups/nulls degrades every downstream query; register filters before bulk ingest.
- **Forgetting to re-embed on update**: `update_rows` on a text column without re-embedding = silent embedding drift.
- **Expecting `quality_filter` to mutate**: it returns a `QualityReport`; pair with `deduplicate`/dead-letter handling to act.
- **Hard dependency on NeMo**: it's optional — code must run the CPU fallback when absent.
