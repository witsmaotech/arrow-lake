"""Search mixin — vector search, text search, hybrid, faceted, ensemble."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from arrow_lake.query.ensemble import EnsembleSearchResult
    from arrow_lake.query.faceted import FacetedSearchResult
    from arrow_lake.query.fts import FullTextSearchResult
    from arrow_lake.query.hybrid import HybridSearchResult
    from arrow_lake.query.vector import IndexInfo, VectorSearchResult


class _LakeSearchMixin:
    """Provides vector search, text search, hybrid search, faceted, and ensemble."""

    def _bridge_kwargs(self) -> dict[str, Any]:
        """Common kwargs for all search bridge factories."""
        return {
            "storage_config": self._config.storage,
            "lance_scan_mode": self._config.olap.lance_scan_mode,
        }

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
            lambda: VectorSearchBridge(
                self._get_storage(),
                config=self._config.vector,
                **self._bridge_kwargs(),
            ),
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
            lambda: VectorSearchBridge(
                self._get_storage(),
                config=self._config.vector,
                **self._bridge_kwargs(),
            ),
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
            lambda: FullTextSearchBridge(
                self._get_storage(),
                config=self._config.fts,
                **self._bridge_kwargs(),
            ),
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
            lambda: FullTextSearchBridge(
                self._get_storage(),
                config=self._config.fts,
                **self._bridge_kwargs(),
            ),
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
            lambda: HybridSearchBridge(
                self._get_storage(),
                config=self._config.hybrid,
                **self._bridge_kwargs(),
            ),
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
            lambda: FacetedSearchBridge(
                self._get_storage(),
                config=self._config.faceted,
                storage_config=self._config.storage,
            ),
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
