"""Regression: DuckLake metadata queries bind params ($1..$N), never interpolate.

Roadmap v1.8.0 #11 (prepared statements) + duckdb-optimization-plan #6.

DuckDB ``EXECUTE name(...)`` cannot accept bound parameters (binder raises
"Unexpected prepared parameter"), so explicit PREPARE/EXECUTE is incompatible
with safe parameter binding. "Prepared statements" for the DuckLake metadata
table are therefore implemented as **parameterized execution**: SQL templates
with ``$N`` placeholders plus positional params. DuckDB caches the query plan
for repeated parameterized SQL automatically.

This test guards that invariant: the metadata INSERT/SELECT/DELETE must pass
values as bound params, never interpolated into SQL text (SQL-injection-safe).
"""

from __future__ import annotations

from typing import Any


class _Rel:
    """Fake DuckDB result relation."""

    def fetchone(self) -> tuple[int]:
        return (0,)  # SELECT COUNT(*) -> 0 rows

    def fetchall(self) -> list[tuple]:
        return []  # no expired rows in cleanup


class _RecordingConn:
    """Records every execute(sql, params) call for assertion."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, Any]] = []

    def execute(self, sql: str, params: Any = None) -> _Rel:
        self.calls.append((sql, params))
        return _Rel()


class TestDuckLakePreparedStatements:
    """Metadata table queries must bind params via $N placeholders."""

    def test_materialize_binds_metadata_insert_params(self) -> None:
        from arrow_lake.query.ducklake_workspace import DuckLakeWorkspace

        ws = DuckLakeWorkspace(ttl_days=7)
        conn = _RecordingConn()
        ws.materialize(conn, "SELECT 1 AS x", "vw_test")

        inserts = [
            c for c in conn.calls
            if "INSERT" in c[0] and "_ducklake_metadata" in c[0]
        ]
        assert len(inserts) == 1
        sql, params = inserts[0]
        # 4 placeholders, 4 positional params
        assert "$1" in sql and "$4" in sql
        assert params is not None and len(params) == 4
        # view_name bound as param, NOT interpolated into the INSERT text
        assert "vw_test" not in sql

    def test_materialize_params_include_view_name_and_counts(self) -> None:
        from arrow_lake.query.ducklake_workspace import DuckLakeWorkspace

        ws = DuckLakeWorkspace(ttl_days=3)
        conn = _RecordingConn()
        ws.materialize(conn, "SELECT 1 AS x", "vw_abc")

        insert_params = next(
            p for sql, p in conn.calls
            if "INSERT" in sql and "_ducklake_metadata" in sql
        )
        view_name, created_at, expires_at, row_count = insert_params
        assert view_name == "vw_abc"
        assert row_count == 0
        # expires_at is created_at + ttl_days
        delta = expires_at - created_at
        assert delta.days == 3

    def test_cleanup_binds_select_param(self) -> None:
        from arrow_lake.query.ducklake_workspace import DuckLakeWorkspace

        ws = DuckLakeWorkspace(ttl_days=7)
        conn = _RecordingConn()
        ws.cleanup_expired(conn)

        selects = [
            c for c in conn.calls
            if "expires_at" in c[0] and "SELECT" in c[0]
        ]
        assert len(selects) == 1
        sql, params = selects[0]
        assert "$1" in sql  # 'now' bound, not interpolated
        assert params is not None and len(params) == 1

    def test_no_metadata_value_interpolated(self) -> None:
        """Bound timestamp/row params must not leak into the INSERT SQL text."""
        from arrow_lake.query.ducklake_workspace import DuckLakeWorkspace

        ws = DuckLakeWorkspace(ttl_days=7)
        conn = _RecordingConn()
        ws.materialize(conn, "SELECT 1 AS x", "vw_x")
        for sql, params in conn.calls:
            if "INSERT" in sql and "_ducklake_metadata" in sql:
                assert params is not None
                # The INSERT template is static; no ISO-timestamp literal leaks
                assert "T00:00" not in sql
                values_clause = sql.split("VALUES")[-1]
                assert "::" not in values_clause
