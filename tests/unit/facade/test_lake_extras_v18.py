"""Tests for v1.8.0 #2 (blob column) + #8 (hf:// dataset) + #16 (streaming) facade additions."""

from __future__ import annotations

import contextlib
from unittest.mock import MagicMock, patch

import pyarrow as pa
import pytest


class TestAddBlobColumn:
    """#2: add_blob_column stores raw bytes as a Lance binary column."""

    def test_add_blob_column_stores_binary(self, tmp_path: str) -> None:
        from arrow_lake import Lake

        name = "blob_v18_test"
        lake = Lake(base_uri=str(tmp_path))
        with contextlib.suppress(Exception):
            lake.delete_dataset(name)
        lake.create_dataset(name, pa.table({"id": [1, 2]}))
        lake.add_blob_column(name, "image_bytes", [b"img1", b"img2"])

        tbl = lake.read_dataset(name)
        assert "image_bytes" in tbl.column_names
        assert tbl.column("image_bytes").to_pylist() == [b"img1", b"img2"]


class TestWriteDataFrame:
    """#16: write_dataframe delegates to LanceStorageManager.write_lance_from_dataframe."""

    def test_write_dataframe_delegates_to_storage(self, tmp_path: str) -> None:
        from arrow_lake import Lake

        lake = Lake(base_uri=str(tmp_path))
        storage = lake._get_storage()
        storage.write_lance_from_dataframe = MagicMock()  # type: ignore[method-assign]

        fake_df = object()
        lake.write_dataframe("wf_ds", fake_df, mode="overwrite")

        storage.write_lance_from_dataframe.assert_called_once_with(
            "wf_ds", fake_df, mode="overwrite"
        )


class TestLoadHfDataset:
    """#8: load_hf_dataset constructs hf:// URI + reads the first table."""

    def test_uri_prefix_and_empty_raises(self, tmp_path: str) -> None:
        from arrow_lake import Lake

        lake = Lake(base_uri=str(tmp_path))
        with patch("lancedb.connect") as mock_connect:
            mock_db = MagicMock()
            mock_db.table_names.return_value = []
            mock_connect.return_value = mock_db
            with pytest.raises(ValueError, match="No tables"):
                lake.load_hf_dataset("user/eval-set")
            mock_connect.assert_called_once_with("hf://datasets/user/eval-set")

    def test_full_uri_passthrough_and_read(self, tmp_path: str) -> None:
        from arrow_lake import Lake

        lake = Lake(base_uri=str(tmp_path))
        with patch("lancedb.connect") as mock_connect:
            mock_db = MagicMock()
            mock_db.table_names.return_value = ["train"]
            mock_tbl = MagicMock()
            mock_tbl.to_arrow.return_value = pa.table({"x": [1, 2, 3]})
            mock_db.open_table.return_value = mock_tbl
            mock_connect.return_value = mock_db

            res = lake.load_hf_dataset("hf://datasets/foo/bar", table="train")
            mock_connect.assert_called_once_with("hf://datasets/foo/bar")
            mock_db.open_table.assert_called_once_with("train")
            assert res.num_rows == 3
