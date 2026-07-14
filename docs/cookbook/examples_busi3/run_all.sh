#!/usr/bin/env bash
# examples_busi3 — 端到端跑通: ingest → build KG → 重测 search/chat → RAG vs 图对比。
# 用法: bash docs/cookbook/examples_busi3/run_all.sh [--rebuild]
#   --rebuild  强制重建 jd_ddd dataset (否则复用已存在的)
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$HERE/env.sh"

PY="${PY:-.venv/bin/python3}"
INGEST_FLAG=""
[[ "${1:-}" == "--rebuild" ]] && INGEST_FLAG="--force"

echo "============================================================"
echo "01 — ingest jd_ddd (PDF → lake dataset)"
echo "============================================================"
$PY "$HERE/01_ingest_jd.py" $INGEST_FLAG

echo ""
echo "============================================================"
echo "02 — build KG (hyper-extract → HugeGraph + KA dump)"
echo "============================================================"
$PY "$HERE/02_build_kg.py"

echo ""
echo "============================================================"
echo "03 — 重测 search_ka/chat_ka (任务#1)"
echo "============================================================"
$PY "$HERE/03_test_search_chat.py" || echo "(03 有失败项, 继续 04)"

echo ""
echo "============================================================"
echo "04 — RAG vs 图查询对比 (任务#3)"
echo "============================================================"
$PY "$HERE/04_compare_rag_vs_graph.py"

echo ""
echo "完成. 结果: $HERE/data/results/"
