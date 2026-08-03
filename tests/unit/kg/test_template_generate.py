"""v1.10.0 M2.5: LLM template generation — _do_generate self-heal + _strip_fences.

Mocks the LLM (generate_fn) so the self-heal loop is unit-tested without a
real provider. The live LLM path is exercised via the /generate endpoint E2E.
"""

from __future__ import annotations

import pytest

from arrow_lake.api.routers.extraction_templates import (
    GenerateRequest, _do_generate, _strip_fences,
)

_VALID = (
    "language: [zh, en]\nname: security_concept_graph\ntype: graph\ntags: [security]\n"
    "description: {zh: s, en: s}\n"
    "output:\n  entities:\n    fields:\n"
    "      - {name: name, type: str, description: d}\n"
    "      - {name: type, type: str, description: d}\n"
    "  relations:\n    fields:\n"
    "      - {name: source, type: str, description: d}\n"
    "      - {name: target, type: str, description: d}\n"
    "      - {name: type, type: str, description: d}\n"
    "guideline:\n  target: {zh: e, en: e}\n"
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


@pytest.mark.asyncio
async def test_generate_valid_first_try() -> None:
    calls = 0

    async def gen(msgs):
        nonlocal calls; calls += 1
        return _VALID

    yaml, errs, healed = await _do_generate(_req(), gen)
    assert not errs and healed is False and calls == 1
    assert "security_concept_graph" in yaml


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
