"""Event sourcing audit trail — Story 8.4.

Provides immutable audit logging with HMAC integrity verification.
Each audit entry is persisted to Lance with a cryptographic hash
that can be verified later to detect tampering.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import pyarrow as pa
import structlog

from arrow_lake.exceptions import AuditError, ErrorCode, StorageError

logger = structlog.get_logger(__name__)

__all__ = ["AuditEntry", "AuditTrail"]

_AUDIT_ENTRY_SCHEMA = pa.schema(
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


@dataclass(frozen=True)
class AuditEntry:
    """An immutable audit log entry.

    Attributes:
        audit_id: Unique entry identifier (UUID).
        timestamp: ISO 8601 timestamp.
        event_type: Type of event (create/checkpoint/rollback/delete/query).
        actor: Who or what triggered the event.
        dataset_name: Affected dataset name.
        lance_version: Lance version at time of event.
        metaflow_run_id: Associated Metaflow run ID.
        metaflow_tags: Associated Metaflow tags as dict.
        payload: Additional event data as dict.
        hmac_hash: HMAC-SHA256 hash for integrity verification.
    """

    audit_id: str
    timestamp: str
    event_type: str
    actor: str
    dataset_name: str
    lance_version: int | None
    metaflow_run_id: str
    metaflow_tags: tuple[tuple[str, str], ...]
    payload: tuple[tuple[str, Any], ...]
    hmac_hash: str


class AuditTrail:
    """Immutable audit trail with HMAC integrity verification.

    Persists audit entries to a Lance dataset. Each entry includes
    an HMAC-SHA256 hash computed over the entry fields, enabling
    tamper detection via verify().

    Args:
        storage: LanceStorageManager for unified data access.
        audit_dataset: Name of the audit trail dataset.
        hmac_secret_key: Secret key for HMAC. Empty string disables HMAC.
    """

    def __init__(
        self,
        storage: Any,
        audit_dataset: str = "_audit_trail",
        hmac_secret_key: str = "",
    ) -> None:
        self._storage = storage
        self._audit_dataset = audit_dataset
        self._hmac_secret = hmac_secret_key.encode() if hmac_secret_key else b""
        self._initialized = False
        if not hmac_secret_key:
            structlog.get_logger(__name__).warning(
                "audit_hmac_disabled",
                message="HMAC secret key is empty — audit trail integrity verification is disabled",
            )

    def record(
        self,
        event_type: str,
        dataset_name: str = "",
        actor: str = "system",
        lance_version: int | None = None,
        metaflow_run_id: str = "",
        metaflow_tags: dict[str, str] | None = None,
        payload: dict[str, Any] | None = None,
    ) -> str:
        """Record an audit entry.

        Args:
            event_type: Type of event.
            dataset_name: Affected dataset name.
            actor: Who triggered the event.
            lance_version: Lance version at time of event.
            metaflow_run_id: Associated Metaflow run ID.
            metaflow_tags: Associated Metaflow tags.
            payload: Additional event data.

        Returns:
            The generated audit_id.
        """
        self._ensure_store()

        audit_id = str(uuid.uuid4())
        timestamp = datetime.now(UTC).isoformat()

        entry_dict = {
            "audit_id": audit_id,
            "timestamp": timestamp,
            "event_type": event_type,
            "actor": actor,
            "dataset_name": dataset_name,
            "lance_version": lance_version,
            "metaflow_run_id": metaflow_run_id,
            "metaflow_tags": metaflow_tags or {},
            "payload": payload or {},
        }

        hmac_hash = self._compute_hmac(entry_dict)

        row = {
            "audit_id": audit_id,
            "timestamp": timestamp,
            "event_type": event_type,
            "actor": actor,
            "dataset_name": dataset_name,
            "lance_version": lance_version,
            "metaflow_run_id": metaflow_run_id,
            "metaflow_tags": json.dumps(metaflow_tags or {}),
            "payload": json.dumps(payload or {}),
            "hmac_hash": hmac_hash,
        }
        table = pa.table({k: [v] for k, v in row.items()}, schema=_AUDIT_ENTRY_SCHEMA)

        try:
            self._storage.append_dataset(self._audit_dataset, table)
        except (OSError, StorageError) as exc:
            raise AuditError(
                error_code=ErrorCode.AUDIT_STORE_FAILED,
                message=f"Failed to record audit entry: {exc}",
            ) from exc

        logger.info(
            "audit_entry_recorded",
            audit_id=audit_id,
            event_type=event_type,
            dataset=dataset_name,
        )
        return audit_id

    def verify(self, audit_id: str) -> bool:
        """Verify the HMAC integrity of an audit entry.

        Args:
            audit_id: The audit entry ID to verify.

        Returns:
            True if the entry is intact, False if tampered or not found.
        """
        try:
            table = self._storage.read_dataset(self._audit_dataset)
        except (StorageError, OSError):
            return False

        if table.num_rows == 0:
            return False

        ids = table.column("audit_id").to_pylist()
        if audit_id not in ids:
            return False

        idx = ids.index(audit_id)
        stored_hash = table.column("hmac_hash")[idx].as_py()

        # 注:audit verify 无 key 的语义项目内测试矛盾(unit 期望 False strict /
        # integration 期望 True dev-mode)。保持 False(安全模块保守语义 = 不信任),
        # integration test_verify_no_secret 的 True 期望是测试债,需单独对齐。
        if not stored_hash:
            logger.warning("Audit HMAC key not configured — integrity check skipped")
            return False

        if not self._hmac_secret:
            logger.warning("Audit HMAC key not configured — integrity check skipped")
            return False

        entry_dict = {
            "audit_id": table.column("audit_id")[idx].as_py(),
            "timestamp": table.column("timestamp")[idx].as_py(),
            "event_type": table.column("event_type")[idx].as_py(),
            "actor": table.column("actor")[idx].as_py(),
            "dataset_name": table.column("dataset_name")[idx].as_py(),
            "lance_version": table.column("lance_version")[idx].as_py(),
            "metaflow_run_id": table.column("metaflow_run_id")[idx].as_py(),
            "metaflow_tags": json.loads(table.column("metaflow_tags")[idx].as_py() or "{}"),
            "payload": json.loads(table.column("payload")[idx].as_py() or "{}"),
        }

        computed = self._compute_hmac(entry_dict)
        return hmac.compare_digest(computed, stored_hash)

    def query(
        self,
        dataset_name: str | None = None,
        start: str | None = None,
        end: str | None = None,
        event_type: str | None = None,
    ) -> list[AuditEntry]:
        """Query audit entries with optional filters.

        Args:
            dataset_name: Filter by dataset name.
            start: ISO timestamp lower bound (inclusive).
            end: ISO timestamp upper bound (exclusive).
            event_type: Filter by event type.

        Returns:
            List of matching AuditEntry in chronological order.
        """
        try:
            table = self._storage.read_dataset(self._audit_dataset)
        except (StorageError, OSError):
            return []

        if table.num_rows == 0:
            return []

        entries = [self._row_to_entry(table, i) for i in range(table.num_rows)]

        if dataset_name is not None:
            entries = [e for e in entries if e.dataset_name == dataset_name]
        if event_type is not None:
            entries = [e for e in entries if e.event_type == event_type]
        if start is not None:
            entries = [e for e in entries if e.timestamp >= start]
        if end is not None:
            entries = [e for e in entries if e.timestamp < end]

        return entries

    def export(self, dataset_name: str, fmt: str = "json") -> dict[str, Any]:
        """Export audit entries for a dataset.

        Args:
            dataset_name: Dataset name to export.
            fmt: Export format (only "json" supported).

        Returns:
            Dict with export metadata and entries list.
        """
        entries = self.query(dataset_name=dataset_name)
        return {
            "dataset_name": dataset_name,
            "total_entries": len(entries),
            "format": fmt,
            "entries": [
                {
                    "audit_id": e.audit_id,
                    "timestamp": e.timestamp,
                    "event_type": e.event_type,
                    "actor": e.actor,
                    "lance_version": e.lance_version,
                    "metaflow_run_id": e.metaflow_run_id,
                    "metaflow_tags": dict(e.metaflow_tags),
                    "payload": dict(e.payload),
                }
                for e in entries
            ],
        }

    def replay(self, dataset_name: str, target_version: int) -> list[AuditEntry]:
        """Replay audit entries for a dataset up to a target Lance version.

        Args:
            dataset_name: Dataset name.
            target_version: Replay events up to this version.

        Returns:
            List of AuditEntry up to the target version, in chronological order.
        """
        entries = self.query(dataset_name=dataset_name)
        result = []
        for entry in entries:
            if entry.lance_version is not None and entry.lance_version <= target_version:
                result.append(entry)
        return result

    def _compute_hmac(self, entry_dict: dict[str, Any]) -> str:
        """Compute HMAC-SHA256 over sorted entry fields."""
        if not self._hmac_secret:
            return ""

        canonical = json.dumps(entry_dict, sort_keys=True, default=str)
        return hmac.new(self._hmac_secret, canonical.encode(), hashlib.sha256).hexdigest()

    def _ensure_store(self) -> None:
        """Lazily create the audit trail dataset if it doesn't exist."""
        if self._initialized:
            return

        if not self._storage.dataset_exists(self._audit_dataset):
            empty_table = pa.table(
                {f.name: [] for f in _AUDIT_ENTRY_SCHEMA},
                schema=_AUDIT_ENTRY_SCHEMA,
            )
            self._storage.create_dataset(self._audit_dataset, empty_table)
            logger.info("audit_trail_created", dataset=self._audit_dataset)

        self._initialized = True

    @staticmethod
    def _row_to_entry(table: pa.Table, index: int) -> AuditEntry:
        """Convert a table row to AuditEntry."""
        tags_str = table.column("metaflow_tags")[index].as_py()
        payload_str = table.column("payload")[index].as_py()
        return AuditEntry(
            audit_id=table.column("audit_id")[index].as_py(),
            timestamp=table.column("timestamp")[index].as_py(),
            event_type=table.column("event_type")[index].as_py(),
            actor=table.column("actor")[index].as_py() or "",
            dataset_name=table.column("dataset_name")[index].as_py() or "",
            lance_version=table.column("lance_version")[index].as_py(),
            metaflow_run_id=table.column("metaflow_run_id")[index].as_py() or "",
            metaflow_tags=tuple(sorted(json.loads(tags_str).items())) if tags_str else (),
            payload=tuple(sorted(json.loads(payload_str).items())) if payload_str else (),
            hmac_hash=table.column("hmac_hash")[index].as_py(),
        )
