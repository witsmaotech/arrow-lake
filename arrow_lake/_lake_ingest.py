"""Ingest mixin — data ingestion, dataset CRUD, quality, and dedup."""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any

import pyarrow as pa

if TYPE_CHECKING:
    from arrow_lake.ingest.ingestor import IngestionReport
    from arrow_lake.quality.dedup import DedupResult
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

    def ingest_documents(
        self,
        dataset_name: str,
        pdf_paths: list[str],
        *,
        doc_config: Any = None,
    ) -> IngestionReport:
        """Ingest PDF documents: parse → chunk → write to Lance dataset.

        Delegates to Ingestor.ingest_documents().

        Args:
            dataset_name: Target dataset name.
            pdf_paths: List of PDF file paths.
            doc_config: Optional DocumentConfig for parsing/chunking options.

        Returns:
            IngestionReport with per-document and total stats.
        """
        from arrow_lake.ingest.ingestor import Ingestor
        from arrow_lake.ingest.ocr import TurboOcrClient
        from arrow_lake.storage.blob_store import BlobStoreManager

        sc = self._config.storage
        blob_store = self._get_component(
            "blob_store",
            lambda: BlobStoreManager(config=sc),
        )

        ocr_endpoint = "http://localhost:8002"
        if doc_config is not None:
            ocr_endpoint = getattr(doc_config, "ocr_endpoint", ocr_endpoint)

        ocr_client = self._get_component(
            "ocr_client",
            lambda: TurboOcrClient(endpoint=ocr_endpoint),
        )

        return Ingestor(self._get_storage()).ingest_documents(
            dataset_name, pdf_paths,
            doc_config=doc_config,
            blob_store=blob_store,
            ocr_client=ocr_client,
        )

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
            from arrow_lake.exceptions import ErrorCode, ValidationError
            raise ValidationError(
                ErrorCode.VALIDATION_TYPE_ERROR,
                f"data must be a pyarrow.Table, got {type(data).__name__}",
            )
        from arrow_lake.api.telemetry import get_tracer
        from arrow_lake.core.metrics import (
            catalog_tables_total,
            get_metrics_enabled,
            ingestion_bytes_total,
            ingestion_duration_seconds,
            ingestion_errors_total,
            ingestion_rows_total,
        )
        from arrow_lake.exceptions import StorageError

        tracer = get_tracer()
        source = "programmatic"
        t0 = time.monotonic()
        rows = data.num_rows
        nbytes = data.nbytes
        try:
            with tracer.start_as_current_span("create_dataset", attributes={"dataset": name, "rows": rows}):
                self._get_storage().create_dataset(name, data)
        except (StorageError, OSError, ValueError) as exc:
            if get_metrics_enabled():
                ingestion_errors_total.labels(source=source, error_type=type(exc).__name__).inc()
            raise
        else:
            if get_metrics_enabled():
                ingestion_rows_total.labels(source=source).inc(rows)
                ingestion_bytes_total.labels(source=source).inc(nbytes)
                ingestion_duration_seconds.labels(source=source).set(time.monotonic() - t0)
                catalog_tables_total.inc()

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
            from arrow_lake.exceptions import ErrorCode, ValidationError
            raise ValidationError(
                ErrorCode.VALIDATION_TYPE_ERROR,
                f"data must be a pyarrow.Table, got {type(data).__name__}",
            )
        from arrow_lake.core.metrics import (
            get_metrics_enabled,
            ingestion_bytes_total,
            ingestion_duration_seconds,
            ingestion_errors_total,
            ingestion_rows_total,
        )
        from arrow_lake.exceptions import StorageError

        source = "programmatic"
        t0 = time.monotonic()
        rows = data.num_rows
        nbytes = data.nbytes
        try:
            self._get_storage().append_dataset(name, data)
        except (StorageError, OSError, ValueError) as exc:
            if get_metrics_enabled():
                ingestion_errors_total.labels(source=source, error_type=type(exc).__name__).inc()
            raise
        else:
            if get_metrics_enabled():
                ingestion_rows_total.labels(source=source).inc(rows)
                ingestion_bytes_total.labels(source=source).inc(nbytes)
                ingestion_duration_seconds.labels(source=source).set(time.monotonic() - t0)

    def upsert(
        self,
        dataset_name: str,
        data: pa.Table,
        *,
        on: str = "id",
    ) -> None:
        """Upsert rows into a dataset using merge on a key column.

        New rows are inserted; existing rows matching ``on`` are updated.

        Args:
            dataset_name: Target dataset name.
            data: PyArrow Table with rows to upsert.
            on: Column name to use as the merge key (default "id").

        Raises:
            StorageError: If dataset not found or schema mismatch.
            TypeError: If data is not a pyarrow.Table.
        """
        if not isinstance(data, pa.Table):
            from arrow_lake.exceptions import ErrorCode, ValidationError
            raise ValidationError(
                ErrorCode.VALIDATION_TYPE_ERROR,
                f"data must be a pyarrow.Table, got {type(data).__name__}",
            )
        from arrow_lake.core.metrics import (
            get_metrics_enabled,
            ingestion_bytes_total,
            ingestion_duration_seconds,
            ingestion_errors_total,
            ingestion_rows_total,
        )
        from arrow_lake.exceptions import StorageError

        source = "upsert"
        t0 = time.monotonic()
        rows = data.num_rows
        nbytes = data.nbytes
        try:
            self._get_storage().upsert_dataset(dataset_name, data, on=on)
        except (StorageError, OSError, ValueError) as exc:
            if get_metrics_enabled():
                ingestion_errors_total.labels(source=source, error_type=type(exc).__name__).inc()
            raise
        else:
            if get_metrics_enabled():
                ingestion_rows_total.labels(source=source).inc(rows)
                ingestion_bytes_total.labels(source=source).inc(nbytes)
                ingestion_duration_seconds.labels(source=source).set(time.monotonic() - t0)

    def delete_rows(
        self,
        dataset_name: str,
        where: str,
    ) -> int:
        """Delete rows matching a filter expression from a dataset.

        Args:
            dataset_name: Target dataset name.
            where: SQL WHERE expression (validated for injection safety).

        Returns:
            Number of rows deleted.

        Raises:
            StorageError: If dataset not found or expression is unsafe.
        """
        from arrow_lake.core.metrics import _QueryTimer

        with _QueryTimer("delete_rows"):
            return self._get_storage().delete_rows(dataset_name, where)

    def update_rows(
        self,
        dataset_name: str,
        where: str,
        values: dict[str, str],
    ) -> None:
        """Update rows matching a filter expression with new values.

        Args:
            dataset_name: Target dataset name.
            where: SQL WHERE expression (validated for injection safety).
            values: Dict mapping column names to SQL value expressions.

        Raises:
            StorageError: If dataset not found or expression is unsafe.
        """
        from arrow_lake.core.metrics import _QueryTimer

        with _QueryTimer("update_rows"):
            self._get_storage().update_rows(dataset_name, where, values)

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

        from arrow_lake.core.metrics import (
            _QueryTimer,
            get_metrics_enabled,
            processing_quality_rejects_total,
        )

        with _QueryTimer("quality_filter"):
            table = self._get_storage().read_dataset(dataset_name)
            report = registry.apply_all(table, active_filters, mode=filter_mode)

        if get_metrics_enabled():
            for fr in report.filter_results:
                processing_quality_rejects_total.labels(filter_name=fr.filter_name).inc(fr.rejected_count)

        return report

    def deduplicate(
        self,
        dataset_name: str,
        *,
        strategy: str | None = None,
        action: str | None = None,
        perceptual_threshold: int | None = None,
    ) -> DedupResult:
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

    def embed_and_add(
        self,
        dataset_name: str,
        *,
        text_column: str = "text_content",
        embedding_column: str = "text_embedding",
        batch_size: int | None = None,
    ) -> int:
        """Encode a text column into embeddings and add them to the dataset.

        Uses the configured embedding backend (local HuggingFace model or
        OpenAI-compatible API such as Ollama). Adds the embedding column
        in-place via ``add_columns_table`` (no full dataset rewrite).

        Args:
            dataset_name: Target dataset name.
            text_column: Column containing text to encode.
            embedding_column: Name for the new embedding column.
            batch_size: Override batch size (None = use config default).

        Returns:
            Number of rows embedded.

        Raises:
            StorageError: If dataset not found.
            EmbeddingError: If encoding fails.
        """
        from arrow_lake.config._enums import EmbeddingBackend
        from arrow_lake.embed.encoder import ApiEmbeddingEncoder, LocalEmbeddingEncoder

        emb_cfg = self._config.embedding

        # Read text column
        table = self._get_storage().read_dataset(
            dataset_name, columns=[text_column],
        )
        texts = table.column(text_column).to_pylist()
        n = len(texts)

        effective_batch = batch_size or emb_cfg.batch_size

        # Encode using configured backend
        if emb_cfg.backend == EmbeddingBackend.OPENAI and emb_cfg.api_base:
            encoder = ApiEmbeddingEncoder(
                api_base=emb_cfg.api_base,
                api_key=emb_cfg.api_key,
                model_name=emb_cfg.model,
                batch_size=effective_batch,
            )
            all_embeddings: list[list[float]] = []
            for i in range(0, n, effective_batch):
                batch = encoder.encode(texts[i : i + effective_batch])
                all_embeddings.extend(batch.embeddings.tolist())
            dim = len(all_embeddings[0])
        else:
            encoder = LocalEmbeddingEncoder(
                model_name=emb_cfg.model,
                batch_size=effective_batch,
            )
            result = encoder.encode_column(
                pa.table({text_column: texts}), column=text_column,
            )
            all_embeddings = result.embeddings.tolist()
            dim = result.embedding_dim

        import numpy as np

        vec_array = pa.FixedSizeListArray.from_arrays(
            np.array(all_embeddings, dtype=np.float32).ravel(), dim,
        )
        vec_table = pa.table({embedding_column: vec_array})
        self._get_storage().add_columns_table(dataset_name, vec_table)
        return n
