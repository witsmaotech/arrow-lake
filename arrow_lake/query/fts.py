"""Full-text search — Story 5.2.

ruff noqa: F821 (Any used in annotations with __future__ import)

Provides FullTextSearchBridge for full-text search over Lance datasets.
Uses LanceDB built-in FTS (BM25) with lance-index or tantivy backend.
Chinese/CJK text is pre-tokenized via jieba so lancedb's default
space-based tokenizer can index and search it correctly.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pyarrow as pa
import pyarrow.compute as pc
import structlog

from arrow_lake.config import FullTextSearchConfig, StorageConfig
from arrow_lake.config._enums import StorageBackend
from arrow_lake.exceptions import ErrorCode, QueryError
from arrow_lake.query._chinese_tokenizer import (
    _JIEBA_AVAILABLE,
    has_cjk,
    segment_query,
    segment_text,
)

_log = structlog.get_logger(__name__)

try:
    import tantivy  # noqa: F401

    _TANTIVY_AVAILABLE = True
except ImportError:
    _TANTIVY_AVAILABLE = False


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

    Pipeline: query string -> (jieba segment) -> LanceDB FTS -> Arrow Table with _score.

    When ``tokenizer_type == "jieba"`` (default):
    - ``create_index`` adds a ``_fts_segmented`` column with jieba-segmented text
      and indexes that column instead of the raw text.
    - ``search`` segments the query string via jieba before passing to LanceDB.

    Thread safety: safe for concurrent reads. NOT safe for concurrent
    index creation on the same dataset.
    """

    def __init__(
        self,
        storage: Any,
        config: FullTextSearchConfig | None = None,
        storage_config: StorageConfig | None = None,
        lance_scan_mode: str = "auto",
        session_manager: Any = None,
    ) -> None:
        self._storage = storage
        self._config = config or FullTextSearchConfig()
        self._storage_config = storage_config
        self._lance_scan_mode = lance_scan_mode
        self._session_manager = session_manager
        self._use_jieba = self._config.tokenizer_type == "jieba"

    # ------------------------------------------------------------------
    # Index
    # ------------------------------------------------------------------

    def create_index(
        self,
        dataset_name: str,
        *,
        fts_column: str | None = None,
        replace: bool = True,
    ) -> None:
        """Create a full-text search index on a dataset.

        When jieba tokenization is enabled (default), adds a
        ``_fts_segmented`` column containing space-separated tokens
        and indexes that column.

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

        if not _TANTIVY_AVAILABLE:
            _log.warning(
                "tantivy not installed — FTS index will use lance-index backend "
                "(DuckDB lance_fts only). Install with: pip install tantivy"
            )

        # Detect CJK content without jieba
        if self._use_jieba and not _JIEBA_AVAILABLE:
            sample = table.to_table().column(column).to_pylist()[:100]
            if any(has_cjk(str(t)) for t in sample if t):
                _log.warning(
                    "CJK content detected but jieba is not installed — "
                    "Chinese text will NOT be tokenized correctly. "
                    "Install with: pip install jieba"
                )

        index_column = column
        if self._use_jieba:
            # Load jieba user dict if configured
            if self._config.jieba_user_dict:
                from pathlib import Path

                dict_path = Path(self._config.jieba_user_dict)
                if not dict_path.is_file():
                    raise QueryError(
                        error_code=ErrorCode.FTS_INDEX_FAILED,
                        message=(
                            f"jieba user dict not found: {self._config.jieba_user_dict}"
                        ),
                    )
                try:
                    import jieba as _jieba

                    _jieba.load_userdict(str(dict_path))
                except QueryError:
                    raise
                except (ValueError, OSError, RuntimeError) as exc:
                    raise QueryError(
                        error_code=ErrorCode.FTS_INDEX_FAILED,
                        message=f"Failed to load jieba user dict: {exc}",
                    ) from exc

            index_column = self._add_segmented_column(table, column, dataset_name)
            table = self._storage.open_dataset(dataset_name)

        # tantivy only supports local filesystem — auto-disable for S3/MinIO/GCS
        is_local = (
            self._storage_config is not None
            and getattr(self._storage_config, "backend", None) == StorageBackend.LOCAL
        )
        use_tantivy = _TANTIVY_AVAILABLE and is_local
        if _TANTIVY_AVAILABLE and not is_local:
            _log.info(
                "tantivy requires local filesystem — using lance-index backend "
                "for %s storage",
                getattr(self._storage_config, "backend", "remote"),
            )

        fts_kwargs: dict[str, Any] = dict(
            field_names=index_column,
            replace=replace,
            use_tantivy=use_tantivy,
            stem=self._config.stem,
            remove_stop_words=self._config.remove_stop_words,
            lower_case=self._config.lower_case,
        )
        if use_tantivy:
            fts_kwargs["language"] = "Chinese"

        try:
            table.create_fts_index(**fts_kwargs)
        except (ValueError, RuntimeError) as exc:
            raise QueryError(
                error_code=ErrorCode.FTS_INDEX_FAILED,
                message=f"Failed to create FTS index on '{dataset_name}': {exc}",
            ) from exc

        seg_note = f" (jieba-segmented column '{index_column}')" if self._use_jieba else ""
        _log.info(
            "FTS index created on column '%s' for dataset '%s'%s",
            index_column,
            dataset_name,
            seg_note,
        )

    def _add_segmented_column(
        self,
        table: Any,
        source_column: str,
        dataset_name: str,
    ) -> str:
        """Add a _fts_segmented column with jieba-segmented text.

        Reads only the source column in batches (not the full table),
        builds the segmented array, then uses lance's add_columns(pa.Table)
        to append the new column without a full dataset rewrite.

        If the column already exists (from a previous index creation), it is
        dropped first so that ``replace=True`` works correctly.

        Returns the name of the new column.
        """
        import lance

        segmented_column = "_fts_segmented"
        uri = self._storage.dataset_uri(dataset_name)
        opts = self._storage.storage_options

        ds = lance.dataset(uri, storage_options=opts)

        # Drop existing segmented column if present (needed for replace)
        if segmented_column in ds.schema.names:
            _log.info(
                "Dropping existing segmented column '%s' for replace",
                segmented_column,
            )
            ds.drop_columns([segmented_column])
            # Re-open to pick up schema change
            ds = lance.dataset(uri, storage_options=opts)

        row_count = ds.count_rows()
        _log.info("Segmenting column '%s' for %d rows", source_column, row_count)

        # Read only the source column in batches to limit memory usage
        segmented_values: list[str | None] = []
        for batch in ds.to_batches(columns=[source_column]):
            texts = batch.column(source_column).to_pylist()
            segmented_values.extend(
                None if t is None else segment_text(str(t))
                for t in texts
            )

        new_col = pa.array(segmented_values, type=pa.string())
        col_table = pa.table({segmented_column: new_col})
        ds.add_columns(col_table)
        del segmented_values, new_col, col_table

        _log.info(
            "Added jieba-segmented column '%s' to dataset '%s' (%d rows)",
            segmented_column,
            dataset_name,
            row_count,
        )
        return segmented_column

    # ------------------------------------------------------------------
    # Search
    # ------------------------------------------------------------------

    def search(
        self,
        dataset_name: str,
        query: str,
        *,
        top_k: int | None = None,
        fts_column: str | None = None,
        where: str | None = None,
        version: int | None = None,
        offset: int = 0,
    ) -> FullTextSearchResult:
        """Full-text search over a dataset.

        If no FTS index exists, LanceDB falls back to brute-force scan.

        Args:
            dataset_name: Name of the Lance dataset.
            query: Search query string.
            top_k: Number of results (None = use config default).
            fts_column: Text column to search (None = use config default).
            where: Optional metadata filter expression.
            offset: Number of results to skip (pagination).

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

        table = self._storage.open_dataset_versioned(dataset_name, version) if version else self._storage.open_dataset(dataset_name)

        # Determine which column was indexed
        search_column = column
        if self._use_jieba and "_fts_segmented" in table.schema.names:
            search_column = "_fts_segmented"

        if search_column not in table.schema.names:
            raise QueryError(
                error_code=ErrorCode.FTS_SEARCH_FAILED,
                message=f"Column '{search_column}' not found in dataset '{dataset_name}'",
            )

        # Segment query for Chinese tokenization
        effective_query = segment_query(query) if self._use_jieba else query

        # Prefer LanceDB SDK path for FTS
        result_table = self._search_via_lancedb(
            table,
            effective_query,
            effective_top_k,
            search_column,
            where,
            offset=offset,
        )

        # Remove _fts_segmented from result if present — caller shouldn't see it
        if "_fts_segmented" in result_table.column_names:
            cols_to_keep = [c for c in result_table.column_names if c != "_fts_segmented"]
            result_table = result_table.select(cols_to_keep)

        # Extract max score for diagnostics
        max_score: float | None = None
        if result_table.num_rows > 0 and "_score" in result_table.column_names:
            max_val = pc.max(result_table.column("_score"))
            max_score = max_val.as_py() if max_val.is_valid else None

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
        from arrow_lake.validation import validate_where_clause

        try:
            validate_where_clause(where)
        except ValueError as exc:
            raise QueryError(
                error_code=ErrorCode.FTS_SEARCH_FAILED,
                message=str(exc),
            ) from exc

    def _search_via_lancedb(
        self,
        table: Any,
        query: str,
        top_k: int,
        fts_column: str,
        where: str | None,
        *,
        offset: int = 0,
    ) -> pa.Table:
        """Search via LanceDB SDK (original path)."""
        try:
            query_builder = table.search(query=query, query_type="fts", fts_columns=fts_column)
        except ValueError:
            schema = table.schema
            for field in schema:
                if pa.types.is_fixed_size_list(field.type) and pa.types.is_floating(
                    field.type.value_type
                ):
                    query_builder = table.search(
                        query=query,
                        query_type="fts",
                        fts_columns=fts_column,
                        vector_column_name=field.name,
                    )
                    break
            else:
                raise

        if where is not None:
            query_builder = query_builder.where(where)

        if offset > 0:
            query_builder = query_builder.offset(offset)
        query_builder = query_builder.limit(top_k)

        try:
            return query_builder.to_arrow()
        except (ValueError, RuntimeError) as exc:
            raise QueryError(
                error_code=ErrorCode.FTS_SEARCH_FAILED,
                message=f"FTS search failed: {exc}",
            ) from exc
