"""Ingest mixin — data ingestion, dataset CRUD, quality, and dedup."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pyarrow as pa

if TYPE_CHECKING:
    from arrow_lake.ingest.ingestor import IngestionReport
    from arrow_lake.quality.models import QualityReport


class _LakeIngestMixin:
    """Provides data ingestion, dataset management, quality filtering, and dedup."""

    def ingest(
        self,
        dataset_name: str,
        file_paths: list[str],
    ) -> IngestionReport:
        """Ingest local files into a Lance dataset (Stories 3.1-3.5).

        Delegates to Ingestor.

        Args:
            dataset_name: Target dataset name.
            file_paths: List of file paths (CSV, JSON, JSONL, Parquet).

        Returns:
            IngestionReport with per-source and total stats.
        """
        from arrow_lake.ingest.ingestor import Ingestor

        return Ingestor(self._get_storage()).ingest(dataset_name, file_paths)

    def ingest_http(
        self,
        dataset_name: str,
        urls: list[str],
    ) -> IngestionReport:
        """Ingest files from HTTP(S) URLs (Story 3.2).

        Delegates to Ingestor.

        Args:
            dataset_name: Target dataset name.
            urls: List of HTTP(S) URLs.

        Returns:
            IngestionReport with per-source and total stats.
        """
        from arrow_lake.ingest.ingestor import Ingestor

        return Ingestor(self._get_storage()).ingest_http(dataset_name, urls)

    def ingest_images(
        self,
        dataset_name: str,
        image_paths: list[str],
    ) -> IngestionReport:
        """Ingest image files with thumbnails and EXIF (Story 3.3).

        Delegates to Ingestor.

        Args:
            dataset_name: Target dataset name.
            image_paths: List of image file paths.

        Returns:
            IngestionReport with per-source and total stats.
        """
        from arrow_lake.ingest.ingestor import Ingestor

        return Ingestor(self._get_storage()).ingest_images(dataset_name, image_paths)

    def ingest_videos(
        self,
        dataset_name: str,
        video_paths: list[str],
    ) -> IngestionReport:
        """Ingest video files with keyframe extraction (Story 3.4).

        Delegates to Ingestor.

        Args:
            dataset_name: Target dataset name.
            video_paths: List of video file paths.

        Returns:
            IngestionReport with per-source and total stats.
        """
        from arrow_lake.ingest.ingestor import Ingestor

        return Ingestor(self._get_storage()).ingest_videos(dataset_name, video_paths)

    def ingest_mixed(
        self,
        dataset_name: str,
        sources: dict[str, list[str]],
    ) -> IngestionReport:
        """Ingest mixed modality sources into a unified table (Story 3.5).

        Delegates to Ingestor.

        Args:
            dataset_name: Target dataset name.
            sources: Dict mapping modality to list of paths/URLs.
                     Supported keys: "files", "urls", "images", "videos".

        Returns:
            Combined IngestionReport.
        """
        from arrow_lake.ingest.ingestor import Ingestor

        return Ingestor(self._get_storage()).ingest_mixed(dataset_name, sources)

    def create_dataset(self, name: str, data: pa.Table) -> None:
        """Create a new dataset from an Arrow Table.

        This is the primary way to write programmatic data into Arrow Lake.

        Args:
            name: Dataset name (must match ^[a-zA-Z_][a-zA-Z0-9_-]*$).
            data: PyArrow Table to write as a Lance dataset.

        Raises:
            StorageError: If dataset already exists or name is invalid.
            TypeError: If data is not a pyarrow.Table.
        """
        if not isinstance(data, pa.Table):
            raise TypeError(f"data must be a pyarrow.Table, got {type(data).__name__}")
        self._get_storage().create_dataset(name, data)

    def append_dataset(self, name: str, data: pa.Table) -> None:
        """Append rows to an existing dataset from an Arrow Table.

        Args:
            name: Dataset name to append to.
            data: PyArrow Table with matching schema.

        Raises:
            StorageError: If dataset does not exist or schema mismatch.
            TypeError: If data is not a pyarrow.Table.
        """
        if not isinstance(data, pa.Table):
            raise TypeError(f"data must be a pyarrow.Table, got {type(data).__name__}")
        self._get_storage().append_dataset(name, data)

    def quality_filter(
        self,
        dataset_name: str,
        active_filters: str = "",
        *,
        mode: str = "all",
    ) -> QualityReport:
        """Run quality filters on a dataset and return a report (Epic 4).

        Delegates to QualityFilterRegistry with built-in filters.

        Args:
            dataset_name: Name of the Lance dataset.
            active_filters: Comma-separated filter names (empty = use config).
            mode: Filter combination mode ("all" for AND, "any" for OR).

        Returns:
            QualityReport with per-filter results and totals.
        """
        from arrow_lake.quality.base import QualityFilterRegistry
        from arrow_lake.quality.builtin import ImageResolutionFilter, TextLengthFilter

        if not active_filters:
            active_filters = self._config.quality.active_filters
        filter_mode = mode or self._config.quality.filter_mode

        registry = QualityFilterRegistry()
        if self._config.quality.enabled:
            registry.register(
                TextLengthFilter(
                    min_chars=self._config.quality.text_min_chars,
                    max_chars=self._config.quality.text_max_chars,
                )
            )
            registry.register(
                ImageResolutionFilter(
                    min_width=self._config.quality.image_min_width,
                    min_height=self._config.quality.image_min_height,
                )
            )

        table = self._get_storage().read_dataset(dataset_name)
        return registry.apply_all(table, active_filters, mode=filter_mode)

    def deduplicate(
        self,
        dataset_name: str,
        *,
        strategy: str | None = None,
        action: str | None = None,
        perceptual_threshold: int | None = None,
    ) -> Any:
        """Run content deduplication on a dataset (Story 4.7).

        Delegates to ContentDeduplicator.

        Args:
            dataset_name: Name of the Lance dataset.
            strategy: "exact", "perceptual", or "both" (None = use config).
            action: "flag" or "remove" (None = use config).
            perceptual_threshold: pHash Hamming distance (None = use config).

        Returns:
            DedupResult with dedup statistics and processed table.
        """
        from arrow_lake.quality.dedup import ContentDeduplicator

        config = self._config.quality
        dedup = ContentDeduplicator(
            strategy=strategy or config.dedup_strategy,
            action=action or config.dedup_action,
            perceptual_threshold=perceptual_threshold or config.dedup_perceptual_threshold,
        )
        table = self._get_storage().read_dataset(dataset_name)
        return dedup.deduplicate(table)
