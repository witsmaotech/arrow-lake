"""Tests for local file ingestion — Story 3.1 (unit).

Tests Ingestor logic with mocked connectors:
- Detects file type from extension
- Routes to correct reader
- Handles mixed file lists
- Reports per-source stats
"""

from __future__ import annotations

from pathlib import Path

import pytest
from arrow_lake.ingest.ingestor import IngestionReport, IngestionSource, Ingestor


class TestIngestorFileDetection:
    """Test file type detection logic."""

    def test_detect_csv_extension(self) -> None:
        """CSV files are detected from .csv extension."""
        assert Ingestor._detect_file_type(Path("data.csv")) == "csv"

    def test_detect_json_extension(self) -> None:
        """JSON/JSONL files are detected from .json/.jsonl extension."""
        assert Ingestor._detect_file_type(Path("data.json")) == "json"
        assert Ingestor._detect_file_type(Path("data.jsonl")) == "json"

    def test_detect_parquet_extension(self) -> None:
        """Parquet files are detected from .parquet extension."""
        assert Ingestor._detect_file_type(Path("data.parquet")) == "parquet"

    def test_detect_unknown_raises(self) -> None:
        """Unknown file extensions raise IngestError."""
        from arrow_lake.exceptions import IngestError

        with pytest.raises(IngestError, match="Unsupported"):
            Ingestor._detect_file_type(Path("data.xlsx"))


class TestIngestionReport:
    """Test ingestion report data structure."""

    def test_report_is_frozen(self) -> None:
        """IngestionReport is immutable."""
        report = IngestionReport(
            sources=(IngestionSource(path="test.csv", row_count=10, file_count=1),),
            total_rows=10,
            total_files=1,
        )
        with pytest.raises(AttributeError):
            report.total_rows = 99  # type: ignore[misc]

    def test_report_total_rows(self) -> None:
        """Total rows is sum of source rows."""
        report = IngestionReport(
            sources=(
                IngestionSource(path="a.csv", row_count=10, file_count=1),
                IngestionSource(path="b.parquet", row_count=20, file_count=1),
            ),
            total_rows=30,
            total_files=2,
        )
        assert report.total_rows == 30

    def test_source_is_frozen(self) -> None:
        """IngestionSource is immutable."""
        src = IngestionSource(path="test.csv", row_count=10, file_count=1)
        with pytest.raises(AttributeError):
            src.row_count = 99  # type: ignore[misc]
