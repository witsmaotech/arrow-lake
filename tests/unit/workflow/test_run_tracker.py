"""Tests for workflow/run_tracker.py — dataclasses and RunTracker with mocked Metaflow."""

from __future__ import annotations

import sys
import types
from unittest.mock import MagicMock, patch

import pytest

from arrow_lake.workflow.run_tracker import RunComparison, RunSummary, RunTracker


# ===========================================================================
# Dataclasses
# ===========================================================================


class TestRunSummary:
    def test_creation(self) -> None:
        s = RunSummary(run_id="r1", status="success", created_at="2024-01-01", tags=("t1",))
        assert s.run_id == "r1"
        assert s.status == "success"
        assert s.tags == ("t1",)

    def test_frozen(self) -> None:
        s = RunSummary(run_id="r1", status="success", created_at="2024-01-01", tags=())
        with pytest.raises(AttributeError):
            s.run_id = "r2"  # type: ignore[misc]

    def test_empty_tags(self) -> None:
        s = RunSummary(run_id="r1", status="failed", created_at="", tags=())
        assert s.tags == ()


class TestRunComparison:
    def test_creation(self) -> None:
        c = RunComparison(
            run_a_id="a", run_b_id="b",
            metrics_a={"rows": 10}, metrics_b={"rows": 15},
            diff={"rows": 5.0},
        )
        assert c.diff["rows"] == 5.0
        assert c.run_a_id == "a"

    def test_frozen(self) -> None:
        c = RunComparison(run_a_id="a", run_b_id="b",
                          metrics_a={}, metrics_b={}, diff={})
        with pytest.raises(AttributeError):
            c.run_a_id = "x"  # type: ignore[misc]

    def test_diff_can_be_negative(self) -> None:
        c = RunComparison(run_a_id="a", run_b_id="b",
                          metrics_a={"x": 10}, metrics_b={"x": 3},
                          diff={"x": -7.0})
        assert c.diff["x"] == -7.0


# ===========================================================================
# RunTracker — compare_runs (mock Metaflow)
# ===========================================================================


class TestCompareRuns:
    def test_compare_with_mocked_metaflow(self) -> None:
        mock_run_a = MagicMock()
        mock_run_a.data.total_rows = 100
        mock_run_a.data.total_success = 90
        mock_run_a.data.total_failure = 10

        mock_run_b = MagicMock()
        mock_run_b.data.total_rows = 120
        mock_run_b.data.total_success = 110
        mock_run_b.data.total_failure = 10

        mock_run_cls = MagicMock(side_effect=[mock_run_a, mock_run_b])

        # Inject fake metaflow module
        fake_mf = types.ModuleType("metaflow")
        fake_mf.Run = mock_run_cls  # type: ignore[attr-defined]
        with patch.dict(sys.modules, {"metaflow": fake_mf}):
            result = RunTracker.compare_runs("Flow", "1", "2")

        assert result.run_a_id == "1"
        assert result.run_b_id == "2"
        assert result.metrics_a["total_rows"] == 100
        assert result.metrics_b["total_rows"] == 120
        assert result.diff["total_rows"] == 20.0
        assert result.diff["success_count"] == 20.0
        assert result.diff["failure_count"] == 0.0


# ===========================================================================
# RunTracker — latest_run (mock Metaflow)
# ===========================================================================


class TestLatestRun:
    def test_returns_first_successful_run(self) -> None:
        """Covers lines 51-60: latest_run returns RunSummary for first successful run."""
        mock_run_1 = MagicMock()
        mock_run_1.successful = False

        mock_run_2 = MagicMock()
        mock_run_2.successful = True
        mock_run_2.id = "run-42"
        mock_run_2.created_at = "2024-06-01T10:00:00"
        mock_run_2.tags = ["prod", "v2"]

        mock_flow_instance = MagicMock()
        mock_flow_instance.__iter__ = MagicMock(return_value=iter([mock_run_1, mock_run_2]))

        fake_mf = types.ModuleType("metaflow")
        fake_mf.Flow = MagicMock(return_value=mock_flow_instance)
        with patch.dict(sys.modules, {"metaflow": fake_mf}):
            result = RunTracker.latest_run("IngestFlow")

        assert result is not None
        assert result.run_id == "run-42"
        assert result.status == "success"
        assert result.created_at == "2024-06-01T10:00:00"
        assert result.tags == ("prod", "v2")

    def test_returns_none_when_no_successful_runs(self) -> None:
        """Covers lines 61: latest_run returns None when all runs failed."""
        mock_run_1 = MagicMock()
        mock_run_1.successful = False

        mock_run_2 = MagicMock()
        mock_run_2.successful = False

        mock_flow_instance = MagicMock()
        mock_flow_instance.__iter__ = MagicMock(return_value=iter([mock_run_1, mock_run_2]))

        fake_mf = types.ModuleType("metaflow")
        fake_mf.Flow = MagicMock(return_value=mock_flow_instance)
        with patch.dict(sys.modules, {"metaflow": fake_mf}):
            result = RunTracker.latest_run("IngestFlow")

        assert result is None

    def test_returns_none_when_no_runs(self) -> None:
        """Covers line 61: latest_run returns None for an empty flow."""
        mock_flow_instance = MagicMock()
        mock_flow_instance.__iter__ = MagicMock(return_value=iter([]))

        fake_mf = types.ModuleType("metaflow")
        fake_mf.Flow = MagicMock(return_value=mock_flow_instance)
        with patch.dict(sys.modules, {"metaflow": fake_mf}):
            result = RunTracker.latest_run("IngestFlow")

        assert result is None

    def test_returns_first_successful_immediately(self) -> None:
        """Covers lines 53-60: first run is successful, returned immediately."""
        mock_run = MagicMock()
        mock_run.successful = True
        mock_run.id = "run-first"
        mock_run.created_at = "2024-01-15"
        mock_run.tags = []

        mock_flow_instance = MagicMock()
        mock_flow_instance.__iter__ = MagicMock(return_value=iter([mock_run]))

        fake_mf = types.ModuleType("metaflow")
        fake_mf.Flow = MagicMock(return_value=mock_flow_instance)
        with patch.dict(sys.modules, {"metaflow": fake_mf}):
            result = RunTracker.latest_run("IngestFlow")

        assert result is not None
        assert result.run_id == "run-first"
        assert result.tags == ()


# ===========================================================================
# RunTracker — run_history (mock Metaflow)
# ===========================================================================


class TestRunHistory:
    def test_returns_multiple_runs(self) -> None:
        """Covers lines 74-87: run_history returns RunSummary list."""
        mock_run_1 = MagicMock()
        mock_run_1.successful = True
        mock_run_1.id = "run-1"
        mock_run_1.created_at = "2024-06-01"
        mock_run_1.tags = ["tag1"]

        mock_run_2 = MagicMock()
        mock_run_2.successful = False
        mock_run_2.id = "run-2"
        mock_run_2.created_at = "2024-06-02"
        mock_run_2.tags = []

        mock_run_3 = MagicMock()
        mock_run_3.successful = True
        mock_run_3.id = "run-3"
        mock_run_3.created_at = "2024-06-03"
        mock_run_3.tags = ["prod"]

        mock_flow_instance = MagicMock()
        mock_flow_instance.__iter__ = MagicMock(
            return_value=iter([mock_run_1, mock_run_2, mock_run_3])
        )

        fake_mf = types.ModuleType("metaflow")
        fake_mf.Flow = MagicMock(return_value=mock_flow_instance)
        with patch.dict(sys.modules, {"metaflow": fake_mf}):
            result = RunTracker.run_history("IngestFlow", limit=10)

        assert len(result) == 3
        assert result[0].run_id == "run-1"
        assert result[0].status == "success"
        assert result[1].run_id == "run-2"
        assert result[1].status == "failed"
        assert result[2].run_id == "run-3"
        assert result[2].status == "success"

    def test_respects_limit(self) -> None:
        """Covers lines 78-79: run_history stops after reaching limit."""
        runs = []
        for i in range(20):
            r = MagicMock()
            r.successful = True
            r.id = f"run-{i}"
            r.created_at = f"2024-06-{i+1:02d}"
            r.tags = []
            runs.append(r)

        mock_flow_instance = MagicMock()
        mock_flow_instance.__iter__ = MagicMock(return_value=iter(runs))

        fake_mf = types.ModuleType("metaflow")
        fake_mf.Flow = MagicMock(return_value=mock_flow_instance)
        with patch.dict(sys.modules, {"metaflow": fake_mf}):
            result = RunTracker.run_history("IngestFlow", limit=5)

        assert len(result) == 5
        for i, s in enumerate(result):
            assert s.run_id == f"run-{i}"

    def test_empty_flow_returns_empty_list(self) -> None:
        """Covers lines 74-88: run_history returns empty list for flow with no runs."""
        mock_flow_instance = MagicMock()
        mock_flow_instance.__iter__ = MagicMock(return_value=iter([]))

        fake_mf = types.ModuleType("metaflow")
        fake_mf.Flow = MagicMock(return_value=mock_flow_instance)
        with patch.dict(sys.modules, {"metaflow": fake_mf}):
            result = RunTracker.run_history("IngestFlow")

        assert result == []

    def test_single_run(self) -> None:
        """Covers lines 76-87: run_history with a single run."""
        mock_run = MagicMock()
        mock_run.successful = False
        mock_run.id = "only-run"
        mock_run.created_at = "2024-01-01"
        mock_run.tags = ["dev"]

        mock_flow_instance = MagicMock()
        mock_flow_instance.__iter__ = MagicMock(return_value=iter([mock_run]))

        fake_mf = types.ModuleType("metaflow")
        fake_mf.Flow = MagicMock(return_value=mock_flow_instance)
        with patch.dict(sys.modules, {"metaflow": fake_mf}):
            result = RunTracker.run_history("IngestFlow")

        assert len(result) == 1
        assert result[0].run_id == "only-run"
        assert result[0].status == "failed"
        assert result[0].tags == ("dev",)
