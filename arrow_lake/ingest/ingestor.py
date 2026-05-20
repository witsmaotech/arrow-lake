"""Data ingestion — Stories 3.1-3.5.

Ingests local files (CSV, JSONL, Parquet), HTTP URLs, images, and videos
into Lance datasets. Supports unified multimodal table ingestion.
Uses Daft for file reading and LanceStorageManager for writing.

Implementation is split across private mixin modules:
- ``_ingest_files``: File, document, batch, join, union ingestion
- ``_ingest_media``: Image, video, mixed-modality ingestion
- ``_ingest_sources``: SQL, Kafka, Iceberg, Delta Lake, HTTP ingestion
"""

from __future__ import annotations

import contextlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, ClassVar

import pyarrow as pa

from arrow_lake.exceptions import ErrorCode, IngestError
from arrow_lake.ingest._ingest_files import _FileIngestMixin
from arrow_lake.ingest._ingest_media import _MediaIngestMixin
from arrow_lake.ingest._ingest_sources import _SourceIngestMixin


@dataclass(frozen=True)
class IngestionSource:
    """Stats for a single ingestion source."""

    path: str
    row_count: int
    file_count: int = 1


@dataclass(frozen=True)
class IngestionReport:
    """Result of an ingestion operation."""

    sources: tuple[IngestionSource, ...] = ()
    total_rows: int = 0
    total_files: int = 0


class Ingestor(_FileIngestMixin, _MediaIngestMixin, _SourceIngestMixin):
    """Ingests files into Lance datasets.

    Supports CSV, JSON/JSONL, Parquet, HTTP URLs, images, and videos.
    Uses Daft for efficient file reading.

    Thread safety: This class is NOT thread-safe for concurrent ingestion
    into the SAME dataset (create/append race). Use separate Ingestor
    instances or external synchronization for concurrent writes.

    Args:
        manager: LanceStorageManager for dataset writes.
    """

    _SUPPORTED_EXTENSIONS: ClassVar[dict[str, str]] = {
        ".csv": "csv",
        ".json": "json",
        ".jsonl": "json",
        ".parquet": "parquet",
    }

    def __init__(self, manager: Any) -> None:
        self._manager = manager
        self._first_table_seen: dict[str, bool] = {}

    def _write_table(
        self,
        dataset_name: str,
        table: pa.Table,
        sources: list[IngestionSource],
        source_path: str,
    ) -> None:
        """Write a table to the dataset (create or append) and track the source."""
        if not self._first_table_seen.get(dataset_name, False):
            self._manager.create_dataset(dataset_name, table)
            self._first_table_seen[dataset_name] = True
        else:
            self._manager.append_dataset(dataset_name, table)

        sources.append(IngestionSource(
            path=source_path,
            row_count=table.num_rows,
            file_count=1,
        ))

    @staticmethod
    def _build_report(sources: list[IngestionSource]) -> IngestionReport:
        """Build an IngestionReport from a list of sources."""
        return IngestionReport(
            sources=tuple(sources),
            total_rows=sum(s.row_count for s in sources),
            total_files=sum(s.file_count for s in sources),
        )

    @classmethod
    def _detect_file_type(cls, path: Path | str) -> str:
        """Detect file type from extension.

        Args:
            path: File path (local, S3 URI, or URL).

        Returns:
            File type string ('csv', 'json', 'parquet').

        Raises:
            IngestError: If extension is not supported.
        """
        p = str(path).lower()
        for ext, ft in cls._SUPPORTED_EXTENSIONS.items():
            if p.endswith(ext):
                return ft
        raise IngestError(
            error_code=ErrorCode.INGEST_UNSUPPORTED_FORMAT,
            message=f"Unsupported file format for '{path}'",
        )

    @staticmethod
    def _read_file_df(
        path: Path | str,
        file_type: str,
        *,
        columns: list[str] | None = None,
    ) -> Any:
        """Read a file into a Daft DataFrame (lazy, not yet materialized).

        Args:
            path: File path (local or s3:// URI).
            file_type: File type string.
            columns: Optional column subset to read (column pruning).

        Returns:
            daft.DataFrame for further lazy operations.

        Raises:
            IngestError: If file cannot be read.
        """
        import daft

        try:
            sp = str(path)
            read_kwargs: dict[str, Any] = {}
            if columns and file_type in ("csv", "parquet"):
                read_kwargs["columns"] = columns

            if file_type == "csv":
                return daft.read_csv(sp, **read_kwargs)
            elif file_type == "json":
                return daft.read_json(sp)
            elif file_type == "parquet":
                return daft.read_parquet(sp, **read_kwargs)
            else:
                raise IngestError(
                    error_code=ErrorCode.INGEST_UNSUPPORTED_FORMAT,
                    message=f"Unsupported file type: {file_type}",
                )
        except IngestError:
            raise
        except (ImportError, OSError, ValueError) as exc:
            raise IngestError(
                error_code=ErrorCode.INGEST_FILE_NOT_FOUND,
                message=f"Failed to read '{path}': {exc}",
            ) from exc

    @staticmethod
    def _read_file(
        path: Path | str,
        file_type: str,
        *,
        columns: list[str] | None = None,
    ) -> pa.Table:
        """Read a file into an Arrow Table using Daft.

        Thin wrapper around ``_read_file_df().to_arrow()``.
        """
        df = Ingestor._read_file_df(path, file_type, columns=columns)
        return df.to_arrow()

    @staticmethod
    def _read_bytes(content: bytes, file_type: str) -> pa.Table:
        """Read raw bytes into an Arrow Table based on detected type.

        Args:
            content: Raw file content bytes.
            file_type: File type string ('csv', 'json', 'parquet').

        Returns:
            Arrow Table with content.

        Raises:
            IngestError: If reading fails.
        """
        import os
        import tempfile

        import daft

        try:
            suffix = f".{file_type}"
            fd, tmp_path = tempfile.mkstemp(suffix=suffix)
            try:
                os.write(fd, content)
                os.close(fd)
                if file_type == "json":
                    df = daft.read_json(tmp_path)
                elif file_type == "csv":
                    df = daft.read_csv(tmp_path)
                elif file_type == "parquet":
                    df = daft.read_parquet(tmp_path)
                else:
                    raise IngestError(
                        error_code=ErrorCode.INGEST_UNSUPPORTED_FORMAT,
                        message=f"Unsupported file type: {file_type}",
                    )
                return df.to_arrow()
            finally:
                with contextlib.suppress(OSError):
                    os.unlink(tmp_path)
        except IngestError:
            raise
        except (ImportError, OSError, ValueError) as exc:
            raise IngestError(
                error_code=ErrorCode.INGEST_FILE_NOT_FOUND,
                message=f"Failed to read content: {exc}",
            ) from exc
