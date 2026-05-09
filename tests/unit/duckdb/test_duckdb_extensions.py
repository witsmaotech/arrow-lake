"""Tests for DuckDB extension installation, loading, and basic SQL operations.

M0a Day 2 — validates lance extension availability for the current environment.
"""

from __future__ import annotations

import duckdb
import pytest

pytest.importorskip("lance", reason="lance not installed")


class TestLanceExtensionAvailable:
    """Test that lance extension can be installed and loaded."""

    def test_lance_extension_installs(self) -> None:
        """INSTALL lance should succeed without error."""
        conn = duckdb.connect()
        conn.execute("INSTALL lance;")
        conn.close()

    def test_lance_extension_loads(self) -> None:
        """LOAD lance should succeed after install."""
        conn = duckdb.connect()
        conn.execute("INSTALL lance; LOAD lance;")
        conn.close()

    def test_lance_scan_function_exists(self) -> None:
        """__lance_scan function should be callable after loading lance extension."""
        conn = duckdb.connect()
        conn.execute("INSTALL lance; LOAD lance;")
        # Check function existence via duckdb_functions
        result = conn.execute(
            "SELECT count(*) FROM duckdb_functions() WHERE function_name LIKE '%lance%'"
        ).fetchone()
        assert result[0] > 0
        conn.close()

    def test_lance_extension_persists_in_session(self) -> None:
        """Once loaded, lance functions should remain available in the session."""
        conn = duckdb.connect()
        conn.execute("INSTALL lance; LOAD lance;")
        # Multiple function lookups should work
        conn.execute(
            "SELECT count(*) FROM duckdb_functions() WHERE function_name LIKE '%lance%'"
        ).fetchone()
        conn.execute(
            "SELECT count(*) FROM duckdb_functions() WHERE function_name LIKE '%lance%'"
        ).fetchone()
        conn.close()


class TestDuckDBSessionLoadsLance:
    """Test that DuckDBSession loads lance extension by default."""

    def test_session_loads_lance(self) -> None:
        """DuckDBSession should load lance extension on enter."""
        from arrow_lake.query._db import DuckDBSession

        with DuckDBSession() as conn:
            result = conn.execute(
                "SELECT count(*) FROM duckdb_functions() WHERE function_name LIKE '%lance%'"
            ).fetchone()
            assert result[0] > 0

    def test_session_lance_scan_on_real_data(self) -> None:
        """__lance_scan should work on a real Lance dataset via DuckDBSession."""
        import lance
        import pyarrow as pa
        from arrow_lake.query._db import DuckDBSession

        # Create a small Lance dataset
        table = pa.table({"id": [1, 2, 3], "text": ["a", "b", "c"]})
        tmp_dir = "/tmp/test_lance_scan_ext"
        lance.write_dataset(table, tmp_dir)

        try:
            with DuckDBSession() as conn:
                result = conn.execute(
                    f"SELECT count(*) FROM __lance_scan('{tmp_dir}', explain_verbose := false)"
                ).fetchone()
                assert result[0] == 3
        finally:
            import shutil

            shutil.rmtree(tmp_dir, ignore_errors=True)


class TestColumnDiscovery:
    """Test that lance extension enables column discovery via information_schema."""

    def test_describe_lance_dataset(self) -> None:
        """DESCRIBE should work on __lance_scan results."""
        import lance
        import pyarrow as pa
        from arrow_lake.query._db import DuckDBSession

        table = pa.table({"id": [1, 2], "name": ["alice", "bob"], "score": [3.14, 2.71]})
        tmp_dir = "/tmp/test_lance_describe"
        lance.write_dataset(table, tmp_dir)

        try:
            with DuckDBSession() as conn:
                conn.execute(
                    f"CREATE VIEW v AS SELECT * FROM __lance_scan('{tmp_dir}', explain_verbose := false)"
                )
                cols = conn.execute("DESCRIBE v").fetchall()
                col_names = [row[0] for row in cols]
                assert "id" in col_names
                assert "name" in col_names
                assert "score" in col_names
        finally:
            import shutil

            shutil.rmtree(tmp_dir, ignore_errors=True)
