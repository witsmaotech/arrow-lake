"""Tests for export.py path traversal security (P0 bug fix).

Tests that the path traversal validation in ExportBlock rejects
malicious paths with '..' components.
"""

from __future__ import annotations

from pathlib import Path

import pyarrow as pa
import pytest
from arrow_lake.exceptions import StorageError
from arrow_lake.query.export import ExportBridge


class TestPathTraversalPrevention:
    """Test that path traversal is blocked."""

    def test_path_with_dotdot_rejected(self, tmp_path: Path) -> None:
        """Paths containing '..' are rejected."""
        bridge = ExportBridge(None)
        table = pa.table({"a": [1, 2, 3]})

        with pytest.raises(StorageError, match="Path traversal"):
            bridge.export_table(table, "../../etc/passwd")

    def test_path_with_dotdot_middle_rejected(self, tmp_path: Path) -> None:
        """Paths with '..' in the middle are rejected."""
        bridge = ExportBridge(None)
        table = pa.table({"a": [1]})

        with pytest.raises(StorageError, match="Path traversal"):
            bridge.export_table(table, str(tmp_path / "safe" / ".." / "output.csv"))

    def test_normal_path_accepted(self, tmp_path: Path) -> None:
        """Normal paths without '..' are accepted."""
        bridge = ExportBridge(None)
        table = pa.table({"a": [1, 2, 3]})
        output = str(tmp_path / "normal_output.parquet")

        result = bridge.export_table(table, output)
        assert result.format == "parquet"

    def test_absolute_path_without_dotdot_accepted(self, tmp_path: Path) -> None:
        """Absolute paths without '..' are accepted."""
        bridge = ExportBridge(None)
        table = pa.table({"a": [1]})
        output = str(tmp_path / "absolute_output.csv")

        result = bridge.export_table(table, output)
        assert result.format == "csv"
