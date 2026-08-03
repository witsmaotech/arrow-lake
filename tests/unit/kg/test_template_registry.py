"""v1.10.0 P2: template_registry validation + filesystem CRUD.

Note: descriptions use ASCII placeholders — validate_template_yaml checks
structure (presence/shape), not description wording, so ASCII keeps the YAML
parser off any CJK-in-flow-map edge cases and the focus on validation logic.
"""

from __future__ import annotations

import textwrap

import pytest

from arrow_lake.knowledge_graph.template_registry import (
    TemplateNameConflict,
    TemplateNotFoundError,
    TemplateValidationError,
    content_hash,
    delete_template,
    load_template,
    save_template,
    template_path,
    validate_template_yaml,
)

_GOOD = """\
language: [zh, en]
name: security_concept_graph
type: graph
tags: [security]
description: {zh: sec-graph, en: security graph}
output:
  entities:
    fields:
      - {name: name, type: str, description: {zh: name, en: ent-name}}
      - {name: type, type: str, description: {zh: etype, en: ent-type}}
  relations:
    fields:
      - {name: source, type: str, description: {zh: src, en: rel-src}}
      - {name: target, type: str, description: {zh: tgt, en: rel-tgt}}
      - {name: type, type: str, description: {zh: rtype, en: rel-type}}
guideline:
  target: {zh: expert, en: security expert}
"""


# --- validation ------------------------------------------------------------

def test_valid_yaml_returns_parsed_dict():
    data = validate_template_yaml(_GOOD)
    assert data["name"] == "security_concept_graph"
    assert data["type"] == "graph"


def test_default_type_is_graph():
    raw = _GOOD.replace("type: graph\n", "")
    data = validate_template_yaml(raw, expect_name="security_concept_graph")
    assert data["type"] == "graph"  # default injected into parsed dict


def test_missing_required_entity_field_name():
    raw = _GOOD.replace(
        "- {name: name, type: str, description: {zh: name, en: ent-name}}\n", "")
    with pytest.raises(TemplateValidationError) as exc:
        validate_template_yaml(raw)
    assert any("entities" in p or "missing" in m for p, m in exc.value.errors)


def test_missing_relation_required_field():
    # Inline block-style YAML (relations lack the required `type` field).
    # Avoids a PyYAML scanner quirk: removing the last flow-map list item that
    # precedes a dedented block key mis-parses; block style is unambiguous.
    raw = textwrap.dedent(
        """\
        language: [zh, en]
        name: t
        type: graph
        output:
          entities:
            fields:
              - name: name
                type: str
                description: d
          relations:
            fields:
              - name: source
                type: str
                description: d
              - name: target
                type: str
                description: d
        guideline:
          target:
            zh: e
            en: e
        """
    )
    with pytest.raises(TemplateValidationError) as exc:
        validate_template_yaml(raw)
    assert any("relations" in p for p, m in exc.value.errors)


def test_invalid_field_type_rejected():
    raw = _GOOD.replace(
        "- {name: name, type: str, description: {zh: name, en: ent-name}}",
        "- {name: name, type: text, description: {zh: name, en: ent-name}}",
    )
    with pytest.raises(TemplateValidationError) as exc:
        validate_template_yaml(raw)
    assert any(".type" in p for p, m in exc.value.errors)


def test_bad_name_rejected():
    raw = _GOOD.replace("name: security_concept_graph", "name: Bad-Name")
    with pytest.raises(TemplateValidationError) as exc:
        validate_template_yaml(raw)
    assert any(p == "name" for p, m in exc.value.errors)


def test_expect_name_mismatch():
    # strict validation (the /validate endpoint) still flags a filename
    # mismatch; save_template (strict=False) tolerates it as a draft.
    with pytest.raises(TemplateValidationError) as exc:
        validate_template_yaml(_GOOD, expect_name="other_name")
    assert any(p == "filename" for p, m in exc.value.errors)


def test_reserved_name_collision():
    with pytest.raises(TemplateValidationError) as exc:
        raw = _GOOD.replace("security_concept_graph", "entity_graph")
        validate_template_yaml(raw, reserved_names={"entity_graph"})
    assert any("collides" in m for p, m in exc.value.errors)


def test_oversized_yaml_rejected():
    raw = "# comment\n" + ("x: y\n" * 20000) + "\n" + _GOOD
    with pytest.raises(TemplateValidationError) as exc:
        validate_template_yaml(raw)
    assert any("bytes" in m for p, m in exc.value.errors)


def test_forbidden_merge_key_rejected():
    raw = "anchors: &a\n  k: v\nfoo:\n  <<: *a\n" + _GOOD
    with pytest.raises(TemplateValidationError) as exc:
        validate_template_yaml(raw)
    assert any("forbidden token" in m for p, m in exc.value.errors)


def test_missing_guideline_rejected():
    raw = _GOOD.split("guideline:")[0]
    with pytest.raises(TemplateValidationError) as exc:
        validate_template_yaml(raw)
    assert any(p == "guideline" for p, m in exc.value.errors)


def test_duplicate_field_rejected():
    raw = _GOOD.replace(
        "- {name: name, type: str, description: {zh: name, en: ent-name}}",
        "- {name: name, type: str, description: dup}\n"
        "      - {name: name, type: str, description: {zh: name, en: ent-name}}",
    )
    with pytest.raises(TemplateValidationError) as exc:
        validate_template_yaml(raw)
    assert any("duplicate" in m for p, m in exc.value.errors)


# --- category validation (M5) ----------------------------------------------

def test_category_optional_when_no_dictionary():
    """Without known_categories, category is NOT required (backward compat for
    LLM self-heal + callers that don't gate on category)."""
    validate_template_yaml(_GOOD)  # _GOOD has no category — must pass
    validate_template_yaml(_GOOD + "category: anything\n")  # and an arbitrary one is fine


def test_category_required_when_dictionary_given():
    with pytest.raises(TemplateValidationError) as exc:
        validate_template_yaml(_GOOD, known_categories={"finance", "legal"})
    assert any(p == "category" for p, m in exc.value.errors)


def test_category_must_be_in_dictionary():
    raw = _GOOD + "category: nope\n"
    with pytest.raises(TemplateValidationError) as exc:
        validate_template_yaml(raw, known_categories={"finance", "legal"})
    assert any(p == "category" and "known" in m for p, m in exc.value.errors)


def test_category_membership_case_insensitive():
    raw = _GOOD + "category: Finance\n"  # uppercase — should still match
    validate_template_yaml(raw, known_categories={"finance", "legal"})


def test_category_valid_member_passes():
    raw = _GOOD + "category: finance\n"
    data = validate_template_yaml(raw, known_categories={"finance", "legal"})
    assert data["category"] == "finance"


def test_save_tolerates_soft_errors(tmp_path):
    # save (strict=False, default) tolerates a missing category (soft/advisory)
    # — a work-in-progress template may be saved despite quality issues.
    path = save_template("security_concept_graph", _GOOD, tmp_path, known_categories={"finance"})
    assert path.is_file()
    # strict save (validate-endpoint semantics) still rejects the missing category
    with pytest.raises(TemplateValidationError):
        save_template("security_concept_graph", _GOOD, tmp_path, known_categories={"finance"}, strict=True)
    # a HARD error (unparseable YAML) blocks even non-strict save
    with pytest.raises(TemplateValidationError):
        save_template("security_concept_graph", "name: security_concept_graph\n  : [unclosed", tmp_path)


# --- filesystem CRUD -------------------------------------------------------

def test_save_load_delete_roundtrip(tmp_path):
    p = save_template("security_concept_graph", _GOOD, tmp_path)
    assert p == tmp_path / "security_concept_graph.yaml"
    assert p.is_file()
    assert load_template("security_concept_graph", tmp_path) == _GOOD
    assert delete_template("security_concept_graph", tmp_path) is True
    assert not p.exists()
    assert delete_template("security_concept_graph", tmp_path) is False


def test_load_missing_raises(tmp_path):
    with pytest.raises(TemplateNotFoundError):
        load_template("absent", tmp_path)


def test_save_reserved_name_raises(tmp_path):
    with pytest.raises(TemplateNameConflict):
        save_template(
            "entity_graph",
            _GOOD.replace("security_concept_graph", "entity_graph"),
            tmp_path, reserved_names={"entity_graph"})


def test_save_invalid_does_not_write(tmp_path):
    bad = _GOOD.replace("name: security_concept_graph", "name: Bad")
    with pytest.raises(TemplateValidationError):
        save_template("Bad", bad, tmp_path)
    assert not (tmp_path / "Bad.yaml").exists()


def test_path_traversal_blocked(tmp_path):
    with pytest.raises(TemplateValidationError):
        template_path("../etc", tmp_path)


def test_content_hash_stable():
    assert content_hash(_GOOD) == content_hash(_GOOD)
    assert content_hash(_GOOD) != content_hash(_GOOD + " ")
