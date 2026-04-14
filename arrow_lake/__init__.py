"""Arrow Lake — Unified multimodal data lakehouse."""

from __future__ import annotations

from typing import Any

from arrow_lake._version import __version__
from arrow_lake.config import ArrowLakeConfig
from arrow_lake.exceptions import (
    ArrowLakeError,
    CatalogError,
    EmbeddingError,
    HttpError,
    IngestError,
    QueryError,
    RayRuntimeError,
    StorageError,
    ValidationError,
)

__all__ = [
    "ArrowLakeConfig",
    "ArrowLakeError",
    "CatalogError",
    "EmbeddingError",
    "HttpError",
    "IngestError",
    "Lake",
    "QueryError",
    "RayRuntimeError",
    "StorageError",
    "ValidationError",
    "__version__",
]


class Lake:
    """Arrow Lake SDK entry point.

    Provides high-level access to ingestion, search, catalog, and versioning.
    """

    def __init__(self) -> None:
        pass

    def ingest(self) -> None:
        """Ingest data into the lakehouse."""

    def search(self) -> None:
        """Search across ingested data."""

    def catalog(self) -> None:
        """Access the catalog for dataset metadata."""

    def query(
        self,
        dataset_name: str,
        sql: str,
        *,
        base_uri: str = "./data",
    ) -> Any:
        """Query dataset metadata via SQL.

        Delegates to MetadataSearchBridge (Story 3.9).

        Args:
            dataset_name: Name of the Lance dataset.
            sql: SQL query (SELECT only).
            base_uri: Base URI for Lance storage.

        Returns:
            MetadataQueryResult with Arrow table.
        """
        from arrow_lake.ingest.storage import LanceStorageManager
        from arrow_lake.query.metadata import MetadataSearchBridge

        storage = LanceStorageManager(base_uri)
        bridge = MetadataSearchBridge(storage)
        return bridge.query(dataset_name, sql)

    def version(self) -> str:
        """Return the current platform version."""
        return __version__
