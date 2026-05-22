"""Tests for Gravitino integration — all tests use mocked SDK (no server needed)."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any
from unittest.mock import MagicMock, patch

import pyarrow as pa
import pytest

from arrow_lake.config.gravitino import GravitinoConfig


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def gravitino_config() -> GravitinoConfig:
    return GravitinoConfig(
        enabled=True,
        uri="http://localhost:8090",
        metalake="test-metalake",
    )


@pytest.fixture
def disabled_config() -> GravitinoConfig:
    return GravitinoConfig(enabled=False)


# ---------------------------------------------------------------------------
# GravitinoConfig
# ---------------------------------------------------------------------------

class TestGravitinoConfig:
    def test_defaults(self) -> None:
        cfg = GravitinoConfig()
        assert cfg.enabled is False
        assert cfg.uri == "http://gravitino:8090"
        assert cfg.metalake == "arrow_lake"

    def test_custom_values(self) -> None:
        cfg = GravitinoConfig(
            enabled=True,
            uri="http://my-gravitino:9090",
            metalake="my-lake",
        )
        assert cfg.enabled is True
        assert cfg.uri == "http://my-gravitino:9090"
        assert cfg.metalake == "my-lake"

    def test_sync_interval_bounds(self) -> None:
        cfg = GravitinoConfig(sync_interval_seconds=5)
        assert cfg.sync_interval_seconds == 5
        cfg = GravitinoConfig(sync_interval_seconds=300)
        assert cfg.sync_interval_seconds == 300
        with pytest.raises(Exception):
            GravitinoConfig(sync_interval_seconds=1)


# ---------------------------------------------------------------------------
# GravitinoBridge
# ---------------------------------------------------------------------------

class TestGravitinoBridge:
    def test_disabled_config_bridge_still_has_health(self, disabled_config: GravitinoConfig) -> None:
        from arrow_lake.catalog.gravitino_bridge import GravitinoBridge

        bridge = GravitinoBridge(disabled_config)
        assert bridge.enabled is True
        assert bridge.sync_outbound([]) == 0
        assert bridge.sync_inbound() == []
        assert bridge.get_table_statistics("t") is None

    @patch("arrow_lake.catalog.gravitino_bridge.GravitinoBridge._request")
    def test_register_dataset(self, mock_request: MagicMock, gravitino_config: GravitinoConfig) -> None:
        from arrow_lake.catalog.gravitino_bridge import GravitinoBridge

        mock_request.return_value = {"code": 0, "table": {"name": "test_table"}}
        bridge = GravitinoBridge(gravitino_config)
        schema = pa.schema([pa.field("id", pa.int64()), pa.field("name", pa.string())])
        bridge.register_dataset("test_table", schema, "/data/test.lance")

    @patch("arrow_lake.catalog.gravitino_bridge.GravitinoBridge._request")
    def test_deregister_dataset(self, mock_request: MagicMock, gravitino_config: GravitinoConfig) -> None:
        from arrow_lake.catalog.gravitino_bridge import GravitinoBridge

        mock_request.return_value = {"code": 0}
        bridge = GravitinoBridge(gravitino_config)
        bridge.deregister_dataset("test_table")

    @patch("arrow_lake.catalog.gravitino_bridge.GravitinoBridge._request")
    def test_sync_outbound(self, mock_request: MagicMock, gravitino_config: GravitinoConfig) -> None:
        from arrow_lake.catalog.gravitino_bridge import GravitinoBridge

        mock_request.return_value = {"code": 0, "table": {"name": "t"}}
        bridge = GravitinoBridge(gravitino_config)
        entries = [
            {"name": "t1", "schema_json": json.dumps([{"name": "id", "type": "int64"}])},
            {"name": "t2", "schema_json": json.dumps([{"name": "name", "type": "string"}])},
        ]
        count = bridge.sync_outbound(entries)
        assert count == 2

    @patch("arrow_lake.catalog.gravitino_bridge.GravitinoBridge._request")
    def test_sync_inbound(self, mock_request: MagicMock, gravitino_config: GravitinoConfig) -> None:
        from arrow_lake.catalog.gravitino_bridge import GravitinoBridge

        mock_request.return_value = {
            "code": 0,
            "identifiers": [{"name": "t1", "namespace": ["arrow_lake", "lance-catalog", "arrow_lake"]}],
        }
        bridge = GravitinoBridge(gravitino_config)
        entries = bridge.sync_inbound()
        assert len(entries) == 1
        assert entries[0]["name"] == "t1"


# ---------------------------------------------------------------------------
# GravitinoTagService
# ---------------------------------------------------------------------------

class TestGravitinoTagService:
    def test_disabled_config_is_noop(self, disabled_config: GravitinoConfig) -> None:
        from arrow_lake.quality.gravitino_tags import GravitinoTagService

        svc = GravitinoTagService(disabled_config)
        svc.create_tag("test", "comment")
        svc.tag_table("t", ["pii"])
        svc.tag_column("t", "c", ["sensitive"])
        assert svc.list_tags("t") == []
        assert svc.get_tables_by_tag("pii") == []

    @patch("arrow_lake.quality.gravitino_tags.GravitinoTagService._init_client")
    def test_create_tag(self, mock_init: MagicMock, gravitino_config: GravitinoConfig) -> None:
        from arrow_lake.quality.gravitino_tags import GravitinoTagService

        svc = GravitinoTagService(gravitino_config)
        mock_metalake = MagicMock()
        svc._client = MagicMock()
        svc._client.load_metalake.return_value = mock_metalake

        svc.create_tag("pii", "Personal Identifiable Information")
        mock_metalake.create_tag.assert_called_once_with(
            name="pii", comment="Personal Identifiable Information"
        )

    @patch("arrow_lake.quality.gravitino_tags.GravitinoTagService._init_client")
    def test_list_tags(self, mock_init: MagicMock, gravitino_config: GravitinoConfig) -> None:
        from arrow_lake.quality.gravitino_tags import GravitinoTagService

        svc = GravitinoTagService(gravitino_config)
        mock_metalake = MagicMock()
        svc._client = MagicMock()
        svc._client.load_metalake.return_value = mock_metalake
        mock_tag = MagicMock()
        mock_tag.name.return_value = "sensitive"
        mock_table = MagicMock()
        mock_table.supports_tags.return_value.list_tags.return_value = [mock_tag]
        mock_catalog = MagicMock()
        mock_catalog.as_table_catalog().load_table.return_value = mock_table
        svc._client.load_catalog.return_value = mock_catalog

        tags = svc.list_tags("test_table")
        assert tags == ["sensitive"]

    def test_tag_constants(self) -> None:
        from arrow_lake.quality.gravitino_tags import GravitinoTagService

        assert GravitinoTagService.SENSITIVE == "sensitive"
        assert GravitinoTagService.PII == "pii"
        assert GravitinoTagService.FINANCIAL == "financial"
        assert GravitinoTagService.EXPIRES_30D == "expires:30d"


# ---------------------------------------------------------------------------
# GravitinoRBACBridge
# ---------------------------------------------------------------------------

class TestGravitinoRBACBridge:
    def test_returns_none_without_sdk(self) -> None:
        from arrow_lake.api.rbac import GravitinoRBACBridge

        bridge = GravitinoRBACBridge(uri="http://localhost:8090", metalake="test")
        with patch("arrow_lake.api.rbac.GravitinoRBACBridge._ensure_client", return_value=False):
            bridge._initialized = False
            result = bridge.check_permission("user", "table", "read")
            assert result is None

    def test_action_mapping(self) -> None:
        from arrow_lake.api.rbac import GravitinoRBACBridge

        mapping = GravitinoRBACBridge._ACTION_TO_PRIVILEGE
        assert mapping["read"] == "SELECT_TABLE"
        assert mapping["write"] == "INSERT_TABLE"
        assert mapping["create"] == "CREATE_TABLE"
        assert mapping["delete"] == "DELETE_TABLE"
        assert mapping["admin"] == "CREATE_CATALOG"

    def test_unknown_action_returns_none(self) -> None:
        from arrow_lake.api.rbac import GravitinoRBACBridge

        bridge = GravitinoRBACBridge(uri="http://localhost:8090", metalake="test")
        mock_client = MagicMock()
        mock_client.load_metalake.return_value = MagicMock()
        bridge._client = mock_client
        bridge._initialized = True

        result = bridge.check_permission("user", "table", "unknown_action")
        assert result is None


# ---------------------------------------------------------------------------
# GravitinoModelRegistry
# ---------------------------------------------------------------------------

class TestGravitinoModelRegistry:
    def test_disabled_config_returns_empty(self, disabled_config: GravitinoConfig) -> None:
        from arrow_lake.catalog.gravitino_models import GravitinoModelRegistry

        reg = GravitinoModelRegistry(disabled_config)
        assert reg.list_models() == []
        assert reg.get_latest_version("model") is None
        assert reg.get_production_version("model") is None

    @patch("arrow_lake.catalog.gravitino_models.GravitinoModelRegistry._init_client")
    def test_list_models(self, mock_init: MagicMock, gravitino_config: GravitinoConfig) -> None:
        from arrow_lake.catalog.gravitino_models import GravitinoModelRegistry

        reg = GravitinoModelRegistry(gravitino_config)
        mock_catalog = MagicMock()
        mock_catalog.as_model_catalog().list_models.return_value = ["model_a", "model_b"]
        mock_client = MagicMock()
        mock_client.load_metalake.return_value = MagicMock()
        mock_client.load_catalog.return_value = mock_catalog
        reg._client = mock_client

        models = reg.list_models()
        assert models == ["model_a", "model_b"]

    @patch("arrow_lake.catalog.gravitino_models.GravitinoModelRegistry._init_client")
    def test_register_model(self, mock_init: MagicMock, gravitino_config: GravitinoConfig) -> None:
        from arrow_lake.catalog.gravitino_models import GravitinoModelRegistry

        reg = GravitinoModelRegistry(gravitino_config)
        mock_client = MagicMock()
        mock_client.load_metalake.return_value = MagicMock()
        reg._client = mock_client

        reg.register_model("test_model", comment="Test", properties={"framework": "sklearn"})

    @patch("arrow_lake.catalog.gravitino_models.GravitinoModelRegistry._init_client")
    def test_get_latest_version(self, mock_init: MagicMock, gravitino_config: GravitinoConfig) -> None:
        from arrow_lake.catalog.gravitino_models import GravitinoModelRegistry

        reg = GravitinoModelRegistry(gravitino_config)
        mock_version = MagicMock()
        mock_version.version.return_value = 3
        mock_version.uri.return_value = "s3://models/test/v3"
        mock_version.aliases.return_value = ["latest"]
        mock_version.properties.return_value = {"framework": "pytorch"}

        mock_catalog = MagicMock()
        mock_catalog.as_model_catalog().get_model_version.return_value = mock_version
        mock_client = MagicMock()
        mock_client.load_metalake.return_value = MagicMock()
        mock_client.load_catalog.return_value = mock_catalog
        reg._client = mock_client

        info = reg.get_latest_version("test_model")
        assert info is not None
        assert info.version == 3
        assert info.uri == "s3://models/test/v3"


# ---------------------------------------------------------------------------
# GravitinoPolicyService
# ---------------------------------------------------------------------------

class TestGravitinoPolicyService:
    def test_disabled_config_is_noop(self, disabled_config: GravitinoConfig) -> None:
        from arrow_lake.quality.gravitino_policies import GravitinoPolicyService

        svc = GravitinoPolicyService(disabled_config)
        svc.create_retention_policy("r1", 30)
        svc.create_masking_policy("m1", ["ssn"])
        svc.apply_policy("r1", "t1")
        assert svc.list_policies() == []

    @patch("arrow_lake.quality.gravitino_policies.GravitinoPolicyService._init_client")
    def test_create_retention_policy(self, mock_init: MagicMock, gravitino_config: GravitinoConfig) -> None:
        from arrow_lake.quality.gravitino_policies import GravitinoPolicyService

        svc = GravitinoPolicyService(gravitino_config)
        mock_metalake = MagicMock()
        svc._client = MagicMock()
        svc._client.load_metalake.return_value = mock_metalake

        svc.create_retention_policy("retention_30d", 30)
        mock_metalake.create_policy.assert_called_once()


# ---------------------------------------------------------------------------
# GravitinoSyncScheduler
# ---------------------------------------------------------------------------

class TestGravitinoSyncScheduler:
    def test_start_stop(self, gravitino_config: GravitinoConfig) -> None:
        from arrow_lake.catalog.gravitino_bridge import GravitinoBridge
        from arrow_lake.catalog.gravitino_sync import GravitinoSyncScheduler

        bridge = GravitinoBridge(gravitino_config)
        mock_lake = MagicMock()
        mock_lake.list_datasets.return_value = []
        scheduler = GravitinoSyncScheduler(bridge, lake=mock_lake, interval=5)
        scheduler.start()
        assert scheduler._thread is not None
        assert scheduler._thread.is_alive()
        scheduler.stop()
        assert scheduler._thread is None

    def test_double_start_is_noop(self, gravitino_config: GravitinoConfig) -> None:
        from arrow_lake.catalog.gravitino_bridge import GravitinoBridge
        from arrow_lake.catalog.gravitino_sync import GravitinoSyncScheduler

        bridge = GravitinoBridge(gravitino_config)
        mock_lake = MagicMock()
        mock_lake.list_datasets.return_value = []
        scheduler = GravitinoSyncScheduler(bridge, lake=mock_lake, interval=5)
        scheduler.start()
        first_thread = scheduler._thread
        scheduler.start()
        assert scheduler._thread is first_thread
        scheduler.stop()


# ---------------------------------------------------------------------------
# GravitinoStatsCollector
# ---------------------------------------------------------------------------

class TestGravitinoStatsCollector:
    def test_collect_table_stats(self, gravitino_config: GravitinoConfig) -> None:
        from arrow_lake.catalog.gravitino_stats import GravitinoStatsCollector

        collector = GravitinoStatsCollector(gravitino_config)
        mock_conn = MagicMock()

        # collect_table_stats makes 3 sequential execute() calls:
        # 1. information_schema.columns → fetchall() → column metadata
        # 2. SELECT COUNT(*) → fetchone() → row count
        # 3. parquet_metadata → fetchone() → size estimate
        col_result = MagicMock()
        col_result.fetchall.return_value = [
            ("id", "BIGINT"),
            ("name", "VARCHAR"),
        ]
        count_result = MagicMock()
        count_result.fetchone.return_value = (100,)
        size_result = MagicMock()
        size_result.fetchone.return_value = (1.5,)

        mock_conn.execute.side_effect = [col_result, count_result, size_result]

        stats = collector.collect_table_stats("test_table", mock_conn)
        assert stats["name"] == "test_table"
        assert stats["column_count"] == 2
        assert stats["row_count"] == 100
        assert stats["size_mb"] == 1.5
        assert len(stats["columns"]) == 2

    def test_register_stats_disabled(self, disabled_config: GravitinoConfig) -> None:
        from arrow_lake.catalog.gravitino_stats import GravitinoStatsCollector

        collector = GravitinoStatsCollector(disabled_config)
        collector.register_stats("t", {"row_count": 100})


# ---------------------------------------------------------------------------
# Arrow type mapping
# ---------------------------------------------------------------------------

class TestArrowTypeMapping:
    def test_primitive_types(self) -> None:
        from arrow_lake.catalog.gravitino_bridge import _arrow_type_to_gravitino

        assert _arrow_type_to_gravitino(pa.int64()) == "long"
        assert _arrow_type_to_gravitino(pa.int32()) == "integer"
        assert _arrow_type_to_gravitino(pa.float64()) == "double"
        assert _arrow_type_to_gravitino(pa.float32()) == "float"
        assert _arrow_type_to_gravitino(pa.bool_()) == "boolean"
        assert _arrow_type_to_gravitino(pa.string()) == "string"
        assert _arrow_type_to_gravitino(pa.date32()) == "date"

    def test_unknown_type_falls_back_to_string(self) -> None:
        from arrow_lake.catalog.gravitino_bridge import _arrow_type_to_gravitino

        # Complex types fall back to "string"
        assert _arrow_type_to_gravitino(pa.list_(pa.int64())) == "string"
        assert _arrow_type_to_gravitino(pa.struct([("id", pa.int64())])) == "string"
        assert _arrow_type_to_gravitino(pa.map_(pa.string(), pa.int64())) == "string"
