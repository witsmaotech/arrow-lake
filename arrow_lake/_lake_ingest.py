"""Ingest mixin — data ingestion, dataset CRUD, quality, and dedup."""

from __future__ import annotations

import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

import pyarrow as pa

if TYPE_CHECKING:
    from arrow_lake.ingest.ingestor import IngestionReport
    from arrow_lake.quality.dedup import DedupResult
    from arrow_lake.quality.models import QualityReport


# v1.10.2 P1.4: dedicated pool + in-process status for background embed backfill.
# embed_and_add + create_vector_index are deferred here when null rows exceed the
# async threshold, so a large first-time ingest / big append doesn't block the
# request thread. Mirrors the olap_executor / kg_executor isolation pattern.
# max_workers=2 bounds concurrent embed calls (local model GPU/CPU OOM guard).
_embed_backfill_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="embed-bf")
# Graceful shutdown at process exit (don't block on stuck workers). Per-task
# timeout is the embed backend's responsibility (LLM_TIMEOUT_SECONDS etc).
import atexit as _atexit
_atexit.register(_embed_backfill_executor.shutdown, wait=False)

# Coarse per-step ceiling for the background embed+vector backfill. The real
# bound is the embed per-batch timeout (30s) + Lance request_timeout; this is a
# backstop so a hung step still moves the status to failed instead of idling in
# running forever. Tunable via env.
_BG_STEP_TIMEOUT = float(os.environ.get("ARROW_LAKE_EMBED_BG_STEP_TIMEOUT", "3600"))


def _run_step_with_timeout(func: Any, *, timeout: float, label: str) -> Any:
    """Run ``func()`` in a daemon worker bounded by ``timeout``.

    Used by the background embed+vector backfill so a hung step (a Lance write
    that slipped past request_timeout, a vector-index training stall) surfaces
    as a timely FAILED status. Python can't kill the leaked worker thread; the
    Lance/embed per-call timeouts are expected to eventually release it.
    """
    holder: dict[str, Any] = {}

    def _work() -> None:
        try:
            holder["result"] = func()
        except BaseException as exc:  # noqa: BLE001 — surfaced to caller
            holder["error"] = exc

    t = threading.Thread(target=_work, daemon=True, name=f"embed-bg-{label}")
    t.start()
    t.join(timeout)
    if t.is_alive():
        raise TimeoutError(f"{label} exceeded {timeout}s")
    if "error" in holder:
        raise holder["error"]
    return holder.get("result")
_EMBED_BG_LOCK = threading.Lock()
_embed_bg: dict[str, "_EmbedBackfillStatus"] = {}


@dataclass
class _EmbedBackfillStatus:
    """Latest background embed+vector backfill status for one dataset."""

    status: str  # "running" | "completed" | "failed"
    started_at: str
    null_rows: int
    finished_at: str | None = None
    error: str | None = None


# v1.10.2 P1.4 G11: _embed_bg is process-local, but gunicorn runs N workers — a
# /embed/status request hitting a non-originating worker would see idle. Mirror
# status to Redis (same connection TaskManager uses) so any worker can read it.
_EMBED_STATUS_TTL_S = 3600
_EMBED_REDIS_PREFIX = "arrow-lake:embed_status"


_embed_redis: Any = None
_embed_redis_tried = False


def _embed_redis_client():
    """Best-effort Redis client for cross-worker status mirror.

    Prefers TaskManager's shared connection; falls back to a direct connection
    from ARROW_LAKE__REDIS__URL (lazy, cached) so the mirror works even when the
    task store isn't initialized in this deployment.
    """
    global _embed_redis, _embed_redis_tried
    try:
        from arrow_lake.api.tasks import TaskManager
        store = TaskManager._redis_store
        if store is not None and getattr(store, "_connected", False):
            return store._redis
    except Exception:  # noqa: BLE001
        pass
    if _embed_redis_tried:
        return _embed_redis
    _embed_redis_tried = True
    try:
        import redis as _redis_mod
        url = os.environ.get("ARROW_LAKE__REDIS__URL", "")
        if not url:
            return None
        _embed_redis = _redis_mod.Redis.from_url(url, socket_connect_timeout=2)
        _embed_redis.ping()
        return _embed_redis
    except Exception:  # noqa: BLE001
        _embed_redis = None
        return None


def _sync_embed_status_redis(dataset_name: str, status: dict) -> None:
    """Mirror embed backfill status to Redis for cross-worker visibility."""
    r = _embed_redis_client()
    if r is None:
        return
    try:
        import json as _json
        key = f"{_EMBED_REDIS_PREFIX}:{dataset_name}"
        r.set(key, _json.dumps(status, default=str), ex=_EMBED_STATUS_TTL_S)
    except Exception:  # noqa: BLE001 — Redis is best-effort
        pass


def _read_embed_status_redis(dataset_name: str) -> dict | None:
    """Read embed backfill status from Redis (cross-worker fallback)."""
    r = _embed_redis_client()
    if r is None:
        return None
    try:
        import json as _json
        raw = r.get(f"{_EMBED_REDIS_PREFIX}:{dataset_name}")
        if not raw:
            return None
        if isinstance(raw, bytes):
            raw = raw.decode()
        return _json.loads(raw)
    except Exception:  # noqa: BLE001
        return None


class _DeadLetterStorageAdapter:
    """Adapt LanceStorageManager to the DeadLetterWriter StorageWriter protocol.

    write(table_name, table) → create-or-append on the Lance dataset.
    """

    def __init__(self, manager: Any) -> None:
        self._manager = manager

    def write(self, table_name: str, table: Any) -> int:
        if self._manager.dataset_exists(table_name):
            self._manager.append_dataset(table_name, table)
        else:
            self._manager.create_dataset(table_name, table)
        return table.num_rows



class _LakeIngestMixin:
    """Provides data ingestion, dataset management, quality filtering, and dedup."""

    def _safe_version(self, name: str) -> int | None:
        """Best-effort current Lance version for lineage; None if unreadable."""
        try:
            return self.get_dataset_version(name)
        except Exception:  # noqa: BLE001
            return None

    def _make_ingestor(self, dataset_name: str | None = None, **extra_kwargs: Any):
        """Construct an Ingestor with the quality gate wired (v1.10.7 WP5).

        Review H9: Ingestor had a quality_gate hook but no construction site
        ever passed one — the gate was dead code. Every ingest path now goes
        through here. ``gate_mode`` defaults to shadow (count/log only);
        target_schema is set for existing datasets so the schema stage can
        validate appends against the real Lance schema.
        """
        from arrow_lake.ingest.ingestor import Ingestor
        from arrow_lake.quality.dead_letter import DeadLetterWriter
        from arrow_lake.quality.gate import build_quality_gate

        quality_cfg = getattr(self._config, "quality", None)
        gate = None
        try:
            if quality_cfg is not None:
                target_schema = None
                if dataset_name:
                    try:
                        target_schema = self.open_dataset(dataset_name).schema
                    except Exception:  # noqa: BLE001 — first write: no schema yet
                        target_schema = None
                gate = build_quality_gate(
                    quality_cfg,
                    target_schema=target_schema,
                    dead_letter_writer=DeadLetterWriter(
                        storage=_DeadLetterStorageAdapter(self._get_storage())
                    ),
                )
        except Exception:  # noqa: BLE001 — gate wiring must never block ingest
            logger.warning("quality_gate_wiring_failed", exc_info=True)
            gate = None
        return Ingestor(self._get_storage(), quality_gate=gate, **extra_kwargs)

    def ingest(
        self,
        dataset_name: str,
        file_paths: list[str],
        *,
        transforms: list[Any] | None = None,
        actor: str = "system",
    ) -> IngestionReport:
        """Ingest local files into a Lance dataset (Stories 3.1-3.5).

        Delegates to Ingestor.

        Args:
            dataset_name: Target dataset name.
            file_paths: List of file paths (CSV, JSON, JSONL, Parquet).
            transforms: Optional list of ``daft.DataFrame -> daft.DataFrame``
                        callables applied before Arrow conversion.

        Returns:
            IngestionReport with per-source and total stats.
        """
        from arrow_lake.ingest.ingestor import Ingestor

        report = self._make_ingestor(dataset_name).ingest(
            dataset_name, file_paths, transforms=transforms,
        )
        try:
            self._lineage_after_ingest(
                dataset_name, source_paths=file_paths, actor=actor,
                lance_version=self._safe_version(dataset_name), total_rows=report.total_rows,
            )
        except Exception:
            logger.warning("ingest_lineage_record_failed", exc_info=True)
        return report

    def load_hf_dataset(self, repo_id: str, *, table: str | None = None) -> Any:
        """Load a HuggingFace Lance-format dataset as an Arrow Table (v1.8.0 #8).

        Uses lancedb's ``hf://`` scheme to read lance-format datasets hosted on
        the HF Hub — useful for evaluation seed data / benchmarks. Returns the
        named table (or the first one) as an Arrow Table; caller can then
        ``create_dataset`` to materialize it locally.

        Args:
            repo_id: HF dataset repo id (e.g. "lance-format/eval-set"), or a
                full ``hf://datasets/...`` URI.
            table: Optional table name within the dataset (None = first).

        Returns:
            Arrow Table with the dataset content.

        Raises:
            ValueError: If the dataset has no tables.
        """
        import lancedb

        uri = repo_id if repo_id.startswith("hf://") else f"hf://datasets/{repo_id}"
        db = lancedb.connect(uri)
        names = db.list_tables()
        if not names:
            raise ValueError(f"No tables found in hf dataset '{repo_id}'")
        return db.open_table(table or names[0]).to_arrow()

    def write_dataframe(
        self, dataset_name: str, df: Any, mode: str = "create"
    ) -> None:
        """Write a Daft DataFrame to Lance with streaming (v1.8.0 #16).

        Daft's lazy execution streams the write, so datasets larger than memory
        (>16x RAM) are handled without materializing — use this for KG build /
        large-batch ingest. Delegates to ``LanceStorageManager.write_lance_from_dataframe``
        (``df.write_lance``); prefer over Arrow-materializing paths for huge data.

        Args:
            dataset_name: Target dataset name.
            df: Daft DataFrame (lazy) to write.
            mode: ``"create"`` | ``"append"`` | ``"overwrite"``.
        """
        self._get_storage().write_lance_from_dataframe(dataset_name, df, mode=mode)

    def ingest_batch(
        self,
        dataset_name: str,
        file_paths: list[str],
        *,
        transforms: list[Any] | None = None,
        actor: str = "system",
    ) -> IngestionReport:
        """Batch-ingest files of same type via Daft write_lance.

        Delegates to Ingestor.ingest_batch().

        Args:
            dataset_name: Target dataset name.
            file_paths: List of file paths to ingest.
            transforms: Optional Daft transform callables.

        Returns:
            IngestionReport with per-group stats.
        """
        from arrow_lake.ingest.ingestor import Ingestor

        report = self._make_ingestor(dataset_name).ingest_batch(
            dataset_name, file_paths, transforms=transforms,
        )
        try:
            self._lineage_after_ingest(
                dataset_name, source_paths=file_paths, actor=actor,
                lance_version=self._safe_version(dataset_name), total_rows=report.total_rows,
            )
        except Exception:
            logger.warning("ingest_lineage_record_failed", exc_info=True)
        return report

    def ingest_sql(
        self,
        dataset_name: str,
        *,
        sql: str,
        connection_url: str,
        partition_col: str | None = None,
        num_partitions: int | None = None,
        transforms: list[Any] | None = None,
        actor: str = "system",
    ) -> IngestionReport:
        """Ingest data from a SQL database query.

        Args:
            dataset_name: Target dataset name.
            sql: SELECT query to execute.
            connection_url: SQLAlchemy connection string.
            partition_col: Optional partition column for parallel reads.
            num_partitions: Number of read partitions.
            transforms: Optional Daft transform callables.

        Returns:
            IngestionReport with stats.
        """
        from arrow_lake.ingest.ingestor import Ingestor

        report = self._make_ingestor(dataset_name).ingest_sql(
            dataset_name,
            sql=sql,
            connection_url=connection_url,
            partition_col=partition_col,
            num_partitions=num_partitions,
            transforms=transforms,
        )
        self._lineage_after_ingest(
            dataset_name, source_descriptor={"sql": sql, "connection_url": connection_url},
            actor=actor, lance_version=self._safe_version(dataset_name),
            total_rows=report.total_rows,
        )
        return report

    def ingest_kafka(
        self,
        dataset_name: str,
        *,
        bootstrap_servers: str,
        topics: list[str] | str,
        start: str = "earliest",
        end: str = "latest",
        json_decode: bool = True,
        transforms: list[Any] | None = None,
        actor: str = "system",
    ) -> IngestionReport:
        """Ingest messages from Kafka topics.

        Args:
            dataset_name: Target dataset name.
            bootstrap_servers: Kafka broker addresses.
            topics: Topic name(s).
            start: Start bound.
            end: End bound.
            json_decode: Auto-decode JSON values.
            transforms: Optional Daft transforms.

        Returns:
            IngestionReport with stats.
        """
        from arrow_lake.ingest.ingestor import Ingestor

        report = self._make_ingestor(dataset_name).ingest_kafka(
            dataset_name,
            bootstrap_servers=bootstrap_servers,
            topics=topics,
            start=start,
            end=end,
            json_decode=json_decode,
            transforms=transforms,
        )
        topics_list = [topics] if isinstance(topics, str) else list(topics)
        self._lineage_after_ingest(
            dataset_name, source_descriptor={"kafka_topics": topics_list},
            transform_type="ingest_kafka", actor=actor,
            lance_version=self._safe_version(dataset_name), total_rows=report.total_rows,
        )
        return report

    def ingest_iceberg(
        self,
        dataset_name: str,
        *,
        table_uri: str,
        transforms: list[Any] | None = None,
        actor: str = "system",
    ) -> IngestionReport:
        """Ingest data from an Apache Iceberg table."""
        from arrow_lake.ingest.ingestor import Ingestor

        report = self._make_ingestor(dataset_name).ingest_iceberg(
            dataset_name, table_uri=table_uri, transforms=transforms,
        )
        self._lineage_after_ingest(
            dataset_name, source_descriptor={"iceberg_table": table_uri},
            transform_type="ingest_iceberg", actor=actor,
            lance_version=self._safe_version(dataset_name), total_rows=report.total_rows,
        )
        return report

    def ingest_deltalake(
        self,
        dataset_name: str,
        *,
        table_uri: str,
        version: int | None = None,
        transforms: list[Any] | None = None,
        actor: str = "system",
    ) -> IngestionReport:
        """Ingest data from a Delta Lake table."""
        from arrow_lake.ingest.ingestor import Ingestor

        report = self._make_ingestor(dataset_name).ingest_deltalake(
            dataset_name, table_uri=table_uri, version=version, transforms=transforms,
        )
        self._lineage_after_ingest(
            dataset_name, source_descriptor={"delta_table": table_uri},
            transform_type="ingest_deltalake", actor=actor,
            lance_version=self._safe_version(dataset_name), total_rows=report.total_rows,
        )
        return report

    def export_to(
        self,
        dataset_name: str,
        *,
        target_uri: str,
        format: str,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Export a Lance dataset to an external target via Daft write_*.

        Args:
            dataset_name: Source dataset name.
            target_uri: Target URI.
            format: Export format (parquet/csv/json/iceberg/clickhouse).
            **kwargs: Format-specific options.

        Returns:
            Dict with export stats.
        """
        import daft

        storage = self._get_storage()
        table = storage.read_dataset(dataset_name)
        df = daft.from_arrow(table)
        result = storage.export_dataframe(df, target_uri, format, **kwargs)
        from arrow_lake.catalog.lineage_hooks import auto_record_export
        auto_record_export(storage, dataset_name, target_uri, format)
        return result

    def ingest_and_embed(
        self,
        dataset_name: str,
        file_paths: list[str],
        *,
        text_column: str = "text_content",
        embedding_column: str = "text_embedding",
        transforms: list[Any] | None = None,
        model: str | None = None,
        num_partitions: int | None = None,
        actor: str = "system",
    ) -> Any:
        """Ingest files and generate embeddings in a single Daft pipeline.

        Args:
            dataset_name: Target dataset name.
            file_paths: Files to ingest.
            text_column: Column containing text to embed.
            embedding_column: Name for the embedding column.
            transforms: Optional Daft DataFrame transforms.
            model: Override embedding model (None = use config default).
            num_partitions: Override partition count (None = use config default).

        Returns:
            IngestEmbedResult with ingestion and embedding stats.
        """
        from arrow_lake.ingest.ingest_embed import IngestEmbedPipeline

        emb_cfg = self._config.embedding
        pipeline = IngestEmbedPipeline(
            storage=self._get_storage(),
            model=model or emb_cfg.model,
            provider=emb_cfg.daft_provider,
            num_partitions=num_partitions or emb_cfg.daft_num_partitions,
        )
        result = pipeline.ingest_and_embed(
            dataset_name,
            file_paths,
            text_column=text_column,
            embedding_column=embedding_column,
            transforms=transforms,
        )
        self._lineage_after_ingest(
            dataset_name, source_paths=file_paths, transform_type="ingest_and_embed",
            actor=actor, lance_version=self._safe_version(dataset_name),
        )
        return result

    def ingest_http(
        self,
        dataset_name: str,
        urls: list[str],
        *,
        actor: str = "system",
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

        report = self._make_ingestor(dataset_name).ingest_http(dataset_name, urls)
        self._lineage_after_ingest(
            dataset_name, source_paths=urls, transform_type="ingest_http", actor=actor,
            lance_version=self._safe_version(dataset_name), total_rows=report.total_rows,
        )
        return report

    def ingest_images(
        self,
        dataset_name: str,
        image_paths: list[str],
        *,
        actor: str = "system",
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

        report = self._make_ingestor(dataset_name).ingest_images(dataset_name, image_paths)
        self._lineage_after_ingest(
            dataset_name, source_paths=image_paths, transform_type="ingest_images", actor=actor,
            lance_version=self._safe_version(dataset_name), total_rows=report.total_rows,
        )
        return report

    def ingest_videos(
        self,
        dataset_name: str,
        video_paths: list[str],
        *,
        actor: str = "system",
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

        report = self._make_ingestor(dataset_name).ingest_videos(dataset_name, video_paths)
        self._lineage_after_ingest(
            dataset_name, source_paths=video_paths, transform_type="ingest_videos", actor=actor,
            lance_version=self._safe_version(dataset_name), total_rows=report.total_rows,
        )
        return report

    def ingest_mixed(
        self,
        dataset_name: str,
        sources: dict[str, list[str]],
        *,
        actor: str = "system",
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

        report = self._make_ingestor(dataset_name).ingest_mixed(dataset_name, sources)
        self._lineage_after_ingest(
            dataset_name, source_descriptor={"modalities": {k: len(v) for k, v in sources.items()}},
            transform_type="ingest_mixed", actor=actor,
            lance_version=self._safe_version(dataset_name), total_rows=report.total_rows,
        )
        return report

    def ingest_documents(
        self,
        dataset_name: str,
        pdf_paths: list[str],
        *,
        doc_config: Any = None,
        doc_type: str | None = None,
        actor: str = "system",
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
            # v1.9.5 批6: raw uploads (store_raw_pdf) go to the uploads bucket.
            lambda: BlobStoreManager(config=sc, bucket=sc.uploads_bucket),
        )

        ocr_endpoint = "http://localhost:8002"
        if doc_config is not None:
            ocr_endpoint = getattr(doc_config, "ocr_endpoint", ocr_endpoint)

        ocr_client = self._get_component(
            "ocr_client",
            lambda: TurboOcrClient(endpoint=ocr_endpoint),
        )

        # v1.7: build a doc_type classifier so ingest can auto-discriminate the
        # document type per-file (only used when caller did NOT pass doc_type —
        # explicit doc_type wins). Failure is non-fatal → None → column stays "".
        doc_type_classifier = None
        if not doc_type:
            try:
                from arrow_lake.knowledge_graph.doc_type_router import DocTypeClassifier
                doc_type_classifier = DocTypeClassifier.from_llm_config(self._config.llm)
            except Exception as exc:  # noqa: BLE001 — best-effort
                import structlog
                structlog.get_logger(__name__).warning(
                    "ingest.doc_type_classifier_disabled", err=str(exc)[:150],
                )

        report = self._make_ingestor(dataset_name, doc_type_classifier=doc_type_classifier).ingest_documents(
            dataset_name, pdf_paths,
            doc_config=doc_config,
            doc_type=doc_type,
            blob_store=blob_store,
            ocr_client=ocr_client,
        )
        self._lineage_after_ingest(
            dataset_name, source_paths=pdf_paths,
            source_descriptor={"doc_type": doc_type} if doc_type else None,
            transform_type="ingest_documents", actor=actor,
            lance_version=self._safe_version(dataset_name), total_rows=report.total_rows,
        )
        return report

    def ingest_documents_and_index(
        self,
        dataset_name: str,
        pdf_paths: list[str],
        *,
        doc_config: Any = None,
        doc_type: str | None = None,
        actor: str = "system",
    ) -> IngestionReport:
        """Ingest documents AND build retrieval indexes (parse→store→embed→FTS→vector).

        Consolidates the post-ingest index sequence that was duplicated across
        routers/datasets.py + routers/async_tasks.py (架构评审 #4). SDK callers
        now get the same indexed product as HTTP — previously ``ingest_documents``
        alone left the dataset invisible to vector / hybrid / RAG retrieval.

        Best-effort semantics: index post-steps (embed/FTS/vector) that RAISE are
        caught + logged so one failing step doesn't block the others; an
        ``ingest_documents`` failure propagates (no partial write).

        Trade-off (known): per-step *timeout isolation* is NOT provided here. The
        router path runs this whole sequence under one ``run_sync`` budget; a HUNG
        step (no client-side timeout) consumes the full budget and later steps
        won't run — whereas the old per-step ``run_sync`` isolated each at 600s.
        Accepted: hung steps are rare (embed/FTS/vector normally fail fast with an
        exception, which IS caught here), and Python threads can't be force-killed
        so the old isolation leaked executor threads anyway. async_tasks path had
        no per-step timeout to begin with → no regression there.
        """
        import structlog

        report = self.ingest_documents(
            dataset_name, pdf_paths, doc_config=doc_config, doc_type=doc_type, actor=actor,
        )
        log = structlog.get_logger(__name__)

        def _try(label: str, fn: Any, *args: Any) -> bool:
            """Run one best-effort post-step; never propagate to the caller."""
            try:
                fn(*args)
                return True
            except Exception as exc:  # noqa: BLE001 — never fail ingest on a post-step
                log.warning(
                    "ingest.post_step_failed",
                    dataset=dataset_name, step=label, err=str(exc)[:160],
                )
                return False

        # P1.4: FTS does not depend on the embedding column — run it first so
        # full-text search is available even while embedding backfills in the
        # background.
        if callable(getattr(self, "create_fts_index", None)):
            _try("create_fts_index", self.create_fts_index, dataset_name)

        # P1.4: embed + vector are coupled (create_vector_index needs
        # text_embedding non-null). When null rows exceed the async threshold
        # (large first-time ingest / big append) defer BOTH to a background
        # thread so the request returns immediately; otherwise run inline.
        n_null = self._estimate_null_embeddings(dataset_name)
        threshold = getattr(self._config.embedding, "embed_async_threshold", 5000)
        with _EMBED_BG_LOCK:
            cur = _embed_bg.get(dataset_name)
        already_running = cur is not None and cur.status == "running"
        if not already_running:
            # v1.10.7 WP6: gunicorn runs N workers — a backfill started by a
            # sibling worker is invisible in our process-local dict. Consult
            # the Redis mirror before starting a duplicate (drop+add race).
            mirrored = _read_embed_status_redis(dataset_name)
            already_running = mirrored is not None and mirrored.get("status") == "running"

        embed_async_info: dict | None = None
        embed_fn = getattr(self, "embed_and_add", None)
        if already_running:
            # A background backfill owns embed+vector for this dataset — don't
            # also run it inline (would race on drop+add). P1.2 self-healing + the
            # running task cover new nulls on its next pass / next ingest.
            log.info("embed_backfill_skip_running", dataset=dataset_name)
        elif n_null > threshold and callable(embed_fn):
            _embed_backfill_executor.submit(self._run_embed_and_vector_bg, dataset_name, n_null)
            embed_async_info = {
                "status": "running",
                "task": "embed_backfill",
                "null_rows": n_null,
                "threshold": threshold,
                "hint": "embedding + vector index building in background; vector "
                        "search unavailable until complete, FTS works now",
            }
            log.info("embed_backfill_deferred", dataset=dataset_name, null_rows=n_null)
        else:
            if callable(embed_fn):
                _try("embed_documents", self.embed_and_add, dataset_name)
            if callable(getattr(self, "create_vector_index", None)):
                _try("create_vector_index", self.create_vector_index, dataset_name)

        if embed_async_info is not None:
            report = replace(report, embed_async=embed_async_info)
        return report

    def create_dataset(self, name: str, data: pa.Table, *, actor: str = "system") -> None:
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
            self._lineage_after_ingest(
                name, transform_type="create", operation="create", actor=actor,
                lance_version=self._safe_version(name), total_rows=rows,
            )

    def append_dataset(self, name: str, data: pa.Table, *, actor: str = "system") -> None:
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
            self._lineage_after_ingest(
                name, transform_type="append", operation="append", actor=actor,
                lance_version=self._safe_version(name), total_rows=rows,
            )

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
            with self._trace_span("upsert", dataset=dataset_name, rows=rows):
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
            with self._trace_span("delete_rows", dataset=dataset_name):
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
            with self._trace_span("update_rows", dataset=dataset_name):
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
        text_column: str | None = None,
    ) -> DedupResult:
        """Run content deduplication on a dataset (Story 4.7).

        Delegates to ContentDeduplicator.

        Args:
            dataset_name: Name of the Lance dataset.
            strategy: "exact", "perceptual", "both", or "minhash" (None = use config).
            action: "flag" or "remove" (None = use config).
            perceptual_threshold: pHash Hamming distance (None = use config).
            text_column: Required for strategy="minhash" (semantic text dedup).

        Returns:
            DedupResult with dedup statistics and processed table.
        """
        from arrow_lake.quality.dedup import ContentDeduplicator

        config = self._config.quality
        dedup = ContentDeduplicator(
            strategy=strategy or config.dedup_strategy,
            action=action or config.dedup_action,
            perceptual_threshold=perceptual_threshold or config.dedup_perceptual_threshold,
            text_column=text_column,
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
        from arrow_lake.config._enums import EmbeddingBackend  # noqa: F401 (kept for callers)
        emb_cfg = self._config.embedding
        storage = self._get_storage()

        # v1.10.2 P1: if the embedding column already exists (append scenario),
        # backfill ONLY the null rows. Re-running add_columns_table would fail
        # ("Column names must not already exist") and be swallowed by the ingest
        # post-step best-effort, leaving new append rows null (§2.2).
        if storage.has_column(dataset_name, embedding_column):
            return self._backfill_embedding_nulls(
                dataset_name, text_column, embedding_column, emb_cfg, batch_size
            )

        # First-time add (original path): full encode + add_columns_table.
        table = storage.read_dataset(dataset_name, columns=[text_column])
        texts = table.column(text_column).to_pylist()
        vecs, dim = self._encode_texts(texts, emb_cfg, batch_size)
        vec_array = pa.FixedSizeListArray.from_arrays(
            pa.array(vecs.ravel()), dim,  # vecs is a float32 ndarray
        )
        storage.add_columns_table(dataset_name, pa.table({embedding_column: vec_array}))
        return len(texts)

    def _encode_texts(self, texts, emb_cfg, batch_size=None):
        """Encode ``texts`` via the configured backend → ``(np.ndarray [n, dim], dim)``.

        Centralizes the DAFT / OPENAI / LOCAL branches (v1.10.2 P1) so the
        first-time add and the null-backfill paths share one encoder path.
        """
        from arrow_lake.config._enums import EmbeddingBackend
        from arrow_lake.embed.encoder import ApiEmbeddingEncoder, LocalEmbeddingEncoder
        import numpy as np

        effective_batch = batch_size or emb_cfg.batch_size
        n = len(texts)
        if emb_cfg.backend == EmbeddingBackend.DAFT:
            from arrow_lake.embed.daft_encoder import DaftBatchEncoder
            encoder = DaftBatchEncoder(
                model=emb_cfg.model, provider=emb_cfg.daft_provider,
                num_partitions=emb_cfg.daft_num_partitions,
                expected_dim=emb_cfg.expected_dim,
            )
            arr, dim = encoder.encode_to_vectors(pa.table({"t": texts}), column="t")
            return np.asarray(arr, dtype=np.float32), dim
        if emb_cfg.backend == EmbeddingBackend.OPENAI and emb_cfg.api_base:
            encoder = ApiEmbeddingEncoder(
                api_base=emb_cfg.api_base, api_key=emb_cfg.api_key,
                model_name=emb_cfg.model, batch_size=effective_batch,
            )
            out: list[list[float]] = []
            for i in range(0, n, effective_batch):
                out.extend(encoder.encode(texts[i : i + effective_batch]).embeddings.tolist())
            arr = np.asarray(out, dtype=np.float32)
            return arr, (arr.shape[1] if n else 0)
        encoder = LocalEmbeddingEncoder(
            model_name=emb_cfg.model, batch_size=effective_batch,
            expected_dim=emb_cfg.expected_dim,
        )
        arr, dim = encoder.encode_to_vectors(pa.table({"t": texts}), column="t")
        return np.asarray(arr, dtype=np.float32), dim

    def _backfill_embedding_nulls(self, dataset_name, text_column, embedding_column,
                                  emb_cfg, batch_size=None) -> int:
        """Backfill NULL embedding rows in place (v1.10.2 P1, append scenario).

        Arrow-native: read text+emb (projected), encode ONLY null rows, rebuild
        the column via numpy (no ``.to_pylist()`` — avoids the ~40GB OOM on a
        1M×1024 table, review performance C1), then drop+re-add. Non-null rows
        are NOT re-encoded. Embeddings are reproducible from text → a mid-write
        failure is recoverable (re-run), not fatal data loss.
        """
        import numpy as np
        storage = self._get_storage()
        table = storage.read_dataset(dataset_name, columns=[text_column, embedding_column])
        n = table.num_rows
        if n == 0:
            return 0
        text_arr = table.column(text_column)
        emb_fsl = table.column(embedding_column).combine_chunks()  # FixedSizeListArray
        dim = emb_fsl.type.list_size
        null_mask = emb_fsl.is_null().to_numpy(zero_copy_only=False)
        n_null = int(null_mask.sum())
        if n_null == 0:
            return 0  # idempotent no-op
        # P1.4: the async threshold gate now lives in ingest_documents_and_index's
        # post-step (large null → fire-and-forget in _embed_backfill_executor).
        # This direct-call path is reached only for sync callers; large counts are
        # deferred before ever calling embed_and_add, so no inline gate is needed.
        null_idxs = np.nonzero(null_mask)[0]
        texts = [
            (text_arr[i].as_py() if text_arr[i].is_valid else "")
            for i in null_idxs
        ]
        new_vecs, _ = self._encode_texts(texts, emb_cfg, batch_size)  # [n_null, dim]
        # Rebuild the full [n, dim] grid. FSL's child array holds values ONLY for
        # non-null rows (verified live: child len = (n - n_null) * dim, NOT n*dim
        # — null rows occupy no child slots), so scatter the existing non-null
        # vectors back to their original row positions and fill the null
        # positions with the freshly-encoded vectors. (replace_with_mask has no
        # FixedSizeList kernel — ArrowNotImplementedError, verified.)
        non_null_rows = np.asarray(emb_fsl.flatten(), dtype=np.float32).reshape(
            n - n_null, dim
        )
        full = np.zeros((n, dim), dtype=np.float32)
        full[np.nonzero(~null_mask)[0]] = non_null_rows  # preserve existing vectors
        full[null_idxs] = np.asarray(new_vecs, dtype=np.float32).reshape(-1, dim)
        new_fsl = pa.FixedSizeListArray.from_arrays(
            pa.array(full.ravel(), type=pa.float32()), dim
        )
        from arrow_lake.exceptions import ErrorCode, StorageError
        # P1.3 TOCTOU guard (review H4): a concurrent ingest append between our
        # read and the drop would make the rebuilt column (n rows) mismatch the
        # live table → add_columns raises on row alignment. Detect it explicitly
        # and abort, so the caller's best-effort retries on the next ingest
        # (clean failure) rather than silently misaligning / wrong-row vectors.
        live_n = storage.read_dataset(dataset_name, columns=[text_column]).num_rows
        if live_n != n:
            raise StorageError(
                error_code=ErrorCode.STORAGE_WRITE_FAILED,
                message=(
                    f"embedding backfill aborted: '{dataset_name}' changed mid-build "
                    f"({n}→{live_n} rows, concurrent append); next ingest retries"
                ),
            )
        # P1.2 (review H3): drop+re-add is not atomic, but embeddings are fully
        # reproducible from text_content — if add_columns fails after the drop,
        # the column is absent and the NEXT ingest's embed_and_add sees no
        # text_embedding column → takes the first-time path → rebuilds it whole
        # (self-healing, no manual intervention). So no temp-column staging.
        storage.drop_column(dataset_name, embedding_column)
        storage.add_columns_table(dataset_name, pa.table({embedding_column: new_fsl}))
        return n_null

    def _estimate_null_embeddings(
        self, dataset_name: str, embedding_column: str = "text_embedding",
    ) -> int:
        """P1.4: cheap estimate of rows needing embedding (gates sync vs async).

        First-time (column absent): every row needs embedding → returns total
        rows. Otherwise counts NULL rows in the embedding column via the Arrow
        valid-bitmap (does NOT decode vector values). Used only to compare
        against ``embed_async_threshold`` — not for the actual encode.
        """
        storage = self._get_storage()
        if not storage.has_column(dataset_name, embedding_column):
            try:
                return storage.read_dataset(dataset_name, columns=["text_content"]).num_rows
            except Exception:  # noqa: BLE001 — unreadable → treat as nothing to do
                return 0
        # append: text_embedding 列已存在。不 read_dataset 全列(FixedSizeList
        # 2560dim 物化 child 卡死,实测 SELECT * 同列 120s 超时),用 lance
        # count_rows(filter) 下推到 valid bitmap(只查 null 标记,不读向量值)。
        try:
            return int(storage.open_dataset(dataset_name).count_rows(
                filter=f"{embedding_column} IS NULL"))
        except Exception:  # noqa: BLE001
            return 0

    def _run_embed_and_vector_bg(self, dataset_name: str, null_rows: int) -> None:
        """P1.4: background embed + vector index (runs in _embed_backfill_executor).

        Synchronous: embed_and_add (first-time or null-backfill, internal branch)
        then create_vector_index (depends on text_embedding non-null). Exceptions
        are swallowed into the status entry — P1.2 self-healing means the next
        ingest retries. Never raises (it's a background thread).
        """
        import structlog
        log = structlog.get_logger(__name__)
        started = datetime.now(timezone.utc).isoformat()
        cur = _EmbedBackfillStatus(status="running", started_at=started, null_rows=null_rows)
        with _EMBED_BG_LOCK:
            _embed_bg[dataset_name] = cur
        _sync_embed_status_redis(dataset_name, asdict(cur))
        try:
            _run_step_with_timeout(
                lambda: self.embed_and_add(dataset_name),
                timeout=_BG_STEP_TIMEOUT, label="embed",
            )
            if callable(getattr(self, "create_vector_index", None)):
                _run_step_with_timeout(
                    lambda: self.create_vector_index(dataset_name),
                    timeout=_BG_STEP_TIMEOUT, label="vector_index",
                )
            cur = _EmbedBackfillStatus(
                status="completed", started_at=cur.started_at,
                finished_at=datetime.now(timezone.utc).isoformat(),
                null_rows=cur.null_rows,
            )
            with _EMBED_BG_LOCK:
                _embed_bg[dataset_name] = cur
            _sync_embed_status_redis(dataset_name, asdict(cur))
            log.info("embed_backfill_bg_completed", dataset=dataset_name)
        except Exception as exc:  # noqa: BLE001 — background thread, never raise
            cur = _EmbedBackfillStatus(
                status="failed", started_at=cur.started_at,
                finished_at=datetime.now(timezone.utc).isoformat(),
                null_rows=null_rows, error=type(exc).__name__,
            )
            with _EMBED_BG_LOCK:
                _embed_bg[dataset_name] = cur
            _sync_embed_status_redis(dataset_name, asdict(cur))
            log.warning(
                "embed_backfill_bg_failed", dataset=dataset_name, err=str(exc)[:200],
            )

    def get_embed_backfill_status(self, dataset_name: str) -> dict | None:
        """P1.4: latest background embed+vector backfill status for a dataset.

        Returns a dict (status/started_at/finished_at/null_rows/error) or None
        if no backfill has ever run for this dataset.
        """
        with _EMBED_BG_LOCK:
            st = _embed_bg.get(dataset_name)
            if st is not None:
                return asdict(st)
        # G11 cross-worker fallback: this worker may not be the one that kicked
        # off the background backfill — check Redis (set by the originating worker).
        return _read_embed_status_redis(dataset_name)

    def embed_media(
        self,
        dataset_name: str,
        *,
        image_column: str,
        embedding_column: str = "image_embedding",
    ) -> int:
        """Encode an image/video-bytes column via CLIP/SigLIP, add embedding in-place.

        Uses CLIPImageEncoder (model configurable via CLIP_MODEL_SOURCE /
        CLIP_MODEL_NAME env; defaults to openai/clip-vit-base-patch32 via HF cache).
        Mirrors embed_and_add: read column → encode → add_columns_table (no rewrite).
        """
        from arrow_lake.embed.image_encoder import CLIPImageEncoder

        encoder = CLIPImageEncoder(image_column=image_column)
        table = self._get_storage().read_dataset(dataset_name, columns=[image_column])
        result = encoder.encode(table)
        if result.embedding_dim > 0 and result.table is not None:
            vec_table = pa.table({embedding_column: result.table.column(result.vector_column)})
            self._get_storage().add_columns_table(dataset_name, vec_table)
        return result.embedded
