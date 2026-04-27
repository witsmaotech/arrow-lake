"""Tests for _LakeAuditMixin facade methods."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from arrow_lake import Lake


@pytest.fixture()
def lake(tmp_path: Path) -> Lake:
    return Lake(base_uri=str(tmp_path / "lance_data"))


class TestAuditRecord:
    def test_audit_record_returns_id(self, lake: Lake) -> None:
        table = MagicMock()
        with patch("arrow_lake.workflow.audit.AuditTrail") as mock_trail:
            mock_trail.return_value.record.return_value = "audit_123"
            result = lake.audit_record("ingest", dataset_name="ds1", actor="user1")
            assert result == "audit_123"
            mock_trail.return_value.record.assert_called_once()

    def test_audit_record_passes_all_params(self, lake: Lake) -> None:
        with patch("arrow_lake.workflow.audit.AuditTrail") as mock_trail:
            mock_trail.return_value.record.return_value = "audit_456"
            lake.audit_record(
                event_type="delete",
                dataset_name="ds1",
                actor="admin",
                lance_version=3,
                metaflow_run_id="run_789",
                metaflow_tags={"env": "prod"},
                payload={"reason": "GDPR"},
            )
            call_kwargs = mock_trail.return_value.record.call_args[1]
            assert call_kwargs["event_type"] == "delete"
            assert call_kwargs["dataset_name"] == "ds1"
            assert call_kwargs["actor"] == "admin"
            assert call_kwargs["lance_version"] == 3
            assert call_kwargs["metaflow_run_id"] == "run_789"
            assert call_kwargs["metaflow_tags"] == {"env": "prod"}
            assert call_kwargs["payload"] == {"reason": "GDPR"}


class TestAuditVerify:
    def test_audit_verify_returns_bool(self, lake: Lake) -> None:
        with patch("arrow_lake.workflow.audit.AuditTrail") as mock_trail:
            mock_trail.return_value.verify.return_value = True
            result = lake.audit_verify("audit_123")
            assert result is True

    def test_audit_verify_false_on_tamper(self, lake: Lake) -> None:
        with patch("arrow_lake.workflow.audit.AuditTrail") as mock_trail:
            mock_trail.return_value.verify.return_value = False
            result = lake.audit_verify("audit_123")
            assert result is False


class TestAuditQuery:
    def test_audit_query_returns_list(self, lake: Lake) -> None:
        mock_entry = MagicMock()
        with patch("arrow_lake.workflow.audit.AuditTrail") as mock_trail:
            mock_trail.return_value.query.return_value = [mock_entry]
            result = lake.audit_query(dataset_name="ds1")
            assert len(result) == 1
            mock_trail.return_value.query.assert_called_once_with(
                dataset_name="ds1", start=None, end=None, event_type=None
            )

    def test_audit_query_with_filters(self, lake: Lake) -> None:
        with patch("arrow_lake.workflow.audit.AuditTrail") as mock_trail:
            mock_trail.return_value.query.return_value = []
            lake.audit_query(
                dataset_name="ds1",
                start="2026-01-01",
                end="2026-12-31",
                event_type="ingest",
            )
            mock_trail.return_value.query.assert_called_once_with(
                dataset_name="ds1",
                start="2026-01-01",
                end="2026-12-31",
                event_type="ingest",
            )


class TestAuditExport:
    def test_audit_export_returns_dict(self, lake: Lake) -> None:
        with patch("arrow_lake.workflow.audit.AuditTrail") as mock_trail:
            mock_trail.return_value.export.return_value = {"entries": [], "count": 0}
            result = lake.audit_export("ds1")
            assert isinstance(result, dict)
            assert result["count"] == 0
            mock_trail.return_value.export.assert_called_once_with("ds1")


class TestAuditAnalyze:
    def test_audit_analyze_returns_list(self, lake: Lake) -> None:
        with patch("arrow_lake.workflow.audit.AuditTrail") as mock_trail:
            mock_trail.return_value.query.return_value = []
            with patch("arrow_lake.workflow.audit_analyzer.AuditAnalyzer") as mock_analyzer:
                mock_analyzer.return_value.analyze.return_value = []
                result = lake.audit_analyze()
                assert isinstance(result, list)
