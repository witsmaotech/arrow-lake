"""Unit tests for KGBuilder (mock HugeGraphClient + EntityExtractor)."""

from __future__ import annotations

import pyarrow as pa
import pytest
from arrow_lake.config import HugeGraphConfig
from arrow_lake.knowledge_graph.builder import KGBuilder, KGBuildStatus, KGBuildTask
from arrow_lake.knowledge_graph.extractor import (
    ExtractedEntity,
    ExtractedRelation,
    ExtractionResult,
)
from arrow_lake.knowledge_graph.schema import ARROW_LAKE_KG_SCHEMA, schema_to_hugegraph_payload

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def config() -> HugeGraphConfig:
    return HugeGraphConfig(
        enabled=True,
        host="localhost",
        port=8089,
        graph_name="test_graph",
        build_batch_size=10,
    )


@pytest.fixture
def mock_client() -> object:
    """Create a mock HugeGraphClient."""
    from unittest.mock import AsyncMock

    client = AsyncMock()
    client.ensure_schema = AsyncMock()

    def _fake_add_vertices(vertices):
        return [f"hg-{i}" for i in range(len(vertices))]

    def _fake_add_edges(edges):
        return len(edges)

    client.add_vertices = AsyncMock(side_effect=_fake_add_vertices)
    client.add_edges = AsyncMock(side_effect=_fake_add_edges)
    return client


@pytest.fixture
def mock_extractor() -> object:
    """Create a mock EntityExtractor."""
    from unittest.mock import AsyncMock

    extractor = AsyncMock()
    extractor.extract.return_value = ExtractionResult(
        entities=(
            ExtractedEntity(name="Alice", entity_type="person"),
            ExtractedEntity(name="Acme Corp", entity_type="organization"),
        ),
        relations=(
            ExtractedRelation(
                source="Alice", target="Acme Corp", relation_type="works_at"
            ),
        ),
        raw_text="Alice works at Acme Corp.",
    )
    return extractor


@pytest.fixture
def chunks_table() -> pa.Table:
    """Create a pyarrow Table with chunk data."""
    return pa.table({
        "id": ["chunk-1", "chunk-2"],
        "content": ["Alice works at Acme Corp.", "Bob lives in NYC."],
        "document_name": ["doc1.txt", "doc1.txt"],
        "chunk_index": [0, 1],
    })


# ---------------------------------------------------------------------------
# KGBuildTask / KGBuildStatus
# ---------------------------------------------------------------------------


def test_kg_build_status_enum() -> None:
    assert KGBuildStatus.PENDING == "PENDING"
    assert KGBuildStatus.RUNNING == "RUNNING"
    assert KGBuildStatus.COMPLETED == "COMPLETED"
    assert KGBuildStatus.FAILED == "FAILED"


def test_kg_build_task_fields() -> None:
    task = KGBuildTask(
        task_id="t1",
        status=KGBuildStatus.PENDING,
        dataset_name="test_ds",
        total_chunks=2,
        processed_chunks=0,
        entity_count=0,
        relation_count=0,
        started_at=None,
        completed_at=None,
        error=None,
    )
    assert task.task_id == "t1"
    assert task.status == KGBuildStatus.PENDING
    assert task.total_chunks == 2


# ---------------------------------------------------------------------------
# build() basic flow
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_build_basic_flow(
    mock_client: object,
    mock_extractor: object,
    chunks_table: pa.Table,
    config: HugeGraphConfig,
) -> None:
    """build() creates schema, inserts vertices/edges, extracts entities."""
    builder = KGBuilder(mock_client, mock_extractor, config)
    task_id = await builder.build("test_ds", chunks_table)

    # Returns a task ID
    assert isinstance(task_id, str)

    # Schema should be ensured
    expected_schema = schema_to_hugegraph_payload(ARROW_LAKE_KG_SCHEMA)
    mock_client.ensure_schema.assert_awaited_once_with(expected_schema)

    # Vertices should be inserted (documents + chunks + entities per chunk)
    assert mock_client.add_vertices.await_count >= 2

    # Edges should be inserted (contains_chunk + next_chunk + references + entity edges)
    assert mock_client.add_edges.await_count >= 1

    # Extractor should be called for each chunk
    assert mock_extractor.extract.await_count == 2


@pytest.mark.asyncio
async def test_build_creates_document_and_chunk_vertices(
    mock_client: object,
    mock_extractor: object,
    chunks_table: pa.Table,
    config: HugeGraphConfig,
) -> None:
    """build() should insert document vertices and chunk vertices."""
    builder = KGBuilder(mock_client, mock_extractor, config)
    await builder.build("test_ds", chunks_table)

    # Collect all vertex insert calls
    all_vertex_calls = []
    for call in mock_client.add_vertices.call_args_list:
        vertices = call[0][0]
        all_vertex_calls.extend(vertices)

    # Should have document vertices (one per unique document_name)
    doc_vertices = [v for v in all_vertex_calls if v.get("label") == "document"]
    assert len(doc_vertices) == 1
    assert doc_vertices[0]["properties"]["name"] == "doc1.txt"

    # Should have chunk vertices
    chunk_vertices = [v for v in all_vertex_calls if v.get("label") == "chunk"]
    assert len(chunk_vertices) == 2
    assert chunk_vertices[0]["properties"]["id"] == "chunk-1"
    assert chunk_vertices[1]["properties"]["id"] == "chunk-2"


@pytest.mark.asyncio
async def test_build_creates_contains_chunk_edges(
    mock_client: object,
    mock_extractor: object,
    chunks_table: pa.Table,
    config: HugeGraphConfig,
) -> None:
    """build() should create contains_chunk edges from document to chunks."""
    builder = KGBuilder(mock_client, mock_extractor, config)
    await builder.build("test_ds", chunks_table)

    # Collect all edge insert calls
    all_edge_calls = []
    for call in mock_client.add_edges.call_args_list:
        edges = call[0][0]
        all_edge_calls.extend(edges)

    # Should have contains_chunk edges
    contains_edges = [e for e in all_edge_calls if e.get("label") == "contains_chunk"]
    assert len(contains_edges) == 2


@pytest.mark.asyncio
async def test_build_creates_entity_vertices_and_edges(
    mock_client: object,
    mock_extractor: object,
    chunks_table: pa.Table,
    config: HugeGraphConfig,
) -> None:
    """build() should create entity vertices and references edges."""
    builder = KGBuilder(mock_client, mock_extractor, config)
    await builder.build("test_ds", chunks_table)

    all_vertex_calls = []
    for call in mock_client.add_vertices.call_args_list:
        vertices = call[0][0]
        all_vertex_calls.extend(vertices)

    # Should have entity vertices
    entity_vertices = [v for v in all_vertex_calls if v.get("label") == "entity"]
    assert len(entity_vertices) >= 1

    all_edge_calls = []
    for call in mock_client.add_edges.call_args_list:
        edges = call[0][0]
        all_edge_calls.extend(edges)

    # Should have references edges from chunks to entities
    ref_edges = [e for e in all_edge_calls if e.get("label") == "references"]
    assert len(ref_edges) >= 1


# ---------------------------------------------------------------------------
# build() empty table
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_build_empty_table(
    mock_client: object,
    mock_extractor: object,
    config: HugeGraphConfig,
) -> None:
    """build() with empty table should complete without errors."""
    empty_table = pa.table({
        "id": pa.array([], type=pa.string()),
        "content": pa.array([], type=pa.string()),
        "document_name": pa.array([], type=pa.string()),
        "chunk_index": pa.array([], type=pa.int32()),
    })
    builder = KGBuilder(mock_client, mock_extractor, config)
    task_id = await builder.build("empty_ds", empty_table)

    assert isinstance(task_id, str)
    mock_client.ensure_schema.assert_awaited_once()
    mock_extractor.extract.assert_not_awaited()


# ---------------------------------------------------------------------------
# get_task_status
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_task_status(
    mock_client: object,
    mock_extractor: object,
    chunks_table: pa.Table,
    config: HugeGraphConfig,
) -> None:
    """get_task_status returns task details after build."""
    builder = KGBuilder(mock_client, mock_extractor, config)
    task_id = await builder.build("test_ds", chunks_table)

    task = builder.get_task_status(task_id)
    assert task is not None
    assert task.task_id == task_id
    assert task.dataset_name == "test_ds"
    assert task.total_chunks == 2
    assert task.processed_chunks == 2
    assert task.entity_count >= 0
    assert task.relation_count >= 0
    assert task.status == KGBuildStatus.COMPLETED
    assert task.started_at is not None
    assert task.completed_at is not None
    assert task.error is None


def test_get_task_status_unknown(builder_no_build: object) -> None:
    """get_task_status returns None for unknown task ID."""
    task = builder_no_build.get_task_status("nonexistent")
    assert task is None


# Fixture for the unknown task test
@pytest.fixture
def builder_no_build(config: HugeGraphConfig) -> KGBuilder:
    from unittest.mock import AsyncMock

    mock_client = AsyncMock()
    mock_extractor = AsyncMock()
    mock_client.add_vertices = AsyncMock(side_effect=lambda v: [f"hg-{i}" for i in range(len(v))])
    mock_client.add_edges = AsyncMock(side_effect=lambda e: len(e))
    return KGBuilder(mock_client, mock_extractor, config)


# ---------------------------------------------------------------------------
# build() error handling
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_build_error_handling(
    mock_client: object,
    mock_extractor: object,
    chunks_table: pa.Table,
    config: HugeGraphConfig,
) -> None:
    """build() should capture errors and store them in the task."""
    mock_client.ensure_schema.side_effect = RuntimeError("Schema error")
    builder = KGBuilder(mock_client, mock_extractor, config)

    task_id = await builder.build("test_ds", chunks_table)
    assert isinstance(task_id, str)

    task = builder.get_task_status(task_id)
    assert task is not None
    assert task.status == KGBuildStatus.FAILED
    assert task.error is not None
    assert "Schema error" in task.error
