"""Data export to standard formats — Story 5.9.

Provides export of Lance datasets and Arrow tables to Parquet and CSV formats.
Binary columns (image_data, video_data, etc.) are excluded from CSV exports
with a warning log.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.csv as csv
import pyarrow.parquet as pq
import structlog

from arrow_lake.exceptions import ErrorCode, StorageError

logger = structlog.get_logger(__name__)

__all__ = ["ExportBridge", "ExportResult"]

_BINARY_COLUMNS = {"image_data", "video_data", "image_thumbnail", "image_preview"}


@dataclass(frozen=True)
class ExportResult:
    """Result of a dataset export operation.

    Attributes:
        dataset_name: Name of the exported dataset.
        output_path: Path to the output file.
        format: Export format ("parquet" or "csv").
        row_count: Number of rows exported.
        column_count: Number of columns exported.
        file_size_bytes: Size of the output file in bytes.
        version: Dataset version that was exported (None for latest).
    """

    dataset_name: str
    output_path: str
    format: str
    row_count: int
    column_count: int
    file_size_bytes: int
    version: int | None


class ExportBridge:
    """Bridges Lance datasets to Parquet/CSV file export.

    Args:
        storage: LanceStorageManager instance.
        config: Export configuration (None = use defaults).
    """

    def __init__(
        self,
        storage: Any,
        config: Any | None = None,
    ) -> None:
        self._storage = storage
        self._config = config

    def export(
        self,
        dataset_name: str,
        output_path: str,
        *,
        format: str | None = None,  # noqa: A002
        columns: list[str] | None = None,
        version: int | None = None,
        compression: str | None = None,
        overwrite: bool = False,
    ) -> ExportResult:
        """Export a Lance dataset to Parquet or CSV.

        Args:
            dataset_name: Name of the Lance dataset.
            output_path: Output file path (.parquet or .csv).
            format: Export format (None = auto-detect from path suffix).
            columns: Optional column subset to export.
            version: Dataset version to export (None = latest).
            compression: Compression codec for Parquet (None = use config default).
            overwrite: Allow overwriting existing file.

        Returns:
            ExportResult with metadata.

        Raises:
            StorageError: If dataset not found, format unsupported, or write fails.
        """
        fmt = self._detect_format(output_path, format)

        table = self._storage.read_dataset(dataset_name, version=version, columns=columns)
        return self.export_table(
            table,
            output_path,
            format=fmt,
            columns=columns,
            compression=compression,
            overwrite=overwrite,
        )

    def export_table(
        self,
        table: pa.Table,
        output_path: str,
        *,
        format: str | None = None,  # noqa: A002
        columns: list[str] | None = None,
        compression: str | None = None,
        overwrite: bool = False,
    ) -> ExportResult:
        """Export an arbitrary Arrow table to Parquet or CSV.

        Args:
            table: Arrow table to export.
            output_path: Output file path.
            format: Export format (None = auto-detect from path suffix).
            columns: Optional column subset to export.
            compression: Compression codec for Parquet.
            overwrite: Allow overwriting existing file.

        Returns:
            ExportResult with metadata.

        Raises:
            StorageError: If format unsupported, path invalid, or write fails.
        """
        # Validate output path — reject traversal attempts BEFORE format detection
        output = Path(output_path)
        if ".." in output.parts:
            raise StorageError(
                error_code=ErrorCode.EXPORT_PATH_INVALID,
                message=f"Path traversal not allowed in output path: {output_path}",
            )
        path = output.resolve()

        fmt = self._detect_format(output_path, format)

        # Select columns if specified
        if columns:
            available = set(table.column_names)
            missing = set(columns) - available
            if missing:
                raise StorageError(
                    error_code=ErrorCode.EXPORT_PATH_INVALID,
                    message=f"Columns not found: {missing}",
                )
            table = table.select(columns)
        if path.exists() and not overwrite:
            raise StorageError(
                error_code=ErrorCode.EXPORT_PATH_INVALID,
                message=f"Output file exists and overwrite=False: {output_path}",
            )
        path.parent.mkdir(parents=True, exist_ok=True)

        # CSV: exclude binary columns
        export_table = table
        if fmt == "csv":
            binary_cols = [c for c in table.column_names if c in _BINARY_COLUMNS]
            if binary_cols:
                logger.warning(
                    "export_csv_binary_excluded",
                    columns=binary_cols,
                    reason="Binary columns cannot be exported to CSV",
                )
            non_binary = [c for c in table.column_names if c not in _BINARY_COLUMNS]
            if non_binary:
                export_table = table.select(non_binary)
            else:
                raise StorageError(
                    error_code=ErrorCode.EXPORT_WRITE_FAILED,
                    message="No non-binary columns to export to CSV",
                )

        # Write
        try:
            if fmt == "parquet":
                comp = compression or (
                    self._config.parquet_compression if self._config else "snappy"
                )
                pq.write_table(export_table, output_path, compression=comp)
            elif fmt == "csv":
                delimiter = self._config.csv_delimiter if self._config else ","
                csv.write_csv(
                    export_table, output_path, write_options=csv.WriteOptions(delimiter=delimiter)
                )
            else:
                raise StorageError(
                    error_code=ErrorCode.EXPORT_FORMAT_NOT_SUPPORTED,
                    message=f"Unsupported export format: {fmt!r}. Use 'parquet' or 'csv'.",
                )
        except StorageError:
            raise
        except Exception as exc:
            raise StorageError(
                error_code=ErrorCode.EXPORT_WRITE_FAILED,
                message=f"Failed to export to {output_path}: {exc}",
            ) from exc

        file_size = os.path.getsize(output_path)
        return ExportResult(
            dataset_name="",
            output_path=str(path),
            format=fmt,
            row_count=export_table.num_rows,
            column_count=len(export_table.column_names),
            file_size_bytes=file_size,
            version=None,
        )

    @staticmethod
    def _detect_format(path: str, explicit: str | None) -> str:
        """Detect export format from path suffix or explicit override."""
        if explicit:
            if explicit not in ("parquet", "csv"):
                raise StorageError(
                    error_code=ErrorCode.EXPORT_FORMAT_NOT_SUPPORTED,
                    message=f"Unsupported export format: {explicit!r}. Use 'parquet' or 'csv'.",
                )
            return explicit
        suffix = Path(path).suffix.lower()
        if suffix == ".parquet":
            return "parquet"
        if suffix == ".csv":
            return "csv"
        raise StorageError(
            error_code=ErrorCode.EXPORT_FORMAT_NOT_SUPPORTED,
            message=f"Cannot detect format from path '{path}'. "
            f"Use format='parquet' or format='csv', or a .parquet/.csv suffix.",
        )
