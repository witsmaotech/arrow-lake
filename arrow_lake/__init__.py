"""Arrow Lake — Unified multimodal data lakehouse."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from arrow_lake._version import __version__
from arrow_lake.config import ArrowLakeConfig
from arrow_lake.exceptions import (
    ArrowLakeError,
    AuditError,
    CatalogError,
    EmbeddingError,
    HttpError,
    IngestError,
    QualityError,
    QueryError,
    RayRuntimeError,
    StorageError,
    ValidationError,
    WorkflowError,
)

if TYPE_CHECKING:
    from arrow_lake.ingest.ingestor import IngestionReport
    from arrow_lake.quality.base import QualityFilterRegistry
    from arrow_lake.quality.models import QualityReport
    from arrow_lake.query.daft_api import LazyDaftFrame
    from arrow_lake.query.ensemble import EnsembleSearchResult
    from arrow_lake.query.faceted import FacetedSearchResult
    from arrow_lake.query.fts import FullTextSearchResult
    from arrow_lake.query.hybrid import HybridSearchResult
    from arrow_lake.query.olap import OlapQueryResult
    from arrow_lake.query.vector import IndexInfo, VectorSearchBridge, VectorSearchResult

__all__ = [
    "ArrowLakeConfig",
    "ArrowLakeError",
    "AuditError",
    "CatalogEntry",
    "CatalogError",
    "CatalogResult",
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
    "RayRuntimeError",
    "StorageError",
    "ValidationError",
    "VectorSearchBridge",
    "VectorSearchResult",
    "WorkflowError",
    "__version__",
]


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


class Lake:
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

    def _get_component(self, key: str, factory: Callable[[], Any]) -> Any:
        """Lazy-init and cache a component instance."""
        if key not in self._components:
            self._components[key] = factory()
        return self._components[key]

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

            self._storage = LanceStorageManager(self._base_uri)
        return self._storage

    def ingest(
        self,
        dataset_name: str,
        file_paths: list[str],
    ) -> IngestionReport:
        """Ingest local files into a Lance dataset (Stories 3.1-3.5).

        Delegates to Ingestor.

        Args:
            dataset_name: Target dataset name.
            file_paths: List of file paths (CSV, JSON, JSONL, Parquet).

        Returns:
            IngestionReport with per-source and total stats.
        """
        from arrow_lake.ingest.ingestor import Ingestor

        return Ingestor(self._get_storage()).ingest(dataset_name, file_paths)

    def ingest_http(
        self,
        dataset_name: str,
        urls: list[str],
    ) -> IngestionReport:
        """Ingest files from HTTP(S) URLs (Story 3.2).

        Delegates to Ingestor.

        Args:
            dataset_name: Target dataset name.
            urls: List of HTTP(S) URLs.

        Returns:
            IngestionReport with per-source and total stats.
        """
        from arrow_lake.ingest.ingestor import Ingestor

        return Ingestor(self._get_storage()).ingest_http(dataset_name, urls)

    def ingest_images(
        self,
        dataset_name: str,
        image_paths: list[str],
    ) -> IngestionReport:
        """Ingest image files with thumbnails and EXIF (Story 3.3).

        Delegates to Ingestor.

        Args:
            dataset_name: Target dataset name.
            image_paths: List of image file paths.

        Returns:
            IngestionReport with per-source and total stats.
        """
        from arrow_lake.ingest.ingestor import Ingestor

        return Ingestor(self._get_storage()).ingest_images(dataset_name, image_paths)

    def ingest_videos(
        self,
        dataset_name: str,
        video_paths: list[str],
    ) -> IngestionReport:
        """Ingest video files with keyframe extraction (Story 3.4).

        Delegates to Ingestor.

        Args:
            dataset_name: Target dataset name.
            video_paths: List of video file paths.

        Returns:
            IngestionReport with per-source and total stats.
        """
        from arrow_lake.ingest.ingestor import Ingestor

        return Ingestor(self._get_storage()).ingest_videos(dataset_name, video_paths)

    def ingest_mixed(
        self,
        dataset_name: str,
        sources: dict[str, list[str]],
    ) -> IngestionReport:
        """Ingest mixed modality sources into a unified table (Story 3.5).

        Delegates to Ingestor.

        Args:
            dataset_name: Target dataset name.
            sources: Dict mapping modality to list of paths/URLs.
                     Supported keys: "files", "urls", "images", "videos".

        Returns:
            Combined IngestionReport.
        """
        from arrow_lake.ingest.ingestor import Ingestor

        return Ingestor(self._get_storage()).ingest_mixed(dataset_name, sources)

    def catalog(self) -> CatalogResult:
        """List all datasets with metadata (Story 7.1).

        Returns:
            CatalogResult with dataset entries (name, version, row count).
        """
        storage = self._get_storage()
        names = storage.list_datasets()
        entries: list[CatalogEntry] = []
        for name in names:
            try:
                ds = storage.open_dataset(name)
                num_rows = ds.count_rows()
            except Exception:
                num_rows = 0
            try:
                version = storage.get_version(name)
            except Exception:
                version = 0
            entries.append(CatalogEntry(name=name, version=version, num_rows=num_rows))
        return CatalogResult(datasets=entries, total=len(entries))

    def list_datasets(self) -> list[str]:
        """List all dataset names.

        Returns:
            Sorted list of dataset name strings.
        """
        return self._get_storage().list_datasets()

    def delete_dataset(self, name: str) -> None:
        """Delete a dataset and all its data.

        Args:
            name: Dataset name to delete.
        """
        self._get_storage().delete_dataset(name)

    def query(
        self,
        dataset_name: str,
        sql: str,
    ) -> Any:
        """Query dataset metadata via SQL.

        Delegates to MetadataSearchBridge (Story 3.9).

        Args:
            dataset_name: Name of the Lance dataset.
            sql: SQL query (SELECT only).

        Returns:
            MetadataQueryResult with Arrow table.
        """
        from arrow_lake.query.metadata import MetadataSearchBridge

        bridge = self._get_component(
            "metadata",
            lambda: MetadataSearchBridge(self._get_storage()),
        )
        return bridge.query(dataset_name, sql)

    def search(
        self,
        dataset_name: str,
        query_vector: list[float],
        *,
        top_k: int = 10,
        metric: str | None = None,
        vector_column: str = "text_embedding",
        where: str | None = None,
        nprobes: int | None = None,
    ) -> VectorSearchResult:
        """Vector similarity search across ingested data (Story 5.1).

        Delegates to VectorSearchBridge.

        Args:
            dataset_name: Name of the Lance dataset.
            query_vector: Query embedding vector.
            top_k: Number of results to return.
            metric: Distance metric ('cosine', 'l2', 'dot').
            vector_column: Name of the vector column.
            where: Optional metadata filter expression.
            nprobes: Number of IVF partitions to probe.

        Returns:
            VectorSearchResult with Arrow table and distance scores.
        """
        from arrow_lake.query.vector import VectorSearchBridge

        bridge = self._get_component(
            "vector",
            lambda: VectorSearchBridge(self._get_storage(), config=self._config.vector),
        )
        return bridge.search(
            dataset_name,
            query_vector,
            top_k=top_k,
            metric=metric,
            vector_column=vector_column,
            where=where,
            nprobes=nprobes,
        )

    def create_vector_index(
        self,
        dataset_name: str,
        *,
        metric: str = "",
        vector_column: str = "text_embedding",
        index_type: str = "",
        num_partitions: int | None = None,
        num_sub_vectors: int | None = None,
        replace: bool = True,
    ) -> IndexInfo:
        """Create a vector index on a dataset (Story 5.1).

        Delegates to VectorSearchBridge.

        Args:
            dataset_name: Name of the Lance dataset.
            metric: Distance metric (None = use config).
            vector_column: Name of the vector column.
            index_type: LanceDB index type (None = use config).
            num_partitions: IVF partitions (None = auto).
            num_sub_vectors: PQ sub-vectors (0 = use config).
            replace: Whether to replace existing index.

        Returns:
            IndexInfo with index metadata.
        """
        from arrow_lake.query.vector import VectorSearchBridge

        bridge = self._get_component(
            "vector",
            lambda: VectorSearchBridge(self._get_storage(), config=self._config.vector),
        )
        return bridge.create_index(
            dataset_name,
            metric=metric,
            vector_column=vector_column,
            index_type=index_type,
            num_partitions=num_partitions,
            num_sub_vectors=num_sub_vectors,
            replace=replace,
        )

    def text_search(
        self,
        dataset_name: str,
        query: str,
        *,
        top_k: int | None = None,
        fts_column: str | None = None,
        where: str | None = None,
    ) -> FullTextSearchResult:
        """Full-text search over ingested data (Story 5.2).

        Delegates to FullTextSearchBridge.

        Args:
            dataset_name: Name of the Lance dataset.
            query: Search query string.
            top_k: Number of results (None = use config default).
            fts_column: Text column to search (None = use config default).
            where: Optional metadata filter expression.

        Returns:
            FullTextSearchResult with Arrow table and _score relevance.
        """
        from arrow_lake.query.fts import FullTextSearchBridge

        bridge = self._get_component(
            "fts",
            lambda: FullTextSearchBridge(self._get_storage(), config=self._config.fts),
        )
        return bridge.search(
            dataset_name,
            query,
            top_k=top_k,
            fts_column=fts_column,
            where=where,
        )

    def create_fts_index(
        self,
        dataset_name: str,
        *,
        fts_column: str | None = None,
        replace: bool = True,
    ) -> None:
        """Create a full-text search index on a dataset (Story 5.2).

        Delegates to FullTextSearchBridge.

        Args:
            dataset_name: Name of the Lance dataset.
            fts_column: Text column to index (None = use config default).
            replace: Whether to replace existing index.
        """
        from arrow_lake.query.fts import FullTextSearchBridge

        bridge = self._get_component(
            "fts",
            lambda: FullTextSearchBridge(self._get_storage(), config=self._config.fts),
        )
        bridge.create_index(dataset_name, fts_column=fts_column, replace=replace)

    def hybrid_search(
        self,
        dataset_name: str,
        query_vector: list[float],
        query_text: str,
        *,
        top_k: int | None = None,
        vector_column: str = "text_embedding",
        fts_column: str | None = None,
        where: str | None = None,
    ) -> HybridSearchResult:
        """Hybrid search combining vector similarity + full-text via RRF (Story 5.3).

        Delegates to HybridSearchBridge.

        Args:
            dataset_name: Name of the Lance dataset.
            query_vector: Query embedding vector.
            query_text: Search query string for FTS.
            top_k: Number of results (None = use config default).
            vector_column: Name of the vector column.
            fts_column: Text column to search (None = use config default).
            where: Optional metadata filter expression.

        Returns:
            HybridSearchResult with Arrow table and _rrf_score.
        """
        from arrow_lake.query.hybrid import HybridSearchBridge

        bridge = self._get_component(
            "hybrid",
            lambda: HybridSearchBridge(self._get_storage(), config=self._config.hybrid),
        )
        return bridge.search(
            dataset_name,
            query_vector,
            query_text,
            top_k=top_k,
            vector_column=vector_column,
            fts_column=fts_column,
            where=where,
        )

    def olap_query(
        self,
        dataset_name: str,
        sql: str,
        *,
        max_rows: int | None = None,
        tables: dict[str, Any] | None = None,
    ) -> OlapQueryResult:
        """OLAP analytics query via DuckDB SQL (Story 5.4, 7.6).

        Supports GROUP BY, aggregation, window functions, HAVING, ORDER BY,
        LIMIT, and JOIN queries.

        Args:
            dataset_name: Name of the Lance dataset.
            sql: SQL query string (must be SELECT only).
            max_rows: Maximum result rows (None = use config default).
            tables: Additional Arrow tables for JOIN queries.

        Returns:
            OlapQueryResult with Arrow table and metadata.
        """
        from arrow_lake.query.olap import OlapSearchBridge

        bridge = self._get_component(
            "olap",
            lambda: OlapSearchBridge(self._get_storage(), config=self._config.olap),
        )
        return bridge.query(dataset_name, sql, max_rows=max_rows, tables=tables)

    def sql_query(
        self,
        dataset_name: str,
        sql: str,
        *,
        max_rows: int | None = None,
        tables: dict[str, Any] | None = None,
    ) -> OlapQueryResult:
        """SQL query — semantic alias for olap_query() (Story 7.6).

        Args:
            dataset_name: Name of the Lance dataset.
            sql: SQL query string (must be SELECT only).
            max_rows: Maximum result rows (None = use config default).
            tables: Additional Arrow tables for JOIN queries.

        Returns:
            OlapQueryResult with Arrow table and metadata.
        """
        return self.olap_query(dataset_name, sql, max_rows=max_rows, tables=tables)

    def faceted_search(
        self,
        dataset_name: str,
        query_vector: list[float],
        *,
        facets: list[str] | None = None,
        top_k: int = 10,
        vector_column: str = "embedding",
        where: str | None = None,
    ) -> FacetedSearchResult:
        """Faceted search combining facet counts with vector results (Story 8.1).

        Delegates to FacetedSearchBridge.

        Args:
            dataset_name: Name of the Lance dataset.
            query_vector: Query embedding vector.
            facets: Column names for CUBE facet computation.
            top_k: Number of results.
            vector_column: Name of the vector column.
            where: Optional metadata filter.

        Returns:
            FacetedSearchResult with search results and facet counts.
        """
        from arrow_lake.query.faceted import FacetedSearchBridge

        bridge = self._get_component(
            "faceted",
            lambda: FacetedSearchBridge(self._get_storage(), config=self._config.faceted),
        )
        return bridge.search(
            dataset_name,
            query_vector,
            facets=facets,
            top_k=top_k,
            vector_column=vector_column,
            where=where,
        )

    def ensemble_search(
        self,
        dataset_name: str,
        query_vector: list[float],
        *,
        columns: list[str] | None = None,
        weights: dict[str, float] | None = None,
        top_k: int | None = None,
        where: str | None = None,
    ) -> EnsembleSearchResult:
        """Multi-model ensemble search via weighted RRF fusion (Story 8.2).

        Searches multiple embedding columns and fuses results using
        weighted Reciprocal Rank Fusion.

        Args:
            dataset_name: Name of the Lance dataset.
            query_vector: Query embedding vector.
            columns: Embedding columns to search (None = auto-detect).
            weights: Per-column weights for RRF (None = all 1.0).
            top_k: Number of results.
            where: Optional metadata filter.

        Returns:
            EnsembleSearchResult with fused search results.
        """
        from arrow_lake.query.ensemble import EnsembleSearchBridge

        bridge = self._get_component(
            "ensemble",
            lambda: EnsembleSearchBridge(self._get_storage(), config=self._config.ensemble),
        )
        return bridge.search(
            dataset_name,
            query_vector,
            columns=columns,
            weights=weights,
            top_k=top_k,
            where=where,
        )

    def lineage_record_event(
        self,
        dataset_name: str,
        operation: str,
        *,
        source_datasets: list[str] | None = None,
        transform_type: str = "",
        actor: str = "system",
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Record a lineage event (Story 8.3).

        Args:
            dataset_name: Target dataset name.
            operation: Operation type (create/append/transform/delete).
            source_datasets: Upstream dataset names.
            transform_type: Transformation type.
            actor: Who triggered the event.
            metadata: Additional context.
        """
        from arrow_lake.catalog.lineage import LineageStore, create_lineage_event

        store = self._get_component(
            "lineage",
            lambda: LineageStore(self._get_storage()),
        )
        event = create_lineage_event(
            dataset_name,
            operation,
            source_datasets=source_datasets,
            transform_type=transform_type,
            actor=actor,
            metadata=metadata,
        )
        store.record_event(event)

    def lineage_history(self, dataset_name: str) -> list[Any]:
        """Get lineage history for a dataset (Story 8.3).

        Args:
            dataset_name: Dataset name.

        Returns:
            List of LineageEvent in chronological order.
        """
        from arrow_lake.catalog.lineage import LineageStore

        store = self._get_component(
            "lineage",
            lambda: LineageStore(self._get_storage()),
        )
        return store.get_dataset_history(dataset_name)

    def lineage_query(self, sql: str) -> Any:
        """SQL query over lineage events (Story 8.3).

        Args:
            sql: SELECT-only SQL query.

        Returns:
            Arrow Table with query results.
        """
        from arrow_lake.catalog.lineage import LineageQueryBridge, LineageStore

        store = self._get_component(
            "lineage",
            lambda: LineageStore(self._get_storage()),
        )
        bridge = LineageQueryBridge(store)
        return bridge.query(sql)

    def audit_record(
        self,
        event_type: str,
        dataset_name: str = "",
        actor: str = "system",
        lance_version: int | None = None,
        metaflow_run_id: str = "",
        metaflow_tags: dict[str, str] | None = None,
        payload: dict[str, Any] | None = None,
    ) -> str:
        """Record an audit entry (Story 8.4).

        Args:
            event_type: Type of event.
            dataset_name: Affected dataset name.
            actor: Who triggered the event.
            lance_version: Lance version at time of event.
            metaflow_run_id: Associated Metaflow run ID.
            metaflow_tags: Associated Metaflow tags.
            payload: Additional event data.

        Returns:
            The generated audit_id.
        """
        from arrow_lake.workflow.audit import AuditTrail

        trail = self._get_component(
            "audit",
            lambda: AuditTrail(
                self._get_storage(),
                audit_dataset=self._config.audit.audit_dataset,
                hmac_secret_key=self._config.audit.hmac_secret_key,
            ),
        )
        return trail.record(
            event_type=event_type,
            dataset_name=dataset_name,
            actor=actor,
            lance_version=lance_version,
            metaflow_run_id=metaflow_run_id,
            metaflow_tags=metaflow_tags,
            payload=payload,
        )

    def audit_verify(self, audit_id: str) -> bool:
        """Verify HMAC integrity of an audit entry (Story 8.4).

        Args:
            audit_id: Audit entry ID to verify.

        Returns:
            True if intact, False if tampered or not found.
        """
        from arrow_lake.workflow.audit import AuditTrail

        trail = self._get_component(
            "audit",
            lambda: AuditTrail(
                self._get_storage(),
                audit_dataset=self._config.audit.audit_dataset,
                hmac_secret_key=self._config.audit.hmac_secret_key,
            ),
        )
        return trail.verify(audit_id)

    def audit_query(
        self,
        dataset_name: str | None = None,
        start: str | None = None,
        end: str | None = None,
        event_type: str | None = None,
    ) -> list[Any]:
        """Query audit entries with optional filters (Story 8.4).

        Args:
            dataset_name: Filter by dataset name.
            start: ISO timestamp lower bound.
            end: ISO timestamp upper bound.
            event_type: Filter by event type.

        Returns:
            List of AuditEntry.
        """
        from arrow_lake.workflow.audit import AuditTrail

        trail = self._get_component(
            "audit",
            lambda: AuditTrail(
                self._get_storage(),
                audit_dataset=self._config.audit.audit_dataset,
                hmac_secret_key=self._config.audit.hmac_secret_key,
            ),
        )
        return trail.query(
            dataset_name=dataset_name,
            start=start,
            end=end,
            event_type=event_type,
        )

    def audit_export(self, dataset_name: str) -> dict[str, Any]:
        """Export audit entries for a dataset (Story 8.4).

        Args:
            dataset_name: Dataset name to export.

        Returns:
            Dict with export metadata and entries.
        """
        from arrow_lake.workflow.audit import AuditTrail

        trail = self._get_component(
            "audit",
            lambda: AuditTrail(
                self._get_storage(),
                audit_dataset=self._config.audit.audit_dataset,
                hmac_secret_key=self._config.audit.hmac_secret_key,
            ),
        )
        return trail.export(dataset_name)

    def version(self) -> str:
        """Return the current platform version."""
        return __version__

    def quality_filter(
        self,
        dataset_name: str,
        active_filters: str = "",
        *,
        mode: str = "all",
    ) -> QualityReport:
        """Run quality filters on a dataset and return a report (Epic 4).

        Delegates to QualityFilterRegistry with built-in filters.

        Args:
            dataset_name: Name of the Lance dataset.
            active_filters: Comma-separated filter names (empty = use config).
            mode: Filter combination mode ("all" for AND, "any" for OR).

        Returns:
            QualityReport with per-filter results and totals.
        """
        from arrow_lake.quality.base import QualityFilterRegistry
        from arrow_lake.quality.builtin import ImageResolutionFilter, TextLengthFilter

        if not active_filters:
            active_filters = self._config.quality.active_filters
        filter_mode = mode or self._config.quality.filter_mode

        registry = QualityFilterRegistry()
        if self._config.quality.enabled:
            registry.register(
                TextLengthFilter(
                    min_chars=self._config.quality.text_min_chars,
                    max_chars=self._config.quality.text_max_chars,
                )
            )
            registry.register(
                ImageResolutionFilter(
                    min_width=self._config.quality.image_min_width,
                    min_height=self._config.quality.image_min_height,
                )
            )

        table = self._get_storage().read_dataset(dataset_name)
        return registry.apply_all(table, active_filters, mode=filter_mode)

    def deduplicate(
        self,
        dataset_name: str,
        *,
        strategy: str | None = None,
        action: str | None = None,
        perceptual_threshold: int | None = None,
    ) -> Any:
        """Run content deduplication on a dataset (Story 4.7).

        Delegates to ContentDeduplicator.

        Args:
            dataset_name: Name of the Lance dataset.
            strategy: "exact", "perceptual", or "both" (None = use config).
            action: "flag" or "remove" (None = use config).
            perceptual_threshold: pHash Hamming distance (None = use config).

        Returns:
            DedupResult with dedup statistics and processed table.
        """
        from arrow_lake.quality.dedup import ContentDeduplicator

        config = self._config.quality
        dedup = ContentDeduplicator(
            strategy=strategy or config.dedup_strategy,
            action=action or config.dedup_action,
            perceptual_threshold=perceptual_threshold or config.dedup_perceptual_threshold,
        )
        table = self._get_storage().read_dataset(dataset_name)
        return dedup.deduplicate(table)

    def export(
        self,
        dataset_name: str,
        output_path: str,
        *,
        format: str | None = None,  # noqa: A002
        columns: list[str] | None = None,
        version: int | None = None,
        compression: str | None = None,
        overwrite: bool = False,
    ) -> Any:
        """Export a dataset to Parquet or CSV (Story 5.9).

        Delegates to ExportBridge.

        Args:
            dataset_name: Name of the Lance dataset.
            output_path: Output file path (.parquet or .csv).
            format: Export format (None = auto-detect from path suffix).
            columns: Optional column subset to export.
            version: Dataset version to export (None = latest).
            compression: Compression codec for Parquet.
            overwrite: Allow overwriting existing file.

        Returns:
            ExportResult with export metadata.
        """
        from arrow_lake.query.export import ExportBridge

        bridge = self._get_component(
            "export",
            lambda: ExportBridge(self._get_storage(), config=self._config.export),
        )
        return bridge.export(
            dataset_name,
            output_path,
            format=format,
            columns=columns,
            version=version,
            compression=compression,
            overwrite=overwrite,
        )

    def daft_query(
        self,
        dataset_name: str,
        *,
        columns: list[str] | None = None,
    ) -> LazyDaftFrame:
        """Load a Lance dataset as a lazy Daft DataFrame (Story 3.7).

        Returns a LazyDaftFrame for chained operations: select, filter,
        sort, join, groupby. Call .collect() to materialize as Arrow Table.

        Args:
            dataset_name: Name of the Lance dataset.
            columns: Optional column subset to load.

        Returns:
            LazyDaftFrame for further lazy operations.
        """
        from arrow_lake.query.daft_api import DaftQueryEngine

        engine = self._get_component(
            "daft",
            lambda: DaftQueryEngine(self._base_uri),
        )
        return engine.load(dataset_name, columns=columns)

    def list_flows(self) -> list[str]:
        """List all registered Metaflow workflow names (Epic 6).

        Returns:
            Sorted list of registered flow names.
        """
        import flows

        flows._register_flows()
        return flows.FlowRegistry.list_flows()

    def get_flow_info(self, name: str) -> dict[str, Any]:
        """Get metadata for a registered Metaflow workflow (Epic 6).

        Args:
            name: Registered flow name.

        Returns:
            Dict with flow class name, module, and docstring.

        Raises:
            WorkflowError: If flow is not registered.
        """
        import flows

        from arrow_lake.exceptions import ErrorCode, WorkflowError

        flows._register_flows()
        try:
            flow_cls = flows.FlowRegistry.get(name)
        except KeyError:
            raise WorkflowError(
                error_code=ErrorCode.WORKFLOW_FLOW_NOT_FOUND,
                message=f"Flow '{name}' is not registered",
            ) from None
        return {
            "name": name,
            "class": flow_cls.__name__,
            "module": flow_cls.__module__,
            "doc": flow_cls.__doc__,
        }
