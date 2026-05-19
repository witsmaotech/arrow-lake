"""Lightweight LRU cache for DuckDB query results.

Stores Arrow Tables keyed by a hash of (dataset_name, sql, tables).
Entries expire after a configurable TTL. Thread-safe via a module-level lock.
"""

from __future__ import annotations

import hashlib
import logging
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any

import pyarrow as pa

logger = logging.getLogger(__name__)

__all__ = ["QueryCache", "CacheEntry"]


@dataclass(frozen=True)
class CacheEntry:
    """A cached query result with metadata.

    Attributes:
        table: Arrow Table with query results.
        created_at: Timestamp when the entry was cached.
        hit_count: Number of cache hits for this entry.
    """

    table: pa.Table
    created_at: float
    hit_count: int


class QueryCache:
    """Thread-safe LRU cache for DuckDB query results.

    Args:
        max_entries: Maximum number of cached entries.
        ttl_seconds: Time-to-live in seconds for each entry.
    """

    def __init__(
        self,
        *,
        max_entries: int = 100,
        ttl_seconds: int = 60,
    ) -> None:
        self._max_entries = max_entries
        self._ttl_seconds = ttl_seconds
        self._lock = threading.Lock()
        self._entries: OrderedDict[str, CacheEntry] = OrderedDict()
        self._hits = 0
        self._misses = 0

    @staticmethod
    def make_key(
        dataset_name: str,
        sql: str,
        tables: dict[str, Any] | None = None,
    ) -> str:
        """Compute a stable cache key from query parameters.

        Args:
            dataset_name: Name of the dataset.
            sql: SQL query string.
            tables: Optional extra table names registered for JOINs.

        Returns:
            Hex digest string.
        """
        parts = [dataset_name, sql]
        if tables:
            parts.append(",".join(sorted(tables.keys())))
        raw = "|".join(parts)
        return hashlib.sha256(raw.encode()).hexdigest()

    def get(
        self,
        dataset_name: str,
        sql: str,
        tables: dict[str, Any] | None = None,
    ) -> pa.Table | None:
        """Look up a cached result.

        Args:
            dataset_name: Name of the dataset.
            sql: SQL query string.
            tables: Optional extra table names.

        Returns:
            Cached Arrow Table, or None if not found or expired.
        """
        key = self.make_key(dataset_name, sql, tables)
        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                self._misses += 1
                return None
            if time.monotonic() - entry.created_at > self._ttl_seconds:
                del self._entries[key]
                self._misses += 1
                return None
            # Move to end (most recently used)
            self._entries.move_to_end(key)
            # Update hit count
            updated = CacheEntry(
                table=entry.table,
                created_at=entry.created_at,
                hit_count=entry.hit_count + 1,
            )
            self._entries[key] = updated
            self._hits += 1
            return entry.table

    def put(
        self,
        dataset_name: str,
        sql: str,
        table: pa.Table,
        tables: dict[str, Any] | None = None,
    ) -> None:
        """Store a query result in the cache.

        Args:
            dataset_name: Name of the dataset.
            sql: SQL query string.
            table: Arrow Table result to cache.
            tables: Optional extra table names.
        """
        key = self.make_key(dataset_name, sql, tables)
        with self._lock:
            if key in self._entries:
                self._entries.move_to_end(key)
            self._entries[key] = CacheEntry(
                table=table,
                created_at=time.monotonic(),
                hit_count=0,
            )
            # Evict oldest entries if over capacity
            while len(self._entries) > self._max_entries:
                self._entries.popitem(last=False)

    def invalidate(
        self,
        dataset_name: str,
        sql: str,
        tables: dict[str, Any] | None = None,
    ) -> bool:
        """Remove a specific entry from the cache.

        Returns:
            True if the entry was found and removed.
        """
        key = self.make_key(dataset_name, sql, tables)
        with self._lock:
            return self._entries.pop(key, None) is not None

    def clear(self) -> int:
        """Clear all cached entries.

        Returns:
            Number of entries cleared.
        """
        with self._lock:
            count = len(self._entries)
            self._entries.clear()
            self._hits = 0
            self._misses = 0
            return count

    def stats(self) -> dict[str, int]:
        """Return cache statistics.

        Returns:
            Dict with entries, hits, misses, hit_rate.
        """
        with self._lock:
            total = self._hits + self._misses
            return {
                "entries": len(self._entries),
                "max_entries": self._max_entries,
                "hits": self._hits,
                "misses": self._misses,
                "hit_rate": (self._hits / total) if total > 0 else 0,
            }
