"""Integration tests for the hardened doc_type → template routing (v1.7.x).

Covers: normalize_doc_type (aliases/case/zh/empty), TemplateGallery metadata
indexing + match (tag/category/name/description), DocTypeRouter 3-layer
precedence (override > gallery > default), observability, backward compat.
"""

from __future__ import annotations

import pytest

from arrow_lake.knowledge_graph.doc_type_router import (
    DOC_TYPE_ALIASES,
    DOC_TYPE_DESCRIPTIONS,
    DocTypeClassifier,
    DocTypeRouter,
    TemplateGallery,
    TemplateInfo,
    normalize_doc_type,
    validate_taxonomy,
)


# --- normalize_doc_type -----------------------------------------------------


class TestNormalizeDocType:
    def test_none_and_empty(self) -> None:
        assert normalize_doc_type(None) is None
        assert normalize_doc_type("") is None
        assert normalize_doc_type("   ") is None

    def test_case_and_strip(self) -> None:
        assert normalize_doc_type("Paper") == "paper"
        assert normalize_doc_type("  PAPER  ") == "paper"

    @pytest.mark.parametrize(
        "raw,canonical",
        [
            ("research_paper", "paper"),
            ("research-paper", "paper"),
            ("论文", "paper"),
            ("学术论文", "paper"),
            ("whitepaper", "report"),
            ("白皮书", "report"),
            ("tutorial", "manual"),
            ("财报", "finance"),
            ("中医", "tcm"),
            ("financial", "finance"),  # old canonical now alias of finance
            ("medical", "medicine"),   # old canonical now alias of medicine
        ],
    )
    def test_alias_collapse(self, raw: str, canonical: str) -> None:
        assert normalize_doc_type(raw) == canonical

    def test_unknown_preserved(self) -> None:
        assert normalize_doc_type("novel") == "novel"


# --- TemplateGallery --------------------------------------------------------


@pytest.fixture(scope="module")
def gallery() -> TemplateGallery:
    g = TemplateGallery.build()
    if not g.templates:  # hyperextract not installed
        pytest.skip("hyperextract not installed")
    return g


class TestTemplateGallery:
    def test_indexes_all_presets(self, gallery: TemplateGallery) -> None:
        assert len(gallery.templates) >= 25
        cats = {t.category for t in gallery.templates}
        assert {"general", "finance", "industry", "legal", "medicine", "tcm"} <= cats

    def test_tag_match(self, gallery: TemplateGallery) -> None:
        hit = gallery.match("concept")
        assert hit is not None
        assert "concept" in hit.tags

    def test_category_match(self, gallery: TemplateGallery) -> None:
        hit = gallery.match("finance")
        assert hit is not None
        assert hit.category == "finance"

    def test_name_substring_match(self, gallery: TemplateGallery) -> None:
        hit = gallery.match("workflow")
        assert hit is not None
        assert "workflow" in hit.name

    def test_description_keyword_match(self, gallery: TemplateGallery) -> None:
        # "paper" is not a tag but appears in concept_graph's description
        hit = gallery.match("paper")
        assert hit is not None
        assert hit.path == "general/concept_graph"

    def test_no_match_returns_none(self, gallery: TemplateGallery) -> None:
        assert gallery.match("xyzzy-nonsense-12345") is None


# --- DocTypeRouter 3-layer precedence ---------------------------------------


@pytest.fixture(scope="module")
def fresh_gallery() -> TemplateGallery:
    g = TemplateGallery.build()
    if not g.templates:
        pytest.skip("hyperextract not installed")
    return g


class TestDocTypeRouterPrecedence:
    def test_override_beats_gallery(self, fresh_gallery: TemplateGallery) -> None:
        r = DocTypeRouter(
            {"paper": "general/base_graph"},
            "general/base_graph",
            gallery=fresh_gallery,
        )
        assert r.resolve("paper") == "general/base_graph"
        assert r.resolve_with_source("paper") == ("general/base_graph", "override")

    def test_gallery_match_when_no_override(
        self, fresh_gallery: TemplateGallery
    ) -> None:
        r = DocTypeRouter({}, "general/base_graph", gallery=fresh_gallery)
        path, source = r.resolve_with_source("concept")
        assert path == "general/concept_graph"
        assert source == "gallery"

    def test_default_when_no_match(self, fresh_gallery: TemplateGallery) -> None:
        r = DocTypeRouter({}, "general/base_graph", gallery=fresh_gallery)
        assert r.resolve("xyzzy-nonsense-12345") == "general/base_graph"
        assert r.resolve_with_source("xyzzy-nonsense-12345")[1] == "default"

    def test_none_doc_type_uses_default(
        self, fresh_gallery: TemplateGallery
    ) -> None:
        r = DocTypeRouter(
            {"paper": "general/concept_graph"},
            "general/base_graph",
            gallery=fresh_gallery,
        )
        assert r.resolve(None) == "general/base_graph"
        assert r.resolve_with_source(None) == ("general/base_graph", "default")

    def test_normalization_before_override(
        self, fresh_gallery: TemplateGallery
    ) -> None:
        # alias "论文" → "paper" before override lookup
        r = DocTypeRouter(
            {"paper": "general/concept_graph"},
            "general/base_graph",
            gallery=fresh_gallery,
        )
        assert r.resolve("论文") == "general/concept_graph"
        assert r.resolve_with_source("论文")[1] == "override"

    def test_normalization_before_gallery(
        self, fresh_gallery: TemplateGallery
    ) -> None:
        # alias "学术论文" → "paper" → gallery description match → concept_graph
        r = DocTypeRouter({}, "general/base_graph", gallery=fresh_gallery)
        assert r.resolve("学术论文") == "general/concept_graph"


# --- hypergraph auto-degradation (v1.8.8 改动1) -----------------------------


class TestHypergraphDegradation:
    """Auto-routed doc_types that hit a high-risk (hypergraph) template degrade
    to the default; explicit overrides to hypergraph are preserved."""

    DEFAULT = "general/concept_graph"

    @staticmethod
    def _synthetic_gallery() -> TemplateGallery:
        # A hypergraph template a classifier could misroute to (via tag match),
        # plus a normal graph template as the default.
        hyper = TemplateInfo(
            path="tcm/formula_composition",
            category="tcm",
            name="formula_composition",
            type="hypergraph",
            tags=("formula", "composition"),
            description="formula composition hypergraph",
        )
        graph = TemplateInfo(
            path="general/concept_graph",
            category="general",
            name="concept_graph",
            type="graph",
            tags=("general", "concept"),
            description="concept graph",
        )
        return TemplateGallery(templates=[graph, hyper])

    def test_auto_hypergraph_match_degrades(self) -> None:
        r = DocTypeRouter({}, self.DEFAULT, gallery=self._synthetic_gallery())
        # "formula" tag-matches the hypergraph template → degrade to default
        path, source = r.resolve_with_source("formula")
        assert path == self.DEFAULT
        assert source == "degraded"

    def test_auto_graph_match_not_degraded(self) -> None:
        r = DocTypeRouter({}, self.DEFAULT, gallery=self._synthetic_gallery())
        # "concept" tag-matches the graph template → stays (source=gallery)
        path, source = r.resolve_with_source("concept")
        assert path == "general/concept_graph"
        assert source == "gallery"

    def test_override_hypergraph_preserved(self) -> None:
        # operator explicitly forces the hypergraph template — NOT degraded
        r = DocTypeRouter(
            {"tcm": "tcm/formula_composition"},
            self.DEFAULT,
            gallery=self._synthetic_gallery(),
        )
        path, source = r.resolve_with_source("tcm")
        assert path == "tcm/formula_composition"
        assert source == "override"

    def test_default_branch_unaffected(self) -> None:
        r = DocTypeRouter({}, self.DEFAULT, gallery=self._synthetic_gallery())
        # no match → default, unaffected by degradation logic
        path, source = r.resolve_with_source("zzz-no-such-tag")
        assert path == self.DEFAULT
        assert source == "default"

    def test_resolve_returns_path_not_degraded_marker(self) -> None:
        # resolve() must return a usable template path even when degraded
        r = DocTypeRouter({}, self.DEFAULT, gallery=self._synthetic_gallery())
        assert r.resolve("formula") == self.DEFAULT

    def test_real_gallery_never_auto_resolves_to_hypergraph(
        self, fresh_gallery: TemplateGallery
    ) -> None:
        # The core safety invariant: the auto (gallery) layer must never hand
        # back a high-risk hypergraph path for any canonical doc_type — those
        # crash / yield 0 entities on sparse content.
        hyper_paths = {t.path for t in fresh_gallery.templates if t.is_high_risk}
        r = DocTypeRouter({}, self.DEFAULT, gallery=fresh_gallery)
        for dt in DOC_TYPE_DESCRIPTIONS:
            path, source = r.resolve_with_source(dt)
            if source == "gallery":
                assert path not in hyper_paths, (
                    f"{dt} auto-resolved to high-risk template {path}"
                )


# --- backward compat --------------------------------------------------------


class TestBackwardCompat:
    def test_legacy_signature_works(self) -> None:
        r = DocTypeRouter({"paper": "general/concept_graph"}, "general/base_graph")
        assert r.resolve("paper") == "general/concept_graph"
        assert r.resolve(None) == "general/base_graph"

    def test_resolve_return_type_unchanged(self) -> None:
        r = DocTypeRouter({}, "general/base_graph")
        assert isinstance(r.resolve("anything"), str)


# --- P3: DocTypeClassifier (content-based inference) ------------------------


def _mock_complete(label: str):
    """Build an async llm_complete callable that always returns ``label``."""

    async def _complete(system: str, user: str) -> str:
        return label

    return _complete


class TestDocTypeClassifier:
    def test_infers_canonical_label(self) -> None:
        c = DocTypeClassifier(_mock_complete("paper"))
        import asyncio

        assert asyncio.run(c.classify("some text about vectors")) == "paper"

    def test_tolerates_punctuation_and_extras(self) -> None:
        import asyncio

        for raw in ("Paper.", " paper ", "PAPER:", "paper is best"):
            c = DocTypeClassifier(_mock_complete(raw))
            assert asyncio.run(c.classify("text")) == "paper", raw

    def test_alias_label_collapses(self) -> None:
        # LLM returns an alias-ish token; normalize_doc_type maps "论文" -> "paper"
        import asyncio

        c = DocTypeClassifier(_mock_complete("论文"))
        assert asyncio.run(c.classify("text")) == "paper"

    def test_empty_text_returns_none(self) -> None:
        import asyncio

        c = DocTypeClassifier(_mock_complete("paper"))
        assert asyncio.run(c.classify("")) is None
        assert asyncio.run(c.classify("   ")) is None

    def test_unknown_label_returns_none(self) -> None:
        import asyncio

        c = DocTypeClassifier(_mock_complete("cookbook-recipe"))
        assert asyncio.run(c.classify("text")) is None

    def test_llm_exception_returns_none(self) -> None:
        async def _boom(system: str, user: str) -> str:
            raise RuntimeError("LLM down")

        import asyncio

        c = DocTypeClassifier(_boom)
        assert asyncio.run(c.classify("text")) is None  # best-effort, no raise


# --- taxonomy consistency (architect review #2) -----------------------------


class TestTaxonomyConsistency:
    """Guard against drift between the three doc_type taxonomies.

    Fails CI if DOC_TYPE_ALIASES, DOC_TYPE_DESCRIPTIONS, or the gallery
    categories diverge — the root cause of silent misrouting.
    """

    def test_aliases_and_descriptions_share_keys(self) -> None:
        # Every classifier label must be a canonical alias key (and vice versa).
        assert set(DOC_TYPE_DESCRIPTIONS) == set(DOC_TYPE_ALIASES)

    def test_validate_taxonomy_clean(self) -> None:
        # With the aligned taxonomy, validate_taxonomy() should report no drift.
        warnings = validate_taxonomy()
        assert warnings == [], f"taxonomy drift: {warnings}"

    def test_gallery_categories_known(self, gallery: TemplateGallery) -> None:
        # Every gallery category should be a known canonical doc_type (so an
        # operator passing a category name routes deterministically).
        cats = {t.category for t in gallery.templates}
        unknown = cats - set(DOC_TYPE_ALIASES)
        assert not unknown, f"gallery categories not in taxonomy: {unknown}"


