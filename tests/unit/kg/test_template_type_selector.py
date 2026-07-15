"""Tests for [#1] TemplateTypeSelector."""

from __future__ import annotations

from arrow_lake.knowledge_graph.template_type_selector import (
    HIGH_RISK_TYPES,
    TEMPLATE_TYPES,
    TYPE_DEFAULTS,
    TemplateTypeSelector,
)


def test_six_selectable_types_present() -> None:
    assert set(TEMPLATE_TYPES) == {
        "graph", "temporal_graph", "hypergraph", "list", "set", "model",
    }


def test_each_type_has_a_default_template() -> None:
    sel = TemplateTypeSelector()
    for t in TEMPLATE_TYPES:
        assert sel.default_for(t), f"missing default for {t}"


def test_explicit_type_returns_its_default() -> None:
    sel = TemplateTypeSelector()
    assert sel.select(template_type="temporal_graph") == TYPE_DEFAULTS["temporal_graph"]
    assert sel.select(template_type="list") == TYPE_DEFAULTS["list"]
    assert sel.select(template_type="graph") == TYPE_DEFAULTS["graph"]


def test_unknown_type_defers_to_none() -> None:
    sel = TemplateTypeSelector()
    assert sel.select(template_type="nonsense") is None


def test_temporal_heuristic_picks_temporal_graph() -> None:
    sel = TemplateTypeSelector()
    # unambiguous temporal signal in content → temporal_graph (no type pinned)
    assert sel.select(content="项目的时间线和里程碑历程") == TYPE_DEFAULTS["temporal_graph"]
    # temporal signal in doc_type → temporal_graph
    assert sel.select(doc_type="timeline") == TYPE_DEFAULTS["temporal_graph"]
    # english signal
    assert sel.select(content="chronological timeline of events") == TYPE_DEFAULTS["temporal_graph"]


def test_generic_words_do_not_trigger_temporal() -> None:
    """Regression: generic technical words (事件/流程/阶段/顺序) must NOT route
    to temporal_graph — they appear in nearly every domain doc (e.g. DDD domain
    events, business processes) and previously crashed kg_build by routing to a
    temporal template that lacks a time_field."""
    sel = TemplateTypeSelector()
    assert sel.select(content="领域事件与业务流程的设计阶段和顺序") is None
    assert sel.select(content="event-driven workflow sequence") is None
    assert sel.select(doc_type="ddd") is None


def test_no_signal_no_type_defers() -> None:
    sel = TemplateTypeSelector()
    assert sel.select(content="聚合根是领域模型的一致性边界", doc_type="ddd") is None


def test_explicit_type_beats_heuristic() -> None:
    """A pinned type wins even when temporal signals are present."""
    sel = TemplateTypeSelector()
    assert sel.select(template_type="graph", content="事件流 时间线") == TYPE_DEFAULTS["graph"]


def test_hypergraph_is_high_risk_and_opt_in() -> None:
    assert "hypergraph" in HIGH_RISK_TYPES
    assert TemplateTypeSelector.is_high_risk("hypergraph") is True
    assert TemplateTypeSelector.is_high_risk("graph") is False
    # hypergraph still resolves (opt-in), just warned
    sel = TemplateTypeSelector()
    assert sel.select(template_type="hypergraph") == TYPE_DEFAULTS["hypergraph"]


def test_is_valid() -> None:
    for t in TEMPLATE_TYPES:
        assert TemplateTypeSelector.is_valid(t)
    assert not TemplateTypeSelector.is_valid("spatial_graph")
    assert not TemplateTypeSelector.is_valid(None)
