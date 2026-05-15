"""Unit tests for Lakehouse connectors — Daft Phase 2, Sprint 7."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from arrow_lake.exceptions import IngestError
from arrow_lake.ingest.connectors_lakehouse import DeltaConnector, IcebergConnector


class TestIcebergConnector:
    def test_read_success(self) -> None:
        mock_df = MagicMock()
        with patch("daft.read_iceberg", return_value=mock_df) as mock_ri:
            c = IcebergConnector("s3://warehouse/db/table")
            result = c.read()
            mock_ri.assert_called_once()
            assert result is mock_df

    def test_read_failure_raises(self) -> None:
        with patch("daft.read_iceberg", side_effect=RuntimeError("not found")):
            c = IcebergConnector("s3://warehouse/missing")
            with pytest.raises(IngestError, match="Iceberg read failed"):
                c.read()

    def test_io_config_passed(self) -> None:
        mock_config = MagicMock()
        with patch("daft.read_iceberg", return_value=MagicMock()) as mock_ri:
            c = IcebergConnector("s3://wh/t", io_config=mock_config)
            c.read()
            assert mock_ri.call_args.kwargs["io_config"] is mock_config


class TestDeltaConnector:
    def test_read_success(self) -> None:
        mock_df = MagicMock()
        with patch("daft.read_deltalake", return_value=mock_df):
            c = DeltaConnector("s3://delta/sales")
            result = c.read()
            assert result is mock_df

    def test_read_with_version(self) -> None:
        with patch("daft.read_deltalake", return_value=MagicMock()) as mock_rd:
            c = DeltaConnector("s3://delta/sales", version=5)
            c.read()
            assert mock_rd.call_args.kwargs["version"] == 5

    def test_read_failure_raises(self) -> None:
        with patch("daft.read_deltalake", side_effect=RuntimeError("not found")):
            c = DeltaConnector("s3://delta/missing")
            with pytest.raises(IngestError, match="Delta Lake read failed"):
                c.read()
