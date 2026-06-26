"""Daft-powered batch embedding encoder — Daft repositioning Sprint 2.

Uses ``daft.functions.embed_text()`` for parallel partitioned embedding,
as an opt-in alternative to LocalEmbeddingEncoder / ApiEmbeddingEncoder.
"""

from __future__ import annotations

import logging
from typing import Any

import numpy as np
import pyarrow as pa

from arrow_lake.embed.encoder import EmbeddingResult
from arrow_lake.exceptions import EmbeddingError, ErrorCode

logger = logging.getLogger(__name__)


class DaftBatchEncoder:
    """Encode text using Daft's parallel embed_text function.

    Args:
        model: HuggingFace model identifier for embedding.
        provider: Daft embed provider (default ``transformers``).
        num_partitions: Number of Daft partitions for parallel encoding.
    """

    def __init__(
        self,
        model: str = "Qwen/Qwen3-Embedding-0.6B",
        provider: str = "transformers",
        num_partitions: int = 4,
        expected_dim: int = 0,
    ) -> None:
        self._model = model
        self._provider = provider
        self._num_partitions = num_partitions
        self._expected_dim = expected_dim

    def encode_column(
        self,
        table: pa.Table,
        column: str = "text_content",
    ) -> EmbeddingResult:
        """Encode a text column using Daft parallel partitions.

        Args:
            table: Arrow table containing the text column.
            column: Name of the text column to encode.

        Returns:
            EmbeddingResult with embedding statistics.

        Raises:
            EmbeddingError: If Daft embed_text fails.
        """
        import daft

        if column not in table.column_names:
            raise ValueError(f"Column '{column}' not found in table")

        total_rows = table.num_rows
        if total_rows == 0:
            return EmbeddingResult(
                total_rows=0, embedded_rows=0, null_rows=0,
                embedding_dim=0, vector_column=f"{column}_embedding",
            )

        try:
            df = daft.from_arrow(table)

            effective_partitions = min(self._num_partitions, max(1, total_rows // 100))
            if effective_partitions != self._num_partitions:
                logger.info(
                    "daft_partitions_adjusted",
                    requested=self._num_partitions,
                    effective=effective_partitions,
                    rows=total_rows,
                )
            df = df.into_partitions(effective_partitions)

            emb_col = f"{column}_embedding"
            df = df.with_column(
                emb_col,
                self._embed_expr(column),
            )

            result_df = df.select(emb_col)
            result_table = result_df.to_arrow()

            emb_array = result_table.column(emb_col)
            dim = self._infer_dim(emb_array)
            self._check_dim(dim)

            embedded_count = sum(1 for v in emb_array.to_pylist() if v is not None)
            null_count = total_rows - embedded_count

            return EmbeddingResult(
                total_rows=total_rows,
                embedded_rows=embedded_count,
                null_rows=null_count,
                embedding_dim=dim,
                vector_column=emb_col,
            )

        except Exception as exc:
            raise EmbeddingError(
                error_code=ErrorCode.EMBEDDING_MODEL_ERROR,
                message=f"Daft embed_text failed: {exc}",
                context={"model": self._model, "provider": self._provider, "column": column},
            ) from exc

    def encode_to_vectors(
        self,
        table: pa.Table,
        column: str = "text_content",
    ) -> tuple[np.ndarray, int]:
        """Encode text column and return raw embedding matrix.

        Returns:
            Tuple of (embeddings array, dimension).
        """
        import daft

        if table.num_rows == 0:
            return np.zeros((0, 0), dtype=np.float32), 0

        df = daft.from_arrow(table)
        effective_partitions = min(self._num_partitions, max(1, table.num_rows // 100))
        df = df.into_partitions(effective_partitions)

        emb_col = f"{column}_embedding"
        df = df.with_column(emb_col, self._embed_expr(column))

        result_table = df.select(emb_col).to_arrow()
        emb_array = result_table.column(emb_col)
        dim = self._infer_dim(emb_array)
        self._check_dim(dim)

        vectors = np.array(
            [v if v is not None else [0.0] * dim for v in emb_array.to_pylist()],
            dtype=np.float32,
        )
        # L2 归一化非零行（null 行零向量保持），对称 LocalEmbeddingEncoder 的 normalize_embeddings=True
        if dim > 0:
            norms = np.linalg.norm(vectors, axis=1, keepdims=True)
            nonzero = norms.ravel() > 0
            vectors[nonzero] = vectors[nonzero] / norms[nonzero]
        return vectors, dim

    def encode(self, texts: list[str]) -> np.ndarray:
        """Encode a list of texts and return the embedding matrix.

        Satisfies ``EmbeddingEncoderProtocol`` so ``DaftBatchEncoder`` is a
        drop-in for callers that consume the ``encode(list[str]) -> Any``
        contract (e.g. alongside ``ApiEmbeddingEncoder.encode``).

        Args:
            texts: List of text strings to encode.

        Returns:
            ``np.ndarray`` of shape (len(texts), dim); null/empty rows are
            zero-filled by :meth:`encode_to_vectors`.
        """
        table = pa.table({"text_content": texts})
        vectors, _ = self.encode_to_vectors(table, column="text_content")
        return vectors

    def _embed_expr(self, column: str) -> Any:
        """Build the Daft embed_text expression."""
        import daft

        try:
            from daft.functions import embed_text
            return embed_text(daft.col(column), provider=self._provider, model=self._model)
        except ImportError as exc:
            raise EmbeddingError(
                error_code=ErrorCode.EMBEDDING_MODEL_ERROR,
                message="daft.functions.embed_text not available — upgrade daft>=0.4",
            ) from exc

    def encode_image_column(
        self,
        table: pa.Table,
        image_column: str = "image_uri",
    ) -> EmbeddingResult:
        """Encode an image column using Daft multimodal embedding.

        Args:
            table: Arrow table containing the image URI column.
            image_column: Name of the image URI/path column.

        Returns:
            EmbeddingResult with embedding statistics.

        Raises:
            EmbeddingError: If Daft image embed fails or model is not multimodal.
        """
        import daft

        if image_column not in table.column_names:
            raise ValueError(f"Column '{image_column}' not found in table")

        total_rows = table.num_rows
        if total_rows == 0:
            return EmbeddingResult(
                total_rows=0, embedded_rows=0, null_rows=0,
                embedding_dim=0, vector_column=f"{image_column}_embedding",
            )

        try:
            df = daft.from_arrow(table)
            effective_partitions = min(self._num_partitions, max(1, total_rows // 50))
            df = df.into_partitions(effective_partitions)

            emb_col = f"{image_column}_embedding"
            df = df.with_column(
                emb_col,
                self._embed_expr(image_column),
            )

            result_df = df.select(emb_col)
            result_table = result_df.to_arrow()

            emb_array = result_table.column(emb_col)
            dim = self._infer_dim(emb_array)
            self._check_dim(dim)

            embedded_count = sum(1 for v in emb_array.to_pylist() if v is not None)
            null_count = total_rows - embedded_count

            return EmbeddingResult(
                total_rows=total_rows,
                embedded_rows=embedded_count,
                null_rows=null_count,
                embedding_dim=dim,
                vector_column=emb_col,
            )

        except Exception as exc:
            raise EmbeddingError(
                error_code=ErrorCode.EMBEDDING_MODEL_ERROR,
                message=f"Daft image embed failed: {exc}",
                context={"model": self._model, "provider": self._provider, "column": image_column},
            ) from exc

    @staticmethod
    def _infer_dim(emb_array: pa.ChunkedArray) -> int:
        """Infer embedding dimension from a FixedSizeListArray or list column."""
        for val in emb_array.to_pylist():
            if val is not None:
                return len(val)
        if hasattr(emb_array, "type") and pa.types.is_fixed_size_list(emb_array.type):
            return emb_array.type.list_size
        return 0

    def _check_dim(self, dim: int) -> None:
        """Raise EmbeddingError if ``expected_dim`` is set and ``dim`` differs."""
        if self._expected_dim > 0 and dim != self._expected_dim:
            raise EmbeddingError(
                error_code=ErrorCode.EMBEDDING_MODEL_ERROR,
                message=(
                    f"Embedding dimension mismatch: model '{self._model}' produces "
                    f"{dim}D vectors, expected {self._expected_dim}D"
                ),
            )
