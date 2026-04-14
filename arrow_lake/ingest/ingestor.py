"""Data ingestion — Stories 3.1-3.5.

Ingests local files (CSV, JSONL, Parquet), HTTP URLs, images, and videos
into Lance datasets. Supports unified multimodal table ingestion.
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

    def ingest_http(
        self,
        dataset_name: str,
        urls: list[str],
    ) -> IngestionReport:
        """Ingest files from HTTP(S) URLs (Story 3.2).

        Args:
            dataset_name: Target dataset name.
            urls: List of HTTP(S) URLs to fetch and ingest.

        Returns:
            IngestionReport with per-source and total stats.

        Raises:
            IngestError: If fetching or ingestion fails.
        """
        from arrow_lake.ingest.connectors_http import HttpConnector

        connector = HttpConnector()
        sources: list[IngestionSource] = []
        total_rows = 0
        total_files = 0
        first_table: pa.Table | None = None

        for url in urls:
            result = connector.fetch(url)
            suffix = self._detect_file_type(Path(url))
            table = self._read_bytes(result.content, suffix)

            if first_table is None:
                first_table = table
                self._manager.create_dataset(dataset_name, table)
            else:
                self._manager.append_dataset(dataset_name, table)

            src = IngestionSource(
                path=result.url,
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

    def ingest_images(
        self,
        dataset_name: str,
        image_paths: list[str],
    ) -> IngestionReport:
        """Ingest image files with thumbnails and EXIF (Story 3.3).

        Args:
            dataset_name: Target dataset name.
            image_paths: List of image file paths.

        Returns:
            IngestionReport with per-source and total stats.

        Raises:
            IngestError: If processing or ingestion fails.
        """
        from arrow_lake.ingest.media import ImageProcessor

        processor = ImageProcessor()
        sources: list[IngestionSource] = []
        total_rows = 0
        total_files = 0
        first_table: pa.Table | None = None

        for img_path in image_paths:
            result = processor.process(img_path)
            row = {
                "image_data": result.original_bytes,
                "image_thumbnail": result.thumbnail_bytes,
                "image_preview": result.preview_bytes,
                "image_width": result.metadata.width,
                "image_height": result.metadata.height,
                "exif_make": result.metadata.exif_make,
                "exif_model": result.metadata.exif_model,
            }
            table = pa.table({k: [v] for k, v in row.items()})

            if first_table is None:
                first_table = table
                self._manager.create_dataset(dataset_name, table)
            else:
                self._manager.append_dataset(dataset_name, table)

            src = IngestionSource(
                path=str(img_path),
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

    def ingest_videos(
        self,
        dataset_name: str,
        video_paths: list[str],
    ) -> IngestionReport:
        """Ingest video files with keyframe extraction (Story 3.4).

        Args:
            dataset_name: Target dataset name.
            video_paths: List of video file paths.

        Returns:
            IngestionReport with per-source and total stats.

        Raises:
            IngestError: If processing or ingestion fails.
        """
        from arrow_lake.ingest.media import VideoProcessor

        processor = VideoProcessor()
        sources: list[IngestionSource] = []
        total_rows = 0
        total_files = 0
        first_table: pa.Table | None = None

        for vid_path in video_paths:
            result = processor.extract_keyframes(vid_path)
            row = {
                "video_data": result.keyframes[0].jpeg_bytes if result.keyframes else None,
                "keyframe_count": len(result.keyframes),
                "video_duration_ms": result.duration_ms,
            }
            table = pa.table({k: [v] for k, v in row.items()})

            if first_table is None:
                first_table = table
                self._manager.create_dataset(dataset_name, table)
            else:
                self._manager.append_dataset(dataset_name, table)

            src = IngestionSource(
                path=str(vid_path),
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

    def ingest_mixed(
        self,
        dataset_name: str,
        sources: dict[str, list[str]],
    ) -> IngestionReport:
        """Ingest mixed modality sources into a unified table (Story 3.5).

        Args:
            dataset_name: Target dataset name.
            sources: Dict mapping modality to list of paths/URLs.
                     Supported keys: "files", "urls", "images", "videos".

        Returns:
            Combined IngestionReport.

        Raises:
            IngestError: If any ingestion fails.
        """
        from arrow_lake.ingest.schema import UnifiedTableManager

        mgr = UnifiedTableManager(self._manager)
        mgr.create(dataset_name)

        report_sources: list[IngestionSource] = []
        total_rows = 0
        total_files = 0

        if "files" in sources:
            r = self.ingest(dataset_name, sources["files"])
            report_sources.extend(r.sources)
            total_rows += r.total_rows
            total_files += r.total_files

        if "urls" in sources:
            r = self.ingest_http(dataset_name, sources["urls"])
            report_sources.extend(r.sources)
            total_rows += r.total_rows
            total_files += r.total_files

        if "images" in sources:
            r = self.ingest_images(dataset_name, sources["images"])
            report_sources.extend(r.sources)
            total_rows += r.total_rows
            total_files += r.total_files

        if "videos" in sources:
            r = self.ingest_videos(dataset_name, sources["videos"])
            report_sources.extend(r.sources)
            total_rows += r.total_rows
            total_files += r.total_files

        return IngestionReport(
            sources=tuple(report_sources),
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
        import io

        import daft

        try:
            buf = io.BytesIO(content)
            if file_type == "json":
                df = daft.read_json(buf)  # type: ignore[arg-type]
            elif file_type == "csv":
                df = daft.read_csv(buf)  # type: ignore[arg-type]
            elif file_type == "parquet":
                buf.seek(0)
                df = daft.read_parquet(buf)  # type: ignore[arg-type]
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
                message=f"Failed to read content: {exc}",
            ) from exc
