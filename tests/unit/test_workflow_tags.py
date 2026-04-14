"""Tests for Story 6.7 — Tag-Based Tracking."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from arrow_lake.workflow.tags import RunTags, find_failed_runs, generate_resume_tags


class TestRunTags:
    """Test RunTags frozen dataclass."""

    def test_to_list_basic(self) -> None:
        tags = RunTags(flow_name="quality_pipeline", run_id="run-123")
        result = tags.to_list()
        assert "flow:quality_pipeline" in result
        assert "run_id:run-123" in result
        assert "status:running" in result

    def test_to_list_with_dataset(self) -> None:
        tags = RunTags(flow_name="ingest", run_id="run-456", dataset_name="documents")
        result = tags.to_list()
        assert "dataset:documents" in result

    def test_to_list_without_dataset(self) -> None:
        tags = RunTags(flow_name="ingest", run_id="run-456")
        result = tags.to_list()
        assert not any("dataset:" in t for t in result)

    def test_frozen(self) -> None:
        tags = RunTags(flow_name="flow", run_id="id")
        with pytest.raises(AttributeError):
            tags.flow_name = "other"

    def test_status_field(self) -> None:
        tags = RunTags(flow_name="flow", run_id="id", status="completed")
        assert "status:completed" in tags.to_list()

    def test_custom_status(self) -> None:
        tags = RunTags(flow_name="flow", run_id="id", status="failed")
        assert "status:failed" in tags.to_list()


class TestGenerateResumeTags:
    """Test generate_resume_tags function."""

    def test_basic_resume_tags(self) -> None:
        tags = generate_resume_tags("failed-run-123", "quality_pipeline")
        assert "flow:quality_pipeline" in tags
        assert "resumed_from:failed-run-123" in tags
        assert "status:resumed" in tags

    def test_different_flow(self) -> None:
        tags = generate_resume_tags("run-999", "ingest_flow")
        assert "flow:ingest_flow" in tags


class TestFindFailedRuns:
    """Test find_failed_runs function."""

    @patch("metaflow.Flow")
    def test_returns_failed_run_ids(self, mock_flow_cls: MagicMock) -> None:
        mock_run_ok = MagicMock()
        mock_run_ok.successful = True
        mock_run_ok.id = "run-ok"

        mock_run_fail = MagicMock()
        mock_run_fail.successful = False
        mock_run_fail.id = "run-fail"

        mock_flow_instance = MagicMock()
        mock_flow_instance.runs.return_value = [mock_run_fail, mock_run_ok]
        mock_flow_cls.return_value = mock_flow_instance

        result = find_failed_runs("test_flow")
        assert result == ["run-fail"]

    @patch("metaflow.Flow")
    def test_returns_empty_when_all_succeed(self, mock_flow_cls: MagicMock) -> None:
        mock_run = MagicMock()
        mock_run.successful = True
        mock_run.id = "run-ok"

        mock_flow_instance = MagicMock()
        mock_flow_instance.runs.return_value = [mock_run]
        mock_flow_cls.return_value = mock_flow_instance

        result = find_failed_runs("test_flow")
        assert result == []

    def test_returns_empty_on_exception(self, tmp_path: object) -> None:
        # find_failed_runs catches any exception and returns []
        # Simulate by removing metaflow temporarily
        import sys

        metaflow_orig = sys.modules.get("metaflow")
        sys.modules["metaflow"] = None  # type: ignore[assignment]
        try:
            result = find_failed_runs("test_flow")
            assert result == []
        finally:
            if metaflow_orig is not None:
                sys.modules["metaflow"] = metaflow_orig
            elif "metaflow" in sys.modules:
                del sys.modules["metaflow"]
