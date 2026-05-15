"""Ingest + embed unified pipeline — Daft repositioning Sprint 4.

Single-pass Daft DataFrame pipeline that reads files, applies transforms,
generates embeddings, and writes directly to Lance — eliminating the
Arrow conversion round-trip and Lance read-modify-write cycle.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from arrow_lake.exceptions import ErrorCode, IngestError
from arrow_lake.ingest.ingestor import IngestionReport

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class IngestEmbedResult:
    """Result of a combined ingest + embed pipeline."""

    ingestion: IngestionReport
    embedded_rows: int
    embedding_dim: int
    vector_column: str


class IngestEmbedPipeline:
    """Unified ingest + embed pipeline using Daft DataFrame operations.

    Reads files into a Daft DataFrame, applies optional transforms,
    generates text embeddings via ``daft.functions.embed_text()``, and
    writes the result directly to Lance.

    Args:
        storage: LanceStorageManager instance.
        model: Embedding model identifier.
        provider: Daft embed provider (default ``transformers``).
        num_partitions: Number of Daft partitions for parallel operations.
    """

    def __init__(
        self,
        storage: Any,
        *,
        model: str = "Qwen/Qwen3-Embedding-0.6B",
        provider: str = "transformers",
        num_partitions: int = 4,
    ) -> None:
        self._storage = storage
        self._model = model
        self._provider = provider
        self._num_partitions = num_partitions

    def ingest_and_embed(
        self,
        dataset_name: str,
        file_paths: list[str],
        *,
        text_column: str = "text_content",
        embedding_column: str = "text_embedding",
        transforms: list[Any] | None = None,
        write_mode: str = "create",
    ) -> IngestEmbedResult:
        """Run the unified ingest + embed pipeline.

        Args:
            dataset_name: Target dataset name.
            file_paths: Files to ingest.
            text_column: Column containing text to embed.
            embedding_column: Name for the generated embedding column.
            transforms: Optional Daft DataFrame transforms.
            write_mode: Lance write mode — "create", "append", or "overwrite".

        Returns:
            IngestEmbedResult with ingestion and embedding stats.

        Raises:
            IngestError: If no files provided or file type unsupported.
            EmbeddingError: If embed_text fails.
        """
        if not file_paths:
            raise IngestError(
                error_code=ErrorCode.INGEST_UNSUPPORTED_FORMAT,
                message="No file paths provided for ingest-and-embed",
            )

        from arrow_lake.ingest.ingestor import Ingestor

        # Group by type and process each group
        grouped = Ingestor._group_by_type(file_paths)
        if not grouped:
            raise IngestError(
                error_code=ErrorCode.INGEST_UNSUPPORTED_FORMAT,
                message="No supported file types found",
            )

        total_rows = 0
        total_files = 0
        embedding_dim = 0

        for file_type, paths in grouped.items():
            df = self._read_group(paths, file_type)

            if transforms:
                for t in transforms:
                    df = t(df)

            effective_partitions = min(self._num_partitions, max(1, len(paths)))
            df = df.into_partitions(effective_partitions)

            df = df.with_column(
                embedding_column,
                self._build_embed_expr(text_column),
            )

            # Materialize to count rows before writing
            row_count = df.count().to_arrow()[0].as_py()

            self._storage.write_lance_from_dataframe(
                dataset_name, df, mode=write_mode,
            )

            total_rows += row_count
            total_files += len(paths)
            if embedding_dim == 0:
                embedding_dim = self._infer_dim_from_df(df, embedding_column)

        from arrow_lake.ingest.ingestor import IngestionReport, IngestionSource

        report = IngestionReport(
            sources=(IngestionSource(
                path=f"ingest_embed:{','.join(grouped.keys())}",
                row_count=total_rows,
                file_count=total_files,
            ),),
            total_rows=total_rows,
            total_files=total_files,
        )

        return IngestEmbedResult(
            ingestion=report,
            embedded_rows=total_rows,
            embedding_dim=embedding_dim,
            vector_column=embedding_column,
        )

    def _read_group(self, paths: list[str], file_type: str) -> Any:
        """Read a group of same-type files into a Daft DataFrame."""
        from arrow_lake.ingest.ingestor import Ingestor

        return Ingestor._read_files_df(paths, file_type)

    def _build_embed_expr(self, text_column: str) -> Any:
        """Build the Daft embed_text expression."""
        from arrow_lake.embed.daft_encoder import DaftBatchEncoder

        encoder = DaftBatchEncoder(
            model=self._model, provider=self._provider,
            num_partitions=self._num_partitions,
        )
        return encoder._embed_expr(text_column)

    @staticmethod
    def _infer_dim_from_df(df: Any, emb_col: str) -> int:
        """Sample a single row to infer embedding dimension."""
        try:
            sample = df.select(emb_col).limit(1).to_arrow()
            val = sample.column(emb_col)[0].as_py()
            return len(val) if val is not None else 0
        except Exception:
            return 0
