"""Vector similarity search — Story 5.1.

ruff noqa: F821 (Any used in annotations with __future__ import)

Provides VectorSearchBridge for vector similarity search over Lance datasets.
Uses IVF_PQ index for scalable nearest-neighbor search.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

import duckdb
import pyarrow as pa
import structlog

from arrow_lake.config import StorageConfig, VectorSearchConfig
from arrow_lake.exceptions import ErrorCode, QueryError
from arrow_lake.validation import (
    validate_identifier,
    validate_where_clause,
)

_LARGE_TABLE_THRESHOLD = 1_000_000
_SMALL_IVF_THRESHOLD = 65_536
_PQ_MIN_TRAINING_ROWS = 256
_DEFAULT_VECTOR_COLUMN = "text_embedding"
_DEFAULT_EMBEDDING_DIM = 384

_log = structlog.get_logger(__name__)


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
    """Bridges Lance datasets to vector similarity search.

    Dual-path strategy:
    - DuckDB native: uses lance_vector_search() SQL (preferred when available)
    - LanceDB SDK: uses lancedb search builder (fallback)

    Thread safety: safe for concurrent reads. NOT safe for concurrent
    index creation on the same dataset.

    Args:
        storage: LanceStorageManager instance.
        config: Vector search configuration (None = use defaults).
        storage_config: Storage configuration for S3 access (None = local).
        lance_scan_mode: Scan mode — "auto", "native", or "pyarrow_fallback".
    """

    def __init__(
        self,
        storage: Any,
        config: VectorSearchConfig | None = None,
        storage_config: StorageConfig | None = None,
        lance_scan_mode: str = "auto",
        session_manager: Any = None,
    ) -> None:
        self._storage = storage
        self._config = config or VectorSearchConfig()
        self._storage_config = storage_config
        self._lance_scan_mode = lance_scan_mode
        self._session_manager = session_manager

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
        m: int | None = None,
        ef_construction: int | None = None,
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
            m: HNSW bi-directional links per node (HNSW/IVF_HNSW_PQ only).
            ef_construction: HNSW build-time candidate list size.

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
            create_kwargs: dict[str, Any] = dict(
                metric=metric,
                vector_column_name=vector_column,
                index_type=index_type,
                replace=replace,
            )
            # IVF-family parameters (v1.7.1 #7: type-aware).
            # PQ/RQ need sub_vectors+bits; SQ needs bits only; FLAT needs partitions only.
            itype = index_type.upper()
            if itype != "HNSW":
                create_kwargs["num_partitions"] = num_partitions
                if itype in ("IVF_PQ", "IVF_HNSW_PQ", "IVF_RQ"):
                    create_kwargs["num_sub_vectors"] = effective_sub_vectors
                    create_kwargs["num_bits"] = effective_num_bits
                elif itype in ("IVF_SQ", "IVF_HNSW_SQ"):
                    create_kwargs["num_bits"] = effective_num_bits
                # IVF_FLAT: num_partitions only
            # HNSW parameters (pure HNSW and IVF_HNSW_*)
            if m is not None:
                create_kwargs["M"] = m
            if ef_construction is not None:
                create_kwargs["ef_construction"] = ef_construction
            table.create_index(**create_kwargs)
        except (ValueError, RuntimeError) as exc:
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
        version: int | None = None,
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

        table = self._storage.open_dataset_versioned(dataset_name, version) if version else self._storage.open_dataset(dataset_name)

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
        if (
            self._lance_scan_mode != "pyarrow_fallback"
            and hasattr(self._storage, "dataset_uri")
            and where is None
        ):
            try:
                result_table = self._search_via_duckdb(
                    dataset_name,
                    query_vector,
                    top_k=effective_top_k,
                    metric=metric,
                    vector_column=vector_column,
                    nprobes=nprobes,
                )
            except QueryError:
                raise
            except (duckdb.Error, OSError):
                _log.debug(
                    "DuckDB vector search failed, falling back to LanceDB SDK", exc_info=True
                )
                result_table = self._search_via_lancedb(
                    table,
                    query_vector,
                    effective_top_k,
                    metric,
                    vector_column,
                    where,
                    nprobes,
                )
        else:
            result_table = self._search_via_lancedb(
                table,
                query_vector,
                effective_top_k,
                metric,
                vector_column,
                where,
                nprobes,
            )

        # Extract max distance for diagnostics
        max_distance: float | None = None
        if result_table.num_rows > 0 and "_distance" in result_table.column_names:
            max_val = pa.compute.max(result_table.column("_distance"))
            max_distance = max_val.as_py() if max_val.is_valid else None

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

    async def search_async(
        self,
        dataset_name: str,
        query_vector: list[float],
        *,
        top_k: int | None = None,
        vector_column: str = _DEFAULT_VECTOR_COLUMN,
        where: str | None = None,
        nprobes: int | None = None,
    ) -> pa.Table:
        """Async vector search via lancedb connect_async (v1.7.1 #9).

        Incremental async entry point for high-concurrency workloads. The
        connection is opened per call (no pooling) — for production throughput,
        pair with an async connection pool and load-test before relying on it.

        Note on filtering (#6): DuckDB ``lance_vector_search`` has no filter
        parameter, so prefiltered search goes through the SDK path (here) which
        honors scalar indexes created via #3.

        Args:
            dataset_name: Name of the Lance dataset.
            query_vector: Query embedding vector.
            top_k: Number of results (None = config default).
            vector_column: Vector column name.
            where: Optional metadata filter (validated).
            nprobes: IVF partitions to probe (None = config).

        Returns:
            Arrow Table with results and _distance column.

        Raises:
            QueryError: If storage lacks base uri, vector empty, or search fails.
        """
        if not query_vector:
            raise QueryError(
                error_code=ErrorCode.VECTOR_INVALID_QUERY,
                message="Query vector must not be empty",
            )
        if where is not None:
            self._validate_where_clause(where)

        base_uri = getattr(self._storage, "_connect_uri", None)
        if not base_uri:
            raise QueryError(
                error_code=ErrorCode.VECTOR_SEARCH_FAILED,
                message="async search requires storage._connect_uri",
            )

        import lancedb

        effective_top_k = top_k if top_k is not None else self._config.default_top_k
        try:
            async_db = await lancedb.connect_async(
                base_uri, storage_options=getattr(self._storage, "_storage_options", None)
            )
            table = await async_db.open_table(dataset_name)
            q = table.search(query_vector, vector_column_name=vector_column).limit(effective_top_k)
            if where is not None:
                q = q.where(where)
            q = q.nprobes(nprobes or self._config.nprobes)
            q = q.refine_factor(self._config.refine_factor)
            return await q.to_arrow()
        except (ValueError, RuntimeError) as exc:
            raise QueryError(
                error_code=ErrorCode.VECTOR_SEARCH_FAILED,
                message=f"Async vector search failed: {exc}",
            ) from exc

    def _search_via_duckdb(
        self,
        dataset_name: str,
        query_vector: list[float],
        *,
        top_k: int,
        metric: str | None,
        vector_column: str,
        nprobes: int | None,
    ) -> pa.Table:
        """Search via DuckDB lance_vector_search() SQL function.

        Args:
            dataset_name: Lance dataset name.
            query_vector: Query embedding vector.
            top_k: Number of results.
            metric: Distance metric.
            vector_column: Vector column name.
            nprobes: Number of IVF partitions to probe.

        Returns:
            Arrow Table with search results and _distance column.

        Raises:
            QueryError: If DuckDB search fails.
        """
        from arrow_lake.query._db import create_duckdb_session

        if not hasattr(self._storage, "dataset_uri"):
            raise QueryError(
                error_code=ErrorCode.VECTOR_SEARCH_FAILED,
                message="storage.dataset_uri() not available for DuckDB native search",
            )

        # Validate column name to prevent SQL injection
        validate_identifier(vector_column)

        # Validate top_k and nprobes bounds
        if top_k < 1 or top_k > 10_000:
            raise QueryError(
                error_code=ErrorCode.VECTOR_INVALID_QUERY,
                message=f"top_k must be between 1 and 10000, got {top_k}",
            )
        if nprobes is not None and (nprobes < 1 or nprobes > 65_536):
            raise QueryError(
                error_code=ErrorCode.VECTOR_INVALID_QUERY,
                message=f"nprobes must be between 1 and 65536, got {nprobes}",
            )

        uri = self._storage.dataset_uri(dataset_name)
        vec_list = "[" + ", ".join(str(v) for v in query_vector) + "]"
        from arrow_lake.validation import escape_sql_literal
        safe_uri = escape_sql_literal(uri)

        sql_parts = [
            "SELECT * FROM lance_vector_search(",
            f"  '{safe_uri}',",
            f"  '{vector_column}',",
            f"  CAST({vec_list} AS FLOAT[]),",
            "  explain_verbose := false,",
            f"  use_index := {metric is not None},",
            "  prefilter := false,",
            f"  refine_factor := {(self._config.refine_factor)}::BIGINT,",
            f"  nprobs := {(nprobes or self._config.nprobes)}::BIGINT,",
            f"  k := {top_k}::BIGINT",
            ")",
        ]

        sql = "\n".join(sql_parts) + f" LIMIT {top_k}"

        if self._session_manager is not None:
            managed = self._session_manager.acquire()
            try:
                reader = managed.conn.execute(sql).arrow()
                if hasattr(reader, "read_all"):
                    return reader.read_all()
                return reader
            finally:
                managed.release()
        else:
            from arrow_lake.query._db import create_duckdb_session
            with create_duckdb_session(storage_config=self._storage_config) as conn:
                reader = conn.execute(sql).arrow()
                if hasattr(reader, "read_all"):
                    return reader.read_all()
                return reader

    def _search_via_lancedb(
        self,
        table: Any,
        query_vector: list[float],
        top_k: int,
        metric: str | None,
        vector_column: str,
        where: str | None,
        nprobes: int | None,
    ) -> pa.Table:
        """Search via LanceDB SDK (original path)."""
        query_builder = table.search(
            query=query_vector,
            vector_column_name=vector_column,
        )

        if where is not None:
            query_builder = query_builder.where(where)

        query_builder = query_builder.limit(top_k)

        if nprobes is not None:
            query_builder = query_builder.nprobes(
                min(nprobes, self._config.max_nprobes),
            )

        if metric is not None:
            query_builder = query_builder.distance_type(metric)

        # v1.7.1 #5: apply refine_factor (mirror DuckDB path at vector.py:415).
        # No-op for flat (brute-force) search; improves recall when an IVF index exists.
        query_builder = query_builder.refine_factor(self._config.refine_factor)

        try:
            return query_builder.to_arrow()
        except (ValueError, RuntimeError) as exc:
            raise QueryError(
                error_code=ErrorCode.VECTOR_SEARCH_FAILED,
                message=f"Vector search failed: {exc}",
            ) from exc

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
        from arrow_lake.exceptions import ErrorCode, QueryError

        try:
            validate_where_clause(where)
        except ValueError as exc:
            raise QueryError(
                error_code=ErrorCode.VECTOR_INVALID_QUERY,
                message=str(exc),
            ) from exc

    @staticmethod
    def _auto_select_partitions(num_rows: int, base_partitions: int = 256) -> int:
        """Auto-select IVF partitions based on row count.

        - <65_536 rows (small): use min(sqrt(num_rows) * 4, base_partitions),
          which avoids the lance KMeans empty-cluster warning.
        - >=1M rows: use sqrt(num_rows), capped at 4096.
        """
        if num_rows < _SMALL_IVF_THRESHOLD:
            return min(int(math.sqrt(num_rows)) * 4, base_partitions)
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
        except (ValueError, RuntimeError, OSError):
            _log.debug(
                "Failed to retrieve index info for column '%s'",
                vector_column,
                exc_info=True,
            )
        return None
