"""W1.3 facade-level container ingest — full threading lake.ingest(table=…).

Covers the DR14 W1 acceptance path: 建容器集 + 多表摄入 + 按表读 through the
real facade (quality-gated Ingestor → storage two-part addressing), plus the
control-plane registration hook when a catalog store is attached.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("lance", reason="lance not installed")
from arrow_lake import Lake
from arrow_lake.config import ArrowLakeConfig, StorageBackend


@pytest.fixture
def lake(tmp_path: Path) -> Lake:
    # Hermetic: the repo-root .env pins STORAGE__BACKEND=minio, which pydantic
    # loads and which makes Lake IGNORE base_uri entirely (writes go to the
    # shared dev bucket — the "test isolation pollution" family). Force LOCAL.
    config = ArrowLakeConfig()
    config.storage.backend = StorageBackend.LOCAL
    return Lake(base_uri=str(tmp_path / "lance_data"), config=config)


def _csv(path: Path, rows: list[str]) -> str:
    p = path / f"{len(list(path.iterdir()))}_data.csv"
    p.write_text("\n".join(rows) + "\n")
    return str(p)


class TestFacadeContainerIngest:
    def test_ingest_two_tables_and_read_back(self, lake: Lake, tmp_path: Path) -> None:
        seg_csv = _csv(tmp_path, ["seg_id,pressure", "G1,120", "G2,300"])
        sta_csv = _csv(tmp_path, ["sta_id,name", "S1,east", "S2,west"])

        lake.ingest("gas_net", [seg_csv], table="segments")
        lake.ingest("gas_net", [sta_csv], table="stations")

        seg = lake._get_storage().read_dataset("gas_net", table="segments")
        sta = lake._get_storage().read_dataset("gas_net", table="stations")
        assert seg.num_rows == 2 and "seg_id" in seg.column_names
        assert sta.num_rows == 2 and "sta_id" in sta.column_names
        # container layout, invisible to plain dataset listing
        assert lake._get_storage().list_container_tables("gas_net") == ["segments", "stations"]
        assert "gas_net" not in lake._get_storage().list_datasets()

    def test_ingest_append_second_batch_to_same_table(self, lake: Lake, tmp_path: Path) -> None:
        first = _csv(tmp_path, ["seg_id,pressure", "G1,120"])
        second = _csv(tmp_path, ["seg_id,pressure", "G2,300"])
        lake.ingest("gas_net", [first], table="segments")
        lake.ingest("gas_net", [second], table="segments")
        assert lake._get_storage().read_dataset("gas_net", table="segments").num_rows == 2

    def test_plain_ingest_unchanged(self, lake: Lake, tmp_path: Path) -> None:
        csv = _csv(tmp_path, ["a,b", "1,x"])
        lake.ingest("plain_ds", [csv])
        assert lake._get_storage().read_dataset("plain_ds").num_rows == 1
        # lineage side-table (_lineage_events) also lands in base_uri — member
        # check, not equality.
        assert "plain_ds" in lake._get_storage().list_datasets()
        assert lake._get_storage().list_container_tables("plain_ds") == []

    def test_registration_hook_with_catalog_store(self, lake: Lake, tmp_path: Path) -> None:
        from arrow_lake.system_db import Migrator, SystemDB
        from arrow_lake.system_db.stores import CatalogStore

        db = SystemDB(":memory:")
        Migrator(db).run()
        lake._catalog_store = CatalogStore(db)
        try:
            csv = _csv(tmp_path, ["seg_id", "G1"])
            lake.ingest("gas_net", [csv], table="segments")
            assert lake._catalog_store.is_container("gas_net") is True
            assert lake._catalog_store.get_container("gas_net")["tables"] == ["segments"]
        finally:
            db.close()

    def test_registration_skipped_without_store(self, lake: Lake, tmp_path: Path) -> None:
        # host/CLI Lake: no lifespan injection — ingest still works, no registration
        csv = _csv(tmp_path, ["seg_id", "G1"])
        lake.ingest("gas_net", [csv], table="segments")
        assert lake._get_storage().dataset_exists("gas_net", table="segments") is True
