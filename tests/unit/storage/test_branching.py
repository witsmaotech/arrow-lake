"""Tests for Lance dataset branching — v1.8.0 #1.

Tests StorageVersioningMixin branch operations (Git-style data branching):
- create_branch (HEAD + explicit version)
- list_branches
- read_at_branch (branch HEAD data)
- delete_branch + error mapping
- duplicate-branch and not-found error mapping
- branch/tag independence
"""

from __future__ import annotations

from pathlib import Path

import pyarrow as pa
import pytest
from arrow_lake.exceptions import StorageError
from arrow_lake.ingest.storage import LanceStorageManager


class TestBranching:
    """Test branch lifecycle operations on real Lance datasets."""

    def test_create_and_list_branch(self, tmp_path: Path) -> None:
        """create_branch at HEAD then list_branches includes it."""
        manager = LanceStorageManager(base_uri=str(tmp_path))
        manager.create_dataset("br_test", pa.table({"x": [1, 2]}))

        manager.create_branch("br_test", "dev")
        branches = manager.list_branches("br_test")

        assert "dev" in branches

    def test_read_at_branch_returns_head_data(self, tmp_path: Path) -> None:
        """read_at_branch returns the data snapshot at branch HEAD."""
        manager = LanceStorageManager(base_uri=str(tmp_path))
        manager.create_dataset("br_test", pa.table({"x": [1, 2]}))

        manager.create_branch("br_test", "dev")
        table = manager.read_at_branch("br_test", "dev")

        assert table.num_rows == 2
        assert table.column("x").to_pylist() == [1, 2]

    def test_branch_isolates_from_main_writes(self, tmp_path: Path) -> None:
        """A branch created at HEAD keeps the HEAD snapshot after main advances.

        Lance branches are refs to a version snapshot; reading the branch after
        appending to main still yields the pre-append rows.
        """
        manager = LanceStorageManager(base_uri=str(tmp_path))
        manager.create_dataset("br_test", pa.table({"x": [1, 2]}))
        manager.create_branch("br_test", "dev")

        # Advance main with a new append
        manager.append_dataset("br_test", pa.table({"x": [3, 4]}))

        main_rows = manager.read_dataset("br_test").num_rows
        branch_rows = manager.read_at_branch("br_test", "dev").num_rows

        assert main_rows == 4
        assert branch_rows == 2  # branch still points at the original snapshot

    def test_delete_branch_removes_from_list(self, tmp_path: Path) -> None:
        """delete_branch removes the branch from list_branches."""
        manager = LanceStorageManager(base_uri=str(tmp_path))
        manager.create_dataset("br_test", pa.table({"x": [1]}))
        manager.create_branch("br_test", "dev")

        manager.delete_branch("br_test", "dev")
        assert "dev" not in manager.list_branches("br_test")

    def test_delete_missing_branch_raises(self, tmp_path: Path) -> None:
        """delete_branch on a nonexistent branch raises StorageError."""
        manager = LanceStorageManager(base_uri=str(tmp_path))
        manager.create_dataset("br_test", pa.table({"x": [1]}))

        with pytest.raises(StorageError):
            manager.delete_branch("br_test", "nope")

    def test_duplicate_create_raises(self, tmp_path: Path) -> None:
        """create_branch with an existing name raises StorageError."""
        manager = LanceStorageManager(base_uri=str(tmp_path))
        manager.create_dataset("br_test", pa.table({"x": [1]}))
        manager.create_branch("br_test", "dev")

        with pytest.raises(StorageError):
            manager.create_branch("br_test", "dev")

    def test_read_missing_branch_raises(self, tmp_path: Path) -> None:
        """read_at_branch on a nonexistent branch raises StorageError."""
        manager = LanceStorageManager(base_uri=str(tmp_path))
        manager.create_dataset("br_test", pa.table({"x": [1]}))

        with pytest.raises(StorageError):
            manager.read_at_branch("br_test", "nope")

    def test_read_branch_on_missing_dataset_raises(self, tmp_path: Path) -> None:
        """read_at_branch on a nonexistent dataset raises StorageError."""
        manager = LanceStorageManager(base_uri=str(tmp_path))

        with pytest.raises(StorageError):
            manager.read_at_branch("nope", "dev")

    def test_branch_and_tag_coexist(self, tmp_path: Path) -> None:
        """A branch and a tag are independent refs and can share a dataset."""
        manager = LanceStorageManager(base_uri=str(tmp_path))
        manager.create_dataset("br_test", pa.table({"x": [1]}))

        manager.create_branch("br_test", "dev")
        manager.create_tag("br_test", "v1")

        assert "dev" in manager.list_branches("br_test")
        assert "v1" in manager.list_tags("br_test")
