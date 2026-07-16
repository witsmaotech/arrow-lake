"""Unit tests for HyperExtractExtractor and DocTypeRouter (v1.7.0 §4.1/§4.2).

Mocks the hyper-extract ``Template.create`` boundary (via ``_get_template``)
so no real LLM / hyperextract dependency is exercised — we verify the adapter's
AutoGraph → ExtractionResult conversion, type normalization, stopword filter,
relation endpoint validity, doc_type routing, and graceful degradation.
"""

from __future__ import annotations

from pathlib import Path
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
# _build_client — max_tokens must flow from LLMConfig (audit: was hardcoded 8192,
# so ARROW_LAKE__LLM__MAX_TOKENS env had NO effect on KG extraction).
# ---------------------------------------------------------------------------


def test_build_client_langchain_uses_config_max_tokens(
    monkeypatch: pytest.MonkeyPatch, extractor: HyperExtractExtractor,
) -> None:
    """The langchain ChatOpenAI path must honor ``cfg.max_tokens``."""
    langchain_openai = pytest.importorskip("langchain_openai")
    captured: dict = {}

    class _FakeChat:  # stand-in for ChatOpenAI
        def __init__(self, **kwargs: object) -> None:
            captured.update(kwargs)

    monkeypatch.setattr(langchain_openai, "ChatOpenAI", _FakeChat)

    cfg = SimpleNamespace(
        api_key="k",
        api_base="http://localhost:11434/v1",  # no "aliyuncs" → langchain branch
        max_tokens=4096,
    )
    extractor._build_client(cfg, "qwen3")
    assert captured.get("max_tokens") == 4096


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
# P0#2: strict default concept_graph template
# ---------------------------------------------------------------------------


def test_default_template_is_strict_project_concept_graph() -> None:
    from arrow_lake.config.rag import HugeGraphConfig

    cfg = HugeGraphConfig()
    assert cfg.he_default_template == "concept_graph"
    assert cfg.he_doc_type_templates["paper"] == "concept_graph"
    assert cfg.he_doc_type_templates["report"] == "concept_graph"


def test_project_concept_graph_template_is_strict() -> None:
    import yaml

    from arrow_lake.knowledge_graph.he_extractor import _PROJECT_TEMPLATES_DIR

    data = yaml.safe_load((_PROJECT_TEMPLATES_DIR / "concept_graph.yaml").read_text("utf-8"))
    fields = data["output"]["entities"]["fields"]
    type_field = next(f for f in fields if f["name"] == "type")
    zh = type_field["description"]["zh"]
    assert "之一：" in zh or "之一:" in zh  # strict enum clause (parsed by _get_type_enum)
    def_field = next(f for f in fields if f["name"] == "definition")
    assert def_field["required"] is True  # kills 0%-description coverage


def test_get_type_enum_resolves_project_templates(extractor) -> None:
    # [#resolution] project-local bare stems must resolve to the project YAML
    # (was gallery-presets-only → None → type post-filter silently never fired).
    enum = extractor._get_type_enum("concept_graph")
    assert enum is not None and len(enum) > 0
    assert "实体" in enum
    assert extractor._get_type_enum("ddd_concept_graph") is not None  # other project templates too


def test_ka_to_extraction_result_snaps_non_enum_type() -> None:
    # The dataset path (build_dataset_ka) now passes valid_types, so a noisy
    # compound LLM type (e.g. "实体/方法") snaps into the enum → 实体.
    ka = SimpleNamespace(
        nodes=[SimpleNamespace(name="FooBar", type="实体/方法", definition="d")],
        edges=[],
    )
    out = HyperExtractExtractor._ka_to_extraction_result(
        ka, valid_types=["实体", "属性", "方法", "过程", "角色", "事件", "组织"])
    assert out.entities[0].entity_type == "实体"  # snapped into the enum


# ---------------------------------------------------------------------------
# P0#3: type-enum race — must stay local, not on self
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_extract_does_not_leak_type_enum_to_instance(extractor) -> None:
    ka = SimpleNamespace(nodes=[], edges=[], parse=lambda text: None)
    _wire_template(extractor, ka)
    await extractor.extract("some document content text here", chunk_id="c1")
    # [#racefix] the type enum was written to self._current_type_enum and read
    # back after an await — clobbered under extract_batch's gather. Must be local.
    assert not hasattr(extractor, "_current_type_enum")


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
    # entity_type keeps the LLM-extracted RAW type — _ka_to_extraction_result
    # intentionally does NOT call _normalize_type (it collapsed every Chinese
    # type to "concept"; see its inline comment). So English types stay as-is.
    assert [e.entity_type for e in result.entities] == ["Person", "Organization"]
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


def _mem(has_index: bool, items=None) -> SimpleNamespace:
    """A stand-in for hyper-extract OMem exposing has_index() + items."""
    return SimpleNamespace(has_index=lambda: has_index, items=items or [])


def _mock_ka(*, nodes=None, edges=None, has_index: bool = True) -> SimpleNamespace:
    """A stand-in for a loaded hyper-extract AutoGraph."""
    return SimpleNamespace(
        _node_memory=_mem(has_index, nodes),
        _edge_memory=_mem(has_index, edges),
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


def test_rebuild_ka_index_loads_builds_dumps(
    extractor: HyperExtractExtractor, tmp_path: SimpleNamespace,
) -> None:
    """[#7] rebuild loads the dump, force-rebuilds the index, and dumps back."""
    import os
    ka_dir = tmp_path / "ds" / "ka"
    ka_dir.mkdir(parents=True)
    (ka_dir / "data.json").write_text("{}", encoding="utf-8")  # dump exists
    extractor._ka_dir_for = lambda ds: ka_dir  # type: ignore[method-assign]
    node = SimpleNamespace(name="x", type="concept")
    dumped = []
    ka = _mock_ka(nodes=[node], has_index=False)
    ka.dump = lambda d: dumped.append(str(d))  # type: ignore[method-assign]
    extractor.load_ka_for_query = lambda ds: ka  # type: ignore[method-assign]

    result = extractor.rebuild_ka_index("ds")
    assert result["index_rebuilt"] is True
    assert result["node_count"] == 1
    assert dumped and dumped[0] == str(ka_dir)  # dumped back to same dir


def test_rebuild_ka_index_raises_when_no_dump(
    extractor: HyperExtractExtractor, tmp_path: SimpleNamespace,
) -> None:
    import pytest as _pytest
    extractor._ka_dir_for = lambda ds: tmp_path / "nodump"  # type: ignore[method-assign]
    with _pytest.raises(FileNotFoundError):
        extractor.rebuild_ka_index("ds")


def test_export_ka_obsidian_loads_and_exports(
    extractor: HyperExtractExtractor, tmp_path: SimpleNamespace,
) -> None:
    """[#5] export loads the dump, calls export_obsidian, returns vault path."""
    ka_dir = tmp_path / "ds" / "ka"
    ka_dir.mkdir(parents=True)
    (ka_dir / "data.json").write_text("{}", encoding="utf-8")
    extractor._ka_dir_for = lambda ds: ka_dir  # type: ignore[method-assign]
    out_dir = tmp_path / "vault"
    node = SimpleNamespace(name="聚合根", type="concept")
    ka = _mock_ka(nodes=[node], has_index=True)
    exported = []
    ka.export_obsidian = lambda d, **kw: exported.append(d) or d  # type: ignore[method-assign]
    extractor.load_ka_for_query = lambda ds: ka  # type: ignore[method-assign]

    result = extractor.export_ka_obsidian("ds", out_dir, vault_name="V", overwrite=True)
    assert result["vault_path"] == str(out_dir)
    assert result["node_count"] == 1
    assert exported and exported[0] == out_dir


def test_export_ka_obsidian_raises_when_no_dump(
    extractor: HyperExtractExtractor, tmp_path: SimpleNamespace,
) -> None:
    import pytest as _pytest
    extractor._ka_dir_for = lambda ds: tmp_path / "nodump"  # type: ignore[method-assign]
    with _pytest.raises(FileNotFoundError):
        extractor.export_ka_obsidian("ds", tmp_path / "vault")
    # error message must NOT leak the filesystem path (security: sanitized)
    try:
        extractor.export_ka_obsidian("ds", tmp_path / "vault")
    except FileNotFoundError as e:
        assert str(tmp_path) not in str(e)


# ---------------------------------------------------------------------------
# [#KG-LLM-split] dual LLM config (extraction vs Q&A)
# ---------------------------------------------------------------------------


def _hg_config(*, extract=None, qa=None) -> SimpleNamespace:
    """HugeGraphConfig stand-in carrying the two phase LLM configs."""
    return SimpleNamespace(
        he_extract_llm=extract, he_qa_llm=qa,
        he_chunk_size=2048, he_chunk_overlap=256, he_max_workers=10,
    )


def test_qa_client_uses_qa_config_when_set(
    llm_config: SimpleNamespace, router: DocTypeRouter,
) -> None:
    """load_ka_for_query's QA client uses he_qa_llm; extraction uses the global llm."""
    qa_cfg = SimpleNamespace(
        api_key="qa-key", model="deepseek-v3",
        api_base="https://dashscope.aliyuncs.com/compatible-mode/v1",
    )
    ex = HyperExtractExtractor(
        llm_config, doc_type_router=router, language="zh",
        hugegraph_config=_hg_config(qa=qa_cfg),
    )
    built: list[tuple] = []
    ex._build_client = lambda cfg, model: built.append((cfg, model)) or SimpleNamespace()  # type: ignore[method-assign]

    ex._get_extract_client()
    ex._get_qa_client()

    assert len(built) == 2
    extract_cfg, extract_model = built[0]
    qa_cfg_used, qa_model = built[1]
    # extraction falls back to the global llm_config (+ its model)
    assert extract_cfg is llm_config
    assert extract_model == llm_config.model
    # Q&A uses the dedicated flagship config
    assert qa_cfg_used is qa_cfg
    assert qa_model == "deepseek-v3"


def test_qa_client_falls_back_to_extract_when_unset(
    llm_config: SimpleNamespace, router: DocTypeRouter,
) -> None:
    """No overrides → QA shares the extract client (single global llm, backward compat)."""
    ex = HyperExtractExtractor(
        llm_config, doc_type_router=router, language="zh",
        hugegraph_config=_hg_config(),  # both None
    )
    built: list[str] = []
    ex._build_client = lambda cfg, model: built.append(model) or SimpleNamespace()  # type: ignore[method-assign]

    c_extract = ex._get_extract_client()
    c_qa = ex._get_qa_client()

    assert len(built) == 1          # one client built, shared by both phases
    assert c_qa is c_extract


# ---------------------------------------------------------------------------
# [#incremental] build_dataset_ka incremental feed (Task 2)
# ---------------------------------------------------------------------------


class _FakeNode:
    def __init__(self, name: str) -> None:
        self.name = name


class _FakeKA:
    """Records feed_text calls; persists node names across dump/load."""

    def __init__(self, template_path: str) -> None:
        self._stem = Path(template_path).stem
        self._names: list[str] = []
        self.fed_texts: list[str] = []  # feed_text calls this run (load doesn't count)

    @property
    def nodes(self) -> list[_FakeNode]:
        return [_FakeNode(n) for n in self._names]

    def feed_text(self, text: str) -> None:
        self.fed_texts.append(text)
        nm = (text or "").strip().split()[0] if (text or "").strip() else "x"
        if nm not in self._names:
            self._names.append(nm)

    def dump(self, ka_dir: Path) -> None:
        import json as _json
        ka_dir.mkdir(parents=True, exist_ok=True)
        (ka_dir / "data.json").write_text("{}")
        (ka_dir / "metadata.json").write_text(
            _json.dumps({"template": self._stem, "lang": "zh", "type": "graph"})
        )
        (ka_dir / "_fake_nodes.json").write_text(_json.dumps(self._names))

    def load(self, ka_dir: Path) -> None:
        import json as _json
        p = ka_dir / "_fake_nodes.json"
        if p.is_file():
            self._names = list(_json.loads(p.read_text("utf-8")))

    def build_index(self) -> None:
        pass

    def empty(self) -> bool:
        return len(self._names) == 0


def _incremental_extractor(
    llm_config: SimpleNamespace, router: DocTypeRouter,
) -> HyperExtractExtractor:
    """Extractor with _create_ka + _ka_to_extraction_result stubbed (no LLM).

    Captures every KA created so tests can inspect what was fed.
    """
    import json as _json
    ex = HyperExtractExtractor(llm_config, doc_type_router=router, language="zh")
    created: list[_FakeKA] = []

    def _create(tpl: str, **kw: object) -> _FakeKA:
        ka = _FakeKA(tpl)
        created.append(ka)
        return ka

    ex._create_ka = _create  # type: ignore[method-assign]
    ex._ka_to_extraction_result = lambda ka, valid_types=None: ExtractionResult(  # type: ignore[method-assign]
        entities=tuple(), relations=tuple(), raw_text="",
    )
    ex.created_kas = created  # type: ignore[attr-defined]
    return ex


@pytest.mark.asyncio
async def test_build_dataset_ka_full_writes_fed_chunks(
    llm_config: SimpleNamespace, router: DocTypeRouter, tmp_path,
) -> None:
    """A full build records every chunk_id in fed_chunks.json."""
    import json as _json
    ex = _incremental_extractor(llm_config, router)
    ka_dir = tmp_path / "ka"
    await ex.build_dataset_ka(
        "general/concept_graph",
        [("0", "alpha"), ("1", "beta")], ka_dir,
    )
    fed = _json.loads((ka_dir / "fed_chunks.json").read_text("utf-8"))
    assert list(fed.keys()) == ["0", "1"]  # [#step3-C] dict {cid: hash}
    # the full build fed both chunks
    assert ex.created_kas[-1].fed_texts == ["alpha", "beta"]


@pytest.mark.asyncio
async def test_build_dataset_ka_incremental_feeds_only_new(
    llm_config: SimpleNamespace, router: DocTypeRouter, tmp_path,
) -> None:
    """Incremental build loads the existing KA and feeds ONLY new chunks."""
    import json as _json
    ex = _incremental_extractor(llm_config, router)
    ka_dir = tmp_path / "ka"
    await ex.build_dataset_ka(
        "general/concept_graph", [("0", "alpha"), ("1", "beta")], ka_dir,
    )
    # incremental: chunks 0,1 already fed + new chunk 2
    await ex.build_dataset_ka(
        "general/concept_graph",
        [("0", "alpha"), ("1", "beta"), ("2", "gamma")], ka_dir,
        incremental=True,
    )
    fed = _json.loads((ka_dir / "fed_chunks.json").read_text("utf-8"))
    assert list(fed.keys()) == ["0", "1", "2"]           # all tracked
    assert ex.created_kas[-1].fed_texts == ["gamma"]    # ONLY the new chunk fed


@pytest.mark.asyncio
async def test_build_dataset_ka_incremental_template_mismatch_full_feed(
    llm_config: SimpleNamespace, router: DocTypeRouter, tmp_path,
) -> None:
    """A template change must force a full feed (schema-tear guard)."""
    import json as _json
    ex = _incremental_extractor(llm_config, router)
    ka_dir = tmp_path / "ka"
    await ex.build_dataset_ka(
        "general/concept_graph", [("0", "alpha"), ("1", "beta")], ka_dir,
    )
    # simulate a routing/template change: metadata now names a different template
    meta = _json.loads((ka_dir / "metadata.json").read_text("utf-8"))
    meta["template"] = "workflow_graph"
    (ka_dir / "metadata.json").write_text(_json.dumps(meta))
    # incremental with the (now mismatched) original template → full feed fallback
    await ex.build_dataset_ka(
        "general/concept_graph",
        [("0", "alpha"), ("1", "beta"), ("2", "gamma")], ka_dir,
        incremental=True,
    )
    # full feed → all 3 chunks fed (not just the new "gamma")
    assert ex.created_kas[-1].fed_texts == ["alpha", "beta", "gamma"]
    fed = _json.loads((ka_dir / "fed_chunks.json").read_text("utf-8"))
    assert list(fed.keys()) == ["0", "1", "2"]


@pytest.mark.asyncio
async def test_build_dataset_ka_incremental_refeeds_changed_content(
    llm_config: SimpleNamespace, router: DocTypeRouter, tmp_path,
) -> None:
    """[#step3-C] Incremental re-feeds a chunk whose TEXT changed (same id)."""
    ex = _incremental_extractor(llm_config, router)
    ka_dir = tmp_path / "ka"
    await ex.build_dataset_ka(
        "general/concept_graph", [("0", "alpha"), ("1", "beta")], ka_dir,
    )
    # chunk "1" content changed (beta → beta-CHANGED), same chunk_id
    await ex.build_dataset_ka(
        "general/concept_graph",
        [("0", "alpha"), ("1", "beta-CHANGED")], ka_dir,
        incremental=True,
    )
    # chunk 0 unchanged (skip), chunk 1 changed → re-fed
    assert ex.created_kas[-1].fed_texts == ["beta-CHANGED"]


class TestFeedRetry:
    """[#step4-B] feed_text retry/backoff on transient LLM errors."""

    def _ex(self):
        return HyperExtractExtractor(
            SimpleNamespace(api_key="k", model="m", api_base="http://x/v1"),
            doc_type_router=None, language="zh",
        )

    def test_is_transient_classification(self) -> None:
        assert HyperExtractExtractor._is_transient_feed_error(RuntimeError("connection reset by peer"))
        assert HyperExtractExtractor._is_transient_feed_error(TimeoutError("request timed out"))
        assert HyperExtractExtractor._is_transient_feed_error(Exception("HTTP 503 unavailable"))
        assert not HyperExtractExtractor._is_transient_feed_error(ValueError("messages must contain json"))
        assert not HyperExtractExtractor._is_transient_feed_error(KeyError("schema"))

    def test_retries_transient_then_succeeds(self) -> None:
        ex = self._ex()
        calls = [0]

        def flaky(text):
            calls[0] += 1
            if calls[0] < 2:
                raise RuntimeError("connection timeout")

        ka = SimpleNamespace(feed_text=flaky)
        ex._feed_with_retry(ka, "t", attempts=3)  # 2nd attempt succeeds
        assert calls[0] == 2

    def test_raises_hard_immediately_no_retry(self) -> None:
        ex = self._ex()
        calls = [0]

        def hard(text):
            calls[0] += 1
            raise ValueError("schema mismatch")

        ka = SimpleNamespace(feed_text=hard)
        with pytest.raises(ValueError):
            ex._feed_with_retry(ka, "t", attempts=3)
        assert calls[0] == 1  # no retry on hard error




