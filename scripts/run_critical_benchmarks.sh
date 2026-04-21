#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# Run all critical path benchmarks for Arrow Lake.
#
# Usage:
#   bash scripts/run_critical_benchmarks.sh          # run all benchmarks
#   bash scripts/run_critical_benchmarks.sh --save    # run and save baselines
#   bash scripts/run_critical_benchmarks.sh --ci      # CI mode: regression only
#
# Benchmarks:
#   1. Ingestion (10K rows)
#   2. Vector search (10K rows, no index)
#   3. Vector search (100K rows, IVF_PQ index)
#   4. Full-text search (10K rows)
#   5. Hybrid search
#   6. KG build overhead
#   7. RAG pipeline
# ---------------------------------------------------------------------------
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$PROJECT_ROOT"

# Parse arguments
MODE="run"
if [[ "${1:-}" == "--save" ]]; then
    MODE="save"
elif [[ "${1:-}" == "--ci" ]]; then
    MODE="ci"
fi

echo "=============================================="
echo "  Arrow Lake Critical Path Benchmarks"
echo "  Mode: $MODE"
echo "  Date: $(date -Iseconds)"
echo "=============================================="

# Use uv to run commands
UV="uv run"

# ---------------------------------------------------------------------------
# Mode: save baselines
# ---------------------------------------------------------------------------
if [[ "$MODE" == "save" ]]; then
    echo ""
    echo "[1/4] Saving baselines..."
    $UV python -m tests.benchmark.save_baselines --tmp-dir /tmp/arrow-lake-baselines
    echo ""
    echo "Baselines saved to tests/benchmark/baselines/"
    exit 0
fi

# ---------------------------------------------------------------------------
# Mode: CI regression check
# ---------------------------------------------------------------------------
if [[ "$MODE" == "ci" ]]; then
    echo ""
    echo "[CI] Running performance regression tests..."
    $UV pytest tests/benchmark/test_perf_regression.py -v -m perf_regression
    exit $?
fi

# ---------------------------------------------------------------------------
# Mode: run all benchmarks (default)
# ---------------------------------------------------------------------------
echo ""
echo "[1/7] Ingestion benchmarks..."
$UV pytest tests/benchmark/test_bench_ingest.py -v --no-header -q -m benchmark 2>&1 | tail -10

echo ""
echo "[2/7] Vector search benchmarks..."
$UV pytest tests/benchmark/test_bench_vector.py -v --no-header -q -m benchmark 2>&1 | tail -10

echo ""
echo "[3/7] Full-text search benchmarks..."
$UV pytest tests/benchmark/test_bench_fts.py -v --no-header -q -m benchmark 2>&1 | tail -10

echo ""
echo "[4/7] Hybrid search benchmarks..."
$UV pytest tests/benchmark/test_bench_hybrid.py -v --no-header -q -m benchmark 2>&1 | tail -10

echo ""
echo "[5/7] KG build benchmarks..."
$UV pytest tests/benchmark/test_bench_kg_build.py -v --no-header -q -m benchmark 2>&1 | tail -10

echo ""
echo "[6/7] RAG pipeline benchmarks..."
$UV pytest tests/benchmark/test_bench_rag_pipeline.py -v --no-header -q -m benchmark 2>&1 | tail -10

echo ""
echo "[7/7] Performance regression check..."
$UV pytest tests/benchmark/test_perf_regression.py -v --no-header -q -m perf_regression 2>&1 | tail -10

echo ""
echo "=============================================="
echo "  All benchmarks complete"
echo "=============================================="
