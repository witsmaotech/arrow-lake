"""Coverage for arrow_lake.ingest.ingestor — Ingestor core: _write_table, _detect_file_type, _read_file_df, _read_file, _read_bytes."""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pyarrow as pa
import pytest

from arrow_lake.exceptions import IngestError
from arrow_lake.ingest.ingestor import IngestionReport, IngestionSource, Ingestor


@pytest.fixture
def mock_manager() -> MagicMock:
    mgr = MagicMock()
    # dataset_exists returns a real bool (default MagicMock is truthy, which
    # would flip _write_table's create-vs-append decision).
    mgr.dataset_exists.return_value = False
    return mgr


@pytest.fixture
def ingestor(mock_manager: MagicMock) -> Ingestor:
    return Ingestor(mock_manager)


# ---------------------------------------------------------------------------
# __init__
# ---------------------------------------------------------------------------


class TestInit:
    def test_stores_manager(self, mock_manager: MagicMock) -> None:
        ing = Ingestor(mock_manager)
        assert ing._manager is mock_manager

    def test_quality_gate_default_none(self, mock_manager: MagicMock) -> None:
        ing = Ingestor(mock_manager)
        assert ing._quality_gate is None

    def test_quality_gate_stored(self, mock_manager: MagicMock) -> None:
        gate = MagicMock()
        ing = Ingestor(mock_manager, quality_gate=gate)
        assert ing._quality_gate is gate

    def test_first_table_seen_empty(self, mock_manager: MagicMock) -> None:
        ing = Ingestor(mock_manager)
        assert ing._first_table_seen == {}


# ---------------------------------------------------------------------------
# _detect_file_type
# ---------------------------------------------------------------------------


class TestDetectFileType:
    @pytest.mark.parametrize("path,expected", [
        ("data.csv", "csv"),
        ("data.json", "json"),
        ("data.jsonl", "json"),
        ("data.parquet", "parquet"),
        ("/path/to/file.CSV", "csv"),
        ("/path/to/file.JSON", "json"),
        ("/path/to/file.PARQUET", "parquet"),
        ("s3://bucket/path/file.csv", "csv"),
    ])
    def test_known_extensions(self, path: str, expected: str) -> None:
        assert Ingestor._detect_file_type(path) == expected

    def test_unsupported_extension_raises(self) -> None:
        with pytest.raises(IngestError, match="Unsupported file format"):
            Ingestor._detect_file_type("data.xml")

    def test_no_extension_raises(self) -> None:
        with pytest.raises(IngestError, match="Unsupported file format"):
            Ingestor._detect_file_type("datafile")

    def test_path_object(self) -> None:
        assert Ingestor._detect_file_type(Path("data.csv")) == "csv"


# ---------------------------------------------------------------------------
# _build_report
# ---------------------------------------------------------------------------


class TestBuildReport:
    def test_empty_sources(self) -> None:
        report = Ingestor._build_report([])
        assert report.total_rows == 0
        assert report.total_files == 0
        assert report.sources == ()

    def test_single_source(self) -> None:
        sources = [IngestionSource(path="/a.csv", row_count=10, file_count=1)]
        report = Ingestor._build_report(sources)
        assert report.total_rows == 10
        assert report.total_files == 1

    def test_multiple_sources(self) -> None:
        sources = [
            IngestionSource(path="/a.csv", row_count=10, file_count=2),
            IngestionSource(path="/b.csv", row_count=5, file_count=1),
        ]
        report = Ingestor._build_report(sources)
        assert report.total_rows == 15
        assert report.total_files == 3


# ---------------------------------------------------------------------------
# _write_table
# ---------------------------------------------------------------------------


class TestWriteTable:
    def test_first_write_creates_dataset(self, ingestor: Ingestor) -> None:
        table = pa.table({"x": [1]})
        sources: list[IngestionSource] = []

        ingestor._write_table("ds", table, sources, "/a.csv")

        ingestor._manager.create_dataset.assert_called_once_with("ds", table)
        assert len(sources) == 1
        assert sources[0].row_count == 1

    def test_second_write_appends(self, ingestor: Ingestor) -> None:
        table = pa.table({"x": [1]})
        sources: list[IngestionSource] = []

        ingestor._write_table("ds", table, sources, "/a.csv")
        ingestor._write_table("ds", table, sources, "/b.csv")

        ingestor._manager.create_dataset.assert_called_once()
        ingestor._manager.append_dataset.assert_called_once()
        assert len(sources) == 2

    def test_appends_when_dataset_already_exists_in_storage(self, mock_manager: MagicMock) -> None:
        # Regression: a fresh Ingestor appending to a dataset created by an
        # earlier request must APPEND (storage says it exists), not try to
        # create and fail with "already exists". This is the incremental
        # file-input case.
        mock_manager.dataset_exists.return_value = True
        ing = Ingestor(mock_manager)
        table = pa.table({"x": [1]})
        sources: list[IngestionSource] = []

        ing._write_table("ds", table, sources, "/a.csv")

        ing._manager.append_dataset.assert_called_once_with("ds", table)
        ing._manager.create_dataset.assert_not_called()

    def test_quality_gate_check_called(self, mock_manager: MagicMock) -> None:
        gate = MagicMock()
        result = MagicMock()
        result.rejected = 0
        gate.check.return_value = (pa.table({"x": [1]}), result)

        ing = Ingestor(mock_manager, quality_gate=gate)
        table = pa.table({"x": [1]})
        sources: list[IngestionSource] = []
        ing._write_table("ds", table, sources, "/a.csv")

        gate.check.assert_called_once()

    def test_quality_gate_rejections_logged(self, mock_manager: MagicMock) -> None:
        gate = MagicMock()
        result = MagicMock()
        result.rejected = 5
        result.rejection_reasons = ["null values"]
        gate.check.return_value = (pa.table({"x": [1]}), result)

        ing = Ingestor(mock_manager, quality_gate=gate)
        table = pa.table({"x": [1]})
        sources: list[IngestionSource] = []

        with patch("structlog.get_logger") as mock_logger:
            ing._write_table("ds", table, sources, "/a.csv")
            mock_logger.return_value.info.assert_called_once()


# ---------------------------------------------------------------------------
# _read_file_df
# ---------------------------------------------------------------------------


class TestReadFileDf:
    @patch("daft.read_csv")
    def test_csv(self, mock_read: MagicMock) -> None:
        mock_read.return_value = "csv_df"
        result = Ingestor._read_file_df("/a.csv", "csv")
        assert result == "csv_df"

    @patch("daft.read_json")
    def test_json(self, mock_read: MagicMock) -> None:
        mock_read.return_value = "json_df"
        result = Ingestor._read_file_df("/a.json", "json")
        assert result == "json_df"

    @patch("daft.read_parquet")
    def test_parquet(self, mock_read: MagicMock) -> None:
        mock_read.return_value = "pq_df"
        result = Ingestor._read_file_df("/a.parquet", "parquet")
        assert result == "pq_df"

    @patch("daft.read_csv")
    def test_csv_with_columns(self, mock_read: MagicMock) -> None:
        mock_read.return_value = "df"
        Ingestor._read_file_df("/a.csv", "csv", columns=["col1"])
        mock_read.assert_called_once_with("/a.csv", columns=["col1"])

    @patch("daft.read_parquet")
    def test_parquet_with_columns(self, mock_read: MagicMock) -> None:
        mock_read.return_value = "df"
        Ingestor._read_file_df("/a.parquet", "parquet", columns=["col1"])
        mock_read.assert_called_once_with("/a.parquet", columns=["col1"])

    def test_json_ignores_columns(self) -> None:
        """JSON reader doesn't support column pruning."""
        with patch("daft.read_json") as mock_read:
            mock_read.return_value = "df"
            Ingestor._read_file_df("/a.json", "json", columns=["col1"])
            mock_read.assert_called_once_with("/a.json")

    def test_unsupported_type_raises(self) -> None:
        with pytest.raises(IngestError, match="Unsupported file type"):
            Ingestor._read_file_df("/a.xml", "xml")

    @patch("daft.read_csv")
    def test_os_error_wraps_to_ingest_error(self, mock_read: MagicMock) -> None:
        mock_read.side_effect = OSError("file not found")
        with pytest.raises(IngestError, match="Failed to read"):
            Ingestor._read_file_df("/missing.csv", "csv")

    @patch("daft.read_csv")
    def test_value_error_wraps_to_ingest_error(self, mock_read: MagicMock) -> None:
        mock_read.side_effect = ValueError("bad format")
        with pytest.raises(IngestError, match="Failed to read"):
            Ingestor._read_file_df("/bad.csv", "csv")

    def test_ingest_error_reraises(self) -> None:
        """IngestError from unsupported type should re-raise as-is."""
        with pytest.raises(IngestError, match="Unsupported file type"):
            Ingestor._read_file_df("/a.xml", "xml")


# ---------------------------------------------------------------------------
# _read_file
# ---------------------------------------------------------------------------


class TestReadFile:
    @patch.object(Ingestor, "_read_file_df")
    def test_returns_arrow_table(self, mock_read_df: MagicMock) -> None:
        mock_df = MagicMock()
        mock_df.to_arrow.return_value = pa.table({"x": [1, 2]})
        mock_read_df.return_value = mock_df

        result = Ingestor._read_file("/a.csv", "csv")
        assert isinstance(result, pa.Table)
        assert result.num_rows == 2

    @patch.object(Ingestor, "_read_file_df")
    def test_passes_columns(self, mock_read_df: MagicMock) -> None:
        mock_df = MagicMock()
        mock_df.to_arrow.return_value = pa.table({"x": [1]})
        mock_read_df.return_value = mock_df

        Ingestor._read_file("/a.csv", "csv", columns=["x"])
        mock_read_df.assert_called_once_with("/a.csv", "csv", columns=["x"])


# ---------------------------------------------------------------------------
# _read_bytes
# ---------------------------------------------------------------------------


class TestReadBytes:
    def test_csv_bytes(self) -> None:
        csv_content = b"a,b\n1,2\n3,4\n"
        with patch("daft.read_csv") as mock_read:
            mock_df = MagicMock()
            mock_df.to_arrow.return_value = pa.table({"a": [1, 3], "b": [2, 4]})
            mock_read.return_value = mock_df

            result = Ingestor._read_bytes(csv_content, "csv")
            assert isinstance(result, pa.Table)
            assert result.num_rows == 2

    def test_json_bytes(self) -> None:
        json_content = b'[{"x": 1}]'
        with patch("daft.read_json") as mock_read:
            mock_df = MagicMock()
            mock_df.to_arrow.return_value = pa.table({"x": [1]})
            mock_read.return_value = mock_df

            result = Ingestor._read_bytes(json_content, "json")
            assert isinstance(result, pa.Table)

    def test_parquet_bytes(self) -> None:
        # Use mocked read_parquet since we just need to test the code path
        table = pa.table({"x": [10]})
        with patch("daft.read_parquet") as mock_read:
            mock_df = MagicMock()
            mock_df.to_arrow.return_value = table
            mock_read.return_value = mock_df

            result = Ingestor._read_bytes(b"fake parquet", "parquet")
            assert result.num_rows == 1

    def test_unsupported_type_raises(self) -> None:
        with pytest.raises(IngestError, match="Unsupported file type"):
            Ingestor._read_bytes(b"data", "xml")

    def test_temp_file_cleaned_up(self) -> None:
        """Verify temp file is cleaned up after reading."""
        csv_content = b"col\n1"
        with patch("daft.read_csv") as mock_read:
            mock_df = MagicMock()
            mock_df.to_arrow.return_value = pa.table({"col": [1]})
            mock_read.return_value = mock_df

            # Track the temp path
            temp_paths: list[str] = []

            original_mkstemp = __import__("tempfile").mkstemp

            def tracking_mkstemp(*args, **kwargs):
                fd, path = original_mkstemp(*args, **kwargs)
                temp_paths.append(path)
                return fd, path

            with patch("tempfile.mkstemp", side_effect=tracking_mkstemp):
                Ingestor._read_bytes(csv_content, "csv")

            # Temp file should be cleaned up
            assert len(temp_paths) == 1
            assert not os.path.exists(temp_paths[0])

    def test_os_error_wraps(self) -> None:
        with patch("daft.read_csv") as mock_read:
            mock_read.side_effect = OSError("read error")
            with pytest.raises(IngestError, match="Failed to read content"):
                Ingestor._read_bytes(b"data", "csv")

    def test_ingest_error_reraises(self) -> None:
        """IngestError (e.g. unsupported type) should re-raise as-is."""
        with pytest.raises(IngestError, match="Unsupported file type"):
            Ingestor._read_bytes(b"data", "xml")


# ---------------------------------------------------------------------------
# IngestionSource / IngestionReport dataclasses
# ---------------------------------------------------------------------------


class TestDataclasses:
    def test_source_defaults(self) -> None:
        s = IngestionSource(path="/a.csv", row_count=10)
        assert s.file_count == 1

    def test_report_defaults(self) -> None:
        r = IngestionReport()
        assert r.sources == ()
        assert r.total_rows == 0
        assert r.total_files == 0

    def test_source_frozen(self) -> None:
        s = IngestionSource(path="/a.csv", row_count=10)
        with pytest.raises(AttributeError):
            s.path = "/b.csv"  # type: ignore[misc]

    def test_report_frozen(self) -> None:
        r = IngestionReport()
        with pytest.raises(AttributeError):
            r.total_rows = 5  # type: ignore[misc]


# ---------------------------------------------------------------------------
# _SUPPORTED_EXTENSIONS
# ---------------------------------------------------------------------------


class TestSupportedExtensions:
    def test_all_extensions_mapped(self) -> None:
        ext = Ingestor._SUPPORTED_EXTENSIONS
        assert ext[".csv"] == "csv"
        assert ext[".json"] == "json"
        assert ext[".jsonl"] == "json"
        assert ext[".parquet"] == "parquet"
