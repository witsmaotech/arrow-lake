"""Tests for time-travel query — Story 2.3.

Tests reading data at specific versions:
- read_dataset with version=N returns correct historical data
- Reading old version does not affect current version
- Nonexistent version raises error
"""

from __future__ import annotations

from pathlib import Path

import pyarrow as pa
import pytest
from arrow_lake.exceptions import StorageError
from arrow_lake.ingest.storage import LanceStorageManager


class TestTimeTravel:
    """Test version-specific data reading."""

    def test_read_at_version_1(self, tmp_path: Path) -> None:
        """Reading version 1 returns initial data only."""
        manager = LanceStorageManager(base_uri=str(tmp_path))
        manager.create_dataset("tt_test", pa.table({"x": [10, 20]}))
        manager.append_dataset("tt_test", pa.table({"x": [30]}))

        v1 = manager.read_dataset("tt_test", version=1)
        assert v1.num_rows == 2
        assert v1.column("x").to_pylist() == [10, 20]

    def test_read_at_latest_version(self, tmp_path: Path) -> None:
        """Reading without version returns latest data."""
        manager = LanceStorageManager(base_uri=str(tmp_path))
        manager.create_dataset("tt_test", pa.table({"x": [10]}))
        manager.append_dataset("tt_test", pa.table({"x": [20]}))

        latest = manager.read_dataset("tt_test")
        assert latest.num_rows == 2
        assert latest.column("x").to_pylist() == [10, 20]

    def test_read_nonexistent_version_raises(self, tmp_path: Path) -> None:
        """Reading a version that doesn't exist raises StorageError."""
        manager = LanceStorageManager(base_uri=str(tmp_path))
        manager.create_dataset("tt_test", pa.table({"x": [1]}))

        with pytest.raises(StorageError, match="not found"):
            manager.read_dataset("tt_test", version=999)

    def test_time_travel_preserves_current(self, tmp_path: Path) -> None:
        """Reading an old version does not change the current version."""
        manager = LanceStorageManager(base_uri=str(tmp_path))
        manager.create_dataset("tt_test", pa.table({"x": [1]}))
        manager.append_dataset("tt_test", pa.table({"x": [2]}))

        _ = manager.read_dataset("tt_test", version=1)

        current = manager.read_dataset("tt_test")
        assert current.num_rows == 2
        assert current.column("x").to_pylist() == [1, 2]
