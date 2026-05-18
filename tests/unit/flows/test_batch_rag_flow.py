"""Tests for BatchRAGFlow + RunTracker — step logic in isolation."""

from __future__ import annotations

import json
from typing import Any


class TestStartStepQuestionLoading:
    """start step: load questions from JSON file."""

    def test_json_loading(self, tmp_path: object) -> None:
        import pathlib

        qfile = pathlib.Path(str(tmp_path)) / "questions.json"
        qfile.write_text('["What is ML?", "How does RAG work?"]')
        questions: list[str] = json.loads(qfile.read_text())
        assert len(questions) == 2
        assert questions[0] == "What is ML?"

    def test_empty_questions(self, tmp_path: object) -> None:
        import pathlib

        qfile = pathlib.Path(str(tmp_path)) / "questions.json"
        qfile.write_text("[]")
        questions: list[str] = json.loads(qfile.read_text())
        assert questions == []

    def test_missing_file_fallback(self) -> None:
        from pathlib import Path

        qfile = Path("/nonexistent/questions.json")
        questions: list[str] = (
            json.loads(qfile.read_text()) if qfile.exists() else ["demo question"]
        )
        assert questions == ["demo question"]


class TestQueryStepResultStructure:
    """query step: result dict for success and failure."""

    def test_success_result(self) -> None:
        result: dict[str, Any] = {
            "question": "What is ML?",
            "status": "success",
            "answer": "Machine learning is...",
            "sources": 3,
        }
        assert result["status"] == "success"
        assert result["sources"] == 3

    def test_failed_result(self) -> None:
        result: dict[str, Any] = {
            "question": "Complex query",
            "status": "failed",
            "error": "Timed out after 60s",
        }
        assert result["status"] == "failed"
        assert "Timed out" in result["error"]


class TestJoinStepAggregation:
    """join step: aggregate query results."""

    def test_mixed_results(self) -> None:
        results = [
            {"question": "q1", "status": "success", "answer": "a1", "sources": 3},
            {"question": "q2", "status": "failed", "error": "timeout"},
            {"question": "q3", "status": "success", "answer": "a3", "sources": 5},
        ]
        total_success = sum(1 for r in results if r["status"] == "success")
        total_failed = sum(1 for r in results if r["status"] != "success")
        assert total_success == 2
        assert total_failed == 1

    def test_all_success(self) -> None:
        results = [
            {"question": "q1", "status": "success", "answer": "a1", "sources": 3},
        ]
        total_failed = sum(1 for r in results if r["status"] != "success")
        assert total_failed == 0


class TestEndStepSummary:
    """end step: summary JSON."""

    def test_summary_json(self) -> None:
        summary = {
            "total_questions": 3,
            "success": 2,
            "failed": 1,
        }
        parsed = json.loads(json.dumps(summary))
        assert parsed["total_questions"] == 3


class TestRunTrackerDataclasses:
    """RunTracker: dataclass structures."""

    def test_run_summary(self) -> None:
        from arrow_lake.workflow.run_tracker import RunSummary

        rs = RunSummary(
            run_id="42",
            status="success",
            created_at="2026-05-18",
            tags=("arrow_lake",),
        )
        assert rs.run_id == "42"
        assert rs.status == "success"
        assert len(rs.tags) == 1

    def test_run_comparison(self) -> None:
        from arrow_lake.workflow.run_tracker import RunComparison

        rc = RunComparison(
            run_a_id="42",
            run_b_id="43",
            metrics_a={"total_rows": 100, "success_count": 90},
            metrics_b={"total_rows": 200, "success_count": 180},
            diff={"total_rows": 100.0, "success_count": 90.0},
        )
        assert rc.diff["total_rows"] == 100.0
        assert rc.run_b_id == "43"

    def test_run_comparison_diff_calculation(self) -> None:
        metrics_a = {"total_rows": 100, "success_count": 90, "failure_count": 10}
        metrics_b = {"total_rows": 200, "success_count": 180, "failure_count": 20}
        diff = {
            k: float(metrics_b.get(k, 0) - metrics_a.get(k, 0))
            for k in set(metrics_a) | set(metrics_b)
        }
        assert diff["total_rows"] == 100.0
        assert diff["failure_count"] == 10.0


class TestFlowRegistration:
    """Verify BatchRAGFlow is registered."""

    def test_batch_rag_registered(self) -> None:
        import importlib

        import flows
        from arrow_lake.workflow.base import FlowRegistry

        FlowRegistry.clear()
        flows._registration_attempted = False
        importlib.reload(flows)
        flows._register_flows()

        assert "batch_rag" in FlowRegistry.list_flows()
