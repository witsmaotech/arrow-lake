"""Entity extractor using LLM for knowledge graph construction.

Extracts entities and relations from text chunks via LLM prompts,
returning frozen dataclass results suitable for KG insertion.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from typing import Any

from arrow_lake.exceptions import ErrorCode, KGError
from arrow_lake.rag.provider import BaseLLMProvider, LLMMessage

logger = logging.getLogger(__name__)

# Common suffixes for repairing truncated JSON from LLM output.
_JSON_REPAIR_SUFFIXES = [
    '"}]}',  # truncated mid-value in entity/relation
    '"}\n]}',
    '"}]}\n]}',
    '}]}',
    ']}',
    '"}]',
    '"}]}}',
    'null}]}',
    'null\n]}',
    '"}\n  ]\n}',
]

# Generic Chinese words that LLM frequently extracts as entities but are
# clearly NOT specific proper nouns.  Keep this set minimal — the prompt
# does the heavy lifting; this is a safety net for the most obvious cases.
# Only include single/double-character abstract words that never identify
# a specific entity.
_GENERIC_ENTITY_STOPWORDS: frozenset[str] = frozenset({
    "优化", "实践", "方法", "构建", "研究", "综述", "分析",
    "评估", "实验", "数据", "结果", "问题", "方案", "应用",
    "场景", "任务", "领域", "理论", "功能", "性能", "效果",
    "优势", "挑战", "策略", "目标", "条件", "特征",
    "因素", "标准", "趋势", "发展", "影响", "改进", "提升",
    "支持", "实现", "集成", "验证", "测试",
    "核心", "关键", "重要", "主要", "有效", "潜在",
    "基础", "能力", "水平", "规模", "范围", "程度",
})

_ENTITY_EXTRACT_PROMPT = """\
你是一个专业的中文知识图谱实体关系抽取器。从给定的文本中提取实体和关系。

返回 JSON（不要包含其他文字）：
{
  "entities": [
    {"name": "实体名", "type": "类型", "confidence": 0.0-1.0}
  ],
  "relations": [
    {"source": "实体名", "target": "实体名", "relation": "关系描述", "confidence": 0.0-1.0}
  ]
}

## 实体类型
- person: 具体人物名（如"张三"、"李四"）
- organization: 机构/公司名（如"OpenAI"、"清华大学"）
- location: 地理位置（如"北京"、"硅谷"）
- concept: 具体的技术概念/产品名（如"Transformer"、"GPT-4"、"LanceDB"、"向量数据库"）
- event: 具体事件（如"WWDC 2024"、"AlphaGo 人机大战"）

## 重要过滤规则
- 不要提取通用词/抽象词作为实体，例如：优化、实践、方法、技术、构建、研究、综述、分析、评估、实验、数据、系统、模型、网络、算法、结果、问题、方案、应用、场景、任务、领域、理论、框架、功能、性能、效果、优势、挑战、策略、目标、条件、环境
- 不要提取单个汉字或两个字的通用词
- 实体名必须是文本中明确出现的具体名词或专有名词
- 只提取文本中实际提到的、有明确指代的实体

## 关系规则
- 只连接同一段文本中明确有关联的实体
- relation 用简短的动词短语描述关系，例如："开发了"、"收购了"、"位于"、"属于"
- 不要为没有明确关联的实体强行创建关系
- source 和 target 必须是 entities 中已存在的实体名

## 置信度评分标准
- 0.9-1.0: 文本中明确提到，关系清晰
- 0.7-0.8: 文本中有提及但关系需要推断
- <0.7: 不确定，不要输出

## 示例
输入: "OpenAI 发布了 GPT-4，该模型基于 Transformer 架构，由 Google 团队最初提出。"
输出:
{"entities":[{"name":"OpenAI","type":"organization","confidence":0.95},{"name":"GPT-4","type":"concept","confidence":0.95},{"name":"Transformer","type":"concept","confidence":0.95},{"name":"Google","type":"organization","confidence":0.9}],"relations":[{"source":"OpenAI","target":"GPT-4","relation":"发布了","confidence":0.95},{"source":"GPT-4","target":"Transformer","relation":"基于","confidence":0.9},{"source":"Google","target":"Transformer","relation":"最初提出","confidence":0.9}]}

输入: "团队使用 Apache Arrow 进行数据处理。"
输出:
{"entities":[{"name":"Apache Arrow","type":"concept","confidence":0.95}],"relations":[]}

待分析文本：
"""


def _parse_properties(raw: dict[str, Any] | None) -> tuple[tuple[str, Any], ...]:
    """Convert a properties dict to a tuple of key-value pairs."""
    if not raw:
        return ()
    return tuple(sorted(raw.items()))


@dataclass(frozen=True)
class ExtractedEntity:
    """A single entity extracted from text."""

    name: str
    entity_type: str
    properties: tuple[tuple[str, Any], ...] = ()


@dataclass(frozen=True)
class ExtractedRelation:
    """A relation between two extracted entities."""

    source: str
    target: str
    relation_type: str
    properties: tuple[tuple[str, Any], ...] = ()


@dataclass(frozen=True)
class ExtractionResult:
    """Result of entity extraction from a text chunk."""

    entities: tuple[ExtractedEntity, ...]
    relations: tuple[ExtractedRelation, ...]
    raw_text: str


class EntityExtractor:
    """Extract entities and relations from text using an LLM provider.

    Args:
        llm: LLM provider for generation.
        confidence_threshold: Minimum confidence score (0-1) to keep an
            entity or relation. Items below this threshold are silently
            dropped.
    """

    def __init__(
        self,
        llm: BaseLLMProvider,
        *,
        confidence_threshold: float = 0.7,
    ) -> None:
        self._llm = llm
        self._confidence_threshold = confidence_threshold

    async def extract(self, text: str, *, chunk_id: str = "", doc_type: str | None = None) -> ExtractionResult:
        """Extract entities and relations from a single text chunk.

        Args:
            text: The text to analyze.
            chunk_id: Optional chunk identifier for logging.

        Returns:
            ExtractionResult with parsed entities and relations.

        Raises:
            KGError: If LLM returns invalid JSON.
        """
        stripped = text.strip()
        if not stripped:
            return ExtractionResult(entities=(), relations=(), raw_text=text)

        messages = [
            LLMMessage(role="system", content=_ENTITY_EXTRACT_PROMPT),
            LLMMessage(role="user", content=stripped),
        ]

        try:
            response = await self._llm.generate(messages)
        except (RuntimeError, ValueError) as exc:
            raise KGError(
                error_code=ErrorCode.KG_EXTRACT_FAILED,
                message=f"LLM call failed for chunk {chunk_id}: {exc}",
                context={"chunk_id": chunk_id},
            ) from exc

        try:
            data = json.loads(response.content)
        except (json.JSONDecodeError, ValueError):
            # qwen3.x may wrap JSON in thinking tags or return empty content.
            # Try extracting JSON from the response.
            data = self._try_parse_json(response.content)
            if data is None:
                logger.warning(
                    "Failed to extract JSON from LLM response for chunk %s, skipping. Raw: %s",
                    chunk_id,
                    response.content[:200],
                )
                return ExtractionResult(entities=(), relations=(), raw_text=text)

        return self._parse_extraction(data, text)

    @staticmethod
    def _try_parse_json(text: str) -> dict[str, Any] | None:
        """Try to extract valid JSON from potentially malformed LLM output.

        Handles: empty strings, thinking-tag wrapped content, markdown fences.
        """
        if not text or not text.strip():
            return None

        # Strip thinking tags: <think...</think > or </think >...<think >...</think >
        stripped = text.strip()
        if stripped.startswith("<think"):
            end = stripped.find("</think")
            if end != -1:
                stripped = stripped[end + len("</think"):].strip()

        # Strip markdown code fences
        if stripped.startswith("```"):
            lines = stripped.split("\n")
            lines = [line for line in lines if not line.strip().startswith("```")]
            stripped = "\n".join(lines).strip()

        # Strip leading non-JSON (find first { or [ from the start)
        start_idx = len(stripped)
        for start_char in ("{", "["):
            idx = stripped.find(start_char)
            if 0 <= idx < start_idx:
                start_idx = idx
        if start_idx > 0:
            stripped = stripped[start_idx:]

        # Strip trailing non-JSON (find last } or ] from the end)
        end_idx = -1
        for end_char in ("}", "]"):
            idx = stripped.rfind(end_char)
            if idx > end_idx:
                end_idx = idx
        if end_idx >= 0:
            stripped = stripped[: end_idx + 1]

        try:
            result = json.loads(stripped)
            if isinstance(result, dict):
                return result
        except (json.JSONDecodeError, ValueError):
            pass

        # Last resort: try to repair truncated JSON by closing open structures.
        # Append closing chars in reverse order of how they would appear.
        for suffix in _JSON_REPAIR_SUFFIXES:
            try:
                result = json.loads(stripped + suffix)
                if isinstance(result, dict):
                    return result
            except (json.JSONDecodeError, ValueError):
                continue
        return None

    async def extract_batch(
        self, chunks: list[tuple[str, str]]
    ) -> list[ExtractionResult]:
        """Extract entities from multiple chunks concurrently.

        Args:
            chunks: List of (chunk_id, text) tuples.

        Returns:
            List of ExtractionResult in the same order as input chunks.
        """
        if not chunks:
            return []

        semaphore = asyncio.Semaphore(5)

        async def _extract_one(chunk_id: str, text: str) -> ExtractionResult:
            async with semaphore:
                return await self.extract(text, chunk_id=chunk_id)

        tasks = [_extract_one(cid, text) for cid, text in chunks]
        return await asyncio.gather(*tasks)

    def _parse_extraction(
        self, data: dict[str, Any], raw_text: str
    ) -> ExtractionResult:
        """Parse the JSON dict from LLM into ExtractionResult.

        Applies three filtering layers:
        1. Confidence threshold (LLM-reported score).
        2. Generic stopword removal (catches words the prompt missed).
        3. Relation validity (source/target must exist in filtered entities).
        """
        threshold = self._confidence_threshold

        entities: list[ExtractedEntity] = []
        for item in data.get("entities", []):
            confidence = item.get("confidence", 0.5)
            if confidence < threshold:
                continue
            name = item.get("name", "").strip()
            if not name:
                continue
            if name in _GENERIC_ENTITY_STOPWORDS:
                continue
            props = _parse_properties(item.get("properties"))
            entities.append(
                ExtractedEntity(
                    name=name,
                    entity_type=item.get("type", "concept"),
                    properties=props,
                )
            )

        valid_names = {e.name for e in entities}

        relations: list[ExtractedRelation] = []
        for item in data.get("relations", []):
            confidence = item.get("confidence", 0.5)
            if confidence < threshold:
                continue
            source = item.get("source", "")
            target = item.get("target", "")
            if source not in valid_names or target not in valid_names:
                continue
            props = _parse_properties(item.get("properties"))
            relations.append(
                ExtractedRelation(
                    source=source,
                    target=target,
                    relation_type=item.get("relation", ""),
                    properties=props,
                )
            )

        return ExtractionResult(
            entities=tuple(entities),
            relations=tuple(relations),
            raw_text=raw_text,
        )
