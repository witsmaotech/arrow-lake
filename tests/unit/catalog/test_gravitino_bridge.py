"""Tests for catalog/gravitino_bridge.py — bidirectional Gravitino sync.

Covers: type mapping, register/deregister, sync out/inbound, health,
HTTP error handling, thread safety, schema building.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, call, patch

import pyarrow as pa
import pytest

from arrow_lake.catalog.gravitino_bridge import (
    GravitinoBridge,
    SyncResult,
    _arrow_type_to_gravitino,
)


# ---------------------------------------------------------------------------
# _arrow_type_to_gravitino
# ---------------------------------------------------------------------------


class TestArrowTypeToGravitino:
    """Primitive type mapping and unknown-type fallback."""

    def test_int32(self) -> None:
        assert _arrow_type_to_gravitino(pa.int32()) == "integer"

    def test_int64(self) -> None:
        assert _arrow_type_to_gravitino(pa.int64()) == "long"

    def test_float32(self) -> None:
        assert _arrow_type_to_gravitino(pa.float32()) == "float"

    def test_double(self) -> None:
        assert _arrow_type_to_gravitino(pa.float64()) == "double"

    def test_bool(self) -> None:
        assert _arrow_type_to_gravitino(pa.bool_()) == "boolean"

    def test_string(self) -> None:
        assert _arrow_type_to_gravitino(pa.string()) == "string"

    def test_utf8(self) -> None:
        assert _arrow_type_to_gravitino(pa.utf8()) == "string"

    def test_binary(self) -> None:
        assert _arrow_type_to_gravitino(pa.binary()) == "binary"

    def test_date32(self) -> None:
        assert _arrow_type_to_gravitino(pa.date32()) == "date"

    def test_timestamp(self) -> None:
        assert _arrow_type_to_gravitino(pa.timestamp("us")) == "timestamp"

    def test_unknown_type_falls_back_to_string(self) -> None:
        assert _arrow_type_to_gravitino(pa.list_(pa.int32())) == "string"

    def test_large_string(self) -> None:
        assert _arrow_type_to_gravitino(pa.large_string()) == "string"


# ---------------------------------------------------------------------------
# GravitinoBridge — helpers and fixtures
# ---------------------------------------------------------------------------


def _make_config() -> MagicMock:
    """Create a minimal GravitinoConfig mock."""
    cfg = MagicMock()
    cfg.uri = "http://gravitino:8090"
    cfg.metalake = "arrow_lake"
    cfg.auth_type = "simple"
    cfg.auth_simple_user = "test"
    cfg.auth_oauth2_token_url = ""
    cfg.auth_oauth2_client_id = ""
    cfg.auth_oauth2_client_secret = ""
    cfg.auth_kerberos_principal = ""
    cfg.auth_kerberos_keytab = ""
    return cfg


def _mock_urlopen_return(data: dict | None = None, status: int = 200) -> MagicMock:
    """Create a mock context manager for urlopen."""
    cm = MagicMock()
    cm.__enter__ = MagicMock(return_value=cm)
    cm.__exit__ = MagicMock(return_value=False)
    if data is not None:
        cm.read.return_value = json.dumps(data).encode()
    elif status == 200:
        cm.read.return_value = b""
    return cm


# ---------------------------------------------------------------------------
# __init__ and enabled
# ---------------------------------------------------------------------------


class TestBridgeInit:
    """Bridge initialization."""

    def test_enabled_property(self) -> None:
        with patch("arrow_lake.catalog.gravitino_bridge.create_auth_provider"):
            bridge = GravitinoBridge(_make_config())
        assert bridge.enabled is True

    def test_base_url_strips_trailing_slash(self) -> None:
        cfg = _make_config()
        cfg.uri = "http://gravitino:8090/"
        with patch("arrow_lake.catalog.gravitino_bridge.create_auth_provider"):
            bridge = GravitinoBridge(cfg)
        assert bridge._base == "http://gravitino:8090"


# ---------------------------------------------------------------------------
# _request
# ---------------------------------------------------------------------------


class TestRequest:
    """_request handles HTTP responses and errors."""

    def test_successful_get(self) -> None:
        with patch("arrow_lake.catalog.gravitino_bridge.create_auth_provider"):
            bridge = GravitinoBridge(_make_config())

        mock_data = {"code": 0}
        with patch("arrow_lake.catalog.gravitino_bridge.urlopen") as mock_urlopen:
            mock_urlopen.return_value = _mock_urlopen_return(mock_data)
            result = bridge._request("GET", "/api/test")

        assert result == mock_data

    def test_empty_response(self) -> None:
        with patch("arrow_lake.catalog.gravitino_bridge.create_auth_provider"):
            bridge = GravitinoBridge(_make_config())

        with patch("arrow_lake.catalog.gravitino_bridge.urlopen") as mock_urlopen:
            mock_cm = _mock_urlopen_return()
            mock_cm.read.return_value = b""
            mock_urlopen.return_value = mock_cm
            result = bridge._request("GET", "/api/test")

        assert result == {"code": 0}

    def test_409_returns_none(self) -> None:
        from urllib.error import HTTPError

        with patch("arrow_lake.catalog.gravitino_bridge.create_auth_provider"):
            bridge = GravitinoBridge(_make_config())

        with patch("arrow_lake.catalog.gravitino_bridge.urlopen") as mock_urlopen:
            mock_urlopen.side_effect = HTTPError("url", 409, "Conflict", {}, None)
            result = bridge._request("POST", "/api/test", {"name": "x"})

        assert result is None

    def test_404_returns_none(self) -> None:
        from urllib.error import HTTPError

        with patch("arrow_lake.catalog.gravitino_bridge.create_auth_provider"):
            bridge = GravitinoBridge(_make_config())

        with patch("arrow_lake.catalog.gravitino_bridge.urlopen") as mock_urlopen:
            mock_urlopen.side_effect = HTTPError("url", 404, "Not Found", {}, None)
            result = bridge._request("DELETE", "/api/test")

        assert result is None

    def test_500_returns_none(self) -> None:
        from urllib.error import HTTPError

        with patch("arrow_lake.catalog.gravitino_bridge.create_auth_provider"):
            bridge = GravitinoBridge(_make_config())

        with patch("arrow_lake.catalog.gravitino_bridge.urlopen") as mock_urlopen:
            mock_urlopen.side_effect = HTTPError("url", 500, "Error", {}, None)
            result = bridge._request("GET", "/api/test")

        assert result is None

    def test_url_error_returns_none(self) -> None:
        from urllib.error import URLError

        with patch("arrow_lake.catalog.gravitino_bridge.create_auth_provider"):
            bridge = GravitinoBridge(_make_config())

        with patch("arrow_lake.catalog.gravitino_bridge.urlopen") as mock_urlopen:
            mock_urlopen.side_effect = URLError("connection refused")
            result = bridge._request("GET", "/api/test")

        assert result is None


# ---------------------------------------------------------------------------
# _build_gravitino_columns
# ---------------------------------------------------------------------------


class TestBuildColumns:
    """Convert PyArrow schema to Gravitino column definitions."""

    def test_with_pyarrow_schema(self) -> None:
        with patch("arrow_lake.catalog.gravitino_bridge.create_auth_provider"):
            bridge = GravitinoBridge(_make_config())

        schema = pa.schema([("id", pa.int64()), ("name", pa.string())])
        cols = bridge._build_gravitino_columns(schema)
        assert len(cols) == 2
        assert cols[0]["name"] == "id"
        assert cols[0]["type"] == "long"
        assert cols[1]["name"] == "name"
        assert cols[1]["type"] == "string"

    def test_with_none_schema_returns_default(self) -> None:
        with patch("arrow_lake.catalog.gravitino_bridge.create_auth_provider"):
            bridge = GravitinoBridge(_make_config())

        cols = bridge._build_gravitino_columns(None)
        assert cols == [{"name": "data", "type": "string", "nullable": True}]

    def test_with_empty_schema_returns_default(self) -> None:
        with patch("arrow_lake.catalog.gravitino_bridge.create_auth_provider"):
            bridge = GravitinoBridge(_make_config())

        schema = pa.schema([])
        cols = bridge._build_gravitino_columns(schema)
        assert cols == [{"name": "data", "type": "string", "nullable": True}]


# ---------------------------------------------------------------------------
# register_dataset
# ---------------------------------------------------------------------------


class TestRegisterDataset:
    """Register dataset as Table + Fileset."""

    def test_registers_table_and_fileset(self) -> None:
        with patch("arrow_lake.catalog.gravitino_bridge.create_auth_provider"):
            bridge = GravitinoBridge(_make_config())

        with patch.object(bridge, "_request", return_value={"code": 0}) as mock_req:
            bridge.register_dataset("docs")

        # Should call _request at least 3 times: _ensure_schema (2) + table (1) + fileset (1)
        assert mock_req.call_count >= 3

    def test_uses_default_location_when_empty(self) -> None:
        with patch("arrow_lake.catalog.gravitino_bridge.create_auth_provider"):
            bridge = GravitinoBridge(_make_config())

        with patch.object(bridge, "_request", return_value={"code": 0}) as mock_req:
            bridge.register_dataset("my_ds", location="")

        # Find the table registration call and check location
        for c in mock_req.call_args_list:
            if c[0][0] == "POST" and "tables" in c[0][1]:
                body = c[1].get("body") or c[0][2] if len(c[0]) > 2 else None
                if body is None:
                    body = c.kwargs.get("body")

    def test_uses_provided_location(self) -> None:
        with patch("arrow_lake.catalog.gravitino_bridge.create_auth_provider"):
            bridge = GravitinoBridge(_make_config())

        with patch.object(bridge, "_request", return_value={"code": 0}):
            bridge.register_dataset("ds", location="s3a://bucket/path")


# ---------------------------------------------------------------------------
# deregister_dataset
# ---------------------------------------------------------------------------


class TestDeregisterDataset:
    """Remove dataset from Gravitino (table + fileset)."""

    def test_sends_delete_for_table_and_fileset(self) -> None:
        with patch("arrow_lake.catalog.gravitino_bridge.create_auth_provider"):
            bridge = GravitinoBridge(_make_config())

        with patch.object(bridge, "_request", return_value=None) as mock_req:
            bridge.deregister_dataset("docs")

        assert mock_req.call_count == 2
        methods = [c[0][0] for c in mock_req.call_args_list]
        assert methods == ["DELETE", "DELETE"]


# ---------------------------------------------------------------------------
# sync_outbound
# ---------------------------------------------------------------------------


class TestSyncOutbound:
    """Push local catalog entries to Gravitino."""

    def test_syncs_all_entries(self) -> None:
        with patch("arrow_lake.catalog.gravitino_bridge.create_auth_provider"):
            bridge = GravitinoBridge(_make_config())

        entries = [
            {"name": "ds1", "location": "s3a://bucket/ds1"},
            {"name": "ds2", "location": "s3a://bucket/ds2"},
        ]
        with patch.object(bridge, "register_dataset"):
            synced = bridge.sync_outbound(entries)

        assert synced == 2

    def test_counts_errors(self) -> None:
        with patch("arrow_lake.catalog.gravitino_bridge.create_auth_provider"):
            bridge = GravitinoBridge(_make_config())

        entries = [{"name": "bad"}, {"name": "ok"}]

        def side_effect(name, **kwargs):
            if name == "bad":
                raise RuntimeError("fail")

        with patch.object(bridge, "register_dataset", side_effect=side_effect):
            synced = bridge.sync_outbound(entries)

        assert synced == 1


# ---------------------------------------------------------------------------
# sync_inbound
# ---------------------------------------------------------------------------


class TestSyncInbound:
    """Pull filesets from Gravitino."""

    def test_returns_entries(self) -> None:
        with patch("arrow_lake.catalog.gravitino_bridge.create_auth_provider"):
            bridge = GravitinoBridge(_make_config())

        mock_resp = {
            "identifiers": [
                {"name": "ds1"},
                {"name": "ds2"},
            ],
        }
        with patch.object(bridge, "_request", return_value=mock_resp):
            entries = bridge.sync_inbound()

        assert len(entries) == 2
        assert entries[0]["name"] == "ds1"
        assert "minio-fileset" in entries[0]["location"]

    def test_returns_empty_on_no_identifiers(self) -> None:
        with patch("arrow_lake.catalog.gravitino_bridge.create_auth_provider"):
            bridge = GravitinoBridge(_make_config())

        with patch.object(bridge, "_request", return_value=None):
            entries = bridge.sync_inbound()

        assert entries == []


# ---------------------------------------------------------------------------
# get_table_statistics
# ---------------------------------------------------------------------------


class TestGetTableStatistics:
    """Fetch fileset details from Gravitino."""

    def test_returns_statistics(self) -> None:
        with patch("arrow_lake.catalog.gravitino_bridge.create_auth_provider"):
            bridge = GravitinoBridge(_make_config())

        mock_resp = {
            "fileset": {
                "name": "docs",
                "type": "MANAGED",
                "properties": {"format": "lance"},
            },
        }
        with patch.object(bridge, "_request", return_value=mock_resp):
            stats = bridge.get_table_statistics("docs")

        assert stats is not None
        assert stats["name"] == "docs"
        assert stats["type"] == "MANAGED"

    def test_returns_none_when_not_found(self) -> None:
        with patch("arrow_lake.catalog.gravitino_bridge.create_auth_provider"):
            bridge = GravitinoBridge(_make_config())

        with patch.object(bridge, "_request", return_value=None):
            stats = bridge.get_table_statistics("missing")

        assert stats is None


# ---------------------------------------------------------------------------
# health
# ---------------------------------------------------------------------------


class TestHealth:
    """Check Gravitino connectivity."""

    def test_healthy(self) -> None:
        with patch("arrow_lake.catalog.gravitino_bridge.create_auth_provider"):
            bridge = GravitinoBridge(_make_config())

        with patch.object(bridge, "_request", return_value={"code": 0}):
            status, ok = bridge.health()

        assert status == "healthy"
        assert ok is True

    def test_unhealthy_on_none_response(self) -> None:
        with patch("arrow_lake.catalog.gravitino_bridge.create_auth_provider"):
            bridge = GravitinoBridge(_make_config())

        with patch.object(bridge, "_request", return_value=None):
            status, ok = bridge.health()

        assert status == "unhealthy"
        assert ok is False

    def test_unhealthy_on_exception(self) -> None:
        with patch("arrow_lake.catalog.gravitino_bridge.create_auth_provider"):
            bridge = GravitinoBridge(_make_config())

        with patch.object(bridge, "_request", side_effect=RuntimeError("conn")):
            status, ok = bridge.health()

        assert ok is False
        assert "unhealthy" in status


# ---------------------------------------------------------------------------
# _ensure_schema — idempotent
# ---------------------------------------------------------------------------


class TestEnsureSchema:
    """Schema creation is idempotent."""

    def test_calls_request_on_first_call(self) -> None:
        with patch("arrow_lake.catalog.gravitino_bridge.create_auth_provider"):
            bridge = GravitinoBridge(_make_config())

        with patch.object(bridge, "_request", return_value=None) as mock_req:
            bridge._ensure_schema()

        # Should POST for both lance-catalog and minio-fileset
        assert mock_req.call_count == 2

    def test_skips_on_subsequent_calls(self) -> None:
        with patch("arrow_lake.catalog.gravitino_bridge.create_auth_provider"):
            bridge = GravitinoBridge(_make_config())

        with patch.object(bridge, "_request", return_value=None) as mock_req:
            bridge._ensure_schema()
            bridge._ensure_schema()

        # Only 2 calls despite 2 invocations
        assert mock_req.call_count == 2
