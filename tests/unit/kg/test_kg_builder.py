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

    def _fake_add_vertices(vertices, **kwargs):
        return [f"hg-{i}" for i in range(len(vertices))]

    def _fake_add_edges(edges, **kwargs):
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

    # Schema should be ensured on the per-dataset graph (v1.8.6 isolation)
    expected_schema = schema_to_hugegraph_payload(ARROW_LAKE_KG_SCHEMA)
    mock_client.ensure_schema.assert_awaited_once_with(
        expected_schema, graph_name="kg_test_ds"
    )

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
    mock_client.add_vertices = AsyncMock(side_effect=lambda v, **kw: [f"hg-{i}" for i in range(len(v))])
    mock_client.add_edges = AsyncMock(side_effect=lambda e, **kw: len(e))
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
    # Alice→entity+person, Acme→entity+organization (typed double-write kept for
    # concrete types). Scheme→entity only: 662075b removed the concept double-
    # write (concept was an over-broad generic sink causing 双写孤岛), so no
    # 'concept' typed vertex is produced.
    assert labels.count("entity") >= 3
    assert "person" in labels
    assert "organization" in labels
    assert "concept" not in labels


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


# ---------------------------------------------------------------------------
# normalize_name: relation endpoint case/whitespace tolerance (orphan fix)
# ---------------------------------------------------------------------------


@pytest.fixture
def spaced_case_extractor() -> object:
    """Extractor whose relation source/target differ from entity names by
    case + whitespace. Without normalize_name these miss ``entity_id_map`` and
    the relation is dropped at ``_insert_kg`` → endpoint vertices go orphan.
    """
    from unittest.mock import AsyncMock

    extractor = AsyncMock()
    extractor._classifier = None
    extractor.extract.return_value = ExtractionResult(
        entities=(
            ExtractedEntity(name="Alice", entity_type="person"),
            ExtractedEntity(name="Acme Corp", entity_type="organization"),
        ),
        relations=(
            # source/target carry case + whitespace variants of the entity names.
            ExtractedRelation(
                source=" alice ", target="ACME CORP", relation_type="works_at"
            ),
        ),
        raw_text="Alice works at Acme Corp.",
    )
    return extractor


@pytest.mark.asyncio
async def test_execute_build_normalizes_relation_endpoints(
    mock_client: object,
    spaced_case_extractor: object,
    chunks_table_with_doc_type: pa.Table,
    config: HugeGraphConfig,
) -> None:
    """A relation whose source/target differ only by case/whitespace from the
    entity names still resolves to an edge (previously dropped → orphans)."""
    builder = KGBuilder(mock_client, spaced_case_extractor, config)
    task_id = await builder.build("ds", chunks_table_with_doc_type)
    await builder.execute_build(task_id)

    works = [
        e for e in _all_edges(mock_client)
        if e.get("properties", {}).get("relation_type") == "works_at"
    ]
    # Pre-fix this was 0 (the relation was `continue`-dropped on name mismatch).
    assert len(works) >= 1


# ---------------------------------------------------------------------------
# entity resolution (per-dataset path, he_entity_resolution=auto)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_execute_build_dataset_path_runs_entity_resolution(
    mock_client: object,
    chunks_table_with_doc_type: pa.Table,
    config: HugeGraphConfig,
    tmp_path,
) -> None:
    """per-dataset path with he_entity_resolution=auto invokes resolve_entities
    and remaps entity_chunks so the canonical inherits the merged name's chunks."""
    from pathlib import Path
    from unittest.mock import AsyncMock, MagicMock

    from arrow_lake.knowledge_graph.he_extractor import DatasetKA

    config.he_kg_granularity = "dataset"
    config.he_entity_resolution = "auto"
    config.he_ka_base_dir = str(tmp_path)

    extractor = AsyncMock()
    extractor._classifier = None
    extractor._kg_granularity = "dataset"
    extractor._embedder = None  # resolve_entities is mocked below; no embedder needed
    extractor._resolve_template = MagicMock(return_value="entity_graph")
    base_result = ExtractionResult(
        entities=(
            ExtractedEntity(name="B", entity_type="x"),
            ExtractedEntity(name="A", entity_type="x"),
        ),
        relations=(),
        raw_text="",
    )
    ec: dict[str, list[str]] = {"B": ["c1"], "A": ["c2"]}
    extractor.build_dataset_ka = AsyncMock(
        return_value=DatasetKA(
            ka=MagicMock(),
            ka_dir=tmp_path / "ka",
            entity_chunks=ec,
            result=base_result,
        )
    )
    resolved = ExtractionResult(
        entities=(ExtractedEntity(name="A", entity_type="x"),),
        relations=(),
        raw_text="",
    )
    extractor.resolve_entities = AsyncMock(return_value=(resolved, {"B": "A"}))

    builder = KGBuilder(mock_client, extractor, config)
    builder._ka_base_dir = Path(tmp_path)
    task_id = await builder.build("ds", chunks_table_with_doc_type)
    await builder.execute_build(task_id)

    extractor.resolve_entities.assert_awaited_once()
    # entity_chunks remapped in place: B's chunk c1 folded into A
    assert "B" not in ec
    assert "c1" in ec["A"]


# ---------------------------------------------------------------------------
# _merge_chunk_results (map_reduce shuffle/reduce step, pure logic)
# ---------------------------------------------------------------------------


def _ent(name: str, etype: str = "概念", defn: str = "") -> ExtractedEntity:
    props = (("definition", defn),) if defn else ()
    return ExtractedEntity(name=name, entity_type=etype, properties=props)


def _rel(s: str, t: str, rt: str) -> ExtractedRelation:
    return ExtractedRelation(source=s, target=t, relation_type=rt, properties=())


def _chunk_res(cid, entities=(), relations=()):
    return (cid, ExtractionResult(entities=tuple(entities), relations=tuple(relations), raw_text=""))


def test_merge_dedup_entities_by_normalized_name() -> None:
    results = [
        _chunk_res("c1", [_ent("Alice", "主体")]),
        _chunk_res("c2", [_ent("ALICE", "主体")]),  # case variant
    ]
    merged, ec = KGBuilder._merge_chunk_results(results)
    assert {e.name for e in merged.entities} == {"Alice"}  # first-seen display name
    assert ec["Alice"] == ["c1", "c2"]


def test_merge_keeps_longest_definition() -> None:
    results = [
        _chunk_res("c1", [_ent("A", defn="short")]),
        _chunk_res("c2", [_ent("A", defn="a much longer definition")]),
    ]
    merged, _ = KGBuilder._merge_chunk_results(results)
    a = next(e for e in merged.entities if e.name == "A")
    assert dict(a.properties).get("definition") == "a much longer definition"


def test_merge_first_nonempty_type_wins() -> None:
    results = [
        _chunk_res("c1", [_ent("A", etype="")]),
        _chunk_res("c2", [_ent("A", etype="模型")]),
    ]
    merged, _ = KGBuilder._merge_chunk_results(results)
    a = next(e for e in merged.entities if e.name == "A")
    assert a.entity_type == "模型"


def test_merge_provenance_union() -> None:
    results = [
        _chunk_res("c1", [_ent("X")]),
        _chunk_res("c2", [_ent("Y")]),
        _chunk_res("c3", [_ent("X")]),
    ]
    _, ec = KGBuilder._merge_chunk_results(results)
    assert ec["X"] == ["c1", "c3"]
    assert ec["Y"] == ["c2"]


def test_merge_relation_endpoint_remap_and_dedup() -> None:
    results = [
        _chunk_res("c1", [_ent("Alice")]),
        _chunk_res("c2", [_ent("ALICE"), _ent("Bob")], [_rel("ALICE", "Bob", "包含")]),
        _chunk_res("c3", [_ent("Bob")], [_rel("alice", "Bob", "包含")]),  # dedup with c2
    ]
    merged, _ = KGBuilder._merge_chunk_results(results)
    rels = [(r.source, r.target, r.relation_type) for r in merged.relations]
    assert ("Alice", "Bob", "包含") in rels
    assert len(merged.relations) == 1  # deduped across chunks


def test_merge_drops_self_loop() -> None:
    results = [_chunk_res("c1", [_ent("A")], [_rel("A", "A", "包含")])]
    merged, _ = KGBuilder._merge_chunk_results(results)
    assert merged.relations == ()


def test_merge_empty_input() -> None:
    merged, ec = KGBuilder._merge_chunk_results([])
    assert merged.entities == ()
    assert merged.relations == ()
    assert ec == {}


def test_merge_keeps_distinct_entities_across_chunks() -> None:
    results = [
        _chunk_res("c1", [_ent("A"), _ent("B")]),
        _chunk_res("c2", [_ent("C")]),
    ]
    merged, ec = KGBuilder._merge_chunk_results(results)
    assert {e.name for e in merged.entities} == {"A", "B", "C"}
    assert ec["A"] == ["c1"] and ec["C"] == ["c2"]


def test_merge_relation_to_unknown_endpoint_kept() -> None:
    # Tolerant: unknown endpoint passes through (insert will drop if unresolved).
    results = [_chunk_res("c1", [_ent("A")], [_rel("A", "Ghost", "包含")])]
    merged, _ = KGBuilder._merge_chunk_results(results)
    assert len(merged.relations) == 1
    assert (merged.relations[0].source, merged.relations[0].target) == ("A", "Ghost")


# ---------------------------------------------------------------------------
# map_reduce _execute_build branch (integration, mock extractor/client)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_execute_build_map_reduce_merges_and_inserts_once(
    mock_client: object,
    chunks_table_with_doc_type: pa.Table,
    config: HugeGraphConfig,
) -> None:
    """map_reduce: per-chunk extract → global merge → SINGLE _insert_kg with
    merged entities + unioned provenance. Resolution + type-pair OFF to isolate
    the merge/insert wiring."""
    from unittest.mock import AsyncMock, MagicMock

    config.he_kg_granularity = "map_reduce"
    config.he_entity_resolution = "off"
    config.he_kg_type_pair = False

    extractor = AsyncMock()
    extractor._classifier = None
    extractor._kg_granularity = "map_reduce"
    extractor._resolve_template = MagicMock(return_value="entity_graph")

    def _extract(content, *, chunk_id="", doc_type=None):
        if chunk_id == "chunk-1":
            return ExtractionResult(
                (ExtractedEntity("Alice", "主体"), ExtractedEntity("Bob", "主体")), (), "")
        return ExtractionResult(
            (ExtractedEntity("ALICE", "主体"), ExtractedEntity("Carol", "主体")), (), "")

    extractor.extract = AsyncMock(side_effect=_extract)

    builder = KGBuilder(mock_client, extractor, config)
    inserted: list = []

    async def _spy(result, graph_name, chunk_id_map, **kw):
        inserted.append((result, kw.get("entity_chunks")))

    builder._insert_kg = _spy  # type: ignore[method-assign]

    task_id = await builder.build("ds", chunks_table_with_doc_type)
    await builder.execute_build(task_id)

    assert extractor.extract.await_count == 2
    assert len(inserted) == 1  # one insert, not two
    result, ec = inserted[0]
    assert {e.name for e in result.entities} == {"Alice", "Bob", "Carol"}  # ALICE→Alice
    assert set(ec) == {"Alice", "Bob", "Carol"}
    assert ec["Alice"] == ["chunk-1", "chunk-2"]  # provenance union
    assert ec["Bob"] == ["chunk-1"]
    assert ec["Carol"] == ["chunk-2"]


@pytest.mark.asyncio
async def test_execute_build_map_reduce_runs_entity_resolution(
    mock_client: object,
    chunks_table_with_doc_type: pa.Table,
    config: HugeGraphConfig,
) -> None:
    """map_reduce with he_entity_resolution=auto invokes resolve_entities on the
    merged result and remaps entity_chunks so the canonical inherits merged chunks."""
    from unittest.mock import AsyncMock, MagicMock

    config.he_kg_granularity = "map_reduce"
    config.he_entity_resolution = "auto"
    config.he_kg_type_pair = False

    extractor = AsyncMock()
    extractor._classifier = None
    extractor._kg_granularity = "map_reduce"
    extractor._embedder = None  # resolve_entities is mocked below; no embedder needed
    extractor._resolve_template = MagicMock(return_value="entity_graph")
    extractor.extract = AsyncMock(side_effect=[
        ExtractionResult((ExtractedEntity("A", "x"),), (), ""),    # chunk-1
        ExtractionResult((ExtractedEntity("B", "x"),), (), ""),    # chunk-2
    ])
    resolved = ExtractionResult((ExtractedEntity("A", "x"),), (), "")
    extractor.resolve_entities = AsyncMock(return_value=(resolved, {"B": "A"}))

    builder = KGBuilder(mock_client, extractor, config)
    captured: dict = {}

    async def _spy(result, graph_name, chunk_id_map, **kw):
        captured["ec"] = kw.get("entity_chunks")

    builder._insert_kg = _spy  # type: ignore[method-assign]

    task_id = await builder.build("ds", chunks_table_with_doc_type)
    await builder.execute_build(task_id)

    extractor.resolve_entities.assert_awaited_once()
    ec = captured["ec"]
    assert "B" not in ec            # merged away
    assert ec["A"] == ["chunk-1", "chunk-2"]  # canonical inherited B's chunk-2


@pytest.mark.asyncio
async def test_execute_build_map_reduce_resumes_from_checkpoint(
    mock_client: object,
    chunks_table_with_doc_type: pa.Table,
    config: HugeGraphConfig,
    tmp_path,
) -> None:
    """incremental=True + matching per-cid checkpoint (template content hash +
    per-cid content hashes) skips MAP entirely: extractor.extract is NOT called;
    the checkpoint's per-cid entities are folded + inserted. (v1.10.2 M3: per-cid
    sharded checkpoint; resume is now gated on incremental=True — G6 default =
    full MAP.)"""
    import hashlib
    import json
    from pathlib import Path
    from unittest.mock import AsyncMock, MagicMock

    from arrow_lake.knowledge_graph._naming import artifact_key_for

    config.he_kg_granularity = "map_reduce"
    config.he_entity_resolution = "off"
    config.he_kg_type_pair = False
    config.he_ka_base_dir = str(tmp_path)

    # Real template file so the content-hash gate (file bytes) is stable.
    tpl = Path(tmp_path) / "entity_graph.yaml"
    tpl.write_text("name: entity_graph\n", encoding="utf-8")
    tpl_hash = hashlib.sha1(tpl.read_bytes()).hexdigest()[:16]

    extractor = AsyncMock()
    extractor._classifier = None
    extractor._kg_granularity = "map_reduce"
    extractor._resolve_template = MagicMock(return_value=str(tpl))
    extractor.extract = AsyncMock()  # must NOT be awaited on full reuse

    contents = [str(c) for c in chunks_table_with_doc_type.column("content").to_pylist()]

    def _h(t: str) -> str:
        return hashlib.sha1((t or "").encode("utf-8", "replace")).hexdigest()[:16]

    ckpt = Path(tmp_path) / artifact_key_for("ds") / "ka" / "map_reduce.json"
    ckpt.parent.mkdir(parents=True, exist_ok=True)
    ckpt.write_text(json.dumps({
        "template": "entity_graph",
        "template_hash": tpl_hash,
        "chunks": {
            "chunk-1": {"hash": _h(contents[0]),
                        "entities": [{"name": "X", "type": "t", "properties": []}],
                        "relations": []},
            "chunk-2": {"hash": _h(contents[1]),
                        "entities": [{"name": "Y", "type": "t", "properties": []}],
                        "relations": []},
        },
    }), encoding="utf-8")

    builder = KGBuilder(mock_client, extractor, config)
    builder._ka_base_dir = Path(tmp_path)
    inserted: list = []

    async def _spy(result, graph_name, chunk_id_map, **kw):
        inserted.append(result)

    builder._insert_kg = _spy  # type: ignore[method-assign]

    task_id = await builder.build("ds", chunks_table_with_doc_type, incremental=True)
    await builder.execute_build(task_id)

    extractor.extract.assert_not_awaited()        # MAP fully skipped (all reused)
    assert len(inserted) == 1
    assert {e.name for e in inserted[0].entities} == {"X", "Y"}  # both per-cid


@pytest.mark.asyncio
async def test_execute_build_map_reduce_incremental_append_maps_only_new(
    mock_client: object, config: HugeGraphConfig, tmp_path,
) -> None:
    """incremental append: first build MAPs all chunks; second build (one new
    chunk appended, others byte-identical) MAPs ONLY the new chunk — the per-cid
    checkpoint reuses the unchanged ones (P2.1, G2)."""
    from pathlib import Path
    from unittest.mock import AsyncMock, MagicMock

    config.he_kg_granularity = "map_reduce"
    config.he_entity_resolution = "off"
    config.he_kg_type_pair = False
    config.he_ka_base_dir = str(tmp_path)
    tpl = Path(tmp_path) / "t.yaml"
    tpl.write_text("name: t\n", encoding="utf-8")

    def _extract(content, *, chunk_id="", doc_type=None):
        return ExtractionResult((ExtractedEntity(chunk_id or "e", "t"),), (), content)

    extractor = AsyncMock()
    extractor._classifier = None
    extractor._kg_granularity = "map_reduce"
    extractor._resolve_template = MagicMock(return_value=str(tpl))
    extractor.extract = AsyncMock(side_effect=_extract)
    builder = KGBuilder(mock_client, extractor, config)
    builder._ka_base_dir = Path(tmp_path)
    builder._insert_kg = AsyncMock(return_value=None)  # type: ignore[method-assign]

    def _tbl(ids, contents):
        return pa.table({
            "id": ids, "content": contents,
            "document_name": ["d.txt"] * len(ids),
            "chunk_index": list(range(len(ids))),
            "doc_type": ["report"] * len(ids),
        })

    # 1st build: 2 chunks → both MAPped (no prior checkpoint)
    t1 = await builder.build("ds", _tbl(["c1", "c2"], ["alpha", "beta"]), incremental=True)
    await builder.execute_build(t1)
    assert extractor.extract.await_count == 2

    # 2nd build: append c3 (c1,c2 unchanged) → ONLY c3 MAPped
    extractor.extract = AsyncMock(side_effect=_extract)
    t2 = await builder.build(
        "ds", _tbl(["c1", "c2", "c3"], ["alpha", "beta", "gamma"]), incremental=True,
    )
    await builder.execute_build(t2)
    assert extractor.extract.await_count == 1


@pytest.mark.asyncio
async def test_execute_build_map_reduce_incremental_content_edit_re_extracts(
    mock_client: object, config: HugeGraphConfig, tmp_path, caplog,
) -> None:
    """A previously-fed chunk whose content changed (re-ingest/edit) is re-
    extracted; unchanged chunks are reused (G5 — granular per-cid hash check)."""
    import logging
    from pathlib import Path
    from unittest.mock import AsyncMock, MagicMock

    config.he_kg_granularity = "map_reduce"
    config.he_entity_resolution = "off"
    config.he_kg_type_pair = False
    config.he_ka_base_dir = str(tmp_path)
    tpl = Path(tmp_path) / "t.yaml"
    tpl.write_text("name: t\n", encoding="utf-8")

    def _extract(content, *, chunk_id="", doc_type=None):
        return ExtractionResult((ExtractedEntity(chunk_id or "e", "t"),), (), content)

    extractor = AsyncMock()
    extractor._classifier = None
    extractor._kg_granularity = "map_reduce"
    extractor._resolve_template = MagicMock(return_value=str(tpl))
    extractor.extract = AsyncMock(side_effect=_extract)
    builder = KGBuilder(mock_client, extractor, config)
    builder._ka_base_dir = Path(tmp_path)
    builder._insert_kg = AsyncMock(return_value=None)  # type: ignore[method-assign]

    def _tbl(ids, contents):
        return pa.table({
            "id": ids, "content": contents,
            "document_name": ["d.txt"] * len(ids),
            "chunk_index": list(range(len(ids))),
            "doc_type": ["report"] * len(ids),
        })

    t1 = await builder.build("ds", _tbl(["c1", "c2"], ["alpha", "beta"]), incremental=True)
    await builder.execute_build(t1)

    # 2nd build: c2 content edited → only c2 re-extracted; warns (G5)
    extractor.extract = AsyncMock(side_effect=_extract)
    with caplog.at_level(logging.WARNING):
        t2 = await builder.build(
            "ds", _tbl(["c1", "c2"], ["alpha", "BETA-changed"]), incremental=True,
        )
        await builder.execute_build(t2)
    assert extractor.extract.await_count == 1
    assert any("content changed" in r.message for r in caplog.records)


@pytest.mark.asyncio
async def test_execute_build_map_reduce_template_content_edit_full_remap(
    mock_client: object, config: HugeGraphConfig, tmp_path,
) -> None:
    """Template YAML content edited (stem unchanged) → the template_hash gate
    fails → no per-cid reuse → full re-MAP. P2.3: a template guideline/enum
    edit must invalidate prior extraction (schema semantically changed)."""
    from pathlib import Path
    from unittest.mock import AsyncMock, MagicMock

    config.he_kg_granularity = "map_reduce"
    config.he_entity_resolution = "off"
    config.he_kg_type_pair = False
    config.he_ka_base_dir = str(tmp_path)
    tpl = Path(tmp_path) / "t.yaml"
    tpl.write_text("name: t\nversion: 1\n", encoding="utf-8")

    def _extract(content, *, chunk_id="", doc_type=None):
        return ExtractionResult((ExtractedEntity(chunk_id or "e", "t"),), (), content)

    extractor = AsyncMock()
    extractor._classifier = None
    extractor._kg_granularity = "map_reduce"
    extractor._resolve_template = MagicMock(return_value=str(tpl))
    extractor.extract = AsyncMock(side_effect=_extract)
    builder = KGBuilder(mock_client, extractor, config)
    builder._ka_base_dir = Path(tmp_path)
    builder._insert_kg = AsyncMock(return_value=None)  # type: ignore[method-assign]

    def _tbl(ids, contents):
        return pa.table({
            "id": ids, "content": contents,
            "document_name": ["d.txt"] * len(ids),
            "chunk_index": list(range(len(ids))),
            "doc_type": ["report"] * len(ids),
        })

    # 1st build: 2 chunks → checkpoint with template_hash = sha1(version:1).
    t1 = await builder.build("ds", _tbl(["c1", "c2"], ["alpha", "beta"]), incremental=True)
    await builder.execute_build(t1)
    assert extractor.extract.await_count == 2

    # Edit template CONTENT (same path/stem, different bytes) → gate must fail.
    tpl.write_text("name: t\nversion: 2\n", encoding="utf-8")
    extractor.extract = AsyncMock(side_effect=_extract)
    t2 = await builder.build("ds", _tbl(["c1", "c2"], ["alpha", "beta"]), incremental=True)
    await builder.execute_build(t2)
    # Full re-MAP (2), NOT reuse — template semantics changed even though cid+content identical.
    assert extractor.extract.await_count == 2


@pytest.mark.asyncio
async def test_execute_build_map_reduce_default_is_full_map(
    mock_client: object, config: HugeGraphConfig, tmp_path,
) -> None:
    """incremental=False (default) → full MAP even when a checkpoint exists
    (G6: default build behavior unchanged from v1.10.1)."""
    from pathlib import Path
    from unittest.mock import AsyncMock, MagicMock

    config.he_kg_granularity = "map_reduce"
    config.he_entity_resolution = "off"
    config.he_kg_type_pair = False
    config.he_ka_base_dir = str(tmp_path)
    tpl = Path(tmp_path) / "t.yaml"
    tpl.write_text("name: t\n", encoding="utf-8")

    def _extract(content, *, chunk_id="", doc_type=None):
        return ExtractionResult((ExtractedEntity(chunk_id or "e", "t"),), (), content)

    extractor = AsyncMock()
    extractor._classifier = None
    extractor._kg_granularity = "map_reduce"
    extractor._resolve_template = MagicMock(return_value=str(tpl))
    extractor.extract = AsyncMock(side_effect=_extract)
    builder = KGBuilder(mock_client, extractor, config)
    builder._ka_base_dir = Path(tmp_path)
    builder._insert_kg = AsyncMock(return_value=None)  # type: ignore[method-assign]

    def _tbl(ids, contents):
        return pa.table({
            "id": ids, "content": contents,
            "document_name": ["d.txt"] * len(ids),
            "chunk_index": list(range(len(ids))),
            "doc_type": ["report"] * len(ids),
        })

    # 1st build (incremental) writes a checkpoint.
    t1 = await builder.build("ds", _tbl(["c1", "c2"], ["alpha", "beta"]), incremental=True)
    await builder.execute_build(t1)

    # 2nd build with incremental=False → full MAP (2 extracts), checkpoint ignored.
    extractor.extract = AsyncMock(side_effect=_extract)
    t2 = await builder.build("ds", _tbl(["c1", "c2"], ["alpha", "beta"]), incremental=False)
    await builder.execute_build(t2)
    assert extractor.extract.await_count == 2


@pytest.mark.asyncio
async def test_execute_build_map_reduce_extract_timeout_skips_chunk(
    mock_client: object,
    chunks_table_with_doc_type: pa.Table,
    config: HugeGraphConfig,
    tmp_path,
    monkeypatch,
) -> None:
    """A chunk whose extract exceeds the timeout is counted as a failure and
    skipped — the build completes instead of hanging."""
    import asyncio as _asyncio
    from pathlib import Path
    from unittest.mock import AsyncMock, MagicMock

    import arrow_lake.knowledge_graph.builder as builder_mod
    monkeypatch.setattr(builder_mod, "_MAP_EXTRACT_TIMEOUT_S", 0.1)

    config.he_kg_granularity = "map_reduce"
    config.he_entity_resolution = "off"
    config.he_kg_type_pair = False
    config.he_ka_base_dir = str(tmp_path)

    async def _slow_extract(content, *, chunk_id="", doc_type=None):
        await _asyncio.sleep(5)  # well over the 0.1s timeout
        return ExtractionResult(entities=(), relations=(), raw_text=content)

    extractor = AsyncMock()
    extractor._classifier = None
    extractor._kg_granularity = "map_reduce"
    extractor._resolve_template = MagicMock(return_value="entity_graph")
    extractor.extract = _slow_extract

    builder = KGBuilder(mock_client, extractor, config)
    builder._ka_base_dir = Path(tmp_path)

    async def _spy(result, graph_name, chunk_id_map, **kw):
        pass

    builder._insert_kg = _spy  # type: ignore[method-assign]

    task_id = await builder.build("ds", chunks_table_with_doc_type)
    await builder.execute_build(task_id)  # would hang without the timeout guard

    task = builder.get_task_status(task_id)
    assert task.status == KGBuildStatus.COMPLETED
    assert task.extraction_failures == chunks_table_with_doc_type.num_rows


@pytest.mark.asyncio
async def test_batch_add_vertices_splits_below_hugegraph_cap(config: HugeGraphConfig) -> None:
    """_batch_add_vertices splits a large list into ≤batch_size chunks (HugeGraph
    caps POSTs at 2500 vertices — the wuhu insert failure) and concatenates the
    returned ids in input order."""
    import asyncio as _asyncio
    from unittest.mock import AsyncMock

    calls: list[int] = []
    n = [0]

    async def _add(vertices, **kw):
        calls.append(len(vertices))
        out = []
        for _ in vertices:
            out.append(f"id-{n[0]}")
            n[0] += 1
        return out

    client = AsyncMock()
    client.add_vertices = _add
    builder = KGBuilder(client, AsyncMock(), config)
    verts = [{"label": "entity", "properties": {}} for _ in range(7)]

    ids = await builder._batch_add_vertices(
        verts, graph_name="g", write_sem=_asyncio.Semaphore(2), batch_size=3,
    )

    assert ids == [f"id-{i}" for i in range(7)]   # concatenated in order
    assert calls == [3, 3, 1]                       # split into ≤3
    assert all(c <= 2500 for c in calls)


@pytest.mark.asyncio
async def test_execute_build_map_reduce_orphan_links_added(
    mock_client: object,
    chunks_table_with_doc_type: pa.Table,
    config: HugeGraphConfig,
) -> None:
    """v1.9.9: map_reduce + he_orphan_linking=auto links orphan entities that
    co-occur with a connected entity in the same chunk (evidence-gated, no LLM).

    chunk-1 yields 甲方(主体)—提供→平台(软件) + 响应时间(指标, orphan in same
    chunk). The linker connects 响应时间 to 平台 (软件→指标 via 要求, reverse)
    because they co-occur and cosine ≥ threshold. _insert_kg thus receives BOTH
    the original 提供 edge and the inferred 要求 edge."""
    from unittest.mock import AsyncMock, MagicMock

    _PCG = "arrow_lake/knowledge_graph/templates/project_concept_graph.yaml"
    config.he_kg_granularity = "map_reduce"
    config.he_entity_resolution = "off"
    config.he_kg_type_pair = True
    config.he_orphan_linking = "auto"

    extractor = AsyncMock()
    extractor._classifier = None
    extractor._kg_granularity = "map_reduce"
    extractor._resolve_template = MagicMock(return_value=_PCG)

    # Sync embedder (production embed_documents is sync). 响应时间 close to 平台.
    _emb_table = {"响应时间": [1.0, 0.0], "平台": [0.8, 0.6], "甲方": [0.0, 1.0]}
    extractor._embedder = MagicMock()
    extractor._embedder.embed_documents = lambda texts: [
        _emb_table.get(t.strip(), [0.5, 0.5]) for t in texts
    ]

    def _extract(content, *, chunk_id="", doc_type=None):
        if chunk_id == "chunk-1":
            return ExtractionResult(
                (ExtractedEntity("甲方", "主体"),
                 ExtractedEntity("平台", "软件"),
                 ExtractedEntity("响应时间", "指标")),
                (ExtractedRelation("甲方", "平台", "提供"),),
                "",
            )
        return ExtractionResult((), (), "")  # chunk-2 empty

    extractor.extract = AsyncMock(side_effect=_extract)

    builder = KGBuilder(mock_client, extractor, config)
    captured: list = []

    async def _spy(result, graph_name, chunk_id_map, **kw):
        captured.append(result)

    builder._insert_kg = _spy  # type: ignore[method-assign]

    task_id = await builder.build("ds", chunks_table_with_doc_type)
    await builder.execute_build(task_id)

    assert len(captured) == 1
    triples = {(r.source, r.target, r.relation_type) for r in captured[0].relations}
    # original legal relation preserved
    assert ("甲方", "平台", "提供") in triples
    # orphan 响应时间 linked to 平台 (co-occur in chunk-1; 软件→指标 要求, reverse)
    assert ("平台", "响应时间", "要求") in triples


@pytest.mark.asyncio
async def test_execute_build_map_reduce_soft_degrade_keeps_endpoints(
    mock_client: object,
    chunks_table_with_doc_type: pa.Table,
    config: HugeGraphConfig,
) -> None:
    """v1.9.9: an illegal type-pair relation (金额—训练→硬件) is soft-degraded to
    相关 (not dropped) so both endpoints stay connected. he_orphan_linking=off
    isolates the degrade path."""
    from unittest.mock import AsyncMock, MagicMock

    _PCG = "arrow_lake/knowledge_graph/templates/project_concept_graph.yaml"
    config.he_kg_granularity = "map_reduce"
    config.he_entity_resolution = "off"
    config.he_kg_type_pair = True
    config.he_orphan_linking = "off"  # isolate soft-degrade

    extractor = AsyncMock()
    extractor._classifier = None
    extractor._kg_granularity = "map_reduce"
    extractor._resolve_template = MagicMock(return_value=_PCG)

    def _extract(content, *, chunk_id="", doc_type=None):
        if chunk_id == "chunk-1":
            return ExtractionResult(
                (ExtractedEntity("X", "金额"), ExtractedEntity("H", "硬件")),
                (ExtractedRelation("X", "H", "训练"),),  # illegal type-pair
                "",
            )
        return ExtractionResult((), (), "")

    extractor.extract = AsyncMock(side_effect=_extract)

    builder = KGBuilder(mock_client, extractor, config)
    captured: list = []

    async def _spy(result, graph_name, chunk_id_map, **kw):
        captured.append(result)

    builder._insert_kg = _spy  # type: ignore[method-assign]

    task_id = await builder.build("ds", chunks_table_with_doc_type)
    await builder.execute_build(task_id)

    assert len(captured) == 1
    rels = captured[0].relations
    assert len(rels) == 1                     # degraded, NOT dropped
    assert rels[0].relation_type == "相关"    # generic marker
    assert dict(rels[0].properties)["original_relation_type"] == "训练"
