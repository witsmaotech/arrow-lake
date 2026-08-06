"""Tests for async ingest background workers (P2).

Verifies the description-race fix: ``description`` is persisted inside the
background worker AFTER the dataset exists (via ``_save_desc``), not by a
separate client ``PUT /description`` that raced dataset creation and 422'd.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture(autouse=True)
def _clear_tasks():
    from arrow_lake.api.tasks import TaskManager

    TaskManager._tasks.clear()
    yield
    TaskManager._tasks.clear()


def test_finalize_persists_description_after_hooks() -> None:
    """_finalize_ingest runs hooks then writes description via _save_desc."""
    from arrow_lake.api.routers.async_tasks import _finalize_ingest

    with (
        patch("arrow_lake.api.routers.datasets._after_ingest_hooks") as hooks,
        patch("arrow_lake.api.routers.datasets._save_desc") as save,
    ):
        app_state, lake = MagicMock(), MagicMock()
        _finalize_ingest(app_state, "ds", lake, "a description")
        hooks.assert_called_once_with(app_state, "ds", lake)
        save.assert_called_once_with("ds", "a description")


def test_finalize_skips_save_when_no_description() -> None:
    from arrow_lake.api.routers.async_tasks import _finalize_ingest

    with (
        patch("arrow_lake.api.routers.datasets._after_ingest_hooks"),
        patch("arrow_lake.api.routers.datasets._save_desc") as save,
    ):
        _finalize_ingest(MagicMock(), "ds", MagicMock(), None)
        save.assert_not_called()


def test_bg_ingest_sql_calls_facade_then_persists_description() -> None:
    """The bg worker ingests, THEN persists description (race-fix contract)."""
    from arrow_lake.api.routers.async_tasks import _bg_ingest_sql

    lake = MagicMock()
    lake.ingest_sql.return_value = MagicMock(embed_async=None)
    with (
        patch("arrow_lake.api.routers.datasets._after_ingest_hooks"),
        patch("arrow_lake.api.routers.datasets._save_desc") as save,
    ):
        _bg_ingest_sql(
            MagicMock(), "ds", "SELECT 1", "sqlite://", None, None,
            None, lake, "alice", "my desc",
        )
    lake.ingest_sql.assert_called_once()
    kwargs = lake.ingest_sql.call_args.kwargs
    assert kwargs["sql"] == "SELECT 1"
    assert kwargs["connection_url"] == "sqlite://"
    assert kwargs["actor"] == "alice"
    save.assert_called_once_with("ds", "my desc")


def test_bg_ingest_http_no_transforms_path() -> None:
    """http worker has no transforms/tmp_dir — covers the minimal path."""
    from arrow_lake.api.routers.async_tasks import _bg_ingest_http

    lake = MagicMock()
    with (
        patch("arrow_lake.api.routers.datasets._after_ingest_hooks"),
        patch("arrow_lake.api.routers.datasets._save_desc"),
    ):
        _bg_ingest_http(MagicMock(), "ds", ["https://example.com/a.csv"], lake, "alice", None)
    lake.ingest_http.assert_called_once_with("ds", ["https://example.com/a.csv"], actor="alice")


def test_async_request_models_inherit_validators() -> None:
    """Async subclasses inherit the sync security validators (SSRF/traversal)."""
    from arrow_lake.api.routers.async_tasks import (
        AsyncIngestHttpRequest,
        AsyncIngestRequest,
        AsyncIngestSqlRequest,
    )

    # traversal rejected (inherited from IngestFilesRequest)
    with pytest.raises(Exception):
        AsyncIngestRequest(blob_keys=["../etc/passwd"])
    # SSRF rejected (inherited from IngestHttpRequest)
    with pytest.raises(Exception):
        AsyncIngestHttpRequest(urls=["http://127.0.0.1/x"])
    # description field present
    assert AsyncIngestSqlRequest(sql="SELECT 1", connection_url="sqlite://").description is None
