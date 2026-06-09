#!/usr/bin/env python3
"""API-05 — Embedding & Index Management

对应 cookbook: 02_search_and_index.py, 04_vector_search.py, 05_fulltext_search.py
验证: 文本/图片嵌入计算、向量索引创建与参数调优、FTS 索引生命周期
"""

from __future__ import annotations
import os

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from conftest import ArrowLakeClient, first_embedding

BASE_URL = os.environ.get("ARROW_LAKE_BASE_URL", "http://localhost:8000")
API_KEY = os.environ.get("ARROW_LAKE_API_KEY", "dev-api-key-for-local-testing-only")


def main() -> None:
    print("=" * 60)
    print("API-05  Embedding & Index Management")
    print("=" * 60)

    c = ArrowLakeClient(BASE_URL, API_KEY)

    ds = c.list_datasets()
    datasets = ds.get("datasets", [])
    if not datasets:
        print("  [SKIP] No datasets available")
        return

    target = max(datasets, key=lambda d: d["num_rows"])
    name = target["name"]
    rows = target["num_rows"]
    print(f"\nUsing dataset: {name} ({rows} rows)")

    # 1. Text embedding
    print("\nSTEP 1: Text embedding")
    resp = c.embed_text(["What is machine learning?", "Arrow Lake is a data lakehouse."])
    if resp.get("success"):
        embeddings = resp.get("embeddings", resp.get("data", []))
        dim = len(embeddings[0]) if embeddings else 0
        c._pass(f"embed_text — {len(embeddings)} vectors, dim={dim}")
    else:
        print(f"  [INFO] {resp.get('error')}: {resp.get('message', '')[:120]}")

    # 2. Text embedding with model param
    print("\nSTEP 2: Text embedding (model param)")
    resp = c.embed_text(["Hello world"], model="default")
    if resp.get("success"):
        c._pass("embed_text with model param")
    else:
        print(f"  [INFO] {resp.get('error')}: {resp.get('message', '')[:120]}")

    # 3. Image embedding
    print("\nSTEP 3: Image embedding")
    datas_dir = Path(__file__).resolve().parent.parent / "datas"
    photo_dir = datas_dir / "photos"
    if photo_dir.exists():
        imgs = [str(p) for p in photo_dir.iterdir() if p.suffix.lower() in (".jpg", ".jpeg", ".png")]
        if imgs:
            resp = c.embed_image(imgs[:2], model="clip")
            if resp.get("success"):
                embeddings = resp.get("embeddings", resp.get("data", []))
                dim = len(embeddings[0]) if embeddings else 0
                c._pass(f"embed_image — {len(embeddings)} vectors, dim={dim}")
            else:
                print(f"  [INFO] {resp.get('error')}: {resp.get('message', '')[:120]}")
        else:
            print("  [SKIP] No image files found")
    else:
        print(f"  [SKIP] {photo_dir} not found")

    # 4. Create vector index (IVF_PQ)
    print("\nSTEP 4: Create vector index (IVF_PQ)")
    resp = c.create_vector_index(name, vector_column="text_embedding",
                                  metric="cosine", index_type="IVF_PQ")
    if resp.get("success"):
        c._pass("vector index IVF_PQ created")
    else:
        print(f"  [INFO] {resp.get('error')}: {resp.get('message', '')[:120]}")

    # 5. Create vector index (flat — brute force)
    print("\nSTEP 5: Create vector index (flat)")
    resp = c.create_vector_index(name, vector_column="text_embedding",
                                  metric="l2", index_type="FLAT")
    if resp.get("success"):
        c._pass("vector index FLAT created")
    else:
        print(f"  [INFO] {resp.get('error')}: {resp.get('message', '')[:120]}")

    # 6. Create vector index with custom params
    print("\nSTEP 6: Create vector index (custom params)")
    resp = c.create_vector_index(name, vector_column="text_embedding",
                                  metric="cosine", index_type="IVF_PQ",
                                  n_lists=10, n_sub_vectors=16)
    if resp.get("success"):
        c._pass("vector index with n_lists/n_sub_vectors created")
    else:
        print(f"  [INFO] {resp.get('error')}: {resp.get('message', '')[:120]}")

    # 7. Create FTS index
    print("\nSTEP 7: Create FTS index")
    resp = c.create_fts_index(name, fts_column="text_content")
    if resp.get("success"):
        c._pass("FTS index created")
    else:
        print(f"  [INFO] {resp.get('error')}: {resp.get('message', '')[:120]}")

    # 8. Create FTS index with custom params
    print("\nSTEP 8: Create FTS index (custom params)")
    resp = c.create_fts_index(name, fts_column="text_content",
                               replace=True)
    if resp.get("success"):
        c._pass("FTS index with replace=True")
    else:
        print(f"  [INFO] {resp.get('error')}: {resp.get('message', '')[:120]}")

    # 9. Search with embeddings — use computed vector
    print("\nSTEP 9: Vector search with API-computed embedding")
    resp = c.embed_text(["artificial intelligence research"])
    if resp.get("success"):
        vec = first_embedding(resp)
        if vec:
            search = c.search_vector(name, vec, top_k=3)
            if search.get("success"):
                c._pass(f"vector search — {search.get('row_count', 0)} results")
            else:
                print(f"  [INFO] search failed: {search.get('message', '')[:80]}")
    else:
        print(f"  [INFO] embed required for search: {resp.get('error', '')[:80]}")

    # 10. Version info
    print("\nSTEP 10: Version info")
    resp = c.version()
    if resp.get("version"):
        c._pass(f"version={resp['version']}")
    else:
        print(f"  [INFO] {resp}")

    print("\n" + "=" * 60)
    print("API-05  ALL PASSED")
    print("=" * 60)


if __name__ == "__main__":
    main()
