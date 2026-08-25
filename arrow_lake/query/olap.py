"""OLAP analytics — Story 5.4, 7.6.

ruff noqa: F821 (Any used in annotations with __future__ import)

Provides OlapSearchBridge for SQL analytics queries over Lance datasets.
Uses DuckDB with zero-copy Arrow integration for GROUP BY, aggregation,
window functions, JOIN, and other OLAP operations.

Story 7.6 additions:
- Multi-table registration for JOIN queries
- enable_join config flag for security control
- to_arrow() convenience method on OlapQueryResult

M0b migration:
- DuckDBSession → create_duckdb_session() (extension loading + resource governance)
- LanceScanAdapter.create_view() for native lance scan
- Backward-compatible PyArrow fallback via conn.register()

Note: Daft expression-based queries are available via Lake.daft_query().
Daft 0.7.8 does not support SQL.
"""

from __future__ import annotations

import contextlib
import logging
import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import duckdb
import pyarrow as pa

from arrow_lake.config import OlapConfig, StorageBackend, StorageConfig
from arrow_lake.exceptions import ErrorCode, QueryError, StorageError
from arrow_lake.query._db import create_duckdb_session
from arrow_lake.query.lance_adapter import create_lance_scan_adapter
from arrow_lake.query.scan_breaker import LanceScanBreaker
from arrow_lake.query.session_manager import run_duckdb_interruptible
from arrow_lake.validation import (
    DANGEROUS_SQL_KEYWORDS_RE,
    validate_identifier,
)

if TYPE_CHECKING:
    from arrow_lake.query._cache import QueryCache as _QueryCache

_JOIN_KEYWORD_RE = re.compile(
    r"\b(INNER\s+JOIN|LEFT\s+JOIN|RIGHT\s+JOIN|FULL\s+JOIN|CROSS\s+JOIN|"
    r"JOIN|NATURAL\s+JOIN)\b",
    re.IGNORECASE,
)

logger = logging.getLogger(__name__)

# Dedupe the vector-override-ignored warning to once per (worker, dataset) so a
# misconfigured high-traffic vector dataset doesn't spam the log every query.
_warned_vector_override: set[str] = set()


@dataclass(frozen=True)
class OlapQueryResult:
    """Result of an OLAP analytics query.

    Attributes:
        table: Arrow Table with query results.
        row_count: Number of rows in the result.
        column_count: Number of columns in the result.
        sql: The SQL query that was executed.
    """

    table: pa.Table
    row_count: int
    column_count: int
    sql: str

    def to_arrow(self) -> pa.Table:
        """Return the result as a PyArrow Table (zero-copy alias)."""
        return self.table


class OlapSearchBridge:
    """Bridges Lance datasets to DuckDB for OLAP analytics queries.

    Pipeline: Lance → (native scan | Arrow) → DuckDB → SQL → Arrow result.

    Supports GROUP BY, aggregation functions, window functions, HAVING,
    ORDER BY, and LIMIT.

    Thread safety: safe for concurrent reads (each query creates its own
    DuckDB connection).

    Args:
        storage: LanceStorageManager instance.
        config: OLAP analytics configuration (None = use defaults).
        storage_config: Storage configuration for S3 access (None = local).
    """

    def __init__(
        self,
        storage: Any,
        config: OlapConfig | None = None,
        storage_config: StorageConfig | None = None,
        session_manager: Any = None,
    ) -> None:
        self._storage = storage
        self._config = config or OlapConfig()
        self._storage_config = storage_config
        self._session_manager = session_manager
        self._cache: _QueryCache | None = None
        if self._config.query_cache_enabled:
            from arrow_lake.query._cache import QueryCache as _QC

            self._cache = _QC(
                max_entries=self._config.query_cache_max_entries,
                ttl_seconds=self._config.query_cache_ttl_seconds,
            )
        # v1.10.4: per-dataset native-scan breaker (bridge is a cached singleton,
        # so one shared breaker spans all queries). Fail-open — constructed even
        # when Redis is absent (the lazy client returns None → no tripping).
        self._breaker = LanceScanBreaker(
            threshold=self._config.lance_breaker_trip_threshold,
            window_s=self._config.lance_breaker_window_seconds,
            cooldown_s=self._config.lance_breaker_cooldown_seconds,
        )

    def query(
        self,
        dataset_name: str,
        sql: str,
        *,
        max_rows: int | None = None,
        tables: dict[str, pa.Table] | None = None,
    ) -> OlapQueryResult:
        """Execute an OLAP SQL query against a Lance dataset.

        Args:
            dataset_name: Name of the Lance dataset to query.
            sql: SQL query string (must be SELECT only).
            max_rows: Maximum result rows (None = use config default).
            tables: Additional Arrow tables to register for JOIN queries.
                    Keys are table names (must match _SAFE_IDENTIFIER_RE).

        Returns:
            OlapQueryResult with Arrow table and metadata.

        Raises:
            QueryError: If SQL is invalid, dataset not found, or query fails.
            ValueError: If dataset name or table name is invalid.
        """
        _validate_dataset_name(dataset_name)
        self._validate_sql(sql)
        source_ds, source_table = _split_two_part(dataset_name)
        if source_table is None:
            self._reject_bare_container(dataset_name)

        # Validate extra table names
        if tables:
            for name in tables:
                validate_identifier(name)

        # Check cache
        limited_sql = self._apply_limit(sql, max_rows if max_rows is not None else self._config.max_result_rows)
        if self._cache is not None:
            cached = self._cache.get(dataset_name, limited_sql, tables)
            if cached is not None:
                return OlapQueryResult(
                    table=cached,
                    row_count=cached.num_rows,
                    column_count=cached.num_columns,
                    sql=limited_sql,
                )


        # Determine if streaming is safe (RecordBatchReader is single-use,
        # so queries that reference the same table multiple times need
        # a full materialized Table that can be scanned repeatedly).
        stripped_sql = sql.strip().upper()
        use_streaming = (
            self._config.enable_streaming
            and hasattr(self._storage, "scan_dataset")
            and not _JOIN_KEYWORD_RE.search(stripped_sql)
            and stripped_sql.count("SELECT") == 1
        )

        session = self._managed_session()

        # Read dataset from Lance
        try:
            if use_streaming:
                source = self._storage.scan_dataset(
                    source_ds,
                    batch_size=self._config.scanner_batch_size,
                    table=source_table,
                )
            else:
                source = self._storage.read_dataset(source_ds, table=source_table)
        except (StorageError, OSError) as exc:
            raise QueryError(
                error_code=ErrorCode.OLAP_QUERY_FAILED,
                message=f"Failed to read dataset '{dataset_name}': {exc}",
            ) from exc

        # Execute query with LanceScanAdapter (native) or PyArrow fallback.
        # conn.execute() runs under an interrupt watchdog so a stuck scan is
        # aborted (conn.interrupt()) at query_timeout_seconds instead of
        # wedging the OLAP executor slot forever.
        with session as conn:
            self._register_dataset(conn, dataset_name, source)
            for name, extra_table in (tables or {}).items():
                conn.register(name, extra_table)

            def _exec_query() -> Any:
                reader = conn.execute(limited_sql).arrow()
                # DuckDB may return RecordBatchReader — convert to Table
                return reader.read_all() if hasattr(reader, "read_all") else reader

            result_table = run_duckdb_interruptible(
                conn,
                _exec_query,
                timeout=self._config.query_timeout_seconds,
                label=f"olap_query:{dataset_name}",
                on_uninterruptible=self._on_uninterruptible_tripper(session, dataset_name),
            )

        # Store in cache
        if self._cache is not None:
            self._cache.put(dataset_name, limited_sql, result_table, tables)

        return OlapQueryResult(
            table=result_table,
            row_count=result_table.num_rows,
            column_count=result_table.num_columns,
            sql=limited_sql,
        )

    def materialize(
        self,
        dataset_name: str,
        sql: str,
        *,
        view_name: str | None = None,
        ttl_days: int | None = None,
        max_join_rows: int | None = None,
    ) -> int:
        """Materialize a SQL query result as a DuckLake table.

        Uses DuckLake for persistent materialized views with TTL-based
        lifecycle management.

        Args:
            dataset_name: Name of the Lance dataset to query.
            sql: SQL query string (must be SELECT only).
            view_name: Name for the materialized table (None = auto-generate).
            ttl_days: TTL in days (None = use config default).
            max_join_rows: Maximum row budget (None = use config default).

        Returns:
            Number of rows materialized.

        Raises:
            QueryError: If SQL validation fails or materialization fails.
            ValueError: If dataset name is invalid.
        """
        _validate_dataset_name(dataset_name)
        self._validate_sql(sql)

        if view_name is not None:
            validate_identifier(view_name)

        if not self._config.ducklake_enabled:
            raise QueryError(
                error_code=ErrorCode.OLAP_QUERY_FAILED,
                message="DuckLake materialization is not enabled (ducklake_enabled=False)",
            )

        if view_name is None:
            view_name = f"_materialized_{dataset_name}"

        from arrow_lake.query.ducklake_workspace import DuckLakeWorkspace

        workspace = DuckLakeWorkspace(
            ttl_days=ttl_days or self._config.ducklake_ttl_days,
            max_join_rows=max_join_rows or self._config.ducklake_max_join_rows,
        )

        # Read dataset
        try:
            source = self._storage.read_dataset(dataset_name)
        except (StorageError, OSError) as exc:
            raise QueryError(
                error_code=ErrorCode.OLAP_QUERY_FAILED,
                message=f"Failed to read dataset '{dataset_name}': {exc}",
            ) from exc

        with self._managed_session(load_ducklake=True) as conn:
            self._register_dataset(conn, dataset_name, source)
            return workspace.materialize(
                conn, sql, view_name,
                index_columns=self._config.ducklake_index_columns or None,
            )

    def cleanup_materialized(self, ttl_days: int | None = None) -> list[str]:
        """Drop expired materialized views.

        Args:
            ttl_days: Override TTL in days (None = use config default).

        Returns:
            List of dropped table names.
        """
        from arrow_lake.query.ducklake_workspace import DuckLakeWorkspace

        workspace = DuckLakeWorkspace(
            ttl_days=ttl_days or self._config.ducklake_ttl_days,
        )

        with self._managed_session(load_ducklake=True) as conn:
            return workspace.cleanup_expired(conn)

    def list_materialized(self) -> list[dict]:
        """List materialized DuckLake views with lifecycle metadata.

        Returns:
            List of ``{view_name, created_at, expires_at, row_count}``.

        Note:
            Requires ``ducklake_enabled=True``; metadata is TEMP/session-scoped
            (see ``DuckLakeWorkspace.list_views``).
        """
        if not self._config.ducklake_enabled:
            raise QueryError(
                error_code=ErrorCode.OLAP_QUERY_FAILED,
                message="DuckLake materialization is not enabled (ducklake_enabled=False)",
            )
        from arrow_lake.query.ducklake_workspace import DuckLakeWorkspace

        workspace = DuckLakeWorkspace(ttl_days=self._config.ducklake_ttl_days)
        with self._managed_session(load_ducklake=True) as conn:
            return workspace.list_views(conn)

    def drop_materialized(self, view_name: str) -> bool:
        """Drop a single materialized view by name.

        Args:
            view_name: Safe SQL identifier (validated).

        Raises:
            QueryError: If ducklake not enabled.
        """
        if not self._config.ducklake_enabled:
            raise QueryError(
                error_code=ErrorCode.OLAP_QUERY_FAILED,
                message="DuckLake materialization is not enabled (ducklake_enabled=False)",
            )
        validate_identifier(view_name)
        from arrow_lake.query.ducklake_workspace import DuckLakeWorkspace

        workspace = DuckLakeWorkspace(ttl_days=self._config.ducklake_ttl_days)
        with self._managed_session(load_ducklake=True) as conn:
            return workspace.drop_view(conn, view_name)

    def graph_query(
        self,
        edges_dataset: str,
        *,
        src_col: str = "src",
        dst_col: str = "dst",
        start_node: int | str,
        max_depth: int = 3,
        weight_col: str | None = None,
        directed: bool = True,
    ) -> OlapQueryResult:
        """Run a bounded graph traversal over an edges dataset.

        Finds reachable nodes and paths from ``start_node`` using a DuckDB
        recursive CTE (cycle-safe BFS). PGQ (``CREATE PROPERTY GRAPH`` /
        ``MATCH``) is unavailable in the bundled DuckDB 1.5.2 build — the
        ``pgq`` extension is not installable on this platform — so recursive
        CTE delivers equivalent lightweight neighbor/path traversal with zero
        extension dependency. This complements HugeGraph for in-process graph
        queries on edge tables (邻居/路径) without standing up a graph server.

        Args:
            edges_dataset: Name of the Lance dataset holding edges.
            src_col: Source node column (default "src").
            dst_col: Destination node column (default "dst").
            start_node: Node value to traverse from (int or str).
            max_depth: Maximum traversal depth; clamped to [1, 10] to bound cost.
            weight_col: Optional numeric edge column summed along each path as
                ``cost`` (None = omit cost column).
            directed: If False, traverse edges in both directions.

        Returns:
            OlapQueryResult with columns ``depth, node, path`` (plus ``cost``
            when ``weight_col`` is given). ``path`` is a ``VARCHAR[]`` of node
            ids from start to the reached node. The start node itself is row 0.

        Raises:
            QueryError: If dataset read fails or traversal fails.
            ValueError: If dataset name or column identifiers are invalid.
        """
        _validate_dataset_name(edges_dataset)
        validate_identifier(src_col)
        validate_identifier(dst_col)
        if weight_col is not None:
            validate_identifier(weight_col)
        if start_node is None:
            raise QueryError(
                error_code=ErrorCode.OLAP_QUERY_FAILED,
                message="start_node must not be None",
            )

        # Bound max_depth to prevent runaway recursive expansion.
        clamped_depth = max(1, min(int(max_depth), 10))
        if clamped_depth != max_depth:
            logger.warning(
                "graph_query max_depth %s clamped to %s", max_depth, clamped_depth,
            )

        # Cast node columns to VARCHAR for uniform typing + array path building.
        # The edge CTE normalizes src/dst (+ optional weight); undirected adds a
        # reversed-edge union so traversal treats edges as bidirectional.
        weight_expr = (
            f"CAST({weight_col} AS DOUBLE)" if weight_col else "CAST(0.0 AS DOUBLE)"
        )
        edge_cte = (
            f"SELECT CAST({src_col} AS VARCHAR) AS s, "
            f"CAST({dst_col} AS VARCHAR) AS d, {weight_expr} AS w "
            f"FROM {edges_dataset}"
        )
        if not directed:
            edge_cte += (
                f" UNION ALL SELECT CAST({dst_col} AS VARCHAR) AS s, "
                f"CAST({src_col} AS VARCHAR) AS d, {weight_expr} AS w "
                f"FROM {edges_dataset}"
            )

        select_cols = "depth, node, path" + (", cost" if weight_col else "")

        # ``params`` CTE binds $1 exactly once; recursive CTE then references it
        # via the params relation. Cycle guard via list_contains prevents loops.
        sql = (
            f"WITH RECURSIVE "
            f"params(start) AS (SELECT $1::VARCHAR), "
            f"edge(s, d, w) AS ({edge_cte}), "
            f"traverse(depth, node, path, cost) AS ("
            f"SELECT 0, p.start, [p.start], CAST(0.0 AS DOUBLE) FROM params p "
            f"UNION ALL "
            f"SELECT t.depth + 1, e.d, list_append(t.path, e.d), "
            f"t.cost + COALESCE(e.w, 0.0) "
            f"FROM traverse t JOIN edge e ON t.node = e.s "
            f"WHERE t.depth < {clamped_depth} "
            f"AND NOT list_contains(t.path, e.d)"
            f") "
            f"SELECT {select_cols} FROM traverse ORDER BY depth, node"
        )

        try:
            source = self._storage.read_dataset(edges_dataset)
        except (StorageError, OSError) as exc:
            raise QueryError(
                error_code=ErrorCode.OLAP_QUERY_FAILED,
                message=f"Failed to read dataset '{edges_dataset}': {exc}",
            ) from exc

        session = self._managed_session()
        with session as conn:
            self._register_dataset(conn, edges_dataset, source)
            try:

                def _exec_graph() -> Any:
                    reader = conn.execute(sql, [start_node]).arrow()
                    return reader.read_all() if hasattr(reader, "read_all") else reader

                result_table = run_duckdb_interruptible(
                    conn,
                    _exec_graph,
                    timeout=self._config.query_timeout_seconds,
                    label=f"graph_query:{edges_dataset}",
                    on_uninterruptible=self._on_uninterruptible_tripper(session, edges_dataset),
                )
            except duckdb.Error as exc:
                raise QueryError(
                    error_code=ErrorCode.OLAP_QUERY_FAILED,
                    message=f"Graph traversal failed on '{edges_dataset}': {exc}",
                ) from exc

        return OlapQueryResult(
            table=result_table,
            row_count=result_table.num_rows,
            column_count=result_table.num_columns,
            sql=sql,
        )

    def fts_search(
        self,
        dataset_name: str,
        query: str,
        *,
        text_column: str = "text_content",
        top_k: int = 10,
    ) -> OlapQueryResult:
        """DuckDB native FTS search (v1.8.0 #12) — alternative to lance_fts.

        Uses the DuckDB ``fts`` extension (PRAGMA create_fts_index + BM25
        MATCH) as a native alternative to lancedb's FTS for comparison /
        benchmarking. The ``vss`` extension is NOT installable on this DuckDB
        build (documented gap, same as PGQ). Requires an ``id`` column.

        Args:
            dataset_name: Name of the Lance dataset (must have an ``id`` col).
            query: Search query (BM25 match, bound via ``$1``).
            text_column: Text column to index/search.
            top_k: Number of results.

        Returns:
            OlapQueryResult with matching rows + a ``score`` column.

        Raises:
            QueryError: If fts extension unavailable, dataset read fails, or
                the dataset lacks an ``id`` column.
        """
        _validate_dataset_name(dataset_name)
        validate_identifier(text_column)

        try:
            source = self._storage.read_dataset(dataset_name)
        except (StorageError, OSError) as exc:
            raise QueryError(
                error_code=ErrorCode.OLAP_QUERY_FAILED,
                message=f"Failed to read dataset '{dataset_name}': {exc}",
            ) from exc

        if "id" not in source.schema.names:
            raise QueryError(
                error_code=ErrorCode.OLAP_QUERY_FAILED,
                message=f"DuckDB fts requires an 'id' column in '{dataset_name}'",
            )

        fts_table = f"_fts_{dataset_name}"
        with self._managed_session() as conn:
            try:
                conn.execute("INSTALL fts; LOAD fts;")
            except duckdb.CatalogException as exc:
                raise QueryError(
                    error_code=ErrorCode.OLAP_QUERY_FAILED,
                    message=f"DuckDB fts extension unavailable: {exc}",
                ) from exc
            # Materialize to a temp TABLE — PRAGMA create_fts_index needs a
            # base table, not the registered Lance view.
            self._register_dataset(conn, dataset_name, source)
            conn.execute(f"DROP TABLE IF EXISTS {fts_table};")  # nosec B608
            conn.execute(
                f"CREATE TEMP TABLE {fts_table} AS SELECT * FROM {dataset_name};"  # nosec B608
            )
            conn.execute(
                f"PRAGMA create_fts_index('{fts_table}', 'id', '{text_column}', overwrite=1);"
            )
            sql = (
                f"SELECT *, fts_main_{fts_table}.match_bm25(id, $1) AS score "
                f"FROM {fts_table} WHERE score IS NOT NULL "
                f"ORDER BY score DESC LIMIT {max(1, int(top_k))}"
            )
            try:
                result_reader = conn.execute(sql, [query]).arrow()
            except duckdb.Error as exc:
                raise QueryError(
                    error_code=ErrorCode.OLAP_QUERY_FAILED,
                    message=f"DuckDB fts search failed: {exc}",
                ) from exc
            if hasattr(result_reader, "read_all"):
                result_table = result_reader.read_all()
            else:
                result_table = result_reader

        return OlapQueryResult(
            table=result_table,
            row_count=result_table.num_rows,
            column_count=result_table.num_columns,
            sql=sql,
        )

    def explain(self, dataset_name: str, sql: str) -> str:
        """Return DuckDB EXPLAIN output for query optimization analysis.

        Args:
            dataset_name: Name of the Lance dataset.
            sql: SQL query string to explain.

        Returns:
            DuckDB EXPLAIN output as a string.

        Raises:
            QueryError: If SQL validation fails or query/explain fails.
            ValueError: If dataset name is invalid.
        """
        _validate_dataset_name(dataset_name)
        self._validate_sql(sql)
        source_ds, source_table = _split_two_part(dataset_name)
        if source_table is None:
            self._reject_bare_container(dataset_name)

        try:
            table = self._storage.read_dataset(source_ds, table=source_table)
        except (StorageError, OSError) as exc:
            raise QueryError(
                error_code=ErrorCode.OLAP_QUERY_FAILED,
                message=f"Failed to read dataset '{dataset_name}': {exc}",
            ) from exc

        with self._managed_session() as conn:
            self._register_dataset(conn, dataset_name, table)
            result = conn.execute(f"EXPLAIN {sql}").fetchall()
            explain_lines = [row[0] for row in result if row]
            return "\n".join(explain_lines)

    def explain_analyze(self, dataset_name: str, sql: str) -> str:
        """Return DuckDB EXPLAIN ANALYZE output with actual execution stats.

        Unlike ``explain()`` which shows the logical plan, this runs the query
        and reports real timing, row counts, and bytes spilled per operator.

        When ``enable_profiling`` is True in OlapConfig, also returns profiling
        metrics via ``pragma_last_profiling_output()``.

        Args:
            dataset_name: Name of the Lance dataset.
            sql: SQL query string to analyze.

        Returns:
            DuckDB EXPLAIN ANALYZE output as a string. If profiling is enabled,
            appended with a ``--- Profiling ---`` section containing per-operator
            timing, cardinality, and memory metrics.

        Raises:
            QueryError: If SQL validation fails or query/analysis fails.
            ValueError: If dataset name is invalid.
        """
        _validate_dataset_name(dataset_name)
        self._validate_sql(sql)

        try:
            table = self._storage.read_dataset(dataset_name)
        except (StorageError, OSError) as exc:
            raise QueryError(
                error_code=ErrorCode.OLAP_QUERY_FAILED,
                message=f"Failed to read dataset '{dataset_name}': {exc}",
            ) from exc

        with self._managed_session() as conn:
            self._register_dataset(conn, dataset_name, table)
            result = conn.execute(f"EXPLAIN ANALYZE {sql}").fetchall()
            explain_lines = [row[0] for row in result if row]

            # Append profiling metrics when enabled
            profiling_section = self._get_profiling_info(conn)
            if profiling_section:
                explain_lines.append("")
                explain_lines.append("--- Profiling ---")
                explain_lines.append(profiling_section)

            return "\n".join(explain_lines)

    @staticmethod
    def _get_profiling_info(conn: duckdb.DuckDBPyConnection) -> str | None:
        """Extract profiling output from DuckDB after a query.

        Returns a formatted string with per-operator metrics, or None if
        profiling is not enabled or no output is available.
        """
        try:
            rows = conn.execute(
                "SELECT name, elapsed_seconds, cardinality, extra_info "
                "FROM pragma_last_profiling_output() "
                "ORDER BY elapsed_seconds DESC",
            ).fetchall()
        except (duckdb.CatalogException, duckdb.ParserException):
            return None

        if not rows:
            return None

        lines: list[str] = []
        for name, elapsed, cardinality, extra_info in rows:
            parts = [f"{name}:"]
            if elapsed is not None:
                parts.append(f" {elapsed:.4f}s")
            if cardinality is not None:
                parts.append(f" rows={cardinality}")
            if extra_info:
                mem = extra_info.get("MemoryUsage", "")
                spilled = extra_info.get("BytesSpilled", "")
                if mem:
                    parts.append(f" mem={mem}")
                if spilled:
                    parts.append(f" spilled={spilled}")
            lines.append("".join(parts))

        return "\n".join(lines)

    def _resolve_scan_mode(self, dataset_name: str, source: Any) -> str:
        """Resolve the lance scan mode for a dataset (v1.10.4).

        Priority:
        1. Per-dataset override (``lance_scan_mode_overrides``) — but a vector
           dataset listed there is demoted back to the global mode with a
           warning, since native lance scan panics on IVF_PQ vector streams.
        2. Global ``lance_scan_mode`` + ``lance_auto_promote`` flip (a vector-less
           dataset on pyarrow_fallback may promote to native for the 34-145x
           speedup; this path is now also breaker-protected).
        3. If the candidate is native/auto and the breaker has tripped for this
           dataset, demote to pyarrow_fallback for the cooldown window.

        Returns one of ``"auto"``, ``"native"``, ``"pyarrow_fallback"``.
        """
        overrides = self._config.lance_scan_mode_overrides
        has_vector = self._has_vector_column(source)

        if dataset_name in overrides:
            candidate = overrides[dataset_name]
            if has_vector:
                # Hard guard: native lance scan panics on IVF_PQ vector streams.
                # Warn once per (worker, dataset) to avoid log spam under traffic.
                if dataset_name not in _warned_vector_override:
                    _warned_vector_override.add(dataset_name)
                    logger.warning(
                        "lance_scan_override_ignored dataset=%s override=%s — vector "
                        "column present; falling back to global lance_scan_mode=%s",
                        dataset_name, candidate, self._config.lance_scan_mode,
                    )
                candidate = self._config.lance_scan_mode
        else:
            candidate = self._config.lance_scan_mode

        # Global auto-promote: promote a vector-less pyarrow_fallback dataset to
        # native scan (override-to-native already targets native, so skip here).
        if (
            candidate == "pyarrow_fallback"
            and self._config.lance_auto_promote
            and not has_vector
        ):
            candidate = "auto"

        # Breaker: demote a tripped native/auto dataset to pyarrow_fallback.
        if candidate in ("native", "auto") and self._breaker.is_tripped(dataset_name):
            logger.info(
                "lance_cb_demoting dataset=%s mode=%s -> pyarrow_fallback (cooldown)",
                dataset_name, candidate,
            )
            return "pyarrow_fallback"
        return candidate

    def _on_uninterruptible_tripper(
        self, session: Any, dataset_name: str,
    ) -> Callable[[], None]:
        """Build the watchdog ``on_uninterruptible`` callback for a query.

        Invoked when a query survives ``conn.interrupt()`` (stuck in native code
        that doesn't poll the interrupt flag): marks the session unhealthy so it
        is closed rather than recycled, and records a breaker strike so a
        repeatedly-stuck native scan demotes to pyarrow_fallback.
        """
        mark = getattr(session, "mark_unhealthy", None)

        def _on_stuck() -> None:
            if mark is not None:
                with contextlib.suppress(Exception):
                    mark()
            self._breaker.record_trip(dataset_name)

        return _on_stuck

    @staticmethod
    def _has_vector_column(source: Any) -> bool:
        """Return True if the Arrow source has a fixed_size_list (vector) column.

        IVF_PQ Rust panic only occurs on vector-column scans, so vector-less
        structured datasets are safe under the fast native lance scan path
        (实测 noaa 翻页 5.4s→0.27s). Unknown schema → True (stay safe).
        """
        try:
            import pyarrow as pa
            for field in source.schema:
                try:
                    if pa.types.is_fixed_size_list(field.type):
                        return True
                except Exception:
                    continue
        except Exception:
            return True
        return False

    def _reject_bare_container(self, dataset_name: str) -> None:
        """D6: a multi-table container referenced without a table is
        ambiguous — 422 with the available tables (a configured
        ``default_table`` escape hatch lands with the console/API surface,
        W4). Plain single-table datasets pass through untouched.
        """
        try:
            tables = self._storage.list_container_tables(dataset_name)
        except Exception:  # noqa: BLE001 — detection must not 500
            return
        # Require a REAL non-empty list: fakes/mocks return assorted truthy
        # objects (MagicMock, list_tables results) that would false-positive.
        if not isinstance(tables, (list, tuple)) or not tables:
            return
        raise QueryError(
            error_code=ErrorCode.OLAP_AMBIGUOUS_DATASET,
            message=(
                f"Dataset '{dataset_name}' is a multi-table container — "
                f"reference it as '{dataset_name}.<table>' "
                f"(available: {', '.join(str(t) for t in tables)})"
            ),
        )

    def _register_dataset(self, conn: Any, dataset_name: str, source: Any) -> None:
        """Register a Lance dataset in DuckDB, preferring native lance scan.

        Tries LanceScanAdapter.create_view() for zero-copy native scan.
        Falls back to conn.register() for PyArrow compatibility.

        Args:
            conn: Active DuckDB connection.
            dataset_name: Name to register the dataset under.
            source: Arrow Table or RecordBatchReader from storage.
        """
        # Scan mode resolution (v1.10.4): per-dataset opt-in override → global +
        # auto-promote, then the breaker demotes a tripped native/auto dataset to
        # pyarrow_fallback. Vector datasets are hard-demoted (IVF_PQ panic guard).
        # The key stays the FULL two-part name — scan overrides and the breaker
        # are per container-table from day one (W3.3).
        mode = self._resolve_scan_mode(dataset_name, source)
        # Two-part refs (W3.2): register under an internal flat name (adapter
        # identifier rules reject dots), then expose a schema-qualified view
        # so `FROM ds.table` resolves natively (probe-verified: DuckDB
        # register() rejects dotted names; CREATE SCHEMA + view works).
        two_part = "." in dataset_name
        if two_part:
            _ds, _, _tbl = dataset_name.partition(".")
            register_name = f"_al__{_ds}__{_tbl}"
            scan_ds, scan_table = _ds, _tbl
        else:
            register_name = dataset_name
            scan_ds, scan_table = dataset_name, None

        def _expose_two_part() -> None:
            if two_part:
                conn.execute(f'CREATE SCHEMA IF NOT EXISTS "{_ds}"')
                conn.execute(
                    f'CREATE OR REPLACE VIEW "{_ds}"."{_tbl}" AS '  # nosec B608
                    f"SELECT * FROM {register_name}"
                )

        if mode == "pyarrow_fallback":
            # Clear stale registration from pooled connections
            with contextlib.suppress(duckdb.Error):
                conn.execute(f"DROP VIEW IF EXISTS {register_name}")  # nosec B608
            conn.register(register_name, source)
            _expose_two_part()
            return

        # Clear any stale VIEW or table function from pooled connections
        with contextlib.suppress(duckdb.Error):
            conn.execute(f"DROP VIEW IF EXISTS {register_name}")  # nosec B608
        with contextlib.suppress(duckdb.Error):
            conn.unregister(register_name)

        # Try native lance scan
        try:
            if hasattr(self._storage, "dataset_uri"):
                if (
                    self._storage_config
                    and self._storage_config.backend != StorageBackend.LOCAL
                ):
                    uri = self._storage.dataset_uri(scan_ds, scan_table)
                else:
                    uri = self._storage.dataset_uri(scan_ds, table=scan_table)
                adapter = create_lance_scan_adapter(
                    conn,
                    mode=mode,
                )
                adapter.create_view(conn, uri, register_name)
                _expose_two_part()
                logger.debug("Registered %s via native lance scan", dataset_name)
                return
        except (duckdb.Error, OSError):
            logger.debug(
                "Native lance scan failed for %s, falling back to PyArrow",
                dataset_name,
            )

        # Fallback: register Arrow source directly
        conn.register(register_name, source)
        _expose_two_part()

    def _managed_session(self, *, load_ducklake: bool = False) -> Any:
        """Acquire a managed session from SessionManager or fallback."""
        if self._session_manager is not None:
            return self._session_manager.acquire(load_ducklake=load_ducklake)
        return create_duckdb_session(
            max_memory_mb=self._config.max_query_memory_mb,
            timeout_seconds=self._config.query_timeout_seconds,
            olap_config=self._config,
            storage_config=self._storage_config,
            load_ducklake=load_ducklake,
        )

    def _validate_sql(self, sql: str) -> None:
        """Validate SQL is SELECT-only with no dangerous patterns.

        When enable_join is False, blocks JOIN keywords as well.

        Raises:
            QueryError: If SQL is empty, not SELECT, contains dangerous
                keywords, contains semicolons, or contains JOIN when disabled.
        """
        if not sql or not sql.strip():
            raise QueryError(
                error_code=ErrorCode.OLAP_QUERY_FAILED,
                message="SQL query must not be empty",
            )

        stripped = sql.strip().upper()
        if not stripped.startswith("SELECT"):
            raise QueryError(
                error_code=ErrorCode.OLAP_QUERY_FAILED,
                message="Only SELECT queries are allowed via OlapSearchBridge",
            )

        # Block JOIN when disabled
        if not self._config.enable_join:
            join_match = _JOIN_KEYWORD_RE.search(stripped)
            if join_match:
                raise QueryError(
                    error_code=ErrorCode.QUERY_JOIN_NOT_ALLOWED,
                    message=f"JOIN queries are not allowed (enable_join=False): "
                    f"'{join_match.group()!r}' found",
                )

        # Block dangerous SQL keywords using word-boundary regex
        match = DANGEROUS_SQL_KEYWORDS_RE.search(stripped)
        if match:
            raise QueryError(
                error_code=ErrorCode.OLAP_QUERY_FAILED,
                message=f"Keyword '{match.group()!r}' is not allowed in queries",
            )

        if ";" in sql:
            raise QueryError(
                error_code=ErrorCode.OLAP_QUERY_FAILED,
                message="Semicolons are not allowed (single statement only)",
            )

    @staticmethod
    def _apply_limit(sql: str, max_rows: int) -> str:
        """Append LIMIT to SQL if not already present.

        Avoids materializing rows beyond max_rows in DuckDB.
        Uses MIN(existing_limit, max_rows) if LIMIT already exists.
        """
        import re as _re

        stripped = sql.rstrip().rstrip(";")
        match = _re.search(
            r"\bLIMIT\s+(\d+)(\s+OFFSET\s+\d+)?\s*$", stripped, _re.IGNORECASE,
        )
        if match:
            existing = int(match.group(1))
            effective = min(existing, max_rows)
            offset_clause = match.group(2) or ""
            return stripped[: match.start()] + f"LIMIT {effective}{offset_clause}"
        return f"{stripped} LIMIT {max_rows}"


def _validate_dataset_name(dataset_name: str) -> None:
    """Validate dataset name to prevent path traversal and injection.

    Accepts plain dataset names and two-part container references
    (``ds.table``, DR14 W3.2); both segments validate as identifiers.

    Raises:
        ValueError: If dataset name contains unsafe characters.
    """
    if "." in dataset_name:
        ds, _, table = dataset_name.partition(".")
        validate_identifier(ds)
        validate_identifier(table)
        return
    validate_identifier(dataset_name)


def _split_two_part(dataset_name: str) -> tuple[str, str | None]:
    """'ds.table' → ('ds', 'table'); plain name → (name, None).

    Dataset names never contain dots (identifier rules), so a dot is an
    unambiguous container-table reference (D1).
    """
    if "." in dataset_name:
        ds, _, table = dataset_name.partition(".")
        return ds, table
    return dataset_name, None
