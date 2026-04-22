"""Knowledge graph builder -- orchestrates schema creation, vertex/edge
insertion, and entity extraction from text chunks."""

from __future__ import annotations

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

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def build(self, dataset_name: str, chunks_table: pa.Table) -> str:
        """Build the knowledge graph from a table of chunks.

        Args:
            dataset_name: Human-readable name for this dataset/build.
            chunks_table: PyArrow table with columns: id, content,
                document_name, chunk_index.

        Returns:
            Task ID string for tracking progress.
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

        try:
            await self._execute_build(task, chunks_table)
            task.status = KGBuildStatus.COMPLETED
        except (RuntimeError, OSError) as exc:
            task.status = KGBuildStatus.FAILED
            task.error = str(exc)
            logger.error("KG build %s failed: %s", task_id, exc)
        finally:
            task.completed_at = datetime.now(UTC)

        return task_id

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
        # 1. Ensure schema
        schema_payload = schema_to_hugegraph_payload(ARROW_LAKE_KG_SCHEMA)
        await self._client.ensure_schema(schema_payload)

        if table.num_rows == 0:
            return

        # 2. Collect unique documents
        doc_names = self._unique_column_values(table, "document_name")

        # 3. Insert document vertices
        doc_vertices = [
            {
                "label": "document",
                "properties": {"id": name, "name": name},
            }
            for name in doc_names
        ]
        await self._client.add_vertices(doc_vertices)

        # 4. Insert chunk vertices
        chunk_ids = table.column("id").to_pylist()
        contents = table.column("content").to_pylist()
        doc_name_col = table.column("document_name").to_pylist()
        chunk_indices = table.column("chunk_index").to_pylist()

        chunk_vertices = [
            {
                "label": "chunk",
                "properties": {
                    "id": cid,
                    "content": content,
                    "chunk_index": idx,
                },
            }
            for cid, content, idx in zip(chunk_ids, contents, chunk_indices, strict=True)
        ]
        batch_size = self._config.build_batch_size
        for i in range(0, len(chunk_vertices), batch_size):
            batch = chunk_vertices[i : i + batch_size]
            await self._client.add_vertices(batch)

        # 5. Insert contains_chunk edges
        contains_edges: list[dict[str, Any]] = []
        for cid, doc_name in zip(chunk_ids, doc_name_col, strict=True):
            contains_edges.append({
                "label": "contains_chunk",
                "outV": doc_name,
                "outVLabel": "document",
                "inV": cid,
                "inVLabel": "chunk",
                "properties": {},
            })
        for i in range(0, len(contains_edges), batch_size):
            await self._client.add_edges(contains_edges[i : i + batch_size])

        # 6. Insert next_chunk edges (sequential chunks in same doc)
        next_edges = self._build_next_chunk_edges(
            chunk_ids, doc_name_col, chunk_indices
        )
        if next_edges:
            for i in range(0, len(next_edges), batch_size):
                await self._client.add_edges(next_edges[i : i + batch_size])

        # 7. Extract entities and relations from each chunk
        total_entities = 0
        total_relations = 0
        for idx, (cid, content) in enumerate(zip(chunk_ids, contents, strict=True)):
            result = await self._extractor.extract(content, chunk_id=cid)
            task.processed_chunks = idx + 1
            total_entities += len(result.entities)
            total_relations += len(result.relations)

            if not result.entities and not result.relations:
                continue

            # 8. Insert entity vertices
            entity_vertices = [
                {
                    "label": "entity",
                    "properties": {
                        "name": e.name,
                        "type": e.entity_type,
                    },
                }
                for e in result.entities
            ]
            if entity_vertices:
                await self._client.add_vertices(entity_vertices)

            # 9. Insert references edges (chunk -> entity)
            ref_edges = [
                {
                    "label": "references",
                    "outV": cid,
                    "outVLabel": "chunk",
                    "inV": e.name,
                    "inVLabel": "entity",
                    "properties": {},
                }
                for e in result.entities
            ]
            if ref_edges:
                await self._client.add_edges(ref_edges)

            # 10. Insert relation edges (entity -> entity)
            rel_edges = [
                {
                    "label": r.relation_type,
                    "outV": r.source,
                    "outVLabel": "entity",
                    "inV": r.target,
                    "inVLabel": "entity",
                    "properties": dict(r.properties) if r.properties else {},
                }
                for r in result.relations
            ]
            if rel_edges:
                await self._client.add_edges(rel_edges)

        task.entity_count = total_entities
        task.relation_count = total_relations

    @staticmethod
    def _unique_column_values(table: pa.Table, column: str) -> list[str]:
        """Return unique string values from a table column."""
        col = table.column(column)
        return list(dict.fromkeys(col.to_pylist()))

    @staticmethod
    def _build_next_chunk_edges(
        chunk_ids: list[str],
        doc_names: list[str],
        chunk_indices: list[int],
    ) -> list[dict[str, Any]]:
        """Build next_chunk edges between sequential chunks in the same document."""
        edges: list[dict[str, Any]] = []
        # Group chunks by document
        doc_chunks: dict[str, list[tuple[int, str]]] = {}
        for cid, doc, idx in zip(chunk_ids, doc_names, chunk_indices, strict=True):
            doc_chunks.setdefault(doc, []).append((idx, cid))

        for _doc, chunks in doc_chunks.items():
            sorted_chunks = sorted(chunks, key=lambda x: x[0])
            for i in range(len(sorted_chunks) - 1):
                _, curr_id = sorted_chunks[i]
                _, next_id = sorted_chunks[i + 1]
                edges.append({
                    "label": "next_chunk",
                    "outV": curr_id,
                    "outVLabel": "chunk",
                    "inV": next_id,
                    "inVLabel": "chunk",
                    "properties": {},
                })

        return edges
