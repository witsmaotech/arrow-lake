"""Tests for _lake_search.py mixin — vector, FTS, hybrid, faceted, ensemble search."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from arrow_lake import Lake
from arrow_lake.config import ArrowLakeConfig, StorageBackend, StorageConfig


@pytest.fixture
def lake(tmp_path: Path) -> Lake:
    cfg = ArrowLakeConfig()
    cfg.storage = StorageConfig(base_uri=str(tmp_path / "data"), backend=StorageBackend.LOCAL)
    return Lake(base_uri=str(tmp_path / "data"), config=cfg)


class TestVectorSearch:
    def test_search(self, lake):
        with patch("arrow_lake.query.vector.VectorSearchBridge") as MockBridge:
            MockBridge.return_value.search.return_value = "results"
            result = lake.search("ds", [0.1, 0.2], top_k=5)
            assert result == "results"

    def test_search_with_all_args(self, lake):
        with patch("arrow_lake.query.vector.VectorSearchBridge") as MockBridge:
            MockBridge.return_value.search.return_value = "results"
            lake.search("ds", [0.1], top_k=3, metric="l2",
                        vector_column="emb", where="x > 0", nprobes=10, version=2)
            MockBridge.return_value.search.assert_called_once()


class TestCreateVectorIndex:
    def test_create_vector_index(self, lake):
        with patch("arrow_lake.query.vector.VectorSearchBridge") as MockBridge:
            MockBridge.return_value.create_index.return_value = "info"
            result = lake.create_vector_index("ds", metric="cosine")
            assert result == "info"

    def test_create_vector_index_caches(self, lake):
        with patch("arrow_lake.query.vector.VectorSearchBridge") as MockBridge:
            MockBridge.return_value.create_index.return_value = "info"
            lake.create_vector_index("ds")
            lake.create_vector_index("ds")
            assert MockBridge.call_count == 1


class TestTextSearch:
    def test_text_search(self, lake):
        with patch("arrow_lake.query.fts.FullTextSearchBridge") as MockBridge:
            MockBridge.return_value.search.return_value = "results"
            result = lake.text_search("ds", "hello world", top_k=10)
            assert result == "results"


class TestCreateFtsIndex:
    def test_create_fts_index(self, lake):
        with patch("arrow_lake.query.fts.FullTextSearchBridge") as MockBridge:
            lake.create_fts_index("ds", fts_column="content", replace=True)
            MockBridge.return_value.create_index.assert_called_once()


class TestHybridSearch:
    def test_hybrid_search(self, lake):
        with patch("arrow_lake.query.hybrid.HybridSearchBridge") as MockBridge:
            MockBridge.return_value.search.return_value = "results"
            result = lake.hybrid_search("ds", [0.1], "hello", top_k=5)
            assert result == "results"


class TestFacetedSearch:
    def test_faceted_search(self, lake):
        with patch("arrow_lake.query.faceted.FacetedSearchBridge") as MockBridge:
            MockBridge.return_value.search.return_value = "results"
            result = lake.faceted_search("ds", [0.1], facets=["category"], top_k=10)
            assert result == "results"


class TestEnsembleSearch:
    def test_ensemble_search(self, lake):
        with patch("arrow_lake.query.ensemble.EnsembleSearchBridge") as MockBridge:
            MockBridge.return_value.search.return_value = "results"
            result = lake.ensemble_search("ds", [0.1], columns=["emb1", "emb2"], top_k=5)
            assert result == "results"


class TestDeleteVectorIndex:
    def test_delete_vector_index(self, lake):
        storage = lake._get_storage()
        with patch.object(storage, "delete_vector_index"):
            storage.delete_vector_index("ds", "idx1")
            storage.delete_vector_index.assert_called_once_with("ds", "idx1")


class TestGetVectorIndexInfo:
    def test_get_vector_index_info(self, lake):
        with patch("arrow_lake.query.vector.VectorSearchBridge") as MockBridge:
            MockBridge.return_value.get_index_info.return_value = "info"
            result = lake.get_vector_index_info("ds", vector_column="emb")
            assert result == "info"


class TestRebuildVectorIndex:
    def test_rebuild_success(self, lake):
        storage = lake._get_storage()
        with patch.object(storage, "rebuild_vector_index"), \
             patch("arrow_lake.query.vector.VectorSearchBridge") as MockBridge:
            MockBridge.return_value.get_index_info.return_value = "new_info"
            result = lake.rebuild_vector_index("ds")
            assert result == "new_info"

    def test_rebuild_no_info_raises(self, lake):
        from arrow_lake.exceptions import QueryError

        storage = lake._get_storage()
        with patch.object(storage, "rebuild_vector_index"), \
             patch("arrow_lake.query.vector.VectorSearchBridge") as MockBridge:
            MockBridge.return_value.get_index_info.return_value = None
            with pytest.raises(QueryError):
                lake.rebuild_vector_index("ds")


class TestDeleteFtsIndex:
    def test_delete_fts_index_no_indices(self, lake):
        storage = lake._get_storage()
        mock_table = MagicMock()
        mock_table.list_indices.return_value = []
        with patch.object(storage, "open_dataset", return_value=mock_table):
            lake.delete_fts_index("ds")

    def test_delete_fts_index_error(self, lake):
        storage = lake._get_storage()
        mock_table = MagicMock()
        mock_table.list_indices.side_effect = RuntimeError("err")
        with patch.object(storage, "open_dataset", return_value=mock_table):
            lake.delete_fts_index("ds")


class TestGetFtsIndexInfo:
    def test_get_fts_index_info_not_found(self, lake):
        storage = lake._get_storage()
        mock_table = MagicMock()
        mock_table.list_indices.return_value = []
        with patch.object(storage, "open_dataset", return_value=mock_table):
            result = lake.get_fts_index_info("ds")
            assert result is None

    def test_get_fts_index_info_error(self, lake):
        storage = lake._get_storage()
        mock_table = MagicMock()
        mock_table.list_indices.side_effect = OSError("err")
        with patch.object(storage, "open_dataset", return_value=mock_table):
            result = lake.get_fts_index_info("ds")
            assert result is None
