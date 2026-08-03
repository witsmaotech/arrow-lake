"""v1.10.0 M4: extraction-templates quality-validation harness helpers.

The three quality endpoints need a live lake + LLM (integration-level, covered
by live E2E). These tests cover the pure helpers that drive doc generation and
fence stripping — the parts most likely to silently regress.
"""

from __future__ import annotations

from arrow_lake.api.routers.extraction_templates import (
    _QUALITY_DS_RE,
    _extract_schema_snippet,
    _quality_ka_root,
    _strip_doc_fences,
)

_YAML = """\
language: [zh, en]
name: security_concept_graph
type: graph
tags: [security]
description: {zh: 安全概念图, en: security concept graph}
output:
  entities:
    description: {zh: 安全要素, en: elements}
    fields:
      - {name: name, type: str, description: {zh: 名称, en: name}, required: true}
      - {name: type, type: str, description: {zh: "必须是: 资产/威胁/控制", en: one of asset/threat/control}, required: true}
  relations:
    description: {zh: 关联, en: relations}
    fields:
      - {name: source, type: str, description: {zh: 源, en: source}, required: true}
      - {name: target, type: str, description: {zh: 目标, en: target}, required: true}
      - {name: type, type: str, description: {zh: "关系: 防护/利用", en: protect/exploit}, required: true}
guideline:
  target: {zh: 安全专家, en: security expert}
"""


def test_extract_schema_snippet_keeps_output_and_guideline_drops_tags():
    """The doc-gen LLM needs output/guideline (type enums live there); tags are noise."""
    snippet = _extract_schema_snippet(_YAML)
    assert "entities" in snippet
    assert "relations" in snippet
    assert "guideline" in snippet
    # entity/relation type enums are preserved so the LLM can cover every type
    assert "资产/威胁/控制" in snippet
    assert "防护/利用" in snippet
    # tags are dropped to keep the prompt focused (the `tags:` key itself)
    assert "\ntags:" not in snippet and not snippet.startswith("tags:")


def test_extract_schema_snippet_handles_unparseable_yaml():
    """Bad YAML falls back to a trimmed raw string (never raises)."""
    snippet = _extract_schema_snippet(":::not: :yaml:::")
    assert isinstance(snippet, str)
    assert snippet  # non-empty fallback


def test_extract_schema_snippet_empty_yaml():
    assert _extract_schema_snippet("") == "{}\n"  # no keys → empty mapping


def test_strip_doc_fences_removes_markdown_fences():
    fenced = "```markdown\n# 标题\n\n这是一段文档正文。\n```"
    assert _strip_doc_fences(fenced) == "# 标题\n\n这是一段文档正文。"


def test_strip_doc_fences_plain_text_unchanged():
    plain = "一段没有围栏的纯文本文档。\n第二段。"
    assert _strip_doc_fences(plain) == plain


def test_strip_doc_fences_handles_none_and_whitespace():
    assert _strip_doc_fences("   \n正文\n   ") == "正文"
    assert _strip_doc_fences(None) == ""  # type: ignore[arg-type]


# --- path-traversal defense (M4 security guard) ---------------------------

def test_quality_ds_re_accepts_generated_names():
    """Only server-generated _quality_<token_hex(6)> names pass (12 lowercase hex)."""
    assert _QUALITY_DS_RE.match("_quality_6b230d285b68")
    assert _QUALITY_DS_RE.match("_quality_000000000000")
    assert _QUALITY_DS_RE.match("_quality_ffffffffffff")


def test_quality_ds_re_rejects_traversal_and_malformed():
    """Everything that could escape the KA dir via _quality_ka_root is rejected."""
    bad = [
        "_quality_..",              # parent traversal
        "_quality_../../etc",       # nested traversal
        "_quality_a/b",             # slash
        "_quality_ABCDEF123456",    # uppercase hex
        "_quality_6b230d285b6",     # too short (11)
        "_quality_6b230d285b689",   # too long (13)
        "_quality_g230d285b68",     # non-hex char
        "../etc",                   # no prefix
        "quality_6b230d285b68",     # missing leading underscore
        "runs",                     # would collide with /quality/runs route
    ]
    for name in bad:
        assert _QUALITY_DS_RE.match(name) is None, f"{name!r} should be rejected"


def test_quality_ka_root_shards_by_token_prefix_and_stays_under_root():
    """Shard = first 2 hex chars; always under /data/lake/template-quality-ka, no traversal."""
    assert _quality_ka_root("_quality_6b230d285b68") == "/data/lake/template-quality-ka/6b"
    assert _quality_ka_root("_quality_a1f0c3d4e5f6") == "/data/lake/template-quality-ka/a1"
    root = _quality_ka_root("_quality_6b230d285b68")
    assert root.startswith("/data/lake/template-quality-ka/")
    assert ".." not in root and "/" not in root.split("template-quality-ka/", 1)[1]

