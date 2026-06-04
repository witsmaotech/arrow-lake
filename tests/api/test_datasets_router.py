"""Coverage for remaining dataset router endpoints — ingest variants, uploads, schema migration."""

from __future__ import annotations

from dataclasses import dataclass
from unittest.mock import MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient


@dataclass(frozen=True)
class _FakeSource:
    path: str
    row_count: int
    file_count: int = 1


@dataclass(frozen=True)
class _FakeReport:
    sources: tuple = ()
    total_rows: int = 0
    total_files: int = 0


@dataclass(frozen=True)
class _FakeEntry:
    name: str
    version: int
    num_rows: int


@dataclass(frozen=True)
class _FakeCatalogResult:
    datasets: list = ()
    total: int = 0


def _make_lake() -> MagicMock:
    lake = MagicMock()
    lake.catalog.return_value = _FakeCatalogResult(
        datasets=[_FakeEntry(name="test", version=1, num_rows=100)],
        total=1,
    )
    lake._config = MagicMock()
    lake._config.storage = MagicMock()
    lake._config.storage.s3_bucket = "test-bucket"
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


# ── Ingest SQL ──


@pytest.mark.asyncio
async def test_ingest_sql(client: AsyncClient, mock_lake: MagicMock) -> None:
    mock_lake.ingest_sql.return_value = _FakeReport(
        sources=(_FakeSource(path="sql://query", row_count=10),),
        total_rows=10, total_files=1,
    )
    resp = await client.post(
        "/api/v1/datasets/test/ingest/sql",
        json={"sql": "SELECT * FROM t", "connection_url": "sqlite:///test.db"},
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["success"] is True
    assert body["total_rows"] == 10


# ── Ingest Kafka ──


@pytest.mark.asyncio
async def test_ingest_kafka(client: AsyncClient, mock_lake: MagicMock) -> None:
    mock_lake.ingest_kafka.return_value = _FakeReport(
        sources=(_FakeSource(path="kafka://topic", row_count=50),),
        total_rows=50, total_files=1,
    )
    resp = await client.post(
        "/api/v1/datasets/test/ingest/kafka",
        json={
            "bootstrap_servers": "localhost:9092",
            "topics": ["my-topic"],
            "start": "earliest",
        },
    )
    assert resp.status_code == 201
    assert resp.json()["total_rows"] == 50


# ── Ingest Iceberg ──


@pytest.mark.asyncio
async def test_ingest_iceberg(client: AsyncClient, mock_lake: MagicMock) -> None:
    mock_lake.ingest_iceberg.return_value = _FakeReport(
        total_rows=200, total_files=1,
    )
    resp = await client.post(
        "/api/v1/datasets/test/ingest/iceberg",
        json={"table_uri": "s3://warehouse/db.table"},
    )
    assert resp.status_code == 201
    assert resp.json()["total_rows"] == 200


# ── Ingest Delta Lake ──


@pytest.mark.asyncio
async def test_ingest_deltalake(client: AsyncClient, mock_lake: MagicMock) -> None:
    mock_lake.ingest_deltalake.return_value = _FakeReport(
        total_rows=150, total_files=1,
    )
    resp = await client.post(
        "/api/v1/datasets/test/ingest/deltalake",
        json={"table_uri": "s3://warehouse/delta-table"},
    )
    assert resp.status_code == 201
    assert resp.json()["total_rows"] == 150


# ── Ingest HTTP ──


@pytest.mark.asyncio
async def test_ingest_http_endpoint(client: AsyncClient, mock_lake: MagicMock) -> None:
    mock_lake.ingest_http.return_value = _FakeReport(
        total_rows=75, total_files=2,
    )
    resp = await client.post(
        "/api/v1/datasets/test/ingest/http",
        json={"urls": ["https://example.com/data.csv"]},
    )
    assert resp.status_code == 201
    assert resp.json()["total_rows"] == 75


# ── Ingest Documents ──


@pytest.mark.asyncio
async def test_ingest_documents(client: AsyncClient, mock_lake: MagicMock) -> None:
    mock_lake.ingest_documents.return_value = _FakeReport(
        total_rows=30, total_files=3,
    )
    mock_lake._config.document = None
    resp = await client.post(
        "/api/v1/datasets/test/ingest/documents",
        json={"pdf_paths": ["doc1.pdf"], "blob_keys": []},
    )
    assert resp.status_code == 201
    assert resp.json()["total_rows"] == 30


# ── Ingest with transforms ──


@pytest.mark.asyncio
async def test_ingest_files_with_transforms(client: AsyncClient, mock_lake: MagicMock) -> None:
    mock_lake.ingest.return_value = _FakeReport(total_rows=10, total_files=1)
    with patch("arrow_lake.ingest.transforms.build_transforms", return_value=[]):
        resp = await client.post(
            "/api/v1/datasets/test/ingest",
            json={"file_paths": ["data.csv"], "transforms": [{"type": "rename", "column": "a", "new_name": "b"}]},
        )
    assert resp.status_code == 201


# ── Presign Upload ──


@pytest.mark.asyncio
async def test_presign_upload(client: AsyncClient, mock_lake: MagicMock) -> None:
    blob_store = MagicMock()
    blob_store.presigned_url.return_value = "https://minio.local/upload?sig=abc"
    mock_lake._get_component.return_value = blob_store

    resp = await client.post(
        "/api/v1/datasets/test/upload/presign",
        json={"filenames": ["data.csv"]},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["uploads"]) == 1
    assert body["uploads"][0]["upload_url"] == "https://minio.local/upload?sig=abc"


# ── Cleanup Uploads ──


@pytest.mark.asyncio
async def test_cleanup_uploads(client: AsyncClient, mock_lake: MagicMock) -> None:
    blob_store = MagicMock()
    blob_store.delete_prefix.return_value = 5
    mock_lake._get_component.return_value = blob_store

    resp = await client.delete("/api/v1/datasets/test/upload/cleanup")
    assert resp.status_code == 200
    assert resp.json()["deleted_count"] == 5


# ── Schema Migration ──


@pytest.mark.asyncio
async def test_schema_migration_dry_run(client: AsyncClient, mock_lake: MagicMock) -> None:
    import pyarrow as pa

    mock_ds = MagicMock()
    mock_ds.schema = pa.schema([pa.field("name", pa.string()), pa.field("age", pa.int64())])
    mock_lake._storage.open_dataset.return_value = mock_ds

    resp = await client.post(
        "/api/v1/datasets/test/schema/migrate",
        json={"dry_run": True, "actions": [{"operation": "add_column", "column_name": "email", "new_type": "string"}]},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    assert body["dry_run"] is True
    assert body["applied_count"] == 0


@pytest.mark.asyncio
async def test_schema_migration_unknown_type(client: AsyncClient, mock_lake: MagicMock) -> None:
    import pyarrow as pa

    mock_ds = MagicMock()
    mock_ds.schema = pa.schema([pa.field("name", pa.string())])
    mock_lake._storage.open_dataset.return_value = mock_ds

    resp = await client.post(
        "/api/v1/datasets/test/schema/migrate",
        json={"dry_run": True, "actions": [{"operation": "alter_column", "column_name": "name", "new_type": "bogus_type"}]},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is False
    assert len(body["issues"]) == 1


@pytest.mark.asyncio
async def test_schema_migration_dataset_not_found(client: AsyncClient, mock_lake: MagicMock) -> None:
    mock_lake.catalog.return_value = _FakeCatalogResult(datasets=[], total=0)
    resp = await client.post(
        "/api/v1/datasets/test/schema/migrate",
        json={"dry_run": True, "actions": [{"operation": "add_column", "column_name": "x", "new_type": "string"}]},
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_schema_migration_apply(client: AsyncClient, mock_lake: MagicMock) -> None:
    import pyarrow as pa

    mock_ds = MagicMock()
    mock_ds.schema = pa.schema([pa.field("name", pa.string())])
    mock_lake._storage.open_dataset.return_value = mock_ds
    mock_lake._storage_advanced.add_column = MagicMock()

    resp = await client.post(
        "/api/v1/datasets/test/schema/migrate",
        json={"dry_run": False, "actions": [{"operation": "add_column", "column_name": "email", "new_type": "string"}]},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    assert body["applied_count"] == 1


@pytest.mark.asyncio
async def test_schema_migration_unknown_operation(client: AsyncClient, mock_lake: MagicMock) -> None:
    import pyarrow as pa

    mock_ds = MagicMock()
    mock_ds.schema = pa.schema([pa.field("name", pa.string())])
    mock_lake._storage.open_dataset.return_value = mock_ds

    resp = await client.post(
        "/api/v1/datasets/test/schema/migrate",
        json={"dry_run": True, "actions": [{"operation": "bogus_op", "column_name": "x", "new_type": "string"}]},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is False


# ── List with pagination ──


@pytest.mark.asyncio
async def test_list_datasets_pagination(client: AsyncClient) -> None:
    resp = await client.get("/api/v1/datasets", params={"limit": 1, "offset": 0})
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 1
    assert len(body["datasets"]) == 1


# ── Helper functions ──


def test_sanitize_filename_valid() -> None:
    from arrow_lake.api.routers.datasets import _sanitize_filename
    assert _sanitize_filename("data.csv") == "data.csv"
    assert _sanitize_filename("/path/to/data.csv") == "data.csv"


def test_sanitize_filename_rejects_special_chars() -> None:
    from arrow_lake.api.routers.datasets import _sanitize_filename
    with pytest.raises(ValueError):
        _sanitize_filename("file with spaces.csv")


def test_sanitize_filename_rejects_empty_after_basename() -> None:
    from arrow_lake.api.routers.datasets import _sanitize_filename
    with pytest.raises(ValueError):
        _sanitize_filename("")


def test_sanitize_filename_rejects_empty() -> None:
    from arrow_lake.api.routers.datasets import _sanitize_filename
    with pytest.raises(Exception):
        _sanitize_filename("")


def test_is_s3_native() -> None:
    from arrow_lake.api.routers.datasets import _is_s3_native
    assert _is_s3_native("data.csv") is True
    assert _is_s3_native("data.json") is True
    assert _is_s3_native("data.parquet") is True
    assert _is_s3_native("data.pdf") is False
    assert _is_s3_native("image.png") is False


def test_unique_blob_key() -> None:
    from arrow_lake.api.routers.datasets import _unique_blob_key
    key = _unique_blob_key("my-ds", "file.csv")
    assert key.startswith("uploads/my-ds/")
    assert key.endswith("_file.csv")


def test_blob_key_to_s3_uri() -> None:
    from arrow_lake.api.routers.datasets import _blob_key_to_s3_uri
    mock_lake = MagicMock()
    mock_lake._config.storage.s3_bucket = "my-bucket"
    uri = _blob_key_to_s3_uri("uploads/ds/file.csv", mock_lake)
    assert uri == "s3://my-bucket/uploads/ds/file.csv"
