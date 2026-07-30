"""Ray Serve embedding encoder — Story 4.2.

Provides a Ray Serve-based embedding backend with automatic fallback
to local HuggingFace inference when Ray Serve is unavailable.
"""

from __future__ import annotations

import logging
from typing import Any

import numpy as np
import pyarrow as pa

from arrow_lake.embed.encoder import EmbeddingResult, LocalEmbeddingEncoder
from arrow_lake.exceptions import EmbeddingError, ErrorCode

# Module-level reference for test patching.
# Actual import is deferred in _get_handle() to avoid hard dependency.
try:
    from ray import serve as _ray_serve

    ray_serve = _ray_serve
except ImportError:
    ray_serve = None  # type: ignore[assignment, misc]

logger = logging.getLogger(__name__)


class RayServeEmbeddingEncoder:
    """Encodes text via a Ray Serve deployment with local fallback.

    Features:
    - Calls a Ray Serve deployment for distributed embedding
    - Automatic fallback to LocalEmbeddingEncoder if Ray Serve is unavailable
    - Warning logged on fallback with EMBEDDING_RAY_SERVE_FALLBACK

    Args:
        deployment_name: Name of the Ray Serve deployment.
        batch_size: Number of texts per Ray Serve call.
        fallback_model: HuggingFace model name for local fallback.
    """

    def __init__(
        self,
        deployment_name: str = "embedding",
        batch_size: int = 128,
        fallback_model: str = "Qwen/Qwen3-Embedding-0.6B",
    ) -> None:
        self.deployment_name = deployment_name
        self.batch_size = batch_size
        self.fallback_model = fallback_model
        self._handle: Any = None
        self._fallback_encoder: LocalEmbeddingEncoder | None = None

    def _get_handle(self) -> Any:
        """Get or create the Ray Serve deployment handle.

        Note: The handle is cached for the lifetime of this encoder.
        In long-running processes, the cached handle may become stale if
        the deployment is updated or scaled down. A future version should
        add TTL-based revalidation.
        """
        if self._handle is not None:
            return self._handle

        if ray_serve is None:
            raise EmbeddingError(
                error_code=ErrorCode.EMBEDDING_RAY_SERVE_UNAVAILABLE,
                message="Ray Serve is not installed. Install with: pip install 'ray[serve]'",
            )

        try:
            self._handle = ray_serve.get_deployment_handle(self.deployment_name)
            logger.info(
                "ray_serve_encoder_connected deployment=%s",
                self.deployment_name,
            )
            return self._handle
        except (ConnectionError, ImportError, TimeoutError, RuntimeError) as exc:
            raise EmbeddingError(
                error_code=ErrorCode.EMBEDDING_RAY_SERVE_UNAVAILABLE,
                message=f"Ray Serve deployment '{self.deployment_name}' not available: {exc}",
            ) from exc

    def encode_column(
        self,
        table: pa.Table,
        column: str = "text_content",
        *,
        fallback_enabled: bool = True,
    ) -> EmbeddingResult:
        """Encode a text column via Ray Serve.

        Args:
            table: Arrow table containing the text column.
            column: Name of the text column to encode.
            fallback_enabled: If True, fall back to local encoder on error.

        Returns:
            EmbeddingResult with stats and metadata.

        Raises:
            ValueError: If column does not exist.
            EmbeddingError: If encoding fails and fallback is disabled.
        """
        if column not in table.column_names:
            raise ValueError(f"Column '{column}' not found in table")

        total_rows = table.num_rows
        col = table.column(column)
        null_mask = col.is_null().to_pylist()

        texts: list[str] = []
        for val, is_null in zip(col.to_pylist(), null_mask, strict=True):
            if not is_null and val is not None:
                texts.append(str(val))

        null_count = sum(null_mask)
        non_null_count = len(texts)

        if non_null_count == 0:
            return EmbeddingResult(
                total_rows=total_rows,
                embedded_rows=0,
                null_rows=null_count,
                embedding_dim=0,
                vector_column=f"{column}_embedding",
            )

        try:
            handle = self._get_handle()
            all_embeddings: list[np.ndarray] = []

            for batch_start in range(0, len(texts), self.batch_size):
                batch = texts[batch_start : batch_start + self.batch_size]
                result_ref = handle.remote(batch)
                batch_emb = result_ref
                all_embeddings.append(np.asarray(batch_emb, dtype=np.float32))

            embeddings = np.concatenate(all_embeddings, axis=0)

            return EmbeddingResult(
                total_rows=total_rows,
                embedded_rows=non_null_count,
                null_rows=null_count,
                embedding_dim=embeddings.shape[1] if embeddings.ndim == 2 else 0,
                vector_column=f"{column}_embedding",
            )
        except (ImportError, ConnectionError, OSError, EmbeddingError) as exc:
            if isinstance(exc, (ImportError, ConnectionError, OSError)) and not fallback_enabled:
                raise EmbeddingError(
                    error_code=ErrorCode.EMBEDDING_MODEL_ERROR,
                    message=f"Ray Serve encoding failed: {exc}",
                ) from exc
            logger.warning(
                "EMBEDDING_RAY_SERVE_FALLBACK: %s — falling back to local encoder",
                exc,
            )
            return self._fallback_encode(table, column, rows_to_encode=non_null_count)

    def _fallback_encode(
        self,
        table: pa.Table,
        column: str,
        *,
        rows_to_encode: int,
    ) -> EmbeddingResult:
        """Fallback to local HuggingFace encoder (cached across calls)."""
        try:
            if self._fallback_encoder is None:
                self._fallback_encoder = LocalEmbeddingEncoder(
                    model_name=self.fallback_model,
                    batch_size=self.batch_size,
                )
            return self._fallback_encoder.encode_column(table, column=column)
        except (ConnectionError, ImportError, TimeoutError, RuntimeError) as exc:
            raise EmbeddingError(
                error_code=ErrorCode.EMBEDDING_MODEL_ERROR,
                message=f"Local fallback encoding also failed: {exc}",
            ) from exc
