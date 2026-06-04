"""Tests for quality filtering and deduplication endpoints."""

from __future__ import annotations

from dataclasses import dataclass, field
from unittest.mock import MagicMock, patch

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


# ---------------------------------------------------------------------------
# Quality rules — POST /{name}/quality/rules
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_quality_rules_length_check(client: AsyncClient, mock_lake: MagicMock) -> None:
    """POST /quality/rules applies length check and returns results."""
    mock_lake.read_dataset.return_value = pa.table({
        "text_content": ["hello", "hi", "world", "a"],
    })

    resp = await client.post(
        "/api/v1/datasets/docs/quality/rules",
        json={
            "rules": [{
                "name": "reject_short",
                "column": "text_content",
                "check": "length",
                "params": {"min": 3},
                "action": "reject",
            }],
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    assert body["applied_rules"] == 1
    assert len(body["results"]) == 1
    assert body["results"][0]["rule_name"] == "reject_short"
    assert body["results"][0]["affected_count"] == 2
    assert body["total_affected_rows"] == 2

    mock_lake.read_dataset.assert_called_once_with("docs")


@pytest.mark.asyncio
async def test_quality_rules_range_check(client: AsyncClient, mock_lake: MagicMock) -> None:
    mock_lake.read_dataset.return_value = pa.table({
        "score": [1.0, 5.0, 10.0, 15.0],
    })

    resp = await client.post(
        "/api/v1/datasets/docs/quality/rules",
        json={
            "rules": [{
                "name": "score_range",
                "column": "score",
                "check": "range",
                "params": {"min": 0, "max": 10},
                "action": "flag",
            }],
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["applied_rules"] == 1
    assert body["results"][0]["affected_count"] == 1


@pytest.mark.asyncio
async def test_quality_rules_duplicate_check(client: AsyncClient, mock_lake: MagicMock) -> None:
    mock_lake.read_dataset.return_value = pa.table({
        "text_content": ["hello", "world", "hello"],
    })

    resp = await client.post(
        "/api/v1/datasets/docs/quality/rules",
        json={
            "rules": [{
                "name": "dedup",
                "column": "text_content",
                "check": "duplicate",
                "action": "remove",
            }],
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["results"][0]["affected_count"] == 1


@pytest.mark.asyncio
async def test_quality_rules_multiple_rules(client: AsyncClient, mock_lake: MagicMock) -> None:
    mock_lake.read_dataset.return_value = pa.table({
        "text_content": ["short", "long enough text"],
        "score": [5.0, 50.0],
    })

    resp = await client.post(
        "/api/v1/datasets/docs/quality/rules",
        json={
            "rules": [
                {
                    "name": "min_text",
                    "column": "text_content",
                    "check": "length",
                    "params": {"min": 10},
                    "action": "reject",
                },
                {
                    "name": "max_score",
                    "column": "score",
                    "check": "range",
                    "params": {"max": 20},
                    "action": "flag",
                },
            ],
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["applied_rules"] == 2
    assert len(body["results"]) == 2
    assert body["total_affected_rows"] == 2


@pytest.mark.asyncio
async def test_quality_rules_no_violations(client: AsyncClient, mock_lake: MagicMock) -> None:
    mock_lake.read_dataset.return_value = pa.table({
        "text_content": ["hello", "world"],
    })

    resp = await client.post(
        "/api/v1/datasets/docs/quality/rules",
        json={
            "rules": [{
                "name": "min_len",
                "column": "text_content",
                "check": "length",
                "params": {"min": 3},
                "action": "reject",
            }],
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["applied_rules"] == 1
    assert body["results"] == []
    assert body["total_affected_rows"] == 0


@pytest.mark.asyncio
async def test_quality_rules_custom_message(client: AsyncClient, mock_lake: MagicMock) -> None:
    mock_lake.read_dataset.return_value = pa.table({
        "text_content": ["ab"],
    })

    resp = await client.post(
        "/api/v1/datasets/docs/quality/rules",
        json={
            "rules": [{
                "name": "min_len",
                "column": "text_content",
                "check": "length",
                "params": {"min": 5},
                "action": "flag",
                "message": "Text too short (min={min} chars)",
            }],
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert "min=5" in body["results"][0]["message"]


@pytest.mark.asyncio
async def test_quality_rules_validation_invalid_check(client: AsyncClient, mock_lake: MagicMock) -> None:
    resp = await client.post(
        "/api/v1/datasets/docs/quality/rules",
        json={
            "rules": [{
                "name": "bad",
                "column": "col",
                "check": "invalid_type",
                "action": "flag",
            }],
        },
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_quality_rules_validation_invalid_action(client: AsyncClient, mock_lake: MagicMock) -> None:
    resp = await client.post(
        "/api/v1/datasets/docs/quality/rules",
        json={
            "rules": [{
                "name": "bad",
                "column": "col",
                "check": "length",
                "action": "destroy",
            }],
        },
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_quality_rules_validation_empty_rules(client: AsyncClient, mock_lake: MagicMock) -> None:
    resp = await client.post(
        "/api/v1/datasets/docs/quality/rules",
        json={"rules": []},
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_quality_rules_validation_missing_name(client: AsyncClient, mock_lake: MagicMock) -> None:
    resp = await client.post(
        "/api/v1/datasets/docs/quality/rules",
        json={
            "rules": [{"column": "col", "check": "length"}],
        },
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_quality_rules_requires_auth(mock_lake: MagicMock) -> None:
    config = ArrowLakeConfig()
    config.api.api_key = "test-api-key"
    config.api.docs_enabled = False
    app = create_app(config=config)
    app.state.lake = mock_lake

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as ac:
        resp = await ac.post(
            "/api/v1/datasets/docs/quality/rules",
            json={
                "rules": [{
                    "name": "r1",
                    "column": "col",
                    "check": "length",
                }],
            },
        )
    assert resp.status_code in (401, 403)


# ---------------------------------------------------------------------------
# Quality profile — GET /{name}/quality/profile (lines 130-175)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_quality_profile_with_histogram(client: AsyncClient, mock_lake: MagicMock) -> None:
    """Profile endpoint returns column stats with histogram for numeric columns."""
    from dataclasses import dataclass
    from datetime import UTC, datetime

    @dataclass(frozen=True)
    class _FakeColumnProfile:
        name: str
        dtype: str
        null_count: int
        null_percentage: float
        unique_count: int
        min_value: object
        max_value: object
        histogram: tuple[dict, ...] | None

    @dataclass(frozen=True)
    class _FakeProfile:
        dataset_name: str
        total_rows: int
        total_columns: int
        overall_quality_score: float
        column_profiles: tuple[_FakeColumnProfile, ...]
        profiled_at: str

    fake_col = _FakeColumnProfile(
        name="score",
        dtype="int64",
        null_count=2,
        null_percentage=2.0,
        unique_count=48,
        min_value=0,
        max_value=100,
        histogram=(
            {"lower": 0.0, "upper": 10.0, "count": 15},
            {"lower": 10.0, "upper": 20.0, "count": 22},
        ),
    )
    fake_profile = _FakeProfile(
        dataset_name="docs",
        total_rows=100,
        total_columns=1,
        overall_quality_score=0.98,
        column_profiles=(fake_col,),
        profiled_at=datetime.now(tz=UTC).isoformat(),
    )

    mock_profiler = MagicMock()
    mock_profiler.profile.return_value = fake_profile

    mock_lake.read_dataset.return_value = pa.table({"score": [10, 20, 30]})

    with patch(
        "arrow_lake.quality.profiler.QualityProfiler",
        return_value=mock_profiler,
    ):
        resp = await client.get("/api/v1/datasets/docs/quality/profile")

    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    assert body["error"] is None
    assert body["data"]["dataset_name"] == "docs"
    assert body["data"]["total_rows"] == 100
    assert len(body["data"]["columns"]) == 1
    col = body["data"]["columns"][0]
    assert col["name"] == "score"
    assert col["null_count"] == 2
    assert col["histogram"] == [
        {"lower": 0.0, "upper": 10.0, "count": 15},
        {"lower": 10.0, "upper": 20.0, "count": 22},
    ]


@pytest.mark.asyncio
async def test_quality_profile_without_histogram(client: AsyncClient, mock_lake: MagicMock) -> None:
    """Profile endpoint omits histogram key for string columns."""
    from dataclasses import dataclass
    from datetime import UTC, datetime

    @dataclass(frozen=True)
    class _FakeColumnProfile:
        name: str
        dtype: str
        null_count: int
        null_percentage: float
        unique_count: int
        min_value: object
        max_value: object
        histogram: tuple[dict, ...] | None

    @dataclass(frozen=True)
    class _FakeProfile:
        dataset_name: str
        total_rows: int
        total_columns: int
        overall_quality_score: float
        column_profiles: tuple[_FakeColumnProfile, ...]
        profiled_at: str

    fake_col = _FakeColumnProfile(
        name="text_content",
        dtype="string",
        null_count=0,
        null_percentage=0.0,
        unique_count=80,
        min_value=None,
        max_value=None,
        histogram=None,
    )
    fake_profile = _FakeProfile(
        dataset_name="docs",
        total_rows=80,
        total_columns=1,
        overall_quality_score=1.0,
        column_profiles=(fake_col,),
        profiled_at=datetime.now(tz=UTC).isoformat(),
    )

    mock_profiler = MagicMock()
    mock_profiler.profile.return_value = fake_profile

    mock_lake.read_dataset.return_value = pa.table({
        "text_content": ["hello", "world"],
    })

    with patch(
        "arrow_lake.quality.profiler.QualityProfiler",
        return_value=mock_profiler,
    ):
        resp = await client.get("/api/v1/datasets/docs/quality/profile")

    assert resp.status_code == 200
    body = resp.json()
    col = body["data"]["columns"][0]
    assert col["name"] == "text_content"
    assert "histogram" not in col


@pytest.mark.asyncio
async def test_quality_profile_empty_columns(client: AsyncClient, mock_lake: MagicMock) -> None:
    """Profile endpoint handles empty column list gracefully."""
    from dataclasses import dataclass
    from datetime import UTC, datetime

    @dataclass(frozen=True)
    class _FakeProfile:
        dataset_name: str
        total_rows: int
        total_columns: int
        overall_quality_score: float
        column_profiles: tuple
        profiled_at: str

    fake_profile = _FakeProfile(
        dataset_name="empty_ds",
        total_rows=0,
        total_columns=0,
        overall_quality_score=0.0,
        column_profiles=(),
        profiled_at=datetime.now(tz=UTC).isoformat(),
    )

    mock_profiler = MagicMock()
    mock_profiler.profile.return_value = fake_profile

    mock_lake.read_dataset.return_value = pa.table({})

    with patch(
        "arrow_lake.quality.profiler.QualityProfiler",
        return_value=mock_profiler,
    ):
        resp = await client.get("/api/v1/datasets/empty_ds/quality/profile")

    assert resp.status_code == 200
    body = resp.json()
    assert body["data"]["total_columns"] == 0
    assert body["data"]["columns"] == []
