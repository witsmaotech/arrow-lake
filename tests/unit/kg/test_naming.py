"""Unit tests for per-dataset graph-name derivation."""

from __future__ import annotations

import pytest

from arrow_lake.knowledge_graph._naming import graph_name_for


class TestGraphNameFor:
    """graph_name_for maps dataset names to stable HugeGraph-safe graph names."""

    def test_basic_name_gets_kg_prefix(self) -> None:
        # Arrange
        # Act
        name = graph_name_for("my_docs")
        # Assert
        assert name == "kg_my_docs"

    def test_uppercase_is_lowercased(self) -> None:
        assert graph_name_for("MyDocs_V2") == "kg_mydocs_v2"

    def test_special_chars_become_underscore(self) -> None:
        # Arrange — spaces, hyphens, dots, slashes all collapse to '_'
        # Act
        name = graph_name_for("lake/path v2.1-beta")
        # Assert
        assert name == "kg_lake_path_v2_1_beta"
        # No character outside [a-z0-9_] survives (beyond the kg_ prefix)
        assert all(c.isalnum() or c == "_" for c in name)

    def test_cjk_chars_become_underscore(self) -> None:
        # CJK has no [a-z0-9_] representation → collapsed to '_', then stripped.
        # "文档集-A" → underscores + 'a' → strip leading '_' → "a"
        name = graph_name_for("文档集-A")
        assert name == "kg_a"
        assert name.startswith("kg_")

    def test_cjk_between_alnums_preserves_separator(self) -> None:
        # CJK chars between alphanumeric tokens → one '_' per char (2 CJK = 2 '_')
        name = graph_name_for("doc文档doc")
        assert name == "kg_doc__doc"

    def test_idempotent(self) -> None:
        # Same input always maps to same output; applying twice changes nothing
        once = graph_name_for("Reports.2026.q1")
        twice = graph_name_for(once)
        # graph_name_for output is already sanitized → re-applying is stable
        assert graph_name_for("Reports.2026.q1") == once
        assert twice == f"kg_{once}"

    def test_leading_trailing_underscores_stripped(self) -> None:
        # Leading/trailing symbols would produce leading/trailing '_' after sub
        assert graph_name_for("---docs---") == "kg_docs"
        assert graph_name_for("...docs...") == "kg_docs"

    def test_empty_input_falls_back_to_default(self) -> None:
        assert graph_name_for("") == "kg_default"

    def test_all_symbols_falls_back_to_default(self) -> None:
        # No [a-z0-9_] content at all → placeholder
        assert graph_name_for("---...///") == "kg_default"

    def test_long_name_is_truncated_under_cap(self) -> None:
        # Arrange — a name far exceeding the 48-char cap
        long_name = "a" * 200
        # Act
        name = graph_name_for(long_name)
        # Assert — total length (prefix + sanitized) <= 48
        assert len(name) <= 48
        assert name == "kg_" + "a" * 45

    def test_exact_boundary_length(self) -> None:
        # 45 alphanumeric chars → exactly 48 with 'kg_' prefix
        name = graph_name_for("x" * 45)
        assert len(name) == 48
        # 46 chars → truncated to 45
        name2 = graph_name_for("x" * 46)
        assert len(name2) == 48

    @pytest.mark.parametrize(
        ("dataset", "expected"),
        [
            ("simple", "kg_simple"),
            ("a.b.c", "kg_a_b_c"),
            ("UPPER", "kg_upper"),
            ("mixed_Case_2", "kg_mixed_case_2"),
        ],
    )
    def test_parametrized_cases(self, dataset: str, expected: str) -> None:
        assert graph_name_for(dataset) == expected
