"""Data ingestion — Story 3.1.

Ingests local files (CSV, JSONL, Parquet) into Lance datasets.
Uses Daft for file reading and LanceStorageManager for writing.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, ClassVar

import pyarrow as pa

from arrow_lake.exceptions import ErrorCode, IngestError


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


class Ingestor:
    """Ingests files into Lance datasets.

    Supports CSV, JSON/JSONL, and Parquet formats.
    Uses Daft for efficient file reading.

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

    def ingest(
        self,
        dataset_name: str,
        file_paths: list[str],
    ) -> IngestionReport:
        """Ingest files into a Lance dataset.

        Args:
            dataset_name: Target dataset name.
            file_paths: List of file paths to ingest.

        Returns:
            IngestionReport with per-source and total stats.

        Raises:
            IngestError: If ingestion fails.
        """
        sources: list[IngestionSource] = []
        total_rows = 0
        total_files = 0
        first_table: pa.Table | None = None

        for file_path in file_paths:
            path = Path(file_path)
            file_type = self._detect_file_type(path)
            table = self._read_file(path, file_type)

            if first_table is None:
                first_table = table
                self._manager.create_dataset(dataset_name, table)
            else:
                self._manager.append_dataset(dataset_name, table)

            src = IngestionSource(
                path=str(path),
                row_count=table.num_rows,
                file_count=1,
            )
            sources.append(src)
            total_rows += table.num_rows
            total_files += 1

        return IngestionReport(
            sources=tuple(sources),
            total_rows=total_rows,
            total_files=total_files,
        )

    @classmethod
    def _detect_file_type(cls, path: Path) -> str:
        """Detect file type from extension.

        Args:
            path: File path.

        Returns:
            File type string ('csv', 'json', 'parquet').

        Raises:
            IngestError: If extension is not supported.
        """
        suffix = path.suffix.lower()
        file_type = cls._SUPPORTED_EXTENSIONS.get(suffix)
        if file_type is None:
            raise IngestError(
                error_code=ErrorCode.INGEST_UNSUPPORTED_FORMAT,
                message=f"Unsupported file format '{suffix}' for '{path}'",
            )
        return file_type

    @staticmethod
    def _read_file(path: Path, file_type: str) -> pa.Table:
        """Read a file into an Arrow Table using Daft.

        Args:
            path: File path.
            file_type: File type string.

        Returns:
            Arrow Table with file contents.

        Raises:
            IngestError: If file cannot be read.
        """
        import daft

        try:
            if file_type == "csv":
                df = daft.read_csv(str(path))
            elif file_type == "json":
                df = daft.read_json(str(path))
            elif file_type == "parquet":
                df = daft.read_parquet(str(path))
            else:
                raise IngestError(
                    error_code=ErrorCode.INGEST_UNSUPPORTED_FORMAT,
                    message=f"Unsupported file type: {file_type}",
                )
            return df.to_arrow()
        except IngestError:
            raise
        except Exception as exc:
            raise IngestError(
                error_code=ErrorCode.INGEST_FILE_NOT_FOUND,
                message=f"Failed to read '{path}': {exc}",
            ) from exc
