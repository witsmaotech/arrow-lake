"""Tests for catalog/gravitino_bridge.py — bidirectional Gravitino sync.

Covers: type mapping, register/deregister, sync out/inbound, health,
HTTP error handling (table REST), SDK error translation (fileset/schema).
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pyarrow as pa
import pytest
from arrow_lake.catalog.gravitino_bridge import (
    GravitinoBridge,
    GravitinoRequestError,
    GravitinoTransientError,
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

    def test_decimal128(self) -> None:
        assert _arrow_type_to_gravitino(pa.decimal128(10, 2)) == "decimal(10,2)"

    def test_decimal256_clamps_precision_to_38(self) -> None:
        # Gravitino caps decimal precision at 38; decimal256 is clamped down.
        assert _arrow_type_to_gravitino(pa.decimal256(76, 5)) == "decimal(38,5)"

    def test_timestamp_with_timezone(self) -> None:
        assert _arrow_type_to_gravitino(pa.timestamp("us", tz="UTC")) == "timestamp_tz"

    def test_compound_type_falls_back_with_warning(self) -> None:
        from structlog.testing import capture_logs

        with capture_logs() as logs:
            result = _arrow_type_to_gravitino(pa.list_(pa.int32()))
        assert result == "string"
        assert any(log.get("event") == "gravitino_type_unmapped" for log in logs)

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


def _make_sdk_mocks() -> tuple[MagicMock, MagicMock, MagicMock]:
    """Build a mock SDK client with schema + fileset catalog chains.

    load_catalog() returns one shared catalog mock regardless of name, so
    both lance-catalog and minio-fileset route through the same
    schema_catalog / fileset_catalog mocks (sufficient for the bridge's
    usage pattern). Returns (client, schema_catalog, fileset_catalog).
    """
    client = MagicMock()
    catalog = MagicMock()
    schema_cat = MagicMock()
    fileset_cat = MagicMock()
    catalog.as_schema_catalog.return_value = schema_cat
    catalog.as_fileset_catalog.return_value = fileset_cat
    client.load_catalog.return_value = catalog
    return client, schema_cat, fileset_cat


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
# _request (table REST path — P1 reliability contract)
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

    def test_500_raises_transient_after_retry(self) -> None:
        from urllib.error import HTTPError

        with patch("arrow_lake.catalog.gravitino_bridge.create_auth_provider"):
            bridge = GravitinoBridge(_make_config())

        with patch("arrow_lake.catalog.gravitino_bridge.urlopen") as mock_urlopen:
            mock_urlopen.side_effect = HTTPError("url", 500, "Error", {}, None)
            with pytest.raises(GravitinoTransientError):
                bridge._request("GET", "/api/test")

        # Transient 5xx is retried; urlopen is hit more than once.
        assert mock_urlopen.call_count >= 2

    def test_400_raises_non_transient_no_retry(self) -> None:
        from urllib.error import HTTPError

        with patch("arrow_lake.catalog.gravitino_bridge.create_auth_provider"):
            bridge = GravitinoBridge(_make_config())

        with patch("arrow_lake.catalog.gravitino_bridge.urlopen") as mock_urlopen:
            mock_urlopen.side_effect = HTTPError("url", 400, "Bad Request", {}, None)
            with pytest.raises(GravitinoRequestError):
                bridge._request("POST", "/api/test", {"name": "x"})

        # 4xx client errors are not retried.
        assert mock_urlopen.call_count == 1

    def test_url_error_raises_transient(self) -> None:
        from urllib.error import URLError

        with patch("arrow_lake.catalog.gravitino_bridge.create_auth_provider"):
            bridge = GravitinoBridge(_make_config())

        with patch("arrow_lake.catalog.gravitino_bridge.urlopen") as mock_urlopen:
            mock_urlopen.side_effect = URLError("connection refused")
            with pytest.raises(GravitinoTransientError):
                bridge._request("GET", "/api/test")


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
# register_dataset (table REST + fileset SDK)
# ---------------------------------------------------------------------------


class TestRegisterDataset:
    """Register dataset as Table (REST) + Fileset (SDK)."""

    def test_registers_table_and_fileset(self) -> None:
        with patch("arrow_lake.catalog.gravitino_bridge.create_auth_provider"):
            bridge = GravitinoBridge(_make_config())
        client, _schema_cat, fileset_cat = _make_sdk_mocks()

        with (
            patch.object(bridge, "_ensure_client", return_value=client),
            patch.object(bridge, "_request", return_value={"code": 0}) as mock_req,
        ):
            bridge.register_dataset("docs")

        # Table POST via _request
        assert any(c[0][0] == "POST" and "tables" in c[0][1] for c in mock_req.call_args_list)
        # Fileset create via SDK
        fileset_cat.create_multiple_location_fileset.assert_called_once()

    def test_fileset_already_exists_is_idempotent(self) -> None:
        """SDK AlreadyExistsException → no-op (logs 'exists'), not a failure."""
        from gravitino.exceptions.base import AlreadyExistsException

        with patch("arrow_lake.catalog.gravitino_bridge.create_auth_provider"):
            bridge = GravitinoBridge(_make_config())
        client, _schema_cat, fileset_cat = _make_sdk_mocks()
        fileset_cat.create_multiple_location_fileset.side_effect = AlreadyExistsException("exists")

        with (
            patch.object(bridge, "_ensure_client", return_value=client),
            patch.object(bridge, "_request", return_value={"code": 0}),
        ):
            bridge.register_dataset("docs")  # must not raise

    def test_fileset_create_failure_is_not_misclassified_as_exists(self) -> None:
        """A 4xx RESTException is caught + logged as register_failed."""
        from gravitino.exceptions.base import RESTException

        with patch("arrow_lake.catalog.gravitino_bridge.create_auth_provider"):
            bridge = GravitinoBridge(_make_config())
        client, _schema_cat, fileset_cat = _make_sdk_mocks()
        fileset_cat.create_multiple_location_fileset.side_effect = RESTException("400")

        with (
            patch.object(bridge, "_ensure_client", return_value=client),
            patch.object(bridge, "_request", return_value={"code": 0}),
        ):
            bridge.register_dataset("docs")  # GravitinoRequestError caught

    def test_uses_default_location_when_empty(self) -> None:
        with patch("arrow_lake.catalog.gravitino_bridge.create_auth_provider"):
            bridge = GravitinoBridge(_make_config())
        client, _schema_cat, fileset_cat = _make_sdk_mocks()

        with (
            patch.object(bridge, "_ensure_client", return_value=client),
            patch.object(bridge, "_request", return_value={"code": 0}),
        ):
            bridge.register_dataset("ds", location="")

        # storage_locations (4th positional arg) gets the default s3 path.
        args = fileset_cat.create_multiple_location_fileset.call_args
        assert args[0][3] == {"default": "s3://arrow-lake/ds.lance"}

    def test_uses_provided_location(self) -> None:
        with patch("arrow_lake.catalog.gravitino_bridge.create_auth_provider"):
            bridge = GravitinoBridge(_make_config())
        client, _schema_cat, fileset_cat = _make_sdk_mocks()

        with (
            patch.object(bridge, "_ensure_client", return_value=client),
            patch.object(bridge, "_request", return_value={"code": 0}),
        ):
            bridge.register_dataset("ds", location="s3a://bucket/path")

        args = fileset_cat.create_multiple_location_fileset.call_args
        assert args[0][3] == {"default": "s3://bucket/path"}

    def test_skips_fileset_when_sdk_client_unavailable(self) -> None:
        with patch("arrow_lake.catalog.gravitino_bridge.create_auth_provider"):
            bridge = GravitinoBridge(_make_config())

        with (
            patch.object(bridge, "_ensure_client", return_value=None),
            patch.object(bridge, "_request", return_value={"code": 0}) as mock_req,
        ):
            bridge.register_dataset("docs")

        # Table still registered via REST; fileset skipped (no SDK client).
        assert any(c[0][0] == "POST" and "tables" in c[0][1] for c in mock_req.call_args_list)


# ---------------------------------------------------------------------------
# deregister_dataset
# ---------------------------------------------------------------------------


class TestDeregisterDataset:
    """Remove dataset from Gravitino (table REST + fileset SDK)."""

    def test_sends_delete_for_table_and_fileset(self) -> None:
        with patch("arrow_lake.catalog.gravitino_bridge.create_auth_provider"):
            bridge = GravitinoBridge(_make_config())
        client, _schema_cat, fileset_cat = _make_sdk_mocks()

        with (
            patch.object(bridge, "_ensure_client", return_value=client),
            patch.object(bridge, "_request", return_value=None) as mock_req,
        ):
            bridge.deregister_dataset("docs")

        # Table DELETE via _request (fileset drop is via SDK).
        assert mock_req.call_count == 1
        assert mock_req.call_args[0][0] == "DELETE"
        fileset_cat.drop_fileset.assert_called_once()

    def test_deregister_fileset_idempotent_when_missing(self) -> None:
        from gravitino.exceptions.base import NotFoundException

        with patch("arrow_lake.catalog.gravitino_bridge.create_auth_provider"):
            bridge = GravitinoBridge(_make_config())
        client, _schema_cat, fileset_cat = _make_sdk_mocks()
        fileset_cat.drop_fileset.side_effect = NotFoundException("missing")

        with (
            patch.object(bridge, "_ensure_client", return_value=client),
            patch.object(bridge, "_request", return_value=None),
        ):
            bridge.deregister_dataset("docs")  # must not raise


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
# sync_inbound (SDK list_filesets)
# ---------------------------------------------------------------------------


class TestSyncInbound:
    """Pull filesets from Gravitino via SDK."""

    def test_returns_entries(self) -> None:
        with patch("arrow_lake.catalog.gravitino_bridge.create_auth_provider"):
            bridge = GravitinoBridge(_make_config())
        client, _schema_cat, fileset_cat = _make_sdk_mocks()
        id1, id2 = MagicMock(), MagicMock()
        id1.name.return_value = "ds1"
        id2.name.return_value = "ds2"
        fileset_cat.list_filesets.return_value = [id1, id2]

        with patch.object(bridge, "_ensure_client", return_value=client):
            entries = bridge.sync_inbound()

        assert len(entries) == 2
        assert entries[0]["name"] == "ds1"
        assert "minio-fileset" in entries[0]["location"]

    def test_returns_empty_when_no_filesets(self) -> None:
        with patch("arrow_lake.catalog.gravitino_bridge.create_auth_provider"):
            bridge = GravitinoBridge(_make_config())
        client, _schema_cat, fileset_cat = _make_sdk_mocks()
        fileset_cat.list_filesets.return_value = []

        with patch.object(bridge, "_ensure_client", return_value=client):
            assert bridge.sync_inbound() == []

    def test_returns_empty_when_client_unavailable(self) -> None:
        with patch("arrow_lake.catalog.gravitino_bridge.create_auth_provider"):
            bridge = GravitinoBridge(_make_config())

        with patch.object(bridge, "_ensure_client", return_value=None):
            assert bridge.sync_inbound() == []


# ---------------------------------------------------------------------------
# get_table_statistics (SDK load_fileset)
# ---------------------------------------------------------------------------


class TestGetTableStatistics:
    """Fetch fileset details from Gravitino via SDK."""

    def test_returns_statistics(self) -> None:
        with patch("arrow_lake.catalog.gravitino_bridge.create_auth_provider"):
            bridge = GravitinoBridge(_make_config())
        client, _schema_cat, fileset_cat = _make_sdk_mocks()
        fs = MagicMock()
        fs.name.return_value = "docs"
        fs.fileset_type.return_value = "MANAGED"
        fs.properties.return_value = {"format": "lance"}
        fileset_cat.load_fileset.return_value = fs

        with patch.object(bridge, "_ensure_client", return_value=client):
            stats = bridge.get_table_statistics("docs")

        assert stats == {
            "name": "docs",
            "type": "MANAGED",
            "properties": {"format": "lance"},
        }

    def test_returns_none_when_not_found(self) -> None:
        from gravitino.exceptions.base import NotFoundException

        with patch("arrow_lake.catalog.gravitino_bridge.create_auth_provider"):
            bridge = GravitinoBridge(_make_config())
        client, _schema_cat, fileset_cat = _make_sdk_mocks()
        fileset_cat.load_fileset.side_effect = NotFoundException("missing")

        with patch.object(bridge, "_ensure_client", return_value=client):
            assert bridge.get_table_statistics("missing") is None


# ---------------------------------------------------------------------------
# health
# ---------------------------------------------------------------------------


class TestHealth:
    """Check Gravitino connectivity (table REST path)."""

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
# _ensure_schema (SDK create_schema, idempotent)
# ---------------------------------------------------------------------------


class TestEnsureSchema:
    """Schema creation is idempotent via SDK."""

    def test_creates_schema_in_both_catalogs(self) -> None:
        with patch("arrow_lake.catalog.gravitino_bridge.create_auth_provider"):
            bridge = GravitinoBridge(_make_config())
        client, schema_cat, _fileset_cat = _make_sdk_mocks()

        with patch.object(bridge, "_ensure_client", return_value=client):
            bridge._ensure_schema()

        # create_schema called for both lance-catalog and minio-fileset.
        assert schema_cat.create_schema.call_count == 2

    def test_skips_on_subsequent_calls(self) -> None:
        with patch("arrow_lake.catalog.gravitino_bridge.create_auth_provider"):
            bridge = GravitinoBridge(_make_config())
        client, schema_cat, _fileset_cat = _make_sdk_mocks()

        with patch.object(bridge, "_ensure_client", return_value=client):
            bridge._ensure_schema()
            bridge._ensure_schema()

        # Only 2 calls despite 2 invocations (schema_ready guard).
        assert schema_cat.create_schema.call_count == 2

    def test_schema_already_exists_is_idempotent(self) -> None:
        from gravitino.exceptions.base import SchemaAlreadyExistsException

        with patch("arrow_lake.catalog.gravitino_bridge.create_auth_provider"):
            bridge = GravitinoBridge(_make_config())
        client, schema_cat, _fileset_cat = _make_sdk_mocks()
        schema_cat.create_schema.side_effect = SchemaAlreadyExistsException("exists")

        with patch.object(bridge, "_ensure_client", return_value=client):
            bridge._ensure_schema()  # must not raise


# ---------------------------------------------------------------------------
# Container mapping (DR14 D7: dataset→schema, table→table) — W4.3
# ---------------------------------------------------------------------------


class TestContainerMapping:
    """register_container creates a schema + per-table REST registrations."""

    def _bridge(self) -> GravitinoBridge:
        with patch("arrow_lake.catalog.gravitino_bridge.create_auth_provider"):
            return GravitinoBridge(_make_config())

    def test_register_container_schema_and_tables(self) -> None:
        import pyarrow as pa

        bridge = self._bridge()
        calls: list[tuple[str, str]] = []
        with (
            patch.object(
                bridge,
                "_request",
                side_effect=lambda m, p, b=None: calls.append((m, p)) or {"code": 0},
            ),
            patch.object(bridge, "_ensure_schema"),
        ):
            bridge.register_container(
                "gas_net",
                {
                    "segments": pa.schema([("id", pa.string())]),
                    "stations": pa.schema([("sid", pa.string())]),
                },
            )
        # 1 schema creation + 2 table registrations, all under the container name
        assert ("POST", "/api/metalakes/arrow_lake/catalogs/lance-catalog/schemas") in calls
        table_paths = [p for _, p in calls if p.endswith("/tables")]
        assert len(table_paths) == 2
        assert all("/schemas/gas_net/tables" in p for p in table_paths)
        assert not any("/schemas/default/tables" in p for p in table_paths)

    def test_register_container_4xx_does_not_abort_remaining_tables(self) -> None:
        """P2-4 (review 2026-08-26 §三): only Transient was caught per-table —
        a 4xx on one table (e.g. an illegal column 400) aborted the whole
        loop, silently skipping every remaining table in the container."""
        import pyarrow as pa
        from arrow_lake.catalog.gravitino_bridge import GravitinoRequestError

        bridge = self._bridge()
        registered: list[str] = []

        def flaky(method: str, path: str, body=None):
            name = (body or {}).get("name", "")
            if name:
                registered.append(name)
            if name == "segments":
                raise GravitinoRequestError(
                    f"gravitino {method} {path} -> HTTP 400",
                    status=400,
                )
            return {"code": 0}

        with (
            patch.object(bridge, "_request", side_effect=flaky),
            patch.object(bridge, "_ensure_schema"),
            patch.object(bridge, "_ensure_container_schema"),
        ):
            bridge.register_container(
                "gas_net",
                {
                    "segments": pa.schema([("id", pa.string())]),
                    "stations": pa.schema([("sid", pa.string())]),
                },
            )
        # stations STILL registered even though segments 4xx'd
        assert "stations" in registered, f"remaining table must not be skipped: {registered}"

    def test_register_container_transient_still_continues(self) -> None:
        import pyarrow as pa
        from arrow_lake.catalog.gravitino_bridge import GravitinoTransientError

        bridge = self._bridge()
        registered: list[str] = []

        def flaky(method: str, path: str, body=None):
            name = (body or {}).get("name", "")
            if name:
                registered.append(name)
            if name == "segments":
                raise GravitinoTransientError(
                    f"gravitino {method} {path} -> HTTP 503",
                    status=503,
                )
            return {"code": 0}

        with (
            patch.object(bridge, "_request", side_effect=flaky),
            patch.object(bridge, "_ensure_schema"),
            patch.object(bridge, "_ensure_container_schema"),
        ):
            bridge.register_container(
                "gas_net",
                {
                    "segments": pa.schema([("id", pa.string())]),
                    "stations": pa.schema([("sid", pa.string())]),
                },
            )
        assert "stations" in registered

    def test_sync_outbound_routes_container_entries(self) -> None:
        bridge = self._bridge()
        with (
            patch.object(bridge, "register_container") as rc,
            patch.object(bridge, "register_dataset") as rd,
        ):
            n = bridge.sync_outbound(
                [
                    {"name": "plain", "location": ""},
                    {"name": "gas_net", "container": True, "tables": {"t1": None}},
                ]
            )
        assert n == 2
        rc.assert_called_once_with("gas_net", {"t1": None})
        rd.assert_called_once_with(name="plain", location="", schema=None)

    def test_load_local_entries_includes_containers(self, tmp_path) -> None:
        import pyarrow as pa
        from arrow_lake.catalog.gravitino_sync import _load_local_entries
        from arrow_lake.ingest.storage import LanceStorageManager

        storage = LanceStorageManager(str(tmp_path))
        storage.create_dataset("gas_net", pa.table({"a": [1]}), table="t1")
        storage.create_dataset("plain", pa.table({"b": [2]}))
        lake = MagicMock()
        lake.list_datasets.return_value = ["plain"]
        lake._get_storage.return_value = storage
        entries = _load_local_entries(lake)
        by = {e["name"]: e for e in entries}
        assert "container" not in by["plain"]  # flat entry: no container flag
        assert by["gas_net"]["container"] is True
        assert by["gas_net"]["tables"]["t1"].names == ["a"]


# ---------------------------------------------------------------------------
# SDK 硬超时守护(2026-08-28 发版期实证:fileset schema 服务端 S3 校验
# 可无限悬挂,SDK 无客户端超时,摄入后 hook 卡死)
# ---------------------------------------------------------------------------


class TestSdkHardTimeout:
    def _bridge(self) -> GravitinoBridge:
        with patch("arrow_lake.catalog.gravitino_bridge.create_auth_provider"):
            return GravitinoBridge(_make_config())

    def test_hung_sdk_call_raises_transient_quickly(self) -> None:
        import time

        bridge = self._bridge()

        def hang():
            time.sleep(30)  # 模拟服务端悬挂(远超 15s 帽)

        t0 = time.monotonic()
        with pytest.raises(GravitinoTransientError, match="exceeded"):
            bridge._timed_call(hang, timeout=0.3)
        assert time.monotonic() - t0 < 3  # 调用方秒级解阻,非 30s

    def test_timed_call_passes_result_and_exception(self) -> None:
        bridge = self._bridge()
        assert bridge._timed_call(lambda: 42) == 42
        with pytest.raises(ValueError, match="boom"):
            bridge._timed_call(lambda: (_ for _ in ()).throw(ValueError("boom")))

    def test_call_sdk_timeout_classified_transient(self) -> None:
        """_call_sdk 全链:悬挂 → GravitinoTransientError(进重试/熔断通道)。

        注:宿主 venv 可无 apache-gravitino(14 个 SDK 依赖存量测试同因
        跳过),这里注入最小假模块只测超时→分类的接线。
        """
        import sys
        import time
        import types

        fake_base = types.ModuleType("gravitino.exceptions.base")

        class _FakeSdkError(Exception):
            pass

        for name in (
            "AlreadyExistsException",
            "NotFoundException",
            "InternalError",
            "RESTException",
        ):
            setattr(fake_base, name, _FakeSdkError)
        fake_exc = types.ModuleType("gravitino.exceptions")
        fake_exc.base = fake_base
        fake_pkg = types.ModuleType("gravitino")
        fake_pkg.exceptions = fake_exc
        bridge = self._bridge()
        with (
            patch.dict(
                sys.modules,
                {
                    "gravitino": fake_pkg,
                    "gravitino.exceptions": fake_exc,
                    "gravitino.exceptions.base": fake_base,
                },
            ),
            patch.object(bridge, "SDK_CALL_TIMEOUT_SECONDS", 0.3),
            pytest.raises(GravitinoTransientError, match="exceeded"),
        ):
            bridge._call_sdk(lambda: time.sleep(5))
