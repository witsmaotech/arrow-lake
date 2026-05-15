"""Lakehouse format connectors — Daft Phase 2, Sprint 7.

Connectors for Apache Iceberg and Delta Lake tables, enabling cross-format
data migration into Lance.
"""

from __future__ import annotations

from typing import Any

from arrow_lake.exceptions import ErrorCode, IngestError


class IcebergConnector:
    """Read from an Apache Iceberg table via Daft.

    Args:
        table_uri: Iceberg table URI (e.g. ``s3://warehouse/db/table``).
        io_config: Optional Daft IOConfig for S3 access.
    """

    def __init__(self, table_uri: str, *, io_config: Any = None) -> None:
        self._table_uri = table_uri
        self._io_config = io_config

    def read(self) -> Any:
        """Read the Iceberg table into a Daft DataFrame."""
        import daft

        try:
            return daft.read_iceberg(self._table_uri, io_config=self._io_config)
        except Exception as exc:
            raise IngestError(
                error_code=ErrorCode.INGEST_FILE_NOT_FOUND,
                message=f"Iceberg read failed: {exc}",
                context={"table_uri": self._table_uri},
            ) from exc


class DeltaConnector:
    """Read from a Delta Lake table via Daft.

    Args:
        table_uri: Delta Lake table URI.
        version: Optional specific version to read.
        io_config: Optional Daft IOConfig for S3 access.
    """

    def __init__(
        self,
        table_uri: str,
        *,
        version: int | None = None,
        io_config: Any = None,
    ) -> None:
        self._table_uri = table_uri
        self._version = version
        self._io_config = io_config

    def read(self) -> Any:
        """Read the Delta Lake table into a Daft DataFrame."""
        import daft

        try:
            kwargs: dict[str, Any] = {}
            if self._version is not None:
                kwargs["version"] = self._version
            return daft.read_deltalake(self._table_uri, io_config=self._io_config, **kwargs)
        except Exception as exc:
            raise IngestError(
                error_code=ErrorCode.INGEST_FILE_NOT_FOUND,
                message=f"Delta Lake read failed: {exc}",
                context={"table_uri": self._table_uri, "version": self._version},
            ) from exc
