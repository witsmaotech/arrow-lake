"""Tests for catalog/gravitino_client.py — Gravitino SDK wrapper.

Covers: lazy init, list/create catalogs, list/load/drop tables, health.
All SDK calls are mocked since the gravitino package is optional.
"""

from __future__ import annotations

import sys
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from arrow_lake.catalog.gravitino_client import (
    ArrowLakeGravitinoClient,
    GravitinoTableInfo,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mock_col(name: str = "id", type_str: str = "integer", nullable: bool = True):
    """Create a mock column object."""
    col = MagicMock()
    col.name.return_value = name
    col.data_type.return_value = type_str
    col.nullable.return_value = nullable
    return col


def _mock_table(name: str = "docs", columns=None, properties=None):
    """Create a mock table object."""
    tbl = MagicMock()
    tbl.name.return_value = name
    tbl.columns.return_value = columns or [_mock_col()]
    tbl.properties.return_value = properties or {}
    return tbl


def _setup_sdk_mocks():
    """Set up mock Gravitino SDK modules and return (admin_client, client)."""
    mock_admin = MagicMock()
    mock_client = MagicMock()
    mock_admin_client_cls = MagicMock(return_value=mock_admin)
    mock_client_cls = MagicMock(return_value=mock_client)

    return mock_admin, mock_client, mock_admin_client_cls, mock_client_cls


# ---------------------------------------------------------------------------
# _ensure_initialized — lazy init
# ---------------------------------------------------------------------------


class TestEnsureInitialized:
    """Lazy SDK initialization with failure handling."""

    def test_initializes_successfully(self) -> None:
        mock_admin, mock_client, mock_admin_cls, mock_client_cls = _setup_sdk_mocks()
        client = ArrowLakeGravitinoClient("http://g:8090", "test")

        with patch.dict(sys.modules, {
            "gravitino": MagicMock(),
            "gravitino.client": MagicMock(),
            "gravitino.client.gravitino_admin_client": MagicMock(
                GravitinoAdminClient=mock_admin_cls,
            ),
            "gravitino.client.gravitino_client": MagicMock(
                GravitinoClient=mock_client_cls,
            ),
        }):
            result = client._ensure_initialized()

        assert result is True
        assert client._initialized is True
        mock_admin.load_metalake.assert_called_once_with("test")

    def test_returns_false_on_import_error(self) -> None:
        client = ArrowLakeGravitinoClient("http://g:8090", "test")

        # Force ImportError for the SDK
        with patch.dict(sys.modules, {
            "gravitino.client.gravitino_admin_client": None,
            "gravitino.client.gravitino_client": None,
        }):
            result = client._ensure_initialized()

        assert result is False
        assert client._client is None

    def test_returns_false_on_init_exception(self) -> None:
        mock_admin_cls = MagicMock(side_effect=RuntimeError("conn refused"))
        client = ArrowLakeGravitinoClient("http://g:8090", "test")

        with patch.dict(sys.modules, {
            "gravitino": MagicMock(),
            "gravitino.client": MagicMock(),
            "gravitino.client.gravitino_admin_client": MagicMock(
                GravitinoAdminClient=mock_admin_cls,
            ),
            "gravitino.client.gravitino_client": MagicMock(),
        }):
            result = client._ensure_initialized()

        assert result is False

    def test_only_initializes_once(self) -> None:
        client = ArrowLakeGravitinoClient("http://g:8090", "test")
        client._initialized = True
        client._client = MagicMock()

        result = client._ensure_initialized()
        assert result is True


# ---------------------------------------------------------------------------
# list_catalogs
# ---------------------------------------------------------------------------


class TestListCatalogs:
    """List catalogs via admin client."""

    def test_returns_catalog_names(self) -> None:
        mock_admin, _, mock_admin_cls, mock_client_cls = _setup_sdk_mocks()
        cat1 = MagicMock()
        cat1.name.return_value = "lance-catalog"
        cat2 = MagicMock()
        cat2.name.return_value = "minio-fileset"
        mock_admin.list_catalogs.return_value = [cat1, cat2]

        client = ArrowLakeGravitinoClient("http://g:8090", "test")

        with patch.dict(sys.modules, {
            "gravitino": MagicMock(),
            "gravitino.client": MagicMock(),
            "gravitino.client.gravitino_admin_client": MagicMock(
                GravitinoAdminClient=mock_admin_cls,
            ),
            "gravitino.client.gravitino_client": MagicMock(
                GravitinoClient=mock_client_cls,
            ),
        }):
            names = client.list_catalogs()

        assert names == ["lance-catalog", "minio-fileset"]

    def test_returns_empty_on_failure(self) -> None:
        client = ArrowLakeGravitinoClient("http://g:8090", "test")
        client._initialized = True
        client._admin_client = MagicMock()
        client._admin_client.list_catalogs.side_effect = RuntimeError("err")

        assert client.list_catalogs() == []

    def test_returns_empty_when_not_initialized(self) -> None:
        client = ArrowLakeGravitinoClient("http://g:8090", "test")
        client._initialized = True
        client._client = None

        assert client.list_catalogs() == []


# ---------------------------------------------------------------------------
# create_catalog
# ---------------------------------------------------------------------------


class TestCreateCatalog:
    """Create catalog via admin client."""

    def test_creates_successfully(self) -> None:
        mock_admin, _, mock_admin_cls, mock_client_cls = _setup_sdk_mocks()
        client = ArrowLakeGravitinoClient("http://g:8090", "test")

        with patch.dict(sys.modules, {
            "gravitino": MagicMock(),
            "gravitino.client": MagicMock(),
            "gravitino.client.gravitino_admin_client": MagicMock(
                GravitinoAdminClient=mock_admin_cls,
            ),
            "gravitino.client.gravitino_client": MagicMock(
                GravitinoClient=mock_client_cls,
            ),
        }):
            result = client.create_catalog("test-cat", provider="lance")

        assert result is True
        mock_admin.create_catalog.assert_called_once()

    def test_returns_false_on_failure(self) -> None:
        client = ArrowLakeGravitinoClient("http://g:8090", "test")
        client._initialized = True
        client._admin_client = MagicMock()
        client._admin_client.create_catalog.side_effect = RuntimeError("err")

        assert client.create_catalog("cat") is False


# ---------------------------------------------------------------------------
# list_tables
# ---------------------------------------------------------------------------


class TestListTables:
    """List tables in a catalog/schema."""

    def test_returns_table_names(self) -> None:
        client = ArrowLakeGravitinoClient("http://g:8090", "test")
        client._initialized = True
        client._client = MagicMock()

        mock_cat = MagicMock()
        mock_table_cat = MagicMock()
        mock_table_cat.list_tables.return_value = ["docs", "images"]
        mock_cat.as_table_catalog.return_value = mock_table_cat
        client._client.load_catalog.return_value = mock_cat

        names = client.list_tables()
        assert names == ["docs", "images"]

    def test_uses_custom_catalog_and_schema(self) -> None:
        client = ArrowLakeGravitinoClient("http://g:8090", "test")
        client._initialized = True
        client._client = MagicMock()

        mock_cat = MagicMock()
        mock_table_cat = MagicMock()
        mock_table_cat.list_tables.return_value = ["t1"]
        mock_cat.as_table_catalog.return_value = mock_table_cat
        client._client.load_catalog.return_value = mock_cat

        names = client.list_tables(catalog="custom-cat", schema="custom-schema")
        client._client.load_catalog.assert_called_once_with("custom-cat")
        mock_table_cat.list_tables.assert_called_once_with("custom-schema")

    def test_returns_empty_on_failure(self) -> None:
        client = ArrowLakeGravitinoClient("http://g:8090", "test")
        client._initialized = True
        client._client = MagicMock()
        client._client.load_catalog.side_effect = RuntimeError("err")

        assert client.list_tables() == []


# ---------------------------------------------------------------------------
# load_table
# ---------------------------------------------------------------------------


class TestLoadTable:
    """Load table metadata."""

    def test_returns_table_info(self) -> None:
        client = ArrowLakeGravitinoClient("http://g:8090", "test")
        client._initialized = True
        client._client = MagicMock()

        mock_cat = MagicMock()
        mock_table_cat = MagicMock()
        tbl = _mock_table("docs", columns=[_mock_col("id", "integer", True)])
        mock_table_cat.load_table.return_value = tbl
        mock_cat.as_table_catalog.return_value = mock_table_cat
        client._client.load_catalog.return_value = mock_cat

        info = client.load_table("docs")
        assert info is not None
        assert info.name == "docs"
        assert len(info.columns) == 1
        assert info.columns[0]["name"] == "id"

    def test_returns_none_on_failure(self) -> None:
        client = ArrowLakeGravitinoClient("http://g:8090", "test")
        client._initialized = True
        client._client = MagicMock()
        client._client.load_catalog.side_effect = RuntimeError("err")

        assert client.load_table("missing") is None


# ---------------------------------------------------------------------------
# drop_table
# ---------------------------------------------------------------------------


class TestDropTable:
    """Drop table via purge_table."""

    def test_drops_successfully(self) -> None:
        client = ArrowLakeGravitinoClient("http://g:8090", "test")
        client._initialized = True
        client._client = MagicMock()

        mock_cat = MagicMock()
        mock_table_cat = MagicMock()
        mock_cat.as_table_catalog.return_value = mock_table_cat
        client._client.load_catalog.return_value = mock_cat

        result = client.drop_table("docs")
        assert result is True
        mock_table_cat.purge_table.assert_called_once()

    def test_returns_false_on_failure(self) -> None:
        client = ArrowLakeGravitinoClient("http://g:8090", "test")
        client._initialized = True
        client._client = MagicMock()
        client._client.load_catalog.side_effect = RuntimeError("err")

        assert client.drop_table("docs") is False


# ---------------------------------------------------------------------------
# health
# ---------------------------------------------------------------------------


class TestHealth:
    """Health check via list_metalakes."""

    def test_healthy(self) -> None:
        client = ArrowLakeGravitinoClient("http://g:8090", "test")
        client._initialized = True
        client._client = MagicMock()
        client._admin_client = MagicMock()

        status, ok = client.health()
        assert status == "healthy"
        assert ok is True
        client._admin_client.list_metalakes.assert_called_once()

    def test_unavailable_when_not_initialized(self) -> None:
        client = ArrowLakeGravitinoClient("http://g:8090", "test")
        # _ensure_initialized fails → _client stays None
        client._initialized = True
        client._client = None
        # _admin_client is None so _ensure_initialized returns False

        status, ok = client.health()
        assert status == "unavailable"
        assert ok is False

    def test_unhealthy_on_exception(self) -> None:
        client = ArrowLakeGravitinoClient("http://g:8090", "test")
        client._initialized = True
        client._client = MagicMock()
        client._admin_client = MagicMock()
        client._admin_client.list_metalakes.side_effect = RuntimeError("err")

        status, ok = client.health()
        assert ok is False
        assert "unhealthy" in status


# ---------------------------------------------------------------------------
# GravitinoTableInfo dataclass
# ---------------------------------------------------------------------------


class TestGravitinoTableInfo:
    """Frozen dataclass for table info."""

    def test_creation(self) -> None:
        info = GravitinoTableInfo(
            name="docs",
            catalog="lance-catalog",
            schema="arrow_lake",
            columns=({"name": "id", "type": "integer", "nullable": True},),
            properties=(("format", "lance"),),
        )
        assert info.name == "docs"
        assert len(info.columns) == 1
        assert info.properties == (("format", "lance"),)
