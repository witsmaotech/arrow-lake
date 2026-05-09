"""Tests for quality filtering and deduplication endpoints."""

from __future__ import annotations

from dataclasses import dataclass, field
from unittest.mock import MagicMock

import pyarrow as pa
import pytest
from arrow_lake.api.app import create_app
from arrow_lake.config import ArrowLakeConfig
from httpx import ASGITransport, AsyncClient


@dataclass(frozen=True)
class _FakeQualityReport:
    total_rows: int = 100
    passed: int = 90
    failed: int = 10
    filters_run: tuple = ("text_length",)


@dataclass(frozen=True)
class _FakeDedupReport:
    total_rows: int = 100
    unique_rows: int = 95
    duplicates: int = 5
    strategy: str = "exact"
    table: pa.Table = field(
        default_factory=lambda: pa.table({
            "id": ["1", "2"],
            "text_content": ["hello", "world"],
            "_duplicate": [False, True],
        })
    )


@pytest.fixture
def mock_lake() -> MagicMock:
    lake = MagicMock()
    lake.quality_filter.return_value = _FakeQualityReport()
    lake.deduplicate.return_value = _FakeDedupReport()
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
# Quality filter
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_quality_filter(client: AsyncClient, mock_lake: MagicMock) -> None:
    resp = await client.post(
        "/api/v1/datasets/docs/quality/filter",
        json={"active_filters": "text_length", "mode": "all"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    assert body["report"]["total_rows"] == 100
    assert body["report"]["passed"] == 90

    mock_lake.quality_filter.assert_called_once_with(
        "docs", "text_length", mode="all"
    )


@pytest.mark.asyncio
async def test_quality_filter_default_mode(client: AsyncClient, mock_lake: MagicMock) -> None:
    resp = await client.post(
        "/api/v1/datasets/docs/quality/filter",
        json={},
    )
    assert resp.status_code == 200
    mock_lake.quality_filter.assert_called_once_with("docs", "", mode="all")


@pytest.mark.asyncio
async def test_quality_filter_invalid_mode(client: AsyncClient) -> None:
    resp = await client.post(
        "/api/v1/datasets/docs/quality/filter",
        json={"mode": "invalid"},
    )
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Deduplication
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_deduplicate(client: AsyncClient, mock_lake: MagicMock) -> None:
    resp = await client.post(
        "/api/v1/datasets/docs/quality/deduplicate",
        json={"strategy": "exact", "action": "flag"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    assert body["report"]["duplicates"] == 5

    mock_lake.deduplicate.assert_called_once_with(
        "docs", strategy="exact", action="flag", perceptual_threshold=None
    )


@pytest.mark.asyncio
async def test_deduplicate_perceptual(client: AsyncClient, mock_lake: MagicMock) -> None:
    resp = await client.post(
        "/api/v1/datasets/docs/quality/deduplicate",
        json={"strategy": "perceptual", "action": "remove", "perceptual_threshold": 15},
    )
    assert resp.status_code == 200
    mock_lake.deduplicate.assert_called_once_with(
        "docs", strategy="perceptual", action="remove", perceptual_threshold=15
    )


# ---------------------------------------------------------------------------
# Quality report
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_quality_report(client: AsyncClient, mock_lake: MagicMock) -> None:
    """GET /api/v1/datasets/{name}/quality/report returns quality report."""
    from dataclasses import dataclass

    @dataclass
    class _FakeReport:
        def to_json(self):
            return {
                "total_rows": 100,
                "passed_rows": 90,
                "rejected_rows": 10,
                "overall_pass_rate_percentage": 90.0,
                "per_filter": [],
            }

    mock_lake.quality_filter.return_value = _FakeReport()

    resp = await client.get("/api/v1/datasets/docs/quality/report")
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    assert body["report"]["total_rows"] == 100
    assert body["report"]["passed_rows"] == 90

    mock_lake.quality_filter.assert_called_once_with("docs")
