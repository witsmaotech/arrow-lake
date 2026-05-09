"""Extra tests for DuckLakeWorkspace — complement to test_ducklake_workspace.py.

Covers edge cases not in the primary test file:
- Real TTL expiry with actual DuckDB
- SQL injection prevention in SQL body (validate_sql_safety)
- cleanup_expired graceful error handling
- list_tables graceful error handling
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock

import duckdb
import pytest
from arrow_lake.query.ducklake_workspace import DuckLakeWorkspace

# Unique table names to avoid collisions between tests
_META_TABLE = "_test_ducklake_meta_extra"


def _cleanup(conn: duckdb.DuckDBPyConnection) -> None:
    """Drop the test metadata table and any leftover materialized tables."""
    try:
        tables = conn.execute(
            f"SELECT table_name FROM {_META_TABLE}"
        ).fetchall()
        for (name,) in tables:
            conn.execute(f"DROP TABLE IF EXISTS {name}")
    except duckdb.Error:
        pass
    conn.execute(f"DROP TABLE IF EXISTS {_META_TABLE}")


class TestRealTTLExpiry:
    """Test cleanup_expired with a real DuckDB connection."""

    def test_cleanup_expired_drops_past_ttl(self) -> None:
        """A table whose expires_at is in the past should be dropped."""
        conn = duckdb.connect()
        try:
            ws = DuckLakeWorkspace(ttl_days=0, metadata_table=_META_TABLE)

            # Create metadata table and an actual data table
            conn.execute(
                f"CREATE TABLE {_META_TABLE} ("
                f"table_name VARCHAR, "
                f"created_at TIMESTAMP, "
                f"expires_at TIMESTAMP, "
                f"row_count BIGINT"
                f")"
            )
            data_table = "test_ttl_expired_view"
            conn.execute(
                f"CREATE TABLE {data_table} AS SELECT 1 AS col"
            )
            now = datetime.now(UTC)
            past = now - timedelta(days=1)
            conn.execute(
                f"INSERT INTO {_META_TABLE} VALUES ($1, $2, $3, $4)",
                [data_table, past.isoformat(), past.isoformat(), 1],
            )

            dropped = ws.cleanup_expired(conn)
            assert data_table in dropped

            # Verify the table was actually dropped
            with pytest.raises(duckdb.CatalogException):
                conn.execute(f"SELECT * FROM {data_table}")
        finally:
            _cleanup(conn)
            conn.close()

    def test_cleanup_expired_keeps_future_ttl(self) -> None:
        """A table whose expires_at is in the future should NOT be dropped."""
        conn = duckdb.connect()
        try:
            ws = DuckLakeWorkspace(ttl_days=0, metadata_table=_META_TABLE)

            conn.execute(
                f"CREATE TABLE {_META_TABLE} ("
                f"table_name VARCHAR, "
                f"created_at TIMESTAMP, "
                f"expires_at TIMESTAMP, "
                f"row_count BIGINT"
                f")"
            )
            data_table = "test_ttl_future_view"
            conn.execute(
                f"CREATE TABLE {data_table} AS SELECT 1 AS col"
            )
            now = datetime.now(UTC)
            future = now + timedelta(days=365)
            conn.execute(
                f"INSERT INTO {_META_TABLE} VALUES ($1, $2, $3, $4)",
                [data_table, now.isoformat(), future.isoformat(), 1],
            )

            dropped = ws.cleanup_expired(conn)
            assert data_table not in dropped

            # Verify the table still exists
            result = conn.execute(f"SELECT col FROM {data_table}").fetchone()
            assert result == (1,)
        finally:
            _cleanup(conn)
            conn.close()


class TestSQLInjectionInSQLBody:
    """Test validate_sql_safety rejects dangerous keywords in SQL parameter."""

    def test_materialize_rejects_drop_in_sql(self) -> None:
        """SQL body containing DROP should raise ValueError."""
        from arrow_lake.validation import validate_sql_safety

        with pytest.raises(ValueError, match="Dangerous SQL keyword"):
            validate_sql_safety("SELECT * FROM t1; DROP TABLE t2")

    def test_materialize_rejects_delete_in_sql(self) -> None:
        """SQL body containing DELETE should raise ValueError."""
        from arrow_lake.validation import validate_sql_safety

        with pytest.raises(ValueError, match="Dangerous SQL keyword"):
            validate_sql_safety("DELETE FROM users WHERE 1=1")

    def test_materialize_rejects_insert_in_sql(self) -> None:
        """SQL body containing INSERT should raise ValueError."""
        from arrow_lake.validation import validate_sql_safety

        with pytest.raises(ValueError, match="Dangerous SQL keyword"):
            validate_sql_safety("INSERT INTO t1 VALUES (1)")

    def test_materialize_rejects_semicolons_in_sql(self) -> None:
        """SQL body containing semicolons should raise ValueError."""
        from arrow_lake.validation import validate_sql_safety

        with pytest.raises(ValueError, match="Semicolons not allowed"):
            validate_sql_safety("SELECT 1; SELECT 2")

    def test_materialize_accepts_safe_select(self) -> None:
        """Simple SELECT should pass validation."""
        from arrow_lake.validation import validate_sql_safety

        validate_sql_safety("SELECT * FROM t1 JOIN t2 ON t1.id = t2.id")

    def test_materialize_rejects_union_in_sql(self) -> None:
        """SQL body containing UNION should raise ValueError."""
        from arrow_lake.validation import validate_sql_safety

        with pytest.raises(ValueError, match="Dangerous SQL keyword"):
            validate_sql_safety("SELECT 1 UNION SELECT 2")


class TestCleanupExpiredErrorHandling:
    """Test cleanup_expired graceful error handling on duckdb.Error."""

    def test_cleanup_expired_handles_query_error(self) -> None:
        """duckdb.Error on the expired query should return empty list."""
        ws = DuckLakeWorkspace(metadata_table=_META_TABLE)
        mock_conn = MagicMock()

        # First call is _ensure_metadata_table (SELECT ... LIMIT 0)
        # which should succeed; second call is the expired query
        # which should raise duckdb.Error.
        call_count = 0

        def _execute_side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                # _ensure_metadata_table SELECT succeeds, but then
                # raises CatalogException so CREATE TABLE is attempted
                raise duckdb.CatalogException("does not exist")
            if call_count == 2:
                # CREATE TABLE succeeds
                result = MagicMock()
                return result
            if call_count == 3:
                # The expired SELECT query raises
                raise duckdb.Error("connection lost")
            result = MagicMock()
            return result

        mock_conn.execute.side_effect = _execute_side_effect

        result = ws.cleanup_expired(mock_conn)
        assert result == []

    def test_cleanup_expired_handles_drop_error(self) -> None:
        """duckdb.Error on DROP should log warning and continue."""
        ws = DuckLakeWorkspace(metadata_table=_META_TABLE)
        mock_conn = MagicMock()

        # First call (ensure metadata) — no error
        # Second call (query expired) — returns one expired table
        # Third call (drop table) — raises duckdb.Error
        # Fourth call (delete from metadata) — raises duckdb.Error
        call_count = 0

        def _execute_side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 3:
                raise duckdb.Error("permission denied")
            if call_count == 4:
                raise duckdb.Error("permission denied")
            result = MagicMock()
            result.fetchall.return_value = [("safe_table",)]
            return result

        mock_conn.execute.side_effect = _execute_side_effect

        result = ws.cleanup_expired(mock_conn)
        # Table should not be in dropped list because DROP failed
        assert result == []


class TestListTablesErrorHandling:
    """Test list_tables graceful error handling on duckdb.Error."""

    def test_list_tables_handles_error(self) -> None:
        """duckdb.Error should return empty list without crashing."""
        ws = DuckLakeWorkspace(metadata_table=_META_TABLE)
        mock_conn = MagicMock()

        # First call is _ensure_metadata_table (SELECT ... LIMIT 0)
        # which should raise CatalogException; second call is CREATE TABLE;
        # third call is the actual SELECT that should raise duckdb.Error.
        call_count = 0

        def _execute_side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise duckdb.CatalogException("does not exist")
            if call_count == 2:
                # CREATE TABLE succeeds
                result = MagicMock()
                return result
            if call_count == 3:
                # The list_tables SELECT query raises
                raise duckdb.Error("catalog error")
            result = MagicMock()
            return result

        mock_conn.execute.side_effect = _execute_side_effect

        result = ws.list_tables(mock_conn)
        assert result == []
