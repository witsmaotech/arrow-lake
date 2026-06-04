"""Tests for export endpoint."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from unittest.mock import MagicMock

import pytest
from arrow_lake.api.app import create_app
from arrow_lake.config import ArrowLakeConfig
from httpx import ASGITransport, AsyncClient


@dataclass(frozen=True)
class _FakeExportResult:
    dataset_name: str = "docs"
    output_path: str = "/tmp/docs.parquet"
    format: str = "parquet"
    row_count: int = 100
    column_count: int = 5
    file_size_bytes: int = 4096
    version: int = 2


@pytest.fixture
def mock_lake() -> MagicMock:
    lake = MagicMock()
    lake.export.return_value = _FakeExportResult()
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
# Export (async)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_export_parquet(client: AsyncClient, mock_lake: MagicMock) -> None:
    resp = await client.post(
        "/api/v1/datasets/docs/export",
        json={"output_path": "output/docs.parquet"},
    )
    assert resp.status_code == 202
    body = resp.json()
    assert body["success"] is True
    assert body["task_id"] != ""
    assert body["status"] == "pending"

    # Wait for background task to complete
    await asyncio.sleep(0.1)

    mock_lake.export.assert_called_once()


@pytest.mark.asyncio
async def test_export_with_options(client: AsyncClient, mock_lake: MagicMock) -> None:
    resp = await client.post(
        "/api/v1/datasets/docs/export",
        json={
            "output_path": "output/docs.csv",
            "format": "csv",
            "columns": ["id", "text"],
            "compression": "gzip",
            "overwrite": True,
        },
    )
    assert resp.status_code == 202

    # Wait for background task to complete
    await asyncio.sleep(0.1)

    call_kwargs = mock_lake.export.call_args
    assert call_kwargs[1]["format"] == "csv"
    assert call_kwargs[1]["columns"] == ["id", "text"]
    assert call_kwargs[1]["compression"] == "gzip"
    assert call_kwargs[1]["overwrite"] is True


@pytest.mark.asyncio
async def test_export_path_traversal_rejected(client: AsyncClient) -> None:
    resp = await client.post(
        "/api/v1/datasets/docs/export",
        json={"output_path": "../../etc/passwd"},
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_export_absolute_path_rejected(client: AsyncClient) -> None:
    resp = await client.post(
        "/api/v1/datasets/docs/export",
        json={"output_path": "/etc/passwd"},
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_export_empty_path_rejected(client: AsyncClient) -> None:
    resp = await client.post(
        "/api/v1/datasets/docs/export",
        json={"output_path": ""},
    )
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Export task status
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_export_status_after_create(client: AsyncClient, mock_lake: MagicMock) -> None:
    resp = await client.post(
        "/api/v1/datasets/docs/export",
        json={"output_path": "output/docs.parquet"},
    )
    body = resp.json()
    task_id = body["task_id"]

    await asyncio.sleep(0.1)

    status_resp = await client.get(f"/api/v1/datasets/docs/export/{task_id}/status")
    assert status_resp.status_code == 200
    status_body = status_resp.json()
    assert status_body["task_id"] == task_id
    assert status_body["status"] in ("completed", "pending", "running")


@pytest.mark.asyncio
async def test_export_status_not_found(client: AsyncClient) -> None:
    resp = await client.get("/api/v1/datasets/docs/export/nonexistent/status")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Export download
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_export_download_not_found(client: AsyncClient) -> None:
    resp = await client.get("/api/v1/datasets/docs/export/nonexistent/download")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_export_download_before_complete(client: AsyncClient, mock_lake: MagicMock) -> None:
    # Create an export that will never complete (mock without side_effect)
    mock_lake.export.side_effect = asyncio.sleep(10)
    resp = await client.post(
        "/api/v1/datasets/docs/export",
        json={"output_path": "output/docs.parquet"},
    )
    body = resp.json()
    task_id = body["task_id"]

    download_resp = await client.get(f"/api/v1/datasets/docs/export/{task_id}/download")
    assert download_resp.status_code == 400


# ---------------------------------------------------------------------------
# Download endpoint additional coverage (lines 95-110)
# ---------------------------------------------------------------------------


@pytest.fixture
async def download_client(tmp_path: asyncio.Event) -> AsyncClient:  # type: ignore[override]
    """Client with a pre-seeded completed task pointing to a temp file."""
    config = ArrowLakeConfig()
    config.api.api_key = "test-api-key"
    config.api.docs_enabled = False
    app = create_app(config=config)
    app.state.lake = MagicMock()
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers={"X-API-Key": "test-api-key"},
    ) as ac:
        yield ac


class TestDownloadExportCoverage:
    """Coverage for download_export lines 95-110."""

    @pytest.fixture
    def _seed_completed_task(
        self, tmp_path: str, monkeypatch: pytest.MonkeyPatch
    ) -> tuple[str, str]:
        """Create a completed ExportTask and write a temp file.

        Returns (task_id, relative_output_path).
        """
        from arrow_lake.api.tasks import ExportTask, TaskManager, TaskStatus

        rel_path = "results/docs.parquet"
        abs_file = tmp_path / rel_path
        abs_file.parent.mkdir(parents=True, exist_ok=True)
        abs_file.write_bytes(b"PARQUET-DATA")

        task_id = "abcd1234efgh5678"
        TaskManager._tasks[task_id] = ExportTask(
            task_id=task_id,
            dataset_name="docs",
            output_path=rel_path,
            fmt="parquet",
            status=TaskStatus.COMPLETED,
            progress=1.0,
            created_at="2026-06-04T00:00:00+00:00",
            completed_at="2026-06-04T00:00:01+00:00",
        )

        # Patch get_config to return base_dir = tmp_path
        mock_cfg = MagicMock()
        mock_cfg.export.base_dir = str(tmp_path)

        import arrow_lake.api.deps as _deps_mod

        monkeypatch.setattr(_deps_mod, "get_config", lambda: mock_cfg)
        return task_id, rel_path

    @pytest.mark.asyncio
    async def test_download_task_not_found(
        self, download_client: AsyncClient, tmp_path: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """GET /download with nonexistent task_id returns 404."""
        import arrow_lake.api.deps as _deps_mod
        mock_cfg = MagicMock()
        monkeypatch.setattr(_deps_mod, "get_config", lambda: mock_cfg)

        resp = await download_client.get(
            "/api/v1/datasets/docs/export/nonexistent_task/download"
        )
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_download_task_wrong_dataset(
        self, download_client: AsyncClient, tmp_path: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """GET /download with mismatched dataset name returns 404."""
        import arrow_lake.api.deps as _deps_mod
        from arrow_lake.api.tasks import ExportTask, TaskManager, TaskStatus

        mock_cfg = MagicMock()
        monkeypatch.setattr(_deps_mod, "get_config", lambda: mock_cfg)

        TaskManager._tasks["task_x"] = ExportTask(
            task_id="task_x",
            dataset_name="other_dataset",
            output_path="file.parquet",
            fmt="parquet",
            status=TaskStatus.COMPLETED,
        )

        resp = await download_client.get(
            "/api/v1/datasets/docs/export/task_x/download"
        )
        assert resp.status_code == 404
        assert "not found" in resp.json()["detail"].lower()

    @pytest.mark.asyncio
    async def test_download_task_not_completed(
        self, download_client: AsyncClient, tmp_path: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """GET /download with pending task returns 400."""
        import arrow_lake.api.deps as _deps_mod
        from arrow_lake.api.tasks import ExportTask, TaskManager, TaskStatus

        mock_cfg = MagicMock()
        monkeypatch.setattr(_deps_mod, "get_config", lambda: mock_cfg)

        TaskManager._tasks["task_pending"] = ExportTask(
            task_id="task_pending",
            dataset_name="docs",
            output_path="file.parquet",
            fmt="parquet",
            status=TaskStatus.PENDING,
        )

        resp = await download_client.get(
            "/api/v1/datasets/docs/export/task_pending/download"
        )
        assert resp.status_code == 400
        assert "not completed" in resp.json()["detail"]

    @pytest.mark.asyncio
    async def test_download_absolute_path_rejected(
        self, download_client: AsyncClient, tmp_path: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """GET /download with absolute output_path returns 400."""
        import arrow_lake.api.deps as _deps_mod
        from arrow_lake.api.tasks import ExportTask, TaskManager, TaskStatus

        mock_cfg = MagicMock()
        monkeypatch.setattr(_deps_mod, "get_config", lambda: mock_cfg)

        TaskManager._tasks["task_abs"] = ExportTask(
            task_id="task_abs",
            dataset_name="docs",
            output_path="/etc/passwd",
            fmt="parquet",
            status=TaskStatus.COMPLETED,
        )

        resp = await download_client.get(
            "/api/v1/datasets/docs/export/task_abs/download"
        )
        assert resp.status_code == 400
        assert "absolute" in resp.json()["detail"].lower()

    @pytest.mark.asyncio
    async def test_download_path_traversal_rejected(
        self, download_client: AsyncClient, tmp_path: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """GET /download with path traversal returns 403."""
        import arrow_lake.api.deps as _deps_mod
        from arrow_lake.api.tasks import ExportTask, TaskManager, TaskStatus

        mock_cfg = MagicMock()
        mock_cfg.export.base_dir = str(tmp_path)
        monkeypatch.setattr(_deps_mod, "get_config", lambda: mock_cfg)

        TaskManager._tasks["task_trav"] = ExportTask(
            task_id="task_trav",
            dataset_name="docs",
            output_path="../../etc/passwd",
            fmt="parquet",
            status=TaskStatus.COMPLETED,
        )

        resp = await download_client.get(
            "/api/v1/datasets/docs/export/task_trav/download"
        )
        assert resp.status_code == 403
        assert "escapes" in resp.json()["detail"].lower()

    @pytest.mark.asyncio
    async def test_download_file_not_on_disk(
        self, download_client: AsyncClient, tmp_path: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """GET /download with non-existent file returns 404."""
        import arrow_lake.api.deps as _deps_mod
        from arrow_lake.api.tasks import ExportTask, TaskManager, TaskStatus

        mock_cfg = MagicMock()
        mock_cfg.export.base_dir = str(tmp_path)
        monkeypatch.setattr(_deps_mod, "get_config", lambda: mock_cfg)

        TaskManager._tasks["task_nofile"] = ExportTask(
            task_id="task_nofile",
            dataset_name="docs",
            output_path="missing/file.parquet",
            fmt="parquet",
            status=TaskStatus.COMPLETED,
        )

        resp = await download_client.get(
            "/api/v1/datasets/docs/export/task_nofile/download"
        )
        assert resp.status_code == 404
        assert "not found on disk" in resp.json()["detail"].lower()

    @pytest.mark.asyncio
    async def test_download_parquet_success(
        self, download_client: AsyncClient, tmp_path: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """GET /download returns 200 for completed parquet export."""
        import arrow_lake.api.deps as _deps_mod
        from arrow_lake.api.tasks import ExportTask, TaskManager, TaskStatus

        rel_path = "results/docs.parquet"
        abs_file = tmp_path / rel_path
        abs_file.parent.mkdir(parents=True, exist_ok=True)
        abs_file.write_bytes(b"PARQUET-FAKE")

        mock_cfg = MagicMock()
        mock_cfg.export.base_dir = str(tmp_path)
        monkeypatch.setattr(_deps_mod, "get_config", lambda: mock_cfg)

        TaskManager._tasks["task_ok_parquet"] = ExportTask(
            task_id="task_ok_parquet",
            dataset_name="docs",
            output_path=rel_path,
            fmt="parquet",
            status=TaskStatus.COMPLETED,
            progress=1.0,
            completed_at="2026-06-04T00:00:01+00:00",
        )

        resp = await download_client.get(
            "/api/v1/datasets/docs/export/task_ok_parquet/download"
        )
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "application/octet-stream"
        assert resp.headers["content-disposition"] == 'attachment; filename="docs.parquet"'
        assert resp.content == b"PARQUET-FAKE"

    @pytest.mark.asyncio
    async def test_download_csv_success(
        self, download_client: AsyncClient, tmp_path: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """GET /download returns 200 for completed CSV export with text/csv."""
        import arrow_lake.api.deps as _deps_mod
        from arrow_lake.api.tasks import ExportTask, TaskManager, TaskStatus

        rel_path = "results/docs.csv"
        abs_file = tmp_path / rel_path
        abs_file.parent.mkdir(parents=True, exist_ok=True)
        abs_file.write_bytes(b"id,name\n1,test\n")

        mock_cfg = MagicMock()
        mock_cfg.export.base_dir = str(tmp_path)
        monkeypatch.setattr(_deps_mod, "get_config", lambda: mock_cfg)

        TaskManager._tasks["task_ok_csv"] = ExportTask(
            task_id="task_ok_csv",
            dataset_name="docs",
            output_path=rel_path,
            fmt="csv",
            status=TaskStatus.COMPLETED,
            progress=1.0,
            completed_at="2026-06-04T00:00:01+00:00",
        )

        resp = await download_client.get(
            "/api/v1/datasets/docs/export/task_ok_csv/download"
        )
        assert resp.status_code == 200
        assert "text/csv" in resp.headers["content-type"]
        assert resp.headers["content-disposition"] == 'attachment; filename="docs.csv"'
        assert b"id,name" in resp.content
