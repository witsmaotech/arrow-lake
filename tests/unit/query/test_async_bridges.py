"""Tests for *_async bridge wrappers + facade exposure (v1.8.0 #17).

The async wrappers delegate the sync ``search`` to ``asyncio.to_thread`` so
async handlers don't block the event loop. These tests verify delegation
(params forwarded correctly) + coroutine-ness, isolating ``search`` via a
``__new__`` bypass of ``__init__``.
"""

from __future__ import annotations

import asyncio
import inspect
from unittest.mock import MagicMock

from arrow_lake._lake_search import _LakeSearchMixin


def _bare(cls: type) -> object:
    """Construct a bridge bypassing __init__ (unit-test isolation)."""
    return cls.__new__(cls)


class TestAsyncBridgeWrappers:
    """fts/hybrid/faceted search_async delegate to sync search via to_thread."""

    def test_fts_search_async_delegates_to_search(self) -> None:
        from arrow_lake.query.fts import FullTextSearchBridge

        b = _bare(FullTextSearchBridge)
        expected = object()
        b.search = MagicMock(return_value=expected)  # type: ignore[attr-defined]
        res = asyncio.run(b.search_async("ds", "q", top_k=5, fts_column="t"))  # type: ignore[attr-defined]
        b.search.assert_called_once_with(  # type: ignore[attr-defined]
            "ds", "q", top_k=5, fts_column="t", where=None, version=None, offset=0
        )
        assert res is expected

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
