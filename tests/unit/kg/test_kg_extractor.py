"""Unit tests for EntityExtractor (mock LLM provider)."""

from __future__ import annotations

import json

import pytest
from arrow_lake.exceptions import ErrorCode, KGError
from arrow_lake.knowledge_graph.extractor import (
    EntityExtractor,
    ExtractedEntity,
    ExtractedRelation,
    ExtractionResult,
)
from arrow_lake.rag.provider import LLMResponse


@pytest.fixture
def mock_llm() -> object:
    """Create a mock LLM provider."""
    from unittest.mock import AsyncMock

    llm = AsyncMock()
    llm.generate.return_value = LLMResponse(
        content=json.dumps({
            "entities": [
                {"name": "Alice", "type": "person"},
                {"name": "Acme Corp", "type": "organization"},
            ],
            "relations": [
                {"source": "Alice", "target": "Acme Corp", "relation": "works_at"},
            ],
        }),
        model="test-model",
        provider="test",
    )
    return llm


# ---------------------------------------------------------------------------
# ExtractedEntity / ExtractedRelation frozen dataclass
# ---------------------------------------------------------------------------


def test_extracted_entity_is_frozen() -> None:
    entity = ExtractedEntity(name="Alice", entity_type="person")
    with pytest.raises(AttributeError):
        entity.name = "Bob"  # type: ignore[misc]


def test_extracted_relation_is_frozen() -> None:
    rel = ExtractedRelation(source="Alice", target="Bob", relation_type="knows")
    with pytest.raises(AttributeError):
        rel.source = "Carol"  # type: ignore[misc]


def test_extraction_result_is_frozen() -> None:
    result = ExtractionResult(entities=(), relations=(), raw_text="hello")
    with pytest.raises(AttributeError):
        result.entities = ()  # type: ignore[misc]


def test_extracted_entity_with_properties() -> None:
    entity = ExtractedEntity(
        name="Alice",
        entity_type="person",
        properties=(("age", 30), ("city", "NYC")),
    )
    assert entity.properties == (("age", 30), ("city", "NYC"))


def test_extracted_relation_with_properties() -> None:
    rel = ExtractedRelation(
        source="Alice",
        target="Bob",
        relation_type="knows",
        properties=(("since", 2020),),
    )
    assert rel.properties == (("since", 2020),)


# ---------------------------------------------------------------------------
# extract() success
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_extract_success(mock_llm: object) -> None:
    extractor = EntityExtractor(mock_llm)
    result = await extractor.extract("Alice works at Acme Corp.", chunk_id="c1")

    assert len(result.entities) == 2
    assert result.entities[0] == ExtractedEntity(name="Alice", entity_type="person")
    assert result.entities[1] == ExtractedEntity(name="Acme Corp", entity_type="organization")

    assert len(result.relations) == 1
    assert result.relations[0] == ExtractedRelation(
        source="Alice", target="Acme Corp", relation_type="works_at"
    )

    assert result.raw_text == "Alice works at Acme Corp."
    mock_llm.generate.assert_awaited_once()

    # Verify the prompt was sent as a system message
    call_args = mock_llm.generate.call_args[0][0]
    assert any(m.role == "system" for m in call_args)
    assert any(m.role == "user" for m in call_args)


# ---------------------------------------------------------------------------
# extract() with entities that have confidence
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_extract_filters_by_confidence(mock_llm: object) -> None:
    """Entities/relations below confidence threshold are dropped."""
    mock_llm.generate.return_value = LLMResponse(
        content=json.dumps({
            "entities": [
                {"name": "High", "type": "concept", "confidence": 0.9},
                {"name": "Low", "type": "concept", "confidence": 0.3},
            ],
            "relations": [
                {
                    "source": "High", "target": "Low", "relation": "links",
                    "confidence": 0.4,
                },
            ],
        }),
        model="test-model",
        provider="test",
    )
    extractor = EntityExtractor(mock_llm, confidence_threshold=0.7)
    result = await extractor.extract("test text")

    assert len(result.entities) == 1
    assert result.entities[0].name == "High"
    assert len(result.relations) == 0  # below threshold


# ---------------------------------------------------------------------------
# extract() JSON parse failure
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_extract_json_parse_error(mock_llm: object) -> None:
    """Invalid JSON from LLM should raise KGError."""
    mock_llm.generate.return_value = LLMResponse(
        content="not valid json {{{",
        model="test-model",
        provider="test",
    )
    extractor = EntityExtractor(mock_llm)
    with pytest.raises(KGError) as exc_info:
        await extractor.extract("some text")
    assert exc_info.value.error_code == ErrorCode.KG_EXTRACT_FAILED


# ---------------------------------------------------------------------------
# extract() empty text
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_extract_empty_text(mock_llm: object) -> None:
    """Empty text should return empty result without calling LLM."""
    extractor = EntityExtractor(mock_llm)
    result = await extractor.extract("", chunk_id="c1")

    assert result.entities == ()
    assert result.relations == ()
    assert result.raw_text == ""
    mock_llm.generate.assert_not_awaited()


# ---------------------------------------------------------------------------
# extract() whitespace-only text
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_extract_whitespace_text(mock_llm: object) -> None:
    """Whitespace-only text should return empty result."""
    extractor = EntityExtractor(mock_llm)
    result = await extractor.extract("   \n\t  ")

    assert result.entities == ()
    assert result.relations == ()
    mock_llm.generate.assert_not_awaited()


# ---------------------------------------------------------------------------
# extract() missing fields in LLM response
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_extract_missing_entities_key(mock_llm: object) -> None:
    """LLM response missing 'entities' key should return empty entities."""
    mock_llm.generate.return_value = LLMResponse(
        content=json.dumps({"relations": []}),
        model="test-model",
        provider="test",
    )
    extractor = EntityExtractor(mock_llm)
    result = await extractor.extract("some text")

    assert result.entities == ()
    assert result.relations == ()


# ---------------------------------------------------------------------------
# extract() with properties in LLM response
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_extract_with_properties(mock_llm: object) -> None:
    """Entities and relations can include arbitrary properties."""
    mock_llm.generate.return_value = LLMResponse(
        content=json.dumps({
            "entities": [
                {
                    "name": "Event A",
                    "type": "event",
                    "confidence": 0.95,
                    "properties": {"date": "2024-01-01", "location": "Beijing"},
                },
            ],
            "relations": [
                {
                    "source": "Alice",
                    "target": "Event A",
                    "relation": "participates_in",
                    "confidence": 0.8,
                    "properties": {"role": "organizer"},
                },
            ],
        }),
        model="test-model",
        provider="test",
    )
    extractor = EntityExtractor(mock_llm)
    result = await extractor.extract("Alice organized Event A.")

    assert len(result.entities) == 1
    assert result.entities[0].name == "Event A"
    # Properties should be stored as tuple of key-value pairs
    assert result.entities[0].properties == (("date", "2024-01-01"), ("location", "Beijing"))

    assert len(result.relations) == 1
    assert result.relations[0].properties == (("role", "organizer"),)


# ---------------------------------------------------------------------------
# extract_batch()
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_extract_batch(mock_llm: object) -> None:
    """extract_batch processes multiple chunks concurrently."""
    extractor = EntityExtractor(mock_llm)
    chunks = [("c1", "Alice works at Acme."), ("c2", "Bob lives in NYC.")]
    results = await extractor.extract_batch(chunks)

    assert len(results) == 2
    assert results[0].raw_text == "Alice works at Acme."
    assert results[1].raw_text == "Bob lives in NYC."
    assert mock_llm.generate.await_count == 2


@pytest.mark.asyncio
async def test_extract_batch_empty(mock_llm: object) -> None:
    """extract_batch with empty list returns empty list."""
    extractor = EntityExtractor(mock_llm)
    results = await extractor.extract_batch([])
    assert results == []
    mock_llm.generate.assert_not_awaited()
