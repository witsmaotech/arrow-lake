"""Coverage for _lake_admin.py — lifecycle and version methods."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from arrow_lake.config import ArrowLakeConfig


def _make_lake():
    """Create a Lake instance with minimal config for testing."""
    config = ArrowLakeConfig()
    config.storage.backend = MagicMock()
    config.storage.base_uri = "/tmp/test-lake"
    config.gravitino.enabled = False
    with patch("arrow_lake.Lake.__init__", lambda self, **kw: None):
        from arrow_lake import Lake
        lake = Lake.__new__(Lake)
        lake._config = config
        lake._components = {}
        lake._start_time = 0
        lake._shutdown = False
    return lake


# ── version ──


class TestVersion:
    def test_returns_version(self) -> None:
        lake = _make_lake()
        with patch("arrow_lake._version.__version__", "1.5.2"):
            v = lake.version()
        assert v == "1.5.2"


# ── lifecycle methods ──


class TestLifecycle:
    def test_lifecycle_apply(self) -> None:
        lake = _make_lake()
        mock_mgr = MagicMock()
        mock_mgr.apply_lifecycle_rules.return_value = {"applied": 3}
        with patch.object(lake, "_get_lifecycle_manager", return_value=mock_mgr):
            result = lake.lifecycle_apply("prefix/")
        assert result["applied"] == 3

    def test_lifecycle_restore(self) -> None:
        lake = _make_lake()
        mock_mgr = MagicMock()
        mock_mgr.restore_object.return_value = {"status": "restored"}
        with patch.object(lake, "_get_lifecycle_manager", return_value=mock_mgr):
            result = lake.lifecycle_restore("key1", days=7)
        assert result["status"] == "restored"

    def test_lifecycle_estimate(self) -> None:
        lake = _make_lake()
        mock_mgr = MagicMock()
        mock_mgr.estimate_cost_savings.return_value = {"savings": 100}
        with patch.object(lake, "_get_lifecycle_manager", return_value=mock_mgr):
            result = lake.lifecycle_estimate(100, "STANDARD_IA")
        assert result["savings"] == 100

    def test_lifecycle_rules(self) -> None:
        lake = _make_lake()
        mock_mgr = MagicMock()
        mock_mgr._build_lifecycle_rules.return_value = [{"id": "r1"}]
        with patch.object(lake, "_get_lifecycle_manager", return_value=mock_mgr):
            result = lake.lifecycle_rules("pfx/")
        assert result["rules"] == [{"id": "r1"}]


# ── audit_analyze ──


class TestAuditAnalyze:
    """Tests for _LakeAuditMixin.audit_analyze() covering lines 109-129."""

    @pytest.fixture
    def lake(self) -> MagicMock:
        return _make_lake()

    def test_analyze_with_anomaly_records(self, lake: MagicMock) -> None:
        """AnomalyRecord instances are converted via asdict()."""
        from arrow_lake.workflow.audit_analyzer import AnomalyRecord

        anomaly = AnomalyRecord(
            anomaly_type="frequency_spike",
            severity="high",
            description="z=5.2 for write:docs",
            affected_events=42,
            detected_at="2026-06-04T12:00:00+00:00",
        )
        mock_trail = MagicMock()
        mock_trail.query.return_value = [{"event_type": "write"}]

        mock_analyzer = MagicMock()
        mock_analyzer.analyze.return_value = [anomaly]

        with (
            patch.object(lake, "_get_audit_trail", return_value=mock_trail),
            patch(
                "arrow_lake.workflow.audit_analyzer.AuditAnalyzer",
                return_value=mock_analyzer,
            ),
        ):
            results = lake.audit_analyze()

        assert len(results) == 1
        assert results[0]["anomaly_type"] == "frequency_spike"
        assert results[0]["severity"] == "high"
        assert results[0]["affected_events"] == 42

    def test_analyze_with_plain_dict_object(self, lake: MagicMock) -> None:
        """Objects with __dict__ are converted via __dict__."""
        mock_trail = MagicMock()
        mock_trail.query.return_value = []

        class SimpleResult:
            def __init__(self) -> None:
                self.key = "value"

        mock_analyzer = MagicMock()
        mock_analyzer.analyze.return_value = [SimpleResult()]

        with (
            patch.object(lake, "_get_audit_trail", return_value=mock_trail),
            patch(
                "arrow_lake.workflow.audit_analyzer.AuditAnalyzer",
                return_value=mock_analyzer,
            ),
        ):
            results = lake.audit_analyze()

        assert len(results) == 1
        assert results[0]["key"] == "value"

    def test_analyze_with_fallback_asdict(self, lake: MagicMock) -> None:
        """Non-AnomalyRecord dataclass instances use __dict__ path."""
        from dataclasses import dataclass

        @dataclass(frozen=True)
        class OtherRecord:
            score: float
            label: str

        mock_trail = MagicMock()
        mock_trail.query.return_value = []

        mock_analyzer = MagicMock()
        mock_analyzer.analyze.return_value = [
            OtherRecord(score=0.95, label="good"),
        ]

        with (
            patch.object(lake, "_get_audit_trail", return_value=mock_trail),
            patch(
                "arrow_lake.workflow.audit_analyzer.AuditAnalyzer",
                return_value=mock_analyzer,
            ),
        ):
            results = lake.audit_analyze()

        assert len(results) == 1
        assert results[0]["score"] == 0.95
        assert results[0]["label"] == "good"

    def test_analyze_with_empty_results(self, lake: MagicMock) -> None:
        """Empty analyzer results return empty list."""
        mock_trail = MagicMock()
        mock_trail.query.return_value = []

        mock_analyzer = MagicMock()
        mock_analyzer.analyze.return_value = []

        with (
            patch.object(lake, "_get_audit_trail", return_value=mock_trail),
            patch(
                "arrow_lake.workflow.audit_analyzer.AuditAnalyzer",
                return_value=mock_analyzer,
            ),
        ):
            results = lake.audit_analyze()

        assert results == []

    def test_analyze_mixed_result_types(self, lake: MagicMock) -> None:
        """Mixed result types: AnomalyRecord + plain object + dataclass."""
        from arrow_lake.workflow.audit_analyzer import AnomalyRecord
        from dataclasses import dataclass

        @dataclass(frozen=True)
        class ExtraInfo:
            tag: str

        anomaly = AnomalyRecord(
            anomaly_type="actor_anomaly",
            severity="medium",
            description="Actor 'bob' has 100 events",
            affected_events=100,
            detected_at="2026-06-04T12:00:00+00:00",
        )

        class PlainResult:
            pass

        pr = PlainResult()
        pr.field = "val"

        mock_trail = MagicMock()
        mock_trail.query.return_value = []

        mock_analyzer = MagicMock()
        mock_analyzer.analyze.return_value = [
            anomaly,
            pr,
            ExtraInfo(tag="note"),
        ]

        with (
            patch.object(lake, "_get_audit_trail", return_value=mock_trail),
            patch(
                "arrow_lake.workflow.audit_analyzer.AuditAnalyzer",
                return_value=mock_analyzer,
            ),
        ):
            results = lake.audit_analyze()

        assert len(results) == 3
        assert results[0]["anomaly_type"] == "actor_anomaly"
        assert results[1]["field"] == "val"
        assert results[2]["tag"] == "note"
