"""Tests for dataset lifecycle — Story 2.8.

Tests CatalogActor archive/restore/cascade-delete:
- archive_dataset hides from default list
- restore_dataset reappears in list
- delete_dataset with cascade removes Lance data + catalog entry
- Double archive raises error
"""

from __future__ import annotations

import contextlib
from pathlib import Path

import pyarrow as pa
import pytest
import ray
from arrow_lake.catalog.actor import CatalogActor
from arrow_lake.ingest.storage import LanceStorageManager


@pytest.fixture(scope="module")
def ray_init() -> None:
    if not ray.is_initialized():
        ray.init(num_cpus=2, ignore_reinit_error=True)
    yield
    if ray.is_initialized():
        ray.shutdown()


@pytest.fixture(scope="module")
def catalog_handle(ray_init: None):
    handle = CatalogActor.options(name="lifecycle_catalog").remote()
    ray.get(handle.ping.remote())
    yield handle
    with contextlib.suppress(Exception):
        ray.kill(handle)


class TestDatasetLifecycle:
    """Test dataset archive/restore/cascade-delete."""

    def test_archive_dataset(self, tmp_path: Path, catalog_handle) -> None:
        """Archived dataset is hidden from default list."""
        manager = LanceStorageManager(base_uri=str(tmp_path))
        manager.create_dataset("lc_test", pa.table({"x": [1]}))
        ray.get(catalog_handle.register_table.remote("lc_test", '{"x":"int64"}', str(tmp_path)))

        ray.get(catalog_handle.archive_dataset.remote("lc_test"))

        tables = ray.get(catalog_handle.list_tables.remote())
        names = [t["name"] for t in tables]
        assert "lc_test" not in names

    def test_restore_dataset(self, tmp_path: Path, catalog_handle) -> None:
        """Restored dataset reappears in list."""
        manager = LanceStorageManager(base_uri=str(tmp_path))
        manager.create_dataset("lc_restore", pa.table({"x": [1]}))
        ray.get(catalog_handle.register_table.remote("lc_restore", '{"x":"int64"}', str(tmp_path)))

        ray.get(catalog_handle.archive_dataset.remote("lc_restore"))
        tables = ray.get(catalog_handle.list_tables.remote())
        names = [t["name"] for t in tables]
        assert "lc_restore" not in names

        ray.get(catalog_handle.restore_dataset.remote("lc_restore"))
        tables = ray.get(catalog_handle.list_tables.remote())
        names = [t["name"] for t in tables]
        assert "lc_restore" in names

    def test_delete_dataset_cascade(self, tmp_path: Path, catalog_handle) -> None:
        """Cascade delete removes catalog entry + Lance data."""
        manager = LanceStorageManager(base_uri=str(tmp_path))
        manager.create_dataset("lc_cascade", pa.table({"x": [1]}))
        ray.get(catalog_handle.register_table.remote("lc_cascade", '{"x":"int64"}', str(tmp_path)))

        ray.get(
            catalog_handle.delete_table.remote("lc_cascade", cascade=True, base_uri=str(tmp_path))
        )

        assert not manager.dataset_exists("lc_cascade")

        with pytest.raises(ray.exceptions.UnserializableException):
            ray.get(catalog_handle.get_table.remote("lc_cascade"))

    def test_double_archive_raises(self, tmp_path: Path, catalog_handle) -> None:
        """Archiving an already-archived dataset raises error."""
        manager = LanceStorageManager(base_uri=str(tmp_path))
        manager.create_dataset("lc_dbl", pa.table({"x": [1]}))
        ray.get(catalog_handle.register_table.remote("lc_dbl", '{"x":"int64"}', str(tmp_path)))

        ray.get(catalog_handle.archive_dataset.remote("lc_dbl"))

        with pytest.raises(ray.exceptions.UnserializableException):
            ray.get(catalog_handle.archive_dataset.remote("lc_dbl"))
