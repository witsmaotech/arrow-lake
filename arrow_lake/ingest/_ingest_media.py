"""Media ingestion methods for Ingestor.

Handles image and video ingestion, and mixed-modality unified ingestion.
"""

from __future__ import annotations

from typing import Any

import pyarrow as pa


class _MediaIngestMixin:
    """Mixin providing image, video, and mixed-modality ingestion methods.

    Requires the host class to have:
    - ``_manager`` (LanceStorageManager)
    - ``_first_table_seen`` (dict)
    - ``_write_table()`` method
    - ``_build_report()`` static method
    - ``ingest()``, ``ingest_http()``, ``ingest_images()``, ``ingest_videos()`` methods
    """

    def ingest_images(
        self,
        dataset_name: str,
        image_paths: list[str],
    ) -> Any:
        """Ingest image files with thumbnails and EXIF."""
        from arrow_lake.ingest.media import ImageProcessor

        processor = ImageProcessor()
        sources: list[Any] = []

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
            self._write_table(dataset_name, table, sources, str(img_path))

        return self._build_report(sources)

    def ingest_videos(
        self,
        dataset_name: str,
        video_paths: list[str],
    ) -> Any:
        """Ingest video files with keyframe extraction."""
        from arrow_lake.ingest.media import VideoProcessor

        processor = VideoProcessor()
        sources: list[Any] = []

        for vid_path in video_paths:
            result = processor.extract_keyframes(vid_path)
            row = {
                "video_data": result.keyframes[0].jpeg_bytes if result.keyframes else None,
                "keyframe_count": len(result.keyframes),
                "video_duration_ms": result.duration_ms,
            }
            table = pa.table({k: [v] for k, v in row.items()})
            self._write_table(dataset_name, table, sources, str(vid_path))

        return self._build_report(sources)

    def ingest_mixed(
        self,
        dataset_name: str,
        sources: dict[str, list[str]],
    ) -> Any:
        """Ingest mixed modality sources into a unified table."""
        from arrow_lake.ingest.ingestor import IngestionReport
        from arrow_lake.ingest.schema import UnifiedTableManager

        mgr = UnifiedTableManager(self._manager)
        mgr.create(dataset_name)

        report_sources: list[Any] = []
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
