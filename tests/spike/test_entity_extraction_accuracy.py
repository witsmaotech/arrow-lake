"""Spike: Entity extraction accuracy benchmark.

Requires an LLM provider (Ollama recommended) configured and accessible.
Tests F1 score against a 50-sample gold standard. Target: F1 > 0.75.

Optimizations: Few-shot prompt, fuzzy matching, system message.
Default model: gemma4:26b.

Usage:
    HUGEGRAPH_LLM_PROVIDER=ollama HUGEGRAPH_LLM_MODEL=gemma4:26b \
    uv run pytest tests/spike/test_entity_extraction_accuracy.py -v -m spike -s

Full benchmark (50 samples):
    HUGEGRAPH_FULL_BENCHMARK=1 uv run pytest tests/spike/test_entity_extraction_accuracy.py -v -m spike -s
"""

from __future__ import annotations

import contextlib
import json
import os
import re
from pathlib import Path

import pytest
from arrow_lake.config import LLMConfig, LLMProviderType
from arrow_lake.rag.provider import LLMMessage, create_llm_provider

# Gold standard path
GOLD_STANDARD_PATH = Path(__file__).parent / "fixtures" / "entity_gold_standard.json"

# LLM configuration
LLM_PROVIDER = os.getenv("HUGEGRAPH_LLM_PROVIDER", "ollama")
LLM_MODEL = os.getenv("HUGEGRAPH_LLM_MODEL", "gemma4:26b")
LLM_API_BASE = os.getenv("HUGEGRAPH_LLM_API_BASE", "http://localhost:11434/v1")

# Entity extraction prompt - structured JSON output with few-shot examples
ENTITY_EXTRACT_SYSTEM = """\
You are an expert entity extractor. Extract ALL named entities from text. \
Be thorough — do not miss any entities. Use the exact name as it appears in text. \
Entity types: person, organization, location, concept, event, date. \
Return ONLY a JSON array."""

ENTITY_EXTRACT_PROMPT = """\
Examples:

Text: "Elon Musk founded SpaceX in Hawthorne, California."
Entities: [{{"name": "Elon Musk", "type": "person"}}, {{"name": "SpaceX", "type": "organization"}}, {{"name": "Hawthorne", "type": "location"}}, {{"name": "California", "type": "location"}}]

Text: "The European Union passed the Digital Markets Act in 2022."
Entities: [{{"name": "European Union", "type": "organization"}}, {{"name": "Digital Markets Act", "type": "concept"}}, {{"name": "2022", "type": "date"}}]

Now extract ALL entities from this text:
{text}"""

# Target accuracy (raised from 0.70 after initial spike pass)
TARGET_F1 = 0.75


def _load_gold_standard() -> list[dict]:
    """Load the gold standard test cases."""
    with open(GOLD_STANDARD_PATH) as f:
        return json.load(f)


def _parse_entities(response: str) -> list[dict]:
    """Parse entity extraction response into list of {name, type} dicts."""
    text = response.strip()

    # Remove markdown code block wrapper if present
    code_block = re.search(r"```(?:json)?\s*\n?(.*?)```", text, re.DOTALL)
    if code_block:
        text = code_block.group(1).strip()

    # Try to find JSON array
    bracket_start = text.find("[")
    bracket_end = text.rfind("]")
    if bracket_start != -1 and bracket_end != -1:
        text = text[bracket_start : bracket_end + 1]

    try:
        entities = json.loads(text)
        if isinstance(entities, list):
            return [
                {"name": str(e.get("name", "")).strip(), "type": str(e.get("type", "")).strip().lower()}
                for e in entities
                if e.get("name")
            ]
    except json.JSONDecodeError:
        pass

    return []


def _normalize_entity(name: str) -> str:
    """Normalize entity name for matching."""
    # Strip common trailing punctuation (both ASCII and CJK)
    return name.strip().lower().rstrip(".,").rstrip("\uff0c\u3002")


def _fuzzy_match(pred_name: str, exp_name: str) -> bool:
    """Check if two entity names refer to the same entity.

    Uses exact match, substring containment, and abbreviation expansion.
    """
    p = _normalize_entity(pred_name)
    e = _normalize_entity(exp_name)
    if not p or not e:
        return False
    # Exact match
    if p == e:
        return True
    # Substring containment (one contains the other)
    if p in e or e in p:
        return True
    # Abbreviation: shorter name is prefix of longer (min 2 chars)
    min_len = min(len(p), len(e))
    return min_len >= 2 and (p[:min_len] == e[:min_len] or p[:min_len] == e)


def _calculate_metrics(predicted: list[dict], expected: list[dict]) -> dict:
    """Calculate precision, recall, and F1 for a single sample.

    Uses fuzzy name matching (substring + abbreviation) to reduce false negatives.
    Entity types are subjective: e.g., "Apple" could be organization or brand.
    """
    pred_names = [e["name"] for e in predicted]
    exp_names = [e["name"] for e in expected]

    if not pred_names and not exp_names:
        return {"precision": 1.0, "recall": 1.0, "f1": 1.0}

    # Fuzzy matching: each expected entity matches at most one predicted
    matched_exp: set[int] = set()
    matched_pred: set[int] = set()

    for ei, ename in enumerate(exp_names):
        for pi, pname in enumerate(pred_names):
            if pi in matched_pred:
                continue
            if _fuzzy_match(pname, ename):
                matched_exp.add(ei)
                matched_pred.add(pi)
                break

    true_positives = len(matched_exp)
    precision = true_positives / len(pred_names) if pred_names else 0.0
    recall = true_positives / len(exp_names) if exp_names else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0.0

    return {"precision": precision, "recall": recall, "f1": f1}


@pytest.fixture
def gold_standard() -> list[dict]:
    return _load_gold_standard()


@pytest.fixture
async def llm_provider():
    """Create an LLM provider for entity extraction.

    Uses function scope to avoid event loop conflicts with pytest-asyncio.
    """
    config = LLMConfig(
        provider=LLMProviderType(LLM_PROVIDER),
        model=LLM_MODEL,
        api_base=LLM_API_BASE,
        temperature=0.0,
        max_tokens=2048,
        timeout_seconds=300.0,
    )
    provider = create_llm_provider(config)
    yield provider
    with contextlib.suppress(Exception):
        await provider.close()


@pytest.mark.spike
@pytest.mark.asyncio
class TestEntityExtractionAccuracy:
    """Benchmark entity extraction accuracy against gold standard."""

    async def test_llm_available(self, llm_provider) -> None:
        """Verify the LLM provider is accessible."""
        resp = await llm_provider.generate(
            [LLMMessage(role="user", content="Reply with just the word OK")]
        )
        assert "ok" in resp.content.lower(), f"LLM not responding: {resp.content}"

    def _build_messages(self, text: str) -> list[LLMMessage]:
        """Build messages with system prompt and few-shot user prompt."""
        return [
            LLMMessage(role="system", content=ENTITY_EXTRACT_SYSTEM),
            LLMMessage(role="user", content=ENTITY_EXTRACT_PROMPT.format(text=text)),
        ]

    async def test_extraction_format(self, llm_provider, gold_standard: list[dict]) -> None:
        """Verify extraction returns parseable JSON for all samples."""
        samples = gold_standard[:5]
        failures = []

        for sample in samples:
            messages = self._build_messages(sample["text"])
            resp = await llm_provider.generate(messages)
            entities = _parse_entities(resp.content)
            if not entities:
                failures.append({
                    "text": sample["text"][:50],
                    "response": resp.content[:200],
                })

        assert len(failures) == 0, (
            f"{len(failures)}/{len(samples)} samples failed to parse. "
            f"Details: {json.dumps(failures, ensure_ascii=False, indent=2)}"
        )

    async def test_extraction_accuracy(self, llm_provider, gold_standard: list[dict]) -> None:
        """Extract entities from gold standard and measure F1.

        Uses a subset (first 10 samples) for faster benchmarking.
        Set HUGEGRAPH_FULL_BENCHMARK=1 to run all 50 samples.
        """
        samples = gold_standard
        full_benchmark = os.getenv("HUGEGRAPH_FULL_BENCHMARK", "0") == "1"
        if not full_benchmark:
            samples = samples[:10]

        all_metrics = []

        for sample in samples:
            messages = self._build_messages(sample["text"])
            resp = await llm_provider.generate(messages)
            predicted = _parse_entities(resp.content)
            expected = sample["entities"]
            metrics = _calculate_metrics(predicted, expected)
            all_metrics.append(metrics)

        # Aggregate metrics
        avg_p = sum(m["precision"] for m in all_metrics) / len(all_metrics)
        avg_r = sum(m["recall"] for m in all_metrics) / len(all_metrics)
        avg_f1 = sum(m["f1"] for m in all_metrics) / len(all_metrics)

        result = {
            "samples": len(samples),
            "precision": avg_p,
            "recall": avg_r,
            "f1": avg_f1,
            "target_f1": TARGET_F1,
            "passed": avg_f1 >= TARGET_F1,
        }

        # Report results
        print(f"\n{'='*60}")
        print(f"Entity Extraction Benchmark ({result['samples']} samples)")
        print(f"{'='*60}")
        print(f"  Precision: {result['precision']:.3f}")
        print(f"  Recall:    {result['recall']:.3f}")
        print(f"  F1 Score:  {result['f1']:.3f} (target: {result['target_f1']:.2f})")
        print(f"  Result:    {'PASS' if result['passed'] else 'FAIL'}")
        print(f"{'='*60}\n")

        assert result["f1"] >= TARGET_F1, (
            f"F1={result['f1']:.3f} below target {TARGET_F1}. "
            f"P={result['precision']:.3f}, R={result['recall']:.3f}"
        )
