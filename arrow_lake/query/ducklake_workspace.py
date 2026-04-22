"""DuckLake workspace management for materialized views.

Provides DuckLakeWorkspace for managing DuckLake materialized views:
- attach/detach DuckLake storage
- materialize SQL results with row budget check
- cleanup expired materialized data based on TTL
- list active materialized tables

The _metadata table tracks materialized view lifecycle:
```
table_name VARCHAR, created_at TIMESTAMP, expires_at TIMESTAMP, row_count BIGINT
```
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

import pyarrow as pa

from arrow_lake.exceptions import ArrowLakeError, ErrorCode

logger = logging.getLogger(__name__)

__all__ = ["DuckLakeWorkspace"]


class DuckLakeWorkspace:
    """Manages DuckLake materialized views with TTL and row budget.

    Args:
        ttl_days: Default TTL in days for materialized data.
        max_join_rows: Maximum row budget for materialize() calls.
        metadata_table: Name of the internal metadata tracking table.
    """

    def __init__(
        self,
        *,
        ttl_days: int = 7,
        max_join_rows: int = 1_000_000,
        metadata_table: str = "_ducklake_metadata",
    ) -> None:
        self._ttl_days = ttl_days
        self._max_join_rows = max_join_rows
        self._metadata_table = metadata_table

    @property
    def metadata_schema(self) -> pa.Schema:
        """Schema for the internal metadata tracking table."""
        return pa.schema(
            [
                ("table_name", pa.string()),
                ("created_at", pa.timestamp("us", tz="UTC")),
                ("expires_at", pa.timestamp("us", tz="UTC")),
                ("row_count", pa.int64()),
            ]
        )

    def _ensure_metadata_table(self, conn: object) -> None:
        """Create _metadata table if it doesn't exist."""
        try:
            conn.execute(
                f"SELECT 1 FROM {self._metadata_table} LIMIT 0"
            )
        except Exception:
            conn.execute(
                f"CREATE TABLE {self._metadata_table} ("
                f"table_name VARCHAR, "
                f"created_at TIMESTAMP, "
                f"expires_at TIMESTAMP, "
                f"row_count BIGINT"
                f")"
            )

    def materialize(
        self,
        conn: object,
        sql: str,
        view_name: str,
    ) -> int:
        """Materialize a SQL query result as a DuckLake table.

        Checks row budget before materializing.

        Args:
            conn: Active DuckDB connection.
            sql: SQL query to materialize.
            view_name: Name for the materialized table.

        Returns:
            Number of rows materialized.

        Raises:
            ArrowLakeError: If row count exceeds budget.
        """
        # Check row budget
        count_sql = f"SELECT COUNT(*) FROM ({sql}) AS _count_check"
        try:
            row_count = conn.execute(count_sql).fetchone()[0]
        except duckdb.Error as exc:
            raise ArrowLakeError(
                ErrorCode.OLAP_QUERY_FAILED,
                f"Failed to count rows for materialization: {exc}",
            ) from exc

        if row_count > self._max_join_rows:
            raise ArrowLakeError(
                ErrorCode.OLAP_QUERY_FAILED,
                f"Materialization row count ({row_count}) exceeds budget ({self._max_join_rows})",
            )

        # Create the materialized table
        conn.execute(
            f"CREATE OR REPLACE TABLE {view_name} AS {sql}"
        )

        # Record metadata
        now = datetime.now(UTC)
        expires = now + timedelta(days=self._ttl_days)
        self._ensure_metadata_table(conn)
        conn.execute(
            f"INSERT INTO {self._metadata_table} VALUES "
            f"('{view_name}', '{now.isoformat()}', '{expires.isoformat()}', {row_count})"
        )

        return row_count

    def cleanup_expired(self, conn: object) -> list[str]:
        """Drop materialized views that have exceeded their TTL.

        Args:
            conn: Active DuckDB connection.

        Returns:
            List of dropped table names.
        """
        self._ensure_metadata_table(conn)
        now = datetime.now(UTC).isoformat()

        try:
            expired = conn.execute(
                f"SELECT table_name FROM {self._metadata_table} WHERE expires_at < '{now}'"
            ).fetchall()
        except Exception:
            return []

        dropped: list[str] = []
        for (table_name,) in expired:
            try:
                conn.execute(f"DROP TABLE IF EXISTS {table_name}")
                conn.execute(
                    f"DELETE FROM {self._metadata_table} WHERE table_name = '{table_name}'"
                )
                dropped.append(table_name)
            except Exception as exc:
                logger.warning("Failed to drop expired table %s: %s", table_name, exc)

        return dropped

    def list_tables(self, conn: object) -> list[str]:
        """List all active materialized tables.

        Args:
            conn: Active DuckDB connection.

        Returns:
            List of materialized table names.
        """
        self._ensure_metadata_table(conn)
        try:
            rows = conn.execute(
                f"SELECT table_name FROM {self._metadata_table}"
            ).fetchall()
            return [row[0] for row in rows]
        except Exception:
            return []
