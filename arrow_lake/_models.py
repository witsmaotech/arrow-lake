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
    """

    name: str
    version: int
    num_rows: int


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
