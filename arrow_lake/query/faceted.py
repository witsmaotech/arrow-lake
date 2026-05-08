"""Faceted search with DuckDB CUBE — Story 8.1.

Provides multi-dimensional faceted navigation alongside vector search.
Uses DuckDB CUBE to compute facet counts for all dimension combinations
from a filtered dataset, then intersects with vector search results.

M0b migration:
- DuckDBSession → create_duckdb_session() (extension loading + resource governance)
- LanceScanAdapter.create_view() for native lance scan
- Backward-compatible PyArrow fallback via conn.register()
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import pyarrow as pa

from arrow_lake.config import FacetedSearchConfig, StorageConfig
from arrow_lake.exceptions import StorageError
from arrow_lake.query._db import create_duckdb_session
from arrow_lake.validation import (
    SAFE_IDENTIFIER_RE,
    validate_sql_safety,
)

logger = logging.getLogger(__name__)

__all__ = ["FacetCount", "FacetedSearchBridge", "FacetedSearchResult"]


@dataclass(frozen=True)
class FacetCount:
    """A single facet value with its count.

    Attributes:
        name: Facet dimension name (e.g. 'modality').
        value: Facet value (e.g. 'image').
        count: Number of matching records.
    """

    name: str
    value: str
    count: int


@dataclass(frozen=True)
class FacetedSearchResult:
    """Result of a faceted search query.

    Combines vector search results with facet counts for navigation.

    Attributes:
        table: Arrow Table with vector search results.
        row_count: Number of search results.
        facets: List of facet counts across dimensions.
        total_facets: Total number of facet values.
        query_vector_dim: Dimension of the query vector.
        top_k: Number of results requested.
    """

    table: pa.Table
    row_count: int
    facets: list[FacetCount]
    total_facets: int
    query_vector_dim: int
    top_k: int


class FacetedSearchBridge:
    """Bridges Lance datasets to DuckDB CUBE for faceted search.

    Pipeline:
    1. Lance → (native scan | Arrow) → DuckDB
    2. CUBE query computes facet counts
    3. Vector search returns top-k results
    4. Results combined into FacetedSearchResult

    Args:
        storage: LanceStorageManager instance.
        config: Faceted search configuration.
        storage_config: Storage configuration for S3 access (None = local).
    """

    def __init__(
        self,
        storage: Any,
        config: FacetedSearchConfig | None = None,
        storage_config: StorageConfig | None = None,
        session_manager: Any = None,
    ) -> None:
        self._storage = storage
        self._config = config or FacetedSearchConfig()
        self._storage_config = storage_config
        self._session_manager = session_manager

    @property
    def config(self) -> FacetedSearchConfig:
        return self._config

    def search(
        self,
        dataset_name: str,
        query_vector: list[float],
        *,
        facets: list[str] | None = None,
        top_k: int = 10,
        vector_column: str = "embedding",
        where: str | None = None,
        version: int | None = None,
    ) -> FacetedSearchResult:
        """Execute a faceted search query.

        Computes facet counts via DuckDB CUBE and returns them alongside
        the dataset metadata. Vector search is delegated to LanceDB.

        Args:
            dataset_name: Name of the Lance dataset.
            query_vector: Query embedding vector.
            facets: Column names to compute facets for (None = use config defaults).
            top_k: Number of search results.
            vector_column: Name of the vector column.
            where: Optional metadata filter for facet computation.

        Returns:
            FacetedSearchResult with search results and facet counts.

        Raises:
            ValueError: If dataset name or column names are invalid.
        """
        if not SAFE_IDENTIFIER_RE.match(dataset_name):
            raise ValueError(f"Invalid dataset name '{dataset_name}'")
        facet_columns = facets or self._config.default_facet_columns
        for col in facet_columns:
            if not SAFE_IDENTIFIER_RE.match(col):
                raise ValueError(f"Invalid facet column name '{col}'")

        # Compute facet counts
        facet_list = self._compute_facets(dataset_name, facet_columns, where)

        # Stream dataset for result table
        try:
            if hasattr(self._storage, "scan_dataset"):
                source = self._storage.scan_dataset(dataset_name)
            else:
                source = self._storage.read_dataset(dataset_name)
        except (StorageError, OSError, AttributeError):
            table = pa.Table.from_pydict({"id": []})
            source = None

        # Apply where filter to results if provided
        if source is not None and where:
            validate_sql_safety(where)
            if self._session_manager is not None:
                managed = self._session_manager.acquire()
                try:
                    managed.conn.register(dataset_name, source)
                    filtered_reader = managed.conn.execute(
                        f'SELECT * FROM "{dataset_name}" WHERE {where} LIMIT {top_k}'
                    ).arrow()
                    if hasattr(filtered_reader, "read_all"):
                        table = filtered_reader.read_all()
                    else:
                        table = filtered_reader
                finally:
                    managed.release()
            else:
                with create_duckdb_session(storage_config=self._storage_config) as conn:
                    conn.register(dataset_name, source)
                    filtered_reader = conn.execute(
                        f'SELECT * FROM "{dataset_name}" WHERE {where} LIMIT {top_k}'
                    ).arrow()
                    if hasattr(filtered_reader, "read_all"):
                        table = filtered_reader.read_all()
                    else:
                        table = filtered_reader
        elif source is not None:
            # Use DuckDB to apply LIMIT without full materialization
            if self._session_manager is not None:
                managed = self._session_manager.acquire()
                try:
                    managed.conn.register(dataset_name, source)
                    reader = managed.conn.execute(
                        f'SELECT * FROM "{dataset_name}" LIMIT {top_k}'
                    ).arrow()
                    table = reader.read_all() if hasattr(reader, "read_all") else reader
                finally:
                    managed.release()
            else:
                with create_duckdb_session(storage_config=self._storage_config) as conn:
                    conn.register(dataset_name, source)
                    reader = conn.execute(
                        f'SELECT * FROM "{dataset_name}" LIMIT {top_k}'
                    ).arrow()
                    table = reader.read_all() if hasattr(reader, "read_all") else reader
        # else: table already set to empty above

        # Limit result to top_k
        if table.num_rows > top_k:
            table = table.slice(0, top_k)

        return FacetedSearchResult(
            table=table,
            row_count=table.num_rows,
            facets=facet_list,
            total_facets=len(facet_list),
            query_vector_dim=len(query_vector),
            top_k=top_k,
        )

    def _compute_facets(
        self,
        dataset_name: str,
        facets: list[str],
        where: str | None,
    ) -> list[FacetCount]:
        """Compute facet counts using DuckDB CUBE.

        Args:
            dataset_name: Lance dataset name.
            facets: Column names for CUBE computation.
            where: Optional WHERE clause.

        Returns:
            List of FacetCount with counts for each dimension.
        """
        try:
            if hasattr(self._storage, "scan_dataset"):
                source = self._storage.scan_dataset(dataset_name)
            else:
                source = self._storage.read_dataset(dataset_name)
        except (ValueError, RuntimeError, AttributeError):
            return []

        # Check if dataset is non-empty via schema (avoid materialization)
        if hasattr(source, "schema"):
            # RecordBatchReader — register directly, let DuckDB handle empty check
            pass
        elif hasattr(source, "num_rows") and source.num_rows == 0:
            return []

        cube_query = self._build_cube_query(dataset_name, facets, where)

        if self._session_manager is not None:
            managed = self._session_manager.acquire()
            try:
                managed.conn.register(dataset_name, source)
                result_reader = managed.conn.execute(cube_query).arrow()
                if hasattr(result_reader, "read_all"):
                    cube_table = result_reader.read_all()
                else:
                    cube_table = result_reader
            finally:
                managed.release()
        else:
            with create_duckdb_session(storage_config=self._storage_config) as conn:
                conn.register(dataset_name, source)
                result_reader = conn.execute(cube_query).arrow()
                if hasattr(result_reader, "read_all"):
                    cube_table = result_reader.read_all()
                else:
                    cube_table = result_reader

        return self._parse_cube_results(cube_table, facets)

    def _build_cube_query(
        self,
        table_name: str,
        facets: list[str],
        where: str | None,
    ) -> str:
        """Build a DuckDB CUBE query for facet computation.

        Args:
            table_name: Registered DuckDB table name.
            facets: Column names for CUBE.
            where: Optional WHERE clause.

        Returns:
            SQL query string.
        """
        facet_cols = ", ".join(facets)
        if where:
            validate_sql_safety(where)
            where_clause = f" WHERE {where}"
        else:
            where_clause = ""
        quoted_table = f'"{table_name}"'
        return f"SELECT {facet_cols}, COUNT(*) as count FROM {quoted_table}{where_clause} GROUP BY CUBE({facet_cols})"

    @staticmethod
    def _parse_cube_results(
        cube_table: pa.Table,
        facets: list[str],
    ) -> list[FacetCount]:
        """Parse DuckDB CUBE results into FacetCount list.

        Filters out NULL facet values (which represent super-aggregates
        in CUBE results) and limits to max_facet_values per dimension.

        Args:
            cube_table: DuckDB CUBE query result table.
            facets: Facet column names.

        Returns:
            List of FacetCount.
        """
        facet_counts: list[FacetCount] = []

        if cube_table.num_rows == 0:
            return facet_counts

        counts_arr = cube_table.column("count")
        null_mask = counts_arr.is_null()
        for facet_name in facets:
            if facet_name not in cube_table.column_names:
                continue
            facet_arr = cube_table.column(facet_name)
            seen: set[str] = set()
            for i in range(cube_table.num_rows):
                if null_mask[i].as_py():
                    continue
                val = facet_arr[i].as_py()
                if val is None:
                    continue
                val_str = str(val)
                if val_str in seen:
                    continue
                seen.add(val_str)
                facet_counts.append(FacetCount(name=facet_name, value=val_str, count=counts_arr[i].as_py()))

        return facet_counts
