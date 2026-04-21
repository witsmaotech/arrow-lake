"""Shared data types for Arrow Lake public API."""

from __future__ import annotations

from dataclasses import dataclass


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
