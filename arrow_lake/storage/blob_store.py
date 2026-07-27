"""Blob storage operations — Story M1.

Provides BlobStoreManager for raw blob upload/download/presigned_url/delete
against S3-compatible backends (MinIO, AWS S3).

Uses boto3 for all S3 operations. Follows the same credential resolution
pattern as S3Connector (connectors.py) and BlobLifecycleManager (lifecycle.py).
"""

from __future__ import annotations

import mimetypes
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, BinaryIO

import botocore.exceptions
import structlog

from arrow_lake.config import StorageConfig
from arrow_lake.exceptions import ErrorCode, StorageError

_log = structlog.get_logger(__name__)

__all__ = ["BlobInfo", "BlobListResult", "BlobStoreManager", "BlobUploadResult"]

# Default content type when none can be detected.
_DEFAULT_CONTENT_TYPE = "application/octet-stream"

# Multipart upload threshold (bytes): files larger than this use multipart.
_MULTIPART_THRESHOLD = 8 * 1024 * 1024  # 8 MB
_MULTIPART_CHUNK_SIZE = 8 * 1024 * 1024  # 8 MB
_S3_MAX_PARTS = 10_000  # S3 multipart upload part limit


@dataclass(frozen=True)
class BlobInfo:
    """Metadata about a stored blob.

    Attributes:
        key: Object key in the bucket.
        size_bytes: Size in bytes.
        last_modified: ISO-8601 timestamp string.
        content_type: MIME type (may be empty string).
        etag: Object ETag.
    """

    key: str
    size_bytes: int
    last_modified: str
    content_type: str
    etag: str


@dataclass(frozen=True)
class BlobUploadResult:
    """Result of a blob upload operation.

    Attributes:
        key: Object key in the bucket.
        size_bytes: Size of the uploaded data.
        etag: Object ETag from S3.
    """

    key: str
    size_bytes: int
    etag: str


@dataclass(frozen=True)
class BlobListResult:
    """Result of a blob list operation.

    Attributes:
        keys: Tuple of matching object keys.
        count: Number of objects found.
        truncated: Whether more results exist (pagination).
        next_token: Pagination token for the next page (None = no more pages).
    """

    keys: tuple[str, ...]
    count: int
    truncated: bool
    next_token: str | None = None


class BlobStoreManager:
    """Manages raw blob storage on S3-compatible backends.

    Provides upload, download, presigned URL, delete, head, and list
    operations using boto3. Designed for MinIO and AWS S3.

    Thread safety: boto3 S3 clients are thread-safe. This class is safe
    for concurrent reads and writes (S3 provides read-after-write consistency).

    Args:
        config: Storage configuration with S3 credentials.
        bucket: Target bucket name (None = use config.s3_bucket).
        s3_client: Pre-configured boto3 S3 client (None = auto-create).
    """

    def __init__(
        self,
        config: StorageConfig,
        bucket: str | None = None,
        s3_client: Any | None = None,
    ) -> None:
        self._config = config
        self._bucket = bucket or config.s3_bucket

        if s3_client is not None:
            self._s3 = s3_client
        else:
            import boto3
            from botocore.config import Config as BotoConfig

            opts: dict[str, Any] = {
                "endpoint_url": config.s3_endpoint,
                "aws_access_key_id": config.s3_access_key,
                "aws_secret_access_key": config.s3_secret_key,
                "region_name": config.s3_region,
                "config": BotoConfig(
                    signature_version="s3v4",
                    s3={"addressing_style": "path"},
                ),
            }
            # Drop empty credential values so boto3 falls back to
            # instance profile / env vars.
            opts = {k: v for k, v in opts.items() if v}
            self._s3 = boto3.client("s3", **opts)
        # v1.9.5 批6: auto-create the bucket if it's not the primary data
        # bucket (i.e. the uploads bucket), so the first upload doesn't 404.
        # Also apply an expiration lifecycle rule if configured (>0 days).
        if self._bucket != self._config.s3_bucket:
            self.ensure_bucket()
            exp = getattr(self._config, "uploads_expiration_days", 0)
            if exp > 0:
                self.set_lifecycle_expiration(exp)

    @property
    def bucket(self) -> str:
        return self._bucket

    def ensure_bucket(self) -> None:
        """Create the bucket if it doesn't exist (idempotent, best-effort).

        Used for the uploads bucket so the first upload doesn't 404. The
        primary data bucket is assumed to already exist.
        """
        from botocore.exceptions import ClientError

        try:
            self._s3.head_bucket(Bucket=self._bucket)
            return
        except ClientError:
            pass
        try:
            self._s3.create_bucket(Bucket=self._bucket)
            _log.info("blob_bucket_created", bucket=self._bucket)
        except (ClientError, botocore.exceptions.BotoCoreError, OSError) as exc:
            _log.warning("blob_bucket_create_failed", bucket=self._bucket, error=str(exc))

    def set_lifecycle_expiration(self, days: int) -> None:
        """Set a bucket lifecycle expiration rule (delete objects after N days).

        Idempotent (rule ID ``uploads-expiration`` replaces prior). Best-effort
        — a failure to set the rule is logged, never raised, so uploads keep
        working. minio community edition supports expiration natively (no
        remote tier needed).
        """
        from botocore.exceptions import ClientError

        if days <= 0:
            return
        rules = [{
            "ID": "uploads-expiration",
            "Status": "Enabled",
            "Filter": {"Prefix": ""},
            "Expiration": {"Days": days},
        }]
        try:
            self._s3.put_bucket_lifecycle_configuration(
                Bucket=self._bucket,
                LifecycleConfiguration={"Rules": rules},
            )
            _log.info("blob_lifecycle_set", bucket=self._bucket, expiration_days=days)
        except (ClientError, botocore.exceptions.BotoCoreError, OSError) as exc:
            _log.warning("blob_lifecycle_set_failed", bucket=self._bucket, error=str(exc))

    # ------------------------------------------------------------------
    # Upload
    # ------------------------------------------------------------------

    def upload(
        self,
        key: str,
        data: bytes | BinaryIO,
        *,
        content_type: str | None = None,
        metadata: dict[str, str] | None = None,
    ) -> BlobUploadResult:
        """Upload a blob to S3.

        Files larger than 8 MB are automatically uploaded via multipart.

        Args:
            key: Destination object key (must match SAFE_IDENTIFIER_RE or
                 contain '/'-separated path segments).
            data: Raw bytes or file-like object.
            content_type: MIME type (None = auto-detect from key suffix).
            metadata: Optional user metadata dict.

        Returns:
            BlobUploadResult with key, size, and ETag.

        Raises:
            StorageError: If upload fails (BLOB_UPLOAD_FAILED).
            ValueError: If key is invalid.
        """
        _validate_blob_key(key)

        if content_type is None:
            content_type = _guess_content_type(key)

        if isinstance(data, (bytes, bytearray)):
            body: BinaryIO = _BytesReader(data)
            size = len(data)
        else:
            body = data
            size = _get_stream_size(data)

        try:
            if size > _MULTIPART_THRESHOLD:
                if size > _S3_MAX_PARTS * _MULTIPART_CHUNK_SIZE:
                    raise ValueError(
                        f"File size ({size} bytes) exceeds maximum multipart upload size "
                        f"({_S3_MAX_PARTS * _MULTIPART_CHUNK_SIZE} bytes)"
                    )
                etag = self._multipart_upload(key, body, size, content_type, metadata)
            else:
                extra: dict[str, Any] = {"ContentType": content_type}
                if metadata:
                    extra["Metadata"] = metadata
                resp = self._s3.put_object(
                    Bucket=self._bucket,
                    Key=key,
                    Body=body,
                    **extra,
                )
                etag = resp.get("ETag", "")

            _log.info("blob_uploaded", key=key, size=size, bucket=self._bucket)
            return BlobUploadResult(key=key, size_bytes=size, etag=etag)
        except StorageError:
            raise
        except ValueError:
            raise
        except (OSError, botocore.exceptions.ClientError, botocore.exceptions.BotoCoreError) as exc:
            raise StorageError(
                error_code=ErrorCode.BLOB_UPLOAD_FAILED,
                message=f"Failed to upload blob '{key}': {exc}",
            ) from exc

    def upload_file(
        self,
        file_path: str | Path,
        key: str | None = None,
        *,
        content_type: str | None = None,
        metadata: dict[str, str] | None = None,
    ) -> BlobUploadResult:
        """Upload a local file to S3.

        Args:
            file_path: Path to the local file.
            key: Destination key (None = use file name).
            content_type: MIME type (None = auto-detect).
            metadata: Optional user metadata dict.

        Returns:
            BlobUploadResult with key, size, and ETag.

        Raises:
            StorageError: If upload fails.
            FileNotFoundError: If file_path does not exist.
        """
        path = Path(file_path)
        if not path.exists():
            raise FileNotFoundError(f"File not found: {path}")

        effective_key = key or path.name
        size = path.stat().st_size

        if content_type is None:
            content_type = _guess_content_type(effective_key)

        try:
            extra: dict[str, Any] = {"ContentType": content_type}
            if metadata:
                extra["Metadata"] = metadata

            if size > _MULTIPART_THRESHOLD:
                if size > _S3_MAX_PARTS * _MULTIPART_CHUNK_SIZE:
                    raise ValueError(
                        f"File size ({size} bytes) exceeds maximum multipart upload size "
                        f"({_S3_MAX_PARTS * _MULTIPART_CHUNK_SIZE} bytes)"
                    )
                with open(path, "rb") as f:
                    etag = self._multipart_upload(effective_key, f, size, content_type, metadata)
            else:
                with open(path, "rb") as f:
                    resp = self._s3.put_object(
                        Bucket=self._bucket,
                        Key=effective_key,
                        Body=f,
                        **extra,
                    )
                    etag = resp.get("ETag", "")

            _log.info(
                "blob_uploaded_file",
                key=effective_key,
                size=size,
                bucket=self._bucket,
                source=str(path),
            )
            return BlobUploadResult(key=effective_key, size_bytes=size, etag=etag)
        except StorageError:
            raise
        except ValueError:
            raise
        except (OSError, botocore.exceptions.ClientError, botocore.exceptions.BotoCoreError) as exc:
            raise StorageError(
                error_code=ErrorCode.BLOB_UPLOAD_FAILED,
                message=f"Failed to upload file '{path}' to '{effective_key}': {exc}",
            ) from exc

    # ------------------------------------------------------------------
    # Download
    # ------------------------------------------------------------------

    def download(self, key: str) -> bytes:
        """Download a blob from S3.

        Args:
            key: Object key.

        Returns:
            Raw bytes of the object.

        Raises:
            StorageError: If download fails (BLOB_DOWNLOAD_FAILED).
        """
        from botocore.exceptions import ClientError

        try:
            resp = self._s3.get_object(Bucket=self._bucket, Key=key)
            return resp["Body"].read()
        except ClientError as exc:
            error_code = exc.response.get("Error", {}).get("Code", "")
            if error_code in ("NoSuchKey", "404"):
                raise StorageError(
                    error_code=ErrorCode.BLOB_NOT_FOUND,
                    message=f"Blob not found: '{key}'",
                ) from exc
            raise StorageError(
                error_code=ErrorCode.BLOB_DOWNLOAD_FAILED,
                message=f"Failed to download blob '{key}': {exc}",
            ) from exc
        except (botocore.exceptions.BotoCoreError, OSError) as exc:
            raise StorageError(
                error_code=ErrorCode.BLOB_DOWNLOAD_FAILED,
                message=f"Failed to download blob '{key}': {exc}",
            ) from exc

    def download_file(self, key: str, dest_path: str | Path) -> int:
        """Download a blob to a local file.

        Args:
            key: Object key.
            dest_path: Destination file path.

        Returns:
            Number of bytes written.

        Raises:
            StorageError: If download fails.
        """
        from botocore.exceptions import ClientError

        dest = Path(dest_path)
        dest.parent.mkdir(parents=True, exist_ok=True)

        try:
            self._s3.download_file(self._bucket, key, str(dest))
            size = dest.stat().st_size
            _log.info(
                "blob_downloaded_file",
                key=key,
                dest=str(dest),
                size=size,
                bucket=self._bucket,
            )
            return size
        except ClientError as exc:
            error_code = exc.response.get("Error", {}).get("Code", "")
            if error_code in ("NoSuchKey", "404"):
                raise StorageError(
                    error_code=ErrorCode.BLOB_NOT_FOUND,
                    message=f"Blob not found: '{key}'",
                ) from exc
            raise StorageError(
                error_code=ErrorCode.BLOB_DOWNLOAD_FAILED,
                message=f"Failed to download '{key}' to '{dest}': {exc}",
            ) from exc
        except (botocore.exceptions.BotoCoreError, OSError) as exc:
            raise StorageError(
                error_code=ErrorCode.BLOB_DOWNLOAD_FAILED,
                message=f"Failed to download '{key}' to '{dest}': {exc}",
            ) from exc

    # ------------------------------------------------------------------
    # Presigned URL
    # ------------------------------------------------------------------

    def presigned_url(
        self,
        key: str,
        *,
        expires_in: int = 3600,
        operation: str = "get_object",
    ) -> str:
        """Generate a presigned URL for a blob.

        Args:
            key: Object key.
            expires_in: URL expiration in seconds (default 1 hour).
            operation: S3 operation ("get_object" or "put_object").

        Returns:
            Presigned URL string.

        Raises:
            StorageError: If URL generation fails (BLOB_PRESIGN_FAILED).
        """

        _validate_blob_key(key)
        if operation not in ("get_object", "put_object"):
            raise ValueError(f"Unsupported presigned operation: {operation}")

        try:
            return self._s3.generate_presigned_url(
                ClientMethod=operation,
                Params={"Bucket": self._bucket, "Key": key},
                ExpiresIn=expires_in,
            )
        except (botocore.exceptions.ClientError, botocore.exceptions.BotoCoreError, OSError) as exc:
            raise StorageError(
                error_code=ErrorCode.BLOB_PRESIGN_FAILED,
                message=f"Failed to generate presigned URL for '{key}': {exc}",
            ) from exc

    # ------------------------------------------------------------------
    # Head / exists
    # ------------------------------------------------------------------

    def head(self, key: str) -> BlobInfo:
        """Retrieve metadata for a blob without downloading it.

        Args:
            key: Object key.

        Returns:
            BlobInfo with object metadata.

        Raises:
            StorageError: If head fails or object not found (BLOB_NOT_FOUND).
        """
        from botocore.exceptions import ClientError

        try:
            resp = self._s3.head_object(Bucket=self._bucket, Key=key)
            return BlobInfo(
                key=key,
                size_bytes=resp.get("ContentLength", 0),
                last_modified=resp.get("LastModified", "").isoformat()
                if hasattr(resp.get("LastModified", ""), "isoformat")
                else str(resp.get("LastModified", "")),
                content_type=resp.get("ContentType", ""),
                etag=resp.get("ETag", ""),
            )
        except ClientError as exc:
            error_code = exc.response.get("Error", {}).get("Code", "")
            if error_code in ("404", "NoSuchKey"):
                raise StorageError(
                    error_code=ErrorCode.BLOB_NOT_FOUND,
                    message=f"Blob not found: '{key}'",
                ) from exc
            raise StorageError(
                error_code=ErrorCode.STORAGE_READ_FAILED,
                message=f"Failed to head blob '{key}': {exc}",
            ) from exc
        except (botocore.exceptions.BotoCoreError, OSError) as exc:
            raise StorageError(
                error_code=ErrorCode.STORAGE_READ_FAILED,
                message=f"Failed to head blob '{key}': {exc}",
            ) from exc

    def exists(self, key: str) -> bool:
        """Check if a blob exists.

        Args:
            key: Object key.

        Returns:
            True if the blob exists.
        """
        from botocore.exceptions import ClientError

        try:
            self._s3.head_object(Bucket=self._bucket, Key=key)
            return True
        except ClientError as exc:
            error_code = exc.response.get("Error", {}).get("Code", "")
            if error_code in ("404", "NoSuchKey"):
                return False
            raise StorageError(
                error_code=ErrorCode.STORAGE_READ_FAILED,
                message=f"Failed to check existence of '{key}': {exc}",
            ) from exc
        except (botocore.exceptions.BotoCoreError, OSError):
            return False

    # ------------------------------------------------------------------
    # Delete
    # ------------------------------------------------------------------

    def delete(self, key: str) -> None:
        """Delete a blob.

        Args:
            key: Object key.

        Raises:
            StorageError: If delete fails (BLOB_DELETE_FAILED).
        """
        try:
            self._s3.delete_object(Bucket=self._bucket, Key=key)
            _log.info("blob_deleted", key=key, bucket=self._bucket)
        except (botocore.exceptions.ClientError, botocore.exceptions.BotoCoreError, OSError) as exc:
            raise StorageError(
                error_code=ErrorCode.BLOB_DELETE_FAILED,
                message=f"Failed to delete blob '{key}': {exc}",
            ) from exc

    def delete_prefix(self, prefix: str) -> int:
        """Delete all blobs under a prefix.

        Falls back to individual delete_object calls if batch delete_objects
        fails (e.g. MinIO versions requiring Content-MD5).

        Args:
            prefix: Key prefix to delete.

        Returns:
            Number of deleted objects.
        """
        from botocore.exceptions import ClientError

        count = 0
        paginator = self._s3.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=self._bucket, Prefix=prefix):
            objects = page.get("Contents", [])
            if not objects:
                break
            delete_keys = [{"Key": obj["Key"]} for obj in objects]
            try:
                self._s3.delete_objects(
                    Bucket=self._bucket,
                    Delete={"Objects": delete_keys, "Quiet": True},
                )
            except ClientError:
                # Fallback: delete one-by-one (e.g. MinIO Content-MD5 issue)
                for key_dict in delete_keys:
                    self._s3.delete_object(Bucket=self._bucket, Key=key_dict["Key"])
            count += len(delete_keys)

        if count > 0:
            _log.info("blob_prefix_deleted", prefix=prefix, count=count, bucket=self._bucket)
        return count

    # ------------------------------------------------------------------
    # Copy
    # ------------------------------------------------------------------

    def copy(self, source_key: str, dest_key: str) -> None:
        """Copy a blob within the same bucket.

        Args:
            source_key: Source object key.
            dest_key: Destination object key.

        Raises:
            StorageError: If copy operation fails.
        """
        _validate_blob_key(source_key)
        _validate_blob_key(dest_key)
        try:
            self._s3.copy_object(
                Bucket=self._bucket,
                CopySource={"Bucket": self._bucket, "Key": source_key},
                Key=dest_key,
            )
        except (botocore.exceptions.ClientError, botocore.exceptions.BotoCoreError, OSError) as exc:
            raise StorageError(
                error_code=ErrorCode.STORAGE_WRITE_FAILED,
                message=f"Failed to copy '{source_key}' to '{dest_key}': {exc}",
            ) from exc

    # ------------------------------------------------------------------
    # List
    # ------------------------------------------------------------------

    def list_blobs(
        self,
        prefix: str = "",
        *,
        max_keys: int = 1000,
        continuation_token: str | None = None,
    ) -> BlobListResult:
        """List blobs under a prefix.

        Args:
            prefix: Key prefix to filter.
            max_keys: Maximum keys to return.
            continuation_token: Pagination token from previous response.

        Returns:
            BlobListResult with keys and pagination info.
        """
        kwargs: dict[str, Any] = {
            "Bucket": self._bucket,
            "Prefix": prefix,
            "MaxKeys": max_keys,
        }
        if continuation_token:
            kwargs["ContinuationToken"] = continuation_token

        try:
            resp = self._s3.list_objects_v2(**kwargs)
        except (botocore.exceptions.ClientError, botocore.exceptions.BotoCoreError, OSError) as exc:
            raise StorageError(
                error_code=ErrorCode.STORAGE_READ_FAILED,
                message=f"Failed to list blobs under '{prefix}': {exc}",
            ) from exc

        keys = tuple(obj["Key"] for obj in resp.get("Contents", []))
        return BlobListResult(
            keys=keys,
            count=len(keys),
            truncated=resp.get("IsTruncated", False),
            next_token=resp.get("NextContinuationToken"),
        )

    # ------------------------------------------------------------------
    # Multipart upload (internal)
    # ------------------------------------------------------------------

    def _multipart_upload(
        self,
        key: str,
        body: BinaryIO,
        total_size: int,
        content_type: str,
        metadata: dict[str, str] | None = None,
    ) -> str:
        """Upload via S3 multipart for large files."""
        from botocore.exceptions import ClientError

        try:
            create_kwargs: dict[str, Any] = {
                "Bucket": self._bucket,
                "Key": key,
                "ContentType": content_type,
            }
            if metadata:
                create_kwargs["Metadata"] = metadata

            upload = self._s3.create_multipart_upload(**create_kwargs)
            upload_id = upload["UploadId"]

            parts: list[dict[str, Any]] = []
            part_number = 1

            while True:
                chunk = body.read(_MULTIPART_CHUNK_SIZE)
                if not chunk:
                    break

                part_resp = self._s3.upload_part(
                    Bucket=self._bucket,
                    Key=key,
                    PartNumber=part_number,
                    UploadId=upload_id,
                    Body=chunk,
                )
                parts.append(
                    {
                        "PartNumber": part_number,
                        "ETag": part_resp["ETag"],
                    }
                )
                part_number += 1

            if not parts:
                # Empty body — abort and upload a zero-byte object instead.
                self._s3.abort_multipart_upload(Bucket=self._bucket, Key=key, UploadId=upload_id)
                resp = self._s3.put_object(
                    Bucket=self._bucket, Key=key, Body=b"", ContentType=content_type
                )
                return resp.get("ETag", "")

            complete_resp = self._s3.complete_multipart_upload(
                Bucket=self._bucket,
                Key=key,
                UploadId=upload_id,
                MultipartUpload={"Parts": parts},
            )
            return complete_resp.get("ETag", "")
        except ClientError as exc:
            # Best-effort abort on failure.
            try:
                self._s3.abort_multipart_upload(Bucket=self._bucket, Key=key, UploadId=upload_id)
            except (botocore.exceptions.ClientError, botocore.exceptions.BotoCoreError, OSError):
                _log.warning("multipart_abort_failed", key=key, exc_info=True)
            raise StorageError(
                error_code=ErrorCode.BLOB_UPLOAD_FAILED,
                message=f"Multipart upload failed for '{key}': {exc}",
            ) from exc


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


# Blob key segments allow dots (file extensions) unlike pure SQL identifiers.
_BLOB_SEGMENT_RE = re.compile(r"^[a-zA-Z0-9_.][a-zA-Z0-9_.\-]*$")


def _validate_blob_key(key: str) -> None:
    """Validate blob key to prevent path traversal and injection.

    Each path segment must match _BLOB_SEGMENT_RE (alphanumeric, dots,
    hyphens, underscores) and must not be '.' or '..'. Leading/trailing
    empty segments (from slashes) are allowed. Empty keys are rejected.
    """
    if not key or not key.strip():
        raise ValueError("Blob key must not be empty")

    if "\x00" in key:
        raise ValueError("Blob key must not contain null bytes")

    segments = key.split("/")
    for seg in segments:
        if not seg:
            continue  # Allow leading/trailing slashes
        if seg in (".", ".."):
            raise ValueError(f"Invalid blob key segment '{seg}': path traversal not allowed")
        if not _BLOB_SEGMENT_RE.match(seg):
            raise ValueError(
                f"Invalid blob key segment '{seg}': "
                f"must contain only alphanumeric chars, dots, hyphens, underscores"
            )


def _guess_content_type(key: str) -> str:
    """Guess MIME type from object key suffix."""
    guessed, _ = mimetypes.guess_type(key)
    return guessed or _DEFAULT_CONTENT_TYPE


def _get_stream_size(stream: BinaryIO) -> int:
    """Try to get the size of a file-like object."""
    if hasattr(stream, "seek") and hasattr(stream, "tell"):
        try:
            pos = stream.tell()
            stream.seek(0, 2)
            size = stream.tell()
            stream.seek(pos)
            return size
        except (OSError, ValueError):
            pass
    return 0


class _BytesReader:
    """Wraps bytes in a seekable file-like object for boto3."""

    def __init__(self, data: bytes | bytearray) -> None:
        self._data = data
        self._pos = 0

    def read(self, n: int = -1) -> bytes:
        if n == -1:
            result = self._data[self._pos :]
            self._pos = len(self._data)
        else:
            result = self._data[self._pos : self._pos + n]
            self._pos += len(result)
        return result

    def seek(self, offset: int, whence: int = 0) -> int:
        if whence == 0:
            self._pos = offset
        elif whence == 1:
            self._pos += offset
        elif whence == 2:
            self._pos = len(self._data) + offset
        return self._pos

    def tell(self) -> int:
        return self._pos
