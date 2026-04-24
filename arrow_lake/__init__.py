"""Arrow Lake — Unified multimodal data lakehouse."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from arrow_lake._lake_admin import _LakeAdminMixin
from arrow_lake._lake_audit import _LakeAuditMixin
from arrow_lake._lake_ingest import _LakeIngestMixin
from arrow_lake._lake_kg import _LakeKGMixin
from arrow_lake._lake_lineage import _LakeLineageMixin
from arrow_lake._lake_query import _LakeQueryMixin
from arrow_lake._lake_rag import _LakeRAGMixin
from arrow_lake._lake_search import _LakeSearchMixin
from arrow_lake._models import CatalogEntry, CatalogResult
from arrow_lake._version import __version__
from arrow_lake.config import ArrowLakeConfig, StorageBackend
from arrow_lake.exceptions import (
    ArrowLakeError,
    AuditError,
    CatalogError,
    DocumentError,
    EmbeddingError,
    HttpError,
    IngestError,
    QualityError,
    QueryError,
    RAGError,
    RayRuntimeError,
    StorageError,
    ValidationError,
    WorkflowError,
)

if TYPE_CHECKING:
    from arrow_lake.quality.base import QualityFilterRegistry
    from arrow_lake.quality.models import QualityReport
    from arrow_lake.query.ensemble import EnsembleSearchResult
    from arrow_lake.query.faceted import FacetedSearchResult
    from arrow_lake.query.fts import FullTextSearchResult
    from arrow_lake.query.hybrid import HybridSearchResult
    from arrow_lake.query.olap import OlapQueryResult
    from arrow_lake.query.vector import IndexInfo, VectorSearchResult
    from arrow_lake.rag.pipeline import RAGPipeline, RAGResponse

__all__ = [
    "ArrowLakeConfig",
    "ArrowLakeError",
    "AuditError",
    "CatalogEntry",
    "CatalogError",
    "CatalogResult",
    "DocumentError",
    "EmbeddingError",
    "EnsembleSearchResult",
    "FacetedSearchResult",
    "FullTextSearchResult",
    "HttpError",
    "HybridSearchResult",
    "IndexInfo",
    "IngestError",
    "Lake",
    "OlapQueryResult",
    "QualityError",
    "QualityFilterRegistry",
    "QualityReport",
    "QueryError",
    "RAGError",
    "RAGPipeline",
    "RAGResponse",
    "RayRuntimeError",
    "StorageError",
    "ValidationError",
    "VectorSearchBridge",
    "VectorSearchResult",
    "WorkflowError",
    "__version__",
]


class Lake(
    _LakeIngestMixin,
    _LakeSearchMixin,
    _LakeQueryMixin,
    _LakeAdminMixin,
    _LakeLineageMixin,
    _LakeAuditMixin,
    _LakeRAGMixin,
    _LakeKGMixin,
):
    """Arrow Lake SDK entry point.

    Provides high-level access to ingestion, search, catalog, and versioning.

    Args:
        base_uri: Base URI for Lance dataset storage.
        config: Full Arrow Lake configuration (None = use defaults + .env + env).
    """

    def __init__(
        self,
        base_uri: str = "./data",
        config: ArrowLakeConfig | None = None,
    ) -> None:
        self._base_uri = base_uri
        self._config = config or ArrowLakeConfig()
        self._storage: Any = None
        self._components: dict[str, Any] = {}

        import time as _time

        from arrow_lake.core.metrics import system_uptime_seconds

        system_uptime_seconds.set_to_current_time()
        self._start_time = _time.monotonic()

    def _get_component(self, key: str, factory: Callable[[], Any]) -> Any:
        """Lazy-init and cache a component instance."""
        if key not in self._components:
            self._components[key] = factory()
        return self._components[key]

    def get_session_manager(self) -> Any:
        """Get the shared DuckDB session manager (lazy-init).

        Bridges use this to acquire managed connections instead of
        creating per-query sessions.
        """
        return self._get_component(
            "session_manager",
            lambda: self._create_session_manager(),
        )

    def _create_session_manager(self) -> Any:
        from arrow_lake.query.session_manager import DuckDBSessionManager

        return DuckDBSessionManager(
            olap_config=self._config.olap,
            storage_config=self._config.storage,
        )

    def shutdown(self) -> None:
        """Gracefully shut down the session manager and release resources."""
        sm = self._components.get("session_manager")
        if sm is not None:
            sm.shutdown()

    @classmethod
    def from_yaml(cls, path: str, *, base_uri: str | None = None) -> Lake:
        """Create a Lake instance from a YAML config file.

        Args:
            path: Path to YAML config file.
            base_uri: Override base URI (None = use "./data").

        Returns:
            Lake instance with config loaded from YAML.
        """
        config = ArrowLakeConfig.from_yaml(path)
        return cls(base_uri=base_uri or "./data", config=config)

    def _get_storage(self) -> Any:
        """Lazy-init and cache the storage manager."""
        if self._storage is None:
            from arrow_lake.ingest.storage import LanceStorageManager

            # Only pass storage_config when S3 backend has real credentials
            # (not placeholder values like <CHANGE_ME>)
            sc = self._config.storage
            has_real_creds = (
                sc.backend != StorageBackend.LOCAL
                and sc.s3_access_key
                and not sc.s3_access_key.startswith("<")
            )
            self._storage = LanceStorageManager(
                self._base_uri,
                storage_config=sc if has_real_creds else None,
            )
        return self._storage
