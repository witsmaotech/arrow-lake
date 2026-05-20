"""Metadata search bridge — Story 3.9, 7.6.

Provides SQL query interface over Lance datasets via DuckDB.
Uses zero-copy Arrow → DuckDB → Arrow pipeline.

Story 7.6: Added to_arrow() convenience method on MetadataQueryResult.

M0b migration:
- DuckDBSession → create_duckdb_session() (extension loading + resource governance)
- LanceScanAdapter.create_view() for native lance scan
- Backward-compatible PyArrow fallback via conn.register()
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import duckdb
import pyarrow as pa

from arrow_lake.config import StorageConfig
from arrow_lake.exceptions import ErrorCode, QueryError, StorageError
from arrow_lake.ingest.storage import LanceStorageManager
from arrow_lake.query._db import create_duckdb_session
from arrow_lake.query.lance_adapter import create_lance_scan_adapter
from arrow_lake.validation import (
    DANGEROUS_SQL_KEYWORDS_RE,
    SAFE_IDENTIFIER_RE,
    validate_identifier,
)

logger = logging.getLogger(__name__)


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

    Pipeline: Lance → (native scan | Arrow) → DuckDB → SQL → Arrow result.
    Only SELECT queries are allowed for safety.

    Args:
        storage: LanceStorageManager instance.
        storage_config: Storage configuration for S3 access (None = local).
    """

    def __init__(
        self,
        storage: LanceStorageManager,
        storage_config: StorageConfig | None = None,
        session_manager: Any = None,
    ) -> None:
        self._storage = storage
        self._storage_config = storage_config
        self._session_manager = session_manager

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
        if not SAFE_IDENTIFIER_RE.match(dataset_name):
            raise ValueError(f"Invalid dataset name '{dataset_name}'")

        stripped = sql.strip().upper()
        if not stripped.startswith("SELECT"):
            raise QueryError(
                error_code=ErrorCode.QUERY_SYNTAX_ERROR,
                message="Only SELECT queries are allowed via MetadataSearchBridge",
            )

        # Block dangerous SQL patterns (injection prevention)
        match = DANGEROUS_SQL_KEYWORDS_RE.search(stripped)
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
                validate_identifier(name)

        # Read dataset from Lance (streaming via RecordBatchReader)
        try:
            source = self._storage.scan_dataset(dataset_name)
        except StorageError as exc:
            raise QueryError(
                error_code=ErrorCode.QUERY_NO_RESULTS,
                message=f"Failed to read dataset '{dataset_name}': {exc}",
            ) from exc

        # Execute query with session
        if self._session_manager is not None:
            managed = self._session_manager.acquire()
            try:
                self._register_dataset(managed.conn, dataset_name, source)
                for name, extra_table in (tables or {}).items():
                    managed.conn.register(name, extra_table)
                result_reader = managed.conn.execute(sql).arrow()
                if hasattr(result_reader, "read_all"):
                    result_table = result_reader.read_all()
                else:
                    result_table = result_reader
            finally:
                managed.release()
        else:
            with create_duckdb_session(storage_config=self._storage_config) as conn:
                self._register_dataset(conn, dataset_name, source)
                for name, extra_table in (tables or {}).items():
                    conn.register(name, extra_table)
                result_reader = conn.execute(sql).arrow()
                if hasattr(result_reader, "read_all"):
                    result_table = result_reader.read_all()
                else:
                    result_table = result_reader

        return MetadataQueryResult(
            table=result_table,
            row_count=result_table.num_rows,
            column_count=result_table.num_columns,
            sql=sql,
        )

    def _register_dataset(self, conn: Any, dataset_name: str, source: Any) -> None:
        """Register a Lance dataset in DuckDB, preferring native lance scan.

        Tries LanceScanAdapter.create_view() for zero-copy native scan.
        Falls back to conn.register() for PyArrow compatibility.
        """
        try:
            if hasattr(self._storage, "dataset_uri"):
                uri = self._storage.dataset_uri(dataset_name)
                adapter = create_lance_scan_adapter(conn, mode="auto")
                adapter.create_view(conn, uri, dataset_name)
                logger.debug("Registered %s via native lance scan", dataset_name)
                return
        except (duckdb.Error, OSError):
            logger.debug(
                "Native lance scan failed for %s, falling back to PyArrow",
                dataset_name,
            )

        conn.register(dataset_name, source)

    def _relational_query(
        self,
        dataset_name: str,
        columns: list[str],
        where: str | None = None,
        limit: int = 1000,
    ) -> pa.Table:
        """Type-safe Relational API query for simple schema discovery.

        Uses DuckDB's Relational API instead of raw SQL strings to eliminate
        SQL injection risk. Intended for simple select/filter operations only.

        Args:
            dataset_name: Name of the Lance dataset to query.
            columns: Column names to select. Each must match SAFE_IDENTIFIER_RE.
            where: Optional filter expression (DuckDB SQL WHERE clause syntax).
            limit: Maximum rows to return (default 1000).

        Returns:
            PyArrow Table with the query results.

        Raises:
            QueryError: If dataset not found or query fails.
            ValueError: If dataset name or column names are invalid.
        """
        if not SAFE_IDENTIFIER_RE.match(dataset_name):
            raise ValueError(f"Invalid dataset name '{dataset_name}'")
        for col in columns:
            if not SAFE_IDENTIFIER_RE.match(col):
                raise ValueError(f"Invalid column name '{col}'")

        try:
            source = self._storage.scan_dataset(dataset_name)
        except StorageError as exc:
            raise QueryError(
                error_code=ErrorCode.QUERY_NO_RESULTS,
                message=f"Failed to read dataset '{dataset_name}': {exc}",
            ) from exc

        if self._session_manager is not None:
            managed = self._session_manager.acquire()
            try:
                self._register_dataset(managed.conn, dataset_name, source)
                rel = managed.conn.table(dataset_name)
                rel = rel.select(*columns)
                if where:
                    rel = rel.filter(where)
                rel = rel.limit(limit)
                result = rel.arrow()
            finally:
                managed.release()
        else:
            with create_duckdb_session(storage_config=self._storage_config) as conn:
                self._register_dataset(conn, dataset_name, source)
                rel = conn.table(dataset_name)
                rel = rel.select(*columns)
                if where:
                    rel = rel.filter(where)
                rel = rel.limit(limit)
                result = rel.arrow()

        return result
