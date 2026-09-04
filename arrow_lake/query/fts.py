"""Full-text search — Story 5.2.

ruff noqa: F821 (Any used in annotations with __future__ import)

Provides FullTextSearchBridge for full-text search over Lance datasets.
Uses LanceDB built-in FTS (BM25) with lance-index or tantivy backend.
Chinese/CJK text is pre-tokenized via jieba so lancedb's default
space-based tokenizer can index and search it correctly.
"""

from __future__ import annotations

import asyncio
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

        index_column = column

        # lancedb 0.36: tantivy-based FTS has been removed. Use native FTS via
        # ``FTS()`` config with ICU ``base_tokenizer`` — Unicode segmentation
        # provides native CJK/Chinese support, replacing the former
        # jieba pre-tokenization + tantivy path. Works on both local and
        # object storage (no local-fs restriction).
        from lancedb.index import FTS

        fts_cfg = FTS(
            base_tokenizer="icu",
            with_position=self._config.with_position,
            stem=self._config.stem,
            remove_stop_words=self._config.remove_stop_words,
            lower_case=self._config.lower_case,
        )
        try:
            table.create_index(index_column, config=fts_cfg, replace=replace)
        except (ValueError, RuntimeError) as exc:
            raise QueryError(
                error_code=ErrorCode.FTS_INDEX_FAILED,
                message=f"Failed to create FTS index on '{dataset_name}': {exc}",
            ) from exc

        _log.info(
            "FTS index created on column '%s' for dataset '%s' (icu native)",
            index_column,
            dataset_name,
        )

    # jieba pre-tokenization helpers (_has_null_segmented / _add_segmented_column)
    # removed in v1.9.7 — lancedb 0.36 native FTS (ICU base_tokenizer) segments
    # inline at index/search time, so no _fts_segmented column is needed.

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

        # lancedb 0.36 native FTS (ICU) tokenizes inline at query time — search
        # the original text column directly. No _fts_segmented column or jieba
        # query pre-segmentation is needed.
        search_column = column
        if search_column not in table.schema.names:
            raise QueryError(
                error_code=ErrorCode.FTS_SEARCH_FAILED,
                message=f"Column '{search_column}' not found in dataset '{dataset_name}'",
            )

        result_table = self._search_via_lancedb(
            table,
            query,
            effective_top_k,
            search_column,
            where,
            offset=offset,
        )

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

    async def search_async(
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
        """Async FTS — native AsyncTable FTS path (v1.11.5-W1-3, backlog #8).

        Runs the FTS query on lancedb's native async API (supported since
        0.33 — the old "AsyncTable lacks FTS" note was wrong) via the pooled
        ``AsyncTable`` (``async_conn_pool``): no worker-thread hop and no GIL
        contention under concurrent scans. ``version is not None`` still goes
        through sync :meth:`search` on a worker thread (the pool is not
        version-aware). Same params/return as :meth:`search`.
        """
        if version is not None:
            return await asyncio.to_thread(
                self.search,
                dataset_name,
                query,
                top_k=top_k,
                fts_column=fts_column,
                where=where,
                version=version,
                offset=offset,
            )

        if not query or not query.strip():
            raise QueryError(
                error_code=ErrorCode.FTS_SEARCH_FAILED,
                message="Search query must not be empty",
            )
        if where is not None:
            self._validate_where_clause(where)
        column = fts_column or self._config.fts_column
        effective_top_k = top_k if top_k is not None else self._config.default_top_k
        if effective_top_k < 1:
            raise QueryError(
                error_code=ErrorCode.FTS_SEARCH_FAILED,
                message=f"top_k must be >= 1, got {effective_top_k}",
            )

        base_uri = getattr(self._storage, "_connect_uri", None)
        if not base_uri:
            raise QueryError(
                error_code=ErrorCode.FTS_SEARCH_FAILED,
                message="async FTS requires storage._connect_uri",
            )

        from arrow_lake.query.async_conn_pool import get_async_table

        try:
            table = await get_async_table(
                base_uri,
                dataset_name,
                getattr(self._storage, "_storage_options", None),
            )
            schema = await table.schema()
            if column not in schema.names:
                raise QueryError(
                    error_code=ErrorCode.FTS_SEARCH_FAILED,
                    message=f"Column '{column}' not found in dataset '{dataset_name}'",
                )
            try:
                query_builder = await table.search(
                    query, query_type="fts", fts_columns=column
                )
            except ValueError:
                # Same disambiguation as the sync path: lancedb needs the
                # vector column named when the dataset also has one.
                for field in schema:
                    if pa.types.is_fixed_size_list(field.type) and pa.types.is_floating(
                        field.type.value_type
                    ):
                        query_builder = await table.search(
                            query,
                            query_type="fts",
                            fts_columns=column,
                            vector_column_name=field.name,
                        )
                        break
                else:
                    raise
            if where is not None:
                query_builder = query_builder.where(where)
            if offset > 0:
                query_builder = query_builder.offset(offset)
            query_builder = query_builder.limit(effective_top_k)
            result_table = await query_builder.to_arrow()
        except QueryError:
            raise
        except (ValueError, RuntimeError) as exc:
            raise QueryError(
                error_code=ErrorCode.FTS_SEARCH_FAILED,
                message=f"Async FTS search failed: {exc}",
            ) from exc

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
