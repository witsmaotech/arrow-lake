"""Tests for arrow_lake.quality.dead_letter — Story 4.10 Dead-Letter Persistence."""

from __future__ import annotations

from unittest.mock import MagicMock

import pyarrow as pa
import pytest
from arrow_lake.exceptions import QualityError
from arrow_lake.quality.dead_letter import DeadLetterWriter
from arrow_lake.quality.models import DEAD_LETTER_EXTRA_SCHEMA


class TestDeadLetterWriter:
    """Test DeadLetterWriter."""

    def _mock_storage(self) -> MagicMock:
        storage = MagicMock()
        storage.write.return_value = 5
        return storage

    def test_write_creates_correct_table_name(self) -> None:
        storage = self._mock_storage()
        writer = DeadLetterWriter(storage)
        table = pa.table({"text": ["hello"]})
        writer.write("my_dataset", table, "text_length")
        storage.write.assert_called_once()
        call_args = storage.write.call_args
        assert call_args[0][0] == "my_dataset_dead_letter"

    def test_write_adds_extra_columns(self) -> None:
        storage = self._mock_storage()
        writer = DeadLetterWriter(storage)
        table = pa.table({"text": ["hello"]})
        writer.write("my_dataset", table, "text_length")
        call_args = storage.write.call_args
        written_table = call_args[0][1]
        assert "_rejection_reason" in written_table.column_names
        assert "_filter_name" in written_table.column_names
        assert "_parent_version" in written_table.column_names
        assert "_rejected_at" in written_table.column_names

    def test_write_sets_filter_name(self) -> None:
        storage = self._mock_storage()
        writer = DeadLetterWriter(storage)
        table = pa.table({"text": ["bad"]})
        writer.write("ds", table, "image_resolution")
        call_args = storage.write.call_args
        written_table = call_args[0][1]
        filter_names = written_table.column("_filter_name").to_pylist()
        assert all(fn == "image_resolution" for fn in filter_names)

    def test_write_empty_table_short_circuits(self) -> None:
        storage = self._mock_storage()
        writer = DeadLetterWriter(storage)
        table = pa.table({"text": []})
        result = writer.write("ds", table, "text_length")
        assert result == 0
        storage.write.assert_not_called()

    def test_write_returns_row_count(self) -> None:
        storage = self._mock_storage()
        storage.write.return_value = 3
        writer = DeadLetterWriter(storage)
        table = pa.table({"text": ["a", "b", "c"]})
        result = writer.write("ds", table, "text_length")
        assert result == 3

    def test_write_with_parent_version(self) -> None:
        storage = self._mock_storage()
        writer = DeadLetterWriter(storage)
        table = pa.table({"text": ["a"]})
        writer.write("ds", table, "text_length", parent_version="v2")
        call_args = storage.write.call_args
        written_table = call_args[0][1]
        versions = written_table.column("_parent_version").to_pylist()
        assert versions == ["v2"]

    def test_write_storage_error_raises_quality_error(self) -> None:
        storage = self._mock_storage()
        storage.write.side_effect = OSError("disk full")
        writer = DeadLetterWriter(storage)
        table = pa.table({"text": ["a"]})
        with pytest.raises(QualityError, match="QUALITY_DEAD_LETTER_WRITE_FAILED"):
            writer.write("ds", table, "text_length")

    def test_write_extra_schema_matches_model(self) -> None:
        """Verify DEAD_LETTER_EXTRA_SCHEMA has the expected fields."""
        expected_fields = {"_rejection_reason", "_filter_name", "_parent_version", "_rejected_at"}
        actual_fields = {f.name for f in DEAD_LETTER_EXTRA_SCHEMA}
        assert expected_fields <= actual_fields
