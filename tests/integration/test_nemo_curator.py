"""Integration tests for NeMo Curator quality filter — Story 8.5.

Tests CPU fallback filtering with real data and integration
with the QualityFilterRegistry pipeline.
"""

from __future__ import annotations

import pyarrow as pa
import pytest
from arrow_lake.quality.base import QualityFilterRegistry
from arrow_lake.quality.models import QualityReport
from arrow_lake.quality.nemo_curator import NeMoCuratorFilter


class TestCPUFallbackFilter:
    """Test CPU heuristic fallback when NeMo Curator is not available."""

    def test_cpu_fallback_filter(self) -> None:
        """Filter via CPU heuristic: text_max_chars=10, threshold=0.5."""
        pytest.importorskip("pyarrow", reason="pyarrow required")

        filt = NeMoCuratorFilter(
            use_gpu=False,
            threshold=0.5,
            text_max_chars=10,
        )

        # With HAS_NEMO=False or use_gpu=False, fallback is always used.
        table = pa.table(
            {
                "text_content": [
                    "hi",  # len=2  -> 0.5*2/10=0.10 -> reject
                    "medium text here",  # len=17 -> 0.5*1.0 =0.50 -> pass
                    "",  # len=0  -> 0.00          -> reject
                    "exactly 10",  # len=10 -> 0.5*1.0 =0.50 -> pass
                    "way too long text here",  # len=22 -> 0.5*1.0 =0.50 -> pass
                ],
                "id": [1, 2, 3, 4, 5],
            }
        )

        passed, rejected = filt.filter(table)

        assert passed.num_rows == 3
        assert rejected.num_rows == 2
        assert filt.using_fallback is True

        # Verify rejected rows have _rejection_reason
        assert "_rejection_reason" in rejected.column_names

        # Verify passed row IDs
        passed_ids = passed.column("id").to_pylist()
        assert set(passed_ids) == {2, 4, 5}

    def test_fallback_all_rejected_high_threshold(self) -> None:
        """All rows rejected when threshold exceeds all heuristic scores."""
        filt = NeMoCuratorFilter(
            use_gpu=False,
            threshold=0.99,
            text_max_chars=1000,
        )

        table = pa.table(
            {
                "text_content": ["short", "medium text here"],
                "id": [1, 2],
            }
        )

        passed, rejected = filt.filter(table)

        assert passed.num_rows == 0
        assert rejected.num_rows == 2

    def test_fallback_empty_table(self) -> None:
        """Filter on empty table returns two empty tables."""
        filt = NeMoCuratorFilter(use_gpu=False)
        table = pa.table(
            {"text_content": pa.array([], type=pa.string()), "id": pa.array([], type=pa.int64())}
        )

        passed, rejected = filt.filter(table)

        assert passed.num_rows == 0
        assert rejected.num_rows == 0


class TestIntegrationWithQualityPipeline:
    """Test NeMoCuratorFilter within QualityFilterRegistry pipeline."""

    def test_integration_with_quality_pipeline(self) -> None:
        """Register in QualityFilterRegistry, apply_all() returns QualityReport."""
        registry = QualityFilterRegistry()
        filt = NeMoCuratorFilter(
            use_gpu=False,
            threshold=0.5,
            text_max_chars=10,
        )
        registry.register(filt)

        table = pa.table(
            {
                "text_content": [
                    "hi",  # reject
                    "medium text here",  # pass (0.50 >= 0.50)
                    "",  # reject
                    "long enough text",  # pass (0.50 >= 0.50)
                ],
                "id": [1, 2, 3, 4],
            }
        )

        report = registry.apply_all(table, active_filters="nemo_curator")

        assert isinstance(report, QualityReport)
        assert report.total == 4
        assert report.passed == 2
        assert report.rejected == 2
        assert len(report.filter_results) == 1
        assert report.filter_results[0].filter_name == "nemo_curator"
        assert report.overall_pass_rate() == 50.0
