"""Auto lineage capture hooks — fire-and-forget lineage recording for core pipelines.

Each hook wraps ``create_lineage_event`` + ``LineageStore.record_event`` in a
try/except so that lineage failure **never** blocks the main pipeline.

Usage (inside pipeline code)::

    from arrow_lake.catalog.lineage_hooks import auto_record_ingest

    # after successful ingest:
    auto_record_ingest(storage, dataset_name, source_files=["/data/file1.csv"])
"""

from __future__ import annotations

from typing import Any

import structlog

logger = structlog.get_logger(__name__)

_store_cache: dict[int, Any] = {}


def _get_store(storage: Any) -> Any:
    """Lazy-create a cached LineageStore from the storage manager."""
    from arrow_lake.catalog.lineage import LineageStore

    cache_key = id(storage)
    if cache_key not in _store_cache:
        _store_cache[cache_key] = LineageStore(storage)
    return _store_cache[cache_key]


def _fire_and_forget(func: Any, *args: Any, **kwargs: Any) -> None:
    """Execute *func* inside a broad try/except so lineage never breaks callers."""
    try:
        func(*args, **kwargs)
    except Exception:
        logger.warning("lineage_hook_failed", exc_info=True)


# ---------------------------------------------------------------------------
# Ingest hook
# ---------------------------------------------------------------------------

def auto_record_ingest(
    storage: Any,
    dataset_name: str,
    *,
    source_files: list[str] | None = None,
    lance_version: int | None = None,
    actor: str = "system:ingest-pipeline",
) -> None:
    """Record a lineage event after successful dataset ingestion.

    Args:
        storage: LanceStorageManager instance.
        dataset_name: Target dataset.
        source_files: Source file paths (converted to source dataset names).
        lance_version: Lance version after ingest.
        actor: Who triggered the ingest.
    """
    from arrow_lake.catalog.lineage import create_lineage_event

    def _record() -> None:
        store = _get_store(storage)
        source_datasets = _extract_source_datasets(source_files)
        event = create_lineage_event(
            dataset_name,
            "append",
            source_datasets=source_datasets,
            transform_type="ingest",
            lance_version=lance_version,
            actor=actor,
            metadata={"source_files": source_files or []},
        )
        store.record_event(event)

    _fire_and_forget(_record)


# ---------------------------------------------------------------------------
# OLAP query hook
# ---------------------------------------------------------------------------

def auto_record_query(
    storage: Any,
    dataset_name: str,
    sql: str,
    result_rows: int = 0,
    actor: str = "system:olap-query",
) -> None:
    """Record a lineage event after successful OLAP query."""
    from arrow_lake.catalog.lineage import create_lineage_event

    def _record() -> None:
        store = _get_store(storage)
        event = create_lineage_event(
            dataset_name,
            "query",
            source_datasets=[dataset_name],
            transform_type="olap-query",
            actor=actor,
            metadata={"sql_preview": sql[:200], "result_rows": result_rows},
        )
        store.record_event(event)

    _fire_and_forget(_record)


# ---------------------------------------------------------------------------
# RAG retrieval hook
# ---------------------------------------------------------------------------

def auto_record_rag(
    storage: Any,
    dataset_name: str,
    question: str,
    retrieved_chunks: list[dict[str, Any]] | None = None,
    actor: str = "system:rag-pipeline",
) -> None:
    """Record a lineage event after successful RAG retrieval."""
    from arrow_lake.catalog.lineage import create_lineage_event

    def _record() -> None:
        store = _get_store(storage)
        source_datasets = list({
            chunk.get("dataset", chunk.get("dataset_name", ""))
            for chunk in (retrieved_chunks or [])
            if chunk.get("dataset") or chunk.get("dataset_name")
        })
        event = create_lineage_event(
            dataset_name,
            "rag-query",
            source_datasets=source_datasets or [dataset_name],
            transform_type="rag-retrieval",
            actor=actor,
            metadata={"question_preview": question[:100]},
        )
        store.record_event(event)

    _fire_and_forget(_record)


# ---------------------------------------------------------------------------
# Export hook
# ---------------------------------------------------------------------------

def auto_record_export(
    storage: Any,
    dataset_name: str,
    output_path: str,
    fmt: str = "parquet",
    actor: str = "system:export-pipeline",
) -> None:
    """Record a lineage event after successful data export."""
    from arrow_lake.catalog.lineage import create_lineage_event

    def _record() -> None:
        store = _get_store(storage)
        event = create_lineage_event(
            dataset_name,
            "export",
            source_datasets=[dataset_name],
            transform_type="file-export",
            actor=actor,
            metadata={"output_path": output_path, "format": fmt},
        )
        store.record_event(event)

    _fire_and_forget(_record)


# ---------------------------------------------------------------------------
# Federated query hook
# ---------------------------------------------------------------------------

def auto_record_federated(
    storage: Any,
    catalog_tables: list[tuple[str, str]],
    join_sql: str,
    result_rows: int = 0,
    actor: str = "system:federated-engine",
) -> None:
    """Record a lineage event after successful cross-catalog query."""
    from arrow_lake.catalog.lineage import create_lineage_event

    def _record() -> None:
        store = _get_store(storage)
        source_datasets = [fqn for fqn, _alias in catalog_tables]
        event = create_lineage_event(
            "_federated_result",
            "federated-join",
            source_datasets=source_datasets,
            transform_type="cross-catalog-sql",
            actor=actor,
            metadata={"join_sql_preview": join_sql[:200], "result_rows": result_rows},
        )
        store.record_event(event)

    _fire_and_forget(_record)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _extract_source_datasets(source_files: list[str] | None) -> list[str]:
    """Best-effort convert file paths to dataset-like identifiers."""
    if not source_files:
        return []

    results: list[str] = []
    for path in source_files:
        # Strip common prefixes and extensions to get a dataset-like name
        name = path.rsplit("/", 1)[-1] if "/" in path else path
        for ext in (".csv", ".parquet", ".json", ".jsonl", ".ndjson", ".arrow"):
            if name.endswith(ext):
                name = name[: -len(ext)]
                break
        if name:
            results.append(f"file:{name}")
    return results
