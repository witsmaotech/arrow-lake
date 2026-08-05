#!/usr/bin/env python3
"""E2E document chunking scenarios — multi-industry validation.

Validates DocumentChunker across 6 industries with 7 strategies.
Each scenario verifies:
  - Chunk count is reasonable (not too few, not too many)
  - No empty chunks
  - Page numbers are preserved
  - Chunk sizes respect configured limits
  - Strategy-specific quality assertions

Usage:
    uv run python examples/chunking/e2e_chunking_scenarios.py
    uv run python examples/chunking/e2e_chunking_scenarios.py --scenario finance
    uv run python examples/chunking/e2e_chunking_scenarios.py --strategy semchunk
"""

from __future__ import annotations

import argparse
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

# Ensure project root on sys.path
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from arrow_lake.config._enums import ChunkStrategy
from arrow_lake.ingest.chunker import Chunk, DocumentChunker

DATA_DIR = Path(__file__).resolve().parent / "data"

# ---------------------------------------------------------------------------
# Scenario definitions
# ---------------------------------------------------------------------------

BUILTIN_STRATEGIES = [
    ChunkStrategy.PAGE,
    ChunkStrategy.PARAGRAPH,
    ChunkStrategy.RECURSIVE,
]

OPTIONAL_STRATEGIES = [
    ChunkStrategy.SEMCHUNK,
    ChunkStrategy.CHONKIE_TOKEN,
    ChunkStrategy.CHONKIE_SEMANTIC,
    ChunkStrategy.CHONKIE_SDPM,
]


@dataclass(frozen=True)
class Scenario:
    name: str
    industry: str
    file: str
    pages: int  # how many logical pages to split into
    min_chunks: int = 1
    max_chunks: int = 200
    quality_checks: list[str] = field(default_factory=list)


SCENARIOS: dict[str, Scenario] = {
    "finance": Scenario(
        name="Financial Annual Report",
        industry="Finance",
        file="finance_annual_report.txt",
        pages=5,
        min_chunks=5,
        max_chunks=80,
        quality_checks=[
            "38.76",
            "华夏科技",
            "12.45",
        ],
    ),
    "tech": Scenario(
        name="Tech Architecture Document",
        industry="Technology",
        file="tech_architecture_doc.txt",
        pages=4,
        min_chunks=4,
        max_chunks=60,
        quality_checks=[
            "DuckDB",
            "SessionManager",
            "Lance",
            "Mixin",
        ],
    ),
    "medical": Scenario(
        name="Clinical Guideline",
        industry="Healthcare",
        file="medical_clinical_guideline.txt",
        pages=5,
        min_chunks=5,
        max_chunks=80,
        quality_checks=[
            "HbA1c",
            "eGFR",
            "二甲双胍",
            "SGLT2",
        ],
    ),
    "business": Scenario(
        name="Market Analysis Report",
        industry="Business",
        file="business_market_analysis.txt",
        pages=5,
        min_chunks=5,
        max_chunks=80,
        quality_checks=[
            "1,280",
            "44.5%",
            "华夏科技",
            "RAG",
        ],
    ),
    "education": Scenario(
        name="University Curriculum",
        industry="Education",
        file="education_curriculum.txt",
        pages=5,
        min_chunks=5,
        max_chunks=80,
        quality_checks=[
            "CS-AI-101",
            "Transformer",
            "RAG",
            "Lance",
        ],
    ),
    "literature": Scenario(
        name="Bilingual Literature",
        industry="Literature",
        file="literature_bilingual.txt",
        pages=2,
        min_chunks=2,
        max_chunks=60,
        quality_checks=[
            "巴金",
            "Gatsby",
            "月",
        ],
    ),
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def load_pages(scenario: Scenario) -> list[tuple[int, str]]:
    """Load scenario file and split into logical pages."""
    path = DATA_DIR / scenario.file
    if not path.exists():
        print(f"  [ERROR] File not found: {path}")
        return []

    text = path.read_text(encoding="utf-8")
    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]

    if not paragraphs:
        return []

    # Distribute paragraphs across pages evenly
    per_page = max(1, len(paragraphs) // scenario.pages)
    pages: list[tuple[int, str]] = []
    for i in range(0, len(paragraphs), per_page):
        page_num = (i // per_page) + 1
        page_text = "\n\n".join(paragraphs[i : i + per_page])
        pages.append((page_num, page_text))

    return pages


def get_available_strategies() -> list[ChunkStrategy]:
    """Return list of strategies that can be used (library available)."""
    available = list(BUILTIN_STRATEGIES)

    try:
        import semchunk  # noqa: F401
        available.append(ChunkStrategy.SEMCHUNK)
    except ImportError:
        pass

    try:
        import chonkie  # noqa: F401
        available.extend([
            ChunkStrategy.CHONKIE_TOKEN,
            ChunkStrategy.CHONKIE_SEMANTIC,
            ChunkStrategy.CHONKIE_SDPM,
        ])
    except ImportError:
        pass

    return available


# ---------------------------------------------------------------------------
# Validators
# ---------------------------------------------------------------------------


def validate_no_empty_chunks(chunks: list[Chunk]) -> list[str]:
    errors = []
    for c in chunks:
        if not c.text.strip():
            errors.append(f"chunk_index={c.chunk_index} page={c.page_number} is empty")
    return errors


def validate_page_numbers(chunks: list[Chunk], pages: list[tuple[int, str]]) -> list[str]:
    errors = []
    valid_pages = {p[0] for p in pages}
    for c in chunks:
        if c.page_number not in valid_pages:
            errors.append(f"chunk_index={c.chunk_index} has invalid page_number={c.page_number}")
    return errors


def validate_chunk_index_sequential(chunks: list[Chunk]) -> list[str]:
    errors = []
    for i, c in enumerate(chunks):
        if c.chunk_index != i:
            errors.append(f"Expected chunk_index={i}, got {c.chunk_index}")
    return errors


def validate_chunk_size(chunks: list[Chunk], max_size: int) -> list[str]:
    errors = []
    for c in chunks:
        if len(c.text) > max_size:
            errors.append(
                f"chunk_index={c.chunk_index} size={len(c.text)} exceeds max_size={max_size}"
            )
    return errors


def validate_quality_checks(
    chunks: list[Chunk], checks: list[str], strategy: ChunkStrategy,
) -> list[str]:
    """Verify key terms appear in at least one chunk."""
    all_text = "\n".join(c.text for c in chunks)
    errors = []
    for check in checks:
        if check not in all_text:
            errors.append(f"Key term '{check}' not found in any chunk")
    return errors


def validate_reasonable_count(
    chunks: list[Chunk], scenario: Scenario,
) -> list[str]:
    errors = []
    if len(chunks) < scenario.min_chunks:
        errors.append(
            f"Too few chunks: {len(chunks)} < min={scenario.min_chunks}"
        )
    if len(chunks) > scenario.max_chunks:
        errors.append(
            f"Too many chunks: {len(chunks)} > max={scenario.max_chunks}"
        )
    return errors


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------


def run_scenario(
    scenario: Scenario, strategy: ChunkStrategy, chunk_size: int = 512,
) -> dict:
    """Run a single scenario + strategy combination and return metrics."""
    pages = load_pages(scenario)
    if not pages:
        return {
            "ok": False,
            "scenario": scenario.name,
            "industry": scenario.industry,
            "strategy": strategy.value,
            "chunk_count": 0,
            "pages": 0,
            "avg_chunk_size": 0,
            "min_chunk_size": 0,
            "max_chunk_size": 0,
            "elapsed_ms": 0,
            "errors": ["Failed to load pages"],
        }

    chunker = DocumentChunker(
        strategy=strategy,
        chunk_size=chunk_size,
        chunk_overlap=chunk_size // 8,
    )

    t0 = time.monotonic()
    chunks = chunker.chunk(pages)
    elapsed = time.monotonic() - t0

    errors: list[str] = []
    errors.extend(validate_no_empty_chunks(chunks))
    errors.extend(validate_page_numbers(chunks, pages))
    errors.extend(validate_chunk_index_sequential(chunks))
    errors.extend(validate_reasonable_count(chunks, scenario))

    if strategy not in (ChunkStrategy.PAGE, ChunkStrategy.PARAGRAPH):
        errors.extend(validate_chunk_size(chunks, int(chunk_size * 2.0)))

    errors.extend(validate_quality_checks(chunks, scenario.quality_checks, strategy))

    sizes = [len(c.text) for c in chunks] if chunks else [0]

    return {
        "ok": len(errors) == 0,
        "scenario": scenario.name,
        "industry": scenario.industry,
        "strategy": strategy.value,
        "chunk_count": len(chunks),
        "pages": len(pages),
        "avg_chunk_size": sum(sizes) / len(sizes) if sizes else 0,
        "min_chunk_size": min(sizes),
        "max_chunk_size": max(sizes),
        "elapsed_ms": round(elapsed * 1000, 1),
        "errors": errors,
    }


def print_result(result: dict) -> None:
    status = "PASS" if result["ok"] else "FAIL"
    icon = "+" if result["ok"] else "x"
    print(
        f"  {icon} [{status:>4}] {result['strategy']:<20} "
        f"chunks={result['chunk_count']:>3}  "
        f"avg_size={result['avg_chunk_size']:>6.0f}  "
        f"min={result['min_chunk_size']:>5}  max={result['max_chunk_size']:>5}  "
        f"time={result['elapsed_ms']:>6.1f}ms"
    )
    if result["errors"]:
        for e in result["errors"]:
            print(f"         - {e}")


def main() -> None:
    parser = argparse.ArgumentParser(description="E2E chunking scenario validation")
    parser.add_argument(
        "--scenario", type=str, default="all",
        choices=["all", "finance", "tech", "medical", "business", "education", "literature"],
        help="Scenario to run (default: all)",
    )
    parser.add_argument(
        "--strategy", type=str, default="all",
        help="Strategy to test (default: all available)",
    )
    parser.add_argument(
        "--chunk-size", type=int, default=512,
        help="Target chunk size (default: 512)",
    )
    parser.add_argument(
        "--verbose", "-v", action="store_true",
        help="Show chunk details",
    )
    args = parser.parse_args()

    strategies = get_available_strategies()
    if args.strategy != "all":
        try:
            s = ChunkStrategy(args.strategy)
            if s not in strategies:
                print(f"Strategy '{args.strategy}' not available (library not installed)")
                return
            strategies = [s]
        except ValueError:
            print(f"Unknown strategy: {args.strategy}")
            print(f"Available: {[s.value for s in strategies]}")
            return

    scenarios_to_run = SCENARIOS
    if args.scenario != "all":
        scenarios_to_run = {args.scenario: SCENARIOS[args.scenario]}

    print("=" * 80)
    print("Arrow Lake E2E Document Chunking Validation")
    print(f"Strategies: {[s.value for s in strategies]}")
    print(f"Chunk size: {args.chunk_size}")
    print("=" * 80)

    total = 0
    passed = 0
    failed_results: list[dict] = []

    for key, scenario in scenarios_to_run.items():
        print(f"\n--- {scenario.name} ({scenario.industry}) [{scenario.file}] ---")

        for strategy in strategies:
            result = run_scenario(scenario, strategy, args.chunk_size)
            print_result(result)
            total += 1
            if result["ok"]:
                passed += 1
            else:
                failed_results.append(result)

    # Summary
    print("\n" + "=" * 80)
    print(f"Results: {passed}/{total} passed")
    if failed_results:
        print(f"\nFailed ({len(failed_results)}):")
        for r in failed_results:
            print(f"  x {r['scenario']} / {r['strategy']}: {r['errors'][0]}")
        sys.exit(1)
    else:
        print("All scenarios passed.")
    print("=" * 80)


if __name__ == "__main__":
    main()
