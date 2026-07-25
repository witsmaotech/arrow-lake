"""Knowledge graph builder -- orchestrates schema creation, vertex/edge
insertion, and entity extraction from text chunks.

Two extraction granularities (``HugeGraphConfig.he_kg_granularity``):

- ``"chunk"``   -- per-chunk: each chunk extracted independently (fresh KA per
                  chunk) and inserted. Legacy path; ``KGBuilder._process_chunk``.
- ``"dataset"`` -- per-dataset: ONE hyper-extract KA fed all chunks via
                  ``feed_text`` (LLM.BALANCED cross-chunk merge), then inserted
                  as a whole + dumped to ``<ka_base_dir>/<dataset>/ka/``.
                  ``KGBuilder._execute_build`` dataset branch → ``_insert_kg``.

Both paths share ``_insert_kg`` for vertex/edge insertion (entity + typed
vertices, references, routed relations).
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

import pyarrow as pa

from arrow_lake.config import HugeGraphConfig
from arrow_lake.knowledge_graph.client import HugeGraphClient
from arrow_lake.knowledge_graph.entity_router import route_entity_type, route_relation
from arrow_lake.knowledge_graph.extractor import EntityExtractor, ExtractionResult
from arrow_lake.knowledge_graph._naming import graph_name_for
from arrow_lake.knowledge_graph.schema import ARROW_LAKE_KG_SCHEMA, schema_to_hugegraph_payload

logger = logging.getLogger(__name__)

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
        incremental: bool = False,
    ) -> str:
        """Prepare a KG build task and return its ID immediately.

        The actual build runs in the background via :meth:`execute_build`.
        Use :meth:`get_task_status` to track progress.

        ``incremental``: feed only NEW chunks (not in the KA's fed_chunks) into
        the existing KA + upsert their entities/edges (idempotent). Falls back to
        a full rebuild when no dump exists or the template changed.
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
        self._pending_tables[task_id] = (chunks_table, incremental)
        return task_id

    async def execute_build(self, task_id: str) -> None:
        """Execute a previously prepared build task in the background."""
        logger.info("KGDISPATCH execute_build ENTER task=%s has_task=%s builder_id=%s", task_id, task_id in self._tasks, id(self))
        task = self._tasks.get(task_id)
        pending = self._pending_tables.pop(task_id, None)
        if task is None or pending is None:
            logger.info("KGDISPATCH execute_build EARLY_RETURN task_none=%s pending_none=%s", task is None, pending is None)
            return
        table, incremental = pending
        try:
            await self._execute_build(task, table, incremental=incremental)
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

    def get_task_status(self, task_id: str) -> KGBuildTask | None:
        """Return the current status of a build task, or None if unknown."""
        return self._tasks.get(task_id)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    async def _execute_build(
        self, task: KGBuildTask, table: pa.Table, *, incremental: bool = False,
    ) -> None:
        """Run the full build pipeline.

        ``incremental``: feed only new chunks into the existing KA (KG insert is
        idempotent upsert by primary key, so re-inserting the merged result is
        safe). Falls back to a full rebuild inside build_dataset_ka when no dump
        exists or the template changed.
        """
        # v1.8.6: per-dataset graph isolation — every write targets kg_{dataset}.
        graph_name = graph_name_for(task.dataset_name)
        # 1. Ensure schema (also creates graph if needed)
        logger.info("KG build %s: STEP1 ensure_schema START graph=%s", task.task_id, graph_name)
        schema_payload = schema_to_hugegraph_payload(ARROW_LAKE_KG_SCHEMA)
        await self._client.ensure_schema(schema_payload, graph_name=graph_name)
        logger.info("KG build %s: STEP1 ensure_schema DONE", task.task_id)

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

        # 2. Collect unique documents and insert document vertices
        doc_names = self._unique_column_values(table, "document_name")
        doc_vertices = [
            {"label": "document", "properties": {"id": name, "name": name}}
            for name in doc_names
        ]
        logger.info("KG build %s: STEP2 add doc vertices n=%d START", task.task_id, len(doc_vertices))
        doc_hg_ids = await self._client.add_vertices(doc_vertices, graph_name=graph_name)
        logger.info("KG build %s: STEP2 add doc vertices DONE", task.task_id)
        doc_id_map: dict[str, str] = dict(zip(doc_names, doc_hg_ids, strict=True))

        # 3. Insert chunk vertices
        chunk_ids = table.column("id").to_pylist()
        contents = [str(c) if c is not None else "" for c in table.column("content").to_pylist()]
        doc_name_col = table.column("document_name").to_pylist()
        chunk_indices = table.column("chunk_index").to_pylist()
        doc_type_col = (
            table.column("doc_type").to_pylist()
            if "doc_type" in table.column_names
            else [None] * table.num_rows
        )

        chunk_vertices = [
            {
                "label": "chunk",
                "properties": {
                    "id": cid,
                    "content": str(content) if content is not None else "",
                    "chunk_index": idx,
                },
            }
            for cid, content, idx in zip(chunk_ids, contents, chunk_indices, strict=True)
        ]
        all_chunk_hg_ids: list[str] = []
        nbatches = (len(chunk_vertices) + batch_size - 1) // max(batch_size, 1)
        logger.info("KG build %s: STEP3 add chunk vertices n=%d bs=%d batches=%d START", task.task_id, len(chunk_vertices), batch_size, nbatches)
        for i in range(0, len(chunk_vertices), batch_size):
            batch = chunk_vertices[i : i + batch_size]
            hg_ids = await self._client.add_vertices(batch, graph_name=graph_name)
            all_chunk_hg_ids.extend(hg_ids)
        logger.info("KG build %s: STEP3 add chunk vertices DONE", task.task_id)
        chunk_id_map: dict[str, str] = dict(zip(chunk_ids, all_chunk_hg_ids, strict=True))

        # 4. Insert contains_chunk edges (document -> chunk)
        logger.info("KG build %s: STEP4 contains edges START", task.task_id)
        contains_edges: list[dict[str, Any]] = []
        for cid, doc_name in zip(chunk_ids, doc_name_col, strict=True):
            contains_edges.append({
                "label": "contains_chunk",
                "outV": doc_id_map[doc_name],
                "outVLabel": "document",
                "inV": chunk_id_map[cid],
                "inVLabel": "chunk",
                "properties": {},
            })
        for i in range(0, len(contains_edges), batch_size):
            await self._client.add_edges(contains_edges[i : i + batch_size], graph_name=graph_name)

        # 5. Insert next_chunk edges (sequential chunks in same doc)
        logger.info("KG build %s: STEP5 next edges START", task.task_id)
        next_edges = self._build_next_chunk_edges_hg(
            chunk_id_map, doc_name_col, chunk_indices
        )
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

        # v1.9.4: three-tier granularity (auto/dataset/grouped/chunk).
        _gran = getattr(self._extractor, "_kg_granularity", "chunk")
        if _gran == "auto":
            _nchunks = len(chunk_ids)
            if _nchunks <= self._config.he_kg_dataset_max_chunks:
                _gran = "dataset"
            elif _nchunks > self._config.he_kg_chunk_min_chunks:
                _gran = "chunk"
            else:
                _gran = "grouped"
        use_dataset_path = (
            _gran in ("dataset", "grouped")
            and hasattr(self._extractor, "build_dataset_ka")
            and self._ka_base_dir is not None
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
            ka_dir = self._ka_base_dir / artifact_key_for(task.dataset_name) / "ka"
            # [#11] Archive the current dump (if any) before overwrite so a
            # regressive/failed rebuild can be rolled back. Then prune to the
            # configured max versions to bound disk usage.
            try:
                from arrow_lake.knowledge_graph import ka_versioning
                ka_versioning.archive_current(self._ka_base_dir, task.dataset_name)
                _max = getattr(self._config, "he_ka_max_versions", 0) or 0
                if _max > 0:
                    ka_versioning.prune(self._ka_base_dir, task.dataset_name, keep=_max)
            except Exception as exc:  # noqa: BLE001 — versioning is best-effort
                logger.warning("KA versioning archive/prune failed for %s: %s",
                               task.dataset_name, exc)
            _chunk_pairs = list(zip(chunk_ids, contents, strict=True))
            if _gran == "grouped":
                logger.info("KG build %s: STEP7 build_grouped_ka START (grouped, %d chunks, group_size=%d)", task.task_id, len(chunk_ids), self._config.he_kg_group_size)
                dataset_ka = await self._extractor.build_grouped_ka(
                    template_path, _chunk_pairs, ka_dir,
                    group_size=self._config.he_kg_group_size,
                )
            else:
                logger.info("KG build %s: STEP7 build_dataset_ka START (dataset, %d chunks)", task.task_id, len(chunk_ids))
                dataset_ka = await self._extractor.build_dataset_ka(
                    template_path, _chunk_pairs, ka_dir,
                    incremental=incremental,
                )
            logger.info("KG build %s: STEP7 extraction DONE entities=%d", task.task_id, len(dataset_ka.result.entities))
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
                await self._insert_kg(
                    result,
                    graph_name,
                    chunk_id_map,
                    entity_chunks=dataset_ka.entity_chunks,
                    write_sem=write_sem,
                )
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
        entity_vertices = [
            {
                "label": "entity",
                "properties": {
                    "name": e.name,
                    "type": e.entity_type,
                    "definition": dict(e.properties).get("definition", ""),
                },
            }
            for e in result.entities
        ]
        entity_id_map: dict[str, str] = {}
        if entity_vertices:
            async with write_sem:
                entity_hg_ids = await self._client.add_vertices(entity_vertices, graph_name=graph_name)
            if len(entity_hg_ids) != len(entity_vertices):
                logger.warning(
                    "entity add_vertices returned %d ids for %d vertices — "
                    "some edges may resolve to stale/missing ids",
                    len(entity_hg_ids), len(entity_vertices),
                )
            # name-only map for edge resolution (last wins for duplicates)
            for e, hg_id in zip(result.entities, entity_hg_ids, strict=False):
                entity_id_map[e.name] = hg_id

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
            typed_keys.append((e.name, label))
        typed_id_map: dict[tuple[str, str], str] = {}
        if typed_vertices:
            async with write_sem:
                typed_hg_ids = await self._client.add_vertices(typed_vertices, graph_name=graph_name)
            if len(typed_hg_ids) != len(typed_vertices):
                logger.warning(
                    "typed add_vertices returned %d ids for %d vertices — "
                    "some typed edges will degrade to related_to",
                    len(typed_hg_ids), len(typed_vertices),
                )
            for key, hg_id in zip(typed_keys, typed_hg_ids, strict=False):
                typed_id_map[key] = hg_id

        entity_type_map = {e.name: e.entity_type for e in result.entities}

        def _vertex_id(name: str, label: str) -> str | None:
            if label == "entity":
                return entity_id_map.get(name)
            return typed_id_map.get((name, label))

        # --- references(chunk→entity) edges ---
        # per-dataset: expand entity_chunks[name] → one edge per owning chunk;
        # per-chunk:   single owning chunk.
        if entity_chunks is not None:
            owner_lists: dict[str, list[str]] = entity_chunks
        elif owning_chunk_id is not None:
            owner_lists = {e.name: [owning_chunk_id] for e in result.entities}
        else:
            owner_lists = {}
        ref_edges: list[dict[str, Any]] = []
        for e in result.entities:
            if e.name not in entity_id_map:
                continue
            for cid in owner_lists.get(e.name, []):
                if cid in chunk_id_map:
                    ref_edges.append({
                        "label": "references",
                        "outV": chunk_id_map[cid],
                        "outVLabel": "chunk",
                        "inV": entity_id_map[e.name],
                        "inVLabel": "entity",
                        "properties": {},
                    })
        if ref_edges:
            async with write_sem:
                await self._client.add_edges(ref_edges, graph_name=graph_name)

        # --- Relation routing (v1.7.1 §4.5): route_relation picks a typed
        # edge label on a synonym hit (endpoints resolved via _EDGE_ENDPOINTS
        # to the matching typed/generic vertices); otherwise falls back to
        # related_to on the generic entity vertices. relation_type is always
        # preserved as an edge property (fixes the prior discard bug). ---
        rel_edges: list[dict[str, Any]] = []
        for r in result.relations:
            if r.source not in entity_id_map or r.target not in entity_id_map:
                continue
            src_type = entity_type_map.get(r.source, "")
            tgt_type = entity_type_map.get(r.target, "")
            edge_label = route_relation(src_type, tgt_type, r.relation_type)
            src_label, tgt_label = _EDGE_ENDPOINTS[edge_label]
            src_id = _vertex_id(r.source, src_label)
            tgt_id = _vertex_id(r.target, tgt_label)
            if src_id is None or tgt_id is None:
                # Missing typed endpoint — degrade to related_to on generic vertices.
                edge_label = "related_to"
                src_label, tgt_label = "entity", "entity"
                src_id = entity_id_map[r.source]
                tgt_id = entity_id_map[r.target]
            props = {
                "relation_type": r.relation_type,
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
            async with write_sem:
                await self._client.add_edges(rel_edges, graph_name=graph_name)

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
