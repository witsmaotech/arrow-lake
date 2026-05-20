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
from dataclasses import dataclass
from typing import Any, TYPE_CHECKING

import duckdb
import pyarrow as pa

from arrow_lake.config import OlapConfig, StorageBackend, StorageConfig
from arrow_lake.exceptions import ErrorCode, QueryError, StorageError
from arrow_lake.query._db import create_duckdb_session
from arrow_lake.query.lance_adapter import create_lance_scan_adapter
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

        effective_max_rows = max_rows if max_rows is not None else self._config.max_result_rows

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
                    dataset_name,
                    batch_size=self._config.scanner_batch_size,
                )
            else:
                source = self._storage.read_dataset(dataset_name)
        except (StorageError, OSError) as exc:
            raise QueryError(
                error_code=ErrorCode.OLAP_QUERY_FAILED,
                message=f"Failed to read dataset '{dataset_name}': {exc}",
            ) from exc

        # Execute query with LanceScanAdapter (native) or PyArrow fallback
        with session as conn:
            self._register_dataset(conn, dataset_name, source)
            for name, extra_table in (tables or {}).items():
                conn.register(name, extra_table)
            result_reader = conn.execute(limited_sql).arrow()
            # DuckDB may return RecordBatchReader — convert to Table
            if hasattr(result_reader, "read_all"):
                result_table = result_reader.read_all()
            else:
                result_table = result_reader

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

        try:
            table = self._storage.read_dataset(dataset_name)
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

    def _register_dataset(self, conn: Any, dataset_name: str, source: Any) -> None:
        """Register a Lance dataset in DuckDB, preferring native lance scan.

        Tries LanceScanAdapter.create_view() for zero-copy native scan.
        Falls back to conn.register() for PyArrow compatibility.

        Args:
            conn: Active DuckDB connection.
            dataset_name: Name to register the dataset under.
            source: Arrow Table or RecordBatchReader from storage.
        """
        if self._config.lance_scan_mode == "pyarrow_fallback":
            # Clear stale registration from pooled connections
            with contextlib.suppress(duckdb.Error):
                conn.execute(f"DROP VIEW IF EXISTS {dataset_name}")  # nosec B608
            conn.register(dataset_name, source)
            return

        # Clear any stale VIEW or table function from pooled connections
        with contextlib.suppress(duckdb.Error):
            conn.execute(f"DROP VIEW IF EXISTS {dataset_name}")  # nosec B608
        with contextlib.suppress(duckdb.Error):
            conn.unregister(dataset_name)

        # Try native lance scan
        try:
            if hasattr(self._storage, "dataset_uri"):
                if (
                    self._storage_config
                    and self._storage_config.backend != StorageBackend.LOCAL
                ):
                    uri = f"{self._storage_config.s3_uri.rstrip('/')}/{dataset_name}.lance"
                else:
                    uri = self._storage.dataset_uri(dataset_name)
                adapter = create_lance_scan_adapter(
                    conn,
                    mode=self._config.lance_scan_mode,
                )
                adapter.create_view(conn, uri, dataset_name)
                logger.debug("Registered %s via native lance scan", dataset_name)
                return
        except (duckdb.Error, OSError):
            logger.debug(
                "Native lance scan failed for %s, falling back to PyArrow",
                dataset_name,
            )

        # Fallback: register Arrow source directly
        conn.register(dataset_name, source)

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

    Raises:
        ValueError: If dataset name contains unsafe characters.
    """
    validate_identifier(dataset_name)
