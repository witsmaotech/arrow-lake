"""Shared data types for Arrow Lake public API."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class CatalogEntry:
    """Metadata for a single dataset in the catalog.

    Attributes:
        name: Dataset name.
        version: Current Lance dataset version.
        num_rows: Number of rows in the dataset.
        num_columns: Number of top-level columns in the schema.
        vector_dim: Dimensionality of the embedding column (None if no vector col).
        has_vector_index: Whether an ANN vector index exists.
        has_fts_index: Whether a BM25 / FTS index exists.
        size_bytes: On-disk size in bytes (None if not cheaply available).
    """

    name: str
    version: int
    num_rows: int
    # Extended metadata (optional; populated by the storage layer when cheap to
    # compute — see _LakeAdmin.catalog). Defaults keep backward compatibility
    # for callers constructing only the 3 base fields.
    num_columns: int = 0
    vector_dim: int | None = None
    has_vector_index: bool = False
    has_fts_index: bool = False
    size_bytes: int | None = None
    created_at: str | None = None
    updated_at: str | None = None
    # W4.2: dataset lineage for the console's structured/document split —
    # "container" (multi-table, DR14) | "document" (ingest-pipeline
    # columns heuristic) | "structured" (default).
    kind: str = "structured"


@dataclass(frozen=True)
class CatalogResult:
    """Result of a catalog listing operation.

    Attributes:
        datasets: List of dataset metadata entries.
        total: Total number of datasets.
    """

    datasets: list[CatalogEntry]
    total: int


@dataclass(frozen=True)
class HealthInfo:
    """Health status snapshot of the Arrow Lake SDK instance.

    Attributes:
        status: Overall health ("ok" or "degraded").
        version: SDK version string.
        storage_status: Storage accessibility description.
        storage_ok: Whether storage is accessible.
        uptime_seconds: Seconds since Lake was initialized.
        session_pool: DuckDB session pool stats (None if not initialized).
    """

    status: str
    version: str
    storage_status: str
    storage_ok: bool
    uptime_seconds: float
    session_pool: dict[str, Any] | None = None
