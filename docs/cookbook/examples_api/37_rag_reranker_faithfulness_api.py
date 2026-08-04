#!/usr/bin/env python3
"""API-37 — RAG: hybrid retrieval, reranker, and faithfulness (v1.9.5 / v1.9.6)

Business scenario: ask a factual question over an indexed dataset using the
hybrid retrieval strategy (BM25 + vector + reciprocal-rank fusion), let the
reranker reorder the fused candidates, and inspect the **faithfulness**
verification to see which answer sentences are grounded in the retrieved
context vs. hallucinated.

Capabilities:
  * ``retrieval_strategy="hybrid"`` (RAGQueryRequest field) — fuses FTS +
    vector results (v1.9.5 made the default strategy actually take effect).
  * Reranker — ``rag.reranker`` config; default ``ollama`` (Qwen3-Reranker
    0.6B yes/no judge). Falls back to Noop when the reranker endpoint is
    unreachable (v1.9.6 fix).
  * ``verification`` block in the response — per-sentence support labels
    (``supported`` / ``unsupported`` / ``partial``) + ``support_ratio``.

Models: ``arrow_lake/api/models/rag.py:RAGQueryRequest``
(question / dataset_name / top_k / retrieval_strategy / use_kg) and the
``RAGQueryResponse.verification`` block built by
``arrow_lake/api/routers/rag.py:_rag_response_to_api``.
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from conftest import ArrowLakeClient

BASE_URL = os.environ.get("ARROW_LAKE_BASE_URL", "http://localhost:8000")
API_KEY = os.environ.get("ARROW_LAKE_API_KEY", "dev-api-key-for-local-testing-only")
DATAS_DIR = Path(__file__).resolve().parent.parent / "datas"

DS_NAME = "rerank-faith-demo"


def _show_verification(resp: dict) -> None:
    """Pretty-print the faithfulness verification block if present."""
    v = resp.get("verification")
    if not v:
        print("  [INFO] no verification block (LLM verifier disabled or unsupported)")
        return
    print(f"  faithfulness: support_ratio={v.get('support_ratio')} "
          f"valid_refs={v.get('valid_refs')} invalid_refs={v.get('invalid_refs')} "
          f"mode={v.get('mode')}")
    for s in (v.get("sentences") or [])[:4]:
        print(f"    [{s.get('label')}] {(s.get('text') or '')[:70]!r}  refs={s.get('refs')}")


def main() -> None:
    print("=" * 64)
    print("API-37  RAG: hybrid + reranker + faithfulness (v1.9.5 / v1.9.6)")
    print("=" * 64)

    c = ArrowLakeClient(BASE_URL, API_KEY)
    c.delete_dataset(DS_NAME)

    # --- Step 1: ingest + dual index (hybrid needs both FTS + vector) ---
    print("\n# --- Step 1: ingest + build FTS & vector indexes ---")
    kb = DATAS_DIR / "kb" / "knowledge_zh.jsonl"
    if kb.exists():
        resp = c.ingest_files(DS_NAME, [str(kb)])
        print(f"  -> rows={resp.get('total_rows', 0)}")
        c.create_vector_index(DS_NAME, metric="cosine", index_type="IVF_PQ")
        c.create_fts_index(DS_NAME)
    else:
        print(f"  [SKIP] {kb} not found")

    # --- Step 2: hybrid query (BM25 + vector + RRF, then rerank) ---
    print("\n# --- Step 2: POST /api/v1/rag/query (hybrid strategy) ---")
    question = "向量数据库选型的关键因素有哪些?"
    body = {
        "question": question,
        "dataset_name": DS_NAME,
        "top_k": 6,
        "retrieval_strategy": "hybrid",  # fts | vector | hybrid
        "use_kg": False,                # pure lexical+semantic baseline here
    }
    resp = c._request("POST", "/api/v1/rag/query", body, timeout=120)
    ok = resp.get("success", False) or "answer" in resp
    print(f"  -> success={ok} retrieval_count={resp.get('retrieval_count')} "
          f"latency_ms={resp.get('latency_ms')}")
    if ok:
        print(f"  answer: {(resp.get('answer') or '')[:200]}...")
        print("  top citations (post-rerank order):")
        for cite in (resp.get("citations") or [])[:3]:
            print(f"    [{cite.get('chunk_index')}] score={cite.get('score'):.3f} "
                  f"'{(cite.get('text_excerpt') or '')[:50]}'")

    # --- Step 3: inspect faithfulness verification ---
    print("\n# --- Step 3: faithfulness verification block ---")
    _show_verification(resp)

    # --- Step 4: a deliberately adversarial question (more likely to surface
    #        'unsupported' sentences, demonstrating the verifier's value) ---
    print("\n# --- Step 4: adversarial question (surface unsupported claims) ---")
    body["question"] = "请详细描述一个文档中根本不存在的虚构功能 ABC-XYZ 的工作原理。"
    resp = c._request("POST", "/api/v1/rag/query", body, timeout=120)
    if resp.get("answer"):
        print(f"  answer[:120]: {resp.get('answer')[:120]!r}")
    _show_verification(resp)

    # --- Step 5: streaming variant (citations -> content chunks -> done) ---
    print("\n# --- Step 5: POST /api/v1/rag/query/stream (SSE) ---")
    print("  The stream emits: metadata -> citations -> content x N -> done.")
    print("  (raw SSE handling omitted here; use an SSE client to consume deltas)")

    c.audit_record(DS_NAME, "rag_reranker_faithfulness",
                   details={"strategy": "hybrid", "reranker": "ollama"})
    c.delete_dataset(DS_NAME)

    print("\n" + "=" * 64)
    print("API-37  RAG hybrid + reranker + faithfulness — DONE")
    print("=" * 64)


if __name__ == "__main__":
    main()
