"""Storage contract regression tests — v1.5.0.

Validates kernel layer storage interface contracts that must not break:
- Dataset name validation (SAFE_IDENTIFIER_RE)
- LRU lock limit (max 1024)
- Schema compatibility across create/append
"""

from __future__ import annotations

from pathlib import Path

import pyarrow as pa
import pytest
from arrow_lake.ingest.storage import LanceStorageManager


class TestDatasetNameValidation:
    """Regression: FQN injection prevention (v1.4.2+)."""

    @pytest.mark.parametrize(
        "bad_name",
        [
            "../../../etc/passwd",
            "my; DROP TABLE x",
            "dataset with spaces",
            "dataset/name",
            "dataset\\name",
            "",
            "   ",
        ],
    )
    def test_reject_invalid_dataset_names(self, tmp_path: Path, bad_name: str) -> None:
        manager = LanceStorageManager(base_uri=str(tmp_path))
        table = pa.table({"id": [1], "value": [1.0]})
        with pytest.raises(Exception):
            manager.create_dataset(bad_name, table)

    @pytest.mark.parametrize(
        "valid_name",
        [
            "test_table",
            "my-dataset-123",
            "camelCaseTable",
            "snake_case_tbl",
            "a",
            "UPPER_CASE",
        ],
    )
    def test_accept_valid_dataset_names(self, tmp_path: Path, valid_name: str) -> None:
        manager = LanceStorageManager(base_uri=str(tmp_path))
        table = pa.table({"id": [1], "value": [1.0]})
        manager.create_dataset(valid_name, table)
        result = manager.read_dataset(valid_name)
        assert result.num_rows == 1


class TestLRULockLimit:
    """Regression: LRU lock upper bound (v1.4.4, max 1024)."""

    def test_lock_limit_enforced(self, tmp_path: Path) -> None:
        manager = LanceStorageManager(base_uri=str(tmp_path))
        table = pa.table({"id": [0], "value": [0.0]})

        # Create datasets up to limit
        for i in range(1025):
            manager.create_dataset(f"ds_{i:04d}", table)

        # All locks should be stored without unbounded growth
        assert len(manager._dataset_locks) <= 1025
        # The LRU eviction should keep size bounded
        assert len(manager._dataset_locks) <= manager._dataset_lock_max + 1


class TestSchemaContract:
    """Kernel layer schema compatibility contracts."""

    def test_create_and_read_schema_match(self, tmp_path: Path) -> None:
        manager = LanceStorageManager(base_uri=str(tmp_path))
        table = pa.table(
            {
                "id": pa.array([1, 2], type=pa.int64()),
                "name": pa.array(["a", "b"], type=pa.string()),
                "score": pa.array([1.5, 2.5], type=pa.float64()),
            }
        )
        manager.create_dataset("schema_test", table)
        result = manager.read_dataset("schema_test")

        assert result.schema.field("id").type == pa.int64()
        assert result.schema.field("name").type == pa.string()
        assert result.schema.field("score").type == pa.float64()

    def test_append_schema_mismatch_raises(self, tmp_path: Path) -> None:
        manager = LanceStorageManager(base_uri=str(tmp_path))
        table1 = pa.table({"id": [1], "value": [1.0]})
        manager.create_dataset("mismatch_test", table1)

        table2 = pa.table({"id": [2], "extra_col": ["x"]})
        with pytest.raises(Exception):
            manager.append_dataset("mismatch_test", table2)

    def test_dataset_not_found_raises(self, tmp_path: Path) -> None:
        manager = LanceStorageManager(base_uri=str(tmp_path))
        with pytest.raises(Exception):
            manager.read_dataset("nonexistent_dataset")
