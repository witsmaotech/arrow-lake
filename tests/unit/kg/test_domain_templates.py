"""Tests for [#9] multi-domain project templates + routing."""

from __future__ import annotations

from arrow_lake.config import HugeGraphConfig
from arrow_lake.knowledge_graph.doc_type_router import DocTypeRouter


def _router() -> DocTypeRouter:
    hg = HugeGraphConfig()
    return DocTypeRouter(hg.he_doc_type_templates, hg.he_default_template)


def test_domain_doc_types_route_to_project_templates() -> None:
    """medicine/legal/finance/ddd override → project template (full path), not preset."""
    r = _router()
    for doc_type, stem in [
        ("medicine", "medical_concept_graph"),
        ("legal", "legal_concept_graph"),
        ("finance", "finance_concept_graph"),
        ("ddd", "ddd_concept_graph"),
    ]:
        path, source = r.resolve_with_source(doc_type)
        assert source == "override", f"{doc_type} should be an override, got {source}"
        # project templates resolve to their absolute .yaml path, not a preset
        assert path.endswith(f"{stem}.yaml"), f"{doc_type} → {path}"
        assert "/presets/" not in path, f"{doc_type} routed to a preset: {path}"


def test_paper_doc_type_uses_project_concept_graph() -> None:
    """paper → project-local concept_graph (strict type/relation enum + required
    definition), NOT the gallery general/concept_graph preset.

    P0#2 (commit d0223fc) switched default/paper/report off the free-type gallery
    preset onto the strict project template to kill the 80+ type / 0% definition
    noise. This is the inverse of the old 'stays on preset' regression guard.
    """
    r = _router()
    path, source = r.resolve_with_source("paper")
    assert source == "override"
    assert path.endswith("concept_graph.yaml")
    assert "/presets/" not in path


def test_override_resolves_bare_project_name() -> None:
    """A bare project-template name in overrides resolves to its file path."""
    r = DocTypeRouter({"custom": "medical_concept_graph"}, "general/concept_graph")
    path, source = r.resolve_with_source("custom")
    assert source == "override"
    assert path.endswith("medical_concept_graph.yaml")


def test_project_templates_in_gallery() -> None:
    """All 4 domain project templates are registered in the gallery."""
    from arrow_lake.knowledge_graph.doc_type_router import get_template_gallery

    names = {t.name for t in get_template_gallery().templates if t.category == "project"}
    for stem in ("ddd_concept_graph", "medical_concept_graph",
                 "legal_concept_graph", "finance_concept_graph"):
        assert stem in names, f"{stem} missing from gallery project templates"
