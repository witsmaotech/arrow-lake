"""Prompt template system for RAG pipeline."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import StrEnum

from jinja2 import Environment, StrictUndefined

logger = logging.getLogger(__name__)

# Shared environment for all templates
_ENV = Environment(undefined=StrictUndefined, autoescape=True)


class PromptType(StrEnum):
    """Types of RAG prompt templates."""

    QA = "qa"
    SUMMARY = "summary"
    EXTRACT = "extract"
    MULTIMODAL = "multimodal"


@dataclass(frozen=True)
class PromptTemplate:
    """A Jinja2-based prompt template."""

    name: str
    type: PromptType
    template: str
    description: str = ""
    _compiled: object = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "_compiled", _ENV.from_string(self.template))

    def render(self, **kwargs: object) -> str:
        """Render the template with the given variables.

        Raises:
            jinja2.UndefinedError: If a required variable is missing.
        """
        return self._compiled.render(**kwargs)


# Built-in templates
_BUILTIN_TEMPLATES: list[PromptTemplate] = [
    PromptTemplate(
        name="default_qa",
        type=PromptType.QA,
        description="Default question-answering with context grounding",
        template=(
            "Answer the following question based on the provided context. "
            "If the context does not contain enough information, say so.\n\n"
            "Context:\n{{ context }}\n\n"
            "Question: {{ question }}\n\n"
            "Answer:"
        ),
    ),
    PromptTemplate(
        name="entity_extract",
        type=PromptType.EXTRACT,
        description="Extract named entities (people, organizations, locations, dates) from text",
        template=(
            "Extract all named entities (people, organizations, locations, dates) "
            "from the following text. Return them as a structured list.\n\n"
            "Text:\n{{ text }}\n\n"
            "Entities:"
        ),
    ),
    PromptTemplate(
        name="summarize",
        type=PromptType.SUMMARY,
        description="Concise summarization preserving key facts and figures",
        template=(
            "Summarize the following text concisely, "
            "preserving key facts and figures.\n\n"
            "Text:\n{{ text }}\n\n"
            "Summary:"
        ),
    ),
    PromptTemplate(
        name="graph_qa",
        type=PromptType.QA,
        description="Question-answering with both document and knowledge graph context",
        template=(
            "You are a helpful assistant with access to both document context "
            "and a knowledge graph. Use the knowledge graph context for "
            "structured entity relationships and the document context for "
            "detailed information.\n\n"
            "Answer the question based on the provided context. "
            "If the context contains knowledge graph triplets, use them to "
            "understand entity relationships.\n\n"
            "Context:\n{{ context }}\n\n"
            "Question: {{ question }}\n\n"
            "Answer:"
        ),
    ),
    PromptTemplate(
        name="entity_extract_from_question",
        type=PromptType.EXTRACT,
        description="Extract entity names from a question for KG lookup",
        template=(
            "Extract entity names from the question for knowledge graph lookup.\n\n"
            "List all named entities (people, organizations, locations, concepts) "
            "mentioned in this question. Return ONLY a JSON array of entity "
            "name strings.\n\n"
            "Question: {{ question }}\n\n"
            "Entities:"
        ),
    ),
]


class PromptRegistry:
    """Registry for prompt templates with built-in defaults."""

    def __init__(self) -> None:
        self._templates: dict[str, PromptTemplate] = {
            t.name: t for t in _BUILTIN_TEMPLATES
        }

    def get(self, name: str) -> PromptTemplate | None:
        """Look up a template by name. Returns None if not found."""
        return self._templates.get(name)

    def register(self, template: PromptTemplate) -> None:
        """Register a template (overwrites if name exists)."""
        self._templates[template.name] = template

    def list_templates(self) -> list[str]:
        """List all registered template names."""
        return list(self._templates.keys())

    def list_by_type(self, prompt_type: PromptType) -> list[PromptTemplate]:
        """List all templates of a given type."""
        return [t for t in self._templates.values() if t.type == prompt_type]
