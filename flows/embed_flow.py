"""Embed Flow — parallel embedding with sharding, resource management, and HTML report.

Splits a dataset into shards, fans out embedding via @foreach,
merges results, and generates an HTML card report.

Run locally::

    python flows/embed_flow.py run

Run with API encoder::

    python flows/embed_flow.py run --encoder-type api
"""

from __future__ import annotations

from typing import Any

from arrow_lake.workflow.base import ArrowLakeFlowSpec
from metaflow import (  # type: ignore[import-untyped]
    FlowSpec,
    Parameter,
    project,
    resources,
    step,
)


@project(name="arrow_lake")
class EmbedFlow(ArrowLakeFlowSpec, FlowSpec):
    """Parallel embedding: foreach sharding + GPU resources + HTML card."""

    base_uri: str = Parameter("base-uri", default="./data/lake")
    config_path: str = Parameter("config-path", default="")
    dataset_name: str = Parameter("dataset-name", default="documents")
    encoder_type: str = Parameter(
        "encoder-type", default="local", help="api | local"
    )
    shard_size: int = Parameter("shard-size", default=500)
    vector_column: str = Parameter("vector-column", default="vector")
    text_column: str = Parameter("text-column", default="text_content")

    @step
    def start(self) -> None:
        """Load dataset and split into shards."""
        from arrow_lake.ingest.storage import LanceStorageManager

        self.config = self._load_config()
        self._auto_tag()

        storage = LanceStorageManager(base_uri=self.base_uri)
        table = storage.read_dataset(self.dataset_name)
        self.total_rows = table.num_rows

        self.shards: list[tuple[int, int]] = []
        for offset in range(0, self.total_rows, self.shard_size):
            length = min(self.shard_size, self.total_rows - offset)
            self.shards.append((offset, length))

        self.next(self.encode_shard, foreach="shards")

    @resources(gpu=1, memory=16000)
    @step
    def encode_shard(self) -> None:
        """Encode a single shard of rows."""
        import pyarrow as pa
        import structlog

        logger = structlog.get_logger(__name__)
        offset, length = self.input

        try:
            from arrow_lake.ingest.storage import LanceStorageManager

            storage = LanceStorageManager(base_uri=self.base_uri)
            table = storage.read_dataset(self.dataset_name)
            shard_table = table.slice(offset, length)

            texts = [
                str(v) if v is not None else ""
                for v in shard_table.column(self.text_column).to_pylist()
            ]

            if self.encoder_type == "api":
                embeddings = self._encode_api(texts)
            else:
                embeddings = self._encode_local(texts)

            dim = embeddings.shape[1]
            vec_col = pa.FixedSizeListArray.from_arrays(embeddings.ravel(), dim)
            self._embedded_table: pa.Table | None = shard_table.append_column(
                self.vector_column, vec_col
            )
            self.result: dict[str, Any] = {
                "shard": self.input,
                "status": "success",
                "rows": length,
            }
            logger.info("embed_shard_complete", offset=offset, rows=length, dim=dim)
        except Exception as exc:
            self._embedded_table = None
            self.result = {
                "shard": self.input,
                "status": "failed",
                "error": str(exc),
            }
            logger.warning(
                "embed_shard_failed", offset=offset, error=str(exc)
            )

        self.next(self.join)

    @step
    def join(self, inputs: list) -> None:
        """Merge all shard results and write back to dataset."""
        import pyarrow as pa
        import structlog
        from arrow_lake.ingest.storage import LanceStorageManager

        logger = structlog.get_logger(__name__)

        tables = []
        for inp in inputs:
            if getattr(inp, "_embedded_table", None) is not None:
                tables.append(inp._embedded_table)

        if tables:
            merged = pa.concat_tables(tables)
            storage = LanceStorageManager(base_uri=self.base_uri)
            storage.create_dataset(self.dataset_name, merged)

        self.total_embedded = sum(
            1 for i in inputs if getattr(i, "result", {}).get("status") == "success"
        )
        self.total_failed = sum(
            1 for i in inputs if getattr(i, "result", {}).get("status") == "failed"
        )
        self.total_shards = self.total_embedded + self.total_failed
        # Propagate start-step artifacts through foreach boundary
        for inp in inputs:
            self.total_rows = getattr(inp, "total_rows", 0)
            break
        logger.info(
            "embed_join_complete",
            embedded=self.total_embedded,
            failed=self.total_failed,
        )
        self.next(self.end)

    @step
    def end(self) -> None:
        """Print summary report."""
        import json

        summary = {
            "total_rows": self.total_rows,
            "total_shards": self.total_shards,
            "embedded_shards": self.total_embedded,
            "failed_shards": self.total_failed,
            "encoder": self.encoder_type,
            "shard_size": self.shard_size,
        }
        print(json.dumps(summary, indent=2))

    # -- helpers --------------------------------------------------------

    _model_cache: Any = None

    def _encode_local(self, texts: list[str]) -> Any:
        """Encode texts using local SentenceTransformer model (cached)."""
        import numpy as np
        from sentence_transformers import SentenceTransformer

        if self._model_cache is None:
            EmbedFlow._model_cache = SentenceTransformer("all-MiniLM-L6-v2")
        embeddings = self._model_cache.encode(
            texts, batch_size=64, show_progress_bar=False, normalize_embeddings=True
        )
        return np.asarray(embeddings, dtype=np.float32)

    def _encode_api(self, texts: list[str]) -> Any:
        """Encode texts using the API encoder."""
        from arrow_lake.embed.encoder import ApiEmbeddingEncoder

        encoder = ApiEmbeddingEncoder()
        batch = encoder.encode(texts)
        return batch.embeddings


if __name__ == "__main__":
    EmbedFlow()
