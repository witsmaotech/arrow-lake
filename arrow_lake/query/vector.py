"""Vector similarity search — Story 5.1.

ruff noqa: F821 (Any used in annotations with __future__ import)

Provides VectorSearchBridge for vector similarity search over Lance datasets.
Uses IVF_PQ index for scalable nearest-neighbor search.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Any

import pyarrow as pa
import structlog

from arrow_lake.config import VectorSearchConfig
from arrow_lake.exceptions import ErrorCode, QueryError

_LARGE_TABLE_THRESHOLD = 1_000_000
_PQ_MIN_TRAINING_ROWS = 256
_DEFAULT_VECTOR_COLUMN = "text_embedding"
_DEFAULT_EMBEDDING_DIM = 384

_log = structlog.get_logger(__name__)

# Dangerous SQL keywords for where clause validation
_DANGEROUS_KEYWORDS_RE = re.compile(
    r"\b(INSERT|UPDATE|DELETE|DROP|ALTER|CREATE|TRUNCATE|GRANT|REVOKE|"
    r"EXEC|EXECUTE|UNION|EXCEPT|INTERSECT)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class VectorSearchResult:
    """Result of a vector similarity search.

    Attributes:
        table: Arrow Table with original columns + '_distance' score column.
        row_count: Number of results returned.
        query_vector_dim: Dimensionality of the query vector used.
        metric: Distance metric used for the search.
        top_k: Maximum number of results requested.
        max_distance: Worst distance score in results, or None if empty.
    """

    table: pa.Table
    row_count: int
    query_vector_dim: int
    metric: str
    top_k: int
    max_distance: float | None


@dataclass(frozen=True)
class IndexInfo:
    """Metadata about a vector index on a dataset.

    Attributes:
        name: Index name.
        index_type: Index type (e.g., 'IVF_PQ').
        distance_type: Distance metric (e.g., 'cosine', 'l2').
        num_indexed_rows: Number of rows covered by the index.
        num_unindexed_rows: Number of rows NOT covered by the index.
        columns: Columns the index covers.
    """

    name: str
    index_type: str
    distance_type: str
    num_indexed_rows: int
    num_unindexed_rows: int
    columns: list[str]


class VectorSearchBridge:
    """Bridges Lance datasets to LanceDB vector similarity search.

    Pipeline: query vector -> LanceDB search -> Arrow Table with _distance.

    Thread safety: safe for concurrent reads. NOT safe for concurrent
    index creation on the same dataset.

    Args:
        storage: LanceStorageManager instance.
        config: Vector search configuration (None = use defaults).
    """

    def __init__(self, storage: Any, config: VectorSearchConfig | None = None) -> None:
        self._storage = storage
        self._config = config or VectorSearchConfig()

    def create_index(
        self,
        dataset_name: str,
        *,
        metric: str = "",
        vector_column: str = _DEFAULT_VECTOR_COLUMN,
        index_type: str = "",
        num_partitions: int | None = None,
        num_sub_vectors: int | None = None,
        num_bits: int | None = None,
        replace: bool = True,
    ) -> IndexInfo:
        """Create a vector similarity index on a dataset.

        Auto-adjusts num_partitions based on row count:
        - <1M rows: uses configured num_partitions (default 256)
        - >=1M rows: uses sqrt(num_rows), capped at 4096

        Args:
            dataset_name: Name of the Lance dataset.
            metric: Distance metric ('cosine', 'l2', 'dot').
            vector_column: Name of the vector column to index.
            index_type: LanceDB index type.
            num_partitions: IVF partitions (None = auto).
            num_sub_vectors: PQ sub-vectors (must be multiple of 8).
            replace: Whether to replace existing index.

        Returns:
            IndexInfo with index metadata.

        Raises:
            QueryError: If dataset not found, too few rows, or index fails.
        """
        table = self._storage.open_dataset(dataset_name)

        # Resolve defaults from config
        metric = metric or self._config.metric.value
        index_type = index_type or self._config.default_index_type.value
        effective_num_bits = num_bits if num_bits is not None else self._config.num_bits
        effective_sub_vectors = (
            num_sub_vectors if num_sub_vectors is not None else self._config.num_sub_vectors
        )

        num_rows = table.count_rows()
        if num_rows < _PQ_MIN_TRAINING_ROWS:
            raise QueryError(
                error_code=ErrorCode.VECTOR_INDEX_TOO_FEW_ROWS,
                message=(
                    f"Dataset '{dataset_name}' has {num_rows} rows, "
                    f"minimum {_PQ_MIN_TRAINING_ROWS} required for index creation"
                ),
            )

        if num_partitions is None:
            num_partitions = self._auto_select_partitions(
                num_rows,
                base_partitions=self._config.num_partitions,
            )

        # LanceDB: column must exist in schema for create_index
        schema = table.schema
        if vector_column not in schema.names:
            raise QueryError(
                error_code=ErrorCode.VECTOR_SEARCH_FAILED,
                message=f"Column '{vector_column}' not found in dataset '{dataset_name}'",
            )

        try:
            table.create_index(
                metric=metric,
                vector_column_name=vector_column,
                index_type=index_type,
                num_partitions=num_partitions,
                num_sub_vectors=effective_sub_vectors,
                num_bits=effective_num_bits,
                replace=replace,
            )
        except Exception as exc:
            raise QueryError(
                error_code=ErrorCode.VECTOR_INDEX_FAILED,
                message=f"Failed to create vector index on '{dataset_name}': {exc}",
            ) from exc

        # Retrieve index info
        info = self._get_latest_index_info(table, vector_column)
        if info is None:
            raise QueryError(
                error_code=ErrorCode.VECTOR_INDEX_FAILED,
                message=f"Index created but metadata unavailable for '{dataset_name}'",
            )
        return info

    def search(
        self,
        dataset_name: str,
        query_vector: list[float],
        *,
        top_k: int | None = None,
        metric: str | None = None,
        vector_column: str = _DEFAULT_VECTOR_COLUMN,
        where: str | None = None,
        nprobes: int | None = None,
    ) -> VectorSearchResult:
        """Search for similar vectors in a dataset.

        If no index exists, LanceDB falls back to flat brute-force search.

        Args:
            dataset_name: Name of the Lance dataset.
            query_vector: Query embedding vector (must be non-empty).
            top_k: Number of results to return.
            metric: Distance metric (None = use index metric).
            vector_column: Name of the vector column.
            where: Optional metadata filter expression.
                WARNING: Must not contain user-supplied values without
                sanitization — LanceDB uses a SQL engine.
            nprobes: Number of IVF partitions to probe (None = auto).

        Returns:
            VectorSearchResult with Arrow table + metadata.
            Empty results return 0-row table (not error).

        Raises:
            QueryError: If dataset not found, vector invalid, or dimension mismatch.
        """
        # Validate query vector is non-empty (M5)
        if not query_vector:
            raise QueryError(
                error_code=ErrorCode.VECTOR_INVALID_QUERY,
                message="Query vector must not be empty",
            )

        # Validate where clause for injection prevention (C1)
        if where is not None:
            self._validate_where_clause(where)

        # Resolve defaults from config
        effective_top_k = top_k if top_k is not None else self._config.default_top_k
        if metric is None:
            metric = self._config.metric.value

        table = self._storage.open_dataset(dataset_name)

        # Validate query vector dimension
        schema = table.schema
        if vector_column not in schema.names:
            raise QueryError(
                error_code=ErrorCode.VECTOR_SEARCH_FAILED,
                message=f"Column '{vector_column}' not found in dataset '{dataset_name}'",
            )

        expected_dim = self._get_vector_dimension(schema, vector_column)
        # M2: Raise on unknown dimension (variable-length list)
        if expected_dim == 0:
            raise QueryError(
                error_code=ErrorCode.VECTOR_DIMENSION_MISMATCH,
                message=(
                    f"Column '{vector_column}' is not a fixed-size list; "
                    f"vector dimension is unknown"
                ),
            )
        if len(query_vector) != expected_dim:
            raise QueryError(
                error_code=ErrorCode.VECTOR_DIMENSION_MISMATCH,
                message=(
                    f"Query vector dimension {len(query_vector)} does not match "
                    f"column '{vector_column}' dimension {expected_dim}"
                ),
            )

        # Build search query
        query_builder = table.search(
            query=query_vector,
            vector_column_name=vector_column,
        )

        if where is not None:
            query_builder = query_builder.where(where)

        query_builder = query_builder.limit(effective_top_k)

        if nprobes is not None:
            query_builder = query_builder.nprobes(min(nprobes, self._config.max_nprobes))

        if metric is not None:
            query_builder = query_builder.distance_type(metric)

        try:
            result_table = query_builder.to_arrow()
        except Exception as exc:
            raise QueryError(
                error_code=ErrorCode.VECTOR_SEARCH_FAILED,
                message=f"Vector search failed on '{dataset_name}': {exc}",
            ) from exc

        # Extract max distance for diagnostics
        max_distance: float | None = None
        if result_table.num_rows > 0 and "_distance" in result_table.column_names:
            distances = result_table.column("_distance").to_pylist()
            max_distance = max(distances) if distances else None

        # M1: Detect actual metric when None was passed
        used_metric = metric
        if used_metric is None:
            idx_info = self._get_latest_index_info(table, vector_column)
            used_metric = idx_info.distance_type if idx_info else "cosine"

        return VectorSearchResult(
            table=result_table,
            row_count=result_table.num_rows,
            query_vector_dim=len(query_vector),
            metric=used_metric,
            top_k=effective_top_k,
            max_distance=max_distance,
        )

    def get_index_info(
        self,
        dataset_name: str,
        vector_column: str | None = None,
    ) -> IndexInfo | None:
        """Get information about the vector index on a dataset.

        Args:
            dataset_name: Name of the Lance dataset.
            vector_column: Column to check (None = check default columns).

        Returns:
            IndexInfo if index exists, None otherwise.
        """
        table = self._storage.open_dataset(dataset_name)

        # Check specific column or default columns
        columns_to_check: list[str]
        if vector_column is not None:
            columns_to_check = [vector_column]
        else:
            columns_to_check = ["text_embedding", "image_embedding"]

        for col in columns_to_check:
            if col in table.schema.names:
                info = self._get_latest_index_info(table, col)
                if info is not None:
                    return info
        return None

    @staticmethod
    def _validate_where_clause(where: str) -> None:
        """Validate where clause for dangerous SQL patterns.

        Raises:
            QueryError: If dangerous SQL keywords are detected.
        """
        match = _DANGEROUS_KEYWORDS_RE.search(where)
        if match:
            raise QueryError(
                error_code=ErrorCode.VECTOR_INVALID_QUERY,
                message=(
                    f"Where clause contains dangerous SQL keyword: {match.group()!r}. "
                    f"Only SELECT-safe filter expressions are allowed."
                ),
            )

    @staticmethod
    def _auto_select_partitions(num_rows: int, base_partitions: int = 256) -> int:
        """Auto-select IVF partitions based on row count.

        - <1M rows: use base_partitions
        - >=1M rows: use sqrt(num_rows), capped at 4096
        """
        if num_rows < _LARGE_TABLE_THRESHOLD:
            return base_partitions
        return min(int(math.sqrt(num_rows)), 4096)

    @staticmethod
    def _get_vector_dimension(schema: pa.Schema, column: str) -> int:
        """Extract vector dimension from a fixed_size_list column."""
        field = schema.field(column)
        field_type = field.type
        if hasattr(field_type, "list_size"):
            return int(field_type.list_size)
        # Variable-length list — dimension unknown
        if pa.types.is_list(field_type):
            return 0
        return 0

    @staticmethod
    def _get_latest_index_info(
        table: Any,
        vector_column: str,
    ) -> IndexInfo | None:
        """Extract IndexInfo from the latest index on a vector column."""
        try:
            indices = list(table.list_indices())
            for idx_config in indices:
                cols = idx_config.columns if hasattr(idx_config, "columns") else []
                if vector_column in cols:
                    stats = table.index_stats(idx_config.name)
                    if stats is not None:
                        return IndexInfo(
                            name=idx_config.name,
                            index_type=stats.index_type
                            if hasattr(stats, "index_type")
                            else str(idx_config.index_type),
                            distance_type=stats.distance_type
                            if hasattr(stats, "distance_type")
                            else "",
                            num_indexed_rows=stats.num_indexed_rows
                            if hasattr(stats, "num_indexed_rows")
                            else 0,
                            num_unindexed_rows=stats.num_unindexed_rows
                            if hasattr(stats, "num_unindexed_rows")
                            else 0,
                            columns=list(cols),
                        )
        except Exception:
            _log.debug(
                "Failed to retrieve index info for column '%s'",
                vector_column,
                exc_info=True,
            )
        return None
