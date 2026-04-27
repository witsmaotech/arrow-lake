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
import structlog

from arrow_lake.config import FullTextSearchConfig, StorageConfig
from arrow_lake.exceptions import ErrorCode, QueryError
from arrow_lake.query._chinese_tokenizer import segment_query, segment_text

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

        fts_kwargs: dict[str, Any] = dict(
            field_names=index_column,
            replace=replace,
            use_tantivy=_TANTIVY_AVAILABLE,
            stem=self._config.stem,
            remove_stop_words=self._config.remove_stop_words,
            lower_case=self._config.lower_case,
        )
        if _TANTIVY_AVAILABLE:
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

        Reads the Lance dataset via lance.dataset, appends a segmented
        column, and writes it back via lance.write_dataset(overwrite).

        Returns the name of the new column.
        """
        import lance

        segmented_column = "_fts_segmented"
        uri = table.uri

        # Read existing data and segment in chunks to limit memory usage
        ds = lance.dataset(uri)
        row_count = ds.count_rows()
        _chunk_size = 50_000

        if row_count <= _chunk_size:
            original = ds.to_table()
            raw_texts = original.column(source_column).to_pylist()
            segmented = [
                None if text is None else segment_text(str(text))
                for text in raw_texts
            ]
            new_col = pa.array(segmented, type=pa.string())
            new_table = original.append_column(segmented_column, new_col)
            lance.write_dataset(new_table, uri, mode="overwrite")
        else:
            # Chunked: process in batches, merge via Lance append
            _log.info(
                "Large dataset (%d rows): segmenting in chunks of %d",
                row_count, _chunk_size,
            )

            first_chunk = True
            for offset in range(0, row_count, _chunk_size):
                batch = ds.to_table(
                    columns=[source_column],
                    offset=offset,
                    limit=_chunk_size,
                )
                raw_texts = batch.column(source_column).to_pylist()
                segmented = [
                    None if text is None else segment_text(str(text))
                    for text in raw_texts
                ]
                new_col = pa.array(segmented, type=pa.string())
                chunk_table = batch.append_column(segmented_column, new_col)
                if first_chunk:
                    lance.write_dataset(chunk_table, uri, mode="overwrite")
                    first_chunk = False
                else:
                    lance.write_dataset(chunk_table, uri, mode="append")
            # Reopen to validate
            ds = lance.dataset(uri)

        _log.info(
            "Added jieba-segmented column '%s' to dataset '%s' (%d rows)",
            segmented_column,
            dataset_name,
            len(segmented),
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
        )

        # Remove _fts_segmented from result if present — caller shouldn't see it
        if "_fts_segmented" in result_table.column_names:
            cols_to_keep = [c for c in result_table.column_names if c != "_fts_segmented"]
            result_table = result_table.select(cols_to_keep)

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

        query_builder = query_builder.limit(top_k)

        try:
            return query_builder.to_arrow()
        except (ValueError, RuntimeError) as exc:
            raise QueryError(
                error_code=ErrorCode.FTS_SEARCH_FAILED,
                message=f"FTS search failed: {exc}",
            ) from exc
