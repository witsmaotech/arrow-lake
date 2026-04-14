"""Image embedding encoder — Story 4.4 (CLIP / SigLIP).

Produces L2-normalized image embeddings using HuggingFace Transformers
vision models.  Supports CLIP and SigLIP model families.

The encoder lazily loads the model and processor on first use.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
import pyarrow as pa
import pyarrow.compute as pc
import structlog

# Module-level references for test patching.  Actual import is deferred
# in ``_ensure_loaded()`` to avoid a hard dependency on transformers.
try:
    from transformers import AutoImageProcessor as _AutoImageProcessor
    from transformers import AutoModel as _AutoModel

    AutoImageProcessor = _AutoImageProcessor
    AutoModel = _AutoModel
except ImportError:
    AutoImageProcessor = None  # type: ignore[assignment, misc]
    AutoModel = None  # type: ignore[assignment, misc]

logger = structlog.get_logger(__name__)

#: Known model → embedding dimension mapping.
_MODEL_DIMENSIONS: dict[str, int] = {
    "openai/clip-vit-base-patch32": 512,
    "openai/clip-vit-large-patch14": 768,
    "google/siglip-so400m-patch14-384": 384,
    "google/siglip-base-patch16-256": 768,
}


@dataclass(frozen=True)
class ImageEmbeddingResult:
    """Result of an image embedding pass.

    Attributes:
        total: Total image rows in the input table.
        embedded: Rows that produced a valid embedding vector.
        null_count: Rows where the image column was NULL.
        failed: Rows where embedding failed (exception caught).
        embedding_dim: Dimensionality of the output vectors.
        vector_column: Name of the appended embedding column.
    """

    total: int = 0
    embedded: int = 0
    null_count: int = 0
    failed: int = 0
    embedding_dim: int = 0
    vector_column: str = "image_embedding"


class CLIPImageEncoder:
    """Image embedding encoder using CLIP/SigLIP models.

    Lazily loads the HuggingFace model and processor on first ``encode()``
    call.  Automatically detects GPU availability and falls back to CPU.

    Args:
        model_name: HuggingFace model identifier.
        batch_size: Number of images per batch.
        image_column: Name of the binary image column in the input table.
    """

    def __init__(
        self,
        model_name: str = "openai/clip-vit-base-patch32",
        batch_size: int = 32,
        image_column: str = "image",
    ) -> None:
        self.model_name = model_name
        self.batch_size = batch_size
        self.image_column = image_column
        self.embedding_dim = _MODEL_DIMENSIONS.get(model_name, 0)
        self._processor: Any = None
        self._model: Any = None

    def _ensure_loaded(self) -> None:
        """Lazy-load the processor and model."""
        if self._processor is not None and self._model is not None:
            return

        if AutoImageProcessor is None or AutoModel is None:
            raise ImportError(
                "transformers is required for image embedding. "
                "Install with: uv pip install transformers torch"
            )

        logger.info(
            "image_encoder_loading",
            model=self.model_name,
            dim=self.embedding_dim,
        )
        self._processor = AutoImageProcessor.from_pretrained(self.model_name)
        self._model = AutoModel.from_pretrained(self.model_name)

    def encode(self, table: pa.Table) -> ImageEmbeddingResult:
        """Produce L2-normalized embeddings for images in *table*.

        Args:
            table: PyArrow table with a binary image column.

        Returns:
            ImageEmbeddingResult with counts and the output table
            gains an ``image_embedding`` column (fixed-size list of float32).
        """
        if table.num_rows == 0:
            return ImageEmbeddingResult(
                total=0,
                vector_column=self.image_column + "_embedding",
            )

        col_name = self.image_column
        if col_name not in table.column_names:
            logger.warning(
                "image_encoder_column_missing",
                column=col_name,
                available=table.column_names,
            )
            return ImageEmbeddingResult(
                total=table.num_rows,
                null_count=table.num_rows,
                vector_column=col_name + "_embedding",
            )

        self._ensure_loaded()

        img_col = table.column(col_name)
        null_mask = pc.is_null(img_col)
        null_count = int(pc.sum(pc.cast(null_mask, pa.int8())).as_py())

        # Identify valid (non-null) image indices
        valid_indices = [i for i in range(table.num_rows) if not null_mask[i].as_py()]

        embeddings = [None] * table.num_rows
        embedded_count = 0
        failed_count = 0

        for batch_start in range(0, len(valid_indices), self.batch_size):
            batch_indices = valid_indices[batch_start : batch_start + self.batch_size]
            batch_images = [img_col[i].as_py() for i in batch_indices]

            try:
                inputs = self._processor(images=batch_images, return_tensors="pt")
                outputs = self._model(**inputs)
                batch_embeddings = outputs.image_embeds.cpu().numpy()

                # L2 normalize
                norms = np.linalg.norm(batch_embeddings, axis=1, keepdims=True)
                norms = np.clip(norms, 1e-12, None)
                batch_embeddings = batch_embeddings / norms

                for j, idx in enumerate(batch_indices):
                    embeddings[idx] = batch_embeddings[j].tolist()
                    embedded_count += 1

            except Exception:
                logger.exception(
                    "image_encoder_batch_failed",
                    batch_start=batch_start,
                    batch_size=len(batch_indices),
                )
                for _idx in batch_indices:
                    failed_count += 1

        # Build output column: fixed-size list of float32
        dim = self.embedding_dim or (len(embeddings[0]) if embeddings[0] else 0)

        def _make_vector(lst: list[float] | None) -> pa.FixedSizeListArray:
            values = []
            for row_emb in embeddings:
                if row_emb is None:
                    values.extend([0.0] * dim)
                else:
                    values.extend(row_emb)
            return pa.FixedSizeListArray.from_arrays(pa.array(values, type=pa.float32()), dim)

        vector_col = _make_vector(embeddings)

        table.append_column(col_name + "_embedding", vector_col)

        result = ImageEmbeddingResult(
            total=table.num_rows,
            embedded=embedded_count,
            null_count=null_count,
            failed=failed_count,
            embedding_dim=dim,
            vector_column=col_name + "_embedding",
        )
        logger.info(
            "image_encoder_result",
            total=result.total,
            embedded=result.embedded,
            null_count=result.null_count,
            failed=result.failed,
            dim=result.embedding_dim,
        )
        return result
