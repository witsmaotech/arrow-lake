"""Tests for GremlinQueries traverser template methods and edge cases.

Covers: all_shortest_paths, weighted_shortest_path, single_source_shortest_path,
multi_node_shortest_path, rays, rings, crosspoints, customized_paths,
_gremlin_escape, and traverse_from_entities edge cases.
"""

from __future__ import annotations

from arrow_lake.knowledge_graph.queries import (
    GremlinQueries,
    _gremlin_escape,
)


# ---------------------------------------------------------------------------
# _gremlin_escape
# ---------------------------------------------------------------------------


class TestGremlinEscape:
    def test_plain_string_unchanged(self) -> None:
        assert _gremlin_escape("Alice") == "Alice"

    def test_backslash_escaped(self) -> None:
        assert _gremlin_escape("a\\b") == "a\\\\b"

    def test_double_quote_escaped(self) -> None:
        assert _gremlin_escape('say "hi"') == 'say \\"hi\\"'

    def test_backslash_and_quote_combined(self) -> None:
        assert _gremlin_escape('a\\"b') == 'a\\\\\\"b'

    def test_empty_string(self) -> None:
        assert _gremlin_escape("") == ""


# ---------------------------------------------------------------------------
# traverse_from_entities — edge case: empty list
# ---------------------------------------------------------------------------


class TestTraverseFromEntitiesEdgeCases:
    def test_empty_list_returns_empty_string(self) -> None:
        result = GremlinQueries.traverse_from_entities([])
        assert result == ""

    def test_single_entity_produces_valid_gremlin(self) -> None:
        result = GremlinQueries.traverse_from_entities(["A"])
        assert "A" in result
        assert "union(" in result or ".union(" in result

    def test_names_with_special_chars_are_escaped(self) -> None:
        result = GremlinQueries.traverse_from_entities(['a"b'])
        assert '\\"' in result


# ---------------------------------------------------------------------------
# all_shortest_paths
# ---------------------------------------------------------------------------


class TestAllShortestPaths:
    def test_basic_query(self) -> None:
        q = GremlinQueries.all_shortest_paths("1:a", "1:b")
        assert "hugegraph.traversal()" in q
        assert 'V("1:a")' in q
        assert 'is("1:b")' in q
        assert "simplePath()" in q
        assert "out()" in q
        assert ".path()" in q

    def test_custom_graph_name(self) -> None:
        q = GremlinQueries.all_shortest_paths("s", "t", graph_name="mygraph")
        assert "mygraph.traversal()" in q

    def test_special_chars_in_ids(self) -> None:
        q = GremlinQueries.all_shortest_paths('a"b', 'c\\d')
        assert '\\"b' in q
        assert "c\\\\d" in q


# ---------------------------------------------------------------------------
# weighted_shortest_path
# ---------------------------------------------------------------------------


class TestWeightedShortestPath:
    def test_basic_query(self) -> None:
        q = GremlinQueries.weighted_shortest_path("1:a", "1:b")
        assert "hugegraph.traversal()" in q
        assert 'V("1:a")' in q
        assert 'is("1:b")' in q
        assert "outE()" in q
        assert "inV()" in q
        assert "simplePath()" in q
        assert ".path()" in q
        assert '.by("weight")' in q

    def test_custom_weight_prop(self) -> None:
        q = GremlinQueries.weighted_shortest_path("a", "b", weight_prop="distance")
        assert "distance" in q

    def test_custom_graph_name(self) -> None:
        q = GremlinQueries.weighted_shortest_path("a", "b", graph_name="g2")
        assert "g2.traversal()" in q


# ---------------------------------------------------------------------------
# single_source_shortest_path
# ---------------------------------------------------------------------------


class TestSingleSourceShortestPath:
    def test_basic_query(self) -> None:
        q = GremlinQueries.single_source_shortest_path("1:a")
        assert "hugegraph.traversal()" in q
        assert 'V("1:a")' in q
        assert "out()" in q
        assert "simplePath()" in q
        assert "emit()" in q
        assert ".path()" in q

    def test_custom_graph_name(self) -> None:
        q = GremlinQueries.single_source_shortest_path("s", graph_name="g1")
        assert "g1.traversal()" in q


# ---------------------------------------------------------------------------
# multi_node_shortest_path
# ---------------------------------------------------------------------------


class TestMultiNodeShortestPath:
    def test_basic_query(self) -> None:
        q = GremlinQueries.multi_node_shortest_path(["a", "b"], ["c"])
        assert "hugegraph.traversal()" in q
        assert '"a"' in q
        assert '"b"' in q
        assert '"c"' in q
        assert "hasId(" in q
        assert "emit(" in q
        assert ".path()" in q

    def test_single_source_and_target(self) -> None:
        q = GremlinQueries.multi_node_shortest_path(["s1"], ["t1"])
        assert '"s1"' in q
        assert '"t1"' in q

    def test_custom_graph_name(self) -> None:
        q = GremlinQueries.multi_node_shortest_path(
            ["a"], ["b"], graph_name="custom"
        )
        assert "custom.traversal()" in q

    def test_ids_with_special_chars(self) -> None:
        q = GremlinQueries.multi_node_shortest_path(['a"b'], ['c\\d'])
        assert '\\"b' in q
        assert "c\\\\d" in q


# ---------------------------------------------------------------------------
# rays
# ---------------------------------------------------------------------------


class TestRays:
    def test_basic_query(self) -> None:
        q = GremlinQueries.rays("1:a")
        assert "hugegraph.traversal()" in q
        assert 'V("1:a")' in q
        assert "out()" in q
        assert "simplePath()" in q
        assert "loops()" in q
        assert "gt(" in q
        assert ".path()" in q

    def test_custom_max_depth(self) -> None:
        q = GremlinQueries.rays("1:a", max_depth=3)
        assert "gt(2)" in q

    def test_max_depth_one(self) -> None:
        q = GremlinQueries.rays("1:a", max_depth=1)
        assert "gt(0)" in q

    def test_custom_graph_name(self) -> None:
        q = GremlinQueries.rays("s", graph_name="g1")
        assert "g1.traversal()" in q


# ---------------------------------------------------------------------------
# rings
# ---------------------------------------------------------------------------


class TestRings:
    def test_basic_query(self) -> None:
        q = GremlinQueries.rings("1:a")
        assert "hugegraph.traversal()" in q
        assert 'V("1:a")' in q
        assert "out()" in q
        assert "simplePath()" in q
        assert "loops()" in q
        assert ".path()" in q
        # Ring returns to source
        assert '.is("1:a")' in q
        assert "gt(0)" in q

    def test_custom_max_depth(self) -> None:
        q = GremlinQueries.rings("1:a", max_depth=10)
        assert 'is("1:a")' in q

    def test_custom_graph_name(self) -> None:
        q = GremlinQueries.rings("s", graph_name="g2")
        assert "g2.traversal()" in q

    def test_source_with_special_chars(self) -> None:
        q = GremlinQueries.rings('a"b')
        assert '\\"b' in q


# ---------------------------------------------------------------------------
# crosspoints
# ---------------------------------------------------------------------------


class TestCrosspoints:
    def test_basic_query(self) -> None:
        q = GremlinQueries.crosspoints("1:a", "1:b")
        assert "hugegraph.traversal()" in q
        assert 'V("1:a")' in q
        assert 'is("1:b")' in q
        assert "out()" in q
        assert "simplePath()" in q
        assert ".path()" in q

    def test_custom_graph_name(self) -> None:
        q = GremlinQueries.crosspoints("s", "t", graph_name="gx")
        assert "gx.traversal()" in q


# ---------------------------------------------------------------------------
# customized_paths
# ---------------------------------------------------------------------------


class TestCustomizedPaths:
    def test_out_direction_default(self) -> None:
        steps = [{"direction": "OUT", "labels": ["knows"]}]
        q = GremlinQueries.customized_paths("1:a", steps)
        assert "out()" in q
        assert 'hasLabel("knows")' in q
        assert ".path()" in q

    def test_in_direction(self) -> None:
        steps = [{"direction": "IN", "labels": ["created"]}]
        q = GremlinQueries.customized_paths("1:a", steps)
        assert "in()" in q
        assert 'hasLabel("created")' in q

    def test_both_direction(self) -> None:
        steps = [{"direction": "BOTH", "labels": ["relates"]}]
        q = GremlinQueries.customized_paths("1:a", steps)
        assert "both()" in q
        assert 'hasLabel("relates")' in q

    def test_unknown_direction_falls_back_to_out(self) -> None:
        steps = [{"direction": "UNKNOWN"}]
        q = GremlinQueries.customized_paths("1:a", steps)
        assert "out()" in q

    def test_step_without_direction_key(self) -> None:
        steps = [{"labels": ["knows"]}]
        q = GremlinQueries.customized_paths("1:a", steps)
        assert "out()" in q
        assert 'hasLabel("knows")' in q

    def test_step_without_labels_key(self) -> None:
        steps = [{"direction": "OUT"}]
        q = GremlinQueries.customized_paths("1:a", steps)
        assert "out()" in q
        assert "hasLabel" not in q

    def test_multiple_steps(self) -> None:
        steps = [
            {"direction": "OUT", "labels": ["knows"]},
            {"direction": "IN", "labels": ["created"]},
            {"direction": "BOTH"},
        ]
        q = GremlinQueries.customized_paths("1:a", steps)
        assert "out()" in q
        assert "in()" in q
        assert "both()" in q
        assert ".path()" in q

    def test_empty_steps_produces_path_only(self) -> None:
        q = GremlinQueries.customized_paths("1:a", [])
        assert ".path()" in q
        assert "out()" not in q
        assert "in()" not in q
        assert "both()" not in q

    def test_custom_graph_name(self) -> None:
        steps = [{"direction": "OUT"}]
        q = GremlinQueries.customized_paths("1:a", steps, graph_name="custom")
        assert "custom.traversal()" in q

    def test_labels_with_special_chars(self) -> None:
        steps = [{"direction": "OUT", "labels": ['a"b']}]
        q = GremlinQueries.customized_paths("1:a", steps)
        assert '\\"b' in q
