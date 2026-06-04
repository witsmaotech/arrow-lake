"""Tests for quality/gate.py — IngestionQualityGate check pipeline."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pyarrow as pa
import pytest

from arrow_lake.quality.gate import GateResult, IngestionQualityGate, _dicts_to_table


def _table() -> pa.Table:
    return pa.table({"id": [1, 2, 3], "text": ["a", "b", "c"]})


def _mock_metrics():
    """Create mock metric objects for patching."""
    m1 = MagicMock()
    m1.labels.return_value.inc = MagicMock()
    m2 = MagicMock()
    m2.labels.return_value.inc = MagicMock()
    return m1, m2


class TestGateResult:
    def test_creation(self) -> None:
        r = GateResult(
            total=10, passed=8, rejected=2,
            schema_rejected=1, filter_rejected=1, score_rejected=0,
            pass_rate=0.8, rejection_reasons=("schema:1",), duration_seconds=0.01,
        )
        assert r.passed == 8
        assert r.pass_rate == 0.8

    def test_frozen(self) -> None:
        r = GateResult(total=10, passed=10, rejected=0,
                       schema_rejected=0, filter_rejected=0, score_rejected=0,
                       pass_rate=1.0, rejection_reasons=(), duration_seconds=0.0)
        with pytest.raises(AttributeError):
            r.total = 99  # type: ignore[misc]


class TestIngestionQualityGate:
    def test_check_no_checks_passes_all(self) -> None:
        """When no checks are enabled, all rows pass."""
        gate = IngestionQualityGate()
        m1, m2 = _mock_metrics()
        with patch("arrow_lake.core.metrics.quality_check_total", m1), \
             patch("arrow_lake.core.metrics.quality_reject_total", m2):
            passed, result = gate.check(_table(), dataset_name="ds")
        assert result.total == 3
        assert result.passed == 3
        assert result.rejected == 0
        assert result.pass_rate == 1.0

    def test_check_schema_rejection(self) -> None:
        """Schema validation rejects invalid rows."""
        schema = pa.schema([("id", pa.int64()), ("text", pa.string())])
        gate = IngestionQualityGate(schema_mode="strict", target_schema=schema)
        m1, m2 = _mock_metrics()
        with patch("arrow_lake.core.metrics.quality_check_total", m1), \
             patch("arrow_lake.core.metrics.quality_reject_total", m2), \
             patch.object(gate, "_validate_schema", return_value=(_table().slice(0, 2), 1)):
            passed, result = gate.check(_table(), dataset_name="ds")
        assert result.schema_rejected == 1
        assert "schema:1" in result.rejection_reasons

    def test_check_with_active_filters(self) -> None:
        """Content filtering is applied when active_filters is set."""
        gate = IngestionQualityGate(active_filters="dedup")
        m1, m2 = _mock_metrics()
        with patch("arrow_lake.core.metrics.quality_check_total", m1), \
             patch("arrow_lake.core.metrics.quality_reject_total", m2), \
             patch.object(gate, "_apply_filters", return_value=(_table().slice(0, 2), 1)):
            _, result = gate.check(_table(), dataset_name="ds")
        assert result.filter_rejected == 1

    def test_check_with_score_threshold(self) -> None:
        """Score threshold rejects low-quality rows."""
        gate = IngestionQualityGate(min_quality_score=0.5)
        m1, m2 = _mock_metrics()
        with patch("arrow_lake.core.metrics.quality_check_total", m1), \
             patch("arrow_lake.core.metrics.quality_reject_total", m2), \
             patch.object(gate, "_apply_score_threshold", return_value=(_table().slice(0, 2), 1)):
            _, result = gate.check(_table(), dataset_name="ds")
        assert result.score_rejected == 1

    def test_check_empty_table(self) -> None:
        gate = IngestionQualityGate()
        empty = pa.table({"id": pa.array([], type=pa.int64())})
        m1, m2 = _mock_metrics()
        with patch("arrow_lake.core.metrics.quality_check_total", m1), \
             patch("arrow_lake.core.metrics.quality_reject_total", m2):
            _, result = gate.check(empty, dataset_name="")
        assert result.total == 0
        assert result.pass_rate == 0.0

    def test_dead_letter_routing(self) -> None:
        """Rejected rows are routed to dead letter writer."""
        mock_writer = MagicMock()
        gate = IngestionQualityGate(dead_letter_writer=mock_writer)
        m1, m2 = _mock_metrics()
        passed = _table().slice(0, 1)  # only 1 row passes
        with patch("arrow_lake.core.metrics.quality_check_total", m1), \
             patch("arrow_lake.core.metrics.quality_reject_total", m2), \
             patch.object(gate, "_route_to_dead_letter") as mock_route:
            gate.check(_table(), dataset_name="ds")
            # Force rejected > 0 by mocking schema validation
        # Test the route method directly
        gate._route_to_dead_letter(_table(), passed, "ds")
        mock_writer.write.assert_called_once()

    def test_no_dead_letter_when_all_pass(self) -> None:
        mock_writer = MagicMock()
        gate = IngestionQualityGate(dead_letter_writer=mock_writer)
        m1, m2 = _mock_metrics()
        with patch("arrow_lake.core.metrics.quality_check_total", m1), \
             patch("arrow_lake.core.metrics.quality_reject_total", m2):
            gate.check(_table(), dataset_name="ds")
        mock_writer.write.assert_not_called()


class TestDictsToTable:
    def test_empty_rows(self) -> None:
        schema = pa.schema([("id", pa.int64())])
        result = _dicts_to_table([], schema)
        assert result.num_rows == 0
        assert "id" in result.column_names

    def test_with_data(self) -> None:
        schema = pa.schema([("id", pa.int64()), ("name", pa.string())])
        result = _dicts_to_table([{"id": 1, "name": "a"}], schema)
        assert result.num_rows == 1
