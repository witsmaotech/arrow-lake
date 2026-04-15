"""Metadata search bridge — Story 3.9, 7.6.

Provides SQL query interface over Lance datasets via DuckDB.
Uses zero-copy Arrow → DuckDB → Arrow pipeline.

Story 7.6: Added to_arrow() convenience method on MetadataQueryResult.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import duckdb
import pyarrow as pa

from arrow_lake.exceptions import ErrorCode, QueryError
from arrow_lake.ingest.storage import LanceStorageManager

_SAFE_IDENTIFIER_RE = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_-]*$")


@dataclass(frozen=True)
class MetadataQueryResult:
    """Result of a metadata SQL query.

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


class MetadataSearchBridge:
    """Bridges Lance datasets to DuckDB for SQL metadata queries.

    Pipeline: Lance → Arrow → DuckDB table → SQL → Arrow result.
    Only SELECT queries are allowed for safety.

    Args:
        storage: LanceStorageManager instance.
    """

    def __init__(self, storage: LanceStorageManager) -> None:
        self._storage = storage

    def query(
        self,
        dataset_name: str,
        sql: str,
        tables: dict[str, pa.Table] | None = None,
    ) -> MetadataQueryResult:
        """Execute a SQL query against a Lance dataset.

        Args:
            dataset_name: Name of the Lance dataset to query.
            sql: SQL query string (must be SELECT only).
            tables: Additional Arrow tables to register for JOIN queries.

        Returns:
            MetadataQueryResult with Arrow table and metadata.

        Raises:
            QueryError: If SQL is not SELECT, dataset not found, or query fails.
            ValueError: If dataset name or table name is invalid.
        """
        if not _SAFE_IDENTIFIER_RE.match(dataset_name):
            raise ValueError(f"Invalid dataset name '{dataset_name}'")

        stripped = sql.strip().upper()
        if not stripped.startswith("SELECT"):
            raise QueryError(
                error_code=ErrorCode.QUERY_SYNTAX_ERROR,
                message="Only SELECT queries are allowed via MetadataSearchBridge",
            )

        # Block dangerous SQL patterns (injection prevention)
        import re

        _dangerous_keywords_re = re.compile(
            r"\b(INSERT|UPDATE|DELETE|DROP|ALTER|CREATE|TRUNCATE|"
            r"EXEC|EXECUTE|GRANT|REVOKE|COPY|IMPORT|EXPORT)\b",
            re.IGNORECASE,
        )
        match = _dangerous_keywords_re.search(stripped)
        if match:
            raise QueryError(
                error_code=ErrorCode.QUERY_SYNTAX_ERROR,
                message=f"Keyword '{match.group()}' is not allowed in queries",
            )
        if ";" in sql:
            raise QueryError(
                error_code=ErrorCode.QUERY_SYNTAX_ERROR,
                message="Semicolons are not allowed (single statement only)",
            )

        # Validate extra table names
        if tables:
            for name in tables:
                if not _SAFE_IDENTIFIER_RE.match(name):
                    raise ValueError(f"Invalid table name '{name}'")

        # Read dataset from Lance
        try:
            table = self._storage.read_dataset(dataset_name)
        except Exception as exc:
            raise QueryError(
                error_code=ErrorCode.QUERY_NO_RESULTS,
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
        except Exception as exc:
            raise QueryError(
                error_code=ErrorCode.QUERY_SYNTAX_ERROR,
                message=f"SQL query failed: {exc}",
            ) from exc
        finally:
            conn.close()

        return MetadataQueryResult(
            table=result_table,
            row_count=result_table.num_rows,
            column_count=result_table.num_columns,
            sql=sql,
        )
