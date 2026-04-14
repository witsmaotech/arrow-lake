"""Tests for Story 4.11 — Quality Statistics Report."""

from __future__ import annotations

import json

import pytest
from arrow_lake.quality.models import FilterResult, QualityReport


class TestFilterResultStats:
    """Test FilterResult statistics methods."""

    def test_pass_rate_all_pass(self) -> None:
        r = FilterResult(filter_name="test", passed_count=100, rejected_count=0)
        assert r.pass_rate() == 100.0

    def test_pass_rate_all_reject(self) -> None:
        r = FilterResult(filter_name="test", passed_count=0, rejected_count=100)
        assert r.pass_rate() == 0.0

    def test_pass_rate_mixed(self) -> None:
        r = FilterResult(filter_name="test", passed_count=970, rejected_count=30)
        assert r.pass_rate() == pytest.approx(97.0, rel=0.01)

    def test_pass_rate_zero_total(self) -> None:
        r = FilterResult(filter_name="test", passed_count=0, rejected_count=0)
        assert r.pass_rate() == 100.0

    def test_to_dict(self) -> None:
        r = FilterResult(filter_name="text_length", passed_count=95, rejected_count=5)
        d = r.to_dict()
        assert d["filter_name"] == "text_length"
        assert d["passed_count"] == 95
        assert d["rejected_count"] == 5
        assert "pass_rate_percentage" in d
        assert isinstance(d["pass_rate_percentage"], float)

    def test_to_dict_json_serializable(self) -> None:
        r = FilterResult(filter_name="test", passed_count=10, rejected_count=2)
        json_str = json.dumps(r.to_dict())
        assert json_str is not None


class TestQualityReportStats:
    """Test QualityReport statistics methods."""

    def test_overall_pass_rate(self) -> None:
        report = QualityReport(total=10000, passed=9500, rejected=500)
        assert report.overall_pass_rate() == pytest.approx(95.0, rel=0.01)

    def test_overall_pass_rate_zero_total(self) -> None:
        report = QualityReport()
        assert report.overall_pass_rate() == 100.0

    def test_to_json_basic(self) -> None:
        report = QualityReport(total=10000, passed=9500, rejected=500)
        d = report.to_json()
        assert d["total_rows"] == 10000
        assert d["passed_rows"] == 9500
        assert d["rejected_rows"] == 500
        assert "overall_pass_rate_percentage" in d
        assert "duration_seconds" in d
        assert "per_filter" in d

    def test_to_json_with_filters(self) -> None:
        fr1 = FilterResult(filter_name="text_length", passed_count=9800, rejected_count=200)
        fr2 = FilterResult(filter_name="image_resolution", passed_count=9700, rejected_count=300)
        report = QualityReport(
            total=10000,
            passed=9500,
            rejected=500,
            filter_results=(fr1, fr2),
        )
        d = report.to_json()
        assert len(d["per_filter"]) == 2
        assert d["per_filter"][0]["filter_name"] == "text_length"
        assert d["per_filter"][0]["pass_rate_percentage"] == pytest.approx(98.0, rel=0.01)
        assert d["per_filter"][1]["filter_name"] == "image_resolution"
        assert d["per_filter"][1]["pass_rate_percentage"] == pytest.approx(97.0, rel=0.01)

    def test_to_json_schema_rejected(self) -> None:
        report = QualityReport(
            total=100,
            passed=90,
            rejected=5,
            schema_rejected=5,
        )
        d = report.to_json()
        assert d["schema_rejected_rows"] == 5

    def test_to_json_duration(self) -> None:
        report = QualityReport(total=100, passed=90, rejected=10, duration_seconds=1.234567)
        d = report.to_json()
        assert d["duration_seconds"] == 1.2346  # rounded to 4 decimal places

    def test_to_json_serializable(self) -> None:
        fr = FilterResult(filter_name="test", passed_count=10, rejected_count=2)
        report = QualityReport(
            total=100,
            passed=90,
            rejected=10,
            filter_results=(fr,),
            duration_seconds=0.5,
        )
        json_str = json.dumps(report.to_json())
        parsed = json.loads(json_str)
        assert parsed["total_rows"] == 100

    def test_per_filter_breakdown(self) -> None:
        fr1 = FilterResult(filter_name="a", passed_count=8, rejected_count=2)
        fr2 = FilterResult(filter_name="b", passed_count=5, rejected_count=5)
        report = QualityReport(filter_results=(fr1, fr2))
        breakdown = report.per_filter_breakdown()
        assert len(breakdown) == 2
        assert breakdown[0]["filter_name"] == "a"
        assert breakdown[1]["filter_name"] == "b"

    def test_metaflow_cards_compatibility(self) -> None:
        """Verify to_json output is compatible with Metaflow Cards."""
        fr = FilterResult(filter_name="image_res", passed_count=9000, rejected_count=1000)
        report = QualityReport(
            total=10000,
            passed=9000,
            rejected=1000,
            schema_rejected=0,
            duration_seconds=3.5,
            filter_results=(fr,),
        )
        data = report.to_json()
        # Metaflow Cards expects flat dict with basic types
        assert all(isinstance(v, (int, float, str, list, dict)) for v in data.values())
