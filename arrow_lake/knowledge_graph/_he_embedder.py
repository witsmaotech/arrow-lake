"""Langchain ``Embeddings`` adapter for hyper-extract over arrow-lake encoders.

hyper-extract's ``Template.create(embedder=...)`` requires a
``langchain_core.embeddings.Embeddings``, **not** arrow-lake's
``LocalEmbeddingEncoder`` / ``ApiEmbeddingEncoder`` / ``DaftBatchEncoder``.
This adapter wraps any of the project's encoders behind the langchain
Embeddings protocol so the KA pipeline (parse / feed_text / build_index) can
reuse the same embedding model that backs LanceDB — keeping the two vector
stores (LanceDB chunk-level vs KA FAISS entity-level) on the same model and
dimension.

Dispatch is by capability (duck typing), so it is forward-compatible if
``LocalEmbeddingEncoder`` later gains an ``encode(list[str])`` method:

- ``encode(list[str]) -> EmbeddingBatch``                  → ``ApiEmbeddingEncoder``
- ``encode_to_vectors(pa.Table, column) -> (ndarray, dim)`` → ``Local`` / ``Daft``
"""

from __future__ import annotations

from typing import Any

import numpy as np
from langchain_core.embeddings import Embeddings


class _LakeEmbedderAdapter(Embeddings):
    """Wrap an arrow-lake encoder as a langchain ``Embeddings``."""

    def __init__(self, encoder: Any) -> None:
        self._encoder = encoder

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        enc = self._encoder
        # ApiEmbeddingEncoder.encode(list[str]) -> EmbeddingBatch
        if hasattr(enc, "encode"):
            batch = enc.encode(texts)
            return np.asarray(batch.embeddings, dtype=np.float32).tolist()
        # LocalEmbeddingEncoder / DaftBatchEncoder.encode_to_vectors(table, col)
        if hasattr(enc, "encode_to_vectors"):
            import pyarrow as pa

            vectors, _dim = enc.encode_to_vectors(
                pa.table({"text_content": texts}), "text_content"
            )
            return np.asarray(vectors, dtype=np.float32).tolist()
        raise TypeError(
            f"Unsupported encoder {type(enc).__name__}: "
            "needs encode(list[str]) or encode_to_vectors(table, column)"
        )

    def embed_query(self, text: str) -> list[float]:
        return self.embed_documents([text])[0]
