"""W1.1 container storage — two-part addressing, zero-migration compat (DR14).

A dataset may act as a *container* holding N Lance tables at
``{base}/{ds}/{table}.lance``. Existing single-table datasets keep their
``{base}/{name}.lance`` layout untouched and every no-``table`` call path
behaves exactly as before (D2 zero physical migration).

Container identity is NOT sniffed from directories (D3): storage-level
guards are best-effort conflict detection; the authoritative registry is
the control-plane catalog store (W1.2).
"""

from __future__ import annotations

from pathlib import Path

import pyarrow as pa
import pytest

from arrow_lake.exceptions import StorageError
from arrow_lake.ingest.storage import LanceStorageManager

T3 = pa.table({"a": [1, 2, 3], "b": ["x", "y", "z"]})
T2 = pa.table({"a": [10, 20], "b": ["p", "q"]})


@pytest.fixture
def mgr(tmp_path: Path) -> LanceStorageManager:
    return LanceStorageManager(base_uri=str(tmp_path))


# --------------------------------------------------------------------------- #
# Container read/write path
# --------------------------------------------------------------------------- #
class TestContainerReadWrite:
    def test_create_and_read_two_tables(self, mgr: LanceStorageManager) -> None:
        mgr.create_dataset("gas_net", T3, table="segments")
        mgr.create_dataset("gas_net", T2, table="stations")

        seg = mgr.read_dataset("gas_net", table="segments")
        sta = mgr.read_dataset("gas_net", table="stations")
        assert seg.num_rows == 3
        assert sta.num_rows == 2

    def test_physical_layout_nested(self, mgr: LanceStorageManager, tmp_path: Path) -> None:
        mgr.create_dataset("gas_net", T3, table="segments")
        # D2: {base}/{ds}/{table}.lance — NOT {base}/{ds}.lance
        assert (tmp_path / "gas_net" / "segments.lance").is_dir()
        assert not (tmp_path / "gas_net.lance").exists()

    def test_append_grows_rows(self, mgr: LanceStorageManager) -> None:
        mgr.create_dataset("gas_net", T3, table="segments")
        mgr.append_dataset("gas_net", T3, table="segments")
        assert mgr.read_dataset("gas_net", table="segments").num_rows == 6

    def test_append_to_missing_table_raises(self, mgr: LanceStorageManager) -> None:
        with pytest.raises(StorageError):
            mgr.append_dataset("gas_net", T3, table="segments")

    def test_read_missing_table_raises_not_found(self, mgr: LanceStorageManager) -> None:
        mgr.create_dataset("gas_net", T3, table="segments")
        with pytest.raises(StorageError):
            mgr.read_dataset("gas_net", table="stations")

    def test_create_same_table_twice_conflicts(self, mgr: LanceStorageManager) -> None:
        mgr.create_dataset("gas_net", T3, table="segments")
        with pytest.raises(StorageError):
            mgr.create_dataset("gas_net", T3, table="segments")

    def test_invalid_table_name_rejected(self, mgr: LanceStorageManager) -> None:
        for bad in ("bad/name", "x y", "", "1no", "a" * 300):
            with pytest.raises(StorageError):
                mgr.create_dataset("gas_net", T3, table=bad)

    def test_table_param_validated_on_read_too(self, mgr: LanceStorageManager) -> None:
        mgr.create_dataset("gas_net", T3, table="segments")
        with pytest.raises(StorageError):
            mgr.read_dataset("gas_net", table="no/table")


# --------------------------------------------------------------------------- #
# Existence / listing / delete
# --------------------------------------------------------------------------- #
class TestContainerExistenceListing:
    def test_dataset_exists_table_semantics(self, mgr: LanceStorageManager) -> None:
        mgr.create_dataset("gas_net", T3, table="segments")
        assert mgr.dataset_exists("gas_net", table="segments") is True
        assert mgr.dataset_exists("gas_net", table="stations") is False
        # Container with tables is not a single-table dataset
        assert mgr.dataset_exists("gas_net") is False

    def test_list_container_tables(self, mgr: LanceStorageManager) -> None:
        mgr.create_dataset("gas_net", T3, table="segments")
        mgr.create_dataset("gas_net", T2, table="stations")
        assert mgr.list_container_tables("gas_net") == ["segments", "stations"]

    def test_container_tables_invisible_to_list_datasets(self, mgr: LanceStorageManager) -> None:
        mgr.create_dataset("plain_ds", T3)
        mgr.create_dataset("gas_net", T3, table="segments")
        assert mgr.list_datasets() == ["plain_ds"]

    def test_delete_single_table_keeps_siblings(self, mgr: LanceStorageManager) -> None:
        mgr.create_dataset("gas_net", T3, table="segments")
        mgr.create_dataset("gas_net", T2, table="stations")
        mgr.delete_dataset("gas_net", table="stations")
        assert mgr.dataset_exists("gas_net", table="stations") is False
        assert mgr.read_dataset("gas_net", table="segments").num_rows == 3

    def test_container_level_delete_drops_all_tables(self, mgr: LanceStorageManager) -> None:
        mgr.create_dataset("gas_net", T3, table="segments")
        mgr.create_dataset("gas_net", T2, table="stations")
        mgr.delete_dataset("gas_net")
        assert mgr.list_container_tables("gas_net") == []
        with pytest.raises(StorageError):
            mgr.read_dataset("gas_net", table="segments")


# --------------------------------------------------------------------------- #
# Zero-regression: single-table semantics unchanged (D2 hard acceptance)
# --------------------------------------------------------------------------- #
class TestSingleTableZeroRegression:
    def test_plain_dataset_lifecycle_unchanged(self, mgr: LanceStorageManager, tmp_path: Path) -> None:
        mgr.create_dataset("plain", T3)
        # Layout untouched: {base}/{name}.lance
        assert (tmp_path / "plain.lance").is_dir()
        assert mgr.dataset_exists("plain") is True
        mgr.append_dataset("plain", T2)
        assert mgr.read_dataset("plain").num_rows == 5
        assert mgr.list_datasets() == ["plain"]
        mgr.delete_dataset("plain")
        assert mgr.dataset_exists("plain") is False

    def test_identity_conflict_table_on_plain_dataset(self, mgr: LanceStorageManager) -> None:
        mgr.create_dataset("plain", T3)
        with pytest.raises(StorageError):
            mgr.create_dataset("plain", T2, table="segments")

    def test_identity_conflict_plain_on_container(self, mgr: LanceStorageManager) -> None:
        mgr.create_dataset("gas_net", T3, table="segments")
        with pytest.raises(StorageError):
            mgr.create_dataset("gas_net", T2)

    def test_upsert_dataset_with_table(self, mgr: LanceStorageManager) -> None:
        mgr.create_dataset("gas_net", T3, table="stations")
        mgr.upsert_dataset("gas_net", pa.table({"a": [2, 9], "b": ["y", "n"]}), on="a", table="stations")
        result = mgr.read_dataset("gas_net", table="stations")
        # merge on 'a': row a=2 updated in place, a=9 inserted, 1/3 untouched
        assert sorted(result.column("a").to_pylist()) == [1, 2, 3, 9]
        a2_b = result.filter(pa.compute.equal(result.column("a"), 2)).column("b").to_pylist()
        assert a2_b == ["y"]

    def test_dataset_uri_with_table(self, mgr: LanceStorageManager, tmp_path: Path) -> None:
        mgr.create_dataset("gas_net", T3, table="segments")
        uri = mgr.dataset_uri("gas_net", table="segments")
        assert uri == str(tmp_path / "gas_net" / "segments.lance")

    def test_schema_evolution_append_with_table(self, mgr: LanceStorageManager) -> None:
        mgr.create_dataset("gas_net", T3, table="segments")
        wider = T3.append_column("c", pa.array([True, False, True]))
        mgr.append_dataset("gas_net", wider, table="segments")
        assert "c" in mgr.read_dataset("gas_net", table="segments").column_names
