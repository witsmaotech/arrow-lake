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
    """A matching checkpoint (total_chunks + template + content sig) skips MAP:
    extractor.extract is NOT called; the checkpoint's merged result is inserted."""
    import hashlib
    import json
    from pathlib import Path
    from unittest.mock import AsyncMock, MagicMock

    from arrow_lake.knowledge_graph._naming import artifact_key_for

    config.he_kg_granularity = "map_reduce"
    config.he_entity_resolution = "off"
    config.he_kg_type_pair = False
    config.he_ka_base_dir = str(tmp_path)

    extractor = AsyncMock()
    extractor._classifier = None
    extractor._kg_granularity = "map_reduce"
    extractor._resolve_template = MagicMock(return_value="entity_graph")
    extractor.extract = AsyncMock()  # must NOT be awaited on resume

    contents = [str(c) for c in chunks_table_with_doc_type.column("content").to_pylist()]
    sig = hashlib.sha1(
        (contents[0] + "\n" + contents[-1]).encode("utf-8", "replace")
    ).hexdigest()[:16]
    ckpt = Path(tmp_path) / artifact_key_for("ds") / "map_reduce.json"
    ckpt.parent.mkdir(parents=True, exist_ok=True)
    ckpt.write_text(json.dumps({
        "total_chunks": chunks_table_with_doc_type.num_rows,
        "template": "entity_graph",
        "sig": sig,
        "entities": [{"name": "X", "type": "t", "properties": []}],
        "relations": [],
        "entity_chunks": {"X": ["chunk-1"]},
    }), encoding="utf-8")

    builder = KGBuilder(mock_client, extractor, config)
    builder._ka_base_dir = Path(tmp_path)
    inserted: list = []

    async def _spy(result, graph_name, chunk_id_map, **kw):
        inserted.append(result)

    builder._insert_kg = _spy  # type: ignore[method-assign]

    task_id = await builder.build("ds", chunks_table_with_doc_type)
    await builder.execute_build(task_id)

    extractor.extract.assert_not_awaited()        # MAP skipped
    assert len(inserted) == 1
    assert {e.name for e in inserted[0].entities} == {"X"}  # checkpoint's entity


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
