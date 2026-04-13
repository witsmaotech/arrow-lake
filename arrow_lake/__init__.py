"""Arrow Lake — Unified multimodal data lakehouse."""

from arrow_lake._version import __version__
from arrow_lake.config import ArrowLakeConfig
from arrow_lake.exceptions import (
    ArrowLakeError,
    CatalogError,
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
    Full implementation in Story 1.4 — this is the scaffold.
    """

    def __init__(self) -> None:
        pass

    def ingest(self) -> None:
        """Ingest data into the lakehouse."""

    def search(self) -> None:
        """Search across ingested data."""

    def catalog(self) -> None:
        """Access the catalog for dataset metadata."""

    def version(self) -> str:
        """Return the current platform version."""
        return __version__
