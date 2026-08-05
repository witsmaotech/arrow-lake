"""Knowledge graph builder -- orchestrates schema creation, vertex/edge
insertion, and entity extraction from text chunks.

Two extraction granularities (``HugeGraphConfig.he_kg_granularity``):

- ``"chunk"``      -- per-chunk: each chunk extracted independently (fresh KA per
                     chunk) and inserted. Legacy path; ``KGBuilder._process_chunk``.
- ``"dataset"``    -- per-dataset: ONE hyper-extract KA fed all chunks via
                     ``feed_text`` (LLM.BALANCED cross-chunk merge), then inserted
                     as a whole + dumped to ``<ka_base_dir>/<dataset>/ka/``.
                     ``KGBuilder._execute_build`` dataset branch → ``_insert_kg``.
- ``"map_reduce"`` -- v1.9.8: concurrent per-chunk extract (NO insert) → global
                     exact-name merge (:meth:`_merge_chunk_results`) → entity
                     resolution + type-pair filter → ONE ``_insert_kg``. Decouples
                     extraction concurrency from global merging; the default since
                     v1.9.11 (auto still routes here for large datasets).

All paths share ``_insert_kg`` for vertex/edge insertion (entity + typed
vertices, references, routed relations).
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

import pyarrow as pa

from arrow_lake.config import HugeGraphConfig
from arrow_lake.knowledge_graph.client import HugeGraphClient
from arrow_lake.knowledge_graph.entity_router import (
    normalize_name,
    route_entity_type,
    route_relation,
)
from arrow_lake.knowledge_graph.extractor import (
    EntityExtractor,
    ExtractedEntity,
    ExtractedRelation,
    ExtractionResult,
)
from arrow_lake.knowledge_graph.relation_validator import (
    filter_relations_by_type_pair,
    is_project_concept_graph,
)
from arrow_lake.knowledge_graph._naming import graph_name_for
from arrow_lake.knowledge_graph.schema import ARROW_LAKE_KG_SCHEMA, schema_to_hugegraph_payload

logger = logging.getLogger(__name__)

# Per-chunk extract timeout for the map_reduce path (seconds). hyper-extract's
# internal LLM call has no timeout, so without this bound one hung request
# (dropped connection) blocks the whole build. 120s matches the LLM config; a
# normal qwen-turbo chunk extract is ~8-15s, so this only kills genuine hangs.
_MAP_EXTRACT_TIMEOUT_S = 120

# edge_label → (source_label, target_label) from the schema, for endpoint
# resolution in relation routing (v1.7.1 §4.5 double-write strategy).
_EDGE_ENDPOINTS: dict[str, tuple[str, str]] = {
    el.name: (el.source_label, el.target_label)
    for el in ARROW_LAKE_KG_SCHEMA.edge_labels
}


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------


class KGBuildStatus(StrEnum):
    """Status of a KG build task."""

    PENDING = "PENDING"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


@dataclass
class KGBuildTask:
    """Tracks the progress and result of a single KG build run."""

    task_id: str
    status: KGBuildStatus
    dataset_name: str
    total_chunks: int
    processed_chunks: int
    entity_count: int
    relation_count: int
    started_at: datetime | None
    completed_at: datetime | None
    error: str | None
    # H1: chunks that yielded NO entities/relations despite non-trivial text —
    # surfaces silent LLM/extractor failures (he backend swallows exceptions to
    # an empty result) so an operator can distinguish "no entities" from
    # "extractor was down". Surfaced via get_task_status.
    extraction_failures: int = 0


# ---------------------------------------------------------------------------
# Builder
# ---------------------------------------------------------------------------


class KGBuilder:
    """Build a knowledge graph from a pyarrow table of text chunks.

    Workflow:
    1. Ensure graph schema (idempotent).
    2. Insert document + chunk vertices.
    3. Insert ``contains_chunk`` / ``next_chunk`` edges.
    4. Extract entities/relations — per-dataset (one KA, feed_text) or
       per-chunk (legacy) — and insert via :meth:`_insert_kg`.

    Args:
        client: HugeGraph REST client.
        extractor: Entity extractor (LLM-backed).
        config: HugeGraph configuration.
        ka_base_dir: base dir for per-dataset KA dumps (``<base>/<dataset>/ka/``).
            Required for ``he_kg_granularity="dataset"``; ``None`` forces the
            per-chunk path.
    """

    def __init__(
        self,
        client: HugeGraphClient,
        extractor: EntityExtractor,
        config: HugeGraphConfig,
        *,
        ka_base_dir: str | Path | None = None,
    ) -> None:
        self._client = client
        self._extractor = extractor
        self._config = config
        self._ka_base_dir = Path(ka_base_dir) if ka_base_dir else None
        self._tasks: dict[str, KGBuildTask] = {}
        self._pending_tables: dict[str, pa.Table] = {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def build(
        self, dataset_name: str, chunks_table: pa.Table, *,
        incremental: bool = False, template_override: str | None = None,
        ka_base_dir: str | None = None,
    ) -> str:
        """Prepare a KG build task and return its ID immediately.

        The actual build runs in the background via :meth:`execute_build`.
        Use :meth:`get_task_status` to track progress.

        ``incremental``: feed only NEW chunks (not in the KA's fed_chunks) into
        the existing KA + upsert their entities/edges (idempotent). Falls back to
        a full rebuild when no dump exists or the template changed.

        ``template_override`` (v1.10.0): a template name or path binding this
        dataset to a specific extraction template; takes priority over doc_type
        routing (see ``he_extractor._resolve_template``). None = route as before.

        ``ka_base_dir`` (v1.10.0 M4): override the per-build KA-artifact root so
        the quality-validation harness can isolate its temp KA dumps/checkpoints
        from production. None = use the configured ``he_ka_base_dir``.
        """
        task_id = str(uuid.uuid4())[:8]
        started = datetime.now(UTC)
        task = KGBuildTask(
            task_id=task_id,
            status=KGBuildStatus.RUNNING,
            dataset_name=dataset_name,
            total_chunks=chunks_table.num_rows,
            processed_chunks=0,
            entity_count=0,
            relation_count=0,
            started_at=started,
            completed_at=None,
            error=None,
        )
        self._tasks[task_id] = task
        self._pending_tables[task_id] = (chunks_table, incremental, template_override, ka_base_dir)
        return task_id

    async def execute_build(self, task_id: str) -> None:
        """Execute a previously prepared build task in the background."""
        logger.debug("KGDISPATCH execute_build ENTER task=%s has_task=%s builder_id=%s", task_id, task_id in self._tasks, id(self))
        task = self._tasks.get(task_id)
        pending = self._pending_tables.pop(task_id, None)
        if task is None or pending is None:
            logger.debug("KGDISPATCH execute_build EARLY_RETURN task_none=%s pending_none=%s", task is None, pending is None)
            return
        table, incremental, template_override, ka_base_dir = pending
        try:
            await self._execute_build(task, table, incremental=incremental,
                                      template_override=template_override,
                                      ka_base_dir=ka_base_dir)
            task.status = KGBuildStatus.COMPLETED
        except asyncio.CancelledError:
            # Cancellation is abnormal — mark FAILED, then propagate (do NOT
            # swallow; asyncio.CancelledError is BaseException on 3.8+ so a bare
            # `except Exception` would miss it, but be explicit for safety).
            task.status = KGBuildStatus.FAILED
            task.error = "cancelled"
            raise
        except Exception as exc:  # noqa: BLE001 — any build error fails the task
            task.status = KGBuildStatus.FAILED
            task.error = str(exc)
            logger.error("KG build %s failed", task_id, exc_info=True)
        finally:
            task.completed_at = datetime.now(UTC)
            # v1.10.0: clear the per-build template override so it never leaks
            # into a later unrelated build on the same extractor instance.
            self._extractor._active_template_override = None

    def get_task_status(self, task_id: str) -> KGBuildTask | None:
        """Return the current status of a build task, or None if unknown."""
        return self._tasks.get(task_id)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    async def _execute_build(
        self, task: KGBuildTask, table: pa.Table, *, incremental: bool = False,
        template_override: str | None = None, ka_base_dir: str | None = None,
    ) -> None:
        """Run the full build pipeline.

        ``incremental``: feed only new chunks into the existing KA (KG insert is
        idempotent upsert by primary key, so re-inserting the merged result is
        safe). Falls back to a full rebuild inside build_dataset_ka when no dump
        exists or the template changed.

        ``template_override`` (v1.10.0): set on the extractor for this build so
        every granularity's ``_resolve_template`` honors the per-dataset binding.
        Cleared by :meth:`execute_build`'s finally to avoid leaking across builds.

        ``ka_base_dir`` (v1.10.0 M4): override root for this build's KA artifacts
        (dataset-path dump + map_reduce checkpoint). The quality harness isolates
        its temp artifacts from production. None = configured ``he_ka_base_dir``.
        """
        # v1.10.0: per-dataset template binding — single chokepoint in _resolve_template.
        self._extractor._active_template_override = template_override
        # v1.10.0 M4: resolve the KA-artifact root for THIS build (override wins,
        # else builder-configured base, else config he_ka_base_dir). None only
        # when no base is configured at all.
        if ka_base_dir:
            _ka_root: Path | None = Path(ka_base_dir)
        elif self._ka_base_dir is not None:
            _ka_root = self._ka_base_dir
        elif getattr(self._config, "he_ka_base_dir", None):
            _ka_root = Path(self._config.he_ka_base_dir)
        else:
            _ka_root = None
        # v1.8.6: per-dataset graph isolation — every write targets kg_{dataset}.
        graph_name = graph_name_for(task.dataset_name)
        # 1. Ensure schema (also creates graph if needed)
        logger.debug("KG build %s: STEP1 ensure_schema START graph=%s", task.task_id, graph_name)
        schema_payload = schema_to_hugegraph_payload(ARROW_LAKE_KG_SCHEMA)
        await self._client.ensure_schema(schema_payload, graph_name=graph_name)
        logger.debug("KG build %s: STEP1 ensure_schema DONE", task.task_id)

        if table.num_rows == 0:
            return

        # 1b. Normalize columns — content first (before adding id to avoid
        # rename collision), then id, document_name, chunk_index
        if "content" not in table.column_names:
            text_col = (
                "text_content"
                if "text_content" in table.column_names
                else table.column_names[0]
            )
            new_names = ["content" if c == text_col else c for c in table.column_names]
            table = table.rename_columns(new_names)
        if "id" not in table.column_names:
            table = table.add_column(
                0, "id", pa.array([str(i) for i in range(table.num_rows)])
            )
        if "document_name" not in table.column_names:
            table = table.append_column(
                "document_name", pa.array([task.dataset_name] * table.num_rows)
            )
        if "chunk_index" not in table.column_names:
            table = table.append_column(
                "chunk_index", pa.array(list(range(table.num_rows)))
            )

        batch_size = self._config.build_batch_size

        # Columns used by STEP2-5 AND the extraction branches below — extract the
        # FULL set once (branches need all chunks; STEP2-5 filter to new below).
        chunk_ids = table.column("id").to_pylist()
        contents = [str(c) if c is not None else "" for c in table.column("content").to_pylist()]
        doc_name_col = table.column("document_name").to_pylist()
        chunk_indices = table.column("chunk_index").to_pylist()
        doc_type_col = (
            table.column("doc_type").to_pylist()
            if "doc_type" in table.column_names
            else [None] * table.num_rows
        )

        # v1.10.2 M4 P4: incremental STEP2-5 — only process NEW chunks (not yet
        # in the graph). fed_chunks {cid: hash} is the canonical "already built"
        # marker (shared with the dataset path / map_reduce checkpoint). For
        # incremental=False (default) fed_map is empty ⇒ every chunk is "new" ⇒
        # STEP2-5 process the full set, byte-identical to v1.10.1 (G6). Re-adding
        # is idempotent (vertex PRIMARY_KEY + edge frequency=SINGLE from M2), so
        # this is a REST-call optimization, not a correctness fix.
        import hashlib as _hl

        def _chunk_hash(t: str) -> str:
            return _hl.sha1((t or "").encode("utf-8", "replace")).hexdigest()[:16]

        fed_map: dict[str, str] = {}
        if incremental:
            from arrow_lake.knowledge_graph._naming import artifact_key_for
            _fed_path = (
                (_ka_root or Path(self._config.he_ka_base_dir))
                / artifact_key_for(task.dataset_name) / "ka" / "fed_chunks.json"
            )
            try:
                import json as _json
                _raw = _json.loads(_fed_path.read_text(encoding="utf-8"))
                if isinstance(_raw, dict):
                    fed_map = {str(k): str(v) for k, v in _raw.items()}
            except Exception:  # noqa: BLE001 — missing/corrupt ⇒ full STEP2-5
                fed_map = {}
        new_idx = [
            i for i, cid in enumerate(chunk_ids)
            if fed_map.get(cid, "") != _chunk_hash(contents[i])
        ]
        new_cid_set = {chunk_ids[i] for i in new_idx}
        if incremental:
            logger.info(
                "KG build %s STEP2-5 incremental: %d chunks, %d new (STEP2-5 skip %d)",
                task.dataset_name, len(chunk_ids), len(new_idx), len(chunk_ids) - len(new_idx),
            )

        # 2. Document vertices — only documents appearing in NEW chunks.
        new_doc_names = list(dict.fromkeys(doc_name_col[i] for i in new_idx))
        logger.debug("KG build %s: STEP2 add doc vertices n=%d START", task.task_id, len(new_doc_names))
        doc_hg_ids = await self._client.add_vertices(
            [{"label": "document", "properties": {"id": n, "name": n}} for n in new_doc_names],
            graph_name=graph_name,
        ) if new_doc_names else []
        logger.debug("KG build %s: STEP2 add doc vertices DONE", task.task_id)
        # doc_id_map covers the docs re-added THIS build. Contains edges below
        # are only for NEW chunks, whose docs ⊆ new_doc_names, so all lookups hit.
        doc_id_map: dict[str, str] = dict(zip(new_doc_names, doc_hg_ids, strict=True))

        # 3. Chunk vertices — NEW chunks only.
        chunk_vertices = [
            {"label": "chunk", "properties": {
                "id": chunk_ids[i], "content": contents[i], "chunk_index": chunk_indices[i],
            }}
            for i in new_idx
        ]
        all_chunk_hg_ids: list[str] = []
        logger.debug("KG build %s: STEP3 add chunk vertices n=%d START", task.task_id, len(chunk_vertices))
        for i in range(0, len(chunk_vertices), batch_size):
            batch = chunk_vertices[i : i + batch_size]
            hg_ids = await self._client.add_vertices(batch, graph_name=graph_name)
            all_chunk_hg_ids.extend(hg_ids)
        logger.debug("KG build %s: STEP3 add chunk vertices DONE", task.task_id)
        # chunk_id_map = NEW chunks only. references(chunk→entity) edges for OLD
        # chunks' entities already exist from prior builds; this build adds only
        # new-chunk-owned references (correct, no dups — M2 edge idempotency).
        chunk_id_map: dict[str, str] = {
            chunk_ids[i]: hgid for i, hgid in zip(new_idx, all_chunk_hg_ids, strict=True)
        }

        # 4. contains_chunk edges — NEW chunks only.
        logger.debug("KG build %s: STEP4 contains edges START", task.task_id)
        contains_edges: list[dict[str, Any]] = [
            {"label": "contains_chunk", "outV": doc_id_map[doc_name_col[i]],
             "outVLabel": "document", "inV": chunk_id_map[chunk_ids[i]],
             "inVLabel": "chunk", "properties": {}}
            for i in new_idx
        ]
        for i in range(0, len(contains_edges), batch_size):
            await self._client.add_edges(contains_edges[i : i + batch_size], graph_name=graph_name)

        # 5. next_chunk edges — among NEW chunks + boundary (old-last → new-first
        # per doc spanning old+new). Append-only ⇒ new chunks are a suffix.
        logger.debug("KG build %s: STEP5 next edges START", task.task_id)
        next_edges = self._build_next_chunk_edges_hg(
            chunk_id_map,
            [doc_name_col[i] for i in new_idx],
            [chunk_indices[i] for i in new_idx],
        )
        if incremental and new_idx:
            next_edges.extend(await self._build_boundary_next_edges(
                chunk_ids, doc_name_col, chunk_indices, new_cid_set, chunk_id_map, graph_name,
            ))
        if next_edges:
            for i in range(0, len(next_edges), batch_size):
                await self._client.add_edges(next_edges[i : i + batch_size], graph_name=graph_name)

        # 6. doc_type handling (shared by both granularities).
        # If any chunk carries an explicit doc_type, use them per-chunk (chunk
        # path) and take the first non-empty as the dataset-level template
        # (dataset path). Only when ALL chunks lack doc_type do we infer ONCE
        # (document-level) so every chunk shares one template.
        if any(d for d in doc_type_col):
            chunk_doc_types = doc_type_col
            dataset_doc_type = next((d for d in doc_type_col if d), None)
        else:
            dataset_doc_type = await self._infer_doc_type(contents)
            chunk_doc_types = [dataset_doc_type] * len(chunk_ids)

        # 7. Extract + insert.
        total_entities = 0
        total_relations = 0
        extraction_failures = 0  # H1: chunks (or dataset) with empty result
        concurrency = self._config.build_concurrency
        batch_delay = self._config.build_batch_delay
        semaphore = asyncio.Semaphore(concurrency)
        # Write-side gate, separate from the extraction semaphore above.
        write_sem = asyncio.Semaphore(self._config.write_concurrency)

        # v1.9.4: granularity (auto/dataset/chunk). grouped tier removed — MERGE_FIELD
        # (non-LLM merge, see he_extractor._create_ka) makes dataset stable at any
        # scale, so the grouped middle tier is no longer needed.
        _gran = getattr(self._extractor, "_kg_granularity", "chunk")
        if _gran == "auto":
            _nchunks = len(chunk_ids)
            # v1.9.8: large files → map_reduce (concurrent extract + global merge;
            # validated on wuhu — better quality than the no-merge chunk path and
            # avoids the dataset path's serial feed_text). Small files → dataset.
            _gran = "map_reduce" if _nchunks > self._config.he_kg_chunk_min_chunks else "dataset"
        use_dataset_path = (
            _gran == "dataset"
            and hasattr(self._extractor, "build_dataset_ka")
            and _ka_root is not None
        )

        if use_dataset_path:
            # --- v1.8.8 per-dataset: ONE KA, feed_text all chunks ---
            # [#1] route via TemplateTypeSelector (config he_template_type +
            # temporal heuristic) when set, else DocTypeRouter as before.
            _sample = next((c for c in contents if c), "")
            template_path = self._extractor._resolve_template(
                dataset_doc_type, _sample, None,
            )
            # [#naming] use artifact_key_for so the build-time KA dir matches
            # the query-time dir (he_extractor._ka_dir_for) and the graph name.
            from arrow_lake.knowledge_graph._naming import artifact_key_for
            ka_dir = _ka_root / artifact_key_for(task.dataset_name) / "ka"
            # [#11] Archive the current dump (if any) before overwrite so a
            # regressive/failed rebuild can be rolled back. Then prune to the
            # configured max versions to bound disk usage.
            try:
                from arrow_lake.knowledge_graph import ka_versioning
                ka_versioning.archive_current(_ka_root, task.dataset_name)
                _max = getattr(self._config, "he_ka_max_versions", 0) or 0
                if _max > 0:
                    ka_versioning.prune(_ka_root, task.dataset_name, keep=_max)
            except Exception as exc:  # noqa: BLE001 — versioning is best-effort
                logger.warning("KA versioning archive/prune failed for %s: %s",
                               task.dataset_name, exc)
            _chunk_pairs = list(zip(chunk_ids, contents, strict=True))
            logger.debug("KG build %s: STEP7 build_dataset_ka START (dataset, %d chunks)", task.task_id, len(chunk_ids))
            dataset_ka = await self._extractor.build_dataset_ka(
                template_path, _chunk_pairs, ka_dir,
                incremental=incremental,
            )
            logger.debug("KG build %s: STEP7 extraction DONE entities=%d", task.task_id, len(dataset_ka.result.entities))
            result = dataset_ka.result
            total_entities = len(result.entities)
            total_relations = len(result.relations)
            task.processed_chunks = len(chunk_ids)
            if not result.entities and not result.relations:
                extraction_failures += 1
                logger.warning(
                    "KG dataset KA yielded no entities for %s (chunks=%d) — "
                    "possible extractor/LLM failure",
                    task.dataset_name, len(chunk_ids),
                )
            else:
                # (A) v1.9.9 soft-degrade (consistency with map_reduce).
                if getattr(self._config, "he_kg_type_pair", True):
                    result = filter_relations_by_type_pair(
                        result, template_path, on_illegal="degrade",
                    )
                    total_relations = len(result.relations)
                _hg_cfg = getattr(self._config, "hugegraph", None) or self._config
                _resolution_on = getattr(_hg_cfg, "he_entity_resolution", "off") != "off"
                _orphan_on = getattr(_hg_cfg, "he_orphan_linking", "auto") != "off"
                # (B) shared embeddings (resolver + orphan linker reuse). PCG guard
                # — orphan linker is PCG-only, so skip embed on other templates
                # when resolution is also off.
                precomputed_vecs, name_to_vec = (None, {})
                if _resolution_on or (
                    _orphan_on and is_project_concept_graph(template_path)
                ):
                    precomputed_vecs, name_to_vec = (
                        await self._shared_entity_embeddings(result)
                    )
                # (C) [entity resolution] merge synonymous entities (opt-in).
                # entity_chunks (chunk→entity provenance) is remapped so the
                # canonical inherits all merged entities' chunk references.
                if _resolution_on:
                    try:
                        result, merge_map = await self._extractor.resolve_entities(
                            result, embeddings=precomputed_vecs,
                        )
                        if merge_map:
                            ec = dataset_ka.entity_chunks
                            for merged, canon in merge_map.items():
                                if merged in ec:
                                    ec.setdefault(canon, []).extend(ec.pop(merged))
                    except Exception as exc:  # noqa: BLE001 — best-effort
                        logger.warning(
                            "entity resolution failed, using unmerged result: %s",
                            str(exc)[:160],
                        )
                # (D) v1.9.9 heuristic orphan linking (evidence-gated, no LLM).
                if _orphan_on and name_to_vec:
                    try:
                        from arrow_lake.knowledge_graph.orphan_linker import link_orphans
                        result, _new_links = link_orphans(
                            result, dataset_ka.entity_chunks, name_to_vec,
                            template_path=template_path,
                            threshold=getattr(_hg_cfg, "he_orphan_threshold", 0.75),
                            max_partners=getattr(_hg_cfg, "he_orphan_max_partners", 3),
                            max_links=getattr(_hg_cfg, "he_orphan_max_links", 500),
                        )
                    except Exception as exc:  # noqa: BLE001 — best-effort
                        logger.warning("orphan linking failed: %s", str(exc)[:160])
                # (E) insert.
                await self._insert_kg(
                    result,
                    graph_name,
                    chunk_id_map,
                    entity_chunks=dataset_ka.entity_chunks,
                    write_sem=write_sem,
                )
        elif _gran == "map_reduce":
            # --- v1.9.8 map-reduce: concurrent per-chunk extract (NO insert) →
            # global exact-name merge → entity resolution → single insert.
            # Decouples extraction (concurrent, fast) from merging (global,
            # high-quality), bypassing the dataset path's serial feed_text AND
            # the per-chunk path's no-merge fragmentation (orphan/碎片).
            _sample = next((c for c in contents if c), "")
            template_path = self._extractor._resolve_template(
                dataset_doc_type, _sample, None,
            )

            async def _extract_only(
                idx: int, cid: str, content: str, doc_type: str | None = None,
            ) -> tuple[str, ExtractionResult]:
                nonlocal extraction_failures
                async with semaphore:
                    try:
                        # Bound each chunk's extract: hyper-extract's LLM call has
                        # no timeout, so a single hung request (dropped bailian
                        # connection) would otherwise block the final gather
                        # forever — proc stalls at N-1/N with idle CPU. On timeout
                        # the chunk is counted as a failure and the build moves on
                        # (the underlying to_thread becomes a harmless zombie).
                        res = await asyncio.wait_for(
                            self._extractor.extract(
                                content, chunk_id=cid, doc_type=doc_type,
                            ),
                            timeout=_MAP_EXTRACT_TIMEOUT_S,
                        )
                    except asyncio.TimeoutError:
                        extraction_failures += 1
                        logger.warning(
                            "KG map_reduce chunk %s extract timed out (>%ss), skipped",
                            cid, _MAP_EXTRACT_TIMEOUT_S,
                        )
                        res = ExtractionResult(
                            entities=(), relations=(), raw_text=content
                        )
                    task.processed_chunks = idx + 1
                if not res.entities and not res.relations and len(content.strip()) > 50:
                    logger.warning(
                        "KG map_reduce chunk %s yielded no entities (content_len=%d)",
                        cid, len(content),
                    )
                return (cid, res)

            # MAP + SHUFFLE/REDUCE + per-cid checkpoint (v1.10.2 M3: incremental).
            # Checkpoint is per-cid {hash, entities, relations} so an append can
            # reuse previously-extracted chunks and MAP only new/changed ones. A
            # granular per-cid content-hash check is correct for append, re-ingest,
            # AND content edits — simpler + more robust than a coarse first/last
            # signature (and re-ingest/edit is logged per G5 instead of forced to a
            # wasteful full rebuild). fed_chunks lives under /ka/ next to the
            # checkpoint, shared with the dataset path (P2.4).
            import hashlib
            import json
            from arrow_lake.knowledge_graph._naming import artifact_key_for
            _ka_subdir = (
                (_ka_root or Path(self._config.he_ka_base_dir))
                / artifact_key_for(task.dataset_name) / "ka"
            )
            ckpt_path = _ka_subdir / "map_reduce.json"
            fed_path = _ka_subdir / "fed_chunks.json"

            def _chunk_hash(t: str) -> str:
                return hashlib.sha1((t or "").encode("utf-8", "replace")).hexdigest()[:16]

            def _template_hash(p: str | Path) -> str:
                try:
                    return hashlib.sha1(Path(p).read_bytes()).hexdigest()[:16]
                except OSError:
                    return ""  # unreadable template ⇒ no incremental (safe fallback)

            def _res_to_dict(res: ExtractionResult) -> dict:
                return {
                    "entities": [{"name": e.name, "type": e.entity_type,
                                  "properties": [list(p) for p in e.properties]}
                                 for e in res.entities],
                    "relations": [{"source": r.source, "target": r.target,
                                   "type": r.relation_type,
                                   "properties": [list(p) for p in r.properties]}
                                  for r in res.relations],
                }

            def _dict_to_res(d: dict) -> ExtractionResult:
                return ExtractionResult(
                    entities=tuple(ExtractedEntity(
                        name=e["name"], entity_type=e["type"],
                        properties=tuple(tuple(p) for p in e.get("properties", [])),
                    ) for e in d.get("entities", [])),
                    relations=tuple(ExtractedRelation(
                        source=r["source"], target=r["target"], relation_type=r["type"],
                        properties=tuple(tuple(p) for p in r.get("properties", [])),
                    ) for r in d.get("relations", [])),
                    raw_text="",
                )

            # --- admission: load per-cid checkpoint, gate on template hash (P2.3) ---
            per_cid: dict[str, ExtractionResult] = {}
            ckpt_hashes: dict[str, str] = {}
            can_incremental = False
            cur_thash = _template_hash(template_path)
            if incremental and ckpt_path.is_file() and cur_thash:
                try:
                    d = json.loads(ckpt_path.read_text(encoding="utf-8"))
                    if (d.get("template") == Path(template_path).stem
                            and d.get("template_hash", "") == cur_thash):
                        for cid, cd in d.get("chunks", {}).items():
                            per_cid[cid] = _dict_to_res(cd)
                            ckpt_hashes[cid] = cd.get("hash", "")
                        can_incremental = True
                except Exception as exc:  # noqa: BLE001 — fall back to full MAP
                    logger.warning(
                        "KG map_reduce checkpoint load failed, full MAP: %s", str(exc)[:120],
                    )
                    per_cid, ckpt_hashes = {}, {}

            # --- decide which chunks to (re-)extract (P2.1 MAP diff) ---
            cur_hashes = {cid: _chunk_hash(contents[i]) for i, cid in enumerate(chunk_ids)}
            new_idxs: list[int] = []
            reingest_warned = False
            for i, cid in enumerate(chunk_ids):
                h = cur_hashes[cid]
                if can_incremental and cid in ckpt_hashes:
                    if ckpt_hashes[cid] == h:
                        continue  # reuse prior extraction (content unchanged)
                    # previously-fed cid now differs → re-ingest / content edit (G5)
                    if not reingest_warned:
                        logger.warning(
                            "KG map_reduce %s: chunk content changed since last build "
                            "(re-ingest/edit) — re-extracting affected chunks",
                            task.dataset_name,
                        )
                        reingest_warned = True
                new_idxs.append(i)

            if can_incremental:
                logger.info(
                    "KG map_reduce %s incremental: %d chunks, MAP %d new/changed (reuse %d)",
                    task.dataset_name, len(chunk_ids), len(new_idxs),
                    len(chunk_ids) - len(new_idxs),
                )

            # --- MAP the new/changed chunks (concurrent, bounded by semaphore) ---
            for batch_start in range(0, len(new_idxs), concurrency):
                batch_end = min(batch_start + concurrency, len(new_idxs))
                batch = await asyncio.gather(*(
                    _extract_only(idx, chunk_ids[idx], contents[idx], chunk_doc_types[idx])
                    for idx in new_idxs[batch_start:batch_end]
                ))
                for cid, _res in batch:
                    per_cid[cid] = _res
                if batch_delay > 0 and batch_end < len(new_idxs):
                    await asyncio.sleep(batch_delay)
            task.processed_chunks = len(chunk_ids)

            # --- SHUFFLE/REDUCE: fold all present chunks (reused + new) in order ---
            acc = KGBuilder._MergeAccumulator()
            for cid in chunk_ids:
                _res = per_cid.get(cid)
                if _res is not None:
                    acc.fold(cid, _res)
            result, entity_chunks = acc.finalize()

            # --- persist per-cid checkpoint + fed_chunks (best-effort) ---
            try:
                ckpt_path.parent.mkdir(parents=True, exist_ok=True)
                from arrow_lake.knowledge_graph._atomic import atomic_write_json
                atomic_write_json(ckpt_path, {
                    "template": Path(template_path).stem,
                    "template_hash": cur_thash,
                    "chunks": {
                        cid: {"hash": cur_hashes[cid], **_res_to_dict(per_cid[cid])}
                        for cid in chunk_ids if cid in per_cid
                    },
                })
                # fed_chunks shared with the dataset path (switching granularity
                # keeps progress); only current cids retained (stale cleanup P2.4).
                atomic_write_json(fed_path, {cid: cur_hashes[cid] for cid in chunk_ids})
            except Exception as exc:  # noqa: BLE001 — checkpoint is best-effort
                logger.warning(
                    "KG map_reduce checkpoint write failed: %s", str(exc)[:120],
                )

            total_entities = len(result.entities)
            total_relations = len(result.relations)

            if not result.entities and not result.relations:
                logger.warning(
                    "KG map_reduce yielded no entities for %s (chunks=%d)",
                    task.dataset_name, len(chunk_ids),
                )
            else:
                # (A) v1.9.9 soft-degrade: illegal type-pair relations are
                # downgraded to 相关 (→ related_to) instead of dropped, so their
                # endpoints stay connected rather than going orphan.
                if getattr(self._config, "he_kg_type_pair", True):
                    result = filter_relations_by_type_pair(
                        result, template_path, on_illegal="degrade",
                    )
                    total_relations = len(result.relations)

                _hg_cfg = getattr(self._config, "hugegraph", None) or self._config
                _resolution_on = getattr(_hg_cfg, "he_entity_resolution", "off") != "off"
                _orphan_on = getattr(_hg_cfg, "he_orphan_linking", "auto") != "off"

                # (B) compute embeddings ONCE — shared by resolver + orphan linker.
                # Skip when neither will use them: orphan linker is PCG-only, so a
                # non-PCG template with resolution off needs no embed pass.
                precomputed_vecs, name_to_vec = (None, {})
                _need_embed = _resolution_on or (
                    _orphan_on and is_project_concept_graph(template_path)
                )
                if _need_embed:
                    precomputed_vecs, name_to_vec = (
                        await self._shared_entity_embeddings(result)
                    )

                # (C) entity resolution (synonym merge) — opt-in, reuses embeddings.
                if _resolution_on:
                    try:
                        result, merge_map = await self._extractor.resolve_entities(
                            result, embeddings=precomputed_vecs,
                        )
                        total_entities = len(result.entities)
                        total_relations = len(result.relations)
                        if merge_map:
                            for merged, canon in merge_map.items():
                                if merged in entity_chunks:
                                    entity_chunks.setdefault(canon, []).extend(
                                        entity_chunks.pop(merged)
                                    )
                    except Exception as exc:  # noqa: BLE001 — best-effort
                        logger.warning(
                            "entity resolution failed, using unmerged result: %s",
                            str(exc)[:160],
                        )

                # (D) v1.9.9 heuristic orphan linking — evidence-gated, no LLM.
                # Links remaining orphan entities to co-occurring connected ones
                # (same chunk + embedding ≥ threshold + legal type-pair verb).
                if _orphan_on and name_to_vec:
                    try:
                        from arrow_lake.knowledge_graph.orphan_linker import link_orphans
                        result, new_links = link_orphans(
                            result, entity_chunks, name_to_vec,
                            template_path=template_path,
                            threshold=getattr(_hg_cfg, "he_orphan_threshold", 0.75),
                            max_partners=getattr(_hg_cfg, "he_orphan_max_partners", 3),
                            max_links=getattr(_hg_cfg, "he_orphan_max_links", 500),
                        )
                        if new_links:
                            total_relations = len(result.relations)
                    except Exception as exc:  # noqa: BLE001 — best-effort
                        logger.warning("orphan linking failed: %s", str(exc)[:160])

                # (E) insert.
                await self._insert_kg(
                    result,
                    graph_name,
                    chunk_id_map,
                    entity_chunks=entity_chunks,
                    write_sem=write_sem,
                )
                # v1.10.2 M3: the per-cid checkpoint PERSISTS after a successful
                # insert — it is the incremental-reuse source for the next build.
                # (Previously deleted to avoid stale crash-recovery resume; now the
                # G6 gate means a default incremental=False build ignores it, and an
                # incremental build reuses matching cids / re-MAPs changed ones.)
        else:
            # --- per-chunk: fresh KA.parse() per chunk (legacy path) ---
            async def _process_chunk(
                idx: int, cid: str, content: str, doc_type: str | None = None,
            ) -> tuple[int, int]:
                nonlocal total_entities, total_relations, extraction_failures
                async with semaphore:
                    result = await self._extractor.extract(content, chunk_id=cid, doc_type=doc_type)
                    task.processed_chunks = idx + 1

                ent_count = len(result.entities)
                rel_count = len(result.relations)
                total_entities += ent_count
                total_relations += rel_count

                if not result.entities and not result.relations:
                    # H1: an empty result on non-trivial text usually means the
                    # extractor/LLM failed silently.
                    if len(content.strip()) > 50:
                        extraction_failures += 1
                        logger.warning(
                            "KG chunk %s yielded no entities (content_len=%d) — "
                            "possible extractor/LLM failure", cid, len(content)
                        )
                    return ent_count, rel_count

                await self._insert_kg(
                    result,
                    graph_name,
                    chunk_id_map,
                    owning_chunk_id=cid,
                    write_sem=write_sem,
                )
                return ent_count, rel_count

            for batch_start in range(0, len(chunk_ids), concurrency):
                batch_end = min(batch_start + concurrency, len(chunk_ids))
                await asyncio.gather(*(
                    _process_chunk(idx, chunk_ids[idx], contents[idx], chunk_doc_types[idx])
                    for idx in range(batch_start, batch_end)
                ))
                if batch_delay > 0 and batch_end < len(chunk_ids):
                    await asyncio.sleep(batch_delay)

        task.entity_count = total_entities
        task.relation_count = total_relations
        task.extraction_failures = extraction_failures

    async def _batch_add_vertices(
        self, vertices: list[dict[str, Any]], *, graph_name: str,
        write_sem: asyncio.Semaphore, batch_size: int,
        update_strategies: dict[str, str] | None = None,
    ) -> list[str]:
        """Insert vertices in batches (HugeGraph caps POSTs at 2500 vertices).

        Single-shot ``add_vertices`` fails on a large merged entity set
        (map_reduce inserts ALL entities at once). Returns the concatenated
        HugeGraph ids in input order so callers can zip them back to vertices.

        ``update_strategies`` (v1.10.2 M4 P-辅.1): forwarded to ``add_vertices``
        (PUT batch w/ per-property merge) — used for ``{"source_chunk": "UNION"}``
        so an incremental rebuild EXTENDS rather than overwrites the entity
        provenance SET (G8: old chunks' provenance survives).
        """
        ids: list[str] = []
        if not vertices:
            return ids
        bs = max(1, min(batch_size, 2500))
        async with write_sem:
            for i in range(0, len(vertices), bs):
                ids.extend(
                    await self._client.add_vertices(
                        vertices[i:i + bs], graph_name=graph_name,
                        update_strategies=update_strategies,
                    )
                )
        return ids

    async def _batch_add_edges(
        self, edges: list[dict[str, Any]], *, graph_name: str,
        write_sem: asyncio.Semaphore, batch_size: int,
    ) -> None:
        """Insert edges in batches (mirrors _batch_add_vertices)."""
        if not edges:
            return
        bs = max(1, min(batch_size, 2500))
        async with write_sem:
            for i in range(0, len(edges), bs):
                await self._client.add_edges(edges[i:i + bs], graph_name=graph_name)

    async def _insert_kg(
        self,
        result: ExtractionResult,
        graph_name: str,
        chunk_id_map: dict[str, str],
        *,
        entity_chunks: dict[str, list[str]] | None = None,
        owning_chunk_id: str | None = None,
        write_sem: asyncio.Semaphore | None = None,
    ) -> None:
        """Insert one ExtractionResult's entities + relations into HugeGraph.

        Shared by the per-chunk path (``owning_chunk_id``) and the per-dataset
        path (``entity_chunks`` = name → [chunk_id] provenance). Builds entity +
        typed vertices, ``references(chunk→entity)`` edges, and routed relation
        edges (degrading to ``related_to`` when a typed endpoint is missing).

        Args:
            result: extracted entities/relations to insert.
            graph_name: target per-dataset graph (``kg_{dataset}``).
            chunk_id_map: logical chunk id → HugeGraph vertex id.
            entity_chunks: dataset-level provenance (name → [chunk_id]); each
                entity gets one ``references`` edge per owning chunk.
            owning_chunk_id: per-chunk owner (single ``references`` edge each).
            write_sem: write-side gate; defaults to a fresh semaphore.
        """
        if write_sem is None:
            write_sem = asyncio.Semaphore(self._config.write_concurrency)

        # --- Entity double-write (v1.7.1 §4.5): a generic `entity` vertex is
        # always written (keeps references/related_to edges intact across
        # the schema's single-type edge endpoints); a typed vertex
        # (person/organization/location/concept/event) is added too when
        # route_entity_type recognizes the type, so typed edges can use it.---
        # v1.9.10: owner map name→[chunk_id] computed once, feeds both the
        # entity `source_chunk` provenance property and the references edges.
        # Shallow-copy entity_chunks (it is also serialized into the KA dump)
        # so read-only use here can never mutate the caller's dict.
        if entity_chunks is not None:
            name2chunks: dict[str, list[str]] = dict(entity_chunks)
        elif owning_chunk_id is not None:
            name2chunks = {e.name: [owning_chunk_id] for e in result.entities}
        else:
            name2chunks = {}
        # SET-cardinality `source_chunk` is omitted when empty: HugeGraph does
        # not guarantee accepting an empty list for a SET property key, and
        # nullability applies only when the key is absent (not when sent as []).
        entity_vertices: list[dict[str, Any]] = []
        for e in result.entities:
            props: dict[str, Any] = {
                "name": e.name,
                "type": e.entity_type,
                "definition": dict(e.properties).get("definition", ""),
            }
            chunks = list(dict.fromkeys(name2chunks.get(e.name, [])))
            if chunks:
                props["source_chunk"] = chunks
            entity_vertices.append({"label": "entity", "properties": props})
        entity_id_map: dict[str, str] = {}
        if entity_vertices:
            entity_hg_ids = await self._batch_add_vertices(
                entity_vertices, graph_name=graph_name, write_sem=write_sem,
                batch_size=self._config.build_batch_size,
                # v1.10.2 M4 P-辅.1: UNION source_chunk so an incremental rebuild
                # accumulates provenance (old + new owning chunks) instead of
                # overwriting the SET — old chunks' references survive (G8).
                update_strategies={"source_chunk": "UNION"},
            )
            if len(entity_hg_ids) != len(entity_vertices):
                logger.warning(
                    "entity add_vertices returned %d ids for %d vertices — "
                    "some edges may resolve to stale/missing ids",
                    len(entity_hg_ids), len(entity_vertices),
                )
            # name-only map for edge resolution (last wins for duplicates).
            # Keys are normalized so a relation whose source/target differs only
            # by case/whitespace from the entity name still resolves — otherwise
            # the relation is dropped at the `continue` below and the endpoint
            # vertex becomes an orphan in the entity-only subgraph.
            for e, hg_id in zip(result.entities, entity_hg_ids, strict=False):
                entity_id_map[normalize_name(e.name)] = hg_id

        typed_vertices: list[dict[str, Any]] = []
        typed_keys: list[tuple[str, str]] = []
        for e in result.entities:
            label = route_entity_type(e.entity_type)
            if label is None:
                continue
            props: dict[str, Any] = {"name": e.name}
            if label == "event" and e.properties:
                date_val = dict(e.properties).get("date")
                if date_val is not None:
                    props["date"] = str(date_val)
            typed_vertices.append({"label": label, "properties": props})
            typed_keys.append((normalize_name(e.name), label))
        typed_id_map: dict[tuple[str, str], str] = {}
        if typed_vertices:
            typed_hg_ids = await self._batch_add_vertices(
                typed_vertices, graph_name=graph_name, write_sem=write_sem,
                batch_size=self._config.build_batch_size,
            )
            if len(typed_hg_ids) != len(typed_vertices):
                logger.warning(
                    "typed add_vertices returned %d ids for %d vertices — "
                    "some typed edges will degrade to related_to",
                    len(typed_hg_ids), len(typed_vertices),
                )
            for key, hg_id in zip(typed_keys, typed_hg_ids, strict=False):
                typed_id_map[key] = hg_id

        entity_type_map = {normalize_name(e.name): e.entity_type for e in result.entities}

        def _vertex_id(name: str, label: str) -> str | None:
            n = normalize_name(name)
            if label == "entity":
                return entity_id_map.get(n)
            return typed_id_map.get((n, label))

        # --- references(chunk→entity) edges ---
        # per-dataset: expand entity_chunks[name] → one edge per owning chunk;
        # per-chunk:   single owning chunk. owner map (`name2chunks`) computed
        # once above (also feeds entity provenance `source_chunk`).
        ref_edges: list[dict[str, Any]] = []
        for e in result.entities:
            if normalize_name(e.name) not in entity_id_map:
                continue
            for cid in name2chunks.get(e.name, []):
                if cid in chunk_id_map:
                    ref_edges.append({
                        "label": "references",
                        "outV": chunk_id_map[cid],
                        "outVLabel": "chunk",
                        "inV": entity_id_map[normalize_name(e.name)],
                        "inVLabel": "entity",
                        "properties": {},
                    })
        if ref_edges:
            await self._batch_add_edges(
                ref_edges, graph_name=graph_name, write_sem=write_sem,
                batch_size=self._config.build_batch_size,
            )

        # --- Relation routing (v1.7.1 §4.5): route_relation picks a typed
        # edge label on a synonym hit (endpoints resolved via _EDGE_ENDPOINTS
        # to the matching typed/generic vertices); otherwise falls back to
        # related_to on the generic entity vertices. relation_type is always
        # preserved as an edge property (fixes the prior discard bug). ---
        rel_edges: list[dict[str, Any]] = []
        for r in result.relations:
            src_n = normalize_name(r.source)
            tgt_n = normalize_name(r.target)
            if src_n not in entity_id_map or tgt_n not in entity_id_map:
                continue
            src_type = entity_type_map.get(src_n, "")
            tgt_type = entity_type_map.get(tgt_n, "")
            edge_label = route_relation(src_type, tgt_type, r.relation_type)
            src_label, tgt_label = _EDGE_ENDPOINTS[edge_label]
            src_id = _vertex_id(r.source, src_label)
            tgt_id = _vertex_id(r.target, tgt_label)
            if src_id is None or tgt_id is None:
                # Missing typed endpoint — degrade to related_to on generic vertices.
                edge_label = "related_to"
                src_label, tgt_label = "entity", "entity"
                src_id = entity_id_map[src_n]
                tgt_id = entity_id_map[tgt_n]
            props = {
                # relation_type is now a sort_key (v1.10.2 M2: relation edges are
                # frequency=MULTIPLE + sort_keys=[relation_type]). HugeGraph
                # rejects a null sort_key value, so coerce empty/None to the
                # routed label name — deterministic, non-empty, groups the pair.
                "relation_type": r.relation_type or edge_label,
                "description": dict(r.properties).get("description", ""),
            }
            if edge_label == "related_to":
                props["weight"] = (
                    dict(r.properties).get("weight", 1.0) if r.properties else 1.0
                )
            rel_edges.append({
                "label": edge_label,
                "outV": src_id,
                "outVLabel": src_label,
                "inV": tgt_id,
                "inVLabel": tgt_label,
                "properties": props,
            })
        if rel_edges:
            await self._batch_add_edges(
                rel_edges, graph_name=graph_name, write_sem=write_sem,
                batch_size=self._config.build_batch_size,
            )

    async def _shared_entity_embeddings(
        self, result: ExtractionResult,
    ) -> tuple[list[list[float]] | None, dict[str, list[float]]]:
        """Compute entity embeddings ONCE for reuse by resolver + orphan linker.

        Returns ``(vectors_aligned_to_result_entities, name_to_vec)``. Both are
        ``None`` / empty on any failure so callers fall back gracefully
        (resolver re-embeds internally; orphan linker no-ops). This is an O(n)
        embed pass — NOT the O(n²) cosine matrix in entity_resolver — so it is
        capped at 50k entities, independent of the resolver's 10k O(n²) cap.
        """
        embed_fn = getattr(getattr(self._extractor, "_embedder", None), "embed_documents", None)
        n = len(result.entities)
        if embed_fn is None or not (2 <= n <= 50000):
            return None, {}
        try:
            from arrow_lake.knowledge_graph.entity_resolver import _entity_text
            texts = [_entity_text(e) for e in result.entities]
            vecs = await asyncio.wait_for(
                asyncio.to_thread(embed_fn, texts), timeout=600,
            )
            name_to_vec = {
                normalize_name(e.name): v
                for e, v in zip(result.entities, vecs, strict=False)
            }
        except Exception as exc:  # noqa: BLE001 — best-effort
            logger.warning("shared entity embed failed: %s", str(exc)[:120])
            return None, {}
        return vecs, name_to_vec

    async def _infer_doc_type(self, contents: list[str]) -> str | None:
        """Infer doc_type ONCE from aggregated document content.

        Delegates to the extractor's classifier when present (he backend); returns
        ``None`` for the legacy backend (no classifier), leaving doc_type unset so
        the extractor uses its default template.
        """
        classifier = getattr(self._extractor, "_classifier", None)
        if classifier is None:
            return None
        aggregated = " ".join(c for c in contents if c)[:1500]
        if not aggregated.strip():
            return None
        return await classifier.classify(aggregated)

    @staticmethod
    def _normalize_table(table: pa.Table, dataset_name: str) -> pa.Table:
        """Ensure the table has the columns required by the build pipeline."""
        if "content" not in table.column_names:
            text_col = (
                "text_content"
                if "text_content" in table.column_names
                else table.column_names[0]
            )
            new_names = ["content" if c == text_col else c for c in table.column_names]
            table = table.rename_columns(new_names)
        if "id" not in table.column_names:
            table = table.add_column(
                0, "id", pa.array([str(i) for i in range(table.num_rows)])
            )
        if "document_name" not in table.column_names:
            table = table.append_column(
                "document_name", pa.array([dataset_name] * table.num_rows)
            )
        if "chunk_index" not in table.column_names:
            table = table.append_column(
                "chunk_index", pa.array(list(range(table.num_rows)))
            )
        return table

    @staticmethod
    def _unique_column_values(table: pa.Table, column: str) -> list[str]:
        """Return unique string values from a table column."""
        col = table.column(column)
        return list(dict.fromkeys(col.to_pylist()))

    class _MergeAccumulator:
        """Streaming merge state for the map_reduce path: fold per chunk,
        finalize once. Folding entities incrementally bounds peak memory to the
        unique-entity set + raw relations (not the whole ``chunk_results`` list),
        so the map_reduce branch can merge-as-it-extracts (#5). Relations are
        stashed raw and remapped at finalize (they need the COMPLETE canonical
        map). The finalized output is pickled to disk after MAP so a post-MAP
        failure (resolve/insert) doesn't lose the extraction work (#3).
        """

        def __init__(self) -> None:
            self.norm_to_canon: dict[str, str] = {}
            self.canon_entities: dict[str, Any] = {}   # display name -> first entity
            self.canon_def: dict[str, str] = {}        # display name -> longest def
            self.canon_type: dict[str, str] = {}       # display name -> first non-empty type
            self.entity_chunks: dict[str, list[str]] = {}
            self._raw_relations: list[Any] = []

        def fold(self, cid: str, res: ExtractionResult) -> None:
            for e in res.entities:
                n = normalize_name(e.name)
                if n not in self.norm_to_canon:
                    self.norm_to_canon[n] = e.name
                    self.canon_entities[e.name] = e
                canon = self.norm_to_canon[n]
                d = ""
                if e.properties:
                    d = str(dict(e.properties).get("definition", "") or "")
                if d and len(d) > len(self.canon_def.get(canon, "")):
                    self.canon_def[canon] = d
                et = e.entity_type or ""
                if et and not self.canon_type.get(canon):
                    self.canon_type[canon] = et
                self.entity_chunks.setdefault(canon, []).append(cid)
            if res.relations:
                self._raw_relations.extend(res.relations)

        def finalize(self) -> tuple[ExtractionResult, dict[str, list[str]]]:
            final_entities = []
            for name, e in self.canon_entities.items():
                d = self.canon_def.get(name, "")
                et = self.canon_type.get(name, "") or e.entity_type
                props = (("definition", d),) if d else (() if not e.properties else e.properties)
                final_entities.append(replace(e, entity_type=et, properties=props))
            seen_rel: set[tuple[str, str, str]] = set()
            final_relations = []
            for r in self._raw_relations:
                src = self.norm_to_canon.get(normalize_name(r.source), r.source)
                tgt = self.norm_to_canon.get(normalize_name(r.target), r.target)
                if normalize_name(src) == normalize_name(tgt):
                    continue
                key = (normalize_name(src), r.relation_type, normalize_name(tgt))
                if key in seen_rel:
                    continue
                seen_rel.add(key)
                final_relations.append(
                    r if (src == r.source and tgt == r.target)
                    else replace(r, source=src, target=tgt)
                )
            return ExtractionResult(
                entities=tuple(final_entities),
                relations=tuple(final_relations),
                raw_text="",
            ), self.entity_chunks

    @staticmethod
    def _merge_chunk_results(
        chunk_results: list[tuple[str, ExtractionResult]],
    ) -> tuple[ExtractionResult, dict[str, list[str]]]:
        """MAP→SHUFFLE/REDUCE merge of per-chunk ExtractionResults (map_reduce).

        Thin wrapper over :class:`_MergeAccumulator` (fold-all-then-finalize).
        The map_reduce branch folds incrementally for bounded memory + checkpoints
        the finalized result; this wrapper preserves the tested batch API.

        - Entities exact-name-deduped by :func:`normalize_name` (first surface
          form canonical; longest definition wins; first non-empty type wins).
        - ``entity_chunks[canonical] = [chunk_id...]`` provenance union.
        - Relations remapped to canonical, self-loops dropped, deduped by
          ``(norm_src, type, norm_tgt)``.
        """
        acc = KGBuilder._MergeAccumulator()
        for cid, res in chunk_results:
            acc.fold(cid, res)
        return acc.finalize()

    @staticmethod
    def _build_next_chunk_edges_hg(
        chunk_id_map: dict[str, str],
        doc_names: list[str],
        chunk_indices: list[int],
    ) -> list[dict[str, Any]]:
        """Build next_chunk edges using actual HugeGraph vertex IDs."""
        edges: list[dict[str, Any]] = []
        doc_chunks: dict[str, list[tuple[int, str]]] = {}
        for logical_id, doc, idx in zip(chunk_id_map, doc_names, chunk_indices, strict=True):
            hg_id = chunk_id_map[logical_id]
            doc_chunks.setdefault(doc, []).append((idx, hg_id))

        for _doc, chunks in doc_chunks.items():
            sorted_chunks = sorted(chunks, key=lambda x: x[0])
            for i in range(len(sorted_chunks) - 1):
                _, curr_hg_id = sorted_chunks[i]
                _, next_hg_id = sorted_chunks[i + 1]
                edges.append({
                    "label": "next_chunk",
                    "outV": curr_hg_id,
                    "outVLabel": "chunk",
                    "inV": next_hg_id,
                    "inVLabel": "chunk",
                    "properties": {},
                })

        return edges

    async def _build_boundary_next_edges(
        self,
        chunk_ids: list[str],
        doc_name_col: list[str],
        chunk_indices: list[int],
        new_cid_set: set[str],
        chunk_id_map: dict[str, str],
        graph_name: str,
    ) -> list[dict[str, Any]]:
        """Boundary next_chunk edges for incremental append (v1.10.2 M4 P4).

        For each document spanning OLD + NEW chunks, connect the doc's prior
        last chunk → its new first chunk, so the next_chunk chain isn't broken
        across the append boundary. Append-only ⇒ new chunks are a contiguous
        suffix, so new-first = min-index new chunk, old-last = max-index OLD
        chunk of that doc. The old-last hg id is queried (it was added in a
        prior build, not in this build's chunk_id_map). Best-effort: a missing
        old vertex is warned + skipped (chain reconnects on the next full build).
        """
        doc_new_first: dict[str, tuple[int, str]] = {}   # doc -> (idx, cid)
        doc_old_last: dict[str, tuple[int, str]] = {}    # doc -> (idx, cid)
        for i, cid in enumerate(chunk_ids):
            doc = doc_name_col[i]
            if cid in new_cid_set:
                cur = doc_new_first.get(doc)
                if cur is None or chunk_indices[i] < cur[0]:
                    doc_new_first[doc] = (chunk_indices[i], cid)
            else:
                cur = doc_old_last.get(doc)
                if cur is None or chunk_indices[i] > cur[0]:
                    doc_old_last[doc] = (chunk_indices[i], cid)

        edges: list[dict[str, Any]] = []
        for doc, (_nidx, ncid) in doc_new_first.items():
            old = doc_old_last.get(doc)
            if old is None:
                continue  # doc entirely new this build — no boundary
            try:
                verts = await self._client.find_vertices_by_property(
                    "chunk", {"id": old[1]}, graph_name=graph_name, limit=5,
                )
            except Exception as exc:  # noqa: BLE001 — best-effort
                logger.warning("boundary next_chunk query failed for %s: %s", old[1], str(exc)[:120])
                continue
            if not verts:
                logger.warning("boundary next_chunk: old chunk %s not found (skip)", old[1])
                continue
            edges.append({
                "label": "next_chunk",
                "outV": verts[0].get("id"), "outVLabel": "chunk",
                "inV": chunk_id_map[ncid], "inVLabel": "chunk",
                "properties": {},
            })
        return edges
