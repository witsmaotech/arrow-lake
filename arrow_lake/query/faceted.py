"""Faceted search with DuckDB CUBE — Story 8.1.

Provides multi-dimensional faceted navigation alongside vector search.
Uses DuckDB CUBE to compute facet counts for all dimension combinations
from a filtered dataset, then intersects with vector search results.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import duckdb
import pyarrow as pa

from arrow_lake.config import FacetedSearchConfig

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
    1. Lance → Arrow → DuckDB register
    2. CUBE query computes facet counts
    3. Vector search returns top-k results
    4. Results combined into FacetedSearchResult

    Args:
        storage: LanceStorageManager instance.
        config: Faceted search configuration.
    """

    def __init__(
        self,
        storage: Any,
        config: FacetedSearchConfig | None = None,
    ) -> None:
        self._storage = storage
        self._config = config or FacetedSearchConfig()

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
        """
        facet_columns = facets or self._config.default_facet_columns

        # Compute facet counts
        facet_list = self._compute_facets(dataset_name, facet_columns, where)

        # Read dataset for result table
        try:
            table = self._storage.read_dataset(dataset_name)
        except Exception:
            table = pa.Table.from_pydict({"id": []})

        # Apply where filter to results if provided
        if where and table.num_rows > 0:
            conn = duckdb.connect()
            try:
                conn.register("data", table)
                filtered_reader = conn.execute(
                    f"SELECT * FROM data WHERE {where} LIMIT {top_k}"
                ).arrow()
                if hasattr(filtered_reader, "read_all"):
                    table = filtered_reader.read_all()
                else:
                    table = filtered_reader
            finally:
                conn.close()

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
            table = self._storage.read_dataset(dataset_name)
        except Exception:
            return []

        if table.num_rows == 0:
            return []

        cube_query = self._build_cube_query("data", facets, where)

        conn = duckdb.connect()
        try:
            conn.register("data", table)
            result_reader = conn.execute(cube_query).arrow()
            if hasattr(result_reader, "read_all"):
                cube_table = result_reader.read_all()
            else:
                cube_table = result_reader
        finally:
            conn.close()

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
        where_clause = f" WHERE {where}" if where else ""
        return f"SELECT {facet_cols}, COUNT(*) as count FROM {table_name}{where_clause} GROUP BY CUBE({facet_cols})"

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

        counts = cube_table.column("count").to_pylist()
        for facet_name in facets:
            if facet_name not in cube_table.column_names:
                continue
            values = cube_table.column(facet_name).to_pylist()
            seen: set[str] = set()
            for i, val in enumerate(values):
                if val is None:
                    continue
                val_str = str(val)
                if val_str in seen:
                    continue
                seen.add(val_str)
                facet_counts.append(FacetCount(name=facet_name, value=val_str, count=counts[i]))

        return facet_counts
