"""TemplateGallery structured detail (v1.8.8 改动2).

Covers the ``TemplateInfo`` structured fields (``description_zh`` /
``description_en`` / ``entity_fields`` / ``relation_fields`` / ``guideline_*``),
the ``is_high_risk`` flag, and the ``get`` / ``describe`` accessors — the data
behind ``kg list-templates`` / ``kg describe-template``.
"""

from __future__ import annotations

import pytest

from arrow_lake.knowledge_graph.doc_type_router import TemplateGallery

# The four hypergraph presets known to crash / 0-entity on sparse content.
_HIGH_RISK_PATHS = {
    "tcm/formula_composition",
    "tcm/syndrome_reasoning",
    "medicine/treatment_map",
    "legal/contract_obligation",
}


@pytest.fixture(scope="module")
def gallery() -> TemplateGallery:
    g = TemplateGallery.build()
    if not g.templates:  # hyperextract not installed
        pytest.skip("hyperextract not installed")
    return g


class TestTemplateCount:
    def test_indexes_all_non_base_presets(self, gallery: TemplateGallery) -> None:
        # 6 categories × ~5 templates; tolerate preset additions, not removals.
        assert len(gallery.templates) >= 26

    def test_no_base_presets(self, gallery: TemplateGallery) -> None:
        for t in gallery.templates:
            assert not t.name.startswith("base_"), f"base preset leaked: {t.path}"


class TestStructuredDetail:
    def test_all_have_bilingual_descriptions(self, gallery: TemplateGallery) -> None:
        for t in gallery.templates:
            assert t.description_zh, f"{t.path} missing description_zh"
            assert t.description_en, f"{t.path} missing description_en"

    def test_all_have_entity_fields(self, gallery: TemplateGallery) -> None:
        for t in gallery.templates:
            assert t.entity_fields, f"{t.path} has no entity_fields"

    def test_concept_graph_fields(self, gallery: TemplateGallery) -> None:
        t = gallery.get("general/concept_graph")
        assert t is not None
        assert set(t.entity_fields) >= {"name", "type", "definition"}
        assert set(t.relation_fields) >= {"source", "target", "type"}
        assert t.guideline_zh and t.guideline_en


class TestHighRiskFlag:
    def test_hypergraph_templates_flagged(self, gallery: TemplateGallery) -> None:
        flagged = {t.path for t in gallery.templates if t.is_high_risk}
        assert _HIGH_RISK_PATHS <= flagged

    def test_non_hypergraph_not_flagged(self, gallery: TemplateGallery) -> None:
        for t in gallery.templates:
            assert t.is_high_risk == (t.type == "hypergraph")

    def test_only_known_hypergraphs_flagged(self, gallery: TemplateGallery) -> None:
        # Guard against an unexpected new hypergraph preset sneaking in
        # unflagged — if one appears, extend the known set explicitly.
        flagged = {t.path for t in gallery.templates if t.is_high_risk}
        assert flagged == {
            t.path for t in gallery.templates if t.type == "hypergraph"
        }


class TestSummaryAndDetail:
    def test_to_summary_shape(self, gallery: TemplateGallery) -> None:
        t = gallery.get("general/concept_graph")
        assert t is not None
        s = t.to_summary()
        assert set(s) == {
            "path", "category", "name", "type", "tags",
            "is_high_risk", "description_zh", "description_en",
        }

    def test_to_detail_shape(self, gallery: TemplateGallery) -> None:
        t = gallery.get("general/concept_graph")
        assert t is not None
        d = t.to_detail()
        assert set(d) >= {
            "path", "category", "name", "type", "tags", "is_high_risk",
            "description_zh", "description_en",
            "entity_fields", "relation_fields", "guideline_zh", "guideline_en",
        }


class TestGalleryAccessors:
    def test_get_hit(self, gallery: TemplateGallery) -> None:
        t = gallery.get("general/concept_graph")
        assert t is not None
        assert t.path == "general/concept_graph"

    def test_get_miss(self, gallery: TemplateGallery) -> None:
        assert gallery.get("no/such-template") is None

    def test_describe_hit(self, gallery: TemplateGallery) -> None:
        d = gallery.describe("general/concept_graph")
        assert d is not None
        assert d["path"] == "general/concept_graph"
        assert "entity_fields" in d and "relation_fields" in d

    def test_describe_miss(self, gallery: TemplateGallery) -> None:
        assert gallery.describe("no/such-template") is None
