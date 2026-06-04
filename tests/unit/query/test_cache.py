"""Tests for query/_cache.py — QueryCache LRU behavior, TTL, and stats."""

from __future__ import annotations

import time

import pyarrow as pa
import pytest

from arrow_lake.query._cache import CacheEntry, QueryCache


def _table(rows: int = 5) -> pa.Table:
    return pa.table({"x": list(range(rows))})


class TestCacheEntry:
    def test_creation(self) -> None:
        t = _table()
        entry = CacheEntry(table=t, created_at=100.0, hit_count=0)
        assert entry.hit_count == 0
        assert entry.table == t

    def test_frozen(self) -> None:
        entry = CacheEntry(table=_table(), created_at=0.0, hit_count=0)
        with pytest.raises(AttributeError):
            entry.created_at = 999.0  # type: ignore[misc]


class TestQueryCacheMakeKey:
    def test_deterministic(self) -> None:
        k1 = QueryCache.make_key("ds", "SELECT 1")
        k2 = QueryCache.make_key("ds", "SELECT 1")
        assert k1 == k2

    def test_different_sql(self) -> None:
        k1 = QueryCache.make_key("ds", "SELECT 1")
        k2 = QueryCache.make_key("ds", "SELECT 2")
        assert k1 != k2

    def test_different_dataset(self) -> None:
        k1 = QueryCache.make_key("ds1", "SELECT 1")
        k2 = QueryCache.make_key("ds2", "SELECT 1")
        assert k1 != k2

    def test_tables_included(self) -> None:
        k1 = QueryCache.make_key("ds", "SELECT 1", tables={"a": None})
        k2 = QueryCache.make_key("ds", "SELECT 1", tables={"b": None})
        assert k1 != k2

    def test_returns_hex_digest(self) -> None:
        key = QueryCache.make_key("ds", "SELECT 1")
        assert len(key) == 64  # SHA-256 hex digest


class TestQueryCacheGetPut:
    def test_put_and_get(self) -> None:
        cache = QueryCache(max_entries=10)
        cache.put("ds", "SELECT 1", _table(3))
        result = cache.get("ds", "SELECT 1")
        assert result is not None
        assert result.num_rows == 3

    def test_get_miss_returns_none(self) -> None:
        cache = QueryCache(max_entries=10)
        assert cache.get("ds", "SELECT 1") is None

    def test_ttl_expired_returns_none(self) -> None:
        cache = QueryCache(max_entries=10, ttl_seconds=0)
        cache.put("ds", "SELECT 1", _table())
        time.sleep(0.01)
        assert cache.get("ds", "SELECT 1") is None

    def test_ttl_not_expired_returns_result(self) -> None:
        cache = QueryCache(max_entries=10, ttl_seconds=60)
        cache.put("ds", "SELECT 1", _table())
        assert cache.get("ds", "SELECT 1") is not None

    def test_get_updates_hit_count(self) -> None:
        cache = QueryCache(max_entries=10)
        cache.put("ds", "SELECT 1", _table())
        cache.get("ds", "SELECT 1")
        cache.get("ds", "SELECT 1")
        stats = cache.stats()
        assert stats["hits"] == 2


class TestQueryCacheLRU:
    def test_max_entries_eviction(self) -> None:
        cache = QueryCache(max_entries=3)
        for i in range(5):
            cache.put("ds", f"SELECT {i}", _table())
        assert cache.stats()["entries"] == 3

    def test_invalidate_specific(self) -> None:
        cache = QueryCache(max_entries=10)
        cache.put("ds", "SELECT 1", _table())
        assert cache.invalidate("ds", "SELECT 1") is True
        assert cache.get("ds", "SELECT 1") is None

    def test_invalidate_missing(self) -> None:
        assert QueryCache(max_entries=10).invalidate("ds", "SELECT 1") is False


class TestQueryCacheStats:
    def test_stats_empty(self) -> None:
        stats = QueryCache(max_entries=10).stats()
        assert stats["entries"] == 0
        assert stats["hits"] == 0
        assert stats["misses"] == 0
        assert stats["hit_rate"] == 0

    def test_stats_after_operations(self) -> None:
        cache = QueryCache(max_entries=10)
        cache.put("ds", "SELECT 1", _table())
        cache.get("ds", "SELECT 1")  # hit
        cache.get("ds", "SELECT 2")  # miss
        stats = cache.stats()
        assert stats["entries"] == 1
        assert stats["hits"] == 1
        assert stats["misses"] == 1
        assert stats["hit_rate"] == 0.5


class TestQueryCacheClear:
    def test_clear_returns_count(self) -> None:
        cache = QueryCache(max_entries=10)
        for i in range(3):
            cache.put("ds", f"SELECT {i}", _table())
        count = cache.clear()
        assert count == 3
        assert cache.stats()["entries"] == 0

    def test_clear_resets_hits_misses(self) -> None:
        cache = QueryCache(max_entries=10)
        cache.put("ds", "SELECT 1", _table())
        cache.get("ds", "SELECT 1")
        cache.clear()
        assert cache.stats()["hits"] == 0
