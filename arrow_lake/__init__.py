"""Arrow Lake — Unified multimodal data lakehouse."""

from __future__ import annotations

import threading
import warnings
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from arrow_lake._lake_admin import _LakeAdminMixin
from arrow_lake._lake_audit import _LakeAuditMixin
from arrow_lake._lake_base import _LakeBaseMixin
from arrow_lake._lake_ingest import _LakeIngestMixin
from arrow_lake._lake_kg import _LakeKGMixin
from arrow_lake._lake_lineage import _LakeLineageMixin
from arrow_lake._lake_query import _LakeQueryMixin
from arrow_lake._lake_rag import _LakeRAGMixin
from arrow_lake._lake_search import _LakeSearchMixin
from arrow_lake._protocols import StorageProtocol
from arrow_lake._models import CatalogEntry, CatalogResult
from arrow_lake._version import __version__
from arrow_lake.config import ArrowLakeConfig, StorageBackend
from arrow_lake.exceptions import (
    ArgoError,
    ArrowLakeError,
    AuditError,
    BackupError,
    CatalogError,
    DocumentError,
    DuckDBError,
    EmbeddingError,
    HttpError,
    IngestError,
    KGError,
    QualityError,
    QueryError,
    RAGError,
    RayRuntimeError,
    SchemaEvolutionError,
    StorageError,
    ValidationError,
    WorkflowError,
)

if TYPE_CHECKING:
    from arrow_lake._models import HealthInfo
    from arrow_lake.ops.backup import BackupInfo
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
    "ArgoError",
    "ArrowLakeConfig",
    "ArrowLakeError",
    "AuditError",
    "BackupError",
    "BackupInfo",
    "CatalogEntry",
    "CatalogError",
    "CatalogResult",
    "DocumentError",
    "DuckDBError",
    "EmbeddingError",
    "EnsembleSearchResult",
    "FacetedSearchResult",
    "FullTextSearchResult",
    "HealthInfo",
    "HttpError",
    "HybridSearchResult",
    "IndexInfo",
    "IngestError",
    "KGError",
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
    "SchemaEvolutionError",
    "StorageBackend",
    "StorageError",
    "ValidationError",
    "VectorSearchResult",
    "WorkflowError",
    "__version__",
]


class Lake(
    _LakeBaseMixin,
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
        import logging
        import time as _time

        self._base_uri = base_uri
        self._config = config or ArrowLakeConfig()
        self._storage: StorageProtocol | None = None
        self._components: dict[str, Any] = {}
        self._component_lock = threading.RLock()
        self._logger = logging.getLogger(__name__)
        self._start_time = _time.monotonic()
        self._shutdown = False

    def _ensure_uptime_metric(self) -> None:
        """Lazily set the uptime metric (skipped in pure-local mode)."""
        from arrow_lake.core.metrics import system_uptime_seconds

        system_uptime_seconds.set_to_current_time()

    def _get_component(self, key: str, factory: Callable[[], Any]) -> Any:
        """Lazy-init and cache a component instance (thread-safe)."""
        if key not in self._components:
            with self._component_lock:
                if key not in self._components:
                    self._components[key] = factory()
        return self._components[key]

    @property
    def config(self) -> ArrowLakeConfig:
        """Return the current Arrow Lake configuration."""
        return self._config

    def get_session_manager(self, skip_warmup: bool = False) -> Any:
        """Get the shared DuckDB session manager (lazy-init).

        Bridges use this to acquire managed connections instead of
        creating per-query sessions.

        Args:
            skip_warmup: When True, create the manager without running the
                blocking warmup inline (the caller runs warmup separately,
                e.g. in a background thread). Only affects first creation.
        """
        return self._get_component(
            "session_manager",
            lambda: self._create_session_manager(skip_warmup),
        )

    def _create_session_manager(self, skip_warmup: bool = False) -> Any:
        from arrow_lake.query.session_manager import DuckDBSessionManager

        olap = self._config.olap

        # Validate memory budget before creating the manager
        warning = olap.validate_memory_budget()
        if warning:
            self._logger.warning("memory_budget_warning: %s", warning)

        manager = DuckDBSessionManager.from_config(
            olap_config=olap,
            storage_config=self._config.storage,
            redis_config=self._config.redis,
        )

        # Automatic warmup for cold-start optimization. Skipped when the caller
        # defers warmup to a background thread (the pool lazy-creates sessions
        # on demand in the meantime).
        if olap.warmup_enabled and not skip_warmup:
            try:
                result = manager.warmup()
                if result.get("errors", 0) > 0:
                    self._logger.warning(
                        "duckdb_warmup_partial: warmed=%d, errors=%d",
                        result.get("warmed", 0),
                        result["errors"],
                    )
            except Exception:
                self._logger.warning("duckdb_warmup_failed", exc_info=True)

        return manager

    def shutdown(self) -> None:
        """Gracefully shut down all managed components and release resources."""
        import asyncio

        if self._shutdown:
            return
        self._shutdown = True

        async_tasks: list[Any] = []
        for key in list(self._components):
            component = self._components[key]
            try:
                if hasattr(component, "shutdown"):
                    component.shutdown()
                elif hasattr(component, "aclose") and asyncio.iscoroutinefunction(component.aclose):
                    # Async clients (e.g. httpx.AsyncClient) use aclose()
                    async_tasks.append((key, component.aclose))
                elif hasattr(component, "close"):
                    close_method = component.close
                    if asyncio.iscoroutinefunction(close_method):
                        async_tasks.append((key, close_method))
                    else:
                        close_method()
            except Exception:
                self._logger.warning("Failed to shut down component %s", key, exc_info=True)

        # Await all async cleanup tasks
        if async_tasks:
            async def _await_all():
                for k, fn in async_tasks:
                    try:
                        await fn()
                    except Exception:
                        self._logger.warning("Failed to async-shut down component %s", k, exc_info=True)
            try:
                loop = asyncio.get_running_loop()
                task = loop.create_task(_await_all())
                task.add_done_callback(lambda t: t.exception() if not t.cancelled() else None)
            except RuntimeError:
                asyncio.run(_await_all())

        self._components.clear()

    def __enter__(self) -> Lake:
        return self

    def __exit__(self, *exc: Any) -> None:
        self.shutdown()

    def __del__(self) -> None:
        if not self._shutdown and self._components:
            warnings.warn(
                "Lake instance not shut down — call lake.shutdown() or use 'with Lake(...) as lake:'",
                ResourceWarning,
                stacklevel=1,
            )

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

    def _get_shared_http_client(self) -> Any:
        """Get a shared synchronous httpx.Client (lazy-init, auto-closed on shutdown).

        Components should use this instead of creating their own clients,
        reducing connection overhead and enabling proxy config consistency.
        """
        return self._get_component(
            "shared_http_client",
            lambda: self._create_shared_http_client(),
        )

    def _get_shared_async_http_client(self) -> Any:
        """Get a shared httpx.AsyncClient (lazy-init, auto-closed on shutdown)."""
        return self._get_component(
            "shared_async_http_client",
            lambda: self._create_shared_async_http_client(),
        )

    def _create_shared_http_client(self) -> Any:
        from arrow_lake.core.http import create_http_client

        return create_http_client(
            timeout=30.0,
            limits=dict(max_connections=20, max_keepalive_connections=10),
        )

    def _create_shared_async_http_client(self) -> Any:
        from arrow_lake.core.http import create_async_http_client

        return create_async_http_client(
            timeout=30.0,
            limits=dict(max_connections=20, max_keepalive_connections=10),
        )

    def _get_storage(self) -> StorageProtocol:
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
