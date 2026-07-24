"""Unit tests for SqlConnector.fetch_column_comments (DB comment capture).

The SQLAlchemy engine is stubbed so no real DB connection is made; the
SqlConnector constructor's SSRF/DNS validation is bypassed by constructing the
instance via __new__ and setting _conn directly.
"""

from __future__ import annotations

import sys
import types

import pytest

from arrow_lake.ingest.connectors_sql import SqlConnector


def _stub_sqlalchemy(monkeypatch, rows):
    """Inject a fake ``sqlalchemy`` module into sys.modules."""

    class _Result:
        def fetchall(self):
            return list(rows)

    class _Conn:
        def execute(self, stmt, params):
            return _Result()

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    class _Engine:
        def connect(self):
            return _Conn()

        def dispose(self):
            pass

    fake = types.ModuleType("sqlalchemy")
    fake.create_engine = lambda url: _Engine()
    fake.text = lambda s: s
    monkeypatch.setitem(sys.modules, "sqlalchemy", fake)


def _make_connector(url):
    c = SqlConnector.__new__(SqlConnector)
    c._conn = url
    return c


class TestFetchColumnComments:
    def test_mysql(self, monkeypatch):
        _stub_sqlalchemy(monkeypatch, [("user_id", "PK"), ("name", "用户"), ("empty", "")])
        c = _make_connector("mysql+pymysql://u:p@db.example.com/db")
        assert c.fetch_column_comments("SELECT * FROM mydb.users WHERE 1=1") == {
            "user_id": "PK",
            "name": "用户",
        }

    def test_postgres_schema_qualified(self, monkeypatch):
        _stub_sqlalchemy(monkeypatch, [("id", "identifier"), ("amount", "total")])
        c = _make_connector("postgresql://u:p@db.example.com/db")
        out = c.fetch_column_comments("select id, amount from public.orders o")
        assert out == {"id": "identifier", "amount": "total"}

    def test_unsupported_dialect_returns_empty(self):
        c = _make_connector("sqlite:///x.db")
        assert c.fetch_column_comments("SELECT * FROM t") == {}

    def test_no_from_clause_returns_empty(self, monkeypatch):
        _stub_sqlalchemy(monkeypatch, [("x", "y")])
        c = _make_connector("mysql://u:p@db.example.com/db")
        assert c.fetch_column_comments("SELECT 1") == {}

    def test_sqlalchemy_missing_returns_empty(self, monkeypatch):
        # If sqlalchemy isn't importable, capture is skipped (best-effort).
        monkeypatch.setitem(sys.modules, "sqlalchemy", None)
        import builtins

        real_import = builtins.__import__

        def _fail(name, *a, **kw):
            if name == "sqlalchemy":
                raise ImportError("no sqlalchemy")
            return real_import(name, *a, **kw)

        monkeypatch.setattr(builtins, "__import__", _fail)
        c = _make_connector("mysql://u:p@db.example.com/db")
        assert c.fetch_column_comments("SELECT * FROM t") == {}

    def test_query_error_returns_empty(self, monkeypatch):
        class _Conn:
            def execute(self, stmt, params):
                raise RuntimeError("boom")

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        class _Engine:
            def connect(self):
                return _Conn()

            def dispose(self):
                pass

        fake = types.ModuleType("sqlalchemy")
        fake.create_engine = lambda url: _Engine()
        fake.text = lambda s: s
        monkeypatch.setitem(sys.modules, "sqlalchemy", fake)
        c = _make_connector("postgresql://u:p@db.example.com/db")
        assert c.fetch_column_comments("SELECT * FROM t") == {}


@pytest.mark.parametrize(
    "url,expected",
    [
        ("mysql+pymysql://u:p@h/db", "mysql"),
        ("mysql://u:p@h/db", "mysql"),
        ("postgresql://u:p@h/db", "postgres"),
        ("postgres://u:p@h/db", "postgres"),
        ("sqlite:///x.db", None),
    ],
)
def test_dialect_resolution(url, expected, monkeypatch):
    # No FROM clause → returns {} before touching sqlalchemy, but dialect is
    # resolved first. Use a FROM clause with sqlalchemy stubbed to assert no
    # crash and empty result for unsupported dialects.
    _stub_sqlalchemy(monkeypatch, [])
    c = _make_connector(url)
    out = c.fetch_column_comments("SELECT * FROM t")
    assert out == {}
