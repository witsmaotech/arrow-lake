"""Tests for Gremlin query validation in the KG router (v1.3.0 security hardening)."""

import pytest
from arrow_lake.api.routers.knowledge_graph import _validate_gremlin


class TestGremlinClosureBypass:
    """Closure syntax must be rejected to prevent arbitrary Groovy execution."""

    def test_curly_braces_rejected(self):
        with pytest.raises(Exception) as exc_info:
            _validate_gremlin('g.V().map{it.get().property("name")}')
        assert "Closure" in str(exc_info.value.detail) or "closure" in str(exc_info.value.detail).lower()

    def test_flatmap_closure_rejected(self):
        with pytest.raises(Exception) as exc_info:
            _validate_gremlin('g.V().flatMap{ it.out() }')
        assert "Closure" in str(exc_info.value.detail) or "closure" in str(exc_info.value.detail).lower()

    def test_groovy_template_rejected(self):
        with pytest.raises(Exception):
            _validate_gremlin('g.V(){ ${Runtime.exec("rm -rf /")} }')

    def test_nested_braces_rejected(self):
        with pytest.raises(Exception):
            _validate_gremlin('g.V().map{ def x = { 1 + 2 }; x() }')


class TestGremlinMapFlatMapRemoved:
    """map/flatMap removed from whitelist — even with parentheses they must be blocked."""

    def test_map_parens_rejected(self):
        with pytest.raises(Exception):
            _validate_gremlin('g.V().map(some_lambda)')

    def test_flatmap_parens_rejected(self):
        with pytest.raises(Exception):
            _validate_gremlin('g.V().flatMap(some_lambda)')


class TestGremlinBareMutationBypass:
    """Dangerous steps without parentheses (bare property access) must be caught."""

    def test_bare_drop_rejected(self):
        with pytest.raises(Exception) as exc_info:
            _validate_gremlin('g.V().drop')
        assert "Mutation" in str(exc_info.value.detail) or "forbidden" in str(exc_info.value.detail).lower()

    def test_bare_addV_rejected(self):
        with pytest.raises(Exception):
            _validate_gremlin('g.addV')

    def test_bare_property_rejected(self):
        with pytest.raises(Exception):
            _validate_gremlin('g.V().property')

    def test_bare_delete_rejected(self):
        with pytest.raises(Exception):
            _validate_gremlin('g.V().delete')


class TestGremlinLineCommentStripping:
    """// line comments must be stripped before validation."""

    def test_line_comment_drop_bypass(self):
        with pytest.raises(Exception):
            _validate_gremlin('g.V() // innocent\n.drop()')

    def test_line_comment_preserves_valid_query(self):
        _validate_gremlin('g.V().hasLabel("Person") // just a comment')


class TestGremlinValidQueriesStillPass:
    """Ensure legitimate queries are not broken by the hardening."""

    @pytest.mark.parametrize("query", [
        'hugegraph.traversal().V().count()',
        'hugegraph.traversal().V().hasLabel("Person").values("name")',
        'hugegraph.traversal().V("v1").repeat(out()).simplePath().times(2)',
        'hugegraph.traversal().V().groupCount().by(label)',
        'hugegraph.traversal().V().has("name",eq("Alice")).out("knows").path()',
    ])
    def test_valid_queries_pass(self, query):
        _validate_gremlin(query)  # should not raise
