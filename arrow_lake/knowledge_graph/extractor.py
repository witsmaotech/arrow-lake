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
你是一位资深的中文知识图谱抽取专家。任务：从给定文本抽取高质量实体与关系，构建结构化知识图。

## 输出格式（仅输出纯 JSON，禁止 markdown 代码围栏 / 解释 / 前后缀文字）
{
  "entities": [
    {"name": "实体名", "type": "类型", "description": "一句话说明", "properties": {"k": "v"}, "confidence": 0.0-1.0}
  ],
  "relations": [
    {"source": "实体名", "target": "实体名", "relation": "关系类型", "confidence": 0.0-1.0}
  ]
}

## 实体类型（受控词表，选最贴切的一个）
- person：具体人名
- organization：机构/公司/部门
- location：地点/区域
- project：工程项目/建设工程/方案（如"芜湖市城市生命线安全工程一期"）
- subsystem：子系统/专项模块（如"燃气安全监测系统"、"桥梁监测系统"）
- facility：设施/设备/传感器（如"燃气泄漏传感器"、"应变计"）
- technology：技术/方法/产品/平台（如"物联网平台"、"边缘计算"、"Apache Arrow"）
- standard：标准/规范/政策/法规（如"GB 50028"）
- metric：指标/参数/阈值（如"报警阈值"）
- event：具体事件/事故
- time：时间/阶段（如"2022年2月"、"一期"）
- concept：其他具体专有名词/技术概念

## 关系类型（优先用受控动词短语；描述不下再用更具体的，保持简短）
建设/建造、包含/组成、采用/使用、负责/主管、监管/管理、位于/所在、监测、联动/协同、发生在、属于、影响、导致、提出/发布、集成/接入、开发了、基于

## 抽取规则（严格遵守）
1. 只抽文本**明确陈述**的实体/关系，禁止用常识/推理脑补。
2. 实体名须为文本中**原样出现**的具体名词/专有名词。**禁止**通用抽象词（系统、工程、技术、方法、数据、模型、平台、应用、建设、管理、优化、研究、方案、问题、目标、效果、性能、功能、需求、能力、体系、框架、措施、内容、工作、方面、领域、方向、阶段、原则 等），除非是专有名词的一部分。
3. **去重**：同一实体只抽一次，选文本中最完整的名称；不同指代不重复抽取。
4. **properties**：提取该实体在文本中的关键属性（数量/规模/位置/时间/指标值等），无则 {}。
5. 关系必须**同段文本内有明确关联**；source/target 须是 entities 中已存在的实体名；禁止凭共现强行连边。
6. 置信度：明确陈述 0.9-1.0；轻度推断 0.7-0.8；<0.7 不输出。

## 示例
输入: "芜湖市城市生命线安全工程一期覆盖燃气、桥梁、供水、排水四个专项，采用物联网感知设备实时监测，2022年2月编制完成。"
输出:
{"entities":[{"name":"芜湖市城市生命线安全工程一期","type":"project","description":"芜湖市城市生命线安全工程一期","properties":{"编制时间":"2022年2月","专项数":"4"},"confidence":0.95},{"name":"燃气专项","type":"subsystem","description":"燃气安全监测专项","properties":{},"confidence":0.9},{"name":"桥梁专项","type":"subsystem","properties":{},"confidence":0.9},{"name":"供水专项","type":"subsystem","properties":{},"confidence":0.9},{"name":"排水专项","type":"subsystem","properties":{},"confidence":0.9},{"name":"物联网感知设备","type":"technology","description":"实时监测用传感设备","properties":{},"confidence":0.9}],"relations":[{"source":"芜湖市城市生命线安全工程一期","target":"燃气专项","relation":"包含","confidence":0.95},{"source":"芜湖市城市生命线安全工程一期","target":"桥梁专项","relation":"包含","confidence":0.95},{"source":"芜湖市城市生命线安全工程一期","target":"物联网感知设备","relation":"采用","confidence":0.9}]}

输入: "OpenAI 发布了 GPT-4，该模型基于 Transformer 架构，由 Google 团队最初提出。"
输出:
{"entities":[{"name":"OpenAI","type":"organization","properties":{},"confidence":0.95},{"name":"GPT-4","type":"technology","description":"OpenAI发布的大模型","properties":{},"confidence":0.95},{"name":"Transformer","type":"technology","description":"模型架构","properties":{},"confidence":0.95},{"name":"Google","type":"organization","properties":{},"confidence":0.9}],"relations":[{"source":"OpenAI","target":"GPT-4","relation":"发布了","confidence":0.95},{"source":"GPT-4","target":"Transformer","relation":"基于","confidence":0.9},{"source":"Google","target":"Transformer","relation":"提出","confidence":0.9}]}

输入: "团队使用 Apache Arrow 进行数据处理。"
输出:
{"entities":[{"name":"Apache Arrow","type":"technology","description":"数据处理列式格式","properties":{},"confidence":0.95}],"relations":[]}

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

        # Retry+指数退避：瞬时错误（429 限流 / empty content / 网络抖动）重试，
        # 避免单 chunk 失败拖垮整批 kg_build。重试耗尽则 skip 该 chunk（不入图）。
        response = None
        last_exc: Exception | None = None
        for attempt in range(4):  # 1 次初始 + 3 次重试
            try:
                response = await self._llm.generate(messages)
                break
            except Exception as exc:  # RAGError(429/empty) / RuntimeError / ValueError / httpx
                last_exc = exc
                if attempt >= 3:
                    break
                delay = min(2.0 * (2 ** attempt), 30.0)  # 2s, 4s, 8s 退避
                logger.warning(
                    "kg_extract_retry chunk=%s attempt=%d/3 sleep=%.1fs err=%s",
                    chunk_id, attempt + 1, delay, str(exc)[:120],
                )
                await asyncio.sleep(delay)
        if response is None:
            logger.warning(
                "kg_extract_giveup chunk=%s retries=3 err=%s — skipping chunk",
                chunk_id, str(last_exc)[:120],
            )
            return ExtractionResult(entities=(), relations=(), raw_text=text)

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
