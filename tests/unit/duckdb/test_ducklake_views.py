"""Tests for DuckLake materialized view management — list/drop (v1.9.2 批6-P2).

Covers the new DuckLakeWorkspace.list_views / drop_view surface, the
MaterializeRequest model validation (view_name whitelist + SELECT-only),
and the bridge/facade ducklake-enabled gating (503 path).
"""

from __future__ import annotations

import duckdb
import pytest

from arrow_lake.exceptions import ArrowLakeError
from arrow_lake.query.ducklake_workspace import DuckLakeWorkspace


def _conn() -> duckdb.DuckDBPyConnection:
    """Fresh in-memory DuckDB connection."""
    return duckdb.connect()


class TestListViews:
    """list_views returns lifecycle metadata for materialized views."""

    def test_list_views_empty_when_nothing_materialized(self) -> None:
        ws = DuckLakeWorkspace(ttl_days=7)
        conn = _conn()
        assert ws.list_views(conn) == []

    def test_list_views_returns_materialized_rows(self) -> None:
        ws = DuckLakeWorkspace(ttl_days=7, max_join_rows=1000)
        conn = _conn()
        ws.materialize(conn, "SELECT 1 AS x", "mv_one")
        views = ws.list_views(conn)
        assert len(views) == 1
        v = views[0]
        assert v["view_name"] == "mv_one"
        assert v["row_count"] == 1
        assert v["created_at"] is not None
        assert v["expires_at"] is not None

    def test_list_views_multiple_ordered_by_created_desc(self) -> None:
        ws = DuckLakeWorkspace(ttl_days=7, max_join_rows=1000)
        conn = _conn()
        ws.materialize(conn, "SELECT 1 AS x", "mv_a")
        ws.materialize(conn, "SELECT 2 AS x", "mv_b")
        views = ws.list_views(conn)
        assert [v["view_name"] for v in views] == ["mv_b", "mv_a"]


class TestDropView:
    """drop_view removes a materialized view and its metadata."""

    def test_drop_view_removes_view_and_metadata(self) -> None:
        ws = DuckLakeWorkspace(ttl_days=7, max_join_rows=1000)
        conn = _conn()
        ws.materialize(conn, "SELECT 1 AS x", "mv_drop_me")
        assert ws.drop_view(conn, "mv_drop_me") is True
        assert ws.list_views(conn) == []
        # the table itself is gone — scanning it raises CatalogException
        with pytest.raises(duckdb.Error):
            conn.execute("SELECT * FROM mv_drop_me LIMIT 1")

    def test_drop_view_missing_returns_true_idempotent(self) -> None:
        # DROP TABLE IF EXISTS is idempotent; metadata delete also safe.
        ws = DuckLakeWorkspace(ttl_days=7)
        conn = _conn()
        # returns True (idempotent drop), even though nothing was there
        assert ws.drop_view(conn, "never_existed") is True

    def test_drop_view_rejects_unsafe_identifier(self) -> None:
        ws = DuckLakeWorkspace(ttl_days=7)
        conn = _conn()
        with pytest.raises((ValueError, ArrowLakeError)):
            ws.drop_view(conn, "bad name!")


class TestMaterializeRequestModel:
    """MaterializeRequest enforces view_name whitelist + SELECT-only SQL."""

    def test_valid_request_accepted(self) -> None:
        from arrow_lake.api.models.query import MaterializeRequest

        req = MaterializeRequest(sql="SELECT * FROM t", view_name="mv_sales", ttl_hours=48)
        assert req.view_name == "mv_sales"
        assert req.ttl_hours == 48

    @pytest.mark.parametrize("bad_name", ["bad-name!", "1num", "has space", "a;b", "", "with.dots"])
    def test_invalid_view_name_rejected(self, bad_name: str) -> None:
        from arrow_lake.api.models.query import MaterializeRequest

        with pytest.raises(Exception):
            MaterializeRequest(sql="SELECT 1", view_name=bad_name)

    def test_non_select_sql_rejected(self) -> None:
        from arrow_lake.api.models.query import MaterializeRequest

        # DROP / CREATE etc. blocked by _BLOCKED_SQL_PREFIXES
        with pytest.raises(Exception):
            MaterializeRequest(sql="DROP TABLE x", view_name="ok_name")

    def test_ttl_hours_bounds(self) -> None:
        from arrow_lake.api.models.query import MaterializeRequest

        with pytest.raises(Exception):
            MaterializeRequest(sql="SELECT 1", view_name="ok", ttl_hours=0)
        with pytest.raises(Exception):
            MaterializeRequest(sql="SELECT 1", view_name="ok", ttl_hours=10_000)


class TestBridgeGating:
    """OlapSearchBridge.list_materialized gates on ducklake_enabled."""

    def test_list_materialized_raises_when_disabled(self) -> None:
        from arrow_lake.config.olap import OlapConfig
        from arrow_lake.query.olap import OlapSearchBridge
        from arrow_lake.exceptions import QueryError

        bridge = OlapSearchBridge(storage=None, config=OlapConfig())  # ducklake_enabled=False default
        with pytest.raises(QueryError, match="not enabled"):
            bridge.list_materialized()

    def test_drop_materialized_raises_when_disabled(self) -> None:
        from arrow_lake.config.olap import OlapConfig
        from arrow_lake.query.olap import OlapSearchBridge
        from arrow_lake.exceptions import QueryError

        bridge = OlapSearchBridge(storage=None, config=OlapConfig())
        with pytest.raises(QueryError, match="not enabled"):
            bridge.drop_materialized("mv_any")
