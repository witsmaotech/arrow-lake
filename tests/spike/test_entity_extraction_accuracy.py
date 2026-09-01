"""Spike: Entity extraction accuracy benchmark.

Requires an LLM provider (Ollama recommended) configured and accessible.
Tests F1 score against a 50-sample gold standard. Target: F1 > 0.75.

Service endpoints align with docker-compose.prod.yml:
  - LLM: ARROW_LAKE__EMBEDDING__API_BASE (Ollama default)
  - Override: HUGEGRAPH_LLM_PROVIDER, HUGEGRAPH_LLM_MODEL, HUGEGRAPH_LLM_API_BASE

Usage:
    uv run pytest tests/spike/test_entity_extraction_accuracy.py -v -m spike -s

Full benchmark (50 samples):
    HUGEGRAPH_FULL_BENCHMARK=1 uv run pytest tests/spike/test_entity_extraction_accuracy.py -v -m spike -s
"""



from __future__ import annotations

import pytest

pytestmark = pytest.mark.spike

import contextlib
import json
import os
import re
from pathlib import Path

import pytest
from arrow_lake.config import LLMConfig, LLMProviderType
from arrow_lake.rag.provider import LLMMessage, create_llm_provider

from tests.conftest_services import OLLAMA_API_BASE, ollama_reachable

GOLD_STANDARD_PATH = Path(__file__).parent / "fixtures" / "entity_gold_standard.json"

LLM_PROVIDER = os.getenv("HUGEGRAPH_LLM_PROVIDER", "ollama")
LLM_MODEL = os.getenv("HUGEGRAPH_LLM_MODEL", "gemma4:26b")
LLM_API_BASE = os.getenv(
    "HUGEGRAPH_LLM_API_BASE",
    OLLAMA_API_BASE,
)

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

TARGET_F1 = 0.75


def _load_gold_standard() -> list[dict]:
    with open(GOLD_STANDARD_PATH) as f:
        return json.load(f)


def _parse_entities(response: str) -> list[dict]:
    text = response.strip()
    code_block = re.search(r"```(?:json)?\s*\n?(.*?)```", text, re.DOTALL)
    if code_block:
        text = code_block.group(1).strip()

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
    return name.strip().lower().rstrip(".,").rstrip("，。")


def _fuzzy_match(pred_name: str, exp_name: str) -> bool:
    p = _normalize_entity(pred_name)
    e = _normalize_entity(exp_name)
    if not p or not e:
        return False
    if p == e:
        return True
    if p in e or e in p:
        return True
    min_len = min(len(p), len(e))
    return min_len >= 2 and (p[:min_len] == e[:min_len] or p[:min_len] == e)


def _calculate_metrics(predicted: list[dict], expected: list[dict]) -> dict:
    pred_names = [e["name"] for e in predicted]
    exp_names = [e["name"] for e in expected]

    if not pred_names and not exp_names:
        return {"precision": 1.0, "recall": 1.0, "f1": 1.0}

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


def _skip_if_llm_unavailable(exc: Exception) -> None:
    """Skip test on auth/connection/server errors from LLM provider."""
    msg = str(exc)
    if "401" in msg or "UNAUTHORIZED" in msg:
        pytest.skip(f"LLM provider returned auth error: {msg}")
    if "ConnectError" in msg or "Connection refused" in msg or "connection" in msg.lower():
        pytest.skip(f"LLM provider not reachable: {msg}")
    if "502" in msg or "503" in msg or "504" in msg:
        pytest.skip(f"LLM provider returned server error: {msg}")


@pytest.fixture
def gold_standard() -> list[dict]:
    return _load_gold_standard()


@pytest.fixture
async def llm_provider():
    """Create an LLM provider for entity extraction."""
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

    @pytest.mark.asyncio
    async def test_llm_available(self, llm_provider) -> None:
        """Verify the LLM provider is accessible."""
        from arrow_lake.exceptions import RAGError

        try:
            resp = await llm_provider.generate(
                [LLMMessage(role="user", content="Reply with just the word OK")]
            )
        except RAGError as exc:
            _skip_if_llm_unavailable(exc)
            raise
        assert "ok" in resp.content.lower(), f"LLM not responding: {resp.content}"

    def _build_messages(self, text: str) -> list[LLMMessage]:
        return [
            LLMMessage(role="system", content=ENTITY_EXTRACT_SYSTEM),
            LLMMessage(role="user", content=ENTITY_EXTRACT_PROMPT.format(text=text)),
        ]

    @pytest.mark.asyncio
    async def test_extraction_format(self, llm_provider, gold_standard: list[dict]) -> None:
        """Verify extraction returns parseable JSON for all samples."""
        from arrow_lake.exceptions import RAGError

        samples = gold_standard[:5]
        failures = []

        for sample in samples:
            messages = self._build_messages(sample["text"])
            try:
                resp = await llm_provider.generate(messages)
            except RAGError as exc:
                _skip_if_llm_unavailable(exc)
                raise
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

    @pytest.mark.asyncio
    async def test_extraction_accuracy(self, llm_provider, gold_standard: list[dict]) -> None:
        """Extract entities from gold standard and measure F1."""
        from arrow_lake.exceptions import RAGError

        samples = gold_standard
        full_benchmark = os.getenv("HUGEGRAPH_FULL_BENCHMARK", "0") == "1"
        if not full_benchmark:
            samples = samples[:10]

        all_metrics = []

        for sample in samples:
            messages = self._build_messages(sample["text"])
            try:
                resp = await llm_provider.generate(messages)
            except RAGError as exc:
                _skip_if_llm_unavailable(exc)
                raise
            predicted = _parse_entities(resp.content)
            expected = sample["entities"]
            metrics = _calculate_metrics(predicted, expected)
            all_metrics.append(metrics)

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


# ---------------------------------------------------------------------------
# Production-backend accuracy (v1.7.0 §12.8): legacy vs he extractor.
# ---------------------------------------------------------------------------
# The bespoke ENTITY_EXTRACT_* prompt above is a standalone benchmark that
# bypasses the real extractors. The parametrized test below runs the SAME gold
# standard through the production extractors used by KGBuilder — legacy
# EntityExtractor and HyperExtractExtractor (he) — so F1 reflects production
# code and the two backends are directly comparable. Configure the he backend
# via HUGEGRAPH_LLM_PROVIDER/MODEL/API_BASE (same env vars as legacy).


def _extraction_result_to_entities(result: object) -> list[dict]:
    """Convert an ExtractionResult to the [{name, type}] shape _calculate_metrics expects."""
    return [
        {"name": e.name, "type": e.entity_type}
        for e in result.entities
    ]


@pytest.fixture
def legacy_extractor(llm_provider):
    """Production legacy EntityExtractor (uses the shared llm_provider fixture)."""
    from arrow_lake.knowledge_graph.extractor import EntityExtractor

    return EntityExtractor(llm_provider)


@pytest.fixture
def he_extractor():
    """Production HyperExtractExtractor (he backend), default template."""
    from arrow_lake.knowledge_graph.doc_type_router import DocTypeRouter
    from arrow_lake.knowledge_graph.he_extractor import HyperExtractExtractor

    cfg = LLMConfig(
        provider=LLMProviderType(LLM_PROVIDER),
        model=LLM_MODEL,
        api_base=LLM_API_BASE,
        temperature=0.0,
        max_tokens=2048,
        timeout_seconds=300.0,
    )
    return HyperExtractExtractor(
        cfg,
        doc_type_router=DocTypeRouter({}, default_template="general/default_graph"),
        language="zh",
    )


@pytest.mark.spike
@pytest.mark.asyncio
@pytest.mark.parametrize("backend", ["legacy", "he"])
async def test_extraction_accuracy_production(
    backend,
    legacy_extractor,
    he_extractor,
    gold_standard: list[dict],
) -> None:
    """Measure F1 of the production extractor (legacy vs he) on the gold standard."""
    from arrow_lake.exceptions import RAGError

    extractor = legacy_extractor if backend == "legacy" else he_extractor
    samples = gold_standard
    if os.getenv("HUGEGRAPH_FULL_BENCHMARK", "0") != "1":
        samples = samples[:10]

    all_metrics: list[dict] = []
    for sample in samples:
        try:
            result = await extractor.extract(sample["text"], chunk_id="bench")
        except RAGError as exc:
            _skip_if_llm_unavailable(exc)
            raise
        predicted = _extraction_result_to_entities(result)
        all_metrics.append(_calculate_metrics(predicted, sample["entities"]))

    avg_p = sum(m["precision"] for m in all_metrics) / len(all_metrics)
    avg_r = sum(m["recall"] for m in all_metrics) / len(all_metrics)
    avg_f1 = sum(m["f1"] for m in all_metrics) / len(all_metrics)

    print(
        f"\n[{backend}] samples={len(all_metrics)} "
        f"P={avg_p:.3f} R={avg_r:.3f} F1={avg_f1:.3f} (target {TARGET_F1:.2f})"
    )
    assert avg_f1 >= TARGET_F1, (
        f"[{backend}] F1={avg_f1:.3f} below target {TARGET_F1}. "
        f"P={avg_p:.3f}, R={avg_r:.3f}"
    )
