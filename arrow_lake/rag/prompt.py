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
            "你是一名严谨的资料分析助手。请基于下方「参考资料」回答问题，遵循这些要求：\n"
            "1. 回答要详尽、有条理：先给结论，再展开背景、关键细节、数据/依据，必要时分点陈述。\n"
            "2. 每条事实性陈述都应在句末用 [n] 标注来源，n 对应参考资料编号（如 [1]、[3]）。\n"
            "3. 若参考资料不足以完整回答，明确指出「哪部分有依据、哪部分缺失」，不要编造。\n"
            "4. 若问题涉及多个方面，逐项覆盖，不要遗漏。\n"
            "5. 用专业、客观的中文表述。\n\n"
            "参考资料：\n{{ context }}\n\n"
            "问题：{{ question }}\n\n"
            "回答："
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
            "你是一名严谨的资料分析助手，同时拥有「文档资料」和「知识图谱」两类上下文。\n"
            "知识图谱上下文（三元组）用于理解实体间的结构化关系；文档资料用于补充细节与数据。\n"
            "请遵循：\n"
            "1. 回答要详尽、有条理：先给结论，再展开背景、关系、关键细节，必要时分点陈述。\n"
            "2. 每条事实性陈述在句末用 [n] 标注来源（文档编号），关系性陈述可标注「[图谱]」。\n"
            "3. 资料不足的部分明确指出，不要编造。\n"
            "4. 用专业、客观的中文表述。\n\n"
            "参考资料：\n{{ context }}\n\n"
            "问题：{{ question }}\n\n"
            "回答："
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
