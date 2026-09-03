"""v1.10.0 M2.5: LLM template generation — _do_generate self-heal + _strip_fences.

Mocks the LLM (generate_fn) so the self-heal loop is unit-tested without a
real provider. The live LLM path is exercised via the /generate endpoint E2E.
"""

from __future__ import annotations

import pytest

from arrow_lake.api.routers.extraction_templates import (
    GenerateRequest, _do_generate, _strip_fences,
)

# Shape mirrors the CURRENT hyperextract TemplateCfg schema (verified against
# arrow_lake/knowledge_graph/templates/concept_graph.yaml): output.description,
# guideline.rules_for_entities/rules_for_relations and display are required —
# the old minimal fixture failed the pre-save load check with 9 field errors.
# Shape mirrors the CURRENT hyperextract TemplateCfg schema (verified against
# arrow_lake/knowledge_graph/templates/concept_graph.yaml): output.description,
# entities/relations.description, guideline.rules_* (string LISTS) and plain-
# string display labels are required — the old minimal fixture failed the
# pre-save load check with 9 field errors.
_VALID = (
    "language: [zh, en]\nname: security_concept_graph\ntype: graph\ntags: [security]\n"
    "description: {zh: s, en: s}\n"
    "output:\n  description: {zh: so, en: so}\n"
    "  entities:\n    description: {zh: ed, en: ed}\n    fields:\n"
    "      - {name: name, type: str, description: d}\n"
    "      - {name: type, type: str, description: d}\n"
    "  relations:\n    description: {zh: rd, en: rd}\n    fields:\n"
    "      - {name: source, type: str, description: d}\n"
    "      - {name: target, type: str, description: d}\n"
    "      - {name: type, type: str, description: d}\n"
    "guideline:\n  target: {zh: e, en: e}\n"
    "  rules_for_entities: {zh: [r1], en: [r1]}\n"
    "  rules_for_relations: {zh: [r2], en: [r2]}\n"
    "display:\n  entity_label: '{name}'\n  relation_label: '{type}'\n"
    "identifiers:\n  entity_id: name\n"
)


def _req(**kw) -> GenerateRequest:
    return GenerateRequest(prompt="网络安全领域,提取资产/威胁/控制/事件", **kw)


@pytest.mark.asyncio
async def test_strip_fences_yaml_block() -> None:
    assert _strip_fences("```yaml\n" + _VALID + "\n```").startswith("language:")


@pytest.mark.asyncio
async def test_strip_fences_leading_prose() -> None:
    out = _strip_fences("好的,这是模板:\n```yaml\n" + _VALID + "```")
    assert out.startswith("language:")


# xfail(W1-1 登记):同进程内他处先触发 hyperextract import 后,其模块级全局
# 态令 _do_generate 的模板 load 抛第三方内部错误(单跑本文件全绿,全量必败,
# 两态均见 docs_offline/v1115-w1-test-isolation-record.md 三i)。非 strict:
# 第三方修复后单跑 XPASS 不应翻红。
@pytest.mark.xfail(reason="hyperextract module-level global state (third-party)", strict=False)
@pytest.mark.asyncio
async def test_generate_valid_first_try() -> None:
    calls = 0

    async def gen(msgs):
        nonlocal calls; calls += 1
        return _VALID

    yaml, errs, healed = await _do_generate(_req(), gen)
    assert not errs and healed is False and calls == 1
    assert "security_concept_graph" in yaml


@pytest.mark.xfail(reason="hyperextract module-level global state (third-party)", strict=False)
@pytest.mark.asyncio
async def test_generate_self_heal_then_valid() -> None:
    bad = _VALID.replace("name: security_concept_graph", "name: Bad-Name")
    seq = iter([bad, _VALID])

    async def gen(msgs):
        return next(seq)

    yaml, errs, healed = await _do_generate(_req(), gen)
    assert not errs and healed is True  # healed on 2nd attempt


@pytest.mark.asyncio
async def test_generate_still_invalid_after_heal_attempts() -> None:
    bad = _VALID.replace("name: security_concept_graph", "name: Bad-Name")

    async def gen(msgs):
        return bad

    yaml, errs, healed = await _do_generate(_req(), gen)
    assert errs and healed is True  # attempted heal, never valid
    assert any("name" in p or "must match" in m for p, m in errs)


@pytest.mark.asyncio
async def test_generate_passes_sample_and_doctype_into_prompt() -> None:
    captured: list[list] = []

    async def gen(msgs):
        captured.append(msgs)
        return _VALID

    await _do_generate(_req(doc_type="security", sample_text="防火墙是网络资产"), gen)
    user_msg = captured[0][1][1]  # [(sys,...),(user,...)] → user content
    assert "security" in user_msg and "防火墙" in user_msg
