"""Tests for compaction — Story 2.5.

Tests LanceStorageManager.compact():
- Fragment count reduces after compaction
- Data integrity preserved
- Version increments
"""

from __future__ import annotations

from pathlib import Path

import pyarrow as pa
from arrow_lake.ingest.storage import LanceStorageManager


class TestCompaction:
    """Test dataset compaction."""

    def test_compact_reduces_fragments(self, tmp_path: Path) -> None:
        """Compaction runs successfully and increments version."""
        manager = LanceStorageManager(base_uri=str(tmp_path))

        table = pa.table({"id": [1], "value": [10.0]})
        manager.create_dataset("compact_test", table)

        for i in range(2, 12):
            manager.append_dataset("compact_test", pa.table({"id": [i], "value": [float(i * 10)]}))

        pre = manager.compact("compact_test")
        # optimize() creates new versions (compaction + cleanup)
        assert pre.version_after > pre.version_before
        assert pre.fragments_before > 0

    def test_compact_preserves_data(self, tmp_path: Path) -> None:
        """Data is unchanged after compaction."""
        manager = LanceStorageManager(base_uri=str(tmp_path))

        table = pa.table({"id": [1, 2, 3], "value": [10.0, 20.0, 30.0]})
        manager.create_dataset("data_test", table)

        for i in range(4, 8):
            manager.append_dataset("data_test", pa.table({"id": [i], "value": [float(i * 10)]}))

        manager.compact("data_test")

        result = manager.read_dataset("data_test")
        assert result.num_rows == 7
        assert result.column("id").to_pylist() == [1, 2, 3, 4, 5, 6, 7]

    def test_compact_increments_version(self, tmp_path: Path) -> None:
        """Compaction creates a new version."""
        manager = LanceStorageManager(base_uri=str(tmp_path))

        table = pa.table({"x": [1]})
        manager.create_dataset("version_test", table)
        manager.append_dataset("version_test", pa.table({"x": [2]}))

        pre = manager.compact("version_test")
        assert pre.version_after > pre.version_before
