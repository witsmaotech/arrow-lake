"""Tests for DuckLake workspace management.

M0a Day 3 — TDD RED phase.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from arrow_lake.exceptions import ArrowLakeError, ErrorCode


class TestDuckLakeWorkspaceInit:
    """Test workspace construction."""

    def test_init_with_defaults(self) -> None:
        from arrow_lake.query.ducklake_workspace import DuckLakeWorkspace

        ws = DuckLakeWorkspace()
        assert ws is not None

    def test_init_with_custom_config(self) -> None:
        from arrow_lake.query.ducklake_workspace import DuckLakeWorkspace

        ws = DuckLakeWorkspace(ttl_days=14, max_join_rows=500_000)
        assert ws is not None


class TestDuckLakeWorkspaceMetadata:
    """Test _metadata table schema and operations."""

    def test_metadata_schema_has_required_columns(self) -> None:
        """_metadata table must have table_name, created_at, expires_at, row_count."""
        from arrow_lake.query.ducklake_workspace import DuckLakeWorkspace

        ws = DuckLakeWorkspace()
        schema = ws.metadata_schema
        field_names = {f.name for f in schema}
        assert "table_name" in field_names
        assert "created_at" in field_names
        assert "expires_at" in field_names
        assert "row_count" in field_names


class TestDuckLakeWorkspaceMaterialize:
    """Test materialize() with row budget check."""

    def test_materialize_within_budget(self) -> None:
        """materialize should succeed when row count is within budget."""
        from arrow_lake.query.ducklake_workspace import DuckLakeWorkspace

        ws = DuckLakeWorkspace(max_join_rows=100)
        mock_conn = MagicMock()
        mock_conn.execute.return_value.fetchone.return_value = (50,)

        ws.materialize(mock_conn, "SELECT * FROM t1 JOIN t2", "my_materialized")
        mock_conn.execute.assert_called()

    def test_materialize_exceeds_budget_raises(self) -> None:
        """materialize should raise when row count exceeds budget."""
        from arrow_lake.query.ducklake_workspace import DuckLakeWorkspace

        ws = DuckLakeWorkspace(max_join_rows=10)
        mock_conn = MagicMock()
        mock_conn.execute.return_value.fetchone.return_value = (50,)

        with pytest.raises(ArrowLakeError, match="exceeds budget"):
            ws.materialize(mock_conn, "SELECT * FROM t1 JOIN t2", "my_materialized")


class TestDuckLakeWorkspaceCleanup:
    """Test cleanup of expired materialized views."""

    def test_cleanup_expired(self) -> None:
        """cleanup_expired should drop views past their TTL."""
        from arrow_lake.query.ducklake_workspace import DuckLakeWorkspace

        ws = DuckLakeWorkspace(ttl_days=0)
        mock_conn = MagicMock()
        # Simulate expired entries: list of single-element tuples (table_name,)
        mock_conn.execute.return_value.fetchall.return_value = [
            ("old_view",),
        ]

        ws.cleanup_expired(mock_conn)
        # Should have called DROP VIEW
        drop_calls = [
            call.args[0] for call in mock_conn.execute.call_args_list if "DROP" in str(call)
        ]
        assert len(drop_calls) > 0

    def test_cleanup_skips_non_expired(self) -> None:
        """cleanup_expired should not drop views within TTL."""
        from arrow_lake.query.ducklake_workspace import DuckLakeWorkspace

        ws = DuckLakeWorkspace(ttl_days=365)
        mock_conn = MagicMock()
        mock_conn.execute.return_value.fetchall.return_value = []

        ws.cleanup_expired(mock_conn)
        # No DROP should be called when there are no entries
        drop_calls = [
            call for call in mock_conn.execute.call_args_list if "DROP" in str(call)
        ]
        assert len(drop_calls) == 0


class TestDuckLakeWorkspaceSQLInjection:
    """Test SQL injection prevention via validate_identifier."""

    @pytest.fixture()
    def ws(self):
        from arrow_lake.query.ducklake_workspace import DuckLakeWorkspace
        return DuckLakeWorkspace()

    def test_materialize_rejects_sql_injection_view_name(self, ws) -> None:
        """view_name containing SQL injection must raise ValueError."""
        import duckdb
        conn = duckdb.connect()
        try:
            with pytest.raises(ValueError, match="Invalid identifier"):
                ws.materialize(conn, "SELECT 1", "x; DROP TABLE _ducklake_metadata; --")
        finally:
            conn.close()

    def test_materialize_rejects_special_chars_in_view_name(self, ws) -> None:
        """view_name with spaces or quotes must raise ValueError."""
        import duckdb
        conn = duckdb.connect()
        try:
            with pytest.raises(ValueError, match="Invalid identifier"):
                ws.materialize(conn, "SELECT 1", "my view")
            with pytest.raises(ValueError, match="Invalid identifier"):
                ws.materialize(conn, "SELECT 1", "my'table")
        finally:
            conn.close()

    def test_materialize_accepts_valid_identifier(self, ws) -> None:
        """Valid identifiers should pass through without error."""
        import duckdb
        conn = duckdb.connect()
        try:
            ws.materialize(conn, "SELECT 42 AS col", "valid_view_name")
            result = conn.execute("SELECT col FROM valid_view_name").fetchone()
            assert result == (42,)
        finally:
            conn.execute("DROP TABLE IF EXISTS valid_view_name")
            conn.close()

    def test_cleanup_rejects_injected_table_names(self, ws) -> None:
        """cleanup_expired must validate fetched table names."""
        import duckdb
        conn = duckdb.connect()
        try:
            ws._ensure_metadata_table(conn)
            conn.execute(
                "INSERT INTO _ducklake_metadata VALUES "
                "('x; DROP TABLE', '2024-01-01T00:00:00+00:00', '2023-01-01T00:00:00+00:00', 1)"
            )
            # Should log warning but not crash
            dropped = ws.cleanup_expired(conn)
            assert dropped == []
        finally:
            conn.execute("DROP TABLE IF EXISTS _ducklake_metadata")
            conn.close()

    def test_init_rejects_invalid_metadata_table(self) -> None:
        """metadata_table with injection must raise ValueError at construction."""
        from arrow_lake.query.ducklake_workspace import DuckLakeWorkspace
        with pytest.raises(ValueError, match="Invalid identifier"):
            DuckLakeWorkspace(metadata_table="x; DROP TABLE users; --")


class TestDuckLakeWorkspaceListTables:
    """Test listing materialized tables."""

    def test_list_tables_returns_names(self) -> None:
        """list_tables should return list of materialized table names."""
        from arrow_lake.query.ducklake_workspace import DuckLakeWorkspace

        ws = DuckLakeWorkspace()
        mock_conn = MagicMock()
        mock_conn.execute.return_value.fetchall.return_value = [
            ("view_a",),
            ("view_b",),
        ]

        tables = ws.list_tables(mock_conn)
        assert "view_a" in tables
        assert "view_b" in tables

    def test_list_tables_empty(self) -> None:
        """list_tables should return empty list when no tables."""
        from arrow_lake.query.ducklake_workspace import DuckLakeWorkspace

        ws = DuckLakeWorkspace()
        mock_conn = MagicMock()
        mock_conn.execute.return_value.fetchall.return_value = []

        tables = ws.list_tables(mock_conn)
        assert tables == []
