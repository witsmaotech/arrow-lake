"""Router-level tests for /ingest/documents post-ingest embed + FTS index (v1.8.9).

The documents endpoint must, after writing text_content, (1) embed → text_embedding
and (2) build the FTS index, so the dataset is FTS/vector/RAG searchable. Both are
best-effort: ingest succeeds even if the embedder or FTS index build fails.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from arrow_lake.api.routers import datasets as ds_mod
from arrow_lake.api.models.dataset import IngestDocumentsRequest


@pytest.fixture
def _patch_endpoint(monkeypatch: pytest.MonkeyPatch):
    """run_sync → async passthrough; stub _after_ingest_hooks + from_report."""
    async def fake_run_sync(fn, *a, **k):
        return fn(*a, **k)

    monkeypatch.setattr(ds_mod, "run_sync", fake_run_sync)
    monkeypatch.setattr(ds_mod, "_after_ingest_hooks", lambda *a, **k: None)
    sentinel = MagicMock(name="IngestResponse")
    monkeypatch.setattr(ds_mod.IngestResponse, "from_report", lambda report: sentinel)
    return sentinel


@pytest.mark.asyncio
async def test_ingest_documents_calls_embed_and_fts_after_write(_patch_endpoint) -> None:
    lake = MagicMock()
    lake.ingest_documents = MagicMock(return_value=MagicMock(name="report"))
    lake.embed_and_add = MagicMock()
    lake.create_fts_index = MagicMock()
    req = IngestDocumentsRequest(pdf_paths=["x.md"])

    resp = await ds_mod.ingest_documents(
        request=MagicMock(), name="ds", req=req, lake=lake, _user={},
    )

    assert resp is _patch_endpoint
    lake.ingest_documents.assert_called_once()
    lake.embed_and_add.assert_called_once()
    assert lake.embed_and_add.call_args.args[0] == "ds"
    lake.create_fts_index.assert_called_once()
    assert lake.create_fts_index.call_args.args[0] == "ds"


@pytest.mark.asyncio
async def test_embed_failure_does_not_block_ingest_or_fts(_patch_endpoint) -> None:
    lake = MagicMock()
    lake.ingest_documents = MagicMock(return_value=MagicMock(name="report"))
    lake.embed_and_add = MagicMock(side_effect=RuntimeError("embedder down"))
    lake.create_fts_index = MagicMock()
    req = IngestDocumentsRequest(pdf_paths=["x.md"])

    resp = await ds_mod.ingest_documents(
        request=MagicMock(), name="ds", req=req, lake=lake, _user={},
    )

    assert resp is _patch_endpoint            # ingest still succeeded
    lake.create_fts_index.assert_called_once()  # FTS index still attempted


@pytest.mark.asyncio
async def test_fts_failure_does_not_block_ingest(_patch_endpoint) -> None:
    lake = MagicMock()
    lake.ingest_documents = MagicMock(return_value=MagicMock(name="report"))
    lake.embed_and_add = MagicMock()
    lake.create_fts_index = MagicMock(side_effect=RuntimeError("fts build failed"))
    req = IngestDocumentsRequest(pdf_paths=["x.md"])

    resp = await ds_mod.ingest_documents(
        request=MagicMock(), name="ds", req=req, lake=lake, _user={},
    )

    assert resp is _patch_endpoint            # ingest still succeeded despite FTS failure
