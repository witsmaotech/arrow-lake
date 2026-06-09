"""Knowledge graph builder -- orchestrates schema creation, vertex/edge
insertion, and entity extraction from text chunks."""

from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

import pyarrow as pa

from arrow_lake.config import HugeGraphConfig
from arrow_lake.knowledge_graph.client import HugeGraphClient
from arrow_lake.knowledge_graph.extractor import EntityExtractor
from arrow_lake.knowledge_graph.schema import ARROW_LAKE_KG_SCHEMA, schema_to_hugegraph_payload

logger = logging.getLogger(__name__)


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


# ---------------------------------------------------------------------------
# Builder
# ---------------------------------------------------------------------------


class KGBuilder:
    """Build a knowledge graph from a pyarrow table of text chunks.

    Workflow:
    1. Ensure graph schema (idempotent).
    2. Insert document + chunk vertices.
    3. Insert ``contains_chunk`` / ``next_chunk`` edges.
    4. Extract entities/relations from each chunk via LLM.
    5. Insert entity vertices and ``references`` / relationship edges.

    Args:
        client: HugeGraph REST client.
        extractor: Entity extractor (LLM-backed).
        config: HugeGraph configuration.
    """

    def __init__(
        self,
        client: HugeGraphClient,
        extractor: EntityExtractor,
        config: HugeGraphConfig,
    ) -> None:
        self._client = client
        self._extractor = extractor
        self._config = config
        self._tasks: dict[str, KGBuildTask] = {}
        self._pending_tables: dict[str, pa.Table] = {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def build(self, dataset_name: str, chunks_table: pa.Table) -> str:
        """Prepare a KG build task and return its ID immediately.

        The actual build runs in the background via :meth:`execute_build`.
        Use :meth:`get_task_status` to track progress.
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
        self._pending_tables[task_id] = chunks_table
        return task_id

    async def execute_build(self, task_id: str) -> None:
        """Execute a previously prepared build task in the background."""
        task = self._tasks.get(task_id)
        table = self._pending_tables.pop(task_id, None)
        if task is None or table is None:
            return
        try:
            await self._execute_build(task, table)
            task.status = KGBuildStatus.COMPLETED
        except (RuntimeError, OSError) as exc:
            task.status = KGBuildStatus.FAILED
            task.error = str(exc)
            logger.error("KG build %s failed: %s", task_id, exc)
        finally:
            task.completed_at = datetime.now(UTC)

    def get_task_status(self, task_id: str) -> KGBuildTask | None:
        """Return the current status of a build task, or None if unknown."""
        return self._tasks.get(task_id)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    async def _execute_build(
        self, task: KGBuildTask, table: pa.Table
    ) -> None:
        """Run the full build pipeline."""
        # 1. Ensure schema (also creates graph if needed)
        schema_payload = schema_to_hugegraph_payload(ARROW_LAKE_KG_SCHEMA)
        await self._client.ensure_schema(schema_payload)

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
        doc_hg_ids = await self._client.add_vertices(doc_vertices)
        doc_id_map: dict[str, str] = dict(zip(doc_names, doc_hg_ids, strict=True))

        # 3. Insert chunk vertices
        chunk_ids = table.column("id").to_pylist()
        contents = [str(c) if c is not None else "" for c in table.column("content").to_pylist()]
        doc_name_col = table.column("document_name").to_pylist()
        chunk_indices = table.column("chunk_index").to_pylist()

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
        for i in range(0, len(chunk_vertices), batch_size):
            batch = chunk_vertices[i : i + batch_size]
            hg_ids = await self._client.add_vertices(batch)
            all_chunk_hg_ids.extend(hg_ids)
        chunk_id_map: dict[str, str] = dict(zip(chunk_ids, all_chunk_hg_ids, strict=True))

        # 4. Insert contains_chunk edges (document -> chunk)
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
            await self._client.add_edges(contains_edges[i : i + batch_size])

        # 5. Insert next_chunk edges (sequential chunks in same doc)
        next_edges = self._build_next_chunk_edges_hg(
            chunk_id_map, doc_name_col, chunk_indices
        )
        if next_edges:
            for i in range(0, len(next_edges), batch_size):
                await self._client.add_edges(next_edges[i : i + batch_size])

        # 7. Extract entities and relations from each chunk (batched)
        total_entities = 0
        total_relations = 0
        concurrency = self._config.build_concurrency
        batch_delay = self._config.build_batch_delay
        semaphore = asyncio.Semaphore(concurrency)

        async def _process_chunk(
            idx: int, cid: str, content: str,
        ) -> tuple[int, int]:
            nonlocal total_entities, total_relations
            async with semaphore:
                result = await self._extractor.extract(content, chunk_id=cid)
                task.processed_chunks = idx + 1

            ent_count = len(result.entities)
            rel_count = len(result.relations)
            total_entities += ent_count
            total_relations += rel_count

            if not result.entities and not result.relations:
                return ent_count, rel_count

            entity_vertices = [
                {
                    "label": "entity",
                    "properties": {"name": e.name, "type": e.entity_type},
                }
                for e in result.entities
            ]
            entity_id_map: dict[str, str] = {}
            if entity_vertices:
                entity_hg_ids = await self._client.add_vertices(entity_vertices)
                # Use (name, type) composite key to avoid losing duplicate-name entities
                entity_keys = [f"{e.name}::{e.entity_type}" for e in result.entities]
                dict(zip(entity_keys, entity_hg_ids, strict=True))
                # Also build name-only map for edge resolution (last wins for duplicates)
                for e, hg_id in zip(result.entities, entity_hg_ids, strict=False):
                    entity_id_map[e.name] = hg_id

            ref_edges = [
                {
                    "label": "references",
                    "outV": chunk_id_map[cid],
                    "outVLabel": "chunk",
                    "inV": entity_id_map[e.name],
                    "inVLabel": "entity",
                    "properties": {},
                }
                for e in result.entities
                if e.name in entity_id_map
            ]
            if ref_edges:
                await self._client.add_edges(ref_edges)

            rel_edges = [
                {
                    "label": "related_to",
                    "outV": entity_id_map[r.source],
                    "outVLabel": "entity",
                    "inV": entity_id_map[r.target],
                    "inVLabel": "entity",
                    "properties": {
                        "weight": dict(r.properties).get("weight", 1.0)
                        if r.properties else 1.0,
                    },
                }
                for r in result.relations
                if r.source in entity_id_map and r.target in entity_id_map
            ]
            if rel_edges:
                await self._client.add_edges(rel_edges)

            return ent_count, rel_count

        all_chunks = list(enumerate(zip(chunk_ids, contents, strict=True)))
        for batch_start in range(0, len(all_chunks), concurrency):
            batch = all_chunks[batch_start : batch_start + concurrency]
            await asyncio.gather(*(
                _process_chunk(idx, cid, content)
                for idx, (cid, content) in batch
            ))
            if batch_delay > 0 and batch_start + concurrency < len(all_chunks):
                await asyncio.sleep(batch_delay)

        task.entity_count = total_entities
        task.relation_count = total_relations

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
