"""File connectors — Story 3.1.

Provides LocalConnector (filesystem) and S3Connector (S3/MinIO)
for discovering files to ingest. Both implement the FileConnector protocol.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


@dataclass(frozen=True)
class ConnectorResult:
    """Result of a file discovery operation."""

    paths: tuple[str, ...] = ()
    file_count: int = 0


class FileConnector(Protocol):
    """Protocol for file discovery connectors."""

    def list_files(self, extensions: list[str] | None = None) -> ConnectorResult: ...


class LocalConnector:
    """Discovers files on the local filesystem.

    Args:
        base_path: Root directory to search.
    """

    def __init__(self, base_path: str | Path) -> None:
        self._base = Path(base_path)

    def list_files(self, extensions: list[str] | None = None) -> ConnectorResult:
        """List files under base_path, optionally filtered by extension.

        Args:
            extensions: File extensions to include (e.g. [".csv"]).
                        None means all files.

        Returns:
            ConnectorResult with matching file paths.

        Raises:
            FileNotFoundError: If base_path does not exist.
        """
        if not self._base.is_dir():
            raise FileNotFoundError(f"Directory not found: {self._base}")

        ext_set = set(extensions) if extensions else None
        paths: list[str] = []

        for item in sorted(self._base.rglob("*")):
            if not item.is_file():
                continue
            if ext_set and item.suffix.lower() not in ext_set:
                continue
            paths.append(str(item))

        return ConnectorResult(paths=tuple(paths), file_count=len(paths))


class S3Connector:
    """Discovers files on S3/MinIO.

    Args:
        bucket: S3 bucket name.
        prefix: Key prefix to filter objects.
        endpoint_url: S3 endpoint URL (required).
        aws_access_key_id: Optional access key.
        aws_secret_access_key: Optional secret key.
    """

    def __init__(
        self,
        bucket: str,
        prefix: str = "",
        endpoint_url: str | None = None,
        aws_access_key_id: str | None = None,
        aws_secret_access_key: str | None = None,
    ) -> None:
        if not endpoint_url:
            raise ValueError("S3Connector requires endpoint_url")
        self.bucket = bucket
        self.prefix = prefix
        self.endpoint_url = endpoint_url
        self.aws_access_key_id = aws_access_key_id
        self.aws_secret_access_key = aws_secret_access_key

    def __repr__(self) -> str:
        return (
            f"S3Connector(bucket={self.bucket!r}, prefix={self.prefix!r}, "
            f"endpoint_url={self.endpoint_url!r})"
        )

    def list_files(self, extensions: list[str] | None = None) -> ConnectorResult:
        """List objects in S3 bucket under prefix.

        Args:
            extensions: File extensions to include.

        Returns:
            ConnectorResult with S3 object keys.

        Raises:
            ConnectionError: If S3 is unreachable.
        """
        import boto3
        from botocore.exceptions import ClientError

        try:
            s3 = boto3.client(
                "s3",
                endpoint_url=self.endpoint_url,
                aws_access_key_id=self.aws_access_key_id,
                aws_secret_access_key=self.aws_secret_access_key,
            )
            paginator = s3.get_paginator("list_objects_v2")
            pages = paginator.paginate(Bucket=self.bucket, Prefix=self.prefix)

            ext_set = set(extensions) if extensions else None
            paths: list[str] = []

            for page in pages:
                for obj in page.get("Contents", []):
                    key = obj["Key"]
                    if ext_set and not any(key.lower().endswith(e) for e in ext_set):
                        continue
                    paths.append(f"s3://{self.bucket}/{key}")

            return ConnectorResult(paths=tuple(paths), file_count=len(paths))
        except ClientError as exc:
            raise ConnectionError(
                f"Failed to list S3 objects in {self.bucket}/{self.prefix}: {exc}"
            ) from exc
