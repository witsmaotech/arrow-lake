"""Tests for BlobStoreManager — Story M1."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from arrow_lake.config import StorageBackend, StorageConfig
from arrow_lake.exceptions import ErrorCode, StorageError
from arrow_lake.storage.blob_store import (
    BlobInfo,
    BlobListResult,
    BlobStoreManager,
    BlobUploadResult,
    _BytesReader,
    _guess_content_type,
    _validate_blob_key,
)
from botocore.exceptions import ClientError

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_config(**overrides) -> StorageConfig:
    defaults = {
        "backend": StorageBackend.MINIO,
        "s3_endpoint": "http://localhost:9000",
        "s3_access_key": "test-key",
        "s3_secret_key": "test-secret",
        "s3_bucket": "test-bucket",
        "s3_region": "us-east-1",
    }
    defaults.update(overrides)
    return StorageConfig(**defaults)


def _make_s3_client() -> MagicMock:
    return MagicMock()


def _mock_head_response(**overrides) -> dict:
    defaults = {
        "ContentLength": 1024,
        "LastModified": datetime(2026, 1, 15, 12, 0, 0, tzinfo=UTC),
        "ContentType": "image/png",
        "ETag": '"abc123"',
    }
    defaults.update(overrides)
    return defaults


def _client_error(code: str, operation: str = "Operation") -> ClientError:
    return ClientError({"Error": {"Code": code}}, operation)


# ---------------------------------------------------------------------------
# Key validation tests
# ---------------------------------------------------------------------------


class TestValidateBlobKey:
    def test_simple_key(self) -> None:
        _validate_blob_key("my-file.pdf")

    def test_path_key(self) -> None:
        _validate_blob_key("documents/reports/2026/q1.pdf")

    def test_leading_slash(self) -> None:
        _validate_blob_key("/documents/report.pdf")

    def test_trailing_slash(self) -> None:
        _validate_blob_key("documents/")

    def test_empty_key_raises(self) -> None:
        with pytest.raises(ValueError, match="empty"):
            _validate_blob_key("")

    def test_whitespace_key_raises(self) -> None:
        with pytest.raises(ValueError, match="empty"):
            _validate_blob_key("   ")

    def test_path_traversal_raises(self) -> None:
        with pytest.raises(ValueError, match="Invalid blob key segment"):
            _validate_blob_key("../etc/passwd")

    def test_null_byte_raises(self) -> None:
        with pytest.raises(ValueError, match="null bytes"):
            _validate_blob_key("file\x00.txt")

    def test_null_byte_in_segment_raises(self) -> None:
        with pytest.raises(ValueError, match="null bytes"):
            _validate_blob_key("path/file\x00name.txt")

    def test_sql_injection_segment_raises(self) -> None:
        with pytest.raises(ValueError, match="Invalid blob key segment"):
            _validate_blob_key("data/DROP TABLE users")

    def test_dot_allowed(self) -> None:
        _validate_blob_key("test.txt")

    def test_version_suffix(self) -> None:
        _validate_blob_key("image-v2.1.png")


# ---------------------------------------------------------------------------
# Content type guessing
# ---------------------------------------------------------------------------


class TestGuessContentType:
    def test_pdf(self) -> None:
        assert _guess_content_type("doc.pdf") == "application/pdf"

    def test_jpeg(self) -> None:
        assert _guess_content_type("photo.jpg") == "image/jpeg"

    def test_png(self) -> None:
        assert _guess_content_type("image.png") == "image/png"

    def test_unknown_suffix(self) -> None:
        assert _guess_content_type("data.zzzz") == "application/octet-stream"

    def test_no_suffix(self) -> None:
        assert _guess_content_type("README") == "application/octet-stream"


# ---------------------------------------------------------------------------
# _BytesReader
# ---------------------------------------------------------------------------


class TestBytesReader:
    def test_read_all(self) -> None:
        reader = _BytesReader(b"hello world")
        assert reader.read() == b"hello world"

    def test_read_partial(self) -> None:
        reader = _BytesReader(b"hello world")
        assert reader.read(5) == b"hello"
        assert reader.read() == b" world"

    def test_seek_tell(self) -> None:
        reader = _BytesReader(b"abcdefgh")
        assert reader.tell() == 0
        reader.seek(3)
        assert reader.tell() == 3
        assert reader.read(2) == b"de"
        reader.seek(0, 2)  # seek to end
        assert reader.tell() == 8


# ---------------------------------------------------------------------------
# BlobStoreManager.upload
# ---------------------------------------------------------------------------


class TestUpload:
    def test_upload_bytes(self) -> None:
        s3 = _make_s3_client()
        s3.put_object.return_value = {"ETag": '"etag1"'}
        config = _make_config()
        mgr = BlobStoreManager(config, s3_client=s3)

        result = mgr.upload("test.txt", b"hello")

        assert isinstance(result, BlobUploadResult)
        assert result.key == "test.txt"
        assert result.size_bytes == 5
        assert result.etag == '"etag1"'
        s3.put_object.assert_called_once()
        call_kwargs = s3.put_object.call_args[1]
        assert call_kwargs["Bucket"] == "test-bucket"
        assert call_kwargs["Key"] == "test.txt"
        assert call_kwargs["ContentType"] == "text/plain"

    def test_upload_bytes_custom_content_type(self) -> None:
        s3 = _make_s3_client()
        s3.put_object.return_value = {"ETag": '"etag2"'}
        mgr = BlobStoreManager(_make_config(), s3_client=s3)

        mgr.upload("data.bin", b"\x00\x01", content_type="application/binary")

        call_kwargs = s3.put_object.call_args[1]
        assert call_kwargs["ContentType"] == "application/binary"

    def test_upload_bytes_with_metadata(self) -> None:
        s3 = _make_s3_client()
        s3.put_object.return_value = {"ETag": '"etag3"'}
        mgr = BlobStoreManager(_make_config(), s3_client=s3)

        mgr.upload("doc.pdf", b"%PDF", metadata={"author": "test"})

        call_kwargs = s3.put_object.call_args[1]
        assert call_kwargs["Metadata"] == {"author": "test"}

    def test_upload_invalid_key_raises(self) -> None:
        mgr = BlobStoreManager(_make_config(), s3_client=_make_s3_client())
        with pytest.raises(ValueError):
            mgr.upload("../etc/passwd", b"data")

    def test_upload_s3_error_raises(self) -> None:
        s3 = _make_s3_client()
        s3.put_object.side_effect = _client_error("AccessDenied", "PutObject")
        mgr = BlobStoreManager(_make_config(), s3_client=s3)

        with pytest.raises(StorageError) as exc_info:
            mgr.upload("fail.txt", b"data")
        assert exc_info.value.error_code == ErrorCode.BLOB_UPLOAD_FAILED

    def test_upload_large_uses_multipart(self) -> None:
        s3 = _make_s3_client()
        s3.create_multipart_upload.return_value = {"UploadId": "uid-1"}
        s3.upload_part.return_value = {"ETag": '"part-etag"'}
        s3.complete_multipart_upload.return_value = {"ETag": '"multipart-etag"'}
        mgr = BlobStoreManager(_make_config(), s3_client=s3)

        large_data = b"x" * (9 * 1024 * 1024)  # 9 MB
        result = mgr.upload("large.bin", large_data)

        assert result.key == "large.bin"
        assert result.size_bytes == len(large_data)
        s3.create_multipart_upload.assert_called_once()
        s3.complete_multipart_upload.assert_called_once()

    def test_multipart_abort_on_failure(self) -> None:
        """Failed multipart upload should abort the upload."""
        s3 = _make_s3_client()
        s3.create_multipart_upload.return_value = {"UploadId": "uid-fail"}
        s3.upload_part.side_effect = _client_error("InternalError", "UploadPart")
        s3.abort_multipart_upload.return_value = {}
        mgr = BlobStoreManager(_make_config(), s3_client=s3)

        with pytest.raises(StorageError) as exc_info:
            mgr.upload("fail.bin", b"x" * (9 * 1024 * 1024))

        assert exc_info.value.error_code == ErrorCode.BLOB_UPLOAD_FAILED
        s3.abort_multipart_upload.assert_called_once_with(
            Bucket="test-bucket", Key="fail.bin", UploadId="uid-fail"
        )

    def test_multipart_empty_body_fallback(self) -> None:
        """Empty body in multipart triggers zero-byte put_object fallback."""
        s3 = _make_s3_client()
        s3.create_multipart_upload.return_value = {"UploadId": "uid-empty"}
        s3.abort_multipart_upload.return_value = {}
        s3.put_object.return_value = {"ETag": '"empty-etag"'}
        mgr = BlobStoreManager(_make_config(), s3_client=s3)

        # Pass data that's larger than threshold but has a reader that returns empty
        # We need to simulate a stream that reports > 8MB but reads 0 bytes.
        mock_reader = MagicMock()
        mock_reader.read.return_value = b""  # Immediate EOF
        mock_reader.seek.return_value = 0
        mock_reader.tell.return_value = 9 * 1024 * 1024  # Reports 9MB

        result = mgr.upload("empty.bin", mock_reader)

        assert result.etag == '"empty-etag"'
        s3.abort_multipart_upload.assert_called_once()

    def test_upload_exceeds_max_multipart_size_raises(self) -> None:
        """Files exceeding S3 10,000 part limit should be rejected."""
        s3 = _make_s3_client()
        mgr = BlobStoreManager(_make_config(), s3_client=s3)

        # Pretend the file is 80 TB (> 10,000 * 8 MB)
        huge_size = 80 * 1024 * 1024 * 1024 * 1024
        huge_data = _BytesReader(b"x")
        with (
            patch("arrow_lake.storage.blob_store._get_stream_size", return_value=huge_size),
            pytest.raises(ValueError, match="exceeds maximum multipart upload size"),
        ):
            mgr.upload("huge.bin", huge_data)


# ---------------------------------------------------------------------------
# BlobStoreManager.upload_file
# ---------------------------------------------------------------------------


class TestUploadFile:
    def test_upload_file(self, tmp_path) -> None:
        s3 = _make_s3_client()
        s3.put_object.return_value = {"ETag": '"etag-file"'}
        mgr = BlobStoreManager(_make_config(), s3_client=s3)

        test_file = tmp_path / "hello.txt"
        test_file.write_bytes(b"file content")

        result = mgr.upload_file(test_file)

        assert result.key == "hello.txt"
        assert result.size_bytes == 12
        s3.put_object.assert_called_once()

    def test_upload_file_custom_key(self, tmp_path) -> None:
        s3 = _make_s3_client()
        s3.put_object.return_value = {"ETag": '"etag-ck"'}
        mgr = BlobStoreManager(_make_config(), s3_client=s3)

        test_file = tmp_path / "local.txt"
        test_file.write_bytes(b"data")

        result = mgr.upload_file(test_file, key="remote/path/data.txt")

        assert result.key == "remote/path/data.txt"
        assert s3.put_object.call_args[1]["Key"] == "remote/path/data.txt"

    def test_upload_file_not_found(self, tmp_path) -> None:
        mgr = BlobStoreManager(_make_config(), s3_client=_make_s3_client())

        with pytest.raises(FileNotFoundError):
            mgr.upload_file(tmp_path / "nonexistent.txt")


# ---------------------------------------------------------------------------
# BlobStoreManager.download
# ---------------------------------------------------------------------------


class TestDownload:
    def test_download_bytes(self) -> None:
        s3 = _make_s3_client()
        body_mock = MagicMock()
        body_mock.read.return_value = b"downloaded content"
        s3.get_object.return_value = {"Body": body_mock}
        mgr = BlobStoreManager(_make_config(), s3_client=s3)

        data = mgr.download("test.txt")

        assert data == b"downloaded content"
        s3.get_object.assert_called_once_with(Bucket="test-bucket", Key="test.txt")

    def test_download_not_found(self) -> None:
        s3 = _make_s3_client()
        s3.get_object.side_effect = _client_error("NoSuchKey", "GetObject")
        mgr = BlobStoreManager(_make_config(), s3_client=s3)

        with pytest.raises(StorageError) as exc_info:
            mgr.download("missing.txt")
        assert exc_info.value.error_code == ErrorCode.BLOB_NOT_FOUND

    def test_download_error(self) -> None:
        s3 = _make_s3_client()
        s3.get_object.side_effect = _client_error("InternalError", "GetObject")
        mgr = BlobStoreManager(_make_config(), s3_client=s3)

        with pytest.raises(StorageError) as exc_info:
            mgr.download("broken.txt")
        assert exc_info.value.error_code == ErrorCode.BLOB_DOWNLOAD_FAILED


# ---------------------------------------------------------------------------
# BlobStoreManager.download_file
# ---------------------------------------------------------------------------


class TestDownloadFile:
    def test_download_file(self, tmp_path) -> None:
        s3 = _make_s3_client()

        # download_file mock should actually write the file
        def _write_file(bucket, key, path):
            Path(path).parent.mkdir(parents=True, exist_ok=True)
            Path(path).write_bytes(b"downloaded content")

        s3.download_file.side_effect = _write_file
        mgr = BlobStoreManager(_make_config(), s3_client=s3)

        dest = tmp_path / "downloaded" / "file.txt"
        size = mgr.download_file("remote.txt", dest)

        assert size == 18  # len("downloaded content")
        assert dest.read_bytes() == b"downloaded content"
        s3.download_file.assert_called_once_with("test-bucket", "remote.txt", str(dest))

    def test_download_file_creates_dirs(self, tmp_path) -> None:
        s3 = _make_s3_client()

        def _write_file(bucket, key, path):
            Path(path).parent.mkdir(parents=True, exist_ok=True)
            Path(path).write_bytes(b"data")

        s3.download_file.side_effect = _write_file
        mgr = BlobStoreManager(_make_config(), s3_client=s3)

        dest = tmp_path / "deep" / "nested" / "file.bin"
        mgr.download_file("data.bin", dest)
        assert dest.parent.exists()
        assert dest.read_bytes() == b"data"


# ---------------------------------------------------------------------------
# BlobStoreManager.presigned_url
# ---------------------------------------------------------------------------


class TestPresignedUrl:
    def test_presigned_url(self) -> None:
        s3 = _make_s3_client()
        s3.generate_presigned_url.return_value = "https://minio:9000/bucket/key?sig=abc"
        mgr = BlobStoreManager(_make_config(), s3_client=s3)

        url = mgr.presigned_url("doc.pdf")

        assert url == "https://minio:9000/bucket/key?sig=abc"
        s3.generate_presigned_url.assert_called_once()
        call_kwargs = s3.generate_presigned_url.call_args[1]
        assert call_kwargs["ExpiresIn"] == 3600

    def test_presigned_url_custom_expiry(self) -> None:
        s3 = _make_s3_client()
        s3.generate_presigned_url.return_value = "https://url"
        mgr = BlobStoreManager(_make_config(), s3_client=s3)

        mgr.presigned_url("doc.pdf", expires_in=7200)

        call_kwargs = s3.generate_presigned_url.call_args[1]
        assert call_kwargs["ExpiresIn"] == 7200

    def test_presigned_url_invalid_key_raises(self) -> None:
        s3 = _make_s3_client()
        mgr = BlobStoreManager(_make_config(), s3_client=s3)

        with pytest.raises(ValueError):
            mgr.presigned_url("../etc/passwd")

    def test_presigned_url_invalid_operation_raises(self) -> None:
        s3 = _make_s3_client()
        mgr = BlobStoreManager(_make_config(), s3_client=s3)

        with pytest.raises(ValueError, match="Unsupported presigned operation"):
            mgr.presigned_url("doc.pdf", operation="delete_object")


# ---------------------------------------------------------------------------
# BlobStoreManager.head / exists
# ---------------------------------------------------------------------------


class TestHead:
    def test_head_success(self) -> None:
        s3 = _make_s3_client()
        s3.head_object.return_value = _mock_head_response()
        mgr = BlobStoreManager(_make_config(), s3_client=s3)

        info = mgr.head("img.png")

        assert isinstance(info, BlobInfo)
        assert info.key == "img.png"
        assert info.size_bytes == 1024
        assert info.content_type == "image/png"
        assert info.etag == '"abc123"'

    def test_head_not_found(self) -> None:
        s3 = _make_s3_client()
        s3.head_object.side_effect = _client_error("404", "HeadObject")
        mgr = BlobStoreManager(_make_config(), s3_client=s3)

        with pytest.raises(StorageError) as exc_info:
            mgr.head("missing.png")
        assert exc_info.value.error_code == ErrorCode.BLOB_NOT_FOUND


class TestExists:
    def test_exists_true(self) -> None:
        s3 = _make_s3_client()
        s3.head_object.return_value = _mock_head_response()
        mgr = BlobStoreManager(_make_config(), s3_client=s3)

        assert mgr.exists("file.txt") is True

    def test_exists_false(self) -> None:
        s3 = _make_s3_client()
        s3.head_object.side_effect = _client_error("404", "HeadObject")
        mgr = BlobStoreManager(_make_config(), s3_client=s3)

        assert mgr.exists("file.txt") is False


# ---------------------------------------------------------------------------
# BlobStoreManager.delete
# ---------------------------------------------------------------------------


class TestDelete:
    def test_delete_success(self) -> None:
        s3 = _make_s3_client()
        s3.delete_object.return_value = {}
        mgr = BlobStoreManager(_make_config(), s3_client=s3)

        mgr.delete("old.txt")

        s3.delete_object.assert_called_once_with(Bucket="test-bucket", Key="old.txt")

    def test_delete_error(self) -> None:
        s3 = _make_s3_client()
        s3.delete_object.side_effect = _client_error("AccessDenied", "DeleteObject")
        mgr = BlobStoreManager(_make_config(), s3_client=s3)

        with pytest.raises(StorageError) as exc_info:
            mgr.delete("protected.txt")
        assert exc_info.value.error_code == ErrorCode.BLOB_DELETE_FAILED

    def test_delete_prefix(self) -> None:
        s3 = _make_s3_client()
        s3.get_paginator.return_value.paginate.return_value = [
            {
                "Contents": [
                    {"Key": "temp/a.txt"},
                    {"Key": "temp/b.txt"},
                ]
            },
            {"Contents": []},
        ]
        s3.delete_objects.return_value = {}
        mgr = BlobStoreManager(_make_config(), s3_client=s3)

        count = mgr.delete_prefix("temp/")

        assert count == 2
        s3.delete_objects.assert_called_once()
        call_kwargs = s3.delete_objects.call_args[1]
        assert len(call_kwargs["Delete"]["Objects"]) == 2

    def test_delete_prefix_empty(self) -> None:
        s3 = _make_s3_client()
        s3.get_paginator.return_value.paginate.return_value = [{"Contents": []}]
        mgr = BlobStoreManager(_make_config(), s3_client=s3)

        count = mgr.delete_prefix("empty/")
        assert count == 0


# ---------------------------------------------------------------------------
# BlobStoreManager.list_blobs
# ---------------------------------------------------------------------------


class TestListBlobs:
    def test_list_blobs(self) -> None:
        s3 = _make_s3_client()
        s3.list_objects_v2.return_value = {
            "Contents": [
                {"Key": "docs/a.pdf"},
                {"Key": "docs/b.pdf"},
            ],
            "IsTruncated": False,
        }
        mgr = BlobStoreManager(_make_config(), s3_client=s3)

        result = mgr.list_blobs("docs/")

        assert isinstance(result, BlobListResult)
        assert result.count == 2
        assert result.keys == ("docs/a.pdf", "docs/b.pdf")
        assert result.truncated is False

    def test_list_blobs_empty(self) -> None:
        s3 = _make_s3_client()
        s3.list_objects_v2.return_value = {"IsTruncated": False}
        mgr = BlobStoreManager(_make_config(), s3_client=s3)

        result = mgr.list_blobs("empty/")

        assert result.count == 0
        assert result.keys == ()

    def test_list_blobs_pagination(self) -> None:
        s3 = _make_s3_client()
        s3.list_objects_v2.return_value = {
            "Contents": [{"Key": "x"}],
            "IsTruncated": True,
            "NextContinuationToken": "token123",
        }
        mgr = BlobStoreManager(_make_config(), s3_client=s3)

        result = mgr.list_blobs("prefix/", max_keys=1)
        assert result.truncated is True

        mgr.list_blobs("prefix/", max_keys=1, continuation_token="token123")
        assert s3.list_objects_v2.call_args[1]["ContinuationToken"] == "token123"


# ---------------------------------------------------------------------------
# BlobStoreManager construction
# ---------------------------------------------------------------------------


class TestConstruction:
    def test_custom_bucket(self) -> None:
        mgr = BlobStoreManager(_make_config(), bucket="custom-bucket", s3_client=_make_s3_client())
        assert mgr.bucket == "custom-bucket"

    def test_default_bucket_from_config(self) -> None:
        mgr = BlobStoreManager(_make_config(), s3_client=_make_s3_client())
        assert mgr.bucket == "test-bucket"

    @patch("boto3.client")
    def test_auto_creates_boto3_client(self, mock_boto3_client) -> None:
        s3 = MagicMock()
        mock_boto3_client.return_value = s3
        mgr = BlobStoreManager(_make_config())

        assert mgr._s3 is s3
        mock_boto3_client.assert_called_once()
        call_kwargs = mock_boto3_client.call_args[1]
        assert call_kwargs["endpoint_url"] == "http://localhost:9000"
        assert call_kwargs["region_name"] == "us-east-1"
