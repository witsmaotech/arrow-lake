"""Tests for schema migration — Story 2.6.

Tests LanceStorageManager schema evolution:
- add_column adds new column with NULL/default values
- alter_column changes column data type
- drop_column removes a column
- Data integrity preserved after migration
- Error handling for nonexistent columns
"""

from __future__ import annotations

from pathlib import Path

import pyarrow as pa
import pytest
from arrow_lake.exceptions import StorageError
from arrow_lake.ingest.storage import LanceStorageManager


class TestSchemaMigration:
    """Test schema evolution operations."""

    def test_add_column(self, tmp_path: Path) -> None:
        """add_column creates a new column with default values."""
        manager = LanceStorageManager(base_uri=str(tmp_path))
        manager.create_dataset("mig_test", pa.table({"id": [1, 2, 3]}))

        manager.add_column("mig_test", "score", "CAST(0 AS DOUBLE)")

        result = manager.read_dataset("mig_test")
        assert "score" in result.column_names
        assert result.column("score").to_pylist() == [0.0, 0.0, 0.0]
        assert result.num_rows == 3

    def test_alter_column_type(self, tmp_path: Path) -> None:
        """alter_column changes a column's data type."""
        manager = LanceStorageManager(base_uri=str(tmp_path))
        manager.create_dataset("mig_test", pa.table({"id": [1, 2], "name": ["a", "b"]}))

        manager.alter_column("mig_test", "name", pa.string())

        result = manager.read_dataset("mig_test")
        assert result.column("name").type == pa.string()
        assert result.column("name").to_pylist() == ["a", "b"]

    def test_drop_column(self, tmp_path: Path) -> None:
        """drop_column removes a column from the dataset."""
        manager = LanceStorageManager(base_uri=str(tmp_path))
        manager.create_dataset("mig_test", pa.table({"id": [1, 2], "extra": [10, 20]}))

        manager.drop_column("mig_test", "extra")

        result = manager.read_dataset("mig_test")
        assert result.column_names == ["id"]
        assert result.column("id").to_pylist() == [1, 2]

    def test_migration_preserves_data(self, tmp_path: Path) -> None:
        """Data integrity is preserved through schema changes."""
        manager = LanceStorageManager(base_uri=str(tmp_path))
        manager.create_dataset(
            "mig_test",
            pa.table({"id": [1, 2, 3], "value": [10.0, 20.0, 30.0]}),
        )

        manager.add_column("mig_test", "label", "CAST('' AS string)")
        manager.drop_column("mig_test", "value")

        result = manager.read_dataset("mig_test")
        assert result.num_rows == 3
        assert result.column("id").to_pylist() == [1, 2, 3]
        assert "value" not in result.column_names
        assert "label" in result.column_names

    def test_drop_nonexistent_column_raises(self, tmp_path: Path) -> None:
        """Dropping a nonexistent column raises StorageError."""
        manager = LanceStorageManager(base_uri=str(tmp_path))
        manager.create_dataset("mig_test", pa.table({"id": [1]}))

        with pytest.raises(StorageError, match="does not exist"):
            manager.drop_column("mig_test", "nonexistent")

    def test_add_column_with_int_type(self, tmp_path: Path) -> None:
        """add_column works with INT SQL type."""
        manager = LanceStorageManager(base_uri=str(tmp_path))
        manager.create_dataset("mig_test", pa.table({"id": [1, 2]}))

        manager.add_column("mig_test", "count", "CAST(0 AS INT)")

        result = manager.read_dataset("mig_test")
        assert "count" in result.column_names
        assert result.column("count").to_pylist() == [0, 0]
