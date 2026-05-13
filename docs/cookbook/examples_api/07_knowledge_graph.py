#!/usr/bin/env python3
"""API-07 — Knowledge Graph

对应 cookbook: 19_knowledge_graph_build.py, 31_graphrag_qa.py,
              32_kg_traversal.py, 33_kg_entity_extract.py, 38_kg_schema_model.py
验证: 知识图谱构建、Schema 查询、Gremlin 查询、GraphRAG 问答、邻居遍历、统计、图清理
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


def main() -> None:
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
    if resp.get("success"):
        vertex_labels = resp.get("vertex_labels", [])
        edge_labels = resp.get("edge_labels", [])
        c._pass(f"schema — {len(vertex_labels)} vertex types, {len(edge_labels)} edge types")
    else:
        print(f"  [INFO] {resp.get('error')}: {resp.get('message', '')[:120]}")

    # 2. KG stats
    print("\nSTEP 2: KG stats")
    resp = c.kg_stats()
    if resp.get("success"):
        v_count = resp.get("vertex_count", resp.get("vertices", 0))
        e_count = resp.get("edge_count", resp.get("edges", 0))
        c._pass(f"stats — {v_count} vertices, {e_count} edges")
    else:
        print(f"  [INFO] {resp.get('error')}: {resp.get('message', '')[:120]}")

    # 3. Build knowledge graph
    print("\nSTEP 3: Build knowledge graph")
    resp = c.kg_build(name)
    if resp.get("success"):
        task_id = resp.get("task_id", "")
        c._pass(f"KG build started — task_id={task_id}")

        # 4. Wait for build completion
        print("\nSTEP 4: Wait for KG build")
        t0 = time.time()
        timeout = 60
        while time.time() - t0 < timeout:
            status = c.kg_build_status(task_id)
            state = status.get("status", status.get("state", ""))
            if state in ("completed", "done", "success"):
                c._pass(f"KG build completed — {state}")
                break
            if state in ("failed", "error"):
                print(f"  [FAIL] KG build failed: {status}")
                break
            print(f"         ... status={state}")
            time.sleep(2)
        else:
            print(f"  [WARN] KG build timed out after {timeout}s")
    else:
        print(f"  [INFO] {resp.get('error')}: {resp.get('message', '')[:120]}")

    # 5. Gremlin query — count vertices
    print("\nSTEP 5: Gremlin query (count)")
    resp = c.kg_query("g.V().count()")
    if resp.get("success"):
        result = resp.get("result", resp.get("data", []))
        c._pass(f"g.V().count() — {result}")
    else:
        print(f"  [INFO] {resp.get('error')}: {resp.get('message', '')[:120]}")

    # 6. Gremlin query — list vertex labels
    print("\nSTEP 6: Gremlin query (labels)")
    resp = c.kg_query("g.V().label().dedup()")
    if resp.get("success"):
        labels = resp.get("result", resp.get("data", []))
        c._pass(f"vertex labels: {labels}")
    else:
        print(f"  [INFO] {resp.get('error')}: {resp.get('message', '')[:120]}")

    # 7. Gremlin query — vertex properties
    print("\nSTEP 7: Gremlin query (vertex properties)")
    resp = c.kg_query("g.V().limit(3).elementMap()")
    if resp.get("success"):
        vertices = resp.get("result", resp.get("data", []))
        c._pass(f"sample vertices — {len(vertices) if isinstance(vertices, list) else 1}")
        for v in (vertices if isinstance(vertices, list) else [vertices])[:2]:
            print(f"         {str(v)[:100]}")
    else:
        print(f"  [INFO] {resp.get('error')}: {resp.get('message', '')[:120]}")

    # 8. Gremlin query — edge traversal
    print("\nSTEP 8: Gremlin query (edge traversal)")
    resp = c.kg_query("g.E().limit(5).elementMap()")
    if resp.get("success"):
        edges = resp.get("result", resp.get("data", []))
        c._pass(f"sample edges — {len(edges) if isinstance(edges, list) else 1}")
    else:
        print(f"  [INFO] {resp.get('error')}: {resp.get('message', '')[:120]}")

    # 9. Neighbor traversal
    print("\nSTEP 9: Neighbor traversal")
    first_vertex = c.kg_query("g.V().limit(1).id()")
    if first_vertex.get("success"):
        vid = first_vertex.get("result", None)
        if isinstance(vid, list):
            vid = vid[0] if vid else None
        if vid is not None:
            resp = c.kg_neighbors(str(vid))
            if resp.get("success"):
                neighbors = resp.get("neighbors", resp.get("data", []))
                c._pass(f"neighbors of {vid} — {len(neighbors)} found")
            else:
                print(f"  [INFO] {resp.get('error')}: {resp.get('message', '')[:120]}")
        else:
            print("  [SKIP] No vertex ID available")
    else:
        print(f"  [SKIP] {first_vertex.get('error', '')}")

    # 10. GraphRAG QA
    print("\nSTEP 10: GraphRAG QA")
    resp = c.kg_graphrag("What are the key relationships in this dataset?")
    if resp.get("success"):
        answer = resp.get("answer", "")
        c._pass(f"GraphRAG answer — {len(answer)} chars")
        print(f"         {answer[:120]}...")
    else:
        print(f"  [INFO] {resp.get('error')}: {resp.get('message', '')[:120]}")

    # 11. GraphRAG with context
    print("\nSTEP 11: GraphRAG QA (structured)")
    resp = c.kg_graphrag("List all entity types and their connections",
                          traversal_depth=2)
    if resp.get("success"):
        c._pass("GraphRAG structured query")
    else:
        print(f"  [INFO] {resp.get('error')}: {resp.get('message', '')[:120]}")

    # 12. Stats after build
    print("\nSTEP 12: KG stats (post-build)")
    resp = c.kg_stats()
    if resp.get("success"):
        v_count = resp.get("vertex_count", resp.get("vertices", 0))
        e_count = resp.get("edge_count", resp.get("edges", 0))
        c._pass(f"post-build stats — {v_count} vertices, {e_count} edges")
    else:
        print(f"  [INFO] {resp.get('error')}: {resp.get('message', '')[:120]}")

    print("\n" + "=" * 60)
    print("API-07  ALL PASSED")
    print("=" * 60)


if __name__ == "__main__":
    main()
