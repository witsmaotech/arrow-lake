"""Tests for named tags — Story 2.2.

Tests LanceStorageManager tag operations:
- create_tag, list_tags, delete_tag
- read_at_tag
- Duplicate tag raises
- Nonexistent tag raises
"""

from __future__ import annotations

from pathlib import Path

import pyarrow as pa
import pytest
from arrow_lake.exceptions import StorageError
from arrow_lake.ingest.storage import LanceStorageManager


class TestNamedTags:
    """Test tag lifecycle."""

    def test_create_and_list_tag(self, tmp_path: Path) -> None:
        """Tags can be created and listed."""
        manager = LanceStorageManager(base_uri=str(tmp_path))
        manager.create_dataset("tag_test", pa.table({"x": [1]}))
        manager.append_dataset("tag_test", pa.table({"x": [2]}))

        manager.create_tag("tag_test", "v1", version=1)
        tags = manager.list_tags("tag_test")
        assert "v1" in tags
        assert tags["v1"] == 1

    def test_delete_tag(self, tmp_path: Path) -> None:
        """Tags can be deleted."""
        manager = LanceStorageManager(base_uri=str(tmp_path))
        manager.create_dataset("tag_test", pa.table({"x": [1]}))
        manager.create_tag("tag_test", "v1", version=1)

        manager.delete_tag("tag_test", "v1")
        tags = manager.list_tags("tag_test")
        assert "v1" not in tags

    def test_read_at_tag(self, tmp_path: Path) -> None:
        """Reading at a tag returns data from that version."""
        manager = LanceStorageManager(base_uri=str(tmp_path))
        manager.create_dataset("tag_test", pa.table({"x": [10, 20]}))
        manager.append_dataset("tag_test", pa.table({"x": [30]}))
        manager.create_tag("tag_test", "initial", version=1)

        tagged_data = manager.read_at_tag("tag_test", "initial")
        assert tagged_data.num_rows == 2
        assert tagged_data.column("x").to_pylist() == [10, 20]

    def test_create_duplicate_tag_raises(self, tmp_path: Path) -> None:
        """Creating a tag that already exists raises StorageError."""
        manager = LanceStorageManager(base_uri=str(tmp_path))
        manager.create_dataset("tag_test", pa.table({"x": [1]}))
        manager.create_tag("tag_test", "v1", version=1)

        with pytest.raises(StorageError, match="already exists"):
            manager.create_tag("tag_test", "v1", version=1)

    def test_delete_nonexistent_tag_raises(self, tmp_path: Path) -> None:
        """Deleting a nonexistent tag raises StorageError."""
        manager = LanceStorageManager(base_uri=str(tmp_path))
        manager.create_dataset("tag_test", pa.table({"x": [1]}))

        with pytest.raises(StorageError, match="not found"):
            manager.delete_tag("tag_test", "ghost_tag")

    def test_read_at_nonexistent_tag_raises(self, tmp_path: Path) -> None:
        """Reading at a nonexistent tag raises StorageError."""
        manager = LanceStorageManager(base_uri=str(tmp_path))
        manager.create_dataset("tag_test", pa.table({"x": [1]}))

        with pytest.raises(StorageError, match="not found"):
            manager.read_at_tag("tag_test", "ghost_tag")
