"""Unit tests for GremlinQueries static query templates."""

from __future__ import annotations

from arrow_lake.knowledge_graph.queries import GremlinQueries

# ---------------------------------------------------------------------------
# find_entity
# ---------------------------------------------------------------------------


class TestFindEntity:
    def test_with_name_only(self) -> None:
        query = GremlinQueries.find_entity("Alice", graph_name="hugegraph")
        assert "hugegraph.traversal()" in query
        assert 'has("name",eq("Alice"))' in query

    def test_with_entity_type(self) -> None:
        query = GremlinQueries.find_entity("Alice", entity_type="person", graph_name="g1")
        assert 'hasLabel("person")' in query
        assert 'has("name",eq("Alice"))' in query

    def test_without_entity_type(self) -> None:
        query = GremlinQueries.find_entity("Alice", entity_type="", graph_name="g1")
        assert "hasLabel" not in query
        assert 'has("name",eq("Alice"))' in query

    def test_uses_eq_not_text_contains(self) -> None:
        query = GremlinQueries.find_entity("test", graph_name="g1")
        assert "textContains" not in query
        assert "eq(" in query


# ---------------------------------------------------------------------------
# get_neighbors
# ---------------------------------------------------------------------------


class TestGetNeighbors:
    def test_default_depth(self) -> None:
        query = GremlinQueries.get_neighbors("20001:marko", graph_name="hugegraph")
        assert "hugegraph.traversal()" in query
        assert 'V("20001:marko")' in query
        assert ".times(2)" in query
        assert "out()" in query

    def test_custom_depth(self) -> None:
        query = GremlinQueries.get_neighbors("20001:marko", depth=3, graph_name="g1")
        assert ".times(3)" in query

    def test_uses_simple_path(self) -> None:
        query = GremlinQueries.get_neighbors("20001:marko", graph_name="g1")
        assert "simplePath()" in query


# ---------------------------------------------------------------------------
# shortest_path
# ---------------------------------------------------------------------------


class TestShortestPath:
    def test_basic(self) -> None:
        query = GremlinQueries.shortest_path("20001:marko", "20002:peter", graph_name="hugegraph")
        assert "hugegraph.traversal()" in query
        assert 'V("20001:marko")' in query
        assert 'is("20002:peter")' in query
        assert "until(" in query
        assert "out()" in query
        assert "path()" in query

    def test_different_graph_name(self) -> None:
        query = GremlinQueries.shortest_path("a", "b", graph_name="my_graph")
        assert "my_graph.traversal()" in query


# ---------------------------------------------------------------------------
# get_subgraph
# ---------------------------------------------------------------------------


class TestGetSubgraph:
    def test_basic(self) -> None:
        query = GremlinQueries.get_subgraph("20001:marko", graph_name="hugegraph")
        assert "hugegraph.traversal()" in query
        assert 'V("20001:marko")' in query
        assert "both()" in query
        assert ".times(2)" in query
        assert "simplePath()" in query

    def test_custom_radius(self) -> None:
        query = GremlinQueries.get_subgraph("20001:marko", radius=3, graph_name="g1")
        assert ".times(3)" in query


# ---------------------------------------------------------------------------
# entity_type_counts
# ---------------------------------------------------------------------------


class TestEntityTypeCounts:
    def test_basic(self) -> None:
        query = GremlinQueries.entity_type_counts("hugegraph")
        assert "hugegraph.traversal()" in query
        assert "V()" in query
        assert "groupCount()" in query
        assert "by(label)" in query


# ---------------------------------------------------------------------------
# traverse_from_entities (GraphRAG)
# ---------------------------------------------------------------------------


class TestTraverseFromEntities:
    def test_single_entity(self) -> None:
        query = GremlinQueries.traverse_from_entities(
            ["Alice"], depth=2, graph_name="hugegraph"
        )
        assert 'has("name",eq("Alice"))' in query
        assert ".times(2)" in query
        assert "out()" in query

    def test_multiple_entities(self) -> None:
        query = GremlinQueries.traverse_from_entities(
            ["Alice", "Bob"], depth=1, graph_name="hugegraph"
        )
        assert 'has("name",eq("Alice"))' in query
        assert 'has("name",eq("Bob"))' in query

    def test_custom_depth(self) -> None:
        query = GremlinQueries.traverse_from_entities(
            ["Alice"], depth=3, graph_name="hugegraph"
        )
        assert ".times(3)" in query

    def test_uses_union_for_multiple(self) -> None:
        query = GremlinQueries.traverse_from_entities(
            ["Alice", "Bob"], graph_name="hugegraph"
        )
        assert "union(" in query or ".union(" in query
