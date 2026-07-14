"""Unit tests for HyperExtractExtractor and DocTypeRouter (v1.7.0 §4.1/§4.2).

Mocks the hyper-extract ``Template.create`` boundary (via ``_get_template``)
so no real LLM / hyperextract dependency is exercised — we verify the adapter's
AutoGraph → ExtractionResult conversion, type normalization, stopword filter,
relation endpoint validity, doc_type routing, and graceful degradation.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from arrow_lake.knowledge_graph.doc_type_router import DocTypeRouter
from arrow_lake.knowledge_graph.extractor import ExtractionResult
from arrow_lake.knowledge_graph.he_extractor import (
    HyperExtractExtractor,
    _normalize_type,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _node(name: str, type_: str) -> SimpleNamespace:
    """A hyper-extract node stand-in."""
    return SimpleNamespace(name=name, type=type_)


def _edge(source: str, target: str, type_: str = "related_to") -> SimpleNamespace:
    """A hyper-extract edge stand-in."""
    return SimpleNamespace(source=source, target=target, type=type_)


def _ka(nodes: list, edges: list) -> SimpleNamespace:
    """A hyper-extract KA stand-in: ``parse(text)`` returns nodes/edges."""
    return SimpleNamespace(parse=lambda text: SimpleNamespace(nodes=nodes, edges=edges))


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def llm_config() -> SimpleNamespace:
    """Minimal stand-in for LLMConfig (only api_key/model/api_base touched)."""
    return SimpleNamespace(
        api_key="test-key", model="qwen3", api_base="http://localhost:11434/v1"
    )


@pytest.fixture
def router() -> DocTypeRouter:
    return DocTypeRouter(
        {"research_paper": "academic/paper", "resume": "general/biography_graph"},
        default_template="general/default_graph",
    )


@pytest.fixture
def extractor(llm_config: SimpleNamespace, router: DocTypeRouter) -> HyperExtractExtractor:
    return HyperExtractExtractor(llm_config, doc_type_router=router, language="zh")


def _wire_template(extractor: HyperExtractExtractor, ka: SimpleNamespace) -> None:
    """Bypass real Template.create by stubbing _parse_fresh (returns parse result).

    v1.8.7: he_extractor renamed/centralized parsing into ``_parse_fresh(path,
    text) -> ka.parse(text)``. The old ``_get_template`` stub no longer wired in,
    so Template.create ran for real against the (intentionally non-existent)
    ``general/default_graph`` fixture default and returned empty. Stub the actual
    method so the adapter conversion logic is what's under test.
    """
    extractor._parse_fresh = lambda template_path, text: ka.parse(text)  # type: ignore[method-assign]


# ---------------------------------------------------------------------------
# _normalize_type
# ---------------------------------------------------------------------------


def test_normalize_type_maps_known_english_types() -> None:
    assert _normalize_type("Person") == "person"
    assert _normalize_type("organization") == "organization"
    assert _normalize_type("Organisation") == "organization"
    assert _normalize_type("Company") == "organization"
    assert _normalize_type("Model") == "concept"
    assert _normalize_type("Technology") == "concept"
    assert _normalize_type("Framework") == "concept"
    assert _normalize_type("Place") == "location"
    assert _normalize_type("Incident") == "event"


def test_normalize_type_unknown_falls_back_to_concept() -> None:
    assert _normalize_type("WackyType") == "concept"
    assert _normalize_type(None) == "concept"
    assert _normalize_type("") == "concept"


# ---------------------------------------------------------------------------
# DocTypeRouter
# ---------------------------------------------------------------------------


def test_router_resolves_mapped_doc_type() -> None:
    r = DocTypeRouter({"research_paper": "academic/paper"}, "general/default_graph")
    assert r.resolve("research_paper") == "academic/paper"


def test_router_falls_back_to_default_for_unmapped_or_empty() -> None:
    r = DocTypeRouter({"research_paper": "academic/paper"}, "general/default_graph")
    assert r.resolve("unknown") == "general/default_graph"
    assert r.resolve(None) == "general/default_graph"
    assert r.resolve("") == "general/default_graph"


# ---------------------------------------------------------------------------
# extract(): nodes → entities
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_extract_converts_nodes_to_entities_with_type_normalization(
    extractor: HyperExtractExtractor,
) -> None:
    _wire_template(
        extractor,
        _ka([_node("Alice", "Person"), _node("Acme", "Organization")], []),
    )
    result = await extractor.extract("Alice works at Acme", chunk_id="c1")

    assert isinstance(result, ExtractionResult)
    assert [e.name for e in result.entities] == ["Alice", "Acme"]
    assert [e.entity_type for e in result.entities] == ["person", "organization"]
    assert result.raw_text == "Alice works at Acme"


@pytest.mark.asyncio
async def test_extract_filters_generic_stopwords(
    extractor: HyperExtractExtractor,
) -> None:
    # "优化" is in _GENERIC_ENTITY_STOPWORDS; "Alice" is a real proper noun.
    _wire_template(
        extractor,
        _ka([_node("优化", "Concept"), _node("Alice", "Person")], []),
    )
    result = await extractor.extract("text", chunk_id="c1")

    names = [e.name for e in result.entities]
    assert "优化" not in names
    assert names == ["Alice"]


# ---------------------------------------------------------------------------
# extract(): edges → relations (endpoint validity)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_extract_converts_edges_to_relations_dropping_invalid_endpoints(
    extractor: HyperExtractExtractor,
) -> None:
    _wire_template(
        extractor,
        _ka(
            [_node("Alice", "Person"), _node("Acme", "Organization")],
            [
                _edge("Alice", "Acme", "works_at"),
                _edge("Alice", "Ghost", "knows"),  # Ghost not in nodes → dropped
            ],
        ),
    )
    result = await extractor.extract("text", chunk_id="c1")

    assert len(result.relations) == 1
    r = result.relations[0]
    assert r.source == "Alice"
    assert r.target == "Acme"
    assert r.relation_type == "works_at"


@pytest.mark.asyncio
async def test_extract_relation_defaults_type_when_missing(
    extractor: HyperExtractExtractor,
) -> None:
    _wire_template(
        extractor,
        _ka(
            [_node("Alice", "Person"), _node("Bob", "Person")],
            [_edge("Alice", "Bob", "")],  # empty type → default "related_to"
        ),
    )
    result = await extractor.extract("text", chunk_id="c1")
    assert result.relations[0].relation_type == "related_to"


# ---------------------------------------------------------------------------
# extract(): doc_type routing
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_extract_routes_to_template_by_doc_type(
    extractor: HyperExtractExtractor,
) -> None:
    seen: list[str] = []
    extractor._parse_fresh = lambda template_path, text: seen.append(template_path) or _ka([], []).parse(text)  # type: ignore[method-assign]

    await extractor.extract("text", doc_type="research_paper")
    await extractor.extract("text", doc_type="resume")
    await extractor.extract("text", doc_type=None)

    assert seen == [
        "academic/paper",
        "general/biography_graph",
        "general/default_graph",
    ]


# ---------------------------------------------------------------------------
# extract(): empty input + graceful degradation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_extract_empty_text_short_circuits_without_template(
    extractor: HyperExtractExtractor,
) -> None:
    touched = False

    def _fail_if_called(path: str) -> SimpleNamespace:
        nonlocal touched
        touched = True
        return _ka([], [])

    extractor._get_template = _fail_if_called  # type: ignore[method-assign]
    result = await extractor.extract("   ", chunk_id="c1")

    assert result.entities == ()
    assert result.relations == ()
    assert touched is False


@pytest.mark.asyncio
async def test_extract_degrades_on_parse_failure(
    extractor: HyperExtractExtractor,
) -> None:
    class _BadKA:
        def parse(self, text: str):  # noqa: ANN001
            raise RuntimeError("LLM down")

    extractor._get_template = lambda path: _BadKA()  # type: ignore[method-assign]
    result = await extractor.extract("text", chunk_id="c1")

    assert result.entities == ()
    assert result.relations == ()


@pytest.mark.asyncio
async def test_extract_retries_default_when_routed_template_fails(
    extractor: HyperExtractExtractor,
) -> None:
    """A doc_type-routed template that fails to parse (e.g. hypergraph
    ``IndexError`` on sparse content) is retried with the default template
    before yielding empty — one misrouted chunk must not zero the KG build."""
    default_path = extractor._router.default_template()  # general/default_graph
    good = _ka([_node("Alice", "Person")], [])

    def _parse(template_path: str, text: str):
        if template_path == default_path:
            return good.parse(text)
        raise RuntimeError("hypergraph IndexError on sparse content")

    extractor._parse_fresh = _parse  # type: ignore[method-assign]

    # research_paper → "academic/paper" (non-default) fails → retries default
    result = await extractor.extract("text", doc_type="research_paper", chunk_id="c1")
    assert len(result.entities) == 1
    assert result.entities[0].name == "Alice"


@pytest.mark.asyncio
async def test_extract_degrades_on_template_resolution_failure(
    extractor: HyperExtractExtractor,
) -> None:
    def _boom(path: str):
        raise RuntimeError("template create failed")

    extractor._get_template = _boom  # type: ignore[method-assign]
    result = await extractor.extract("text", chunk_id="c1")

    assert result.entities == ()
    assert result.relations == ()


# ---------------------------------------------------------------------------
# extract_batch()
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_extract_batch_fans_out_per_chunk(
    extractor: HyperExtractExtractor,
) -> None:
    _wire_template(extractor, _ka([_node("Alice", "Person")], []))
    results = await extractor.extract_batch(
        [("c1", "alpha"), ("c2", "beta"), ("c3", "")]
    )

    assert len(results) == 3
    assert len(results[0].entities) == 1
    assert len(results[1].entities) == 1
    # empty chunk short-circuits → empty result
    assert results[2].entities == ()


@pytest.mark.asyncio
async def test_extract_batch_empty_input_returns_empty_list(
    extractor: HyperExtractExtractor,
) -> None:
    assert await extractor.extract_batch([]) == []


# ---------------------------------------------------------------------------
# [#2] search_ka / chat_ka / _ensure_ka_index
# ---------------------------------------------------------------------------


def _mem(has_index: bool) -> SimpleNamespace:
    """A stand-in for hyper-extract OMem exposing has_index()."""
    return SimpleNamespace(has_index=lambda: has_index)


def _mock_ka(*, nodes=None, edges=None, has_index: bool = True) -> SimpleNamespace:
    """A stand-in for a loaded hyper-extract AutoGraph."""
    return SimpleNamespace(
        _node_memory=_mem(has_index),
        _edge_memory=_mem(has_index),
        build_index=lambda *a, **k: None,
        search=lambda query, top_k=5: (nodes or [], edges or []),
        chat=lambda question, top_k=5: SimpleNamespace(
            content="answer",
            additional_kwargs={"retrieved_items": nodes or []},
        ),
    )


def test_ensure_ka_index_skips_when_index_present(
    extractor: HyperExtractExtractor,
) -> None:
    """load() restores the index from the dump → build_index must NOT run."""
    ka = _mock_ka(has_index=True)
    calls = []
    ka.build_index = lambda *a, **k: calls.append(1)
    extractor._ensure_ka_index(ka, "ds")
    assert calls == []  # skipped — index already loaded


def test_ensure_ka_index_builds_when_missing(
    extractor: HyperExtractExtractor,
) -> None:
    """No index after load → build_index runs once."""
    ka = _mock_ka(has_index=False)
    calls = []
    ka.build_index = lambda *a, **k: calls.append(1)
    extractor._ensure_ka_index(ka, "ds")
    assert calls == [1]


def test_search_ka_delegates_to_loaded_ka(
    extractor: HyperExtractExtractor,
) -> None:
    node = SimpleNamespace(name="聚合根", type="concept")
    ka = _mock_ka(nodes=[node], edges=[], has_index=True)
    extractor.load_ka_for_query = lambda ds: ka  # type: ignore[method-assign]

    nodes, edges = extractor.search_ka("jd_ddd", "聚合根", top_k=5)
    assert nodes == [node]
    assert edges == []


def test_chat_ka_delegates_to_loaded_ka(
    extractor: HyperExtractExtractor,
) -> None:
    node = SimpleNamespace(name="聚合根", type="concept")
    ka = _mock_ka(nodes=[node], has_index=True)
    extractor.load_ka_for_query = lambda ds: ka  # type: ignore[method-assign]

    resp = extractor.chat_ka("jd_ddd", "什么是聚合根", top_k=5)
    assert resp.content == "answer"
    assert resp.additional_kwargs["retrieved_items"] == [node]
