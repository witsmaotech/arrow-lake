"""Shared DuckDB session context manager for query bridges.

Consolidates DuckDB connection logic that was duplicated across
OLAP, faceted, metadata, and lineage bridges.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass

__all__ = ["DuckDBSession"]


@contextmanager
def DuckDBSession():  # noqa: N802 — context manager used as a class-like factory
    """Context manager for an in-memory DuckDB session.

    Creates a new ephemeral connection on enter and closes it on exit.
    Bridges use this for one-off SQL queries against Arrow tables.

    Usage::

        from arrow_lake.query._db import DuckDBSession

        with DuckDBSession() as conn:
            conn.register("t", arrow_table)
            result = conn.execute("SELECT * FROM t").arrow()

    Yields:
        duckdb.DuckDBPyConnection: An active DuckDB connection.
    """
    import duckdb

    conn = duckdb.connect()
    try:
        yield conn
    finally:
        conn.close()
