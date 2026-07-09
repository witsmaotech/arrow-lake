"""Tests for LanceDB async connection pool (#1).

Verifies AsyncConnection + AsyncTable handle reuse, invalidation, and that
tables co-locate on a shared connection. Uses a real embedded LanceDB in
tmp_path (integration-style) to assert genuine handle identity — mock-based
tests cannot prove the pool actually reuses a live AsyncConnection.

ruff noqa: F821 (Any used in annotations with __future__ import)
"""

from __future__ import annotations

import asyncio

import pyarrow as pa

_TABLE = pa.table({"id": [1, 2], "vec": [[0.1, 0.2], [0.3, 0.4]]})


def _run(coro):
    return asyncio.run(coro)


def test_get_async_connection_reuses(tmp_path) -> None:
    from arrow_lake.query.async_conn_pool import get_async_connection, reset_pool

    reset_pool()
    uri = str(tmp_path / "db")
    c1 = _run(get_async_connection(uri, None))
    c2 = _run(get_async_connection(uri, None))
    assert c1 is c2


def test_get_async_table_reuses(tmp_path) -> None:
    from arrow_lake.query.async_conn_pool import (
        get_async_connection,
        get_async_table,
        reset_pool,
    )

    reset_pool()
    uri = str(tmp_path / "db")
    conn = _run(get_async_connection(uri, None))
    _run(conn.create_table("t", data=_TABLE, mode="overwrite"))
    t1 = _run(get_async_table(uri, "t", None))
    t2 = _run(get_async_table(uri, "t", None))
    assert t1 is t2


def test_invalidate_async_table_drops_handle(tmp_path) -> None:
    from arrow_lake.query.async_conn_pool import (
        get_async_connection,
        get_async_table,
        invalidate_async_table,
        reset_pool,
    )

    reset_pool()
    uri = str(tmp_path / "db")
    conn = _run(get_async_connection(uri, None))
    _run(conn.create_table("t", data=_TABLE, mode="overwrite"))
    t1 = _run(get_async_table(uri, "t", None))
    invalidate_async_table("t", uri)
    t2 = _run(get_async_table(uri, "t", None))
    assert t1 is not t2


def test_connection_shared_across_tables(tmp_path) -> None:
    from arrow_lake.query import async_conn_pool
    from arrow_lake.query.async_conn_pool import (
        get_async_connection,
        get_async_table,
        reset_pool,
    )

    reset_pool()
    uri = str(tmp_path / "db")
    conn = _run(get_async_connection(uri, None))
    _run(conn.create_table("t1", data=_TABLE, mode="overwrite"))
    _run(conn.create_table("t2", data=_TABLE, mode="overwrite"))
    _run(get_async_table(uri, "t1", None))
    _run(get_async_table(uri, "t2", None))
    # Two tables, but only one underlying AsyncConnection was created.
    assert len(async_conn_pool._conn_cache) == 1


def test_invalidate_without_base_uri_clears_all(tmp_path) -> None:
    from arrow_lake.query.async_conn_pool import (
        _table_cache,
        get_async_connection,
        get_async_table,
        invalidate_async_table,
        reset_pool,
    )

    reset_pool()
    uri = str(tmp_path / "db")
    conn = _run(get_async_connection(uri, None))
    _run(conn.create_table("t", data=_TABLE, mode="overwrite"))
    _run(get_async_table(uri, "t", None))
    assert _table_cache
    invalidate_async_table("t")  # base_uri=None → clear across all connections
    assert not _table_cache
