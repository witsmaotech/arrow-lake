"""P2-1 (review 2026-08-26 §三): container-table delete reclaims the
container_registry row — the registry was write-only before, so a dropped
table stayed declared forever (permanent drift between the control plane
and storage). The facade now takes ``table=`` and syncs
``CatalogStore.drop_container_table`` best-effort after the storage drop.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("lance", reason="lance not installed")
from arrow_lake import Lake
from arrow_lake.config import ArrowLakeConfig, StorageBackend


@pytest.fixture
def lake(tmp_path: Path) -> Lake:
    # Hermetic: force LOCAL (repo-root .env pins minio otherwise — the
    # "test isolation pollution" family; see test_container_ingest_facade).
    config = ArrowLakeConfig()
    config.storage.backend = StorageBackend.LOCAL
    return Lake(base_uri=str(tmp_path / "lance_data"), config=config)


def _csv(path: Path, rows: list[str]) -> str:
    p = path / f"del_{len(list(path.iterdir()))}_data.csv"
    p.write_text("\n".join(rows) + "\n")
    return str(p)


class TestFacadeContainerTableDelete:
    def _setup(self, lake: Lake, tmp_path: Path) -> None:
        lake.ingest("gas_net", [_csv(tmp_path, ["seg_id", "G1"])], table="segments")
        lake.ingest("gas_net", [_csv(tmp_path, ["sta_id", "S1"])], table="stations")

    def _store(self, lake: Lake):
        from arrow_lake.system_db import Migrator, SystemDB
        from arrow_lake.system_db.stores import CatalogStore

        db = SystemDB(":memory:")
        Migrator(db).run()
        lake._catalog_store = CatalogStore(db)
        return db

    def test_table_delete_drops_storage_and_registry_row(
        self, lake: Lake, tmp_path: Path,
    ) -> None:
        db = self._store(lake)  # store must exist BEFORE ingest (registers rows)
        try:
            self._setup(lake, tmp_path)
            assert lake._catalog_store.get_container("gas_net")["tables"] == [
                "segments", "stations",
            ]
            lake.delete_dataset("gas_net", table="stations")
            # storage: table gone, sibling intact
            assert lake._get_storage().dataset_exists("gas_net", table="stations") is False
            assert lake._get_storage().read_dataset("gas_net", table="segments").num_rows == 1
            # registry: row reclaimed (no permanent drift)
            assert lake._catalog_store.get_container("gas_net")["tables"] == ["segments"]
        finally:
            db.close()

    def test_table_delete_without_store_still_works(
        self, lake: Lake, tmp_path: Path,
    ) -> None:
        self._setup(lake, tmp_path)
        # host/CLI Lake: no lifespan-injected store — no registration, no crash
        lake.delete_dataset("gas_net", table="stations")
        assert lake._get_storage().dataset_exists("gas_net", table="stations") is False
        assert lake._get_storage().dataset_exists("gas_net", table="segments") is True

    def test_table_delete_missing_table_raises_not_found(
        self, lake: Lake, tmp_path: Path,
    ) -> None:
        self._setup(lake, tmp_path)
        with pytest.raises(Exception):
            lake.delete_dataset("gas_net", table="nope")

    def test_whole_dataset_delete_unregisters_container(
        self, lake: Lake, tmp_path: Path,
    ) -> None:
        from arrow_lake.system_db import Migrator, SystemDB
        from arrow_lake.system_db.stores import CatalogStore

        db = SystemDB(":memory:")
        Migrator(db).run()
        lake._catalog_store = CatalogStore(db)
        try:
            self._setup(lake, tmp_path)
            assert lake._catalog_store.is_container("gas_net") is True
            lake.delete_dataset("gas_net")
            assert lake._catalog_store.is_container("gas_net") is False
        finally:
            db.close()
