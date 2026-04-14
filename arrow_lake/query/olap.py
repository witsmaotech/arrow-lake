"""OLAP analytics — Story 5.4.

ruff noqa: F821 (Any used in annotations with __future__ import)

Provides OlapSearchBridge for SQL analytics queries over Lance datasets.
Uses DuckDB with zero-copy Arrow integration for GROUP BY, aggregation,
window functions, and other OLAP operations.
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
    ) -> OlapQueryResult:
        """Execute an OLAP SQL query against a Lance dataset.

        Args:
            dataset_name: Name of the Lance dataset to query.
            sql: SQL query string (must be SELECT only).
            max_rows: Maximum result rows (None = use config default).

        Returns:
            OlapQueryResult with Arrow table and metadata.

        Raises:
            QueryError: If SQL is invalid, dataset not found, or query fails.
            ValueError: If dataset name is invalid.
        """
        _validate_dataset_name(dataset_name)
        self._validate_sql(sql)

        effective_max_rows = max_rows if max_rows is not None else self._config.max_result_rows

        # Read dataset from Lance
        try:
            table = self._storage.read_dataset(dataset_name)
        except Exception as exc:
            raise QueryError(
                error_code=ErrorCode.OLAP_QUERY_FAILED,
                message=f"Failed to read dataset '{dataset_name}': {exc}",
            ) from exc

        # Register as DuckDB table and execute query
        conn = duckdb.connect()
        try:
            conn.register("data", table)
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

    @staticmethod
    def _validate_sql(sql: str) -> None:
        """Validate SQL is SELECT-only with no dangerous patterns.

        Raises:
            QueryError: If SQL is empty, not SELECT, contains dangerous
                keywords, or contains semicolons.
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
