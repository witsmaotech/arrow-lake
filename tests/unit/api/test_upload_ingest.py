"""Tests for upload endpoint and blob_keys ingest — unit level with mocks."""

from __future__ import annotations

import os
from unittest.mock import AsyncMock, MagicMock

import pytest

from types import SimpleNamespace

from arrow_lake.api.auth_models import Role


def _authed_request() -> MagicMock:
    """Request 伪对象:EDITOR user + allow-all checker(v1.10.7 WP1 守卫契约)。"""
    req = MagicMock()
    req.state.user = SimpleNamespace(role=Role.EDITOR, sub="1", user_id=1)
    checker = MagicMock()
    checker.check_dataset_access.return_value = True
    req.app.state.checker = checker
    return req

from arrow_lake.api.models.dataset import (
    CleanupResponse,
    IngestDocumentsRequest,
    IngestFilesRequest,
    IngestImagesRequest,
    IngestMixedRequest,
    IngestVideosRequest,
    PresignedUpload,
    PresignRequest,
    PresignResponse,
    UploadedBlob,
    UploadResponse,
)


def _make_mock_blob_store():
    """Create a mock BlobStoreManager that actually writes files on download."""
    mock = MagicMock()

    def _fake_download(key: str, dest_path: str) -> int:
        os.makedirs(os.path.dirname(dest_path), exist_ok=True)
        with open(dest_path, "wb") as f:
            f.write(b"mock data for " + key.encode())
        return len(b"mock data for " + key.encode())

    mock.download_file.side_effect = _fake_download
    return mock


# ---------------------------------------------------------------------------
# Model validation tests
# ---------------------------------------------------------------------------


class TestIngestFilesRequestBlobKeys:
    def test_file_paths_only(self):
        req = IngestFilesRequest(file_paths=["/data/file.csv"])
        assert req.file_paths == ["/data/file.csv"]
        assert req.blob_keys == []

    def test_blob_keys_only(self):
        req = IngestFilesRequest(blob_keys=["uploads/ds/file.csv"])
        assert req.blob_keys == ["uploads/ds/file.csv"]
        assert req.file_paths == []

    def test_both_sources(self):
        req = IngestFilesRequest(
            file_paths=["/data/a.csv"],
            blob_keys=["uploads/ds/b.csv"],
        )
        assert len(req.file_paths) == 1
        assert len(req.blob_keys) == 1

    def test_neither_source_raises(self):
        from pydantic import ValidationError
        with pytest.raises(ValidationError, match="At least one"):
            IngestFilesRequest()

    def test_blob_keys_invalid_prefix(self):
        with pytest.raises(ValueError, match="must start with 'uploads/'"):
            IngestFilesRequest(blob_keys=["other/file.csv"])

    def test_blob_keys_traversal(self):
        with pytest.raises(ValueError, match="traversal"):
            IngestFilesRequest(blob_keys=["uploads/../../../etc/passwd"])


class TestIngestImagesRequestBlobKeys:
    def test_blob_keys_only(self):
        req = IngestImagesRequest(blob_keys=["uploads/ds/photo.jpg"])
        assert req.blob_keys == ["uploads/ds/photo.jpg"]

    def test_neither_source_raises(self):
        with pytest.raises(ValueError):
            IngestImagesRequest()


class TestIngestVideosRequestBlobKeys:
    def test_blob_keys_only(self):
        req = IngestVideosRequest(blob_keys=["uploads/ds/clip.mp4"])
        assert req.blob_keys == ["uploads/ds/clip.mp4"]


class TestIngestDocumentsRequestBlobKeys:
    def test_blob_keys_pdf(self):
        req = IngestDocumentsRequest(blob_keys=["uploads/ds/paper.pdf"])
        assert req.blob_keys == ["uploads/ds/paper.pdf"]

    def test_blob_keys_non_pdf_rejected(self):
        # v1.8.9: /ingest/documents accepts all kreuzberg doc types (not just PDF);
        # .csv is still rejected but the message now lists supported extensions.
        with pytest.raises(ValueError, match="supported document type"):
            IngestDocumentsRequest(blob_keys=["uploads/ds/file.csv"])

    def test_neither_source_raises(self):
        with pytest.raises(ValueError):
            IngestDocumentsRequest()


class TestIngestMixedRequestBlobKeys:
    def test_blob_keys_only(self):
        req = IngestMixedRequest(blob_keys={"files": ["uploads/ds/data.csv"]})
        assert req.blob_keys["files"] == ["uploads/ds/data.csv"]

    def test_sources_only(self):
        req = IngestMixedRequest(sources={"files": ["data.csv"]})
        assert req.sources["files"] == ["data.csv"]

    def test_neither_source_raises(self):
        with pytest.raises(ValueError):
            IngestMixedRequest()

    def test_invalid_blob_modality(self):
        with pytest.raises(ValueError, match="Unknown blob modality"):
            IngestMixedRequest(blob_keys={"invalid": ["uploads/ds/file.csv"]})


class TestUploadResponse:
    def test_empty(self):
        resp = UploadResponse()
        assert resp.success is True
        assert resp.blobs == []

    def test_with_blobs(self):
        resp = UploadResponse(blobs=[
            UploadedBlob(key="uploads/ds/file.csv", size_bytes=1024, content_type="text/csv"),
        ])
        assert len(resp.blobs) == 1
        assert resp.blobs[0].key == "uploads/ds/file.csv"


# ---------------------------------------------------------------------------
# Router logic tests (mocked)
# ---------------------------------------------------------------------------


class TestUploadEndpoint:
    """Test the upload endpoint handler with mocked dependencies."""

    @pytest.mark.asyncio
    async def test_upload_single_file(self):
        from arrow_lake.api.routers.datasets import upload_files

        mock_lake = MagicMock()
        mock_blob_store = MagicMock()
        mock_blob_store.upload.return_value = MagicMock(
            key="uploads/test/data.csv", size_bytes=100, etag="abc",
        )
        mock_lake._get_component.return_value = mock_blob_store
        mock_lake._config.storage.s3_bucket = "arrow-lake"

        mock_file = MagicMock(spec=["filename", "content_type", "read"])
        mock_file.filename = "data.csv"
        mock_file.content_type = "text/csv"
        mock_file.read = AsyncMock(return_value=b"id,name\n1,test\n")

        resp = await upload_files(
            request=_authed_request(),
            name="test",
            files=[mock_file],
            lake=mock_lake,
            _user={"role": "editor"},
        )

        assert resp.success is True
        assert len(resp.blobs) == 1
        # Blob key stored under dataset prefix
        assert "uploads/test/" in resp.blobs[0].key
        assert resp.blobs[0].key.endswith("data.csv")
        assert resp.blobs[0].size_bytes == 100

    @pytest.mark.asyncio
    async def test_upload_bad_filename(self):
        """Filenames that sanitize to empty (pure unsafe chars) are rejected.

        Note: normal filenames with spaces (e.g. "Attention Is All You Need.pdf")
        are intentionally SANITIZED, not rejected — a strict allow-list was found
        to 500 on perfectly normal names. Only names that reduce to empty after
        sanitization raise "Invalid filename".
        """
        from arrow_lake.api.routers.datasets import upload_files

        mock_lake = MagicMock()
        mock_file = MagicMock(spec=["filename", "content_type", "read"])
        mock_file.filename = "???###"  # pure unsafe chars → sanitize to empty
        mock_file.content_type = "text/csv"
        mock_file.read = AsyncMock(return_value=b"data")

        with pytest.raises(ValueError, match="Invalid filename"):
            await upload_files(
                request=_authed_request(),
                name="test",
                files=[mock_file],
                lake=mock_lake,
                _user={"role": "editor"},
            )


class TestResolveBlobKeys:
    """Test the _resolve_blob_keys helper."""

    def test_resolve_single_key(self, tmp_path):
        from arrow_lake.api.routers.datasets import _resolve_blob_keys

        mock_lake = MagicMock()
        mock_blob_store = _make_mock_blob_store()
        mock_lake._get_component.return_value = mock_blob_store

        paths = _resolve_blob_keys(
            ["uploads/test/data.csv"], mock_lake, str(tmp_path),
        )

        assert len(paths) == 1
        assert paths[0].endswith("data.csv")
        mock_blob_store.download_file.assert_called_once()

    def test_resolve_multiple_keys(self, tmp_path):
        from arrow_lake.api.routers.datasets import _resolve_blob_keys

        mock_lake = MagicMock()
        mock_blob_store = _make_mock_blob_store()
        mock_lake._get_component.return_value = mock_blob_store

        paths = _resolve_blob_keys(
            ["uploads/test/a.csv", "uploads/test/b.jsonl"],
            mock_lake, str(tmp_path),
        )

        assert len(paths) == 2
        assert mock_blob_store.download_file.call_count == 2


class TestResolveBlobSources:
    """Test the _resolve_blob_sources helper."""

    def test_resolve_multi_modality(self, tmp_path):
        from arrow_lake.api.routers.datasets import _resolve_blob_sources

        mock_lake = MagicMock()
        mock_blob_store = _make_mock_blob_store()
        mock_lake._get_component.return_value = mock_blob_store

        result = _resolve_blob_sources(
            {"files": ["uploads/test/data.csv"], "images": ["uploads/test/img.jpg"]},
            mock_lake, str(tmp_path),
        )

        assert "files" in result
        assert "images" in result
        assert len(result["files"]) == 1
        assert len(result["images"]) == 1


# ---------------------------------------------------------------------------
# Phase 2: Presigned URL + S3-native + Concurrent
# ---------------------------------------------------------------------------


class TestPresignModels:
    def test_presign_request_valid(self):
        req = PresignRequest(filenames=["data.csv", "report.pdf"])
        assert len(req.filenames) == 2

    def test_presign_request_bad_filename(self):
        with pytest.raises(ValueError, match="Invalid filename"):
            PresignRequest(filenames=["file with spaces.csv"])

    def test_presign_request_traversal(self):
        with pytest.raises(ValueError, match="traversal|Invalid"):
            PresignRequest(filenames=["../../etc/passwd"])

    def test_presign_response(self):
        resp = PresignResponse(uploads=[
            PresignedUpload(key="uploads/ds/data.csv", upload_url="http://minio:9000/..."),
        ])
        assert len(resp.uploads) == 1
        assert resp.uploads[0].key == "uploads/ds/data.csv"


class TestPresignEndpoint:
    """Test the presign upload endpoint."""

    @pytest.mark.asyncio
    async def test_presign_single_file(self):
        from arrow_lake.api.routers.datasets import presign_upload

        mock_lake = MagicMock()
        mock_blob_store = MagicMock()
        mock_blob_store.presigned_url.return_value = "http://minio:9000/arrow-lake/uploads/test/data.csv?signature=abc"
        mock_lake._get_component.return_value = mock_blob_store

        resp = await presign_upload(
            request=_authed_request(),
            name="test",
            req=PresignRequest(filenames=["data.csv"]),
            lake=mock_lake,
            _user={"role": "editor"},
        )

        assert resp.success is True
        assert len(resp.uploads) == 1
        # Blob key now has UUID prefix for collision resistance
        assert resp.uploads[0].key.startswith("uploads/test/")
        assert resp.uploads[0].key.endswith("_data.csv")
        assert "minio" in resp.uploads[0].upload_url
        mock_blob_store.presigned_url.assert_called_once()
        call_args = mock_blob_store.presigned_url.call_args
        assert call_args[0][0].startswith("uploads/test/")
        assert call_args[1].get("expires_in") == 3600
        assert call_args[1].get("operation") == "put_object"

    @pytest.mark.asyncio
    async def test_presign_multiple_files(self):
        from arrow_lake.api.routers.datasets import presign_upload

        mock_lake = MagicMock()
        mock_blob_store = MagicMock()
        mock_blob_store.presigned_url.return_value = "http://minio:9000/presigned"
        mock_lake._get_component.return_value = mock_blob_store

        resp = await presign_upload(
            request=_authed_request(),
            name="test",
            req=PresignRequest(filenames=["a.csv", "b.jsonl", "c.parquet"]),
            lake=mock_lake,
            _user={"role": "editor"},
        )

        assert len(resp.uploads) == 3
        assert mock_blob_store.presigned_url.call_count == 3


class TestS3NativeResolve:
    """Test _resolve_blob_keys_smart — all keys download to temp (S3-native deferred)."""

    def test_csv_downloads_to_temp(self, tmp_path):
        from arrow_lake.api.routers.datasets import _resolve_blob_keys_smart

        mock_lake = MagicMock()
        mock_blob_store = _make_mock_blob_store()
        mock_lake._get_component.return_value = mock_blob_store

        s3_uris, local_paths = _resolve_blob_keys_smart(
            ["uploads/test/data.csv"], mock_lake, str(tmp_path),
        )

        assert len(s3_uris) == 0
        assert len(local_paths) == 1

    def test_mixed_extensions_all_download(self, tmp_path):
        from arrow_lake.api.routers.datasets import _resolve_blob_keys_smart

        mock_lake = MagicMock()
        mock_blob_store = _make_mock_blob_store()
        mock_lake._get_component.return_value = mock_blob_store

        s3_uris, local_paths = _resolve_blob_keys_smart(
            ["uploads/test/data.csv", "uploads/test/photo.jpg", "uploads/test/kb.jsonl"],
            mock_lake, str(tmp_path),
        )

        assert len(s3_uris) == 0
        assert len(local_paths) == 3


class TestConcurrentResolve:
    """Test that _resolve_blob_keys uses ThreadPoolExecutor."""

    def test_concurrent_download(self, tmp_path):
        from arrow_lake.api.routers.datasets import _resolve_blob_keys

        mock_lake = MagicMock()
        mock_blob_store = _make_mock_blob_store()
        mock_lake._get_component.return_value = mock_blob_store

        keys = [f"uploads/test/file{i}.jpg" for i in range(8)]
        paths = _resolve_blob_keys(keys, mock_lake, str(tmp_path))

        assert len(paths) == 8
        assert mock_blob_store.download_file.call_count == 8


class TestCleanupEndpoint:
    """Test the upload cleanup endpoint."""

    @pytest.mark.asyncio
    async def test_cleanup_uploads(self):
        from arrow_lake.api.routers.datasets import cleanup_uploads

        mock_lake = MagicMock()
        mock_blob_store = MagicMock()
        mock_blob_store.delete_prefix.return_value = 3
        mock_lake._get_component.return_value = mock_blob_store

        resp = await cleanup_uploads(
            request=_authed_request(),
            name="test",
            lake=mock_lake,
            _user={"role": "editor"},
        )

        assert resp.success is True
        assert resp.deleted_count == 3
        mock_blob_store.delete_prefix.assert_called_once_with("uploads/test/")

    def test_cleanup_response_model(self):
        resp = CleanupResponse(deleted_count=5)
        assert resp.success is True
        assert resp.deleted_count == 5


class TestIngestorS3Support:
    """Test that Ingestor handles S3 URIs in _detect_file_type."""

    def test_detect_s3_csv(self):
        from arrow_lake.ingest.ingestor import Ingestor
        assert Ingestor._detect_file_type("s3://bucket/uploads/ds/data.csv") == "csv"

    def test_detect_s3_jsonl(self):
        from arrow_lake.ingest.ingestor import Ingestor
        assert Ingestor._detect_file_type("s3://bucket/uploads/ds/kb.jsonl") == "json"

    def test_detect_s3_parquet(self):
        from arrow_lake.ingest.ingestor import Ingestor
        assert Ingestor._detect_file_type("s3://bucket/uploads/ds/data.parquet") == "parquet"

    def test_detect_local_csv(self):
        from arrow_lake.ingest.ingestor import Ingestor
        assert Ingestor._detect_file_type("/tmp/al_ingest_abc/data.csv") == "csv"

    def test_detect_unsupported_raises(self):
        from arrow_lake.exceptions import IngestError
        from arrow_lake.ingest.ingestor import Ingestor
        with pytest.raises(IngestError, match="Unsupported"):
            Ingestor._detect_file_type("s3://bucket/file.xyz")


# ---------------------------------------------------------------------------
# Phase 3: High/Medium security + logic fixes
# ---------------------------------------------------------------------------


class TestIngestorEmptyPaths:
    """Test that Ingestor.ingest rejects empty file_paths."""

    def test_empty_paths_raises(self):
        from arrow_lake.exceptions import IngestError
        from arrow_lake.ingest.ingestor import Ingestor
        with pytest.raises(IngestError, match="No file paths"):
            Ingestor(MagicMock()).ingest("ds", [])


class TestSSRFPrevention:
    """Test expanded private network SSRF prevention."""

    def test_ipv4_mapped_ipv6_blocked(self):
        from arrow_lake.api.models.dataset import IngestHttpRequest
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            IngestHttpRequest(urls=["http://[::ffff:127.0.0.1]/secret"])

    def test_carrier_grade_nat_blocked(self):
        from arrow_lake.api.models.dataset import IngestHttpRequest
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            IngestHttpRequest(urls=["http://100.64.0.1/secret"])


class TestDownloadVerification:
    """Test that _resolve_blob_keys verifies downloaded files."""

    def test_failed_download_cleans_up(self, tmp_path):
        from arrow_lake.api.routers.datasets import _resolve_blob_keys

        mock_lake = MagicMock()
        mock_blob_store = MagicMock()

        def _fail_download(key, dest):
            pass  # Simulate download that doesn't create file

        mock_blob_store.download_file.side_effect = _fail_download
        mock_lake._get_component.return_value = mock_blob_store

        with pytest.raises(OSError, match="verification failed"):
            _resolve_blob_keys(["uploads/test/data.csv"], mock_lake, str(tmp_path))


class TestIngestMixedUrlPaths:
    """Test that IngestMixedRequest allows absolute paths for urls."""

    def test_urls_allow_absolute(self):
        req = IngestMixedRequest(sources={"urls": ["/absolute/path"]})
        assert req.sources["urls"] == ["/absolute/path"]

    def test_files_reject_absolute(self):
        from pydantic import ValidationError
        with pytest.raises(ValidationError, match="Absolute"):
            IngestMixedRequest(sources={"files": ["/absolute/path"]})


class TestContentTypeValidation:
    """Test upload Content-Type validation."""

    @pytest.mark.asyncio
    async def test_reject_bad_content_type(self):
        from arrow_lake.api.routers.datasets import upload_files
        from fastapi import HTTPException

        mock_lake = MagicMock()
        mock_file = MagicMock(spec=["filename", "content_type", "read"])
        mock_file.filename = "evil.bin"
        mock_file.content_type = "executable/binary"
        mock_file.read = AsyncMock(return_value=b"\x00\x01")

        with pytest.raises(HTTPException) as exc_info:
            await upload_files(
                request=_authed_request(),
                name="test",
                files=[mock_file],
                lake=mock_lake,
                _user={"role": "editor"},
            )
        assert exc_info.value.status_code == 415
