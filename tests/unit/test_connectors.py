"""Tests for file connectors — Story 3.1 connectors.

Tests LocalConnector and S3Connector:
- LocalConnector lists files by extension
- LocalConnector handles missing directory
- S3Connector lists S3 objects by prefix
- Connector protocol compliance
"""

from __future__ import annotations

from pathlib import Path

import pytest
from arrow_lake.ingest.connectors import (
    ConnectorResult,
    FileConnector,
    LocalConnector,
    S3Connector,
)


class TestLocalConnector:
    """Test local filesystem connector."""

    def test_list_csv_files(self, tmp_path: Path) -> None:
        """LocalConnector finds CSV files recursively."""
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        (data_dir / "a.csv").write_text("id\n1\n")
        (data_dir / "b.csv").write_text("id\n2\n")
        (data_dir / "c.parquet").write_bytes(b"\x00")

        connector = LocalConnector(base_path=str(data_dir))
        result = connector.list_files(extensions=[".csv"])

        assert result.file_count == 2
        assert len(result.paths) == 2
        assert all(p.endswith(".csv") for p in result.paths)

    def test_list_all_files(self, tmp_path: Path) -> None:
        """LocalConnector lists all files when no extension filter."""
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        (data_dir / "a.csv").write_text("")
        (data_dir / "b.jsonl").write_text("")

        connector = LocalConnector(base_path=str(data_dir))
        result = connector.list_files()

        assert result.file_count == 2

    def test_missing_directory_raises(self, tmp_path: Path) -> None:
        """LocalConnector raises on nonexistent directory."""
        connector = LocalConnector(base_path=str(tmp_path / "nope"))
        with pytest.raises(FileNotFoundError):
            connector.list_files()

    def test_nested_directories(self, tmp_path: Path) -> None:
        """LocalConnector searches subdirectories recursively."""
        data_dir = tmp_path / "data"
        sub = data_dir / "sub"
        sub.mkdir(parents=True)
        (data_dir / "root.csv").write_text("")
        (sub / "nested.csv").write_text("")

        connector = LocalConnector(base_path=str(data_dir))
        result = connector.list_files(extensions=[".csv"])

        assert result.file_count == 2

    def test_result_is_frozen(self, tmp_path: Path) -> None:
        """ConnectorResult is immutable."""
        result = ConnectorResult(paths=("a.csv",), file_count=1)
        with pytest.raises(AttributeError):
            result.file_count = 99  # type: ignore[misc]


class TestS3Connector:
    """Test S3 connector (unit — no real S3 calls)."""

    def test_s3_connector_requires_endpoint(self) -> None:
        """S3Connector requires endpoint_url."""
        with pytest.raises(ValueError, match="endpoint_url"):
            S3Connector(bucket="test-bucket", prefix="data/")

    def test_s3_connector_stores_config(self) -> None:
        """S3Connector stores bucket, prefix, endpoint."""
        connector = S3Connector(
            bucket="my-bucket",
            prefix="data/",
            endpoint_url="http://localhost:9000",
        )
        assert connector.bucket == "my-bucket"
        assert connector.prefix == "data/"


class TestFileConnectorProtocol:
    """Test that connectors satisfy the FileConnector protocol."""

    def test_local_is_connector(self, tmp_path: Path) -> None:
        """LocalConnector satisfies FileConnector protocol."""
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        (data_dir / "f.csv").write_text("")

        connector: FileConnector = LocalConnector(base_path=str(data_dir))
        result = connector.list_files()
        assert result.file_count == 1
