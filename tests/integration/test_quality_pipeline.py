"""Integration tests for quality pipeline — Epic 4 (Steps 3, 6)."""

from __future__ import annotations

from unittest.mock import MagicMock

import pyarrow as pa
from arrow_lake.quality.base import QualityFilterRegistry
from arrow_lake.quality.builtin import ImageResolutionFilter, TextLengthFilter
from arrow_lake.quality.dead_letter import DeadLetterWriter
from arrow_lake.quality.models import QualityReport


class TestQualityFilterRegistryIntegration:
    """Integration: Registry + Built-in Filters end-to-end."""

    def test_text_filter_on_real_table(self) -> None:
        """Apply TextLengthFilter to a realistic table."""
        registry = QualityFilterRegistry()
        registry.register(TextLengthFilter(min_chars=3, max_chars=100))

        table = pa.table(
            {
                "text_content": ["hi", "hello world", "a", "good morning everyone"],
                "id": [1, 2, 3, 4],
            }
        )
        report = registry.apply_all(table, active_filters="text_length")

        assert report.total == 4
        assert report.passed == 2  # "hello world", "good morning everyone"
        assert report.rejected == 2  # "hi", "a"
        assert len(report.filter_results) == 1
        assert report.filter_results[0].filter_name == "text_length"

    def test_multiple_filters_and_mode(self) -> None:
        """Apply both text and image filters in AND mode."""
        registry = QualityFilterRegistry()
        registry.register(TextLengthFilter(min_chars=2))
        registry.register(ImageResolutionFilter(min_width=64, min_height=64))

        table = pa.table(
            {
                "text_content": ["ok", "a"],
                "image_width": [100, 10],
                "image_height": [100, 200],
                "id": [1, 2],
            }
        )
        report = registry.apply_all(table, active_filters="text_length,image_resolution")

        # Row 0: text ok (2≥2), image ok (100≥64) → pass
        # Row 1: text reject (1<2), image w reject (10<64) → reject
        assert report.total == 2
        assert report.passed == 1
        assert report.rejected == 1

    def test_rejected_table_has_reason_column(self) -> None:
        """Verify rejected rows carry _rejection_reason metadata."""
        registry = QualityFilterRegistry()
        registry.register(TextLengthFilter(min_chars=10))

        table = pa.table(
            {
                "text_content": ["short", "this is long enough"],
                "id": [1, 2],
            }
        )
        report = registry.apply_all(table, active_filters="text_length")

        assert report.rejected == 1  # "short" (5 chars < 10) rejected
        assert report.duration_seconds >= 0


class TestDeadLetterWriterIntegration:
    """Integration: Dead-Letter Writer + Filter pipeline."""

    def _mock_storage(self) -> MagicMock:
        storage = MagicMock()
        storage.write.return_value = 3
        return storage

    def test_write_rejected_rows_from_filter(self) -> None:
        """End-to-end: filter → dead-letter write."""
        registry = QualityFilterRegistry()
        registry.register(TextLengthFilter(min_chars=10))

        table = pa.table(
            {
                "text_content": ["short", "this is long enough", "tiny", "also good text"],
                "id": [1, 2, 3, 4],
            }
        )
        registry.apply_all(table, active_filters="text_length")

        # Get rejected table by applying filter directly
        f = TextLengthFilter(min_chars=10)
        _, rejected = f.filter(table)

        # "short"=5 <10 reject, "tiny"=4 <10 reject → 2 rejected
        # "this is long enough"=21 pass, "also good text"=14 pass
        assert rejected.num_rows == 2

        # Write rejected to dead letter
        storage = MagicMock()
        storage.write.return_value = 2
        writer = DeadLetterWriter(storage)
        written = writer.write("test_dataset", rejected, "text_length")

        assert written == 2
        storage.write.assert_called_once()
        call_table = storage.write.call_args[0][1]
        assert call_table.num_rows == 2
        assert "_rejection_reason" in call_table.column_names
        assert "_filter_name" in call_table.column_names
        assert "_rejected_at" in call_table.column_names

    def test_dead_letter_empty_rejected_no_write(self) -> None:
        """When all rows pass, no dead-letter write occurs."""
        registry = QualityFilterRegistry()
        registry.register(TextLengthFilter(min_chars=1))

        table = pa.table(
            {
                "text_content": ["hello", "world"],
                "id": [1, 2],
            }
        )
        registry.apply_all(table, active_filters="text_length")

        f = TextLengthFilter(min_chars=1)
        _, rejected = f.filter(table)

        storage = self._mock_storage()
        writer = DeadLetterWriter(storage)
        written = writer.write("test_dataset", rejected, "text_length")

        assert written == 0
        storage.write.assert_not_called()

    def test_full_pipeline_quality_report(self) -> None:
        """Full pipeline: table → filter → report with dead-letter stats."""
        registry = QualityFilterRegistry()
        registry.register(TextLengthFilter(min_chars=3))

        table = pa.table(
            {
                "text_content": ["hi", "hello", "ab", "world"],
                "id": [1, 2, 3, 4],
            }
        )
        report = registry.apply_all(table, active_filters="text_length")

        assert isinstance(report, QualityReport)
        assert report.total == 4
        assert report.passed == 2
        assert report.rejected == 2
        assert report.duration_seconds >= 0
        assert len(report.filter_results) == 1
