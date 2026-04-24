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

_ENTITY_EXTRACT_PROMPT = """\
You are a knowledge graph entity extractor. Analyze the given text and extract \
entities and relations in JSON format.

Return ONLY valid JSON with this structure:
{
  "entities": [
    {"name": "entity_name", "type": "person|organization|location|concept|event", "confidence": 0.0-1.0, "properties": {}}
  ],
  "relations": [
    {"source": "entity_name", "target": "entity_name", "relation": "relation_type", "confidence": 0.0-1.0, "properties": {}}
  ]
}

Rules:
- "confidence" is optional (defaults to 1.0)
- "properties" is optional (defaults to empty)
- "type" for entities should be one of: person, organization, location, concept, event
- Extract ALL named entities and meaningful relations
- If no entities or relations are found, return empty arrays

Text to analyze:
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

    async def extract(self, text: str, *, chunk_id: str = "") -> ExtractionResult:
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
            lines = [l for l in lines if not l.strip().startswith("```")]
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
        """Parse the JSON dict from LLM into ExtractionResult."""
        threshold = self._confidence_threshold

        entities: list[ExtractedEntity] = []
        for item in data.get("entities", []):
            confidence = item.get("confidence", 1.0)
            if confidence < threshold:
                continue
            props = _parse_properties(item.get("properties"))
            entities.append(
                ExtractedEntity(
                    name=item["name"],
                    entity_type=item["type"],
                    properties=props,
                )
            )

        relations: list[ExtractedRelation] = []
        for item in data.get("relations", []):
            confidence = item.get("confidence", 1.0)
            if confidence < threshold:
                continue
            props = _parse_properties(item.get("properties"))
            relations.append(
                ExtractedRelation(
                    source=item["source"],
                    target=item["target"],
                    relation_type=item["relation"],
                    properties=props,
                )
            )

        return ExtractionResult(
            entities=tuple(entities),
            relations=tuple(relations),
            raw_text=raw_text,
        )
