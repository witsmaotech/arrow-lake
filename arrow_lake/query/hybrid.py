"""Hybrid search — Story 5.3.

ruff noqa: F821 (Any used in annotations with __future__ import)

Provides HybridSearchBridge for RRF-fused hybrid search over Lance datasets.
Manually runs vector + FTS searches separately and fuses results using
Reciprocal Rank Fusion (RRF).
"""

from __future__ import annotations

import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

import duckdb
import pyarrow as pa
import structlog

from arrow_lake.config import HybridSearchConfig, StorageConfig
from arrow_lake.exceptions import ErrorCode, QueryError

_log = structlog.get_logger(__name__)


@dataclass(frozen=True)
class HybridSearchResult:
    """Result of a hybrid search (RRF-fused vector + FTS).

    Attributes:
        table: Arrow Table with original columns + '_rrf_score' column.
        row_count: Number of results returned.
        query_text: The FTS query string used.
        query_vector_dim: Dimensionality of the query vector.
        top_k: Maximum number of results requested.
        rrf_k: RRF constant used for fusion.
        max_rrf_score: Highest RRF score in results, or None if empty.
    """

    table: pa.Table
    row_count: int
    query_text: str
    query_vector_dim: int
    top_k: int
    rrf_k: int
    max_rrf_score: float | None


class HybridSearchBridge:
    """Bridges Lance datasets to hybrid vector + FTS search via RRF.

    Pipeline:
      1. Vector search → candidate table with _distance
      2. FTS search → candidate table with _score
      3. RRF fusion by id → ranked table with _rrf_score

    Thread safety: safe for concurrent reads. NOT safe for concurrent
    index creation on the same dataset.

    Args:
        storage: LanceStorageManager instance.
        config: Hybrid search configuration (None = use defaults).
    """

    def __init__(
        self,
        storage: Any,
        config: HybridSearchConfig | None = None,
        storage_config: StorageConfig | None = None,
        lance_scan_mode: str = "auto",
        session_manager: Any = None,
    ) -> None:
        self._storage = storage
        self._storage_config = storage_config
        self._lance_scan_mode = lance_scan_mode
        self._config = config or HybridSearchConfig()
        self._session_manager = session_manager
        self._vector_bridge: Any | None = None
        self._fts_bridge: Any | None = None
        self._reranker: Any = None  # 懒加载（v1.8.0 #5 cross-encoder 精排）

    def search(
        self,
        dataset_name: str,
        query_vector: list[float],
        query_text: str,
        *,
        top_k: int | None = None,
        vector_column: str = "text_embedding",
        fts_column: str | None = None,
        where: str | None = None,
        version: int | None = None,
    ) -> HybridSearchResult:
        """Hybrid search using RRF fusion of vector + FTS results.

        Runs vector and FTS searches separately, then fuses results
        using Reciprocal Rank Fusion.

        Args:
            dataset_name: Name of the Lance dataset.
            query_vector: Query embedding vector.
            query_text: Search query string for FTS.
            top_k: Number of final results (None = use config default).
            vector_column: Name of the vector column.
            fts_column: Text column to search (None = use config default).
            where: Optional metadata filter expression.

        Returns:
            HybridSearchResult with Arrow table and _rrf_score.

        Raises:
            QueryError: If inputs are invalid or search fails.
        """
        if not query_vector:
            raise QueryError(
                error_code=ErrorCode.HYBRID_SEARCH_FAILED,
                message="Query vector must not be empty",
            )

        if not query_text or not query_text.strip():
            raise QueryError(
                error_code=ErrorCode.HYBRID_SEARCH_FAILED,
                message="Query text must not be empty",
            )

        if where is not None:
            self._validate_where_clause(where)

        effective_top_k = top_k if top_k is not None else self._config.default_top_k
        if effective_top_k < 1:
            raise QueryError(
                error_code=ErrorCode.HYBRID_SEARCH_FAILED,
                message=f"top_k must be >= 1, got {effective_top_k}",
            )

        vector_top_k = effective_top_k * self._config.vector_top_k_multiplier
        fts_top_k = effective_top_k * self._config.fts_top_k_multiplier

        # Try DuckDB native hybrid search first
        if (
            self._lance_scan_mode != "pyarrow_fallback"
            and hasattr(self._storage, "dataset_uri")
            and where is None
        ):
            try:
                result_table = self._search_via_duckdb(
                    dataset_name,
                    query_vector,
                    query_text,
                    vector_column=vector_column,
                    fts_column=fts_column,
                    top_k=effective_top_k,
                )
            except QueryError:
                raise
            except (duckdb.Error, OSError):
                _log.debug(
                    "DuckDB hybrid search failed, falling back to sub-bridge RRF",
                    exc_info=True,
                )
                result_table = self._search_via_sub_bridges(
                    dataset_name,
                    query_vector,
                    query_text,
                    vector_top_k,
                    fts_top_k,
                    vector_column,
                    fts_column,
                    where,
                    effective_top_k,
                    version=version,
                )
        else:
            result_table = self._search_via_sub_bridges(
                dataset_name,
                query_vector,
                query_text,
                vector_top_k,
                fts_top_k,
                vector_column,
                fts_column,
                where,
                effective_top_k,
                version=version,
            )

        # Extract scores for result
        max_rrf_score: float | None = None
        if result_table.num_rows > 0 and "_rrf_score" in result_table.column_names:
            max_val = pa.compute.max(result_table.column("_rrf_score"))
            max_rrf_score = max_val.as_py() if max_val.is_valid else None

        # Rerank (optional, v1.8.0 #5): cross-encoder 精排 RRF 粗排结果
        if self._config.reranker_type and self._config.reranker_type != "none":
            result_table = self._rerank_table(
                result_table, query_text, fts_column or "text_content", effective_top_k
            )

        return HybridSearchResult(
            table=result_table,
            row_count=result_table.num_rows,
            query_text=query_text,
            query_vector_dim=len(query_vector),
            top_k=effective_top_k,
            rrf_k=self._config.rrf_k,
            max_rrf_score=max_rrf_score,
        )

    async def search_async(
        self,
        dataset_name: str,
        query_vector: list[float],
        query_text: str,
        *,
        top_k: int | None = None,
        vector_column: str = "text_embedding",
        fts_column: str | None = None,
        where: str | None = None,
        version: int | None = None,
    ) -> HybridSearchResult:
        """Async hybrid search — non-blocking wrapper (v1.8.0 #17).

        Delegates the sync :meth:`search` to a worker thread via
        ``asyncio.to_thread`` so async handlers don't block the event loop on
        a long RRF + rerank pass. lancedb has no native async FTS path
        (``AsyncTable`` lacks FTS), so the sub-bridge fusion can't be made
        GIL-free like ``VectorSearchBridge.search_async`` — the value here is a
        non-blocking async interface for the FastAPI layer under concurrent
        mixed workloads. Same params/return as :meth:`search`.
        """
        return await asyncio.to_thread(
            self.search,
            dataset_name,
            query_vector,
            query_text,
            top_k=top_k,
            vector_column=vector_column,
            fts_column=fts_column,
            where=where,
            version=version,
        )

    def _rerank_table(
        self,
        table: pa.Table,
        query_text: str,
        text_column: str,
        top_k: int,
    ) -> pa.Table:
        """Rerank a result table via configured cross-encoder (v1.8.0 #5).

        Converts rows to ``ContextChunk``, reranks, reorders the table, and
        appends a ``_rerank_score`` column. Returns the original table unchanged
        if the text column is missing or reranking fails (graceful degradation).
        """
        if table.num_rows == 0 or text_column not in table.column_names:
            return table

        from arrow_lake.rag.context import ContextChunk
        from arrow_lake.rag.reranker import create_reranker

        if self._reranker is None:
            self._reranker = create_reranker(
                self._config.reranker_type,
                model_name=self._config.reranker_model,
            )

        texts = table.column(text_column).to_pylist()
        rrf_scores = (
            table.column("_rrf_score").to_pylist()
            if "_rrf_score" in table.column_names
            else [0.0] * len(texts)
        )
        chunks = [
            ContextChunk(
                text=str(t) if t is not None else "",
                dataset="",
                row_id=str(i),
                score=float(s),
                metadata={"row_idx": i},
            )
            for i, (t, s) in enumerate(zip(texts, rrf_scores, strict=True))
        ]

        try:
            reranked = self._reranker.rerank(query_text, chunks, top_k)
        except Exception:
            logger.warning("Rerank failed, returning original order", exc_info=True)
            return table

        if not reranked:
            return table

        order = [int(c.metadata.get("row_idx", i)) for i, c in enumerate(reranked)]
        rerank_scores = [float(c.score) for c in reranked]
        return table.take(order).append_column(
            "_rerank_score", pa.array(rerank_scores, type=pa.float32())
        )

    def _search_via_duckdb(
        self,
        dataset_name: str,
        query_vector: list[float],
        query_text: str,
        *,
        vector_column: str,
        fts_column: str | None = None,
        top_k: int,
    ) -> pa.Table:
        """Search via DuckDB lance_hybrid_search() SQL function.

        Args:
            dataset_name: Lance dataset name.
            query_vector: Query embedding vector.
            query_text: Search query string.
            vector_column: Vector column name.
            fts_column: Full-text search column name.
            top_k: Number of results.

        Returns:
            Arrow Table with search results and _rrf_score column.

        Raises:
            QueryError: If DuckDB search fails.
        """
        from arrow_lake.query._db import create_duckdb_session

        if not hasattr(self._storage, "dataset_uri"):
            raise QueryError(
                error_code=ErrorCode.HYBRID_SEARCH_FAILED,
                message="storage.dataset_uri() not available for DuckDB native hybrid",
            )

        uri = self._storage.dataset_uri(dataset_name)

        from arrow_lake.validation import escape_sql_literal, validate_identifier

        safe_uri = escape_sql_literal(uri)
        validate_identifier(vector_column)
        if fts_column is not None:
            validate_identifier(fts_column)

        vec_list = "[" + ", ".join(str(v) for v in query_vector) + "]"
        safe_text = escape_sql_literal(query_text)
        safe_fts = fts_column if fts_column else vector_column

        from arrow_lake.config.search import VectorSearchConfig

        vec_cfg = VectorSearchConfig()

        sql = (
            f"SELECT * FROM lance_hybrid_search("  # nosec B608
            f"  '{safe_uri}',"
            f"  '{vector_column}',"
            f"  CAST({vec_list} AS FLOAT[]),"
            f"  '{safe_fts}',"
            f"  '{safe_text}',"
            f"  alpha := 0.5,"
            f"  use_index := false,"
            f"  prefilter := false,"
            f"  refine_factor := {vec_cfg.refine_factor}::BIGINT,"
            f"  nprobs := {vec_cfg.nprobes}::BIGINT,"
            f"  oversample_factor := 1,"
            f"  k := {top_k}::BIGINT"
            f") LIMIT {top_k}"
        )

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
            with create_duckdb_session(storage_config=self._storage_config) as conn:
                reader = conn.execute(sql).arrow()
                if hasattr(reader, "read_all"):
                    return reader.read_all()
                return reader

    def _search_via_sub_bridges(
        self,
        dataset_name: str,
        query_vector: list[float],
        query_text: str,
        vector_top_k: int,
        fts_top_k: int,
        vector_column: str,
        fts_column: str | None,
        where: str | None,
        effective_top_k: int,
        *,
        version: int | None = None,
    ) -> pa.Table:
        """Search via VectorSearchBridge + FullTextSearchBridge + RRF fusion."""
        from arrow_lake.query.fts import FullTextSearchBridge
        from arrow_lake.query.vector import VectorSearchBridge

        if self._vector_bridge is None:
            self._vector_bridge = VectorSearchBridge(
                self._storage,
                storage_config=self._storage_config,
                lance_scan_mode=self._lance_scan_mode,
            )
        if self._fts_bridge is None:
            self._fts_bridge = FullTextSearchBridge(
                self._storage,
                storage_config=self._storage_config,
                lance_scan_mode=self._lance_scan_mode,
            )
        vector_bridge = self._vector_bridge
        fts_bridge = self._fts_bridge

        with ThreadPoolExecutor(max_workers=2) as pool:
            v_future = pool.submit(
                vector_bridge.search,
                dataset_name, query_vector,
                top_k=vector_top_k, vector_column=vector_column,
                where=where, version=version,
            )
            f_future = pool.submit(
                fts_bridge.search,
                dataset_name, query_text,
                top_k=fts_top_k, fts_column=fts_column,
                version=version, where=where,
            )

            vector_result = None
            fts_result = None
            errors: list[str] = []

            try:
                vector_result = v_future.result()
            except (QueryError, ValueError, RuntimeError) as exc:
                logger.warning("Vector search failed in hybrid, degrading: %s", exc)
                errors.append(f"vector: {exc}")

            try:
                fts_result = f_future.result()
            except (QueryError, ValueError, RuntimeError) as exc:
                logger.warning("FTS search failed in hybrid, degrading: %s", exc)
                errors.append(f"fts: {exc}")

        if vector_result is None and fts_result is None:
            raise QueryError(
                error_code=ErrorCode.HYBRID_SEARCH_FAILED,
                message=f"Both vector and FTS search failed on '{dataset_name}': {'; '.join(errors)}",
            )

        # Degraded mode: single result, skip RRF fusion
        if vector_result is None or fts_result is None:
            logger.info("Hybrid search degraded to single mode: %s", errors)
            single = vector_result if vector_result is not None else fts_result
            return single.table

        return self._rrf_fuse(
            vector_result.table,
            fts_result.table,
            k=self._config.rrf_k,
            top_k=effective_top_k,
        )

    @staticmethod
    def _rrf_fuse(
        vector_table: pa.Table,
        fts_table: pa.Table,
        k: int,
        top_k: int,
    ) -> pa.Table:
        """Fuse vector and FTS results using Reciprocal Rank Fusion.

        RRF formula: score(doc) = Σ 1/(rank(doc, list_i) + k)

        Deduplicates by 'id' column and returns top_k rows sorted
        by descending RRF score.

        Args:
            vector_table: Vector search results (may have _distance column).
            fts_table: FTS search results (may have _score column).
            k: RRF constant.
            top_k: Maximum results to return.

        Returns:
            Arrow Table with _rrf_score column, sorted by descending score.
        """
        from collections import defaultdict

        rrf_scores: dict[str, float] = defaultdict(float)

        # Calculate RRF scores from vector results
        # RRF formula: score = 1/(rank + k), rank starts at 1 (Cormack et al., 2009)
        if vector_table.num_rows > 0 and "id" in vector_table.column_names:
            ids = vector_table.column("id").to_pylist()
            for rank, doc_id in enumerate(ids, start=1):
                rrf_scores[str(doc_id)] += 1.0 / (rank + k)
            del ids

        # Calculate RRF scores from FTS results
        if fts_table.num_rows > 0 and "id" in fts_table.column_names:
            ids = fts_table.column("id").to_pylist()
            for rank, doc_id in enumerate(ids, start=1):
                rrf_scores[str(doc_id)] += 1.0 / (rank + k)
            del ids

        if not rrf_scores:
            return pa.table({"_rrf_score": []})

        # Sort by descending RRF score, tiebreak by doc_id for determinism
        sorted_ids = sorted(
            rrf_scores.keys(),
            key=lambda doc_id: (-rrf_scores[doc_id], doc_id),
        )[:top_k]

        # Build a lookup table of id -> row index from concatenated tables
        # Use vector_table as primary source, fall back to fts_table
        _score_cols = frozenset({"_distance", "_score", "_rrf_score"})

        def _extract_by_ids(table: pa.Table, wanted: set[str]) -> pa.Table | None:
            if table.num_rows == 0 or "id" not in table.column_names:
                return None
            id_col = table.column("id").to_pylist()
            id_to_idx: dict[str, int] = {}
            for i, doc_id in enumerate(id_col):
                s = str(doc_id)
                if s in wanted and s not in id_to_idx:
                    id_to_idx[s] = i
            del id_col
            if not id_to_idx:
                return None
            indices = sorted(id_to_idx.values())
            data_cols = [c for c in table.column_names if c not in _score_cols]
            taken = table.select(data_cols).take(indices)
            return taken

        wanted = set(sorted_ids)
        result = _extract_by_ids(vector_table, wanted)
        if result is None:
            result = _extract_by_ids(fts_table, wanted)
            if result is None:
                return pa.table({"_rrf_score": []})
        if result is not None:
            wanted -= set(str(v) for v in result.column("id").to_pylist())

        if wanted:
            fts_part = _extract_by_ids(fts_table, wanted)
            if fts_part is not None:
                common_cols = list(set(result.column_names) & set(fts_part.column_names))
                result = pa.concat_tables([result.select(common_cols), fts_part.select(common_cols)])

        # Add _rrf_score column
        result_ids = [str(v) for v in result.column("id").to_pylist()]
        score_values = [rrf_scores[did] for did in result_ids]
        result = result.append_column("_rrf_score", pa.array(score_values, type=pa.float32()))

        # Sort by descending _rrf_score
        sort_indices = pa.compute.sort_indices(
            result.column("_rrf_score"),
            sort_keys=[("direction", "descending")],
        )
        result = result.take(sort_indices.to_pylist())

        if result.num_rows > top_k:
            result = result.slice(0, top_k)

        return result

    @staticmethod
    def _validate_where_clause(where: str) -> None:
        """Validate where clause for dangerous SQL patterns.

        Raises:
            QueryError: If dangerous SQL keywords are detected.
        """
        from arrow_lake.exceptions import ErrorCode, QueryError
        from arrow_lake.validation import validate_where_clause

        try:
            validate_where_clause(where)
        except ValueError as exc:
            raise QueryError(
                error_code=ErrorCode.HYBRID_SEARCH_FAILED,
                message=str(exc),
            ) from exc
