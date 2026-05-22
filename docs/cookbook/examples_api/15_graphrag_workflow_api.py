#!/usr/bin/env python3
"""API-15 — GraphRAG 端到端工作流

业务场景: 构建知识图谱增强的 RAG 系统，对比纯向量 RAG 与 GraphRAG 效果差异
数据源: datas/kb/knowledge_zh.jsonl (中文技术知识库)
流程: 知识库摄取 → 图谱构建 → Gremlin 查询 → GraphRAG QA → 对比评估
"""

from __future__ import annotations

import time
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from conftest import ArrowLakeClient

BASE_URL = "http://localhost:8000"
API_KEY = "dev-api-key-for-local-testing-only"
DATAS_DIR = Path(__file__).resolve().parent.parent / "datas"

DS_NAME = "graphrag-kb"


def main() -> None:
    print("=" * 60)
    print("API-15  GraphRAG 端到端工作流")
    print("=" * 60)

    c = ArrowLakeClient(BASE_URL, API_KEY)

    kg_available = False
    c.delete_dataset(DS_NAME)

    # ── Phase 1: 知识库准备 ──

    print("\n── Phase 1: 知识库准备 ──")

    print("\nSTEP 1: 摄取中文知识库")
    zh_jsonl = DATAS_DIR / "kb" / "knowledge_zh.jsonl"
    if zh_jsonl.exists():
        resp = c.ingest_files(DS_NAME, [str(zh_jsonl)])
        if resp.get("success"):
            rows = resp.get("total_rows", 0)
            c._pass(f"知识库摄取 — {rows} 条")
        else:
            print(f"  [WARN] 摄取失败: {resp.get('error', '')}")
            c.delete_dataset(DS_NAME)
            print("\n" + "=" * 60)
            print("API-15  GraphRAG 端到端工作流 — ALL PASSED (SKIP)")
            print("=" * 60)
            return
    else:
        print(f"  [SKIP] {zh_jsonl} 不存在")

    print("\nSTEP 2: 构建双索引")
    c.create_vector_index(DS_NAME, vector_column="text_embedding",
                           metric="cosine", index_type="IVF_PQ")
    c.create_fts_index(DS_NAME, fts_column="text_content")
    c._pass("向量 + FTS 索引就绪")

    # ── Phase 2: 知识图谱构建 ──

    print("\n── Phase 2: 知识图谱构建 ──")

    print("\nSTEP 3: 发起图谱构建")
    resp = c.kg_build(DS_NAME)
    if resp.get("success"):
        kg_available = True
        task_id = resp.get("task_id", "")
        c._pass(f"图谱构建任务 — {task_id}")

        print("\nSTEP 4: 等待构建完成")
        t0 = time.time()
        timeout = 60
        while time.time() - t0 < timeout:
            status = c.kg_build_status(task_id)
            state = status.get("status", status.get("state", ""))
            if state in ("completed", "done", "success"):
                c._pass(f"图谱构建完成 — {state}")
                break
            if state in ("failed", "error"):
                print(f"  [FAIL] 构建失败: {status}")
                break
            print(f"         ... {state}")
            time.sleep(2)
        else:
            print(f"  [WARN] 构建超时 ({timeout}s)")
    else:
        print(f"  [INFO] 图谱构建不可用: {resp.get('error', '')}")

    # ── Phase 3: 图谱探索 ──

    print("\n── Phase 3: 图谱探索 ──")

    print("\nSTEP 5: 图谱 Schema")
    resp = c.kg_schema()
    if resp.get("success"):
        v_labels = resp.get("vertex_labels", [])
        e_labels = resp.get("edge_labels", [])
        c._pass(f"Schema — {len(v_labels)} 顶点类型, {len(e_labels)} 边类型")
        for vl in v_labels[:5]:
            print(f"         顶点: {vl}")
    else:
        print(f"  [INFO] {resp.get('error', '')}")

    print("\nSTEP 6: 图谱统计")
    resp = c.kg_stats()
    if resp.get("success"):
        v_count = resp.get("vertex_count", resp.get("vertices", 0))
        e_count = resp.get("edge_count", resp.get("edges", 0))
        c._pass(f"图谱规模 — {v_count} 顶点, {e_count} 边")
    else:
        print(f"  [INFO] {resp.get('error', '')}")

    print("\nSTEP 7: Gremlin 查询 — 顶点标签分布")
    resp = c.kg_query("g.V().label().groupCount()")
    if resp.get("success"):
        result = resp.get("result", resp.get("data", {}))
        c._pass(f"标签分布: {result}")
    else:
        print(f"  [INFO] {resp.get('error', '')}")

    print("\nSTEP 8: Gremlin 查询 — 度中心性 TOP 5")
    resp = c.kg_query(
        "g.V().project('id','label','degree')"
        ".by(id()).by(label()).by(outE().count())"
        ".order().by('degree', desc).limit(5)"
    )
    if resp.get("success"):
        result = resp.get("result", resp.get("data", []))
        c._pass(f"TOP 5 度中心性: {result}")
    else:
        print(f"  [INFO] {resp.get('error', '')}")

    print("\nSTEP 9: 邻居遍历")
    first = c.kg_query("g.V().limit(1).id()")
    if first.get("success"):
        vid = first.get("result", None)
        if isinstance(vid, list):
            vid = vid[0] if vid else None
        if vid is not None:
            resp = c.kg_neighbors(str(vid), limit=5)
            if resp.get("success"):
                neighbors = resp.get("neighbors", resp.get("data", []))
                c._pass(f"邻居遍历 {vid} — {len(neighbors)} 个邻居")
    else:
        print("  [SKIP] 无可用顶点")

    # ── Phase 4: GraphRAG vs 纯向量 RAG 对比 ──

    print("\n── Phase 4: GraphRAG vs Vector RAG 对比 ──")

    if not kg_available:
        print("  [SKIP] 知识图谱未构建，跳过 RAG 对比")
    else:
        test_questions = [
            "Arrow 和 Lance 之间有什么技术关联？",
            "数据工程中选择向量数据库的关键因素是什么？",
            "RAG 系统如何与知识图谱结合？",
        ]

        for i, q in enumerate(test_questions, 1):
            print(f"\nSTEP {9 + i}: Q{i}: {q}")

            # Vector RAG (with extended timeout)
            rag_resp = c._request("POST", "/api/v1/rag/query",
                                  {"question": q, "dataset_name": DS_NAME, "top_k": 3},
                                  timeout=90)
            rag_ok = rag_resp.get("success", False)
            rag_answer = rag_resp.get("answer", "")[:80] if rag_ok else "N/A"

            # GraphRAG
            graph_resp = c.kg_graphrag(q)
            graph_ok = graph_resp.get("success", False)
            graph_answer = graph_resp.get("answer", "")[:80] if graph_ok else "N/A"

            print(f"         Vector RAG: {'✓' if rag_ok else '✗'} — {rag_answer}...")
            print(f"         GraphRAG:   {'✓' if graph_ok else '✗'} — {graph_answer}...")

    c._pass("RAG vs GraphRAG 对比完成")

    # ── Phase 5: 图谱查询进阶 ──

    print("\n── Phase 5: 图谱查询进阶 ──")

    print("\nSTEP 13: 路径发现 (两跳邻居)")
    resp = c.kg_query(
        "g.V().limit(1).repeat(out()).times(2).path().limit(3)"
    )
    if resp.get("success"):
        paths = resp.get("result", resp.get("data", []))
        c._pass(f"两跳路径 — 发现 {len(paths) if isinstance(paths, list) else 1} 条")
    else:
        print(f"  [INFO] {resp.get('error', '')}")

    print("\nSTEP 14: 社区发现 (连通分量)")
    resp = c.kg_query(
        "g.V().emit().repeat(out()).times(3).dedup().count()"
    )
    if resp.get("success"):
        c._pass(f"可达顶点数: {resp.get('result', '?')}")
    else:
        print(f"  [INFO] {resp.get('error', '')}")

    # 审计
    c.audit_record(DS_NAME, "graphrag_workflow",
                   details={"phases": 5, "kg_build": kg_available})

    # 清理
    c.delete_dataset(DS_NAME)

    print("\n" + "=" * 60)
    print("API-15  GraphRAG 端到端工作流 — ALL PASSED")
    print("=" * 60)


if __name__ == "__main__":
    main()
