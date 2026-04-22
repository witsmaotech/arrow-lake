"""Tests for RAG prompt template system — M2 Day 4."""

from __future__ import annotations

import pytest
from arrow_lake.rag.prompt import (
    PromptRegistry,
    PromptTemplate,
    PromptType,
)
from jinja2 import UndefinedError

# ---------------------------------------------------------------------------
# PromptType
# ---------------------------------------------------------------------------


class TestPromptType:
    def test_values(self) -> None:
        assert PromptType.QA == "qa"
        assert PromptType.SUMMARY == "summary"
        assert PromptType.EXTRACT == "extract"
        assert PromptType.MULTIMODAL == "multimodal"


# ---------------------------------------------------------------------------
# PromptTemplate
# ---------------------------------------------------------------------------


class TestPromptTemplate:
    def test_render_simple(self) -> None:
        tmpl = PromptTemplate(
            name="test",
            type=PromptType.QA,
            template="Hello {{ name }}!",
        )
        result = tmpl.render(name="World")
        assert result == "Hello World!"

    def test_render_with_context(self) -> None:
        tmpl = PromptTemplate(
            name="test",
            type=PromptType.QA,
            template="Context:\n{{ context }}\n\nQuestion: {{ question }}",
        )
        result = tmpl.render(context="Some text", question="What?")
        assert "Some text" in result
        assert "What?" in result

    def test_render_missing_variable_raises(self) -> None:
        tmpl = PromptTemplate(
            name="test",
            type=PromptType.QA,
            template="Hello {{ name }}!",
        )
        with pytest.raises(UndefinedError):
            tmpl.render()  # missing 'name'

    def test_frozen(self) -> None:
        tmpl = PromptTemplate(name="t", type=PromptType.QA, template="{{ x }}")
        with pytest.raises(AttributeError):
            tmpl.name = "other"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# PromptRegistry
# ---------------------------------------------------------------------------


class TestPromptRegistry:
    def test_builtin_templates_exist(self) -> None:
        registry = PromptRegistry()
        templates = registry.list_templates()
        assert "default_qa" in templates
        assert "entity_extract" in templates
        assert "summarize" in templates

    def test_get_builtin_template(self) -> None:
        registry = PromptRegistry()
        tmpl = registry.get("default_qa")
        assert tmpl is not None
        assert tmpl.name == "default_qa"
        assert tmpl.type == PromptType.QA

    def test_get_nonexistent_returns_none(self) -> None:
        registry = PromptRegistry()
        assert registry.get("does_not_exist") is None

    def test_register_custom_template(self) -> None:
        registry = PromptRegistry()
        custom = PromptTemplate(
            name="my_qa",
            type=PromptType.QA,
            template="Answer: {{ question }} using {{ context }}",
        )
        registry.register(custom)
        retrieved = registry.get("my_qa")
        assert retrieved is not None
        assert retrieved.name == "my_qa"

    def test_register_overwrites_existing(self) -> None:
        registry = PromptRegistry()
        original = registry.get("default_qa")
        custom = PromptTemplate(
            name="default_qa",
            type=PromptType.QA,
            template="Custom: {{ question }}",
        )
        registry.register(custom)
        updated = registry.get("default_qa")
        assert updated is not None
        assert updated.template != original.template

    def test_list_by_type(self) -> None:
        registry = PromptRegistry()
        qa_templates = registry.list_by_type(PromptType.QA)
        assert len(qa_templates) >= 1
        assert all(t.type == PromptType.QA for t in qa_templates)

    def test_builtin_entity_extract(self) -> None:
        registry = PromptRegistry()
        tmpl = registry.get("entity_extract")
        assert tmpl is not None
        assert tmpl.type == PromptType.EXTRACT

    def test_builtin_summarize(self) -> None:
        registry = PromptRegistry()
        tmpl = registry.get("summarize")
        assert tmpl is not None
        assert tmpl.type == PromptType.SUMMARY

    def test_render_default_qa(self) -> None:
        registry = PromptRegistry()
        tmpl = registry.get("default_qa")
        assert tmpl is not None
        result = tmpl.render(context="Sample context", question="Test question")
        assert "Sample context" in result
        assert "Test question" in result
