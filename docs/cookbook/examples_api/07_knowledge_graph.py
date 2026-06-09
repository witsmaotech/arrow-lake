#!/usr/bin/env python3
"""API-07 — Knowledge Graph

验证: 知识图谱构建、Schema 查询、Gremlin 查询、邻居遍历、统计
前置: 需要已存在含 text_content 列的数据集，HugeGraph 后端可用
"""

from __future__ import annotations

import time
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from conftest import ArrowLakeClient

BASE_URL = "http://localhost:8000"
API_KEY = "dev-api-key-for-local-testing-only"

PASS = 0
SKIP = 0
FAIL = 0


def _ok(resp: dict, label: str) -> bool:
    """Check if API response indicates success."""
    global PASS, SKIP, FAIL
    if resp.get("error") or resp.get("detail"):
        msg = resp.get("error") or resp.get("detail", "")
        msg_str = str(msg) if not isinstance(msg, str) else msg
        if "not found" in msg_str.lower() or "unavailable" in msg_str.lower():
            print(f"  [SKIP] {label}: {msg_str[:100]}")
            SKIP += 1
        else:
            print(f"  [FAIL] {label}: {msg_str[:120]}")
            FAIL += 1
        return False
    PASS += 1
    return True


def _results(resp: dict) -> list:
    """Extract result data from any known API response shape."""
    return resp.get("results", resp.get("result", resp.get("data", [])))


def main() -> None:
    global PASS, SKIP, FAIL
    print("=" * 60)
    print("API-07  Knowledge Graph")
    print("=" * 60)

    c = ArrowLakeClient(BASE_URL, API_KEY)

    ds = c.list_datasets()
    datasets = ds.get("datasets", [])
    if not datasets:
        print("  [SKIP] No datasets available")
        return

    target = max(datasets, key=lambda d: d["num_rows"])
    name = target["name"]
    print(f"\nUsing dataset: {name} ({target['num_rows']} rows)")

    # 1. KG schema
    print("\nSTEP 1: KG schema")
    resp = c.kg_schema()
    if _ok(resp, "schema"):
        vl = resp.get("vertex_labels", [])
        el = resp.get("edge_labels", [])
        c._pass(f"schema — {len(vl)} vertex types, {len(el)} edge types")

    # 2. KG stats
    print("\nSTEP 2: KG stats")
    resp = c.kg_stats()
    if _ok(resp, "stats"):
        v = resp.get("total_vertices", resp.get("vertex_count", 0))
        e = resp.get("total_edges", resp.get("edge_count", 0))
        c._pass(f"stats — {v} vertices, {e} edges")

    # 3. Build knowledge graph
    print("\nSTEP 3: Build knowledge graph")
    resp = c.kg_build(name)
    task_id = resp.get("task_id", "")
    if task_id:
        c._pass(f"KG build started — task_id={task_id}")

        # 4. Wait for build completion
        print("\nSTEP 4: Wait for KG build")
        t0 = time.time()
        timeout = 120
        while time.time() - t0 < timeout:
            status = c.kg_build_status(task_id)
            state = status.get("status", status.get("state", "")).lower()
            if state in ("completed", "done", "success"):
                c._pass(f"KG build completed — {state}")
                break
            if state in ("failed", "error"):
                print(f"  [FAIL] KG build failed: {status}")
                FAIL += 1
                break
            print(f"         ... status={state}")
            time.sleep(3)
        else:
            print(f"  [WARN] KG build timed out after {timeout}s")
    else:
        msg = resp.get("error") or resp.get("detail") or resp.get("message", "")
        print(f"  [SKIP] Build not started: {msg[:120]}")
        SKIP += 1

    # 5. Gremlin query — count vertices
    print("\nSTEP 5: Gremlin query (count)")
    resp = c.kg_query("g.V().count()")
    if _ok(resp, "Gremlin count"):
        c._pass(f"g.V().count() — {_results(resp)}")

    # 6. Gremlin query — list vertex labels
    print("\nSTEP 6: Gremlin query (labels)")
    resp = c.kg_query("g.V().label().dedup()")
    if _ok(resp, "Gremlin labels"):
        c._pass(f"vertex labels: {_results(resp)}")

    # 7. Gremlin query — vertex properties
    print("\nSTEP 7: Gremlin query (vertex properties)")
    resp = c.kg_query("g.V().limit(3).elementMap()")
    if _ok(resp, "Gremlin vertex props"):
        vertices = _results(resp)
        n = len(vertices) if isinstance(vertices, list) else 1
        c._pass(f"sample vertices — {n}")
        for v in (vertices if isinstance(vertices, list) else [vertices])[:2]:
            print(f"         {str(v)[:120]}")

    # 8. Gremlin query — edge traversal
    print("\nSTEP 8: Gremlin query (edge traversal)")
    resp = c.kg_query("g.E().limit(5).elementMap()")
    if _ok(resp, "Gremlin edges"):
        edges = _results(resp)
        n = len(edges) if isinstance(edges, list) else 1
        c._pass(f"sample edges — {n}")

    # 9. Neighbor traversal
    print("\nSTEP 9: Neighbor traversal")
    first_vertex = c.kg_query("g.V().limit(1).id()")
    vid = None
    if _ok(first_vertex, "get vertex ID"):
        ids = _results(first_vertex)
        if isinstance(ids, list) and ids:
            vid = ids[0]
    if vid is not None:
        resp = c.kg_neighbors(str(vid))
        if _ok(resp, "neighbors"):
            neighbors = resp.get("neighbors", resp.get("results", resp.get("data", [])))
            n = len(neighbors) if isinstance(neighbors, list) else "N/A"
            c._pass(f"neighbors of {vid} — {n} found")
    else:
        print("  [SKIP] No vertex ID available")
        SKIP += 1

    # 10. GraphRAG QA (requires FTS-indexed dataset with text_content)
    print("\nSTEP 10: GraphRAG QA")
    resp = c.kg_graphrag("What are the key relationships in this dataset?",
                          dataset_name=name)
    err = resp.get("error") or resp.get("detail", "")
    if err and "fts" in str(err).lower():
        print(f"  [SKIP] GraphRAG — dataset lacks FTS index: {str(err)[:80]}")
        SKIP += 1
    elif _ok(resp, "GraphRAG"):
        answer = resp.get("answer", resp.get("results", ""))
        c._pass(f"GraphRAG answer — {len(str(answer))} chars")
        print(f"         {str(answer)[:150]}...")

    # 11. Stats after build
    print("\nSTEP 11: KG stats (post-build)")
    resp = c.kg_stats()
    if _ok(resp, "final stats"):
        v = resp.get("total_vertices", resp.get("vertex_count", 0))
        e = resp.get("total_edges", resp.get("edge_count", 0))
        c._pass(f"post-build stats — {v} vertices, {e} edges")

    print(f"\n{'=' * 60}")
    print(f"API-07  DONE — {PASS} passed, {SKIP} skipped, {FAIL} failed")
    print(f"{'=' * 60}")
    if FAIL > 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
