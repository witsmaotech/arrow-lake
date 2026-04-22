"""Tests for version diff — Story 2.4.

Tests VersionDiffer comparing two dataset versions:
- Detects added/removed rows
- Detects schema changes
- Supports tag names
- Output is JSON-serializable
- No changes returns empty diff
"""

from __future__ import annotations

from pathlib import Path

import pyarrow as pa
import pytest
from arrow_lake.ingest.diff import VersionDiffer
from arrow_lake.ingest.storage import LanceStorageManager


class TestVersionDiff:
    """Test version comparison."""

    def test_diff_detects_added_rows(self, tmp_path: Path) -> None:
        """Diff detects rows added between versions."""
        manager = LanceStorageManager(base_uri=str(tmp_path))
        manager.create_dataset("diff_test", pa.table({"id": [1, 2]}))
        manager.append_dataset("diff_test", pa.table({"id": [3, 4]}))

        differ = VersionDiffer(manager)
        diff = differ.diff("diff_test", 1, 2)
        assert diff.added_rows == 2
        assert diff.removed_rows == 0

    def test_diff_no_schema_changes(self, tmp_path: Path) -> None:
        """Diff with no schema changes reports empty schema_changes."""
        manager = LanceStorageManager(base_uri=str(tmp_path))
        manager.create_dataset("diff_test", pa.table({"id": [1]}))
        manager.append_dataset("diff_test", pa.table({"id": [2]}))

        differ = VersionDiffer(manager)
        diff = differ.diff("diff_test", 1, 2)
        assert len(diff.schema_changes) == 0

    def test_diff_is_frozen(self, tmp_path: Path) -> None:
        """VersionDiff is immutable (frozen dataclass)."""
        manager = LanceStorageManager(base_uri=str(tmp_path))
        manager.create_dataset("diff_test", pa.table({"id": [1]}))
        manager.append_dataset("diff_test", pa.table({"id": [2]}))

        differ = VersionDiffer(manager)
        diff = differ.diff("diff_test", 1, 2)
        with pytest.raises(AttributeError):
            diff.added_rows = 99  # type: ignore[misc]

    def test_diff_same_version(self, tmp_path: Path) -> None:
        """Diffing same version returns empty diff."""
        manager = LanceStorageManager(base_uri=str(tmp_path))
        manager.create_dataset("diff_test", pa.table({"id": [1]}))

        differ = VersionDiffer(manager)
        diff = differ.diff("diff_test", 1, 1)
        assert diff.added_rows == 0
        assert diff.removed_rows == 0

    def test_diff_json_serializable(self, tmp_path: Path) -> None:
        """Diff output can be serialized to JSON."""
        import json

        manager = LanceStorageManager(base_uri=str(tmp_path))
        manager.create_dataset("diff_test", pa.table({"id": [1]}))
        manager.append_dataset("diff_test", pa.table({"id": [2]}))

        differ = VersionDiffer(manager)
        diff = differ.diff("diff_test", 1, 2)
        serialized = json.dumps(
            {
                "added_rows": diff.added_rows,
                "removed_rows": diff.removed_rows,
                "schema_changes": diff.schema_changes,
            }
        )
        assert isinstance(serialized, str)
