"""Full-text search — Story 5.2.

ruff noqa: F821 (Any used in annotations with __future__ import)

Provides FullTextSearchBridge for full-text search over Lance datasets.
Uses LanceDB built-in FTS (BM25) with lance-index backend.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pyarrow as pa
import structlog

from arrow_lake.config import FullTextSearchConfig
from arrow_lake.exceptions import ErrorCode, QueryError

_log = structlog.get_logger(__name__)


@dataclass(frozen=True)
class FullTextSearchResult:
    """Result of a full-text search.

    Attributes:
        table: Arrow Table with original columns + '_score' relevance column.
        row_count: Number of results returned.
        query: The search query string used.
        top_k: Maximum number of results requested.
        fts_column: Text column that was searched.
        max_score: Highest relevance score in results, or None if empty.
    """

    table: pa.Table
    row_count: int
    query: str
    top_k: int
    fts_column: str
    max_score: float | None


class FullTextSearchBridge:
    """Bridges Lance datasets to LanceDB full-text search.

    Pipeline: query string -> LanceDB FTS -> Arrow Table with _score.

    Thread safety: safe for concurrent reads. NOT safe for concurrent
    index creation on the same dataset.

    Args:
        storage: LanceStorageManager instance.
        config: Full-text search configuration (None = use defaults).
    """

    def __init__(
        self,
        storage: Any,
        config: FullTextSearchConfig | None = None,
    ) -> None:
        self._storage = storage
        self._config = config or FullTextSearchConfig()

    def create_index(
        self,
        dataset_name: str,
        *,
        fts_column: str | None = None,
        replace: bool = True,
    ) -> None:
        """Create a full-text search index on a dataset.

        Uses LanceDB's lance-index FTS backend (use_tantivy=False).

        Args:
            dataset_name: Name of the Lance dataset.
            fts_column: Text column to index (None = use config default).
            replace: Whether to replace existing index.

        Raises:
            QueryError: If dataset not found, column not found, or index fails.
        """
        column = fts_column or self._config.fts_column

        table = self._storage.open_dataset(dataset_name)

        # Validate column exists and is a text type
        if column not in table.schema.names:
            raise QueryError(
                error_code=ErrorCode.FTS_INDEX_FAILED,
                message=f"Column '{column}' not found in dataset '{dataset_name}'",
            )

        field = table.schema.field(column)
        if not pa.types.is_string(field.type) and not pa.types.is_large_string(field.type):
            raise QueryError(
                error_code=ErrorCode.FTS_INDEX_FAILED,
                message=(
                    f"Column '{column}' is not a text column "
                    f"(type: {field.type}), cannot create FTS index"
                ),
            )

        try:
            table.create_fts_index(
                field_names=column,
                replace=replace,
                use_tantivy=False,
                stem=self._config.stem,
                remove_stop_words=self._config.remove_stop_words,
                lower_case=self._config.lower_case,
            )
        except Exception as exc:
            raise QueryError(
                error_code=ErrorCode.FTS_INDEX_FAILED,
                message=f"Failed to create FTS index on '{dataset_name}': {exc}",
            ) from exc

        _log.info(
            "FTS index created on column '%s' for dataset '%s'",
            column,
            dataset_name,
        )

    def search(
        self,
        dataset_name: str,
        query: str,
        *,
        top_k: int | None = None,
        fts_column: str | None = None,
        where: str | None = None,
    ) -> FullTextSearchResult:
        """Full-text search over a dataset.

        If no FTS index exists, LanceDB falls back to brute-force scan.

        Args:
            dataset_name: Name of the Lance dataset.
            query: Search query string.
            top_k: Number of results (None = use config default).
            fts_column: Text column to search (None = use config default).
            where: Optional metadata filter expression.

        Returns:
            FullTextSearchResult with Arrow table + _score relevance.
            Empty results return 0-row table (not error).

        Raises:
            QueryError: If dataset not found or search fails.
        """
        if not query or not query.strip():
            raise QueryError(
                error_code=ErrorCode.FTS_SEARCH_FAILED,
                message="Search query must not be empty",
            )

        # Validate where clause
        if where is not None:
            self._validate_where_clause(where)

        column = fts_column or self._config.fts_column
        effective_top_k = top_k if top_k is not None else self._config.default_top_k
        if effective_top_k < 1:
            raise QueryError(
                error_code=ErrorCode.FTS_SEARCH_FAILED,
                message=f"top_k must be >= 1, got {effective_top_k}",
            )

        table = self._storage.open_dataset(dataset_name)

        if column not in table.schema.names:
            raise QueryError(
                error_code=ErrorCode.FTS_SEARCH_FAILED,
                message=f"Column '{column}' not found in dataset '{dataset_name}'",
            )

        # Build FTS query
        query_builder = table.search(query=query, fts_columns=column)

        if where is not None:
            query_builder = query_builder.where(where)

        query_builder = query_builder.limit(effective_top_k)

        try:
            result_table = query_builder.to_arrow()
        except Exception as exc:
            raise QueryError(
                error_code=ErrorCode.FTS_SEARCH_FAILED,
                message=f"FTS search failed on '{dataset_name}': {exc}",
            ) from exc

        # Extract max score for diagnostics
        max_score: float | None = None
        if result_table.num_rows > 0 and "_score" in result_table.column_names:
            scores = result_table.column("_score").to_pylist()
            max_score = max(scores) if scores else None

        return FullTextSearchResult(
            table=result_table,
            row_count=result_table.num_rows,
            query=query,
            top_k=effective_top_k,
            fts_column=column,
            max_score=max_score,
        )

    @staticmethod
    def _validate_where_clause(where: str) -> None:
        """Validate where clause for dangerous SQL patterns.

        Raises:
            QueryError: If dangerous SQL keywords are detected.
        """
        from arrow_lake.query.vector import _DANGEROUS_KEYWORDS_RE

        match = _DANGEROUS_KEYWORDS_RE.search(where)
        if match:
            raise QueryError(
                error_code=ErrorCode.FTS_SEARCH_FAILED,
                message=(
                    f"Where clause contains dangerous SQL keyword: {match.group()!r}. "
                    f"Only SELECT-safe filter expressions are allowed."
                ),
            )
