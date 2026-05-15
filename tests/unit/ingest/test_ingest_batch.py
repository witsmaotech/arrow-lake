"""Unit tests for ingest_batch — Daft repositioning Sprint 3."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from arrow_lake.ingest.ingestor import Ingestor


@pytest.fixture
def mock_storage() -> MagicMock:
    return MagicMock()


@pytest.fixture
def ingestor(mock_storage: MagicMock) -> Ingestor:
    return Ingestor(mock_storage)


class TestGroupByType:
    def test_csv_grouping(self, ingestor: Ingestor) -> None:
        paths = ["/data/a.csv", "/data/b.csv", "/data/c.parquet"]
        groups = ingestor._group_by_type(paths)
        assert "csv" in groups
        assert "parquet" in groups
        assert len(groups["csv"]) == 2
        assert len(groups["parquet"]) == 1

    def test_jsonl_grouped_as_json(self, ingestor: Ingestor) -> None:
        groups = ingestor._group_by_type(["/data/a.jsonl", "/data/b.json"])
        assert "json" in groups
        assert len(groups["json"]) == 2

    def test_unsupported_extension_skipped(self, ingestor: Ingestor) -> None:
        groups = ingestor._group_by_type(["/data/a.txt", "/data/b.csv"])
        assert "txt" not in groups
        assert "csv" in groups


class TestIngestBatch:
    def test_empty_paths_raises(self, ingestor: Ingestor) -> None:
        from arrow_lake.exceptions import IngestError
        with pytest.raises(IngestError, match="No file paths"):
            ingestor.ingest_batch("test", [])

    @patch("arrow_lake.ingest.ingestor.Ingestor._read_files_df")
    def test_single_type_batch(self, mock_read: MagicMock, ingestor: Ingestor, mock_storage: MagicMock) -> None:
        mock_df = MagicMock()
        mock_count_df = MagicMock()
        mock_count_arrow = MagicMock()
        mock_count_arrow.__getitem__ = MagicMock(return_value=MagicMock(as_py=MagicMock(return_value=10)))
        mock_count_df.to_arrow.return_value = mock_count_arrow
        mock_df.count.return_value = mock_count_df
        mock_read.return_value = mock_df

        report = ingestor.ingest_batch("test", ["/a.csv", "/b.csv"])

        assert report.total_files == 2
        assert report.total_rows == 10
        mock_storage.write_lance_from_dataframe.assert_called_once()

    @patch("arrow_lake.ingest.ingestor.Ingestor._read_files_df")
    def test_with_transforms(self, mock_read: MagicMock, ingestor: Ingestor, mock_storage: MagicMock) -> None:
        mock_df = MagicMock()
        mock_count_df = MagicMock()
        mock_count_arrow = MagicMock()
        mock_count_arrow.__getitem__ = MagicMock(return_value=MagicMock(as_py=MagicMock(return_value=5)))
        mock_count_df.to_arrow.return_value = mock_count_arrow
        mock_df.count.return_value = mock_count_df
        mock_read.return_value = mock_df

        transform = MagicMock(return_value=mock_df)
        ingestor.ingest_batch("test", ["/a.csv"], transforms=[transform])

        transform.assert_called_once_with(mock_df)


class TestReadFilesDf:
    def test_unsupported_type_raises(self, ingestor: Ingestor) -> None:
        from arrow_lake.exceptions import IngestError
        with pytest.raises(IngestError, match="Batch read unsupported"):
            ingestor._read_files_df(["/a.xml"], "xml")
