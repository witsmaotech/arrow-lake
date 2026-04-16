"""Text embedding encoders — Stories 4.1, 4.3.

Provides:
- LocalEmbeddingEncoder: HuggingFace SentenceTransformer (local)
- ApiEmbeddingEncoder: OpenAI-compatible API with fallback
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import httpx
import numpy as np
import pyarrow as pa
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from arrow_lake.exceptions import EmbeddingError, ErrorCode

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class EmbeddingBatch:
    """A batch of embedding vectors with null mask."""

    embeddings: np.ndarray[Any, Any]
    null_mask: tuple[bool, ...]


@dataclass(frozen=True)
class EmbeddingResult:
    """Result of embedding a column of text."""

    total_rows: int
    embedded_rows: int
    null_rows: int
    embedding_dim: int
    vector_column: str


class LocalEmbeddingEncoder:
    """Encodes text using a local HuggingFace SentenceTransformer model.

    Features:
    - Lazy model loading (loaded on first encode call)
    - GPU auto-detection
    - NULL/empty text handling (returns null embedding)
    - Batch processing
    - ModelScope support for China mainland users

    Args:
        model_name: HuggingFace model identifier.
        model_source: Model download source — "huggingface" or "modelscope".
        batch_size: Number of texts per batch.
    """

    def __init__(
        self,
        model_name: str = "Qwen/Qwen3-Embedding-0.6B",
        model_source: str = "huggingface",
        batch_size: int = 128,
    ) -> None:
        self.model_name = model_name
        self.model_source = model_source
        self.batch_size = batch_size
        self._model: Any = None
        self._embedding_dim: int = 0

    def _load_model(self) -> Any:
        """Lazy-load the SentenceTransformer model."""
        if self._model is not None:
            return self._model

        from sentence_transformers import SentenceTransformer

        device = None
        try:
            import torch

            if torch.cuda.is_available():
                device = "cuda"
        except Exception:
            pass

        if self.model_source == "modelscope":
            from modelscope import snapshot_download

            model_path = snapshot_download(self.model_name)
            self._model = SentenceTransformer(model_path, device=device)
        else:
            self._model = SentenceTransformer(self.model_name, device=device)
        self._embedding_dim = self._model.get_embedding_dimension()
        return self._model

    def encode_column(
        self,
        table: pa.Table,
        column: str = "text_content",
    ) -> EmbeddingResult:
        """Encode a text column into embedding vectors.

        Args:
            table: Arrow table containing the text column.
            column: Name of the text column to encode.

        Returns:
            EmbeddingResult with stats and metadata.

        Raises:
            ValueError: If column does not exist in table.
            EmbeddingError: If model loading or encoding fails.
        """
        if column not in table.column_names:
            raise ValueError(f"Column '{column}' not found in table")

        total_rows = table.num_rows
        col = table.column(column)
        null_mask = col.is_null().to_pylist()

        # Separate null and non-null texts
        texts: list[str] = []
        valid_indices: list[int] = []
        for i, (val, is_null) in enumerate(zip(col.to_pylist(), null_mask, strict=True)):
            if is_null:
                continue
            texts.append(str(val) if val is not None else "")
            valid_indices.append(i)

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
            model = self._load_model()
            embeddings = model.encode(
                texts,
                batch_size=self.batch_size,
                show_progress_bar=False,
                normalize_embeddings=True,
            )
            embeddings = np.asarray(embeddings, dtype=np.float32)
        except Exception as exc:
            raise EmbeddingError(
                error_code=ErrorCode.EMBEDDING_MODEL_ERROR,
                message=f"Failed to encode texts with '{self.model_name}': {exc}",
            ) from exc

        return EmbeddingResult(
            total_rows=total_rows,
            embedded_rows=non_null_count,
            null_rows=null_count,
            embedding_dim=embeddings.shape[1],
            vector_column=f"{column}_embedding",
        )


class ApiEmbeddingEncoder:
    """Encodes text via an OpenAI-compatible embedding API.

    Features:
    - Retry with exponential backoff
    - Error mapping: 429→API_ERROR, timeout→TIMEOUT
    - Fallback to LocalEmbeddingEncoder on unreachable API

    Args:
        api_base: Base URL for the embedding API.
        api_key: API key for authentication.
        model_name: Model name to use (e.g. text-embedding-ada-002).
        batch_size: Number of texts per request.
        timeout_seconds: Request timeout in seconds.
        max_retries: Maximum retry attempts.
    """

    def __init__(
        self,
        api_base: str,
        api_key: str = "",
        model_name: str = "text-embedding-ada-002",
        batch_size: int = 128,
        timeout_seconds: float = 30.0,
        max_retries: int = 3,
        fallback_model: str = "Qwen/Qwen3-Embedding-0.6B",
    ) -> None:
        if not api_base:
            raise ValueError("api_base is required for ApiEmbeddingEncoder")
        self.api_base = api_base.rstrip("/")
        self.api_key = api_key
        self.model_name = model_name
        self.batch_size = batch_size
        self.timeout_seconds = timeout_seconds
        self.max_retries = max_retries
        self.fallback_model = fallback_model

    def encode(self, texts: list[str]) -> EmbeddingBatch:
        """Encode texts via the API.

        Args:
            texts: List of text strings to encode.

        Returns:
            EmbeddingBatch with embedding vectors and null mask.

        Raises:
            EmbeddingError: On API error, timeout, or rate limit.
        """
        headers: dict[str, str] = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        @retry(
            retry=retry_if_exception_type((httpx.TimeoutException, httpx.ConnectError)),
            stop=stop_after_attempt(self.max_retries),
            wait=wait_exponential(multiplier=1, min=1, max=10),
            reraise=True,
        )
        def _do_encode(texts_to_encode: list[str]) -> EmbeddingBatch:
            with httpx.Client(timeout=self.timeout_seconds) as client:
                response = client.post(
                    f"{self.api_base}/embeddings",
                    json={
                        "model": self.model_name,
                        "input": texts_to_encode,
                    },
                    headers=headers,
                )

                if response.status_code == 429:
                    raise EmbeddingError(
                        error_code=ErrorCode.EMBEDDING_API_ERROR,
                        message=f"Rate limited by embedding API: {response.text[:200]}",
                    )

                if response.status_code != 200:
                    raise EmbeddingError(
                        error_code=ErrorCode.EMBEDDING_API_ERROR,
                        message=(
                            f"Embedding API returned {response.status_code}: {response.text[:200]}"
                        ),
                    )

                data = response.json()
                embeddings = []
                null_mask: list[bool] = []

                # Sort by index to maintain order
                sorted_data = sorted(data["data"], key=lambda x: x["index"])
                for item in sorted_data:
                    embeddings.append(item["embedding"])
                    null_mask.append(False)

                return EmbeddingBatch(
                    embeddings=np.array(embeddings, dtype=np.float32),
                    null_mask=tuple(null_mask),
                )

        try:
            return _do_encode(texts)
        except EmbeddingError:
            raise
        except httpx.TimeoutException as exc:
            raise EmbeddingError(
                error_code=ErrorCode.EMBEDDING_TIMEOUT,
                message=f"Embedding API timed out: {exc}",
            ) from exc
        except httpx.ConnectError as exc:
            logger.warning(
                "Embedding API unreachable at %s, falling back to local encoder: %s",
                self.api_base,
                exc,
            )
            return self._fallback_encode(texts)

    def _fallback_encode(self, texts: list[str]) -> EmbeddingBatch:
        """Fallback to local encoder when API is unreachable.

        Args:
            texts: List of text strings to encode.

        Returns:
            EmbeddingBatch from local encoder.
        """
        try:
            local_encoder = LocalEmbeddingEncoder(
                model_name=self.fallback_model,
                batch_size=self.batch_size,
            )
            # Build a temporary Arrow table to use encode_column
            table = pa.table({"text_content": texts})
            result = local_encoder.encode_column(table, column="text_content")
            if result.embedded_rows == 0:
                return EmbeddingBatch(
                    embeddings=np.zeros((len(texts), 1024), dtype=np.float32),
                    null_mask=tuple(True for _ in texts),
                )
            # Reconstruct embeddings from the encoder
            model = local_encoder._load_model()
            embeddings = np.asarray(
                model.encode(texts, normalize_embeddings=True),
                dtype=np.float32,
            )
            return EmbeddingBatch(
                embeddings=embeddings,
                null_mask=tuple(False for _ in texts),
            )
        except Exception as exc:
            raise EmbeddingError(
                error_code=ErrorCode.EMBEDDING_MODEL_ERROR,
                message=f"Local fallback encoding also failed: {exc}",
            ) from exc
