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

import duckdb
import pyarrow as pa

from arrow_lake.exceptions import ArrowLakeError, ErrorCode
from arrow_lake.validation import SAFE_IDENTIFIER_RE, validate_identifier, validate_sql_safety

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
        validate_identifier(metadata_table)
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
        """Create temp _metadata table if it doesn't exist."""
        try:
            conn.execute(
                f"SELECT 1 FROM {self._metadata_table} LIMIT 0"  # nosec B608
            )
        except duckdb.CatalogException:
            conn.execute(
                f"CREATE TEMP TABLE IF NOT EXISTS {self._metadata_table} ("  # nosec B608
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
        *,
        index_columns: list[str] | None = None,
    ) -> int:
        """Materialize a SQL query result as a DuckLake table.

        Checks row budget before materializing. Optionally creates ART indexes
        on specified columns for faster point lookups.

        Args:
            conn: Active DuckDB connection.
            sql: SQL query to materialize.
            view_name: Name for the materialized table.
            index_columns: Columns to create ART indexes on (None = no indexes).

        Returns:
            Number of rows materialized.

        Raises:
            ArrowLakeError: If row count exceeds budget.
            ValueError: If view_name is not a safe SQL identifier.
        """
        validate_identifier(view_name)
        validate_sql_safety(sql)

        # Create the materialized table directly — avoids double query
        try:
            conn.execute(
                f"CREATE OR REPLACE TABLE {view_name} AS {sql}"  # nosec B608
            )
        except duckdb.Error as exc:
            raise ArrowLakeError(
                ErrorCode.OLAP_QUERY_FAILED,
                f"Failed to materialize view '{view_name}': {exc}",
            ) from exc

        # Count rows and check budget from the materialized table
        try:
            row_count = conn.execute(
                f"SELECT COUNT(*) FROM {view_name}"  # nosec B608
            ).fetchone()[0]
        except duckdb.Error as exc:
            raise ArrowLakeError(
                ErrorCode.OLAP_QUERY_FAILED,
                f"Failed to count materialized rows: {exc}",
            ) from exc

        if row_count > self._max_join_rows:
            # Drop the oversized table to avoid leaving orphan data
            conn.execute(f"DROP TABLE IF EXISTS {view_name}")  # nosec B608
            raise ArrowLakeError(
                ErrorCode.OLAP_QUERY_FAILED,
                f"Materialization row count ({row_count}) exceeds budget ({self._max_join_rows})",
            )
        now = datetime.now(UTC)
        expires = now + timedelta(days=self._ttl_days)
        self._ensure_metadata_table(conn)
        conn.execute(
            f"INSERT INTO {self._metadata_table} VALUES ($1, $2, $3, $4)",  # nosec B608
            [view_name, now, expires, row_count],
        )

        # Create ART indexes on requested columns for faster point lookups
        if index_columns:
            for col in index_columns:
                if SAFE_IDENTIFIER_RE.match(col):
                    idx_name = f"idx_{view_name}_{col}"
                    try:
                        conn.execute(
                            f"CREATE INDEX IF NOT EXISTS {idx_name} ON {view_name}({col})"  # nosec B608
                        )
                    except duckdb.Error as exc:
                        logger.warning("Failed to create index %s on %s: %s", idx_name, view_name, exc)

        return row_count

    def cleanup_expired(self, conn: object) -> list[str]:
        """Drop materialized views that have exceeded their TTL.

        Args:
            conn: Active DuckDB connection.

        Returns:
            List of dropped table names.
        """
        self._ensure_metadata_table(conn)
        now = datetime.now(UTC)

        try:
            expired = conn.execute(
                f"SELECT table_name FROM {self._metadata_table} WHERE expires_at < $1",  # nosec B608
                [now],
            ).fetchall()
        except duckdb.Error:
            return []

        dropped: list[str] = []
        for (table_name,) in expired:
            if not SAFE_IDENTIFIER_RE.match(table_name):
                logger.warning("Skipping invalid table name in metadata: %r", table_name)
                continue
            try:
                conn.execute(f"DROP TABLE IF EXISTS {table_name}")  # nosec B608
                conn.execute(
                    f"DELETE FROM {self._metadata_table} WHERE table_name = $1",  # nosec B608
                    [table_name],
                )
                dropped.append(table_name)
            except duckdb.Error as exc:
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
                f"SELECT table_name FROM {self._metadata_table}"  # nosec B608
            ).fetchall()
            return [row[0] for row in rows]
        except duckdb.Error:
            return []

    def list_views(self, conn: object) -> list[dict]:
        """List materialized views with lifecycle metadata.

        Args:
            conn: Active DuckDB connection.

        Returns:
            List of dicts: ``{table_name, created_at, expires_at, row_count}``.

        Note:
            The ``_metadata`` table is TEMP (session-scoped) — across fresh
            sessions this returns ``[]`` even if DuckLake tables persist on
            the pooled connection. Cross-session durability requires a
            persistent DuckLake catalog (not configured by default).
        """
        self._ensure_metadata_table(conn)
        try:
            rows = conn.execute(
                f"SELECT table_name, created_at, expires_at, row_count "  # nosec B608
                f"FROM {self._metadata_table} ORDER BY created_at DESC"
            ).fetchall()
        except duckdb.Error:
            return []
        out: list[dict] = []
        for name, created, expires, rc in rows:
            out.append({
                "view_name": name,
                "created_at": created.isoformat() if hasattr(created, "isoformat") else str(created),
                "expires_at": expires.isoformat() if hasattr(expires, "isoformat") else str(expires),
                "row_count": rc,
            })
        return out

    def drop_view(self, conn: object, view_name: str) -> bool:
        """Drop a single materialized view by name (safe identifier validated).

        Returns:
            True if dropped, False if not found.
        """
        validate_identifier(view_name)
        self._ensure_metadata_table(conn)
        try:
            conn.execute(f"DROP TABLE IF EXISTS {view_name}")  # nosec B608
            conn.execute(
                f"DELETE FROM {self._metadata_table} WHERE table_name = $1",  # nosec B608
                [view_name],
            )
            return True
        except duckdb.Error as exc:
            logger.warning("Failed to drop materialized view %s: %s", view_name, exc)
            return False
