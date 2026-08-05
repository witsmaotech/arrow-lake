"""Document chunking benchmarks — measures the ingest front-end throughput.

The chunking step is the CPU-hot front of the ingest pipeline:
``OCR (kreuzberg/docling) → page text → chunks → embed``. These benchmarks
exercise the in-process :class:`DocumentChunker` on synthetic page text so
they run anywhere with zero external dependencies (no LLM, no GPU, no OCR).

Strategies benchmarked are the always-available pure-Python ones
(``RECURSIVE``, ``PAGE``, ``PARAGRAPH``). Optional strategies
(``SEMCHUNK``/``CHONKIE_*``/``DOCLING_HYBRID``) degrade to ``RECURSIVE``
when their extras are missing, so they are intentionally excluded to keep
the measurement deterministic.

Run::

    .venv/bin/pytest tests/benchmark/test_bench_parse.py -m benchmark -s
"""

from __future__ import annotations

import pytest

from arrow_lake.config._enums import ChunkStrategy
from arrow_lake.ingest.chunker import DocumentChunker
from tests.benchmark.benchmark_report import BenchmarkReport

# Representative technical paragraph (~120 chars). Repeated per page to mimic
# real document density. This is the same seed text used by the cookbook
# benchmark (docs/cookbook/examples/53_performance_benchmark.py).
_SAMPLE_PARA = (
    "This is a sample document about machine learning and artificial intelligence. "
    "It contains multiple sentences that discuss various topics including neural networks, "
    "deep learning, natural language processing, and computer vision. "
    "The document is designed to be representative of typical text data that would be "
    "chunked and embedded for retrieval augmented generation systems. "
)


def _make_pages(n_pages: int, paragraphs_per_page: int = 5) -> list[tuple[int, str]]:
    """Build ``n_pages`` of synthetic ``(page_number, page_text)`` tuples."""
    return [(i, _SAMPLE_PARA * paragraphs_per_page) for i in range(n_pages)]


@pytest.mark.benchmark
class TestParseBenchmark:
    """Benchmark document chunking latency and throughput."""

    def test_chunk_recursive_throughput(self) -> None:
        """Recursive chunking of 20 pages → chunks (pages/sec)."""
        chunker = DocumentChunker(
            strategy=ChunkStrategy.RECURSIVE, chunk_size=512, chunk_overlap=50
        )
        pages = _make_pages(20)

        report = BenchmarkReport("parse_recursive_20p")
        elapsed = report.measure(
            "recursive chunk (20 pages, 512/50)",
            lambda: chunker.chunk(pages),
            rows=20,
            repeats=10,
        )
        n_chunks = len(chunker.chunk(pages))
        report.print_summary()
        print(report.to_json())
        print(f"[meta] chunks produced: {n_chunks} ({n_chunks / elapsed:,.0f} chunks/s)")
        assert elapsed > 0
        assert n_chunks > 0

    def test_chunk_strategy_comparison(self) -> None:
        """Compare RECURSIVE vs PAGE vs PARAGRAPH on the same 20 pages."""
        pages = _make_pages(20)
        report = BenchmarkReport("parse_strategy_compare_20p")

        results: dict[str, int] = {}
        for strategy in (ChunkStrategy.RECURSIVE, ChunkStrategy.PAGE, ChunkStrategy.PARAGRAPH):
            chunker = DocumentChunker(strategy=strategy, chunk_size=512, chunk_overlap=50)
            elapsed = report.measure(
                f"{strategy.value} chunk (20 pages)",
                lambda c=chunker: c.chunk(pages),
                rows=20,
                repeats=8,
            )
            results[strategy.value] = len(chunker.chunk(pages))
            assert elapsed > 0

        report.print_summary()
        print(report.to_json())
        print(f"[meta] chunks by strategy: {results}")

    def test_chunk_scale_100_pages(self) -> None:
        """Recursive chunking at scale: 100 pages (verifies linear throughput)."""
        chunker = DocumentChunker(
            strategy=ChunkStrategy.RECURSIVE, chunk_size=512, chunk_overlap=50
        )
        pages = _make_pages(100)

        report = BenchmarkReport("parse_recursive_100p")
        elapsed = report.measure(
            "recursive chunk (100 pages, 512/50)",
            lambda: chunker.chunk(pages),
            rows=100,
            repeats=5,
        )
        n_chunks = len(chunker.chunk(pages))
        report.print_summary()
        print(report.to_json())
        print(f"[meta] chunks produced: {n_chunks} ({n_chunks / elapsed:,.0f} chunks/s)")
        assert elapsed > 0
        assert n_chunks > 0

    def test_chunk_size_sweep(self) -> None:
        """Sweep chunk_size 256 / 512 / 1024 — effect on latency and chunk count."""
        pages = _make_pages(20)
        report = BenchmarkReport("parse_chunk_size_sweep")

        counts: dict[int, int] = {}
        for size in (256, 512, 1024):
            chunker = DocumentChunker(
                strategy=ChunkStrategy.RECURSIVE, chunk_size=size, chunk_overlap=size // 10
            )
            elapsed = report.measure(
                f"recursive chunk_size={size} (20 pages)",
                lambda c=chunker: c.chunk(pages),
                rows=20,
                repeats=8,
            )
            counts[size] = len(chunker.chunk(pages))
            assert elapsed > 0

        report.print_summary()
        print(report.to_json())
        print(f"[meta] chunks by chunk_size: {counts}")
