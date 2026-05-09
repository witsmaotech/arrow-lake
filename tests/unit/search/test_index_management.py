"""Tests for index management methods on Lake search mixin."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

from arrow_lake._lake_search import _LakeSearchMixin
from arrow_lake.config import ArrowLakeConfig


def _make_lake():
    config = ArrowLakeConfig()
    obj = _LakeSearchMixin()
    obj._config = config
    obj._base_uri = "./data"
    obj._components = {}
    obj._get_storage = MagicMock()
    obj._get_component = MagicMock()
    obj._bridge_kwargs = MagicMock(return_value={})
    return obj


class TestDeleteVectorIndex:
    def test_delegates_to_storage(self):
        lake = _make_lake()
        lake._get_storage().delete_vector_index = MagicMock()
        lake.delete_vector_index("ds1", "idx1")
        lake._get_storage().delete_vector_index.assert_called_once_with("ds1", "idx1")


class TestListVectorIndexes:
    def test_empty_list(self):
        lake = _make_lake()
        mock_table = MagicMock()
        mock_table.list_indices.return_value = []
        lake._get_storage().open_dataset.return_value = mock_table
        result = lake.list_vector_indexes("ds1")
        assert result == []

    def test_returns_index_info(self):
        lake = _make_lake()
        mock_table = MagicMock()
        mock_idx = MagicMock()
        mock_idx.name = "idx1"
        mock_idx.columns = ["embedding"]
        mock_table.list_indices.return_value = [mock_idx]
        lake._get_storage().open_dataset.return_value = mock_table

        mock_info = MagicMock()
        with patch("arrow_lake.query.vector.VectorSearchBridge._get_latest_index_info", return_value=mock_info):
            lake._get_component.return_value = MagicMock()
            result = lake.list_vector_indexes("ds1")
            assert len(result) == 1


class TestGetVectorIndexInfo:
    def test_delegates_to_bridge(self):
        lake = _make_lake()
        mock_info = MagicMock()
        mock_bridge = MagicMock()
        mock_bridge.get_index_info.return_value = mock_info
        lake._get_component.return_value = mock_bridge
        result = lake.get_vector_index_info("ds1", "embedding")
        mock_bridge.get_index_info.assert_called_once()
        assert result == mock_info

    def test_returns_none_when_no_index(self):
        lake = _make_lake()
        mock_bridge = MagicMock()
        mock_bridge.get_index_info.return_value = None
        lake._get_component.return_value = mock_bridge
        result = lake.get_vector_index_info("ds1")
        assert result is None


class TestDeleteFtsIndex:
    def test_no_index_returns_silently(self):
        lake = _make_lake()
        mock_table = MagicMock()
        mock_table.list_indices.return_value = []
        lake._get_storage().open_dataset.return_value = mock_table
        lake.delete_fts_index("ds1")

    def test_deletes_fts_index(self):
        lake = _make_lake()
        mock_table = MagicMock()
        mock_idx = MagicMock()
        mock_idx.name = "__fts_index"
        mock_idx.columns = ["text"]
        mock_table.list_indices.return_value = [mock_idx]
        lake._get_storage().open_dataset.return_value = mock_table
        lake._get_storage().delete_vector_index = MagicMock()
        lake.delete_fts_index("ds1")
        lake._get_storage().delete_vector_index.assert_called_once()


class TestGetFtsIndexInfo:
    def test_no_index_returns_none(self):
        lake = _make_lake()
        mock_table = MagicMock()
        mock_table.list_indices.return_value = []
        lake._get_storage().open_dataset.return_value = mock_table
        result = lake.get_fts_index_info("ds1")
        assert result is None

    def test_returns_index_metadata(self):
        lake = _make_lake()
        mock_table = MagicMock()
        mock_idx = MagicMock()
        mock_idx.name = "fts_idx"
        mock_idx.columns = ["text"]
        mock_idx.index_type = "fts"
        mock_table.list_indices.return_value = [mock_idx]
        lake._get_storage().open_dataset.return_value = mock_table
        result = lake.get_fts_index_info("ds1")
        assert result is not None
        assert result["name"] == "fts_idx"
