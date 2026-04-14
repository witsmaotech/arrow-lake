"""Metadata search bridge — Story 3.9.

Provides SQL query interface over Lance datasets via DuckDB.
Uses zero-copy Arrow → DuckDB → Arrow pipeline.
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
    """Result of a metadata SQL query."""

    table: pa.Table
    row_count: int
    column_count: int
    sql: str


class MetadataSearchBridge:
    """Bridges Lance datasets to DuckDB for SQL metadata queries.

    Pipeline: Lance → Arrow → DuckDB table → SQL → Arrow result.
    Only SELECT queries are allowed for safety.

    Args:
        storage: LanceStorageManager instance.
    """

    def __init__(self, storage: LanceStorageManager) -> None:
        self._storage = storage

    def query(self, dataset_name: str, sql: str) -> MetadataQueryResult:
        """Execute a SQL query against a Lance dataset.

        Args:
            dataset_name: Name of the Lance dataset to query.
            sql: SQL query string (must be SELECT only).

        Returns:
            MetadataQueryResult with Arrow table and metadata.

        Raises:
            QueryError: If SQL is not SELECT, dataset not found, or query fails.
            ValueError: If dataset name is invalid.
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
        _dangerous_keywords = (
            "INSERT",
            "UPDATE",
            "DELETE",
            "DROP",
            "ALTER",
            "CREATE",
            "TRUNCATE",
            "EXEC",
            "EXECUTE",
            "GRANT",
            "REVOKE",
            "COPY",
            "IMPORT",
            "EXPORT",
        )
        for keyword in _dangerous_keywords:
            if keyword in stripped:
                raise QueryError(
                    error_code=ErrorCode.QUERY_SYNTAX_ERROR,
                    message=f"Keyword '{keyword}' is not allowed in queries",
                )
        if ";" in sql:
            raise QueryError(
                error_code=ErrorCode.QUERY_SYNTAX_ERROR,
                message="Semicolons are not allowed (single statement only)",
            )

        # Read dataset from Lance
        try:
            table = self._storage.read_dataset(dataset_name)
        except Exception as exc:
            raise QueryError(
                error_code=ErrorCode.QUERY_NO_RESULTS,
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
