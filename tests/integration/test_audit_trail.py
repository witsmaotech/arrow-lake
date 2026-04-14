"""Integration tests for event sourcing audit trail — Story 8.4.

Tests audit entry persistence, HMAC verification, querying, and
export over real Lance datasets.
"""

from __future__ import annotations

from pathlib import Path

from arrow_lake.workflow.audit import AuditTrail


class TestRecordAndVerify:
    """Test audit entry recording and HMAC verification."""

    def test_record_and_verify(self, tmp_path: Path) -> None:
        """Create AuditTrail with secret key, record event, verify() returns True."""
        trail = AuditTrail(str(tmp_path), hmac_secret_key="test-secret-key")
        audit_id = trail.record(
            event_type="create",
            dataset_name="test_dataset",
            actor="integration_test",
        )

        assert audit_id
        assert trail.verify(audit_id) is True

    def test_verify_nonexistent_id(self, tmp_path: Path) -> None:
        """verify() returns False for non-existent audit ID."""
        trail = AuditTrail(str(tmp_path), hmac_secret_key="secret")
        trail.record(event_type="create", dataset_name="ds")

        assert trail.verify("00000000-0000-0000-0000-000000000000") is False

    def test_verify_no_secret(self, tmp_path: Path) -> None:
        """verify() returns True when no secret key is configured (dev mode)."""
        trail = AuditTrail(str(tmp_path), hmac_secret_key="")
        audit_id = trail.record(event_type="create", dataset_name="ds")

        assert trail.verify(audit_id) is True


class TestQueryByDataset:
    """Test querying audit entries by dataset name."""

    def test_query_by_dataset(self, tmp_path: Path) -> None:
        """Record 3 events for dataset 'test_ds', query returns 3."""
        trail = AuditTrail(str(tmp_path), hmac_secret_key="secret")
        trail.record(event_type="create", dataset_name="test_ds")
        trail.record(event_type="append", dataset_name="test_ds")
        trail.record(event_type="transform", dataset_name="test_ds")
        # Record for a different dataset
        trail.record(event_type="create", dataset_name="other_ds")

        entries = trail.query(dataset_name="test_ds")
        assert len(entries) == 3
        event_types = [e.event_type for e in entries]
        assert "create" in event_types
        assert "append" in event_types
        assert "transform" in event_types

    def test_query_filters_by_event_type(self, tmp_path: Path) -> None:
        """Query with event_type filter returns only matching entries."""
        trail = AuditTrail(str(tmp_path), hmac_secret_key="secret")
        trail.record(event_type="create", dataset_name="ds")
        trail.record(event_type="append", dataset_name="ds")
        trail.record(event_type="delete", dataset_name="ds")

        entries = trail.query(dataset_name="ds", event_type="create")
        assert len(entries) == 1
        assert entries[0].event_type == "create"


class TestExport:
    """Test audit trail export."""

    def test_export(self, tmp_path: Path) -> None:
        """Export returns dict with entries list."""
        trail = AuditTrail(str(tmp_path), hmac_secret_key="secret")
        trail.record(event_type="create", dataset_name="export_ds", actor="alice")
        trail.record(event_type="append", dataset_name="export_ds", actor="bob")

        exported = trail.export("export_ds")

        assert exported["dataset_name"] == "export_ds"
        assert exported["total_entries"] == 2
        assert exported["format"] == "json"
        assert len(exported["entries"]) == 2
        actors = [e["actor"] for e in exported["entries"]]
        assert "alice" in actors
        assert "bob" in actors

    def test_export_empty_dataset(self, tmp_path: Path) -> None:
        """Export for dataset with no entries returns empty list."""
        trail = AuditTrail(str(tmp_path), hmac_secret_key="secret")
        exported = trail.export("nonexistent")

        assert exported["total_entries"] == 0
        assert exported["entries"] == []
