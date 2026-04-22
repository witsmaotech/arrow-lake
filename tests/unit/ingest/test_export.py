"""Tests for arrow_lake.query.export — Story 5.9 Data Export."""

from __future__ import annotations

import os
import tempfile

import pyarrow as pa
import pytest
from arrow_lake.exceptions import ErrorCode, StorageError
from arrow_lake.query.export import ExportBridge, ExportResult

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_table(n: int = 5) -> pa.Table:
    return pa.table(
        {
            "id": list(range(n)),
            "text_content": [f"row-{i}" for i in range(n)],
            "score": [float(i) for i in range(n)],
            "image_data": [b"img-bytes"] * n,
        }
    )


class _MockStorage:
    """Mock storage manager for unit tests."""

    def read_dataset(
        self, dataset_name: str, version: int | None = None, columns: list[str] | None = None
    ):
        return _make_table(5)


class _MockConfig:
    """Mock export config."""

    parquet_compression = "snappy"
    csv_delimiter = ","


# ---------------------------------------------------------------------------
# ExportResult
# ---------------------------------------------------------------------------


class TestExportResult:
    """Test ExportResult frozen dataclass."""

    def test_is_frozen(self) -> None:
        result = ExportResult(
            dataset_name="test",
            output_path="/tmp/test.parquet",
            format="parquet",
            row_count=5,
            column_count=3,
            file_size_bytes=1024,
            version=None,
        )
        with pytest.raises(AttributeError):
            result.row_count = 10  # type: ignore[misc]

    def test_fields(self) -> None:
        result = ExportResult(
            dataset_name="test",
            output_path="/tmp/test.parquet",
            format="parquet",
            row_count=5,
            column_count=3,
            file_size_bytes=1024,
            version=1,
        )
        assert result.dataset_name == "test"
        assert result.format == "parquet"
        assert result.row_count == 5
        assert result.column_count == 3
        assert result.file_size_bytes == 1024
        assert result.version == 1


# ---------------------------------------------------------------------------
# Format detection
# ---------------------------------------------------------------------------


class TestFormatDetection:
    """Test _detect_format static method."""

    def test_parquet_suffix(self) -> None:
        assert ExportBridge._detect_format("out.parquet", None) == "parquet"

    def test_csv_suffix(self) -> None:
        assert ExportBridge._detect_format("out.csv", None) == "csv"

    def test_explicit_format_overrides_suffix(self) -> None:
        assert ExportBridge._detect_format("out.txt", "parquet") == "parquet"

    def test_unsupported_explicit_raises(self) -> None:
        with pytest.raises(StorageError, match="Unsupported export format"):
            ExportBridge._detect_format("out.parquet", "json")

    def test_unknown_suffix_raises(self) -> None:
        with pytest.raises(StorageError, match="Cannot detect format"):
            ExportBridge._detect_format("out.txt", None)

    def test_case_insensitive_suffix(self) -> None:
        assert ExportBridge._detect_format("out.PARQUET", None) == "parquet"
        assert ExportBridge._detect_format("out.CSV", None) == "csv"


# ---------------------------------------------------------------------------
# Binary column exclusion
# ---------------------------------------------------------------------------


class TestBinaryColumnExclusion:
    """Test that binary columns are excluded from CSV exports."""

    def test_csv_excludes_image_data(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "out.csv")
            table = _make_table(3)
            bridge = ExportBridge(_MockStorage(), config=_MockConfig())

            result = bridge.export_table(table, path, format="csv")

            assert "image_data" not in result.output_path  # just checking result
            assert result.format == "csv"
            # image_data should be excluded
            assert (
                "image_data"
                not in table.select(
                    [c for c in table.column_names if c not in {"image_data"}]
                ).column_names
            )

    def test_parquet_includes_all_columns(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "out.parquet")
            table = _make_table(3)
            bridge = ExportBridge(_MockStorage(), config=_MockConfig())

            result = bridge.export_table(table, path, format="parquet")

            assert result.column_count == table.num_columns


# ---------------------------------------------------------------------------
# Parquet export
# ---------------------------------------------------------------------------


class TestExportParquet:
    """Test Parquet export."""

    def test_basic_parquet_export(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "out.parquet")
            table = _make_table(3)
            bridge = ExportBridge(_MockStorage(), config=_MockConfig())

            result = bridge.export_table(table, path, format="parquet")

            assert result.format == "parquet"
            assert result.row_count == 3
            assert result.file_size_bytes > 0
            assert os.path.exists(path)

    def test_parquet_compression(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "out.parquet")
            table = _make_table(3)
            bridge = ExportBridge(_MockStorage(), config=_MockConfig())

            result = bridge.export_table(table, path, format="parquet", compression="gzip")

            assert os.path.exists(path)
            assert result.file_size_bytes > 0

    def test_column_selection(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "out.parquet")
            table = _make_table(3)
            bridge = ExportBridge(_MockStorage(), config=_MockConfig())

            result = bridge.export_table(table, path, format="parquet", columns=["id", "score"])

            assert result.column_count == 2

    def test_column_not_found_raises(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "out.parquet")
            table = _make_table(3)
            bridge = ExportBridge(_MockStorage(), config=_MockConfig())

            with pytest.raises(StorageError, match="Columns not found"):
                bridge.export_table(table, path, format="parquet", columns=["nonexistent"])


# ---------------------------------------------------------------------------
# CSV export
# ---------------------------------------------------------------------------


class TestExportCSV:
    """Test CSV export."""

    def test_basic_csv_export(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "out.csv")
            table = _make_table(3)
            bridge = ExportBridge(_MockStorage(), config=_MockConfig())

            result = bridge.export_table(table, path, format="csv")

            assert result.format == "csv"
            assert result.row_count == 3
            assert result.file_size_bytes > 0
            assert os.path.exists(path)


# ---------------------------------------------------------------------------
# Overwrite behavior
# ---------------------------------------------------------------------------


class TestOverwrite:
    """Test overwrite parameter behavior."""

    def test_overwrite_false_raises_on_existing(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "out.parquet")
            table = _make_table(1)
            bridge = ExportBridge(_MockStorage(), config=_MockConfig())

            # First write succeeds
            bridge.export_table(table, path, format="parquet", overwrite=True)

            # Second write without overwrite raises
            with pytest.raises(StorageError, match="overwrite"):
                bridge.export_table(table, path, format="parquet", overwrite=False)

    def test_overwrite_true_succeeds(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "out.parquet")
            table = _make_table(1)
            bridge = ExportBridge(_MockStorage(), config=_MockConfig())

            bridge.export_table(table, path, format="parquet", overwrite=True)
            result = bridge.export_table(table, path, format="parquet", overwrite=True)

            assert result.row_count == 1


# ---------------------------------------------------------------------------
# Export from dataset
# ---------------------------------------------------------------------------


class TestExportDataset:
    """Test export() method that reads from storage."""

    def test_export_from_storage(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "out.parquet")
            bridge = ExportBridge(_MockStorage(), config=_MockConfig())

            result = bridge.export("test_dataset", path)

            assert result.format == "parquet"
            assert result.row_count == 5


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------


class TestErrorHandling:
    """Test error handling for edge cases."""

    def test_unsupported_format_raises(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "out.json")
            table = _make_table(1)
            bridge = ExportBridge(_MockStorage(), config=_MockConfig())

            with pytest.raises(StorageError) as exc_info:
                bridge.export_table(table, path, format="json")

            assert exc_info.value.error_code == ErrorCode.EXPORT_FORMAT_NOT_SUPPORTED
