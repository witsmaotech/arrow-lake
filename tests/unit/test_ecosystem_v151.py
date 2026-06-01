"""Unit tests for v1.5.1 ecosystem enhancements — Phase 5."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from arrow_lake.api.rbac import GravitinoRBACBridge
from arrow_lake.catalog.lineage import ColumnMapping, LineageStore, create_lineage_event
from arrow_lake.query.federated_engine import FederatedQueryEngine


# ---------------------------------------------------------------------------
# 5.1 Iceberg format reader
# ---------------------------------------------------------------------------


class TestIcebergFormatReader:
    """Test Iceberg is registered as a supported format."""

    def test_iceberg_in_format_readers(self) -> None:
        assert "iceberg" in FederatedQueryEngine._FORMAT_READERS
        assert FederatedQueryEngine._FORMAT_READERS["iceberg"] == "read_iceberg"

    def test_all_expected_formats(self) -> None:
        readers = FederatedQueryEngine._FORMAT_READERS
        assert set(readers.keys()) == {"lance", "parquet", "csv", "iceberg"}


# ---------------------------------------------------------------------------
# 5.2 Gravitino Lineage REST API sync
# ---------------------------------------------------------------------------


class TestGravitinoLineageSync:
    """Test _sync_lineage_to_gravitino method."""

    @patch("urllib.request.urlopen")
    def test_posts_lineage_event(self, mock_urlopen: MagicMock) -> None:
        mock_resp = MagicMock()
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_resp.status = 200
        mock_urlopen.return_value = mock_resp

        store = LineageStore(MagicMock())
        event = create_lineage_event(
            "target_ds", "transform",
            source_datasets=["src_a", "src_b"],
            transform_type="enrich",
        )

        with patch.dict("os.environ", {
            "ARROW_LAKE__GRAVITINO__URI": "http://gravitino:8090",
            "ARROW_LAKE__GRAVITINO__METALAKE": "test_metalake",
        }):
            store._sync_lineage_to_gravitino(event)

        mock_urlopen.assert_called_once()
        req = mock_urlopen.call_args[0][0]
        body = json.loads(req.data)
        assert len(body["upstream"]) == 2
        assert body["upstream"][0]["table"] == "src_a"
        assert body["downstream"][0]["table"] == "target_ds"
        assert body["transformation"] == "enrich"

    def test_skips_when_no_source_datasets(self) -> None:
        store = LineageStore(MagicMock())
        event = create_lineage_event("ds", "create")  # no source_datasets

        with patch.dict("os.environ", {"ARROW_LAKE__GRAVITINO__URI": "http://gravitino:8090"}):
            store._sync_lineage_to_gravitino(event)  # should not raise

    def test_skips_when_no_gravitino_uri(self) -> None:
        store = LineageStore(MagicMock())
        event = create_lineage_event("ds", "transform", source_datasets=["src"])

        with patch.dict("os.environ", {"ARROW_LAKE__GRAVITINO__URI": ""}):
            store._sync_lineage_to_gravitino(event)  # should not raise

    @patch("urllib.request.urlopen")
    def test_auth_provider_used(self, mock_urlopen: MagicMock) -> None:
        mock_resp = MagicMock()
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_resp.status = 200
        mock_urlopen.return_value = mock_resp

        auth_provider = MagicMock()
        store = LineageStore(MagicMock())
        store.set_auth_provider(auth_provider)
        event = create_lineage_event("ds", "transform", source_datasets=["src"])

        with patch.dict("os.environ", {
            "ARROW_LAKE__GRAVITINO__URI": "http://gravitino:8090",
            "ARROW_LAKE__GRAVITINO__METALAKE": "ml",
        }):
            store._sync_lineage_to_gravitino(event)

        auth_provider.authenticate.assert_called_once()

    @patch("urllib.request.urlopen")
    def test_failure_is_silent(self, mock_urlopen: MagicMock) -> None:
        mock_urlopen.side_effect = Exception("connection refused")

        store = LineageStore(MagicMock())
        event = create_lineage_event("ds", "transform", source_datasets=["src"])

        with patch.dict("os.environ", {
            "ARROW_LAKE__GRAVITINO__URI": "http://gravitino:8090",
            "ARROW_LAKE__GRAVITINO__METALAKE": "ml",
        }):
            store._sync_lineage_to_gravitino(event)  # should not raise


# ---------------------------------------------------------------------------
# 5.3 Expanded permission mapping
# ---------------------------------------------------------------------------


class TestExpandedPermissionMapping:
    """Test _ACTION_TO_PRIVILEGE has 15 mappings covering all pipeline actions."""

    def test_mapping_count(self) -> None:
        assert len(GravitinoRBACBridge._ACTION_TO_PRIVILEGE) == 15

    def test_read_mapping(self) -> None:
        assert GravitinoRBACBridge._ACTION_TO_PRIVILEGE["read"] == "SELECT_TABLE"

    def test_write_mapping(self) -> None:
        assert GravitinoRBACBridge._ACTION_TO_PRIVILEGE["write"] == "MODIFY_TABLE"

    def test_append_mapping(self) -> None:
        assert GravitinoRBACBridge._ACTION_TO_PRIVILEGE["append"] == "INSERT_TABLE"

    def test_ingest_mapping(self) -> None:
        assert GravitinoRBACBridge._ACTION_TO_PRIVILEGE["ingest"] == "INSERT_TABLE"

    def test_update_mapping(self) -> None:
        assert GravitinoRBACBridge._ACTION_TO_PRIVILEGE["update"] == "UPDATE_TABLE"

    def test_delete_mapping(self) -> None:
        assert GravitinoRBACBridge._ACTION_TO_PRIVILEGE["delete"] == "DROP_TABLE"

    def test_query_mapping(self) -> None:
        assert GravitinoRBACBridge._ACTION_TO_PRIVILEGE["query"] == "SELECT_TABLE"

    def test_export_mapping(self) -> None:
        assert GravitinoRBACBridge._ACTION_TO_PRIVILEGE["export"] == "SELECT_TABLE"

    def test_admin_mapping(self) -> None:
        assert GravitinoRBACBridge._ACTION_TO_PRIVILEGE["admin"] == "ALL"

    def test_schema_create_mapping(self) -> None:
        assert GravitinoRBACBridge._ACTION_TO_PRIVILEGE["schema_create"] == "CREATE_TABLE"

    def test_schema_list_mapping(self) -> None:
        assert GravitinoRBACBridge._ACTION_TO_PRIVILEGE["schema_list"] == "USAGE"

    def test_tag_manage_mapping(self) -> None:
        assert GravitinoRBACBridge._ACTION_TO_PRIVILEGE["tag_manage"] == "USE_CATALOG"

    def test_policy_manage_mapping(self) -> None:
        assert GravitinoRBACBridge._ACTION_TO_PRIVILEGE["policy_manage"] == "USE_CATALOG"

    def test_stats_collect_mapping(self) -> None:
        assert GravitinoRBACBridge._ACTION_TO_PRIVILEGE["stats_collect"] == "USAGE"

    def test_create_mapping(self) -> None:
        assert GravitinoRBACBridge._ACTION_TO_PRIVILEGE["create"] == "CREATE_TABLE"

    def test_all_values_are_gravitino_privileges(self) -> None:
        """All mapped values are valid Gravitino privilege names."""
        valid = {
            "SELECT_TABLE", "MODIFY_TABLE", "INSERT_TABLE", "UPDATE_TABLE",
            "DELETE_TABLE", "DROP_TABLE", "CREATE_TABLE", "USAGE",
            "USE_CATALOG", "ALL",
        }
        for action, priv in GravitinoRBACBridge._ACTION_TO_PRIVILEGE.items():
            assert priv in valid, f"Invalid privilege '{priv}' for action '{action}'"
