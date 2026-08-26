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


# --------------------------------------------------------------------------- #
# DR14 W1.3: container table targeting on async ingest
# --------------------------------------------------------------------------- #
def test_bg_ingest_files_threads_table_to_facade() -> None:
    from arrow_lake.api.routers.async_tasks import _bg_ingest_files

    lake = MagicMock()
    lake.ingest.return_value = MagicMock(embed_async=None)
    with (
        patch("arrow_lake.api.routers.datasets._after_ingest_hooks"),
        patch("arrow_lake.api.routers.datasets._save_desc"),
    ):
        _bg_ingest_files(
            MagicMock(), "gas_net", ["/tmp/x.csv"], [], None, lake, "alice",
            None, table="segments",
        )
    kwargs = lake.ingest.call_args.kwargs
    assert kwargs["table"] == "segments"


def test_bg_ingest_files_default_table_is_none() -> None:
    from arrow_lake.api.routers.async_tasks import _bg_ingest_files

    lake = MagicMock()
    lake.ingest.return_value = MagicMock(embed_async=None)
    with (
        patch("arrow_lake.api.routers.datasets._after_ingest_hooks"),
        patch("arrow_lake.api.routers.datasets._save_desc"),
    ):
        _bg_ingest_files(MagicMock(), "plain_ds", ["/tmp/x.csv"], [], None, lake, "alice", None)
    assert lake.ingest.call_args.kwargs["table"] is None


def test_bg_ingest_sql_threads_table_to_facade() -> None:
    from arrow_lake.api.routers.async_tasks import _bg_ingest_sql

    lake = MagicMock()
    lake.ingest_sql.return_value = MagicMock(embed_async=None)
    with (
        patch("arrow_lake.api.routers.datasets._after_ingest_hooks"),
        patch("arrow_lake.api.routers.datasets._save_desc"),
    ):
        _bg_ingest_sql(
            MagicMock(), "gas_net", "SELECT 1", "sqlite://", None, None,
            None, lake, "alice", None, table="stations",
        )
    assert lake.ingest_sql.call_args.kwargs["table"] == "stations"


def test_async_request_models_table_field() -> None:
    """``table`` validates against the identifier pattern (D1/D6 hygiene)."""
    from pydantic import ValidationError

    from arrow_lake.api.routers.async_tasks import AsyncIngestRequest, AsyncIngestSqlRequest

    assert AsyncIngestRequest(file_paths=["/tmp/a.csv"]).table is None
    assert AsyncIngestRequest(file_paths=["/tmp/a.csv"], table="seg_01").table == "seg_01"
    assert AsyncIngestSqlRequest(
        sql="SELECT 1", connection_url="sqlite://", table="stations"
    ).table == "stations"
    with pytest.raises(ValidationError):
        AsyncIngestRequest(file_paths=["/tmp/a.csv"], table="bad/name")
    with pytest.raises(ValidationError):
        AsyncIngestSqlRequest(sql="SELECT 1", connection_url="sqlite://", table="x y")


def test_documents_request_rejects_table_field() -> None:
    """P0-4 (review 2026-08-26): documents ingest has no container-table
    support; a ``table`` key must 422 at validation instead of being
    silently dropped by pydantic's extra-ignore (rows would land on the
    single-table path while the caller believed they targeted a table)."""
    from pydantic import ValidationError

    from arrow_lake.api.models.dataset import IngestDocumentsRequest
    from arrow_lake.api.routers.async_tasks import AsyncDocumentsIngestRequest

    # sanity: the field set still validates normally
    assert IngestDocumentsRequest(blob_keys=["uploads/a.pdf"]).doc_type is None
    with pytest.raises(ValidationError, match="does not support 'table'"):
        IngestDocumentsRequest(blob_keys=["uploads/a.pdf"], table="segments")
    with pytest.raises(ValidationError, match="does not support 'table'"):
        AsyncDocumentsIngestRequest(blob_keys=["uploads/a.pdf"], table="segments")


def test_same_dataset_different_tables_two_tasks_allowed() -> None:
    """D8: the backend imposes NO dataset-level ingest mutex — concurrent
    ingests into different tables of one container must both be dispatchable.
    Pins the contract so a future guard must key on (dataset, table), not
    dataset alone (the console-side guard is the UI concern, W4)."""
    import asyncio

    from arrow_lake.api.routers.async_tasks import _run_ingest_async
    from arrow_lake.api.tasks import TaskManager

    async def _driver():
        bg = MagicMock(return_value=None)
        r1 = _run_ingest_async("gas_net", "ingest", bg, (), None)
        r2 = _run_ingest_async("gas_net", "ingest", bg, (), None)
        await asyncio.sleep(0)  # let spawned background tasks run
        return r1, r2

    r1, r2 = asyncio.run(_driver())
    assert r1.task_id != r2.task_id
    assert r1.task_id in TaskManager._tasks
    assert r2.task_id in TaskManager._tasks


@pytest.mark.anyio()
async def test_endpoints_pass_table_into_bg_args() -> None:
    """The endpoint bg_args tuple carries ``req.table`` into the worker."""
    from arrow_lake.api.routers.async_tasks import (
        AsyncIngestRequest,
        AsyncIngestSqlRequest,
        ingest_files_async,
        ingest_sql_async,
    )

    captured: list[tuple] = []

    def fake_run(name, operation, bg_fn, bg_args, user_id):
        # sync like the real _run_ingest_async — the endpoint returns it as-is
        captured.append((operation, bg_fn, bg_args))
        return MagicMock(task_id="t1", operation=operation, message="ok")

    user = MagicMock()
    user.user_id = 7
    with patch("arrow_lake.api.routers.async_tasks._run_ingest_async", fake_run), \
         patch("arrow_lake.api.routers.async_tasks.authorize_dataset"):
        await ingest_files_async(
            "gas_net",
            req=AsyncIngestRequest(file_paths=["/tmp/a.csv"], table="segments"),
            request=MagicMock(), lake=MagicMock(), _user=user,
        )
        await ingest_sql_async(
            "gas_net",
            req=AsyncIngestSqlRequest(
                sql="SELECT 1", connection_url="sqlite://", table="stations",
            ),
            request=MagicMock(), lake=MagicMock(), _user=user,
        )

    ops = {op: args for op, _, args in captured}
    assert ops["ingest"][-1] == "segments"       # table is the last bg_arg
    assert ops["ingest_sql"][-1] == "stations"
