"""Coverage for lineage, quality, and maintenance router endpoints."""

from __future__ import annotations

from dataclasses import dataclass
from unittest.mock import MagicMock, patch

import pyarrow as pa
import pytest
from httpx import ASGITransport, AsyncClient


@dataclass(frozen=True)
class _FakeDedupReport:
    table: pa.Table
    strategy: str = "exact"
    action: str = "flag"
    duplicates_found: int = 0
    duplicates_removed: int = 0


@dataclass
class _FakeQualityReport:
    score: float = 0.9
    total_rows: int = 100
    passed_rows: int = 95
    failed_rows: int = 5

    def to_json(self) -> dict:
        return {"score": self.score, "total_rows": self.total_rows}


def _make_dedup_report(tbl: pa.Table) -> _FakeDedupReport:
    return _FakeDedupReport(table=tbl)


def _make_lake() -> MagicMock:
    lake = MagicMock()
    tbl = pa.table({"id": [1, 2], "name": ["a", "b"]})
    lake.lineage_record_event.return_value = None
    lake.lineage_history.return_value = [
        {"dataset_name": "ds1", "operation": "ingest"},
        {"dataset_name": "ds2", "operation": "transform"},
    ]
    lake.lineage_query.return_value = [{"id": 1, "operation": "ingest"}]
    lake.lineage_graph.return_value = {
        "nodes": [{"id": "ds1", "type": "source"}, {"id": "ds2", "type": "derived"}],
        "edges": [{"from": "ds1", "to": "ds2", "operation": "transform"}],
        "stats": {"total_nodes": 2, "total_edges": 1, "max_depth": 1},
    }
    lake.lineage_impact.return_value = [
        {"dataset": "ds2", "depth": 1, "operation": "transform"},
    ]
    lake.quality_filter.return_value = _FakeQualityReport()
    lake.deduplicate.return_value = _make_dedup_report(tbl)
    lake.read_dataset.return_value = tbl
    return lake


@pytest.fixture
def mock_lake() -> MagicMock:
    return _make_lake()


@pytest.fixture
async def client(mock_lake: MagicMock) -> AsyncClient:
    from arrow_lake.api.app import create_app
    from arrow_lake.config import ArrowLakeConfig

    config = ArrowLakeConfig()
    config.api.api_key = "test-key"
    config.api.api_key_default_role = "ADMIN"
    app = create_app(config)
    app.state.lake = mock_lake
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers={"X-API-Key": "test-key"},
    ) as ac:
        yield ac


# ── Lineage ──


@pytest.mark.asyncio
async def test_lineage_record(client: AsyncClient) -> None:
    resp = await client.post(
        "/api/v1/lineage/record",
        params={"dataset_name": "ds1"},
        json={"operation": "ingest"},
    )
    assert resp.status_code == 200
    assert "recorded" in resp.json()["message"]


@pytest.mark.asyncio
async def test_lineage_history(client: AsyncClient) -> None:
    resp = await client.get("/api/v1/lineage/history/ds1")
    assert resp.status_code == 200
    assert resp.json()["dataset_name"] == "ds1"


@pytest.mark.asyncio
async def test_lineage_query(client: AsyncClient) -> None:
    resp = await client.post(
        "/api/v1/lineage/query",
        json={"sql": "SELECT * FROM lineage WHERE operation='ingest'"},
    )
    assert resp.status_code == 200
    assert len(resp.json()["data"]) == 1


@pytest.mark.asyncio
async def test_lineage_graph_json(client: AsyncClient) -> None:
    resp = await client.get("/api/v1/lineage/graph/ds1")
    assert resp.status_code == 200
    body = resp.json()
    assert "nodes" in body
    assert "edges" in body


@pytest.mark.asyncio
async def test_lineage_graph_mermaid(client: AsyncClient) -> None:
    resp = await client.get("/api/v1/lineage/graph/ds1", params={"format": "mermaid"})
    assert resp.status_code == 200
    assert "graph LR" in resp.text


@pytest.mark.asyncio
async def test_lineage_graph_dot(client: AsyncClient) -> None:
    resp = await client.get("/api/v1/lineage/graph/ds1", params={"format": "dot"})
    assert resp.status_code == 200
    assert "digraph" in resp.text


@pytest.mark.asyncio
async def test_lineage_impact(client: AsyncClient) -> None:
    resp = await client.post(
        "/api/v1/lineage/impact",
        json={"dataset_name": "ds1"},
    )
    assert resp.status_code == 200
    assert len(resp.json()["impacted_datasets"]) == 1


@pytest.mark.asyncio
async def test_lineage_stats(client: AsyncClient) -> None:
    resp = await client.get("/api/v1/lineage/stats")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total_events"] == 2
    assert body["total_datasets_tracked"] == 2


# ── Quality ──


@pytest.mark.asyncio
async def test_quality_filter_endpoint(client: AsyncClient) -> None:
    resp = await client.post(
        "/api/v1/datasets/test/quality/filter",
        json={"active_filters": "null_check", "mode": "all"},
    )
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_quality_report_endpoint(client: AsyncClient) -> None:
    resp = await client.get("/api/v1/datasets/test/quality/report")
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_deduplicate_endpoint(client: AsyncClient) -> None:
    resp = await client.post(
        "/api/v1/datasets/test/quality/deduplicate",
        json={"strategy": "exact", "action": "flag"},
    )
    assert resp.status_code == 200


# ── Maintenance ──


@pytest.mark.asyncio
async def test_maintenance_status_no_scheduler(client: AsyncClient) -> None:
    resp = await client.get("/api/v1/admin/maintenance/status")
    assert resp.status_code == 200
    assert resp.json()["enabled"] is False


@pytest.mark.asyncio
async def test_maintenance_run_no_scheduler(client: AsyncClient) -> None:
    resp = await client.post("/api/v1/admin/maintenance/run")
    assert resp.status_code == 200
    assert resp.json()["success"] is False


@pytest.mark.asyncio
async def test_maintenance_status_with_scheduler(client: AsyncClient) -> None:
    from arrow_lake.ingest.maintenance_scheduler import MaintenanceReport, MaintenanceStatus

    scheduler = MagicMock()
    scheduler.status.return_value = MaintenanceStatus(
        enabled=True, last_run="2025-01-01T00:00:00", next_run="2025-01-01T01:00:00",
        interval_seconds=3600,
        last_report=MaintenanceReport(
            datasets_compacted=1, datasets_cleaned=2,
            total_fragments_before=10, total_fragments_after=5,
            total_versions_removed=3, duration_seconds=1.5,
        ),
    )
    client._transport.app.state.maintenance_scheduler = scheduler

    resp = await client.get("/api/v1/admin/maintenance/status")
    assert resp.status_code == 200
    body = resp.json()
    assert body["enabled"] is True
    assert body["last_report"]["datasets_compacted"] == 1


@pytest.mark.asyncio
async def test_maintenance_status_scheduler_error_never_500s(client: AsyncClient) -> None:
    """W1-3: /status must degrade gracefully — an exception from the scheduler
    (mid-run state, thread teardown, ...) previously escaped as a 500."""
    scheduler = MagicMock()
    scheduler.status.side_effect = RuntimeError("scheduler mid-run teardown")
    client._transport.app.state.maintenance_scheduler = scheduler

    resp = await client.get("/api/v1/admin/maintenance/status")
    assert resp.status_code == 200
    body = resp.json()
    assert body["enabled"] is False
    assert body["error"] == "scheduler mid-run teardown"


@pytest.mark.asyncio
async def test_maintenance_status_malformed_report_never_500s(client: AsyncClient) -> None:
    """W1-3: a report with None fields (failed maintenance run) trips the
    all-int MaintenanceReportModel → ValidationError → 500. Degrade instead."""
    from types import SimpleNamespace

    scheduler = MagicMock()
    scheduler.status.return_value = SimpleNamespace(
        enabled=True, last_run="2025-01-01T00:00:00", next_run="2025-01-01T01:00:00",
        interval_seconds=3600,
        last_report=SimpleNamespace(
            datasets_compacted=None, datasets_cleaned=0,
            total_fragments_before=None, total_fragments_after=0,
            total_versions_removed=0, duration_seconds=0.0,
        ),
    )
    client._transport.app.state.maintenance_scheduler = scheduler

    resp = await client.get("/api/v1/admin/maintenance/status")
    assert resp.status_code == 200
    body = resp.json()
    assert body["enabled"] is True
    assert body["last_report"] is None
    assert body["error"]


@pytest.mark.asyncio
async def test_maintenance_run_with_scheduler(client: AsyncClient) -> None:
    from arrow_lake.ingest.maintenance_scheduler import MaintenanceReport

    scheduler = MagicMock()
    scheduler.run_once.return_value = MaintenanceReport(
        datasets_compacted=2, datasets_cleaned=1,
        total_fragments_before=20, total_fragments_after=10,
        total_versions_removed=5, duration_seconds=2.0,
    )
    client._transport.app.state.maintenance_scheduler = scheduler

    resp = await client.post("/api/v1/admin/maintenance/run")
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    assert body["data"]["datasets_compacted"] == 2


@pytest.mark.asyncio
async def test_maintenance_run_error(client: AsyncClient) -> None:
    scheduler = MagicMock()
    scheduler.run_once.side_effect = RuntimeError("disk full")
    client._transport.app.state.maintenance_scheduler = scheduler

    resp = await client.post("/api/v1/admin/maintenance/run")
    assert resp.status_code == 200
    assert resp.json()["success"] is False
    assert "disk full" in resp.json()["error"]
