"""Tests for *_async bridge wrappers + facade exposure (v1.8.0 #17).

The async wrappers delegate the sync ``search`` to ``asyncio.to_thread`` so
async handlers don't block the event loop. These tests verify delegation
(params forwarded correctly) + coroutine-ness, isolating ``search`` via a
``__new__`` bypass of ``__init__``.
"""

from __future__ import annotations

import asyncio
import inspect
from unittest.mock import MagicMock, patch

from arrow_lake._lake_search import _LakeSearchMixin


def _bare(cls: type) -> object:
    """Construct a bridge bypassing __init__ (unit-test isolation)."""
    return cls.__new__(cls)


class TestAsyncBridgeWrappers:
    """fts search_async = native AsyncTable FTS (W1-3); hybrid/faceted search_async
    delegate the sync search to ``asyncio.to_thread`` (their wrapped work is
    Python-bound fusion/aggregation, not a single lancedb call)."""

    def test_fts_search_async_version_falls_back_to_sync(self) -> None:
        """version-aware queries still go through sync search on a worker
        thread (the async pool is not version-aware)."""
        from arrow_lake.query.fts import FullTextSearchBridge

        b = _bare(FullTextSearchBridge)
        expected = object()
        b.search = MagicMock(return_value=expected)  # type: ignore[attr-defined]
        res = asyncio.run(b.search_async("ds", "q", version=3))  # type: ignore[attr-defined]
        b.search.assert_called_once_with(  # type: ignore[attr-defined]
            "ds", "q", top_k=None, fts_column=None, where=None, version=3, offset=0
        )
        assert res is expected

    def test_fts_search_async_native_path(self) -> None:
        """W1-3: version=None runs the native AsyncTable FTS query — pooled
        handle, chained where/offset/limit, awaited to_arrow."""
        from types import SimpleNamespace

        import pyarrow as pa

        from arrow_lake.query.fts import FullTextSearchBridge

        b = _bare(FullTextSearchBridge)
        b._config = SimpleNamespace(fts_column="text", default_top_k=7)
        b._storage = SimpleNamespace(
            _connect_uri="lancedb:///tmp/x", _storage_options=None
        )
        b._validate_where_clause = MagicMock()  # type: ignore[attr-defined]

        calls: dict = {}

        class _FakeFTSQuery:
            def where(self, w): calls["where"] = w; return self
            def offset(self, o): calls["offset"] = o; return self
            def limit(self, k): calls["limit"] = k; return self
            async def to_arrow(self):
                return pa.table({"row_id": [1, 2], "_score": [2.0, 1.0]})

        class _FakeAsyncTable:
            async def schema(self):
                return pa.schema([("text", pa.string())])
            async def search(self, query, *, query_type, fts_columns):
                calls["search"] = (query, query_type, fts_columns)
                return _FakeFTSQuery()

        async def _fake_get_async_table(base_uri, name, opts):
            calls["pool"] = (base_uri, name, opts)
            return _FakeAsyncTable()

        # fts.search_async resolves get_async_table lazily at call time, so
        # patching the pool module attribute intercepts it.
        with patch(
            "arrow_lake.query.async_conn_pool.get_async_table",
            new=_fake_get_async_table,
        ):
            res = asyncio.run(
                b.search_async("ds", "hello", top_k=5, where="lang='zh'", offset=2)
            )

        assert calls["pool"] == ("lancedb:///tmp/x", "ds", None)
        assert calls["search"] == ("hello", "fts", "text")
        assert calls["where"] == "lang='zh'"
        assert calls["offset"] == 2
        assert calls["limit"] == 5
        assert res.row_count == 2
        assert res.max_score == 2.0

    def test_hybrid_search_async_delegates_to_search(self) -> None:
        from arrow_lake.query.hybrid import HybridSearchBridge

        b = _bare(HybridSearchBridge)
        expected = object()
        b.search = MagicMock(return_value=expected)  # type: ignore[attr-defined]
        res = asyncio.run(b.search_async("ds", [0.1], "q", top_k=3))  # type: ignore[attr-defined]
        b.search.assert_called_once_with(  # type: ignore[attr-defined]
            "ds", [0.1], "q", top_k=3, vector_column="text_embedding",
            fts_column=None, where=None, version=None,
        )
        assert res is expected

    def test_faceted_search_async_delegates_to_search(self) -> None:
        from arrow_lake.query.faceted import FacetedSearchBridge

        b = _bare(FacetedSearchBridge)
        expected = object()
        b.search = MagicMock(return_value=expected)  # type: ignore[attr-defined]
        res = asyncio.run(b.search_async("ds", [0.1], facets=["cat"], top_k=5))  # type: ignore[attr-defined]
        b.search.assert_called_once_with(  # type: ignore[attr-defined]
            "ds", [0.1], facets=["cat"], top_k=5, vector_column="embedding",
            where=None, version=None,
        )
        assert res is expected

    def test_all_bridge_search_async_are_coroutines(self) -> None:
        from arrow_lake.query.faceted import FacetedSearchBridge
        from arrow_lake.query.fts import FullTextSearchBridge
        from arrow_lake.query.hybrid import HybridSearchBridge

        for cls in (FullTextSearchBridge, HybridSearchBridge, FacetedSearchBridge):
            assert inspect.iscoroutinefunction(cls.search_async), (
                f"{cls.__name__}.search_async must be async"
            )


class TestFacadeAsyncExposure:
    """LakeSearchMixin exposes *_async for vector/fts/hybrid/faceted."""

    def test_facade_has_async_methods(self) -> None:
        for name in (
            "search_async",
            "text_search_async",
            "hybrid_search_async",
            "faceted_search_async",
        ):
            assert hasattr(_LakeSearchMixin, name), f"facade missing {name}"
            assert inspect.iscoroutinefunction(getattr(_LakeSearchMixin, name)), (
                f"facade.{name} must be async"
            )
