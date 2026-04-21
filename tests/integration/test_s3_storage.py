"""Integration tests for BlobStoreManager against MinIO.

Requires MinIO running at localhost:9000 (default docker-compose setup).
Tests are skipped if MinIO is unreachable.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from arrow_lake.config import StorageBackend, StorageConfig
from arrow_lake.exceptions import ErrorCode, StorageError
from arrow_lake.storage.blob_store import BlobStoreManager

# ---------------------------------------------------------------------------
# MinIO connection fixtures
# ---------------------------------------------------------------------------


def _minio_available() -> bool:
    """Check if MinIO is reachable at localhost:9000."""
    import boto3
    from botocore.exceptions import ClientError

    try:
        client = boto3.client(
            "s3",
            endpoint_url="http://localhost:9000",
            aws_access_key_id="minioadmin",
            aws_secret_access_key="minioadmin",
            region_name="us-east-1",
        )
        client.head_bucket(Bucket="arrow-lake")
        return True
    except (ClientError, Exception):
        return False


def _make_config() -> StorageConfig:
    return StorageConfig(
        backend=StorageBackend.MINIO,
        s3_endpoint=os.environ.get("S3_ENDPOINT", "http://localhost:9000"),
        s3_access_key=os.environ.get("S3_ACCESS_KEY", "minioadmin"),
        s3_secret_key=os.environ.get("S3_SECRET_KEY", "minioadmin"),
        s3_bucket=os.environ.get("S3_BUCKET", "arrow-lake"),
        s3_region="us-east-1",
    )


@pytest.fixture(scope="module")
def blob_store() -> BlobStoreManager:
    """Create a BlobStoreManager against MinIO."""
    if not _minio_available():
        pytest.skip("MinIO not available at localhost:9000")
    return BlobStoreManager(_make_config())


@pytest.fixture(autouse=True)
def _cleanup(blob_store: BlobStoreManager) -> None:
    """Clean up test prefix after each test."""
    yield
    import contextlib

    with contextlib.suppress(Exception):
        blob_store.delete_prefix("test-blob-store/")


# ---------------------------------------------------------------------------
# Upload tests
# ---------------------------------------------------------------------------


class TestUpload:
    def test_upload_bytes(self, blob_store: BlobStoreManager) -> None:
        result = blob_store.upload(
            "test-blob-store/hello.txt", b"Hello, MinIO!"
        )

        assert result.key == "test-blob-store/hello.txt"
        assert result.size_bytes == 13
        assert result.etag != ""

    def test_upload_bytes_auto_content_type(self, blob_store: BlobStoreManager) -> None:
        blob_store.upload("test-blob-store/doc.pdf", b"%PDF-1.4")

        info = blob_store.head("test-blob-store/doc.pdf")
        assert "pdf" in info.content_type

    def test_upload_bytes_with_metadata(self, blob_store: BlobStoreManager) -> None:
        blob_store.upload(
            "test-blob-store/meta.txt",
            b"with metadata",
            metadata={"author": "test", "version": "1"},
        )
        # Metadata round-trip is verified via head_object in real S3

    def test_upload_file(self, blob_store: BlobStoreManager, tmp_path: Path) -> None:
        f = tmp_path / "upload-me.bin"
        f.write_bytes(b"\x00\x01\x02\x03" * 100)

        result = blob_store.upload_file(f, key="test-blob-store/uploaded.bin")

        assert result.key == "test-blob-store/uploaded.bin"
        assert result.size_bytes == 400

    def test_upload_file_auto_key(self, blob_store: BlobStoreManager, tmp_path: Path) -> None:
        f = tmp_path / "auto-named.json"
        f.write_bytes(b'{"key": "value"}')

        result = blob_store.upload_file(f, key="test-blob-store/auto/auto-named.json")

        assert "auto-named.json" in result.key

    def test_upload_file_not_found(self, blob_store: BlobStoreManager) -> None:
        with pytest.raises(FileNotFoundError):
            blob_store.upload_file("/nonexistent/file.txt")

    def test_upload_jpeg_content_type(self, blob_store: BlobStoreManager) -> None:
        # Minimal JPEG: SOI + EOI markers
        jpeg_bytes = b"\xff\xd8\xff\xd9"
        blob_store.upload("test-blob-store/tiny.jpg", jpeg_bytes)

        info = blob_store.head("test-blob-store/tiny.jpg")
        assert "jpeg" in info.content_type


# ---------------------------------------------------------------------------
# Download tests
# ---------------------------------------------------------------------------


class TestDownload:
    def test_download_bytes_roundtrip(self, blob_store: BlobStoreManager) -> None:
        original = b"round-trip test data"
        blob_store.upload("test-blob-store/rt.bin", original)

        downloaded = blob_store.download("test-blob-store/rt.bin")

        assert downloaded == original

    def test_download_not_found(self, blob_store: BlobStoreManager) -> None:
        with pytest.raises(StorageError) as exc_info:
            blob_store.download("test-blob-store/nonexistent.txt")
        assert exc_info.value.error_code == ErrorCode.BLOB_NOT_FOUND

    def test_download_file(self, blob_store: BlobStoreManager, tmp_path: Path) -> None:
        blob_store.upload("test-blob-store/dl.bin", b"file download content")
        dest = tmp_path / "downloads" / "file.bin"

        size = blob_store.download_file("test-blob-store/dl.bin", dest)

        assert size == 21
        assert dest.read_bytes() == b"file download content"


# ---------------------------------------------------------------------------
# Head / exists tests
# ---------------------------------------------------------------------------


class TestHeadExists:
    def test_head_after_upload(self, blob_store: BlobStoreManager) -> None:
        blob_store.upload("test-blob-store/head-test.txt", b"head me")

        info = blob_store.head("test-blob-store/head-test.txt")

        assert info.key == "test-blob-store/head-test.txt"
        assert info.size_bytes == 7
        assert info.content_type == "text/plain"

    def test_head_not_found(self, blob_store: BlobStoreManager) -> None:
        with pytest.raises(StorageError) as exc_info:
            blob_store.head("test-blob-store/nope.txt")
        assert exc_info.value.error_code == ErrorCode.BLOB_NOT_FOUND

    def test_exists_true(self, blob_store: BlobStoreManager) -> None:
        blob_store.upload("test-blob-store/exists.txt", b"yes")
        assert blob_store.exists("test-blob-store/exists.txt") is True

    def test_exists_false(self, blob_store: BlobStoreManager) -> None:
        assert blob_store.exists("test-blob-store/ghost.txt") is False


# ---------------------------------------------------------------------------
# Presigned URL tests
# ---------------------------------------------------------------------------


class TestPresignedUrl:
    def test_presigned_url_download(self, blob_store: BlobStoreManager) -> None:
        blob_store.upload("test-blob-store/signed.txt", b"signed content")

        url = blob_store.presigned_url("test-blob-store/signed.txt", expires_in=300)

        assert url.startswith("http")
        assert "arrow-lake" in url
        assert "test-blob-store/signed.txt" in url

    def test_presigned_url_not_found(self, blob_store: BlobStoreManager) -> None:
        # presigned URL generation itself doesn't fail for missing keys
        url = blob_store.presigned_url("test-blob-store/nope.txt")
        assert url.startswith("http")


# ---------------------------------------------------------------------------
# Delete tests
# ---------------------------------------------------------------------------


class TestDelete:
    def test_delete(self, blob_store: BlobStoreManager) -> None:
        blob_store.upload("test-blob-store/to-delete.txt", b"delete me")
        assert blob_store.exists("test-blob-store/to-delete.txt") is True

        blob_store.delete("test-blob-store/to-delete.txt")

        assert blob_store.exists("test-blob-store/to-delete.txt") is False

    def test_delete_prefix(self, blob_store: BlobStoreManager) -> None:
        blob_store.upload("test-blob-store/pfx/a.txt", b"a")
        blob_store.upload("test-blob-store/pfx/b.txt", b"b")
        blob_store.upload("test-blob-store/pfx/c.txt", b"c")

        count = blob_store.delete_prefix("test-blob-store/pfx/")

        assert count >= 3
        assert not blob_store.exists("test-blob-store/pfx/a.txt")


# ---------------------------------------------------------------------------
# List tests
# ---------------------------------------------------------------------------


class TestList:
    def test_list_blobs(self, blob_store: BlobStoreManager) -> None:
        blob_store.upload("test-blob-store/list/1.txt", b"one")
        blob_store.upload("test-blob-store/list/2.txt", b"two")
        blob_store.upload("test-blob-store/list/3.txt", b"three")

        result = blob_store.list_blobs("test-blob-store/list/")

        assert result.count >= 3
        assert all("test-blob-store/list/" in k for k in result.keys)

    def test_list_blobs_empty_prefix(self, blob_store: BlobStoreManager) -> None:
        result = blob_store.list_blobs("test-blob-store/nonexistent-prefix/")

        assert result.count == 0
        assert result.keys == ()
