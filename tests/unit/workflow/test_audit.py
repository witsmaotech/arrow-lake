"""Tests for AuditTrail and AuditEntry — Story 8.4."""

from __future__ import annotations

from unittest.mock import MagicMock

import pyarrow as pa
import pytest
from arrow_lake.exceptions import AuditError, ErrorCode, StorageError
from arrow_lake.workflow.audit import AuditEntry, AuditTrail


class TestAuditEntry:
    """AuditEntry frozen dataclass."""

    def test_frozen(self) -> None:
        entry = AuditEntry(
            audit_id="test-id",
            timestamp="2026-01-01T00:00:00Z",
            event_type="create",
            actor="system",
            dataset_name="docs",
            lance_version=1,
            metaflow_run_id="run-42",
            metaflow_tags=(("env", "dev"),),
            payload=(("key", "val"),),
            hmac_hash="abc123",
        )
        with pytest.raises(AttributeError):
            entry.audit_id = "changed"  # type: ignore[misc]

    def test_field_access(self) -> None:
        entry = AuditEntry(
            audit_id="id-1",
            timestamp="2026-01-01",
            event_type="checkpoint",
            actor="user",
            dataset_name="test",
            lance_version=None,
            metaflow_run_id="",
            metaflow_tags=(),
            payload=(),
            hmac_hash="hash",
        )
        assert entry.audit_id == "id-1"
        assert entry.event_type == "checkpoint"
        assert entry.lance_version is None
        assert entry.metaflow_tags == ()


class TestAuditTrailInit:
    """AuditTrail initialization."""

    def test_default_dataset_name(self) -> None:
        trail = AuditTrail(storage=MagicMock())
        assert trail._audit_dataset == "_audit_trail"

    def test_custom_dataset_name(self) -> None:
        trail = AuditTrail(storage=MagicMock(), audit_dataset="custom_audit")
        assert trail._audit_dataset == "custom_audit"

    def test_hmac_key_encoded(self) -> None:
        trail = AuditTrail(storage=MagicMock(), hmac_secret_key="secret")
        assert trail._hmac_secret == b"secret"

    def test_empty_hmac_key(self) -> None:
        trail = AuditTrail(storage=MagicMock(), hmac_secret_key="")
        assert trail._hmac_secret == b""


class TestAuditTrailRecord:
    """AuditTrail.record() — create and persist audit entries."""

    def _make_trail(self) -> tuple[AuditTrail, MagicMock]:
        storage = MagicMock()
        storage.dataset_exists.return_value = True
        storage.append_dataset.return_value = None
        trail = AuditTrail(storage=storage, hmac_secret_key="test-key")
        return trail, storage

    def test_record_returns_audit_id(self) -> None:
        trail, _storage = self._make_trail()
        audit_id = trail.record(event_type="create", dataset_name="docs")
        assert isinstance(audit_id, str)
        assert len(audit_id) > 0

    def test_record_appends_to_dataset(self) -> None:
        trail, storage = self._make_trail()
        trail.record(event_type="create", dataset_name="docs")
        storage.append_dataset.assert_called_once()
        call_args = storage.append_dataset.call_args
        assert call_args[0][0] == "_audit_trail"

    def test_record_with_all_fields(self) -> None:
        trail, storage = self._make_trail()
        trail.record(
            event_type="checkpoint",
            dataset_name="my_data",
            actor="admin",
            lance_version=5,
            metaflow_run_id="run-99",
            metaflow_tags={"env": "prod"},
            payload={"rows": 1000},
        )
        # Verify the table passed to append_dataset
        table = storage.append_dataset.call_args[0][1]
        assert table.num_rows == 1
        assert table.column("event_type")[0].as_py() == "checkpoint"
        assert table.column("dataset_name")[0].as_py() == "my_data"
        assert table.column("lance_version")[0].as_py() == 5
        assert table.column("hmac_hash")[0].as_py() != ""

    def test_record_raises_on_storage_failure(self) -> None:
        trail, storage = self._make_trail()
        storage.append_dataset.side_effect = StorageError(
            error_code=ErrorCode.STORAGE_WRITE_FAILED, message="disk full"
        )
        with pytest.raises(AuditError, match="Failed to record"):
            trail.record(event_type="create")


class TestAuditTrailVerify:
    """AuditTrail.verify() — HMAC integrity check."""

    def _make_trail_with_entry(self) -> tuple[AuditTrail, MagicMock]:
        storage = MagicMock()
        storage.dataset_exists.return_value = True
        storage.append_dataset.return_value = None

        trail = AuditTrail(storage=storage, hmac_secret_key="test-key")
        audit_id = trail.record(event_type="create", dataset_name="docs")

        # Simulate the stored table
        stored_table = storage.append_dataset.call_args[0][1]
        storage.read_dataset.return_value = stored_table

        return trail, storage, audit_id

    def test_verify_valid_entry(self) -> None:
        trail, _, audit_id = self._make_trail_with_entry()
        assert trail.verify(audit_id) is True

    def test_verify_missing_entry(self) -> None:
        trail, _, _ = self._make_trail_with_entry()
        assert trail.verify("nonexistent-id") is False

    def test_verify_storage_error_returns_false(self) -> None:
        trail, storage, _ = self._make_trail_with_entry()
        storage.read_dataset.side_effect = StorageError(
            error_code=ErrorCode.STORAGE_PATH_NOT_FOUND, message="not found"
        )
        assert trail.verify("any-id") is False

    def test_verify_no_hmac_key_returns_false(self) -> None:
        storage = MagicMock()
        storage.dataset_exists.return_value = True
        storage.append_dataset.return_value = None
        trail = AuditTrail(storage=storage, hmac_secret_key="")
        trail.record(event_type="create")
        stored_table = storage.append_dataset.call_args[0][1]
        storage.read_dataset.return_value = stored_table
        # No HMAC key → verification is disabled, returns False
        assert trail.verify(stored_table.column("audit_id")[0].as_py()) is False


class TestAuditTrailQuery:
    """AuditTrail.query() — filter audit entries."""

    def _make_trail_with_entries(self) -> tuple[AuditTrail, MagicMock]:
        storage = MagicMock()
        storage.dataset_exists.return_value = True
        storage.append_dataset.return_value = None
        trail = AuditTrail(storage=storage, hmac_secret_key="key")

        trail.record(event_type="create", dataset_name="docs")
        trail.record(event_type="query", dataset_name="docs")
        trail.record(event_type="create", dataset_name="other")

        # Collect all appended tables into one
        tables = [call[0][1] for call in storage.append_dataset.call_args_list]
        merged = pa.concat_tables(tables)
        storage.read_dataset.return_value = merged
        return trail, storage

    def test_query_all(self) -> None:
        trail, _ = self._make_trail_with_entries()
        entries = trail.query()
        assert len(entries) == 3

    def test_query_by_dataset(self) -> None:
        trail, _ = self._make_trail_with_entries()
        entries = trail.query(dataset_name="docs")
        assert len(entries) == 2
        assert all(e.dataset_name == "docs" for e in entries)

    def test_query_by_event_type(self) -> None:
        trail, _ = self._make_trail_with_entries()
        entries = trail.query(event_type="create")
        assert len(entries) == 2
        assert all(e.event_type == "create" for e in entries)

    def test_query_empty_storage(self) -> None:
        storage = MagicMock()
        storage.read_dataset.side_effect = StorageError(
            error_code=ErrorCode.STORAGE_PATH_NOT_FOUND, message="no data"
        )
        trail = AuditTrail(storage=storage, hmac_secret_key="key")
        entries = trail.query()
        assert entries == []


class TestAuditTrailExport:
    """AuditTrail.export() — structured export."""

    def test_export_json(self) -> None:
        storage = MagicMock()
        storage.dataset_exists.return_value = True
        storage.append_dataset.return_value = None
        trail = AuditTrail(storage=storage, hmac_secret_key="key")

        trail.record(event_type="create", dataset_name="docs")

        stored_table = storage.append_dataset.call_args[0][1]
        storage.read_dataset.return_value = stored_table

        result = trail.export("docs")
        assert result["dataset_name"] == "docs"
        assert result["total_entries"] == 1
        assert result["format"] == "json"
        assert len(result["entries"]) == 1
