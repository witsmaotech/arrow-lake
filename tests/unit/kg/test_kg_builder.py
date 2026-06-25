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
    # A mock extractor has no real doc_type classifier → _infer_doc_type is a
    # no-op (returns None), so tests asserting doc_type=None behavior hold.
    extractor._classifier = None
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
    await builder.execute_build(task_id)

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
    task_id = await builder.build("test_ds", chunks_table)
    await builder.execute_build(task_id)

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
    task_id = await builder.build("test_ds", chunks_table)
    await builder.execute_build(task_id)

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
    task_id = await builder.build("test_ds", chunks_table)
    await builder.execute_build(task_id)

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
    await builder.execute_build(task_id)

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
    await builder.execute_build(task_id)

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
    await builder.execute_build(task_id)
    assert isinstance(task_id, str)

    task = builder.get_task_status(task_id)
    assert task is not None
    assert task.status == KGBuildStatus.FAILED
    assert task.error is not None
    assert "Schema error" in task.error


# ---------------------------------------------------------------------------
# execute_build() path — doc_type passthrough + full chain (v1.7.0 §12.4/§12.8)
# ---------------------------------------------------------------------------
# NOTE: build() is fire-and-forget (v1.6.1) — it only stages the task; the
# actual schema/extract/insert work happens in execute_build(). The build_* tests
# above pre-date that refactor and assert post-execution state without awaiting
# execute_build(), so they are expected to fail until back-filled. The tests
# below exercise the real execute_build() path.


@pytest.fixture
def chunks_table_with_doc_type() -> pa.Table:
    """Table with a per-ingest doc_type column (v1.7.0 §12.3)."""
    return pa.table({
        "id": ["chunk-1", "chunk-2"],
        "content": ["Alice works at Acme Corp.", "Bob lives in NYC."],
        # Different documents so no next_chunk edges are generated.
        "document_name": ["doc1.txt", "doc2.txt"],
        "chunk_index": [0, 0],
        "doc_type": ["research_paper", "report"],
    })


@pytest.mark.asyncio
async def test_execute_build_doc_type_passthrough(
    mock_client: object,
    mock_extractor: object,
    chunks_table_with_doc_type: pa.Table,
    config: HugeGraphConfig,
) -> None:
    """execute_build() forwards each chunk's doc_type to extractor.extract()."""
    builder = KGBuilder(mock_client, mock_extractor, config)
    task_id = await builder.build("test_ds", chunks_table_with_doc_type)
    await builder.execute_build(task_id)

    # extract() called once per chunk, with the chunk's doc_type kwarg.
    assert mock_extractor.extract.await_count == 2
    forwarded = [call.kwargs.get("doc_type") for call in mock_extractor.extract.call_args_list]
    assert forwarded == ["research_paper", "report"]


@pytest.mark.asyncio
async def test_execute_build_missing_doc_type_column(
    mock_client: object,
    mock_extractor: object,
    chunks_table: pa.Table,
    config: HugeGraphConfig,
) -> None:
    """execute_build() tolerates tables without a doc_type column (None)."""
    builder = KGBuilder(mock_client, mock_extractor, config)
    task_id = await builder.build("test_ds", chunks_table)  # no doc_type column
    await builder.execute_build(task_id)

    assert mock_extractor.extract.await_count == 2
    for call in mock_extractor.extract.call_args_list:
        assert call.kwargs.get("doc_type") is None


@pytest.mark.asyncio
async def test_execute_build_completes_full_chain(
    mock_client: object,
    mock_extractor: object,
    chunks_table_with_doc_type: pa.Table,
    config: HugeGraphConfig,
) -> None:
    """execute_build() runs the full pipeline and marks the task COMPLETED."""
    builder = KGBuilder(mock_client, mock_extractor, config)
    task_id = await builder.build("test_ds", chunks_table_with_doc_type)
    await builder.execute_build(task_id)

    task = builder.get_task_status(task_id)
    assert task is not None
    assert task.status == KGBuildStatus.COMPLETED
    assert task.processed_chunks == 2
    assert task.entity_count >= 1
    assert task.relation_count >= 1
    assert task.error is None

    # Schema ensured + vertices/edges inserted via the client.
    mock_client.ensure_schema.assert_awaited_once()
    assert mock_client.add_vertices.await_count >= 2
    assert mock_client.add_edges.await_count >= 1


# ---------------------------------------------------------------------------
# v1.7.1 §4.5: entity double-write + relation routing (A strategy)
# ---------------------------------------------------------------------------


@pytest.fixture
def typed_extractor() -> object:
    """Mock extractor returning typed entities + a routable + a fallback relation."""
    from unittest.mock import AsyncMock

    extractor = AsyncMock()
    extractor.extract.return_value = ExtractionResult(
        entities=(
            ExtractedEntity(name="Alice", entity_type="person"),
            ExtractedEntity(name="Acme", entity_type="organization"),
            ExtractedEntity(name="Scheme", entity_type="concept"),  # no typed edge target
        ),
        relations=(
            # person→organization + synonym → belongs_to
            ExtractedRelation(source="Alice", target="Acme", relation_type="works_at"),
            # person→concept + no synonym → related_to fallback
            ExtractedRelation(source="Alice", target="Scheme", relation_type="knows"),
        ),
        raw_text="Alice works at Acme and knows Scheme.",
    )
    return extractor


def _all_vertices(mock_client: object) -> list[dict]:
    return [v for call in mock_client.add_vertices.call_args_list for v in call[0][0]]


def _all_edges(mock_client: object) -> list[dict]:
    return [e for call in mock_client.add_edges.call_args_list for e in call[0][0]]


@pytest.mark.asyncio
async def test_execute_build_double_writes_typed_vertices(
    mock_client: object,
    typed_extractor: object,
    chunks_table_with_doc_type: pa.Table,
    config: HugeGraphConfig,
) -> None:
    """Recognized entity types produce BOTH an entity vertex and a typed vertex."""
    builder = KGBuilder(mock_client, typed_extractor, config)
    task_id = await builder.build("ds", chunks_table_with_doc_type)
    await builder.execute_build(task_id)

    labels = [v["label"] for v in _all_vertices(mock_client)]
    # Alice→entity+person, Acme→entity+organization, Scheme→entity+concept
    assert labels.count("entity") >= 3
    assert "person" in labels
    assert "organization" in labels
    assert "concept" in labels


@pytest.mark.asyncio
async def test_execute_build_routes_typed_edge_on_synonym(
    mock_client: object,
    typed_extractor: object,
    chunks_table_with_doc_type: pa.Table,
    config: HugeGraphConfig,
) -> None:
    """works_at (person→organization) routes to belongs_to on typed vertices."""
    builder = KGBuilder(mock_client, typed_extractor, config)
    task_id = await builder.build("ds", chunks_table_with_doc_type)
    await builder.execute_build(task_id)

    belongs = [e for e in _all_edges(mock_client) if e["label"] == "belongs_to"]
    assert len(belongs) >= 1
    b = belongs[0]
    assert b["outVLabel"] == "person"
    assert b["inVLabel"] == "organization"
    assert b["properties"]["relation_type"] == "works_at"


@pytest.mark.asyncio
async def test_execute_build_falls_back_to_related_to_with_relation_type(
    mock_client: object,
    typed_extractor: object,
    chunks_table_with_doc_type: pa.Table,
    config: HugeGraphConfig,
) -> None:
    """knows (person→concept, no synonym) → related_to on entity vertices, relation_type kept."""
    builder = KGBuilder(mock_client, typed_extractor, config)
    task_id = await builder.build("ds", chunks_table_with_doc_type)
    await builder.execute_build(task_id)

    related = [e for e in _all_edges(mock_client) if e["label"] == "related_to"]
    assert len(related) >= 1
    r = related[0]
    assert r["outVLabel"] == "entity"
    assert r["inVLabel"] == "entity"
    assert r["properties"]["relation_type"] == "knows"
    assert r["properties"]["weight"] == 1.0
