"""Multi-model ensemble search — Story 8.2.

Provides ensemble search across multiple embedding columns using
weighted Reciprocal Rank Fusion (RRF). Reuses VectorSearchBridge
for individual column searches and fuses results via generalized RRF.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Any

import pyarrow as pa

from arrow_lake.exceptions import ErrorCode, QueryError

__all__ = ["EnsembleSearchBridge", "EnsembleSearchConfig", "EnsembleSearchResult"]


@dataclass(frozen=True)
class EnsembleSearchConfig:
    """Configuration for ensemble search.

    Attributes:
        default_top_k: Default number of results.
        rrf_k: RRF smoothing constant.
        fusion_method: Fusion method (only "rrf" supported).
        candidate_multiplier: Per-column candidate pool size = top_k * multiplier.
    """

    default_top_k: int = 10
    rrf_k: int = 60
    fusion_method: str = "rrf"
    candidate_multiplier: int = 3


@dataclass(frozen=True)
class EnsembleSearchResult:
    """Result of an ensemble search query.

    Attributes:
        table: Arrow Table with search results + _ensemble_score column.
        row_count: Number of results.
        columns_searched: Tuple of embedding columns searched.
        fusion_method: Fusion method used.
        top_k: Number of results requested.
        query_vector_dim: Dimension of the query vector.
    """

    table: pa.Table
    row_count: int
    columns_searched: tuple[str, ...]
    fusion_method: str
    top_k: int
    query_vector_dim: int


class EnsembleSearchBridge:
    """Bridges multiple embedding columns via weighted RRF fusion.

    Runs VectorSearchBridge on each specified column, then fuses
    results using weighted Reciprocal Rank Fusion.

    Args:
        storage: LanceStorageManager instance.
        config: Ensemble search configuration.
    """

    def __init__(
        self,
        storage: Any,
        config: EnsembleSearchConfig | None = None,
    ) -> None:
        self._storage = storage
        self._config = config or EnsembleSearchConfig()

    @property
    def config(self) -> EnsembleSearchConfig:
        return self._config

    def search(
        self,
        dataset_name: str,
        query_vector: list[float],
        *,
        columns: list[str] | None = None,
        weights: dict[str, float] | None = None,
        top_k: int | None = None,
        where: str | None = None,
    ) -> EnsembleSearchResult:
        """Execute ensemble search across multiple embedding columns.

        Args:
            dataset_name: Name of the Lance dataset.
            query_vector: Query embedding vector (same dim for all columns).
            columns: Embedding columns to search (None = use first vector column).
            weights: Per-column weights for RRF (None = all 1.0).
            top_k: Number of results (None = use config default).
            where: Optional metadata filter.

        Returns:
            EnsembleSearchResult with fused results.

        Raises:
            QueryError: If no columns specified or column not found.
        """
        effective_top_k = top_k if top_k is not None else self._config.default_top_k

        # Resolve columns: validate and check dimension
        search_columns = self._resolve_columns(dataset_name, columns, len(query_vector))

        if not search_columns:
            raise QueryError(
                error_code=ErrorCode.ENSEMBLE_NO_COLUMNS,
                message="No embedding columns found for ensemble search",
            )

        # Resolve weights
        resolved_weights = (
            [weights.get(c, 1.0) for c in search_columns]
            if weights
            else [1.0] * len(search_columns)
        )

        # Run vector search on each column
        from arrow_lake.query.vector import VectorSearchBridge

        bridge = VectorSearchBridge(self._storage)
        # Use larger candidate pool for multi-column fusion
        candidate_k = (
            effective_top_k * self._config.candidate_multiplier
            if len(search_columns) > 1
            else effective_top_k
        )

        result_tables = []
        for col in search_columns:
            try:
                result = bridge.search(
                    dataset_name,
                    query_vector,
                    top_k=candidate_k,
                    vector_column=col,
                    where=where,
                )
                result_tables.append(result.table)
            except Exception as exc:
                raise QueryError(
                    error_code=ErrorCode.VECTOR_SEARCH_FAILED,
                    message=f"Ensemble search failed on column '{col}': {exc}",
                ) from exc

        # Fuse results via weighted RRF
        if len(result_tables) == 1:
            fused_table = result_tables[0]
        else:
            fused_table = self._weighted_rrf_fuse(
                result_tables,
                k=self._config.rrf_k,
                top_k=effective_top_k,
                weights=resolved_weights,
            )

        return EnsembleSearchResult(
            table=fused_table,
            row_count=fused_table.num_rows,
            columns_searched=tuple(search_columns),
            fusion_method=self._config.fusion_method,
            top_k=effective_top_k,
            query_vector_dim=len(query_vector),
        )

    def _resolve_columns(
        self,
        dataset_name: str,
        columns: list[str] | None,
        query_dim: int,
    ) -> list[str]:
        """Resolve and validate embedding columns.

        Args:
            dataset_name: Dataset name.
            columns: User-specified columns (None = auto-detect).
            query_dim: Expected vector dimension.

        Returns:
            List of validated column names.

        Raises:
            QueryError: If a specified column is not found or dimension mismatch.
        """
        ds = self._storage.open_dataset(dataset_name)
        schema = ds.schema

        if columns:
            resolved = []
            for col in columns:
                if col not in schema.names:
                    raise QueryError(
                        error_code=ErrorCode.ENSEMBLE_COLUMN_NOT_FOUND,
                        message=f"Column '{col}' not found in dataset '{dataset_name}'",
                    )
                field = schema.field(col)
                if pa.types.is_fixed_size_list(field.type):
                    dim = field.type.list_size
                    if dim != query_dim:
                        raise QueryError(
                            error_code=ErrorCode.VECTOR_DIMENSION_MISMATCH,
                            message=f"Column '{col}' has dimension {dim}, expected {query_dim}",
                        )
                resolved.append(col)
            return resolved

        # Auto-detect: find all fixed-size-list columns matching query_dim
        vector_cols = []
        for field in schema:
            if pa.types.is_fixed_size_list(field.type) and field.type.list_size == query_dim:
                vector_cols.append(field.name)

        return vector_cols

    @staticmethod
    def _weighted_rrf_fuse(
        tables: list[pa.Table],
        k: int,
        top_k: int,
        weights: list[float],
    ) -> pa.Table:
        """Fuse N ranked tables using weighted Reciprocal Rank Fusion.

        RRF formula: score(doc) = Σ weight_i / (rank(doc, list_i) + k)

        Args:
            tables: List of Arrow Tables with 'id' column.
            k: RRF smoothing constant.
            top_k: Maximum results to return.
            weights: Per-table weights (same length as tables).

        Returns:
            Fused Arrow Table with _ensemble_score column, sorted descending.
        """
        _score_columns = {"_distance", "_score", "_rrf_score", "_ensemble_score"}
        rrf_scores: dict[str, float] = defaultdict(float)
        id_to_row: dict[str, dict[str, Any]] = {}

        def _collect_rows(table: pa.Table) -> None:
            if table.num_rows == 0 or "id" not in table.column_names:
                return
            ids = table.column("id").to_pylist()
            for i, doc_id in enumerate(ids):
                id_str = str(doc_id)
                if id_str not in id_to_row:
                    row: dict[str, Any] = {}
                    for col_name in table.column_names:
                        if col_name in _score_columns:
                            continue
                        row[col_name] = table.column(col_name)[i].as_py()
                    id_to_row[id_str] = row

        for table in tables:
            _collect_rows(table)

        for table, weight in zip(tables, weights, strict=True):
            if table.num_rows == 0 or "id" not in table.column_names:
                continue
            for rank, doc_id in enumerate(table.column("id").to_pylist()):
                rrf_scores[str(doc_id)] += weight / (rank + 1 + k)

        # Sort by descending score, take top_k
        sorted_ids = sorted(
            rrf_scores.keys(),
            key=lambda doc_id: rrf_scores[doc_id],
            reverse=True,
        )[:top_k]

        if not sorted_ids:
            return pa.table({"id": []})

        # Build result columns
        first_row = id_to_row[sorted_ids[0]]
        col_names = [k for k in first_row if k != "id"]
        columns: dict[str, list[Any]] = {"id": []}
        for name in col_names:
            columns[name] = []
        columns["_ensemble_score"] = []

        for doc_id in sorted_ids:
            row = id_to_row[doc_id]
            columns["id"].append(row.get("id", doc_id))
            for name in col_names:
                columns[name].append(row.get(name))
            columns["_ensemble_score"].append(round(rrf_scores[doc_id], 6))

        return pa.table(columns)
