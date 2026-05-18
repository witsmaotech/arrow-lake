"""Storage layer configuration."""

from __future__ import annotations

import logging
from urllib.parse import urlparse

from pydantic import BaseModel, model_validator

from arrow_lake.config._enums import StorageBackend

logger = logging.getLogger(__name__)


def _is_local_endpoint(endpoint: str) -> bool:
    """Check if an S3 endpoint points to localhost / 127.x."""
    try:
        host = urlparse(endpoint).hostname or ""
        return host in ("localhost", "127.0.0.1") or host.startswith("127.")
    except Exception:
        return False


class StorageConfig(BaseModel):
    """Storage layer configuration.

    Attributes:
        base_uri: Base URI for Lance dataset storage (local path or s3:// URI).
        backend: Storage backend type (minio, s3, gcs, local).
        s3_endpoint: S3-compatible endpoint URL.
        s3_access_key: S3 access key (empty = use default credentials).
        s3_secret_key: S3 secret key (empty = use default credentials).
        s3_bucket: Default bucket name.
        s3_region: S3 region.
    """

    base_uri: str = "./data"
    backend: StorageBackend = StorageBackend.MINIO
    s3_endpoint: str = "http://localhost:9000"
    s3_access_key: str = ""
    s3_secret_key: str = ""
    s3_bucket: str = "arrow-lake"
    s3_region: str = "us-east-1"

    # Lance write optimization parameters
    lance_max_rows_per_file: int = 100_000
    lance_max_rows_per_group: int = 10_000
    lance_compression: str = "zstd"

    # Lance read cache (bytes, 0 = disabled)
    lance_cache_size: int = 0

    @model_validator(mode="after")
    def _validate_remote_backend(self) -> StorageConfig:
        if self.backend == StorageBackend.LOCAL:
            return self
        if not self.s3_bucket:
            raise ValueError(
                f"s3_bucket is required when backend={self.backend.value}"
            )
        if not self.s3_endpoint and self.backend != StorageBackend.S3:
            raise ValueError(
                f"s3_endpoint is required when backend={self.backend.value} "
                f"(only backend=s3 supports empty endpoint for default AWS)"
            )
        if (not self.s3_access_key or not self.s3_secret_key) and self.backend == StorageBackend.S3:
            logger.warning(
                "S3 credentials are empty for backend=s3 — "
                "operations will use default credential chain"
            )
        return self

    # -- S3 helper methods for lance/boto3 and DuckDB integration --

    @property
    def s3_uri(self) -> str:
        """Return the full dataset URI for Lance operations.

        For LOCAL backend, returns base_uri as-is.
        For S3-compatible backends, returns ``s3://{bucket}/{base_uri}``.
        Leading ``./`` is stripped from base_uri for S3 paths.
        """
        if self.backend == StorageBackend.LOCAL:
            return self.base_uri
        path = self.base_uri
        if path.startswith("./"):
            path = path[2:]
        return f"s3://{self.s3_bucket}/{path}"

    def to_storage_options(self) -> dict[str, str] | None:
        """Return storage_options dict for lance/boto3, or None for local.

        The returned dict contains keys expected by both the Lance filesystem
        abstraction and boto3-based S3 clients.
        """
        if self.backend == StorageBackend.LOCAL:
            return None
        opts: dict[str, str] = {
            "region": self.s3_region,
            "endpoint_url": self.s3_endpoint,
            "aws_access_key_id": self.s3_access_key,
            "aws_secret_access_key": self.s3_secret_key,
            "allow_anonymous": "false",
        }
        # LanceDB rust S3 client requires explicit allow_http for HTTP endpoints
        if self.s3_endpoint.startswith("http://"):
            opts["allow_http"] = "true"
        return opts

    def to_duckdb_s3_config(self) -> list[str]:
        """Return list of DuckDB ``SET`` statements for S3 access.

        Returns an empty list for LOCAL backend.
        Single quotes in values are escaped to prevent SQL injection.
        """
        if self.backend == StorageBackend.LOCAL:
            return []
        return [
            f"SET s3_region='{self.s3_region.replace(chr(39), chr(39) + chr(39))}'",
            f"SET s3_endpoint='{self.s3_endpoint.replace(chr(39), chr(39) + chr(39))}'",
            f"SET s3_access_key_id='{self.s3_access_key.replace(chr(39), chr(39) + chr(39))}'",
            f"SET s3_secret_access_key='{self.s3_secret_key.replace(chr(39), chr(39) + chr(39))}'",
        ]

    @classmethod
    def from_env(cls) -> StorageConfig:
        """Create a StorageConfig from environment variables.

        Reads:
        - ``S3_ENDPOINT`` / ``S3_ENDPOINT_URL``
        - ``AWS_ACCESS_KEY_ID``
        - ``AWS_SECRET_ACCESS_KEY``
        - ``S3_BUCKET``
        - ``AWS_REGION`` / ``AWS_DEFAULT_REGION``
        """
        import os

        endpoint = os.environ.get("S3_ENDPOINT") or os.environ.get(
            "S3_ENDPOINT_URL", "http://localhost:9000"
        )
        access_key = os.environ.get("AWS_ACCESS_KEY_ID", "")
        secret_key = os.environ.get("AWS_SECRET_ACCESS_KEY", "")
        bucket = os.environ.get("S3_BUCKET", "arrow-lake")
        region = os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION", "us-east-1")
        return cls(
            backend=StorageBackend.MINIO,
            s3_endpoint=endpoint,
            s3_access_key=access_key,
            s3_secret_key=secret_key,
            s3_bucket=bucket,
            s3_region=region,
        )
