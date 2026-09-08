"""Tests for dataset endpoints: SQL/Kafka/Iceberg/DeltaLake/documents ingest,
presign, cleanup, schema migration (supplements test_datasets.py)."""

from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pyarrow as pa
import pytest
from arrow_lake.api.app import create_app
from arrow_lake.config import ArrowLakeConfig
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
    # Fields added in v1.9.x; mirror arrow_lake.api.models.dataset.DatasetInfo.
    num_columns: int = 0
    vector_dim: int | None = None
    has_vector_index: bool = False
    has_fts_index: bool = False
    size_bytes: int | None = None
    created_at: str | None = None
    updated_at: str | None = None


@dataclass(frozen=True)
class _FakeCatalogResult:
    datasets: list = ()
    total: int = 0


@pytest.fixture
def mock_lake() -> MagicMock:
    lake = MagicMock()
    lake.catalog.return_value = _FakeCatalogResult(
        datasets=[_FakeEntry(name="test", version=1, num_rows=100)],
        total=1,
    )
    return lake


@pytest.fixture
async def client(mock_lake: MagicMock) -> AsyncClient:
    config = ArrowLakeConfig()
    config.api.api_key = "test-api-key"
    config.api.api_key_default_role = "ADMIN"
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
# POST /{name}/ingest/sql
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_ingest_sql(client: AsyncClient, mock_lake: MagicMock) -> None:
    mock_lake.ingest_sql.return_value = _FakeReport(
        sources=(_FakeSource(path="sql://query", row_count=50),),
        total_rows=50, total_files=1,
    )
    resp = await client.post(
        "/api/v1/datasets/test/ingest/sql",
        json={
            "sql": "SELECT * FROM users",
            "connection_url": "postgresql://localhost/mydb",
        },
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["total_rows"] == 50
    mock_lake.ingest_sql.assert_called_once()


@pytest.mark.asyncio
async def test_ingest_sql_empty_rejected(client: AsyncClient) -> None:
    resp = await client.post(
        "/api/v1/datasets/test/ingest/sql",
        json={"sql": "", "connection_url": "postgresql://localhost/db"},
    )
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# POST /{name}/ingest/kafka
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_ingest_kafka(client: AsyncClient, mock_lake: MagicMock) -> None:
    mock_lake.ingest_kafka.return_value = _FakeReport(
        sources=(_FakeSource(path="kafka://topic1", row_count=200),),
        total_rows=200, total_files=1,
    )
    resp = await client.post(
        "/api/v1/datasets/test/ingest/kafka",
        json={
            "bootstrap_servers": "localhost:9092",
            "topics": ["events"],
        },
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["total_rows"] == 200
    mock_lake.ingest_kafka.assert_called_once()


# ---------------------------------------------------------------------------
# POST /{name}/ingest/iceberg
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_ingest_iceberg(client: AsyncClient, mock_lake: MagicMock) -> None:
    mock_lake.ingest_iceberg.return_value = _FakeReport(
        sources=(_FakeSource(path="iceberg://tbl", row_count=300),),
        total_rows=300, total_files=1,
    )
    resp = await client.post(
        "/api/v1/datasets/test/ingest/iceberg",
        json={"table_uri": "s3://warehouse/db.tbl"},
    )
    assert resp.status_code == 201
    assert resp.json()["total_rows"] == 300
    mock_lake.ingest_iceberg.assert_called_once()


# ---------------------------------------------------------------------------
# POST /{name}/ingest/deltalake
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_ingest_deltalake(client: AsyncClient, mock_lake: MagicMock) -> None:
    mock_lake.ingest_deltalake.return_value = _FakeReport(
        sources=(_FakeSource(path="delta://tbl", row_count=400),),
        total_rows=400, total_files=1,
    )
    resp = await client.post(
        "/api/v1/datasets/test/ingest/deltalake",
        json={"table_uri": "s3://warehouse/delta/tbl", "version": 3},
    )
    assert resp.status_code == 201
    assert resp.json()["total_rows"] == 400
    mock_lake.ingest_deltalake.assert_called_once()


# ---------------------------------------------------------------------------
# POST /{name}/ingest/documents
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_ingest_documents(client: AsyncClient, mock_lake: MagicMock) -> None:
    # The route calls ingest_documents_and_index (架构评审 #4 consolidation)
    # — stubbing the old name leaves a bare MagicMock whose embed_async
    # attribute fails IngestResponse validation (500).
    mock_lake.ingest_documents_and_index.return_value = _FakeReport(
        sources=(_FakeSource(path="doc.pdf", row_count=10),),
        total_rows=10, total_files=1,
    )
    resp = await client.post(
        "/api/v1/datasets/test/ingest/documents",
        json={"pdf_paths": ["report.pdf"]},
    )
    assert resp.status_code == 201
    assert resp.json()["total_rows"] == 10
    mock_lake.ingest_documents_and_index.assert_called_once()


@pytest.mark.asyncio
async def test_ingest_documents_non_pdf_rejected(client: AsyncClient) -> None:
    resp = await client.post(
        "/api/v1/datasets/test/ingest/documents",
        json={"pdf_paths": ["data.csv"]},
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_ingest_documents_no_source_rejected(client: AsyncClient) -> None:
    resp = await client.post(
        "/api/v1/datasets/test/ingest/documents",
        json={"pdf_paths": [], "blob_keys": []},
    )
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# POST /{name}/upload/presign
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_presign_upload(client: AsyncClient, mock_lake: MagicMock) -> None:
    from unittest.mock import patch

    fake_blob = MagicMock()
    fake_blob.presigned_url.return_value = "https://minio.local/upload?sig=abc"

    with patch("arrow_lake.api.routers.datasets._get_blob_store", return_value=fake_blob):
        resp = await client.post(
            "/api/v1/datasets/test/upload/presign",
            json={"filenames": ["data.csv", "report.pdf"]},
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    assert len(body["uploads"]) == 2
    assert body["uploads"][0]["key"].startswith("uploads/test/")
    assert body["uploads"][0]["upload_url"] == "https://minio.local/upload?sig=abc"


@pytest.mark.asyncio
async def test_presign_upload_invalid_filename(client: AsyncClient) -> None:
    resp = await client.post(
        "/api/v1/datasets/test/upload/presign",
        json={"filenames": ["../../../etc/passwd"]},
    )
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# DELETE /{name}/upload/cleanup
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_cleanup_uploads(client: AsyncClient, mock_lake: MagicMock) -> None:
    from unittest.mock import patch

    fake_blob = MagicMock()
    fake_blob.delete_prefix.return_value = 5

    with patch("arrow_lake.api.routers.datasets._get_blob_store", return_value=fake_blob):
        resp = await client.delete("/api/v1/datasets/test/upload/cleanup")
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    assert body["deleted_count"] == 5


# ---------------------------------------------------------------------------
# POST /{name}/schema/migrate (dry_run)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_schema_migrate_dry_run_success(client: AsyncClient, mock_lake: MagicMock) -> None:
    fake_ds = MagicMock()
    fake_ds.schema = pa.schema([pa.field("name", pa.string()), pa.field("age", pa.int64())])
    mock_lake._storage.open_dataset.return_value = fake_ds

    resp = await client.post(
        "/api/v1/datasets/test/schema/migrate",
        json={
            "actions": [{"operation": "add_column", "column_name": "score", "new_type": "float64"}],
            "dry_run": True,
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    assert body["dry_run"] is True
    assert body["applied_count"] == 0


@pytest.mark.asyncio
async def test_schema_migrate_dry_run_with_issues(client: AsyncClient, mock_lake: MagicMock) -> None:
    fake_ds = MagicMock()
    fake_ds.schema = pa.schema([pa.field("name", pa.string())])
    mock_lake._storage.open_dataset.return_value = fake_ds

    resp = await client.post(
        "/api/v1/datasets/test/schema/migrate",
        json={
            "actions": [{"operation": "drop_column", "column_name": "nonexistent"}],
            "dry_run": True,
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is False
    assert len(body["issues"]) > 0


@pytest.mark.asyncio
async def test_schema_migrate_dataset_not_found(client: AsyncClient, mock_lake: MagicMock) -> None:
    mock_lake.catalog.return_value = _FakeCatalogResult(datasets=[], total=0)

    resp = await client.post(
        "/api/v1/datasets/nonexistent/schema/migrate",
        json={
            "actions": [{"operation": "add_column", "column_name": "x"}],
            "dry_run": True,
        },
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_schema_migrate_dry_run_lint_traps(client: AsyncClient, mock_lake: MagicMock) -> None:
    """v1.11.6: dry_run statically lints Lance dialect traps (TRIM + double-quote literal + CASE)."""
    fake_ds = MagicMock()
    fake_ds.schema = pa.schema([pa.field("name", pa.string())])
    mock_lake._storage.open_dataset.return_value = fake_ds

    resp = await client.post(
        "/api/v1/datasets/test/schema/migrate",
        json={
            "actions": [{"operation": "add_column", "column_name": "x",
                         "sql_expr": 'TRIM("name")'}],
            "dry_run": True,
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is False
    msgs = " ".join(body["issues"][0]["messages"])
    assert "TRIM" in msgs
    assert "Double quotes" in msgs

    resp = await client.post(
        "/api/v1/datasets/test/schema/migrate",
        json={
            "actions": [{"operation": "add_column", "column_name": "flag",
                         "sql_expr": "CASE WHEN name = 'a' THEN 1 ELSE 0 END"}],
            "dry_run": True,
        },
    )
    body = resp.json()
    assert body["success"] is False
    assert "CASE" in " ".join(body["issues"][0]["messages"])


@pytest.mark.asyncio
async def test_schema_migrate_dry_run_expression_preview(client: AsyncClient, mock_lake: MagicMock) -> None:
    """v1.11.6: dry_run samples the add_column expression (values + inferred type)."""
    fake_lt = MagicMock()
    fake_lt.schema = pa.schema([pa.field("name", pa.string())])
    fake_lt.to_lance.return_value.to_table.return_value = pa.table({"shout": ["A", "B"]})
    mock_lake._storage.open_dataset.return_value = fake_lt

    resp = await client.post(
        "/api/v1/datasets/test/schema/migrate",
        json={
            "actions": [{"operation": "add_column", "column_name": "shout",
                         "sql_expr": "upper(name)"}],
            "dry_run": True,
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    assert body["previews"][0]["ok"] is True
    assert body["previews"][0]["sample_values"] == ["A", "B"]
    assert "string" in body["previews"][0]["inferred_type"]


# ---------------------------------------------------------------------------
# POST /{name}/schema/migrate — typed placeholder mode (v1.11.6 mode B)
# ---------------------------------------------------------------------------

def _fake_ds(mock_lake: MagicMock, schema: pa.Schema, storage_version: float = 2.1) -> MagicMock:
    """A dataset stub whose to_lance() reports the given data-file version."""
    fake_ds = MagicMock()
    fake_ds.schema = schema
    fake_ds.to_lance.return_value.data_storage_version = storage_version
    mock_lake._storage.open_dataset.return_value = fake_ds
    return fake_ds


@pytest.mark.asyncio
async def test_migrate_typed_placeholder_dry_run(client: AsyncClient, mock_lake: MagicMock) -> None:
    """new_type + empty expr → placeholder preview carries the resolved type."""
    _fake_ds(mock_lake, pa.schema([pa.field("s", pa.string())]))
    resp = await client.post(
        "/api/v1/datasets/test/schema/migrate",
        json={"actions": [
            {"operation": "add_column", "column_name": "emb", "new_type": "vector:768"},
        ], "dry_run": True},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    preview = body["previews"][0]
    assert "float" in preview["inferred_type"]
    assert "768" in preview["inferred_type"]
    assert any("NULL" in v for v in preview["sample_values"])


@pytest.mark.asyncio
async def test_migrate_add_unknown_type_rejected(client: AsyncClient, mock_lake: MagicMock) -> None:
    """Unknown type fails closed on the add path too (no silent string fallback)."""
    _fake_ds(mock_lake, pa.schema([pa.field("s", pa.string())]))
    resp = await client.post(
        "/api/v1/datasets/test/schema/migrate",
        json={"actions": [
            {"operation": "add_column", "column_name": "x", "new_type": "float16"},
        ], "dry_run": True},
    )
    body = resp.json()
    assert body["success"] is False
    assert "Unknown type" in " ".join(body["issues"][0]["messages"])


@pytest.mark.asyncio
async def test_migrate_add_expr_and_type_conflict(client: AsyncClient, mock_lake: MagicMock) -> None:
    _fake_ds(mock_lake, pa.schema([pa.field("s", pa.string())]))
    resp = await client.post(
        "/api/v1/datasets/test/schema/migrate",
        json={"actions": [
            {"operation": "add_column", "column_name": "x",
             "sql_expr": "upper(s)", "new_type": "int32"},
        ], "dry_run": True},
    )
    body = resp.json()
    assert body["success"] is False
    assert "not both" in " ".join(body["issues"][0]["messages"])


@pytest.mark.asyncio
async def test_migrate_add_neither_expr_nor_type(client: AsyncClient, mock_lake: MagicMock) -> None:
    _fake_ds(mock_lake, pa.schema([pa.field("s", pa.string())]))
    resp = await client.post(
        "/api/v1/datasets/test/schema/migrate",
        json={"actions": [{"operation": "add_column", "column_name": "x"}], "dry_run": True},
    )
    body = resp.json()
    assert body["success"] is False
    assert "needs a sql_expr" in " ".join(body["issues"][0]["messages"])


@pytest.mark.asyncio
async def test_migrate_blob_version_wall_issue(client: AsyncClient, mock_lake: MagicMock) -> None:
    """blob on a 2.1 table is rejected with the rewrite/binary guidance."""
    _fake_ds(mock_lake, pa.schema([pa.field("s", pa.string())]), storage_version=2.1)
    resp = await client.post(
        "/api/v1/datasets/test/schema/migrate",
        json={"actions": [
            {"operation": "add_column", "column_name": "photo", "new_type": "blob"},
        ], "dry_run": True},
    )
    body = resp.json()
    assert body["success"] is False
    msgs = " ".join(body["issues"][0]["messages"])
    assert "2.2" in msgs
    assert "binary" in msgs


@pytest.mark.asyncio
async def test_migrate_blob_on_2_2_passes(client: AsyncClient, mock_lake: MagicMock) -> None:
    _fake_ds(mock_lake, pa.schema([pa.field("s", pa.string())]), storage_version=2.2)
    resp = await client.post(
        "/api/v1/datasets/test/schema/migrate",
        json={"actions": [
            {"operation": "add_column", "column_name": "photo", "new_type": "blob"},
        ], "dry_run": True},
    )
    body = resp.json()
    assert body["success"] is True
    assert "blob" in body["previews"][0]["inferred_type"].lower()


@pytest.mark.asyncio
async def test_migrate_alter_to_blob_rejected(client: AsyncClient, mock_lake: MagicMock) -> None:
    _fake_ds(mock_lake, pa.schema([pa.field("s", pa.string())]))
    resp = await client.post(
        "/api/v1/datasets/test/schema/migrate",
        json={"actions": [
            {"operation": "alter_column", "column_name": "s", "new_type": "blob"},
        ], "dry_run": True},
    )
    body = resp.json()
    assert body["success"] is False
    assert "not supported" in " ".join(body["issues"][0]["messages"])


@pytest.mark.asyncio
async def test_migrate_alter_vector_dimension_issue(client: AsyncClient, mock_lake: MagicMock) -> None:
    """Vector specs reach the (previously dead) dimension check in the checker."""
    _fake_ds(mock_lake, pa.schema([pa.field("emb", pa.list_(pa.float32(), 384))]))
    resp = await client.post(
        "/api/v1/datasets/test/schema/migrate",
        json={"actions": [
            {"operation": "alter_column", "column_name": "emb", "new_type": "vector:768"},
        ], "dry_run": True},
    )
    body = resp.json()
    assert body["success"] is False
    assert "dimension" in " ".join(body["issues"][0]["messages"]).lower()


@pytest.mark.asyncio
async def test_migrate_typed_placeholder_apply_routes_to_add_null_column(
    client: AsyncClient, mock_lake: MagicMock,
) -> None:
    """Apply mode B dispatches to lake.add_null_column with the resolved type."""
    _fake_ds(mock_lake, pa.schema([pa.field("s", pa.string())]))
    mock_lake.catalog.return_value = _FakeCatalogResult(
        datasets=[SimpleNamespace(name="test")], total=1,
    )
    resp = await client.post(
        "/api/v1/datasets/test/schema/migrate",
        json={"actions": [
            {"operation": "add_column", "column_name": "emb", "new_type": "vector:384"},
        ], "dry_run": False},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["success"] is True
    assert body["applied_count"] == 1
    mock_lake.add_null_column.assert_called_once()
    call = mock_lake.add_null_column.call_args
    assert call.args == ("test", "emb", pa.list_(pa.float32(), 384))
    assert call.kwargs["blob"] is False


@pytest.mark.asyncio
async def test_schema_migrate_unknown_operation(client: AsyncClient, mock_lake: MagicMock) -> None:
    fake_ds = MagicMock()
    fake_ds.schema = pa.schema([pa.field("name", pa.string())])
    mock_lake._storage.open_dataset.return_value = fake_ds

    resp = await client.post(
        "/api/v1/datasets/test/schema/migrate",
        json={
            "actions": [{"operation": "rename_column", "column_name": "name"}],
            "dry_run": True,
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is False
    assert any("Unknown operation" in i["messages"][0] for i in body["issues"])


# ---------------------------------------------------------------------------
# List datasets with pagination
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_list_datasets_pagination(client: AsyncClient) -> None:
    resp = await client.get("/api/v1/datasets?limit=1&offset=0")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 1
    assert len(body["datasets"]) == 1


@pytest.mark.asyncio
async def test_list_datasets_offset_beyond(client: AsyncClient) -> None:
    resp = await client.get("/api/v1/datasets?limit=10&offset=100")
    assert resp.status_code == 200
    body = resp.json()
    assert body["datasets"] == []


# ---------------------------------------------------------------------------
# Input validation — ingest request requires at least one source
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_ingest_no_source_rejected(client: AsyncClient) -> None:
    resp = await client.post(
        "/api/v1/datasets/test/ingest",
        json={"file_paths": [], "blob_keys": []},
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_ingest_http_private_ip_rejected(client: AsyncClient) -> None:
    resp = await client.post(
        "/api/v1/datasets/test/ingest/http",
        json={"urls": ["http://127.0.0.1/secret"]},
    )
    assert resp.status_code == 422
