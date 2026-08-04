"""Tests for arrow_lake.workflow.audit — Story 8.4 Event Sourcing Audit.

Tests AuditEntry, AuditTrail (HMAC, record, query, export, replay),
and AuditConfig using mocked LanceStorageManager (no real datasets).
"""

from __future__ import annotations

import hashlib
import hmac as hmac_mod
import json
from dataclasses import FrozenInstanceError
from unittest.mock import MagicMock

import pyarrow as pa
import pytest
from arrow_lake.config import AuditConfig
from arrow_lake.exceptions import ArrowLakeError, AuditError
from arrow_lake.workflow.audit import AuditEntry, AuditTrail

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_AUDIT_SCHEMA = pa.schema(
    [
        pa.field("audit_id", pa.string(), nullable=False),
        pa.field("timestamp", pa.string(), nullable=False),
        pa.field("event_type", pa.string(), nullable=False),
        pa.field("actor", pa.string(), nullable=True),
        pa.field("dataset_name", pa.string(), nullable=True),
        pa.field("lance_version", pa.int64(), nullable=True),
        pa.field("metaflow_run_id", pa.string(), nullable=True),
        pa.field("metaflow_tags", pa.string(), nullable=True),
        pa.field("payload", pa.string(), nullable=True),
        pa.field("hmac_hash", pa.string(), nullable=False),
    ]
)


def _make_audit_row(
    audit_id: str = "abc",
    timestamp: str = "2026-01-01T00:00:00",
    event_type: str = "create",
    actor: str = "system",
    dataset_name: str = "ds",
    lance_version: int | None = 1,
    metaflow_run_id: str = "",
    metaflow_tags: str = "{}",
    payload: str = "{}",
    hmac_hash: str = "",
) -> dict[str, object]:
    return {
        "audit_id": audit_id,
        "timestamp": timestamp,
        "event_type": event_type,
        "actor": actor,
        "dataset_name": dataset_name,
        "lance_version": lance_version,
        "metaflow_run_id": metaflow_run_id,
        "metaflow_tags": metaflow_tags,
        "payload": payload,
        "hmac_hash": hmac_hash,
    }


def _compute_hmac(secret: bytes, entry_dict: dict) -> str:
    """Replicate AuditTrail._compute_hmac for test assertions."""
    canonical = json.dumps(entry_dict, sort_keys=True, default=str)
    return hmac_mod.new(secret, canonical.encode(), hashlib.sha256).hexdigest()


def _make_arrow_table(rows: list[dict[str, object]]) -> pa.Table:
    """Create an Arrow table from row dicts using _AUDIT_SCHEMA."""
    if not rows:
        return pa.table({f.name: [] for f in _AUDIT_SCHEMA}, schema=_AUDIT_SCHEMA)
    columns: dict[str, list[object]] = {f.name: [] for f in _AUDIT_SCHEMA}
    for row in rows:
        for field_name in columns:
            columns[field_name].append(row.get(field_name))
    return pa.table(columns, schema=_AUDIT_SCHEMA)


def _make_mock_storage() -> MagicMock:
    """Create a mock LanceStorageManager."""
    storage = MagicMock()
    storage.dataset_exists.return_value = True
    return storage


# ---------------------------------------------------------------------------
# TestAuditEntry
# ---------------------------------------------------------------------------


class TestAuditEntry:
    """Test AuditEntry frozen dataclass."""

    def test_frozen(self) -> None:
        entry = AuditEntry(
            audit_id="a1",
            timestamp="2026-01-01T00:00:00",
            event_type="create",
            actor="system",
            dataset_name="ds",
            lance_version=1,
            metaflow_run_id="",
            metaflow_tags=(("k", "v"),),
            payload=(("p", 1),),
            hmac_hash="hash123",
        )
        with pytest.raises(FrozenInstanceError):
            entry.audit_id = "changed"  # type: ignore[misc]

    def test_all_fields_present(self) -> None:
        entry = AuditEntry(
            audit_id="a1",
            timestamp="2026-01-01T00:00:00",
            event_type="create",
            actor="system",
            dataset_name="ds",
            lance_version=1,
            metaflow_run_id="run-1",
            metaflow_tags=(("env", "prod"),),
            payload=(("key", "val"),),
            hmac_hash="h",
        )
        assert entry.audit_id == "a1"
        assert entry.timestamp == "2026-01-01T00:00:00"
        assert entry.event_type == "create"
        assert entry.actor == "system"
        assert entry.dataset_name == "ds"
        assert entry.lance_version == 1
        assert entry.metaflow_run_id == "run-1"
        assert entry.metaflow_tags == (("env", "prod"),)
        assert entry.payload == (("key", "val"),)
        assert entry.hmac_hash == "h"


# ---------------------------------------------------------------------------
# TestComputeHmac
# ---------------------------------------------------------------------------


class TestComputeHmac:
    """Test AuditTrail._compute_hmac."""

    def test_empty_secret_returns_empty_string(self) -> None:
        trail = AuditTrail(_make_mock_storage(), hmac_secret_key="")
        result = trail._compute_hmac({"audit_id": "abc"})
        assert result == ""

    def test_non_empty_secret_returns_hex_string(self) -> None:
        trail = AuditTrail(_make_mock_storage(), hmac_secret_key="test-secret")
        result = trail._compute_hmac({"audit_id": "abc", "timestamp": "2026-01-01T00:00:00"})
        assert len(result) == 64  # SHA-256 hex digest
        assert all(c in "0123456789abcdef" for c in result)

    def test_deterministic_output(self) -> None:
        trail = AuditTrail(_make_mock_storage(), hmac_secret_key="test-secret")
        data = {"audit_id": "abc", "timestamp": "2026-01-01T00:00:00"}
        r1 = trail._compute_hmac(data)
        r2 = trail._compute_hmac(data)
        assert r1 == r2


# ---------------------------------------------------------------------------
# TestVerify
# ---------------------------------------------------------------------------


class TestVerify:
    """Test AuditTrail.verify."""

    def test_matching_hash_returns_true(self) -> None:
        secret = b"test-secret"
        entry_dict = {
            "audit_id": "abc",
            "timestamp": "2026-01-01T00:00:00",
            "event_type": "create",
            "actor": "system",
            "dataset_name": "ds",
            "lance_version": 1,
            "metaflow_run_id": "",
            "metaflow_tags": {},
            "payload": {},
        }
        expected_hash = _compute_hmac(secret, entry_dict)
        row = _make_audit_row(hmac_hash=expected_hash)
        table = _make_arrow_table([row])
        storage = _make_mock_storage()
        storage.read_dataset.return_value = table

        trail = AuditTrail(storage, hmac_secret_key="test-secret")
        trail._initialized = True
        assert trail.verify("abc") is True

    def test_tampered_hash_returns_false(self) -> None:
        row = _make_audit_row(hmac_hash="deadbeef" * 8)
        table = _make_arrow_table([row])
        storage = _make_mock_storage()
        storage.read_dataset.return_value = table

        trail = AuditTrail(storage, hmac_secret_key="test-secret")
        trail._initialized = True
        assert trail.verify("abc") is False

    def test_empty_hash_returns_false(self) -> None:
        """Empty stored HMAC hash cannot be verified — returns False (v1.6.0 strict)."""
        row = _make_audit_row(hmac_hash="")
        table = _make_arrow_table([row])
        storage = _make_mock_storage()
        storage.read_dataset.return_value = table

        trail = AuditTrail(storage, hmac_secret_key="test-secret")
        trail._initialized = True
        assert trail.verify("abc") is False

    def test_not_found_returns_false(self) -> None:
        row = _make_audit_row(audit_id="other_id")
        table = _make_arrow_table([row])
        storage = _make_mock_storage()
        storage.read_dataset.return_value = table

        trail = AuditTrail(storage, hmac_secret_key="test-secret")
        trail._initialized = True
        assert trail.verify("nonexistent") is False


# ---------------------------------------------------------------------------
# TestRecord
# ---------------------------------------------------------------------------


class TestRecord:
    """Test AuditTrail.record."""

    def test_generates_uuid_and_timestamp(self) -> None:
        storage = _make_mock_storage()
        trail = AuditTrail(storage)
        trail._initialized = True
        audit_id = trail.record("create", dataset_name="ds")
        assert len(audit_id) == 36

        # Verify append_dataset was called
        storage.append_dataset.assert_called_once()
        call_arg = storage.append_dataset.call_args[0][1]
        assert isinstance(call_arg, pa.Table)
        assert call_arg.num_rows == 1

    def test_calls_append_with_correct_schema(self) -> None:
        storage = _make_mock_storage()
        trail = AuditTrail(storage)
        trail._initialized = True
        trail.record("checkpoint", dataset_name="my_ds", actor="alice")
        call_arg = storage.append_dataset.call_args[0][1]
        assert "audit_id" in call_arg.column_names
        assert "hmac_hash" in call_arg.column_names

    def test_record_with_hmac(self) -> None:
        storage = _make_mock_storage()
        trail = AuditTrail(storage, hmac_secret_key="secret-key")
        trail._initialized = True
        audit_id = trail.record("create", dataset_name="ds")
        assert audit_id
        call_arg = storage.append_dataset.call_args[0][1]
        # Verify HMAC hash column is populated
        hashes = call_arg.column("hmac_hash").to_pylist()
        assert len(hashes[0]) == 64  # SHA-256 hex


# ---------------------------------------------------------------------------
# TestQuery
# ---------------------------------------------------------------------------


class TestQuery:
    """Test AuditTrail.query filtering."""

    def _setup_rows(self) -> list[dict[str, object]]:
        return [
            _make_audit_row(
                audit_id="a1",
                timestamp="2026-01-01T10:00:00",
                event_type="create",
                dataset_name="ds_a",
            ),
            _make_audit_row(
                audit_id="a2",
                timestamp="2026-01-02T10:00:00",
                event_type="checkpoint",
                dataset_name="ds_a",
            ),
            _make_audit_row(
                audit_id="a3",
                timestamp="2026-01-03T10:00:00",
                event_type="create",
                dataset_name="ds_b",
            ),
        ]

    def _setup_trail(self, rows: list[dict[str, object]]) -> tuple[AuditTrail, MagicMock]:
        table = _make_arrow_table(rows)
        storage = _make_mock_storage()
        storage.read_dataset.return_value = table
        trail = AuditTrail(storage)
        trail._initialized = True
        return trail, storage

    def test_filter_by_dataset_name(self) -> None:
        rows = self._setup_rows()
        trail, _storage = self._setup_trail(rows)
        result = trail.query(dataset_name="ds_a")
        assert len(result) == 2
        assert all(e.dataset_name == "ds_a" for e in result)

    def test_filter_by_event_type(self) -> None:
        rows = self._setup_rows()
        trail, _storage = self._setup_trail(rows)
        result = trail.query(event_type="create")
        assert len(result) == 2
        assert all(e.event_type == "create" for e in result)

    def test_filter_by_time_range(self) -> None:
        rows = self._setup_rows()
        trail, _storage = self._setup_trail(rows)
        result = trail.query(start="2026-01-02T00:00:00", end="2026-01-03T00:00:00")
        assert len(result) == 1
        assert result[0].audit_id == "a2"

    def test_no_filters_returns_all(self) -> None:
        rows = self._setup_rows()
        trail, _storage = self._setup_trail(rows)
        result = trail.query()
        assert len(result) == 3


# ---------------------------------------------------------------------------
# TestExport
# ---------------------------------------------------------------------------


class TestExport:
    """Test AuditTrail.export."""

    def test_returns_dict_with_expected_keys(self) -> None:
        rows = [
            _make_audit_row(
                audit_id="a1",
                timestamp="2026-01-01T10:00:00",
                dataset_name="ds_export",
            ),
        ]
        table = _make_arrow_table(rows)
        storage = _make_mock_storage()
        storage.read_dataset.return_value = table

        trail = AuditTrail(storage)
        trail._initialized = True
        result = trail.export("ds_export")
        assert result["dataset_name"] == "ds_export"
        assert result["total_entries"] == 1
        assert isinstance(result["entries"], list)
        assert len(result["entries"]) == 1


# ---------------------------------------------------------------------------
# TestReplay
# ---------------------------------------------------------------------------


class TestReplay:
    """Test AuditTrail.replay."""

    def test_returns_entries_up_to_target_version(self) -> None:
        rows = [
            _make_audit_row(
                audit_id="a1",
                dataset_name="ds_replay",
                lance_version=1,
            ),
            _make_audit_row(
                audit_id="a2",
                dataset_name="ds_replay",
                lance_version=2,
            ),
            _make_audit_row(
                audit_id="a3",
                dataset_name="ds_replay",
                lance_version=5,
            ),
        ]
        table = _make_arrow_table(rows)
        storage = _make_mock_storage()
        storage.read_dataset.return_value = table

        trail = AuditTrail(storage)
        trail._initialized = True
        result = trail.replay("ds_replay", target_version=2)
        assert len(result) == 2
        assert result[0].audit_id == "a1"
        assert result[1].audit_id == "a2"


# ---------------------------------------------------------------------------
# TestAuditConfig
# ---------------------------------------------------------------------------


class TestAuditConfig:
    """Test AuditConfig defaults."""

    def test_defaults(self) -> None:
        cfg = AuditConfig()
        assert cfg.enabled is False
        assert cfg.hmac_secret_key == ""
        assert cfg.audit_dataset == "sys_audit_trail"
        assert cfg.auto_record_workflow is True


# ---------------------------------------------------------------------------
# TestAuditError
# ---------------------------------------------------------------------------


class TestAuditError:
    """Test AuditError inheritance."""

    def test_is_subclass_of_arrow_lake_error(self) -> None:
        assert issubclass(AuditError, ArrowLakeError)
