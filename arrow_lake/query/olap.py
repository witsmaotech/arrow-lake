"""OLAP analytics — Story 5.4, 7.6.

ruff noqa: F821 (Any used in annotations with __future__ import)

Provides OlapSearchBridge for SQL analytics queries over Lance datasets.
Uses DuckDB with zero-copy Arrow integration for GROUP BY, aggregation,
window functions, JOIN, and other OLAP operations.

Story 7.6 additions:
- Multi-table registration for JOIN queries
- enable_join config flag for security control
- to_arrow() convenience method on OlapQueryResult
- daft_sql() placeholder (ADR-05: Daft 0.7.8 has no SQL)
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

import duckdb
import pyarrow as pa

from arrow_lake.config import OlapConfig
from arrow_lake.exceptions import ErrorCode, QueryError

_SAFE_IDENTIFIER_RE = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_-]*$")

_JOIN_KEYWORD_RE = re.compile(
    r"\b(INNER\s+JOIN|LEFT\s+JOIN|RIGHT\s+JOIN|FULL\s+JOIN|CROSS\s+JOIN|"
    r"JOIN|NATURAL\s+JOIN)\b",
    re.IGNORECASE,
)

_DANGEROUS_KEYWORDS_RE = re.compile(
    r"\b(INSERT|UPDATE|DELETE|DROP|ALTER|CREATE|TRUNCATE|EXEC|EXECUTE|"
    r"GRANT|REVOKE|COPY|IMPORT|EXPORT|UNION|EXCEPT|INTERSECT)\b",
    re.IGNORECASE,
)


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

    Pipeline: Lance → Arrow → DuckDB register → SQL → Arrow result.

    Supports GROUP BY, aggregation functions, window functions, HAVING,
    ORDER BY, and LIMIT.

    Thread safety: safe for concurrent reads (each query creates its own
    DuckDB connection).

    Args:
        storage: LanceStorageManager instance.
        config: OLAP analytics configuration (None = use defaults).
    """

    def __init__(
        self,
        storage: Any,
        config: OlapConfig | None = None,
    ) -> None:
        self._storage = storage
        self._config = config or OlapConfig()

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
                if not _SAFE_IDENTIFIER_RE.match(name):
                    raise ValueError(f"Invalid table name '{name}'")

        effective_max_rows = max_rows if max_rows is not None else self._config.max_result_rows

        # Read dataset from Lance
        try:
            table = self._storage.read_dataset(dataset_name)
        except Exception as exc:
            raise QueryError(
                error_code=ErrorCode.OLAP_QUERY_FAILED,
                message=f"Failed to read dataset '{dataset_name}': {exc}",
            ) from exc

        # Register tables and execute query
        conn = duckdb.connect()
        try:
            conn.register("data", table)
            for name, extra_table in (tables or {}).items():
                conn.register(name, extra_table)
            result_reader = conn.execute(sql).arrow()
            # DuckDB may return RecordBatchReader — convert to Table
            if hasattr(result_reader, "read_all"):
                result_table = result_reader.read_all()
            else:
                result_table = result_reader
        except QueryError:
            raise
        except Exception as exc:
            raise QueryError(
                error_code=ErrorCode.OLAP_QUERY_FAILED,
                message=f"OLAP query failed on '{dataset_name}': {exc}",
            ) from exc
        finally:
            conn.close()

        # Truncate to max_rows
        if result_table.num_rows > effective_max_rows:
            result_table = result_table.slice(0, effective_max_rows)

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

        try:
            table = self._storage.read_dataset(dataset_name)
        except Exception as exc:
            raise QueryError(
                error_code=ErrorCode.OLAP_QUERY_FAILED,
                message=f"Failed to read dataset '{dataset_name}': {exc}",
            ) from exc

        conn = duckdb.connect()
        try:
            conn.register("data", table)
            result = conn.execute(f"EXPLAIN {sql}").fetchall()
            explain_lines = [row[0] for row in result if row]
            return "\n".join(explain_lines)
        except QueryError:
            raise
        except Exception as exc:
            raise QueryError(
                error_code=ErrorCode.OLAP_QUERY_FAILED,
                message=f"EXPLAIN failed on '{dataset_name}': {exc}",
            ) from exc
        finally:
            conn.close()

    def daft_sql(self, sql: str) -> None:
        """Placeholder for Daft SQL interface (ADR-05).

        Daft 0.7.8 does not support df.sql(). Use the DuckDB path instead.
        This method will be replaced with a real Daft SQL implementation
        when Daft adds SQL support in a future version.

        Args:
            sql: SQL query string (unused in current version).

        Raises:
            NotImplementedError: Always. Use DuckDB via query() instead.
        """
        raise NotImplementedError(
            "Daft SQL is not available in Daft 0.7.8. "
            "Use the DuckDB OLAP path via OlapSearchBridge.query() instead."
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
        match = _DANGEROUS_KEYWORDS_RE.search(stripped)
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


def _validate_dataset_name(dataset_name: str) -> None:
    """Validate dataset name to prevent path traversal and injection.

    Raises:
        ValueError: If dataset name contains unsafe characters.
    """
    if not _SAFE_IDENTIFIER_RE.match(dataset_name):
        raise ValueError(f"Invalid dataset name '{dataset_name}'")
