"""Router-level tests for /ingest/documents (v1.8.x 架构评审 #4 之后的契约).

The parse→store→embed→FTS→vector sequence is consolidated in the facade
(``lake.ingest_documents_and_index`` — its best-effort semantics are covered by
tests/unit/api/test_ingest_documents_and_index.py). This file pins the ROUTE
contract: delegation with doc_config, the HTTP-layer post-hooks
(gravitino registration + query-cache invalidation), and error propagation.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from arrow_lake.api.routers import datasets as ds_mod
from arrow_lake.api.models.dataset import IngestDocumentsRequest


def _authed_request() -> MagicMock:
    """Request 伪对象:EDITOR user + allow-all checker(v1.10.7 WP1 守卫契约)。"""
    from arrow_lake.api.auth_models import Role

    req = MagicMock()
    req.state.user = SimpleNamespace(role=Role.EDITOR, sub="1", user_id=1)
    checker = MagicMock()
    checker.check_dataset_access.return_value = True
    req.app.state.checker = checker
    return req


@pytest.fixture
def _patch_endpoint(monkeypatch: pytest.MonkeyPatch):
    """run_sync → async passthrough; stub from_report so the sentinel proves wiring."""
    async def fake_run_sync(fn, *a, **k):
        return fn(*a, **k)

    monkeypatch.setattr(ds_mod, "run_sync", fake_run_sync)
    sentinel = MagicMock(name="IngestResponse")
    monkeypatch.setattr(ds_mod.IngestResponse, "from_report", lambda report: sentinel)
    return sentinel


@pytest.mark.asyncio
async def test_ingest_documents_delegates_to_facade(_patch_endpoint) -> None:
    """Route calls ingest_documents_and_index with doc_config and wraps the report."""
    lake = MagicMock()
    lake._config.document = MagicMock(name="doc_config")
    req = IngestDocumentsRequest(pdf_paths=["x.md"])

    resp = await ds_mod.ingest_documents(
        request=_authed_request(), name="ds", req=req, lake=lake, _user={},
    )

    assert resp is _patch_endpoint
    lake.ingest_documents_and_index.assert_called_once()
    call = lake.ingest_documents_and_index.call_args
    assert call.args[:2] == ("ds", ["x.md"])
    assert call.kwargs["doc_config"] is lake._config.document


@pytest.mark.asyncio
async def test_ingest_documents_runs_http_layer_hooks(_patch_endpoint, monkeypatch: pytest.MonkeyPatch) -> None:
    """_after_ingest_hooks (gravitino + cache invalidate) runs after a successful ingest."""
    hook_calls: list[tuple] = []
    monkeypatch.setattr(
        ds_mod, "_after_ingest_hooks",
        lambda app_state, ds, lake: hook_calls.append((app_state, ds, lake)),
    )
    lake = MagicMock()
    req = IngestDocumentsRequest(pdf_paths=["x.md"])
    request = _authed_request()

    await ds_mod.ingest_documents(
        request=request, name="ds", req=req, lake=lake, _user={},
    )

    assert hook_calls == [(request.app.state, "ds", lake)]


@pytest.mark.asyncio
async def test_facade_failure_propagates(_patch_endpoint) -> None:
    """A facade-level ingest failure surfaces — the route does not swallow it."""
    lake = MagicMock()
    lake.ingest_documents_and_index = MagicMock(side_effect=RuntimeError("parse failed"))
    req = IngestDocumentsRequest(pdf_paths=["x.md"])

    with pytest.raises(RuntimeError, match="parse failed"):
        await ds_mod.ingest_documents(
            request=_authed_request(), name="ds", req=req, lake=lake, _user={},
        )
