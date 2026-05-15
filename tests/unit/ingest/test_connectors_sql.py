"""Unit tests for SQL connector — Daft Phase 2, Sprint 5."""

from __future__ import annotations

import sqlite3
import tempfile
from pathlib import Path

import pytest
from arrow_lake.exceptions import IngestError
from arrow_lake.ingest.connectors_sql import SqlConnector, _validate_sql_readonly


class TestValidateSqlReadonly:
    def test_select_allowed(self) -> None:
        _validate_sql_readonly("SELECT * FROM t")

    def test_with_cte_allowed(self) -> None:
        _validate_sql_readonly("WITH cte AS (SELECT 1) SELECT * FROM cte")

    def test_insert_rejected(self) -> None:
        with pytest.raises(IngestError, match="Only SELECT|forbidden"):
            _validate_sql_readonly("INSERT INTO t VALUES (1)")

    def test_update_rejected(self) -> None:
        with pytest.raises(IngestError, match="Only SELECT|forbidden"):
            _validate_sql_readonly("UPDATE t SET x = 1")

    def test_delete_rejected(self) -> None:
        with pytest.raises(IngestError, match="Only SELECT|forbidden"):
            _validate_sql_readonly("DELETE FROM t")

    def test_drop_rejected(self) -> None:
        with pytest.raises(IngestError, match="Only SELECT|forbidden"):
            _validate_sql_readonly("DROP TABLE t")

    def test_non_select_prefix_rejected(self) -> None:
        with pytest.raises(IngestError, match="Only SELECT"):
            _validate_sql_readonly("DESCRIBE t")

    def test_select_with_insert_subquery_rejected(self) -> None:
        with pytest.raises(IngestError, match="forbidden"):
            _validate_sql_readonly("SELECT * FROM t; INSERT INTO t VALUES (1)")


class TestSqlConnectorRead:
    def test_read_from_sqlite(self) -> None:
        db = tempfile.mktemp(suffix=".db")
        conn = sqlite3.connect(db)
        conn.execute("CREATE TABLE items(id INTEGER, name TEXT)")
        conn.execute("INSERT INTO items VALUES (1, 'a'), (2, 'b'), (3, 'c')")
        conn.commit()
        conn.close()

        try:
            connector = SqlConnector(f"sqlite:///{db}")
            df = connector.read("SELECT * FROM items")
            table = df.to_arrow()
            assert table.num_rows == 3
            assert "id" in table.column_names
            assert "name" in table.column_names
        finally:
            Path(db).unlink(missing_ok=True)

    def test_invalid_connection_raises(self) -> None:
        connector = SqlConnector("sqlite:///nonexistent/path/db.sqlite")
        with pytest.raises(IngestError, match="SQL read failed"):
            connector.read("SELECT * FROM t")

    def test_safe_url_masks_password(self) -> None:
        connector = SqlConnector("postgresql://user:secret@dbhost:5432/mydb")
        safe = connector._safe_url()
        assert "secret" not in safe
        assert "***" in safe

    def test_safe_url_no_credentials(self) -> None:
        connector = SqlConnector("sqlite:///test.db")
        assert connector._safe_url() == "sqlite:///test.db"
