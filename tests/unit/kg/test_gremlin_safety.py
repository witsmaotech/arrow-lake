"""Tests for Gremlin injection prevention (Round 4 — C1 fix)."""


from arrow_lake.knowledge_graph.client import _BLOCKED_GREMLIN_PATTERNS


class TestBlockedGremlinPatterns:
    """Verify all dangerous Gremlin patterns are blocked."""

    def test_core_patterns_exist(self):
        assert "drop(" in _BLOCKED_GREMLIN_PATTERNS
        assert "eval(" in _BLOCKED_GREMLIN_PATTERNS
        assert "System." in _BLOCKED_GREMLIN_PATTERNS
        assert "java.lang" in _BLOCKED_GREMLIN_PATTERNS

    def test_os_command_patterns_exist(self):
        assert "ProcessBuilder" in _BLOCKED_GREMLIN_PATTERNS
        assert "Runtime." in _BLOCKED_GREMLIN_PATTERNS
        assert "Exec(" in _BLOCKED_GREMLIN_PATTERNS
        assert "GroovyShell" in _BLOCKED_GREMLIN_PATTERNS

    def test_round1_added_patterns(self):
        assert "groovy." in _BLOCKED_GREMLIN_PATTERNS
        assert "script(" in _BLOCKED_GREMLIN_PATTERNS
        assert "ExecTransformer" in _BLOCKED_GREMLIN_PATTERNS
        assert "org.apache" in _BLOCKED_GREMLIN_PATTERNS

    def test_round1_security_patterns(self):
        assert "new File(" in _BLOCKED_GREMLIN_PATTERNS
        assert "Class.forName" in _BLOCKED_GREMLIN_PATTERNS
        assert "inject(" in _BLOCKED_GREMLIN_PATTERNS
        assert "AccessController" in _BLOCKED_GREMLIN_PATTERNS

    def test_total_pattern_count(self):
        assert len(_BLOCKED_GREMLIN_PATTERNS) >= 16

    def test_is_tuple(self):
        assert isinstance(_BLOCKED_GREMLIN_PATTERNS, tuple)
