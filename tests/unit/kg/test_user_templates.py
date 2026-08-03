"""v1.10.0 P1: user extraction-template gallery merge.

Covers `_merge_user_templates` (doc_type_router.py): user YAMLs on a writable
volume are indexed into the shared gallery with absolute file paths so
``Template.create()`` loads them directly, ``source="user"``, and the gallery
rebuilds on ``reset_gallery_cache()`` (no rebuild/restart).
"""

from __future__ import annotations

import textwrap

import pytest

from arrow_lake.knowledge_graph import doc_type_router as dtr
from arrow_lake.knowledge_graph.doc_type_router import (
    TemplateGallery,
    _merge_user_templates,
    _user_templates_dir,
    get_template_gallery,
    reset_gallery_cache,
    validate_taxonomy,
)

_VALID_YAML = textwrap.dedent(
    """\
    language: [zh, en]
    name: security_concept_graph
    type: graph
    category: security
    tags: [security, asset, threat, control, 安全]
    description:
      zh: 网络安全概念图
      en: cybersecurity concept graph
    output:
      entities:
        fields:
          - {name: name, type: str, required: true}
          - {name: type, type: str, required: true}
      relations:
        fields:
          - {name: source, type: str, required: true}
          - {name: target, type: str, required: true}
          - {name: type, type: str, required: true}
    guideline:
      target:
        zh: 你是安全专家
        en: you are a security expert
    """
)


@pytest.fixture
def user_dir(tmp_path, monkeypatch):
    """Point the user-templates env var at a temp dir and reset the gallery."""
    d = tmp_path / "templates"
    d.mkdir()
    monkeypatch.setenv("ARROW_LAKE__HUGEGRAPH__HE_USER_TEMPLATES_DIR", str(d))
    reset_gallery_cache()
    yield d
    reset_gallery_cache()


def _write(d, name, yaml):
    (d / name).write_text(yaml, encoding="utf-8")


def test_user_template_indexed_with_absolute_path(user_dir):
    _write(user_dir, "security_concept_graph.yaml", _VALID_YAML)
    reset_gallery_cache()
    g = get_template_gallery()

    hits = [t for t in g.templates if t.name == "security_concept_graph"]
    assert len(hits) == 1
    t = hits[0]
    assert t.source == "user"
    # absolute file path → Template.create() loads it directly (C1/§4.7 base)
    assert t.path.endswith("security_concept_graph.yaml")
    assert "/" in t.path
    assert t.category == "security"  # domain from YAML
    assert "security" in t.tags


def test_user_template_category_empty_when_missing(user_dir):
    # M5: the "user" fallback is retired — a user template without a domain
    # category loads with an EMPTY category (routes only via explicit binding,
    # not Layer-2). CRUD validation now requires category, so this only arises
    # for hand-edited / pre-M5 templates.
    yaml = _VALID_YAML.replace("category: security\n", "")
    _write(user_dir, "no_category.yaml", yaml.replace("name: security_concept_graph", "name: no_category"))
    reset_gallery_cache()
    g = get_template_gallery()
    t = g.get(next(p for p in [t.path for t in g.templates] if "no_category" in p))
    assert t is not None
    assert t.category == ""
    assert t.source == "user"


def test_gallery_rebuild_picks_up_new_template(user_dir):
    g = get_template_gallery()
    assert not any(t.source == "user" for t in g.templates)
    _write(user_dir, "security_concept_graph.yaml", _VALID_YAML)
    reset_gallery_cache()
    g = get_template_gallery()
    assert any(t.source == "user" and t.name == "security_concept_graph" for t in g.templates)


def test_merge_user_templates_missing_dir_is_noop(tmp_path):
    g = TemplateGallery.build()
    n = len(g.templates)
    _merge_user_templates(g, tmp_path / "does_not_exist")
    assert len(g.templates) == n  # no error, no change


def test_skips_hidden_and_nonyaml(user_dir):
    _write(user_dir, ".hidden.yaml", _VALID_YAML)
    (user_dir / "readme.txt").write_text("not a template")
    _write(user_dir, "good.yaml", _VALID_YAML.replace("security_concept_graph", "good"))
    reset_gallery_cache()
    g = get_template_gallery()
    names = {t.name for t in g.templates if t.source == "user"}
    assert "good" in names
    assert not any(n.startswith("hidden") for n in names)


def test_empty_category_exempt_from_taxonomy_warning(user_dir):
    # M5: a user template without a domain category loads with category="" and
    # must NOT be flagged as drift (it routes only via explicit binding, like
    # the "project" source marker). Parallel to the pre-M5 "user" exemption.
    yaml = _VALID_YAML.replace("category: security\n", "").replace(
        "name: security_concept_graph", "name: uncat")
    _write(user_dir, "uncat.yaml", yaml)
    reset_gallery_cache()
    warnings = validate_taxonomy()
    assert not any("category ''" in w for w in warnings)


def test_user_templates_dir_env_default(monkeypatch):
    monkeypatch.delenv("ARROW_LAKE__HUGEGRAPH__HE_USER_TEMPLATES_DIR", raising=False)
    assert _user_templates_dir() == "/data/lake/templates"
