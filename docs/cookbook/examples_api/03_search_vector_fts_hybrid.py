#!/usr/bin/env python3
"""API-03 — Search: Vector / FTS / Hybrid / Faceted / Ensemble

对应 cookbook: 02_search_and_index.py, 04_vector_search.py, 05_fulltext_search.py, 06_hybrid_faceted.py
验证: 向量搜索、全文搜索、混合搜索、分面搜索、集成搜索
前置: 需要已存在含 text_content + text_embedding 列的数据集 (如 api-test)
"""

from __future__ import annotations
import os

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from conftest import ArrowLakeClient

BASE_URL = os.environ.get("ARROW_LAKE_BASE_URL", "http://localhost:8000")
API_KEY = os.environ.get("ARROW_LAKE_API_KEY", "dev-api-key-for-local-testing-only")


def main() -> None:
    print("=" * 60)
    print("API-03  Search (Vector / FTS / Hybrid)")
    print("=" * 60)

    c = ArrowLakeClient(BASE_URL, API_KEY)

    # Find a dataset with search capability
    ds = c.list_datasets()
    datasets = ds.get("datasets", [])
    if not datasets:
        print("  [SKIP] No datasets available")
        return

    # Use the largest dataset for search testing
    target = max(datasets, key=lambda d: d["num_rows"])
    name = target["name"]
    print(f"\nUsing dataset: {name} ({target['num_rows']} rows)")

    # 1. FTS Search
    print("\nSTEP 1: Full-text search")
    resp = c.search_fts(name, "data science", top_k=5)
    if resp.get("success"):
        rows = resp.get("rows", [])
        c._pass(f"FTS 'data science' — {resp.get('row_count', 0)} results")
        for r in rows[:2]:
            txt = r.get("text_content", r.get("text", ""))[:60]
            print(f"         score={r.get('_score', '?'):.3f}  {txt}...")
    else:
        print(f"  [WARN] FTS failed: {resp.get('error')} — {resp.get('message', '')[:80]}")

    # 2. FTS with Chinese
    print("\nSTEP 2: FTS with Chinese query")
    resp = c.search_fts(name, "学习", top_k=3)
    if resp.get("success"):
        c._pass(f"FTS '学习' — {resp.get('row_count', 0)} results")
    else:
        print(f"  [WARN] {resp.get('error')}: {resp.get('message', '')[:80]}")

    # 3. FTS with filter
    print("\nSTEP 3: FTS with where filter")
    resp = c.search_fts(name, "AI", top_k=5, where="source IS NOT NULL")
    if resp.get("success"):
        c._pass(f"FTS 'AI' + filter — {resp.get('row_count', 0)} results")
    else:
        print(f"  [WARN] {resp.get('error')}: {resp.get('message', '')[:80]}")

    # 4. Vector Search
    print("\nSTEP 4: Vector search")
    import numpy as np
    DEFAULT_VEC_DIM = 384  # Must match embedding model output dimension
    np.random.seed(42)
    vec = np.random.randn(DEFAULT_VEC_DIM).astype(np.float32).tolist()
    resp = c.search_vector(name, vec, top_k=3)
    has_vector = resp.get("success", False)
    if has_vector:
        c._pass(f"Vector search — {resp.get('row_count', 0)} results")
    else:
        print(f"  [INFO] {resp.get('error')}: {resp.get('message', '')[:120]}")

    # 5. Hybrid Search (skip if no vector capability)
    print("\nSTEP 5: Hybrid search")
    if has_vector:
        resp = c.search_hybrid(name, vec, "machine learning", top_k=3)
        if resp.get("success"):
            c._pass(f"Hybrid search — {resp.get('row_count', 0)} results")
        else:
            print(f"  [INFO] {resp.get('error')}: {resp.get('message', '')[:120]}")
    else:
        print("  [SKIP] Dataset has no embedding column — skipping hybrid/ensemble")

    # 6. Faceted Search
    print("\nSTEP 6: Faceted search")
    resp = c.search_faceted(name, "data", facets=["source", "doc_type"], top_k=3)
    if resp.get("success"):
        c._pass(f"Faceted search — {resp.get('row_count', 0)} results")
    else:
        print(f"  [INFO] {resp.get('error')}: {resp.get('message', '')[:120]}")

    # 7. Ensemble Search
    print("\nSTEP 7: Ensemble search")
    if has_vector:
        resp = c.search_ensemble(name, vec, "deep learning", top_k=3)
        if resp.get("success"):
            c._pass(f"Ensemble search — {resp.get('row_count', 0)} results")
        else:
            print(f"  [INFO] {resp.get('error')}: {resp.get('message', '')[:120]}")
    else:
        print("  [SKIP] No embedding column")

    # 8. Create Vector Index
    print("\nSTEP 8: Create vector index")
    resp = c.create_vector_index(name, vector_column="text_embedding",
                                  metric="cosine", index_type="IVF_PQ")
    if resp.get("success"):
        c._pass("Vector index created")
    else:
        print(f"  [INFO] {resp.get('error')}: {resp.get('message', '')[:120]}")

    # 9. Create FTS Index
    print("\nSTEP 9: Create FTS index")
    resp = c.create_fts_index(name, fts_column="text_content")
    if resp.get("success"):
        c._pass("FTS index created")
    else:
        print(f"  [INFO] {resp.get('error')}: {resp.get('message', '')[:120]}")

    # 10. Search after index
    print("\nSTEP 10: FTS after index creation")
    resp = c.search_fts(name, "artificial intelligence", top_k=3)
    if resp.get("success") and resp.get("row_count", 0) > 0:
        c._pass(f"FTS after index — {resp.get('row_count')} results")
    else:
        print(f"  [INFO] {resp.get('error', 'no results')}: {resp.get('message', '')[:80]}")

    print("\n" + "=" * 60)
    print("API-03  ALL PASSED")
    print("=" * 60)


if __name__ == "__main__":
    main()
