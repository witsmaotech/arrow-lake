"""P0-8/P1-2 (review 2026-08-26): container restore destination + identity guards.

Container backups (DR14 W4.4) store table dirs (``{table}.lance/``) inside
the dataset's backup prefix; restore previously wrote them under the
unconditional ``{name}.lance/`` prefix, producing the illegal
``{name}.lance/{table}.lance/`` layout and flipping the identity to a
single-table dataset. The write paths (``write_lance_from_dataframe``,
``restore_dataset``) additionally lacked the D3 single-table-vs-container
identity guard ``create_dataset`` has.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from unittest.mock import MagicMock

import pyarrow as pa
import pytest

from arrow_lake.config import ArrowLakeConfig, StorageBackend
from arrow_lake.exceptions import StorageError
from arrow_lake.ops.backup_restore import BackupRestorer


@dataclass(frozen=True)
class _FakeListResult:
    keys: list[str]
    next_token: str | None = None
    truncated: bool = False
    count: int = 0


@dataclass
class _FakeManifest:
    backup_id: str = "test-backup"
    datasets: list = None
    blob_prefixes: list = None

    def __post_init__(self):
        if self.datasets is None:
            self.datasets = [{"name": "gas", "rows": 10, "file_hashes": {}}]


def _remote(blob_store: MagicMock) -> BackupRestorer:
    cfg = ArrowLakeConfig()
    cfg.storage.backend = StorageBackend.S3
    cfg.storage.base_uri = "lake"
    return BackupRestorer(
        blob_store=blob_store, lance_base_uri="lake", storage_config=cfg.storage,
    )


def _prefix_aware_blob_store(backup_prefix: str, rel_paths: list[str]) -> MagicMock:
    """Blob store mock whose list_blobs is prefix-aware: the backup prefix
    lists the backup's rel paths, the temp prefix lists the SAME rel paths
    under itself (mirroring Phase 1's copy), everything else is empty."""
    bs = MagicMock()
    bs.download.return_value = b"data"

    def _list(prefix, max_keys=1000, continuation_token=None):
        if prefix == backup_prefix:
            return _FakeListResult(keys=[f"{backup_prefix}{r}" for r in rel_paths])
        if ".restore-" in prefix:
            return _FakeListResult(keys=[f"{prefix}{r}" for r in rel_paths])
        return _FakeListResult(keys=[])

    bs.list_blobs.side_effect = _list
    return bs

class TestContainerRestoreDestination:
    def test_container_backup_restores_to_container_prefix(self) -> None:
        """Table dirs land at ``{base}/{name}/{table}.lance/`` — not the
        illegal ``{name}.lance/{table}.lance/`` nesting."""
        bs = _prefix_aware_blob_store(
            "backups/b1/datasets/gas/", ["segments.lance/_versions/1.manifest"],
        )
        manifest = _FakeManifest(backup_id="b1")
        _remote(bs).restore_lance_dataset("gas", manifest, overwrite=True)
        dest_keys = [args.args[1] for args in bs.copy.call_args_list]
        # Phase 1 copies backup -> tmp; Phase 3 copies tmp -> FINAL dest.
        final = [k for k in dest_keys if "restore-" not in k]
        assert final == ["lake/gas/segments.lance/_versions/1.manifest"]

    def test_plain_backup_still_restores_to_lance_prefix(self) -> None:
        bs = _prefix_aware_blob_store(
            "backups/b1/datasets/gas/", ["_versions/1.manifest"],
        )
        _remote(bs).restore_lance_dataset("gas", _FakeManifest(backup_id="b1"), overwrite=True)
        dest_keys = [args.args[1] for args in bs.copy.call_args_list]
        final = [k for k in dest_keys if "restore-" not in k]
        assert final == ["lake/gas.lance/_versions/1.manifest"]

    def test_container_detected_from_manifest_hashes(self) -> None:
        """Hash keys (no listing needed) carry the table-dir marker."""
        bs = _prefix_aware_blob_store(
            "backups/b1/datasets/gas/", ["segments.lance/_versions/1.manifest"],
        )
        import hashlib

        manifest = _FakeManifest(backup_id="b1")
        manifest.datasets = [{
            "name": "gas",
            "file_hashes": {
                "segments.lance/_versions/1.manifest": hashlib.sha256(b"data").hexdigest(),
            },
        }]
        _remote(bs).restore_lance_dataset("gas", manifest, overwrite=True)
        dest_keys = [args.args[1] for args in bs.copy.call_args_list]
        final = [k for k in dest_keys if "restore-" not in k]
        assert final == ["lake/gas/segments.lance/_versions/1.manifest"]

    def test_bare_lance_file_is_not_container(self) -> None:
        """A file literally named ``data.lance`` (local-fixture shape) must
        not be mistaken for a table dir."""
        from arrow_lake.ops.backup_restore import BackupRestorer as BR

        assert BR._backup_is_container(["data.lance"]) is False
        assert BR._backup_is_container(["segments.lance/_versions/1"]) is True
        assert BR._backup_is_container(["_versions/1.manifest"]) is False

    def test_identity_conflict_refused(self) -> None:
        """Plain backup + existing container prefix → refuse (no dual identity)."""
        bs = MagicMock()
        # Every list call returns the container's objects (prefix-agnostic mock).
        bs.list_blobs.return_value = _FakeListResult(
            keys=["lake/gas/segments.lance/_versions/1.manifest"],
        )
        with pytest.raises(StorageError, match="Identity conflict"):
            _remote(bs).restore_lance_dataset("gas", _FakeManifest(), overwrite=False)


class TestWritePathIdentityGuards:
    """P1-2: write paths refuse to create a dual identity."""

    @staticmethod
    def _mgr(tmp_path) -> Any:
        from arrow_lake.ingest.storage import LanceStorageManager

        cfg = ArrowLakeConfig()
        cfg.storage.backend = StorageBackend.LOCAL
        return LanceStorageManager(base_uri=str(tmp_path), storage_config=cfg.storage)

    def test_write_lance_df_table_beside_plain_refused(self, tmp_path) -> None:
        mgr = self._mgr(tmp_path)
        mgr.create_dataset("gas", pa.table({"a": [1]}))  # plain single-table
        import daft  # noqa: F401 — ensure the write path import works
        with pytest.raises(StorageError, match="single-table dataset"):
            mgr.write_lance_from_dataframe(
                "gas", MagicMock(), mode="create", table="segments",
            )

    def test_write_lance_df_plain_beside_container_refused(self, tmp_path) -> None:
        mgr = self._mgr(tmp_path)
        mgr.create_dataset("gas", pa.table({"a": [1]}), table="segments")
        with pytest.raises(StorageError, match="exists as a container"):
            mgr.write_lance_from_dataframe("gas", MagicMock(), mode="create")

    def test_restore_dataset_table_beside_plain_refused(self, tmp_path) -> None:
        mgr = self._mgr(tmp_path)
        mgr.create_dataset("gas", pa.table({"a": [1]}))
        with pytest.raises(StorageError, match="Identity conflict"):
            mgr.restore_dataset("gas", pa.table({"a": [2]}), table="segments")

    def test_restore_dataset_plain_beside_container_refused(self, tmp_path) -> None:
        mgr = self._mgr(tmp_path)
        mgr.create_dataset("gas", pa.table({"a": [1]}), table="segments")
        with pytest.raises(StorageError, match="Identity conflict"):
            mgr.restore_dataset("gas", pa.table({"a": [2]}))

    def test_restore_dataset_same_shape_still_works(self, tmp_path) -> None:
        mgr = self._mgr(tmp_path)
        mgr.create_dataset("gas", pa.table({"a": [1]}), table="segments")
        mgr.restore_dataset("gas", pa.table({"a": [2]}), table="segments")
        assert mgr.read_dataset("gas", table="segments").column("a").to_pylist() == [2]
