"""Tests for embedding and index management endpoints."""

from __future__ import annotations

from dataclasses import dataclass
from unittest.mock import MagicMock

import pytest
from arrow_lake.api.app import create_app
from arrow_lake.config import ArrowLakeConfig
from httpx import ASGITransport, AsyncClient


@dataclass(frozen=True)
class _FakeIndexInfo:
    dataset_name: str = "docs"
    index_type: str = "IVF_PQ"
    metric: str = "cosine"
    vector_column: str = "text_embedding"
    num_partitions: int = 256


@pytest.fixture
def mock_lake() -> MagicMock:
    lake = MagicMock()
    lake.create_vector_index.return_value = _FakeIndexInfo()
    lake.create_fts_index.return_value = None
    return lake


@pytest.fixture
async def client(mock_lake: MagicMock) -> AsyncClient:
    config = ArrowLakeConfig()
    config.api.api_key = "test-api-key"
    config.api.docs_enabled = False
    app = create_app(config=config)
    app.state.lake = mock_lake
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers={"X-API-Key": "test-api-key"},
    ) as ac:
        yield ac


# ---------------------------------------------------------------------------
# Vector index
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_create_vector_index(client: AsyncClient, mock_lake: MagicMock) -> None:
    resp = await client.post(
        "/api/v1/datasets/docs/index/vector",
        json={"metric": "cosine", "vector_column": "text_embedding"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    assert body["index_info"]["metric"] == "cosine"
    assert body["index_info"]["index_type"] == "IVF_PQ"

    mock_lake.create_vector_index.assert_called_once()


@pytest.mark.asyncio
async def test_create_vector_index_custom_params(client: AsyncClient, mock_lake: MagicMock) -> None:
    resp = await client.post(
        "/api/v1/datasets/docs/index/vector",
        json={
            "metric": "l2",
            "vector_column": "image_embedding",
            "index_type": "IVF_HNSW_PQ",
            "num_partitions": 128,
            "num_sub_vectors": 16,
            "replace": False,
        },
    )
    assert resp.status_code == 200

    call_kwargs = mock_lake.create_vector_index.call_args
    assert call_kwargs[1]["metric"] == "l2"
    assert call_kwargs[1]["vector_column"] == "image_embedding"
    assert call_kwargs[1]["index_type"] == "IVF_HNSW_PQ"
    assert call_kwargs[1]["num_partitions"] == 128
    assert call_kwargs[1]["replace"] is False


# ---------------------------------------------------------------------------
# FTS index
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_create_fts_index(client: AsyncClient, mock_lake: MagicMock) -> None:
    resp = await client.post(
        "/api/v1/datasets/docs/index/fts",
        json={"fts_column": "text_content"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    assert "docs" in body["message"]

    mock_lake.create_fts_index.assert_called_once_with(
        "docs", fts_column="text_content", replace=True
    )


@pytest.mark.asyncio
async def test_create_fts_index_default(client: AsyncClient, mock_lake: MagicMock) -> None:
    resp = await client.post(
        "/api/v1/datasets/docs/index/fts",
        json={},
    )
    assert resp.status_code == 200
    mock_lake.create_fts_index.assert_called_once_with(
        "docs", fts_column=None, replace=True
    )


# ---------------------------------------------------------------------------
# Standalone text embedding
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.xfail(reason="LocalEmbeddingEncoder requires model files not available in CI", strict=False)
async def test_embed_text(client: AsyncClient) -> None:
    """POST /api/v1/embed/text computes embeddings."""
    from unittest.mock import patch

    fake_result = MagicMock()
    fake_result.total_rows = 2
    fake_result.null_rows = 0
    fake_result.embedding_dim = 3
    fake_result.vector_column = "text_content_embedding"

    with patch("arrow_lake.embed.encoder.LocalEmbeddingEncoder") as MockEncoder:
        mock_encoder = MagicMock()
        mock_encoder.encode_column.return_value = fake_result
        MockEncoder.return_value = mock_encoder

        resp = await client.post(
            "/api/v1/embed/text",
            json={
                "texts": ["hello", "world"],
                "model": "test-model",
                "model_source": "huggingface",
            },
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    assert body["model"] == "test-model"
    assert body["total"] == 2
    assert body["embedding_dim"] == 3


@pytest.mark.asyncio
async def test_embed_text_empty_list_rejected(client: AsyncClient) -> None:
    resp = await client.post(
        "/api/v1/embed/text",
        json={"texts": [], "model": "test"},
    )
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Standalone image embedding
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.xfail(reason="CLIPImageEncoder requires model files not available in CI", strict=False)
async def test_embed_image(client: AsyncClient) -> None:
    """POST /api/v1/embed/image computes image embeddings."""
    from unittest.mock import patch

    fake_result = MagicMock()
    fake_result.total = 1
    fake_result.null_count = 0
    fake_result.embedding_dim = 512
    fake_result.vector_column = "image_embedding"

    with patch("arrow_lake.embed.image_encoder.CLIPImageEncoder") as MockEncoder:
        mock_encoder = MagicMock()
        mock_encoder.encode.return_value = fake_result
        MockEncoder.return_value = mock_encoder

        resp = await client.post(
            "/api/v1/embed/image",
            json={
                "images": ["data:image/png;base64,AAAA"],
                "model": "openai/clip-vit-base-patch32",
                "model_source": "modelscope",
            },
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    assert body["model"] == "openai/clip-vit-base-patch32"
    assert body["embedding_dim"] == 512
