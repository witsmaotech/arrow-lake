"""Unit tests for KGRetriever (mock HugeGraphClient)."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from arrow_lake.config import HugeGraphConfig
from arrow_lake.knowledge_graph.retriever import (
    GraphRetrievalResult,
    GraphTriplet,
    KGRetriever,
)

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
        default_traversal_depth=2,
        max_traversal_depth=5,
    )


@pytest.fixture
def mock_client(config: HugeGraphConfig) -> AsyncMock:
    """Create a mock HugeGraphClient."""
    client = AsyncMock()
    # get_vertex returns vertex dict when found (label 3 = entity)
    client.get_vertex.return_value = {
        "id": "3:Alice",
        "label": "entity",
        "properties": {"name": "Alice"},
    }
    # traverser_kneighbor returns list of neighbor dicts
    client.traverser_kneighbor.return_value = [
        {
            "id": "20002:Bob",
            "label": "person",
            "properties": {"name": "Bob"},
        },
        {
            "id": "20003:Acme Corp",
            "label": "organization",
            "properties": {"name": "Acme Corp"},
        },
    ]
    return client


# ---------------------------------------------------------------------------
# GraphTriplet frozen dataclass
# ---------------------------------------------------------------------------


class TestGraphTriplet:
    def test_is_frozen(self) -> None:
        triplet = GraphTriplet(subject="Alice", predicate="works_at", object_="Acme Corp")
        with pytest.raises(AttributeError):
            triplet.subject = "Bob"  # type: ignore[misc]

    def test_default_properties_empty(self) -> None:
        triplet = GraphTriplet(subject="A", predicate="p", object_="B")
        assert triplet.properties == ()


# ---------------------------------------------------------------------------
# GraphRetrievalResult frozen dataclass
# ---------------------------------------------------------------------------


class TestGraphRetrievalResult:
    def test_is_frozen(self) -> None:
        result = GraphRetrievalResult(
            query_entities=("Alice",),
            triplets=(),
            traversal_depth=2,
            vertex_count=0,
            edge_count=0,
        )
        with pytest.raises(AttributeError):
            result.vertex_count = 1  # type: ignore[misc]


# ---------------------------------------------------------------------------
# KGRetriever.retrieve
# ---------------------------------------------------------------------------


class TestKGRetrieverRetrieve:
    @pytest.mark.asyncio
    async def test_empty_entities_returns_empty_result(
        self, mock_client: AsyncMock, config: HugeGraphConfig
    ) -> None:
        retriever = KGRetriever(mock_client, config)
        result = await retriever.retrieve("some question")
        assert result.query_entities == ()
        assert result.triplets == ()
        assert result.vertex_count == 0
        assert result.edge_count == 0

    @pytest.mark.asyncio
    async def test_none_entities_returns_empty_result(
        self, mock_client: AsyncMock, config: HugeGraphConfig
    ) -> None:
        retriever = KGRetriever(mock_client, config)
        result = await retriever.retrieve("some question", extracted_entities=None)
        assert result.triplets == ()

    @pytest.mark.asyncio
    async def test_single_entity_returns_triplets(
        self, mock_client: AsyncMock, config: HugeGraphConfig
    ) -> None:
        retriever = KGRetriever(mock_client, config)
        result = await retriever.retrieve(
            "Who does Alice know?", extracted_entities=["Alice"]
        )
        assert result.query_entities == ("Alice",)
        assert result.traversal_depth == 2
        assert len(result.triplets) > 0
        # Verify get_vertex was called to look up entity
        mock_client.get_vertex.assert_called()
        # Verify traverser_kneighbor was called
        mock_client.traverser_kneighbor.assert_called_once()

    @pytest.mark.asyncio
    async def test_custom_traversal_depth(
        self, mock_client: AsyncMock, config: HugeGraphConfig
    ) -> None:
        retriever = KGRetriever(mock_client, config)
        result = await retriever.retrieve(
            "query", extracted_entities=["Alice"], traversal_depth=3
        )
        assert result.traversal_depth == 3
        # traverser_kneighbor should receive depth=3
        call_kwargs = mock_client.traverser_kneighbor.call_args[1]
        assert call_kwargs["depth"] == 3

    @pytest.mark.asyncio
    async def test_max_triplets_truncation(
        self, mock_client: AsyncMock, config: HugeGraphConfig
    ) -> None:
        # Return many neighbors to trigger truncation
        mock_client.traverser_kneighbor.return_value = [
            {"id": f"2000{i}:item{i}", "label": "thing", "properties": {"name": f"item{i}"}}
            for i in range(100)
        ]
        retriever = KGRetriever(mock_client, config)
        result = await retriever.retrieve(
            "query", extracted_entities=["Alice"], max_triplets=5
        )
        assert len(result.triplets) <= 5

    @pytest.mark.asyncio
    async def test_entity_not_found_skipped(
        self, mock_client: AsyncMock, config: HugeGraphConfig
    ) -> None:
        # get_vertex returns None for "Unknown", vertex dict for "Alice"
        call_count = 0

        async def get_vertex_side_effect(vertex_id: str, **kwargs):
            nonlocal call_count
            call_count += 1
            if "Unknown" in vertex_id:
                return None  # not found for any label prefix
            return {"id": "3:Alice", "label": "entity", "properties": {"name": "Alice"}}

        mock_client.get_vertex.side_effect = get_vertex_side_effect
        retriever = KGRetriever(mock_client, config)
        result = await retriever.retrieve(
            "query", extracted_entities=["Unknown", "Alice"]
        )
        assert result.query_entities == ("Unknown", "Alice")
        # Only Alice should be used for traversal
        mock_client.traverser_kneighbor.assert_called_once()


# ---------------------------------------------------------------------------
# KGRetriever.triplets_to_text
# ---------------------------------------------------------------------------


class TestV1911EdgeRelationAndCharOverlap:
    """v1.9.11 #1 (edge relation_type) + #2 (char-overlap fallback)."""

    def test_char_overlap_exact_hit(self) -> None:
        snap = [{"id": "3:A", "properties": {"name": "应急指挥中心"}}]
        assert KGRetriever._char_overlap_vertex("应急指挥中心", snap)["id"] == "3:A"

    def test_char_overlap_paraphrase_hit(self) -> None:
        # "市应急指挥中心" vs "应急指挥中心": 6/6 candidate chars overlap → hit
        snap = [{"id": "3:A", "properties": {"name": "应急指挥中心"}}]
        assert KGRetriever._char_overlap_vertex("市应急指挥中心", snap)["id"] == "3:A"

    def test_char_overlap_no_match(self) -> None:
        snap = [{"id": "3:B", "properties": {"name": "完全无关的另一实体"}}]
        assert KGRetriever._char_overlap_vertex("响应时间", snap) is None

    def test_char_overlap_empty(self) -> None:
        assert KGRetriever._char_overlap_vertex("A", []) is None

    def test_retrieve_uses_edge_relation_type(self) -> None:
        import asyncio
        from types import SimpleNamespace
        cfg = SimpleNamespace(default_traversal_depth=1, max_traversal_depth=2)
        client = AsyncMock()
        client.get_vertex.return_value = {
            "id": "3:resp", "label": "entity", "properties": {"name": "响应时间"},
        }
        client.traverser_kneighbor.return_value = [
            {"id": "3:sys", "label": "entity", "properties": {"name": "业务系统"}},
        ]
        client.get_vertex_edges.return_value = [
            {"inV": "3:sys", "label": "requires", "properties": {"relation_type": "要求"}},
        ]
        client.get_graph_snapshot.return_value = ([], [])
        ret = KGRetriever(client, cfg)
        result = asyncio.run(
            ret.retrieve("q", extracted_entities=["响应时间"], dataset_name="ds")
        )
        assert len(result.triplets) == 1
        assert result.triplets[0].predicate == "要求"  # relation_type, not related_to_entity
        assert result.triplets[0].object_ == "业务系统"


class TestTripletsToText:
    def test_single_triplet(self) -> None:
        retriever = KGRetriever(AsyncMock(), HugeGraphConfig(enabled=True))
        result = GraphRetrievalResult(
            query_entities=("Alice",),
            triplets=(
                GraphTriplet(subject="Alice", predicate="works_at", object_="Acme Corp"),
            ),
            traversal_depth=2,
            vertex_count=2,
            edge_count=1,
        )
        text = retriever.triplets_to_text(result)
        assert "Alice --works_at--> Acme Corp" in text

    def test_multiple_triplets(self) -> None:
        retriever = KGRetriever(AsyncMock(), HugeGraphConfig(enabled=True))
        result = GraphRetrievalResult(
            query_entities=("Alice",),
            triplets=(
                GraphTriplet(subject="Alice", predicate="works_at", object_="Acme Corp"),
                GraphTriplet(subject="Alice", predicate="knows", object_="Bob"),
            ),
            traversal_depth=2,
            vertex_count=3,
            edge_count=2,
        )
        text = retriever.triplets_to_text(result)
        lines = [line for line in text.strip().split("\n") if line.strip()]
        assert len(lines) == 2

    def test_empty_triplets(self) -> None:
        retriever = KGRetriever(AsyncMock(), HugeGraphConfig(enabled=True))
        result = GraphRetrievalResult(
            query_entities=(),
            triplets=(),
            traversal_depth=0,
            vertex_count=0,
            edge_count=0,
        )
        text = retriever.triplets_to_text(result)
        assert text.strip() == ""
