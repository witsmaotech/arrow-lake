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

    def test_key_embeds_dataset_prefix(self) -> None:
        # [#step2-B] key embeds dataset as a scannable prefix (for invalidate_dataset)
        key = QueryCache.make_key("myds", "SELECT 1")
        assert "myds" in key


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


class TestQueryCacheInvalidateDataset:
    def test_invalidates_only_target_dataset(self) -> None:
        cache = QueryCache(max_entries=10)
        cache.put("dsA", "SELECT 1", _table())
        cache.put("dsA", "SELECT 2", _table())
        cache.put("dsB", "SELECT 1", _table())
        removed = cache.invalidate_dataset("dsA")
        assert removed == 2
        assert cache.get("dsA", "SELECT 1") is None
        assert cache.get("dsA", "SELECT 2") is None
        assert cache.get("dsB", "SELECT 1") is not None  # other dataset untouched

    def test_invalidate_dataset_missing_returns_zero(self) -> None:
        assert QueryCache(max_entries=10).invalidate_dataset("none") == 0


class TestFtsNullSegmentedDetect:
    """[#step2-A] FTS must detect NULL _fts_segmented (appended rows) to trigger re-index."""

    def _bridge(self):
        from unittest.mock import MagicMock

        from arrow_lake.query.fts import FullTextSearchBridge

        return FullTextSearchBridge(MagicMock())  # default config, cheap init

    def test_detects_nulls(self) -> None:
        t = pa.table({"_fts_segmented": [None, "a b", None]})
        assert self._bridge()._has_null_segmented(t) is True

    def test_no_nulls(self) -> None:
        t = pa.table({"_fts_segmented": ["a b", "c d"]})
        assert self._bridge()._has_null_segmented(t) is False

    def test_missing_column_returns_false(self) -> None:
        t = pa.table({"x": [1, 2]})
        assert self._bridge()._has_null_segmented(t) is False
