"""Tests for time-travel query version parameter."""

from __future__ import annotations

from unittest.mock import MagicMock

from arrow_lake._lake_search import _LakeSearchMixin
from arrow_lake.config import ArrowLakeConfig


def _make_lake():
    config = ArrowLakeConfig()
    obj = _LakeSearchMixin()
    obj._config = config
    obj._components = {}
    obj._get_storage = MagicMock()
    obj._get_component = MagicMock()
    obj._bridge_kwargs = MagicMock(return_value={})
    return obj


class TestTimeTravelVersion:
    def test_search_passes_version_to_bridge(self) -> None:
        lake = _make_lake()
        mock_bridge = MagicMock()
        mock_result = MagicMock()
        mock_bridge.search.return_value = mock_result
        lake._get_component.return_value = mock_bridge

        lake.search("ds1", [0.1, 0.2], version=5)
        call_kwargs = mock_bridge.search.call_args[1]
        assert call_kwargs["version"] == 5

    def test_search_version_none_default(self) -> None:
        lake = _make_lake()
        mock_bridge = MagicMock()
        mock_bridge.search.return_value = MagicMock()
        lake._get_component.return_value = mock_bridge

        lake.search("ds1", [0.1, 0.2])
        call_kwargs = mock_bridge.search.call_args[1]
        assert call_kwargs["version"] is None

    def test_text_search_passes_version(self) -> None:
        lake = _make_lake()
        mock_bridge = MagicMock()
        mock_bridge.search.return_value = MagicMock()
        lake._get_component.return_value = mock_bridge

        lake.text_search("ds1", "query", version=3)
        call_kwargs = mock_bridge.search.call_args[1]
        assert call_kwargs["version"] == 3

    def test_hybrid_search_passes_version(self) -> None:
        lake = _make_lake()
        mock_bridge = MagicMock()
        mock_bridge.search.return_value = MagicMock()
        lake._get_component.return_value = mock_bridge

        lake.hybrid_search("ds1", [0.1], "query", version=2)
        call_kwargs = mock_bridge.search.call_args[1]
        assert call_kwargs["version"] == 2

    def test_open_dataset_versioned(self) -> None:
        lake = _make_lake()
        storage = lake._get_storage()
        mock_ds = MagicMock()
        storage.open_dataset_versioned.return_value = mock_ds

        table = storage.open_dataset_versioned("ds1", 5)
        storage.open_dataset_versioned.assert_called_once_with("ds1", 5)
        assert table is mock_ds
