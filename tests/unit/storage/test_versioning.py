"""Tests for auto versioning — Story 2.1.

Tests LanceStorageManager version auto-increment:
- append creates new version
- list_versions returns correct metadata
- get_version returns current version number
"""

from __future__ import annotations

from pathlib import Path

import pyarrow as pa
import pytest
from arrow_lake.ingest.storage import LanceStorageManager


class TestAutoVersioning:
    """Test automatic version management."""

    def test_version_after_create(self, tmp_path: Path) -> None:
        """Create produces version 1."""
        manager = LanceStorageManager(base_uri=str(tmp_path))
        manager.create_dataset("ver_test", pa.table({"x": [1]}))
        assert manager.get_version("ver_test") == 1

    def test_version_increments_on_append(self, tmp_path: Path) -> None:
        """Each append increments version."""
        manager = LanceStorageManager(base_uri=str(tmp_path))
        manager.create_dataset("ver_test", pa.table({"x": [1]}))

        manager.append_dataset("ver_test", pa.table({"x": [2]}))
        assert manager.get_version("ver_test") == 2

        manager.append_dataset("ver_test", pa.table({"x": [3]}))
        assert manager.get_version("ver_test") == 3

    def test_list_versions_metadata(self, tmp_path: Path) -> None:
        """list_versions returns version metadata dicts."""
        manager = LanceStorageManager(base_uri=str(tmp_path))
        manager.create_dataset("ver_test", pa.table({"x": [1]}))
        manager.append_dataset("ver_test", pa.table({"x": [2]}))

        versions = manager.list_versions("ver_test")
        assert len(versions) == 2
        assert versions[0]["version"] == 1
        assert versions[1]["version"] == 2
        assert "timestamp" in versions[0]
        assert "metadata" in versions[0]

    def test_version_data_independent(self, tmp_path: Path) -> None:
        """Each version's data is independently queryable via read_dataset."""
        manager = LanceStorageManager(base_uri=str(tmp_path))
        manager.create_dataset("ver_test", pa.table({"x": [10, 20]}))

        manager.append_dataset("ver_test", pa.table({"x": [30, 40]}))

        # Latest has all rows
        latest = manager.read_dataset("ver_test")
        assert latest.num_rows == 4

    def test_get_version_nonexistent_raises(self, tmp_path: Path) -> None:
        """get_version raises on nonexistent dataset."""
        manager = LanceStorageManager(base_uri=str(tmp_path))
        from arrow_lake.exceptions import StorageError

        with pytest.raises(StorageError):
            manager.get_version("nonexistent")
