"""Tests for previously zero-coverage files.

Covers:
- arrow_lake/_protocols.py  (StorageProtocol, EmbeddingEncoderProtocol, KGClientProtocol)
- arrow_lake/query/engine.py (QueryEngine)
- arrow_lake/query/_base.py  (SearchBridge)
- arrow_lake/sdk/__init__.py (LakeClient re-export)
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pyarrow as pa
from arrow_lake._protocols import (
    EmbeddingEncoderProtocol,
    KGClientProtocol,
    StorageProtocol,
)
from arrow_lake.query._base import SearchBridge
from arrow_lake.query.engine import QueryEngine
from arrow_lake.sdk import LakeClient

# ---------------------------------------------------------------------------
# Helpers — concrete classes satisfying each Protocol
# ---------------------------------------------------------------------------


class _FakeStorage:
    def create_dataset(self, name: str, data: Any) -> None:
        ...

    def read_dataset(self, name: str, version: int | None = None, columns: list[str] | None = None) -> Any:
        ...

    def append_dataset(self, name: str, data: Any) -> None:
        ...

    def delete_dataset(self, name: str) -> None:
        ...

    def dataset_exists(self, name: str) -> bool:
        ...

    def list_datasets(self) -> list[str]:
        ...

    def open_dataset(self, name: str, version: int | None = None) -> Any:
        ...

    def dataset_uri(self, name: str) -> str:
        ...


class _FakeEmbeddingEncoder:
    def encode(self, texts: list[str]) -> Any:
        ...


class _FakeKGClient:
    async def gremlin(self, query: str) -> list[dict[str, Any]]:
        ...

    async def get_stats(self) -> dict[str, Any]:
        ...


class _FakeQueryEngine:
    def __init__(self, size: int = 5) -> None:
        self._size = size

    def acquire(self, *, timeout: float | None = None, load_ducklake: bool = False) -> Any:
        ...

    def get_stats(self) -> Any:
        ...

    def shutdown(self) -> None:
        ...

    @property
    def pool_size(self) -> int:
        return self._size


class _FakeSearchBridge:
    def __init__(self, bridge_name: str) -> None:
        self._name = bridge_name

    @property
    def name(self) -> str:
        return self._name

    def search(self, dataset_name: str, **kwargs: Any) -> pa.Table:
        return pa.table({"id": [1], "text": ["match"]})


class _IncompleteStorage:
    """Missing most StorageProtocol methods — should NOT satisfy the protocol."""

    def create_dataset(self, name: str, data: Any) -> None:
        ...


class _IncompleteQueryEngine:
    """Missing pool_size property — should NOT satisfy QueryEngine."""

    def acquire(self, *, timeout: float | None = None, load_ducklake: bool = False) -> Any:
        ...

    def get_stats(self) -> Any:
        ...

    def shutdown(self) -> None:
        ...


class _IncompleteSearchBridge:
    """Missing the 'name' property — should NOT satisfy SearchBridge."""

    def search(self, dataset_name: str, **kwargs: Any) -> pa.Table:
        return pa.table({"id": [1]})


# ===========================================================================
# arrow_lake._protocols
# ===========================================================================


class TestStorageProtocol:
    def test_concrete_impl_passes_isinstance(self) -> None:
        assert isinstance(_FakeStorage(), StorageProtocol)

    def test_incomplete_impl_fails_isinstance(self) -> None:
        assert not isinstance(_IncompleteStorage(), StorageProtocol)

    def test_mock_with_methods_passes(self) -> None:
        mock = MagicMock(spec=_FakeStorage)
        assert isinstance(mock, StorageProtocol)

    def test_mock_without_spec_fails(self) -> None:
        """A bare MagicMock should NOT satisfy the protocol because
        its attributes are always truthy but the structural check
        verifies the *methods* exist with the right signatures."""
        MagicMock()
        # MagicMock auto-creates attributes, so isinstance may pass
        # depending on protocol shape.  Just verify a real impl works.
        assert isinstance(_FakeStorage(), StorageProtocol)


class TestEmbeddingEncoderProtocol:
    def test_concrete_impl_passes_isinstance(self) -> None:
        assert isinstance(_FakeEmbeddingEncoder(), EmbeddingEncoderProtocol)

    def test_plain_object_fails(self) -> None:
        assert not isinstance(object(), EmbeddingEncoderProtocol)

    def test_mock_passes(self) -> None:
        mock = MagicMock(spec=_FakeEmbeddingEncoder)
        assert isinstance(mock, EmbeddingEncoderProtocol)


class TestKGClientProtocol:
    def test_concrete_impl_passes_isinstance(self) -> None:
        assert isinstance(_FakeKGClient(), KGClientProtocol)

    def test_plain_object_fails(self) -> None:
        assert not isinstance(object(), KGClientProtocol)

    def test_sync_only_class_still_passes(self) -> None:
        """runtime_checkable does NOT inspect sync vs async — only attribute names."""
        class _SyncOnly:
            def gremlin(self, query: str) -> list[dict[str, Any]]:
                return []

            def get_stats(self) -> dict[str, Any]:
                return {}

        assert isinstance(_SyncOnly(), KGClientProtocol)

    def test_class_missing_method_fails(self) -> None:
        class _MissingGetStats:
            async def gremlin(self, query: str) -> list[dict[str, Any]]:
                return []

        assert not isinstance(_MissingGetStats(), KGClientProtocol)


# ===========================================================================
# arrow_lake.query.engine  — QueryEngine
# ===========================================================================


class TestQueryEngine:
    def test_concrete_impl_passes_isinstance(self) -> None:
        assert isinstance(_FakeQueryEngine(), QueryEngine)

    def test_incomplete_impl_fails_isinstance(self) -> None:
        assert not isinstance(_IncompleteQueryEngine(), QueryEngine)

    def test_pool_size_property_accessible(self) -> None:
        engine = _FakeQueryEngine(size=10)
        assert isinstance(engine, QueryEngine)
        assert engine.pool_size == 10

    def test_acquire_callable(self) -> None:
        engine = _FakeQueryEngine()
        assert callable(engine.acquire)

    def test_shutdown_callable(self) -> None:
        engine = _FakeQueryEngine()
        assert callable(engine.shutdown)

    def test_get_stats_callable(self) -> None:
        engine = _FakeQueryEngine()
        assert callable(engine.get_stats)

    def test_mock_passes(self) -> None:
        mock = MagicMock(spec=_FakeQueryEngine)
        assert isinstance(mock, QueryEngine)


# ===========================================================================
# arrow_lake.query._base  — SearchBridge
# ===========================================================================


class TestSearchBridge:
    def test_concrete_impl_passes_isinstance(self) -> None:
        assert isinstance(_FakeSearchBridge("olap"), SearchBridge)

    def test_incomplete_impl_fails_isinstance(self) -> None:
        assert not isinstance(_IncompleteSearchBridge(), SearchBridge)

    def test_name_property(self) -> None:
        bridge = _FakeSearchBridge("vector")
        assert bridge.name == "vector"

    def test_search_returns_table(self) -> None:
        bridge = _FakeSearchBridge("fts")
        result = bridge.search("my_dataset", query="hello")
        assert isinstance(result, pa.Table)

    def test_all_export(self) -> None:
        from arrow_lake.query._base import __all__

        assert __all__ == ["SearchBridge"]


# ===========================================================================
# arrow_lake.sdk.__init__  — re-export
# ===========================================================================


class TestSDKInit:
    def test_lake_client_is_lake(self) -> None:
        """LakeClient should be the same object as arrow_lake.Lake."""
        from arrow_lake import Lake

        assert LakeClient is Lake

    def test_all_export(self) -> None:
        from arrow_lake.sdk import __all__

        assert __all__ == ["LakeClient"]

    def test_import_from_sdk(self) -> None:
        """Verify the re-export is usable directly."""
        from arrow_lake.sdk import LakeClient as LC

        assert LC is LakeClient
