#!/usr/bin/env python3
"""API-36 — GraphRAG: relation-aware QA with enriched triples (v1.9.11)

Business scenario: after building a knowledge graph, ask relation-rich
questions and let GraphRAG inject ``relation_type``-enriched triples into the
LLM context. Contrast with ``use_kg: false`` (pure vector/FTS RAG) to see when
graph structure helps.

v1.9.11 improvements:
  * The KG neighbor context now reads ``properties.relation_type`` (not just
    the edge label), so triples carry real semantics (``exploits`` /
    ``mitigates`` instead of a generic ``related_to``).
  * ``retriever.py`` predicate enrichment: ``client.get_vertex_edges`` fetches
    edge ``relation_type`` for the kneighbor traversal too.
  * ``use_kg`` is per-query on ``/api/v1/rag/query`` — no need to toggle
    hugegraph.enabled globally.

Models: ``arrow_lake/api/models/rag.py:RAGQueryRequest`` (use_kg field) and
``arrow_lake/api/models/knowledge_graph.py:KGBuildRequest`` (template field).
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

DS_NAME = "graphrag-rel-demo"


def main() -> None:
    print("=" * 64)
    print("API-36  GraphRAG: relation-aware QA (v1.9.11)")
    print("=" * 64)

    c = ArrowLakeClient(BASE_URL, API_KEY)
    c.delete_dataset(DS_NAME)

    # --- Step 1: ingest a relation-rich knowledge base ---
    print("\n# --- Step 1: ingest knowledge base ---")
    kb = DATAS_DIR / "kb" / "knowledge_zh.jsonl"
    if kb.exists():
        resp = c.ingest_files(DS_NAME, [str(kb)])
        print(f"  -> rows={resp.get('total_rows', 0)}")
        c.create_vector_index(DS_NAME, metric="cosine", index_type="IVF_PQ")
        c.create_fts_index(DS_NAME)
    else:
        print(f"  [SKIP] {kb} not found — point DATAS_DIR at a knowledge base")

    # --- Step 2: build the KG (explicit template recommended) ---
    print("\n# --- Step 2: POST /api/v1/kg/build ---")
    # template is optional; explicit name wins, else dataset binding, else doc_type routing
    resp = c._request("POST", "/api/v1/kg/build", {"dataset": DS_NAME})
    task_id = resp.get("task_id")
    print(f"  -> task_id={task_id}")
    if not task_id:
        print(f"  [INFO] KG unavailable: {resp.get('detail') or resp.get('error')}")
        print("        rest of this demo needs a built KG; exiting.")
        c.delete_dataset(DS_NAME)
        return

    # --- Step 3: poll build status ---
    print("\n# --- Step 3: poll /kg/build/{task_id}/status ---")
    t0, timeout = time.time(), 120
    while time.time() - t0 < timeout:
        st = c._request("GET", f"/api/v1/kg/build/{task_id}/status")
        state = st.get("status")
        print(f"     ... {state} chunks={st.get('processed_chunks')}/{st.get('total_chunks')} "
              f"V={st.get('entity_count')} E={st.get('relation_count')}")
        if state in ("completed", "done", "success"):
            break
        if state in ("failed", "error"):
            print(f"  [FAIL] {st.get('error')}")
            c.delete_dataset(DS_NAME)
            return
        time.sleep(3)

    # --- Step 4: relation-rich question with GraphRAG (use_kg: true) ---
    print("\n# --- Step 4: RAG query use_kg=true (GraphRAG, enriched triples) ---")
    question = "数据工程中各组件之间有什么依赖与协作关系?"
    body = {"question": question, "dataset_name": DS_NAME, "top_k": 5, "use_kg": True}
    kg_resp = c._request("POST", "/api/v1/rag/query", body, timeout=120)
    kg_ok = kg_resp.get("success", False) or "answer" in kg_resp
    print(f"  -> success={kg_ok} citations={kg_resp.get('retrieval_count')}")
    if kg_ok:
        print(f"  answer: {(kg_resp.get('answer') or '')[:160]}...")
        # GraphRAG injects KG triples as context; citations list the source chunks
        for cite in (kg_resp.get("citations") or [])[:3]:
            print(f"    cite[{cite.get('chunk_index')}] score={cite.get('score'):.3f} "
                  f"'{(cite.get('text_excerpt') or '')[:50]}'")

    # --- Step 5: same question without KG (use_kg: false = pure vector/FTS) ---
    print("\n# --- Step 5: RAG query use_kg=false (pure vector/FTS baseline) ---")
    body["use_kg"] = False
    vec_resp = c._request("POST", "/api/v1/rag/query", body, timeout=120)
    vec_ok = vec_resp.get("success", False) or "answer" in vec_resp
    print(f"  -> success={vec_ok} citations={vec_resp.get('retrieval_count')}")
    if vec_ok:
        print(f"  answer: {(vec_resp.get('answer') or '')[:160]}...")

    # --- Step 6: contrast ---
    print("\n# --- Step 6: contrast ---")
    if kg_ok and vec_ok:
        print("  GraphRAG tends to surface structural/relational answers")
        print("  (entity-relation triples injected) while vector RAG answers")
        print("  from lexical/semantic proximity. Pick use_kg per query.")

    # --- Step 7: dedicated GraphRAG endpoint (traversal_depth) ---
    print("\n# --- Step 7: POST /api/v1/kg/query/graphrag (dedicated path) ---")
    resp = c._request("POST", "/api/v1/kg/query/graphrag",
                      {"question": question, "dataset": DS_NAME,
                       "top_k": 5, "traversal_depth": 2, "graph_weight": 0.3},
                      timeout=120)
    print(f"  -> success={resp.get('success', False) or 'answer' in resp} "
          f"answer[:80]={(resp.get('answer') or '')[:80]!r}")

    # cleanup
    c.audit_record(DS_NAME, "graphrag_relation_qa", details={"use_kg": True})
    c.delete_dataset(DS_NAME)

    print("\n" + "=" * 64)
    print("API-36  GraphRAG relation-aware QA — DONE")
    print("=" * 64)


if __name__ == "__main__":
    main()
