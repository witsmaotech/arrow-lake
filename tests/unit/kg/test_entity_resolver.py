"""Unit tests for entity_resolver (pure-logic synonym merge)."""

from __future__ import annotations

import pytest
from arrow_lake.knowledge_graph.entity_resolver import resolve_entities
from arrow_lake.knowledge_graph.extractor import (
    ExtractedEntity,
    ExtractedRelation,
    ExtractionResult,
)


def _e(name: str, defn: str = "") -> ExtractedEntity:
    return ExtractedEntity(
        name=name,
        entity_type="x",
        properties=(("definition", defn),) if defn else (),
    )


def _result(entities, relations=()) -> ExtractionResult:
    return ExtractionResult(
        entities=tuple(entities), relations=tuple(relations), raw_text=""
    )


@pytest.mark.asyncio
async def test_merge_synonyms_rewrites_relations_and_definitions() -> None:
    result = _result(
        [_e("A", "a"), _e("B", "bbbblong"), _e("C", "c")],
        relations=[ExtractedRelation(source="B", target="C", relation_type="r")],
    )

    def embed_fn(texts):
        base = {"a": [1.0, 0.0], "b": [0.99, 0.01], "c": [0.0, 1.0]}
        out = []
        for t in texts:
            nm = t.split(":", 1)[0].strip().lower()
            out.append(base.get(nm, [0.5, 0.5]))
        return out

    async def gen_fn(prompt):
        return '{"merge": {"B": "A"}}'

    new_r, mmap = await resolve_entities(
        result, embed_fn=embed_fn, generate_fn=gen_fn, threshold=0.86, batch=8
    )

    assert mmap == {"B": "A"}
    assert {e.name for e in new_r.entities} == {"A", "C"}
    # B's longer definition merged into A
    a = next(e for e in new_r.entities if e.name == "A")
    assert dict(a.properties).get("definition") == "bbbblong"
    # relation B->C rewritten to A->C
    assert ("A", "C") in [(r.source, r.target) for r in new_r.relations]


@pytest.mark.asyncio
async def test_no_merge_when_dissimilar() -> None:
    result = _result([_e("A"), _e("B")])

    def embed_fn(texts):
        return [[1.0, 0.0], [0.0, 1.0]]  # orthogonal → no cluster

    called = []

    async def gen_fn(p):
        called.append(p)
        return "{}"

    new_r, mmap = await resolve_entities(
        result, embed_fn=embed_fn, generate_fn=gen_fn, threshold=0.86, batch=8
    )
    assert mmap == {}
    assert not called  # LLM never called
    assert len(new_r.entities) == 2


@pytest.mark.asyncio
async def test_bad_json_skips_batch() -> None:
    result = _result([_e("A"), _e("B")])

    def embed_fn(texts):
        return [[1.0, 0.0], [0.99, 0.01]]  # similar → cluster

    async def gen_fn(p):
        return "not json"

    new_r, mmap = await resolve_entities(
        result, embed_fn=embed_fn, generate_fn=gen_fn, threshold=0.86, batch=8
    )
    assert mmap == {}
    assert len(new_r.entities) == 2  # unchanged


@pytest.mark.asyncio
async def test_single_entity_noop() -> None:
    result = _result([_e("A")])

    async def gen_fn(p):
        return "should not be called"

    new_r, mmap = await resolve_entities(
        result, embed_fn=lambda t: [[1.0]], generate_fn=gen_fn, threshold=0.86, batch=8
    )
    assert mmap == {}
    assert len(new_r.entities) == 1


@pytest.mark.asyncio
async def test_json_with_markdown_fence_parsed() -> None:
    result = _result([_e("A"), _e("B")])

    def embed_fn(texts):
        return [[1.0, 0.0], [0.99, 0.01]]

    async def gen_fn(p):
        return "```json\n{\"merge\": {\"B\": \"A\"}}\n```"

    new_r, mmap = await resolve_entities(
        result, embed_fn=embed_fn, generate_fn=gen_fn, threshold=0.86, batch=8
    )
    assert mmap == {"B": "A"}
    assert {e.name for e in new_r.entities} == {"A"}


@pytest.mark.asyncio
async def test_parse_recovers_bare_name_when_llm_wraps_with_definition() -> None:
    """LLM often returns the key with its definition in parens
    ('应急指挥中心（全市应急…）'); _extract_name must recover the bare name."""
    result = _result(
        [_e("应急指挥中心", "全市应急"), _e("市应急指挥中心", "芜湖市")]
    )

    def embed_fn(texts):
        return [[1.0, 0.0], [0.99, 0.01]]

    async def gen_fn(p):
        return (
            '{"merge": {"应急指挥中心（全市应急）": '
            '"市应急指挥中心（芜湖市）"}}'
        )

    new_r, mmap = await resolve_entities(
        result, embed_fn=embed_fn, generate_fn=gen_fn, threshold=0.60, batch=8
    )
    assert mmap == {"应急指挥中心": "市应急指挥中心"}
    assert {e.name for e in new_r.entities} == {"市应急指挥中心"}
