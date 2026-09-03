"""Tests for Lake facade new methods — DARMU integration (unit).

Tests:
- Lake.ingest() delegation to Ingestor
- Lake.catalog() listing
- Lake.list_datasets() / delete_dataset() delegation
- Lake.from_yaml() config loading
- Lake.daft_query() lazy frame return
- Lake.list_flows() / get_flow_info() workflow discovery
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pyarrow as pa
import pytest

pytest.importorskip("daft", reason="daft not installed")
pytest.importorskip("lance", reason="lance not installed")
from arrow_lake import CatalogEntry, CatalogResult, Lake


@pytest.fixture()
def lake(tmp_path: Path) -> Lake:
    from arrow_lake.config import ArrowLakeConfig, StorageBackend, StorageConfig

    cfg = ArrowLakeConfig()
    cfg.storage = StorageConfig(base_uri=str(tmp_path / "lance_data"), backend=StorageBackend.LOCAL)
    return Lake(base_uri=str(tmp_path / "lance_data"), config=cfg)


class TestLakeIngestDelegation:
    """Test that Lake.ingest* methods delegate to Ingestor."""

    def test_ingest_delegates_to_ingestor(self, lake: Lake) -> None:
        mock_report = MagicMock()
        mock_report.total_rows = 5
        with patch("arrow_lake.ingest.ingestor.Ingestor") as mock_ingestor:
            mock_ingestor.return_value.ingest.return_value = mock_report
            result = lake.ingest("test_ds", ["/tmp/file.csv"])
            assert result is mock_report
            mock_ingestor.return_value.ingest.assert_called_once_with("test_ds", ["/tmp/file.csv"], transforms=None, target_table=None)

    def test_ingest_http_delegates(self, lake: Lake) -> None:
        mock_report = MagicMock()
        with patch("arrow_lake.ingest.ingestor.Ingestor") as mock_ingestor:
            mock_ingestor.return_value.ingest_http.return_value = mock_report
            result = lake.ingest_http("test_ds", ["http://example.com/data.json"])
            assert result is mock_report
            mock_ingestor.return_value.ingest_http.assert_called_once()

    def test_ingest_images_delegates(self, lake: Lake) -> None:
        mock_report = MagicMock()
        with patch("arrow_lake.ingest.ingestor.Ingestor") as mock_ingestor:
            mock_ingestor.return_value.ingest_images.return_value = mock_report
            result = lake.ingest_images("test_ds", ["/tmp/img.png"])
            assert result is mock_report
            mock_ingestor.return_value.ingest_images.assert_called_once()

    def test_ingest_videos_delegates(self, lake: Lake) -> None:
        mock_report = MagicMock()
        with patch("arrow_lake.ingest.ingestor.Ingestor") as mock_ingestor:
            mock_ingestor.return_value.ingest_videos.return_value = mock_report
            result = lake.ingest_videos("test_ds", ["/tmp/vid.mp4"])
            assert result is mock_report
            mock_ingestor.return_value.ingest_videos.assert_called_once()

    def test_ingest_mixed_delegates(self, lake: Lake) -> None:
        mock_report = MagicMock()
        with patch("arrow_lake.ingest.ingestor.Ingestor") as mock_ingestor:
            mock_ingestor.return_value.ingest_mixed.return_value = mock_report
            sources = {"files": ["/tmp/data.csv"]}
            result = lake.ingest_mixed("test_ds", sources)
            assert result is mock_report
            mock_ingestor.return_value.ingest_mixed.assert_called_once_with("test_ds", sources)


class TestLakeCatalog:
    """Test Lake.catalog() returns dataset metadata."""

    def test_catalog_empty(self, lake: Lake) -> None:
        result = lake.catalog()
        assert isinstance(result, CatalogResult)
        assert result.total == 0
        assert result.datasets == []

    def test_catalog_returns_datasets(self, lake: Lake, tmp_path: Path) -> None:
        from arrow_lake.ingest.storage import LanceStorageManager

        storage = LanceStorageManager(str(tmp_path / "lance_data"))
        table = pa.table({"a": [1, 2], "b": ["x", "y"]})
        storage.create_dataset("ds1", table)
        storage.create_dataset("ds2", table)

        result = lake.catalog()
        assert isinstance(result, CatalogResult)
        assert result.total == 2
        names = [e.name for e in result.datasets]
        assert "ds1" in names
        assert "ds2" in names


class TestLakeListDeleteDatasets:
    """Test Lake.list_datasets() and delete_dataset() pass-through."""

    def test_list_datasets_empty(self, lake: Lake) -> None:
        result = lake.list_datasets()
        assert result == []

    def test_list_datasets_returns_names(self, lake: Lake, tmp_path: Path) -> None:
        from arrow_lake.ingest.storage import LanceStorageManager

        storage = LanceStorageManager(str(tmp_path / "lance_data"))
        table = pa.table({"a": [1]})
        storage.create_dataset("alpha", table)
        storage.create_dataset("beta", table)

        result = lake.list_datasets()
        assert "alpha" in result
        assert "beta" in result

    def test_delete_dataset_delegates(self, lake: Lake, tmp_path: Path) -> None:
        from arrow_lake.ingest.storage import LanceStorageManager

        storage = LanceStorageManager(str(tmp_path / "lance_data"))
        table = pa.table({"a": [1]})
        storage.create_dataset("to_delete", table)

        lake.delete_dataset("to_delete")
        assert "to_delete" not in lake.list_datasets()


class TestLakeDeleteCascade:
    """Lake.delete_dataset(cascade=True) reclaims derived assets — KA dump dir,
    libSQL catalog row, RBAC grants/denies, and extraction-template bindings.
    KG-graph and Gravitino paths are no-ops here (disabled in the unit config)."""

    def _wire_stores(self, lake: Lake) -> None:
        from arrow_lake.system_db import Migrator, SystemDB
        from arrow_lake.system_db.stores.catalog import CatalogStore
        from arrow_lake.system_db.stores.extraction_templates import (
            ExtractionTemplateStore,
        )
        from arrow_lake.system_db.stores.rbac import RbacStore

        db = SystemDB(":memory:")
        Migrator(db).run()
        lake._catalog_store = CatalogStore(db)
        lake._rbac_store = RbacStore(db, cache_ttl=0)
        lake._extraction_template_store = ExtractionTemplateStore(db)

    def _seed_ka(self, ka_base: str, ds: str) -> Path:
        from arrow_lake.knowledge_graph._naming import artifact_key_for

        ka_root = Path(ka_base) / artifact_key_for(ds)
        (ka_root / "ka").mkdir(parents=True)
        (ka_root / "ka" / "data.json").write_text("{}")
        return ka_root

    def test_delete_dataset_cascades_derived_assets(
        self, lake: Lake, tmp_path: Path
    ) -> None:
        from arrow_lake.ingest.storage import LanceStorageManager

        self._wire_stores(lake)
        lake._config.hugegraph.he_ka_base_dir = str(tmp_path / "ka")
        ds = "cascade_ds"

        # populate derived assets
        LanceStorageManager(str(tmp_path / "lance_data")).create_dataset(
            ds, pa.table({"a": [1]})
        )
        ka_root = self._seed_ka(lake._config.hugegraph.he_ka_base_dir, ds)
        lake._rbac_store.grant_dataset_access(ds, "editor", "read")
        lake._extraction_template_store.set_binding(ds, "project_concept_graph")
        lake._catalog_store.register_table(ds, '{"a":"int64"}', "loc")

        # sanity: assets exist
        assert ka_root.exists()
        assert lake._rbac_store.get_dataset_grants(ds) == {"editor": {"read"}}
        assert lake._extraction_template_store.get_binding(ds) == "project_concept_graph"

        lake.delete_dataset(ds, cascade=True)

        assert ds not in lake.list_datasets()  # Lance table dropped
        assert not ka_root.exists()  # KA dump reclaimed
        assert lake._rbac_store.get_dataset_grants(ds) == {}  # RBAC purged
        assert lake._extraction_template_store.get_binding(ds) is None  # binding cleared
        assert lake._catalog_store.delete_table(ds) is False  # catalog row already gone

    def test_cascade_false_keeps_derived_assets(
        self, lake: Lake, tmp_path: Path
    ) -> None:
        """cascade=False = table-only; KG/KA/metadata/ACL preserved for reuse."""
        from arrow_lake.ingest.storage import LanceStorageManager

        self._wire_stores(lake)
        lake._config.hugegraph.he_ka_base_dir = str(tmp_path / "ka")
        ds = "keep_ds"
        LanceStorageManager(str(tmp_path / "lance_data")).create_dataset(
            ds, pa.table({"a": [1]})
        )
        ka_root = self._seed_ka(lake._config.hugegraph.he_ka_base_dir, ds)
        lake._rbac_store.grant_dataset_access(ds, "editor", "read")

        lake.delete_dataset(ds, cascade=False)

        assert ds not in lake.list_datasets()  # table dropped
        assert ka_root.exists()  # KA dump preserved
        assert lake._rbac_store.get_dataset_grants(ds) == {"editor": {"read"}}  # ACL kept

    def test_cascade_best_effort_resilience(
        self, lake: Lake, tmp_path: Path
    ) -> None:
        """A failing subsystem never blocks deletion or the other cleanups."""
        from arrow_lake.ingest.storage import LanceStorageManager

        self._wire_stores(lake)
        lake._config.hugegraph.he_ka_base_dir = str(tmp_path / "ka")
        ds = "resilient_ds"
        LanceStorageManager(str(tmp_path / "lance_data")).create_dataset(
            ds, pa.table({"a": [1]})
        )
        ka_root = self._seed_ka(lake._config.hugegraph.he_ka_base_dir, ds)
        # RBAC purge blows up — must not abort the rest of the cascade
        bad_rbac = MagicMock()
        bad_rbac.purge_dataset.side_effect = RuntimeError("db down")
        lake._rbac_store = bad_rbac

        lake.delete_dataset(ds, cascade=True)  # must not raise

        assert ds not in lake.list_datasets()  # core delete succeeded
        assert not ka_root.exists()  # KA cleanup still ran (independent try/except)
        bad_rbac.purge_dataset.assert_called_once_with(ds)


class TestLakeFromYaml:
    """Test Lake.from_yaml() class method."""

    def test_from_yaml_creates_lake(self, tmp_path: Path) -> None:
        config_file = tmp_path / "test_config.yaml"
        config_file.write_text("olap:\n  max_result_rows: 500\nquality:\n  enabled: false\n")
        lake = Lake.from_yaml(str(config_file))
        assert lake is not None
        assert lake._config.olap.max_result_rows == 500
        assert lake._config.quality.enabled is False

    def test_from_yaml_with_base_uri(self, tmp_path: Path) -> None:
        config_file = tmp_path / "test_config.yaml"
        config_file.write_text("olap:\n  max_result_rows: 100\n")
        lake = Lake.from_yaml(str(config_file), base_uri="/custom/path")
        assert lake._base_uri == "/custom/path"

    def test_from_yaml_default_base_uri(self, tmp_path: Path) -> None:
        config_file = tmp_path / "test_config.yaml"
        config_file.write_text("olap:\n  max_result_rows: 100\n")
        lake = Lake.from_yaml(str(config_file))
        assert lake._base_uri == "./data"


class TestLakeDaftQuery:
    """Test Lake.daft_query() returns lazy frame."""

    def test_daft_query_returns_lazy_frame(self, lake: Lake, tmp_path: Path) -> None:
        from arrow_lake.ingest.storage import LanceStorageManager
        from arrow_lake.query.daft_api import LazyDaftFrame

        storage = LanceStorageManager(str(tmp_path / "lance_data"))
        table = pa.table({"a": [1, 2, 3], "b": ["x", "y", "z"]})
        storage.create_dataset("daft_ds", table)

        frame = lake.daft_query("daft_ds")
        assert isinstance(frame, LazyDaftFrame)

    def test_daft_query_with_columns(self, lake: Lake, tmp_path: Path) -> None:
        from arrow_lake.ingest.storage import LanceStorageManager
        from arrow_lake.query.daft_api import LazyDaftFrame

        storage = LanceStorageManager(str(tmp_path / "lance_data"))
        table = pa.table({"a": [1, 2], "b": ["x", "y"], "c": [3.0, 4.0]})
        storage.create_dataset("daft_ds", table)

        frame = lake.daft_query("daft_ds", columns=["a", "b"])
        assert isinstance(frame, LazyDaftFrame)

    def test_daft_query_invalid_name_raises(self, lake: Lake) -> None:
        with pytest.raises(ValueError, match="Invalid"):
            lake.daft_query("invalid;name")


class TestLakeFlows:
    """Test Lake.list_flows() and get_flow_info()."""

    def test_list_flows_returns_registered(self, lake: Lake) -> None:
        result = lake.list_flows()
        assert isinstance(result, list)
        assert "quality_pipeline" in result

    def test_get_flow_info_returns_metadata(self, lake: Lake) -> None:
        info = lake.get_flow_info("quality_pipeline")
        assert info["name"] == "quality_pipeline"
        assert "class" in info
        assert "module" in info
        assert info["class"] == "QualityPipelineFlow"

    def test_get_flow_info_not_found_raises(self, lake: Lake) -> None:
        from arrow_lake import WorkflowError

        with pytest.raises(WorkflowError, match="nonexistent_flow"):
            lake.get_flow_info("nonexistent_flow")

    def test_list_flows_idempotent(self, lake: Lake) -> None:
        """Calling list_flows twice doesn't duplicate registrations."""
        first = lake.list_flows()
        second = lake.list_flows()
        assert first == second


class TestCatalogEntryDTO:
    """Test CatalogEntry and CatalogResult dataclasses."""

    def test_catalog_entry_frozen(self) -> None:
        entry = CatalogEntry(name="test", version=1, num_rows=100)
        with pytest.raises(AttributeError):
            entry.name = "changed"  # type: ignore[misc]

    def test_catalog_result_frozen(self) -> None:
        result = CatalogResult(datasets=[], total=0)
        with pytest.raises(AttributeError):
            result.total = 99  # type: ignore[misc]


class TestLakeCreateDataset:
    """Test Lake.create_dataset() public method."""

    def test_create_dataset_writes_table(self, lake: Lake) -> None:
        table = pa.table({"id": ["1", "2"], "val": [10, 20]})
        lake.create_dataset("test_create", table)
        assert "test_create" in lake.list_datasets()
        result = lake.catalog()
        entry = next(e for e in result.datasets if e.name == "test_create")
        assert entry.num_rows == 2

    def test_create_dataset_rejects_non_table(self, lake: Lake) -> None:
        from arrow_lake.exceptions import ValidationError
        with pytest.raises(ValidationError, match="pyarrow.Table"):
            lake.create_dataset("bad", [1, 2, 3])  # type: ignore

    def test_create_dataset_rejects_existing(self, lake: Lake) -> None:
        table = pa.table({"a": [1]})
        lake.create_dataset("dup", table)
        with pytest.raises(Exception):
            lake.create_dataset("dup", table)

    def test_create_dataset_validates_name(self, lake: Lake) -> None:
        table = pa.table({"a": [1]})
        with pytest.raises(Exception):
            lake.create_dataset("bad;name", table)


class TestLakeAppendDataset:
    """Test Lake.append_dataset() public method."""

    def test_append_dataset_adds_rows(self, lake: Lake) -> None:
        table = pa.table({"id": ["1"], "val": [10]})
        lake.create_dataset("append_test", table)
        more = pa.table({"id": ["2"], "val": [20]})
        lake.append_dataset("append_test", more)
        result = lake.catalog()
        entry = next(e for e in result.datasets if e.name == "append_test")
        assert entry.num_rows == 2

    def test_append_dataset_rejects_non_table(self, lake: Lake) -> None:
        from arrow_lake.exceptions import ValidationError
        with pytest.raises(ValidationError):
            lake.append_dataset("missing", "not a table")  # type: ignore

    def test_append_dataset_rejects_missing_dataset(self, lake: Lake) -> None:
        table = pa.table({"a": [1]})
        with pytest.raises(Exception):
            lake.append_dataset("nonexistent", table)
