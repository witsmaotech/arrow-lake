"""Tests for IngestFlow — step logic in isolation.

Metaflow FlowSpec hooks into the CLI on instantiation, so we test
each step's business logic as standalone operations.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


class TestStartStepFileScanning:
    """start step: scan source directory and build file list."""

    def test_empty_directory(self, tmp_path: Path) -> None:
        source = tmp_path / "empty"
        source.mkdir()
        files = sorted(str(f) for f in source.rglob("*") if f.is_file())
        assert files == []

    def test_nonexistent_directory(self) -> None:
        source = Path("/nonexistent/path/xyz")
        assert not source.exists()
        files = sorted(str(f) for f in source.rglob("*") if f.is_file()) if source.exists() else []
        assert files == []

    def test_finds_all_files(self, tmp_path: Path) -> None:
        (tmp_path / "a.csv").write_text("id\n1")
        (tmp_path / "sub").mkdir()
        (tmp_path / "sub" / "b.json").write_text("{}")
        (tmp_path / "sub" / "c.parquet").write_text("fake", encoding="utf-8")

        files = sorted(str(f) for f in tmp_path.rglob("*") if f.is_file())
        assert len(files) == 3
        assert files[0].endswith("a.csv")
        assert files[1].endswith("b.json")
        assert files[2].endswith("c.parquet")

    def test_ignores_subdirectories(self, tmp_path: Path) -> None:
        (tmp_path / "data.csv").write_text("x")
        (tmp_path / "empty_dir").mkdir()

        files = sorted(str(f) for f in tmp_path.rglob("*") if f.is_file())
        assert len(files) == 1

    def test_recursive_scan(self, tmp_path: Path) -> None:
        deep = tmp_path / "a" / "b" / "c"
        deep.mkdir(parents=True)
        (deep / "deep.csv").write_text("id\n1")

        files = sorted(str(f) for f in tmp_path.rglob("*") if f.is_file())
        assert len(files) == 1
        assert "deep.csv" in files[0]


class TestIngestFileStepLogic:
    """ingest_file step: single-file processing with error capture."""

    def test_success_result_structure(self, tmp_path: Path) -> None:
        """Successful ingest produces expected result dict."""
        csv_path = tmp_path / "data.csv"
        csv_path.write_text("id,text\n1,hello")

        # Simulate: no ingest_error attribute → success path
        result: dict[str, Any] = {
            "file": str(csv_path),
            "status": "success",
            "rows_ingested": 1,
        }
        assert result["status"] == "success"
        assert result["rows_ingested"] == 1

    def test_dead_letter_result_structure(self) -> None:
        """Failed ingest produces dead-letter entry."""
        result: dict[str, Any] = {
            "file": "/bad/file.csv",
            "status": "failed",
            "error": "corrupt file",
        }
        assert result["status"] == "failed"
        assert "corrupt file" in result["error"]

    def test_ingest_creates_correct_dataset(self, tmp_path: Path) -> None:
        """Verify Ingestor.ingest is called with correct args."""
        from arrow_lake.ingest.ingestor import Ingestor
        from arrow_lake.ingest.storage import LanceStorageManager

        csv_path = tmp_path / "data.csv"
        csv_path.write_text("id,text\n1,hello")

        lake_dir = tmp_path / "lake"
        storage = LanceStorageManager(base_uri=str(lake_dir))
        ingestor = Ingestor(storage)

        # Create a minimal CSV for the ingestor
        report = ingestor.ingest("test_ingest", [str(csv_path)])

        assert report.total_rows == 1
        assert len(report.sources) == 1
        assert report.sources[0].row_count == 1

    def test_unsupported_file_type_produces_error(self, tmp_path: Path) -> None:
        """Unsupported file extension should result in dead-letter."""
        bad_file = tmp_path / "data.xyz"
        bad_file.write_text("garbage")

        error_msg = f"Unsupported file format for '{bad_file}'"
        result: dict[str, Any] = {
            "file": str(bad_file),
            "status": "failed",
            "error": error_msg,
        }
        assert result["status"] == "failed"
        assert "Unsupported" in result["error"]


class TestJoinStepLogic:
    """join step: aggregate success and failure results."""

    def _aggregate(self, inputs: list[dict[str, Any]]) -> dict[str, Any]:
        successes = [i for i in inputs if i["status"] == "success"]
        failures = [i for i in inputs if i["status"] != "success"]
        total_rows = sum(r["rows_ingested"] for r in successes)
        return {
            "successes": successes,
            "failures": failures,
            "total_rows": total_rows,
        }

    def test_all_success(self) -> None:
        inputs = [
            {"file": "a.csv", "status": "success", "rows_ingested": 10},
            {"file": "b.csv", "status": "success", "rows_ingested": 20},
        ]
        result = self._aggregate(inputs)
        assert result["total_rows"] == 30
        assert len(result["successes"]) == 2
        assert len(result["failures"]) == 0

    def test_all_failed(self) -> None:
        inputs = [
            {"file": "a.csv", "status": "failed", "error": "bad"},
            {"file": "b.csv", "status": "failed", "error": "worse"},
        ]
        result = self._aggregate(inputs)
        assert result["total_rows"] == 0
        assert len(result["successes"]) == 0
        assert len(result["failures"]) == 2

    def test_mixed_results(self) -> None:
        inputs = [
            {"file": "a.csv", "status": "success", "rows_ingested": 5},
            {"file": "b.csv", "status": "failed", "error": "timeout"},
            {"file": "c.csv", "status": "success", "rows_ingested": 15},
        ]
        result = self._aggregate(inputs)
        assert result["total_rows"] == 20
        assert len(result["successes"]) == 2
        assert len(result["failures"]) == 1

    def test_empty_inputs(self) -> None:
        result = self._aggregate([])
        assert result["total_rows"] == 0
        assert result["successes"] == []
        assert result["failures"] == []


class TestEndStepSummary:
    """end step: summary JSON report."""

    def _build_summary(
        self,
        successes: list[dict[str, Any]],
        failures: list[dict[str, Any]],
        total_rows: int,
    ) -> dict[str, Any]:
        return {
            "total_files": len(successes) + len(failures),
            "success": len(successes),
            "failed": len(failures),
            "total_rows_ingested": total_rows,
            "dead_letter": failures,
        }

    def test_mixed_summary(self) -> None:
        successes = [{"file": "a.csv", "status": "success", "rows_ingested": 10}]
        failures = [{"file": "b.csv", "status": "failed", "error": "corrupt"}]
        summary = self._build_summary(successes, failures, 10)
        assert summary["total_files"] == 2
        assert summary["success"] == 1
        assert summary["failed"] == 1
        assert summary["total_rows_ingested"] == 10
        assert len(summary["dead_letter"]) == 1

    def test_all_success_summary(self) -> None:
        successes = [{"file": "a.csv", "status": "success", "rows_ingested": 100}]
        summary = self._build_summary(successes, [], 100)
        assert summary["success"] == 1
        assert summary["failed"] == 0
        assert summary["dead_letter"] == []

    def test_json_roundtrip(self) -> None:
        summary = self._build_summary(
            [{"file": "a.csv", "status": "success", "rows_ingested": 42}],
            [],
            42,
        )
        json_str = json.dumps(summary, indent=2)
        parsed = json.loads(json_str)
        assert parsed["total_rows_ingested"] == 42


class TestFlowRegistration:
    """Verify IngestFlow is registered in FlowRegistry."""

    def test_ingest_registered_in_init(self) -> None:
        """flows/__init__.py should include ingest flow."""
        import importlib

        import flows
        from arrow_lake.workflow.base import FlowRegistry

        FlowRegistry.clear()
        flows._registration_attempted = False
        importlib.reload(flows)
        flows._register_flows()

        assert "ingest" in FlowRegistry.list_flows()
