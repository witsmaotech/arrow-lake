"""Embedding encode throughput benchmark (v1.10.2 §5.3 / review performance C1).

Quantifies the cost of encoding text rows — the data anchor for the P1
embedding-backfill scheme choice: "drop+re-add column reusing existing vectors"
(scheme a) vs "fragment-level rewrite of null rows" (scheme b). The choice
hinges on whether re-encoding old rows is cheap or prohibitive; this benchmark
gives rows/sec for the configured embedder.

Uses a local OpenAI-compatible backend (ollama ``qwen3-embedding:0.6b``).
**Skips** (does NOT fail) when no backend is reachable — the benchmark suite
must not hard-depend on a running embedder.

Run::

    .venv/bin/python3 -m pytest tests/benchmark/test_bench_embed.py -m benchmark -s
"""



from __future__ import annotations

import pytest

pytestmark = pytest.mark.benchmark

import pytest

from tests.benchmark.benchmark_report import BenchmarkReport

_OLLAMA_BASE = "http://127.0.0.1:11434/v1"
_MODEL = "qwen3-embedding:0.6b"
_SAMPLE = (
    "应急管理是指政府及其他公共机构在突发事件的事前预防、事发应对、事中处置和善后管理 "
    "过程中,通过建立必要的应对机制,对公众进行救援保障的一系列措施。"
)


def _make_encoder():
    from arrow_lake.embed.encoder import ApiEmbeddingEncoder

    return ApiEmbeddingEncoder(
        api_base=_OLLAMA_BASE, api_key="ollama",
        model_name=_MODEL, batch_size=16,
    )


@pytest.fixture(scope="module")
def embed_ok() -> bool:
    """Probe the backend once; skip the whole module if unreachable."""
    try:
        enc = _make_encoder()
        res = enc.encode([_SAMPLE])
        return bool(getattr(res, "embeddings", None) is not None)
    except Exception:
        return False


@pytest.mark.benchmark
class TestEmbedBenchmark:
    """Embedding encode throughput at 100 / 500 rows."""

    def test_encode_throughput_100(self, embed_ok: bool) -> None:
        if not embed_ok:
            pytest.skip("ollama embed backend not reachable at " + _OLLAMA_BASE)
        enc = _make_encoder()
        texts = [_SAMPLE] * 100
        report = BenchmarkReport("embed_100")
        elapsed = report.measure(
            f"encode 100 texts ({_MODEL})",
            lambda: enc.encode(texts),
            rows=100,
            repeats=1,
        )
        report.print_summary()
        print(report.to_json())
        print(f"[meta] throughput: {100 / elapsed:,.0f} texts/s")
        assert elapsed > 0

    def test_encode_throughput_200(self, embed_ok: bool) -> None:
        if not embed_ok:
            pytest.skip("ollama embed backend not reachable at " + _OLLAMA_BASE)
        enc = _make_encoder()
        texts = [_SAMPLE] * 200
        report = BenchmarkReport("embed_200")
        elapsed = report.measure(
            f"encode 200 texts ({_MODEL})",
            lambda: enc.encode(texts),
            rows=200,
            repeats=1,
        )
        report.print_summary()
        print(report.to_json())
        print(f"[meta] throughput: {200 / elapsed:,.0f} texts/s")
        assert elapsed > 0
