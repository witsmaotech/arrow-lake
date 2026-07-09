"""LanceDB async connection pool (#1).

Process-wide reuse of ``AsyncConnection`` + per-dataset ``AsyncTable`` handles
to eliminate the per-call ``connect_async``/``open_table`` overhead in
``VectorSearchBridge.search_async`` (which previously opened a fresh
connection on every call before this pool was introduced).

Handle safety: ``AsyncConnection`` / ``AsyncTable`` are safe for concurrent
reads (same guarantee the hybrid bridge relies on, hybrid.py). Only one
``AsyncConnection`` is created per ``base_uri``; tables are cached per
``(base_uri, name)``.

Staleness: a cached ``AsyncTable`` handle may go stale after schema-changing
operations (rebuild vector index, add/drop columns). Those call sites must
invoke :func:`invalidate_async_table` to drop the affected handle so the next
access reopens a fresh one.
"""

from __future__ import annotations

import asyncio
from typing import Any

import lancedb

# Process-wide caches. Keyed by base_uri (connections) and (base_uri, name)
# (tables). base_uri uniquely identifies a storage config within a process
# (storage_options are fixed at LanceStorageManager init).
_conn_cache: dict[str, Any] = {}
_table_cache: dict[tuple[str, str], Any] = {}

# Serializes handle creation only; reads of cached handles are lock-free.
_lock: asyncio.Lock = asyncio.Lock()


async def get_async_connection(
    base_uri: str, storage_options: dict[str, str] | None
) -> Any:
    """Return a cached ``AsyncConnection`` for ``base_uri``, creating on first use.

    Args:
        base_uri: LanceDB connect URI (local path or ``s3://``/``gs://``/``az://``).
        storage_options: Storage options for object-storage backends (None for local).

    Returns:
        A shared ``AsyncConnection`` (identity-stable across calls for the same URI).
    """
    cached = _conn_cache.get(base_uri)
    if cached is not None:
        return cached
    async with _lock:
        # Double-checked: another task may have created it while we waited.
        cached = _conn_cache.get(base_uri)
        if cached is not None:
            return cached
        conn = await lancedb.connect_async(base_uri, storage_options=storage_options)
        _conn_cache[base_uri] = conn
        return conn


async def get_async_table(
    base_uri: str, name: str, storage_options: dict[str, str] | None
) -> Any:
    """Return a cached ``AsyncTable`` for ``(base_uri, name)``.

    Reuses the shared ``AsyncConnection`` for ``base_uri``.

    Args:
        base_uri: LanceDB connect URI.
        name: Dataset/table name.
        storage_options: Storage options (forwarded only on first connection).

    Returns:
        A shared ``AsyncTable`` handle.
    """
    key = (base_uri, name)
    cached = _table_cache.get(key)
    if cached is not None:
        return cached
    conn = await get_async_connection(base_uri, storage_options)
    async with _lock:
        cached = _table_cache.get(key)
        if cached is not None:
            return cached
        table = await conn.open_table(name)
        _table_cache[key] = table
        return table


def invalidate_async_table(name: str, base_uri: str | None = None) -> None:
    """Drop cached ``AsyncTable`` handle(s).

    Call after schema- or index-changing operations (rebuild_vector_index,
    add/drop columns) so the next access reopens a fresh handle.

    Args:
        name: Dataset/table name to invalidate.
        base_uri: Restrict to one connection. None → clear across all connections.
    """
    if base_uri is None:
        for key in [k for k in _table_cache if k[1] == name]:
            _table_cache.pop(key, None)
    else:
        _table_cache.pop((base_uri, name), None)


def reset_pool() -> None:
    """Clear all cached connections and tables.

    Intended for tests. In production the pool lives for the process lifetime.
    """
    _conn_cache.clear()
    _table_cache.clear()
