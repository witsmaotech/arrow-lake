"""Tests for Story 4.13 — Quality Score Column."""

from __future__ import annotations

import pyarrow as pa
import pytest
from arrow_lake.quality.models import FilterResult, QualityReport
from arrow_lake.quality.scoring import compute_quality_scores


class TestComputeQualityScores:
    """Test compute_quality_scores function."""

    def test_all_passed_gets_score_1(self) -> None:
        report = QualityReport(total=10, passed=10, rejected=0)
        table = pa.table({"id": range(10)})
        result = compute_quality_scores(table, report)
        assert "quality_score" in result.column_names
        scores = result.column("quality_score").to_pylist()
        assert all(s == 1.0 for s in scores)

    def test_rejected_rows_get_lower_score(self) -> None:
        report = QualityReport(
            total=10,
            passed=8,
            rejected=2,
            filter_results=(
                FilterResult(filter_name="text_length", passed_count=8, rejected_count=2),
            ),
        )
        table = pa.table({"id": range(10)})
        # rejected_table contains rows 3 and 7
        rejected_table = pa.table({"id": [3, 7]})
        result = compute_quality_scores(table, report, rejected_table=rejected_table)
        scores = result.column("quality_score").to_pylist()
        # Only rows with id 3 and 7 should have score < 1.0
        assert scores[3] < 1.0
        assert scores[7] < 1.0
        assert scores[0] == 1.0
        assert scores[1] == 1.0

    def test_empty_table(self) -> None:
        report = QualityReport(total=0, passed=0, rejected=0)
        table = pa.table({"id": []})
        result = compute_quality_scores(table, report)
        assert "quality_score" in result.column_names
        assert result.num_rows == 0

    def test_score_column_float32(self) -> None:
        report = QualityReport(total=5, passed=5, rejected=0)
        table = pa.table({"id": range(5)})
        result = compute_quality_scores(table, report)
        assert result.column("quality_score").type == pa.float32()

    def test_custom_score_column_name(self) -> None:
        report = QualityReport(total=3, passed=3, rejected=0)
        table = pa.table({"id": range(3)})
        result = compute_quality_scores(table, report, score_column="my_score")
        assert "my_score" in result.column_names

    def test_multiple_filters_heavier_penalty(self) -> None:
        """More filters = heavier penalty per rejected row."""
        report_1 = QualityReport(
            total=10,
            passed=8,
            rejected=2,
            filter_results=(
                FilterResult(filter_name="text_length", passed_count=8, rejected_count=2),
            ),
        )
        report_2 = QualityReport(
            total=10,
            passed=8,
            rejected=2,
            filter_results=(
                FilterResult(filter_name="text_length", passed_count=8, rejected_count=2),
                FilterResult(filter_name="image_res", passed_count=9, rejected_count=1),
            ),
        )
        table = pa.table({"id": range(10)})
        rejected_table = pa.table({"id": [0, 1]})
        r1 = compute_quality_scores(table, report_1, rejected_table=rejected_table)
        r2 = compute_quality_scores(table, report_2, rejected_table=rejected_table)
        # More filters → heavier penalty → lower score for rejected rows
        assert r1.column("quality_score").to_pylist()[0] > r2.column("quality_score").to_pylist()[0]

    def test_score_minimum_zero(self) -> None:
        """Scores should never be negative."""
        report = QualityReport(
            total=10,
            passed=0,
            rejected=10,
            filter_results=(
                FilterResult(filter_name="f1", passed_count=0, rejected_count=10),
                FilterResult(filter_name="f2", passed_count=0, rejected_count=10),
                FilterResult(filter_name="f3", passed_count=0, rejected_count=10),
                FilterResult(filter_name="f4", passed_count=0, rejected_count=10),
                FilterResult(filter_name="f5", passed_count=0, rejected_count=10),
                FilterResult(filter_name="f6", passed_count=0, rejected_count=10),
            ),
        )
        table = pa.table({"id": range(10)})
        rejected_table = pa.table({"id": range(10)})
        result = compute_quality_scores(table, report, rejected_table=rejected_table)
        scores = result.column("quality_score").to_pylist()
        assert all(s >= 0.0 for s in scores)

    def test_total_mismatch_raises(self) -> None:
        """report.total != table.num_rows should raise ValueError."""
        report = QualityReport(total=100, passed=90, rejected=10)
        table = pa.table({"id": range(10)})
        with pytest.raises(ValueError, match="does not match"):
            compute_quality_scores(table, report)

    def test_no_rejected_table_fallback(self) -> None:
        """Without rejected_table, first N rows assumed rejected."""
        report = QualityReport(
            total=5,
            passed=3,
            rejected=2,
            filter_results=(FilterResult(filter_name="f1", passed_count=3, rejected_count=2),),
        )
        table = pa.table({"id": range(5)})
        result = compute_quality_scores(table, report)
        scores = result.column("quality_score").to_pylist()
        assert scores[0] < 1.0
        assert scores[1] < 1.0
        assert scores[2] == 1.0
        assert scores[3] == 1.0
        assert scores[4] == 1.0
