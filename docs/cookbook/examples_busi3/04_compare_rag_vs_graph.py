#!/usr/bin/env python3
"""examples_busi3 / 04 — RAG(KA 语义检索) vs HugeGraph 图查询 对比 (任务#3)。

同一批问题/概念, 分别走两条检索路径, 对比召回方式 / 内容 / 延迟 / 适用场景:

  RAG 路径 (hyper-extract KA):
    - lake.kg_search(ds, q)     → FAISS 语义召回 nodes/edges (按"意思")
    - lake.kg_chat(ds, q)        → RAG 生成式回答

  图查询路径 (HugeGraph kg_jd_ddd):
    - REST 顶点扫描 + 客户端关键词过滤  → 精确 label/name 匹配 (按"字面")
    - lake.kg_get_neighbors(vid)        → 边邻居遍历 (按"拓扑")
    - lake.kg_stats(ds)                 → 全图规模

  注: per-dataset 动态图 gremlin 遍历源未全局绑定 (见 CLAUDE.md 运维速查),
      故图侧用 REST 顶点/边 + neighbors, 不走 gremlin。

结果落 data/results/04_compare.json + 打印对比表。
env: source env.sh; 前置: 02_build_kg.py。
"""
from __future__ import annotations

import asyncio
import json
import os
import time
from pathlib import Path

import requests

DS = os.environ.get("BUSI3_DS", "jd_ddd")
RESULTS = Path(os.environ.get("BUSI3_DIR", ".")) / "data" / "results"
RESULTS.mkdir(parents=True, exist_ok=True)
REPORT = RESULTS / "04_compare.json"

HG_HOST = os.environ.get("ARROW_LAKE__HUGEGRAPH__HOST", "127.0.0.1")
HG_PORT = os.environ.get("ARROW_LAKE__HUGEGRAPH__PORT", "8089")
HG_USER = os.environ.get("ARROW_LAKE__HUGEGRAPH__USERNAME", "admin")
HG_PASS = os.environ.get("ARROW_LAKE__HUGEGRAPH__PASSWORD", "pa")
GRAPH = f"kg_{DS}"

# 对比用例: (concept 关键词, 自然语言问题)
CASES = [
    ("聚合根", "聚合根的核心设计原则是什么?它和领域服务有何区别?"),
    ("限界上下文", "什么是限界上下文?它如何划分系统边界?"),
    ("领域事件", "领域事件的作用是什么?谁发布谁消费?"),
]

t0 = time.time()


def log(m: str) -> None:
    print(f"[{time.time() - t0:>6.1f}s] {m}", flush=True)


def _sess() -> requests.Session:
    s = requests.Session()
    s.trust_env = False  # 不走代理 (内部 HG)
    return s


def rest_vertices(label: str | None = None, limit: int = 500) -> list[dict]:
    """REST 分页拉取图顶点 (per-dataset 图 gremlin 不可靠, 走 REST).

    ``label`` 过滤某类顶点 (如 'entity' 概念顶点), 避开 chunk/document 溯源顶点。
    HugeGraph vertices REST 单次上限 ~200, 按 page token 分页直到取完。
    """
    s = _sess()
    all_v: list[dict] = []
    page: str | None = None
    while True:
        params: dict = {"limit": 200}
        if label:
            params["label"] = label
        if page:
            params["page"] = page
        r = s.get(f"http://{HG_HOST}:{HG_PORT}/graphs/{GRAPH}/graph/vertices",
                  params=params, auth=(HG_USER, HG_PASS),
                  headers={"Accept-Encoding": "gzip"}, timeout=30)
        if r.status_code != 200:
            break
        data = r.json()
        batch = data.get("vertices", [])
        all_v.extend(batch)
        nxt = data.get("page")
        if not nxt or not batch or len(all_v) >= limit:
            break
        page = nxt
    return all_v


def vertex_name(v: dict) -> str:
    props = v.get("properties", {})
    for k in ("name", "label", "title", "名称"):
        if k in props:
            val = props[k]
            if isinstance(val, list) and val:
                val = val[0].get("value", "") if isinstance(val[0], dict) else val[0]
            return str(val)
    return v.get("id", "?")


def vertex_match(vertices: list[dict], keyword: str) -> list[dict]:
    """图侧"精确/字面"召回: 顶点名包含关键词。"""
    kw = keyword.lower()
    out = []
    for v in vertices:
        name = vertex_name(v).lower()
        if kw in name:
            out.append({"id": v.get("id"), "label": v.get("label"),
                        "name": vertex_name(v)})
    return out


async def main() -> int:
    from arrow_lake import Lake

    lake = Lake()

    # --- 图侧预取: entity 概念顶点 (字面匹配用) ---
    log(f"=== 图查询路径: REST 顶点扫描 (kg_{DS}) ===")
    ts = time.time()
    concept_vertices = rest_vertices(label="entity")
    log(f"  概念顶点(entity): {len(concept_vertices)} ({time.time() - ts:.1f}s)")
    stats = await lake.kg_stats(dataset_name=DS)
    log(f"  kg_stats: vertices={stats.get('total_vertices',0)} edges={stats.get('total_edges',0)}")
    # 图的结构优势: 概念顶点的 type 分布 (RAG 给不出这种全局分类视图)
    vtypes = {}
    for v in concept_vertices:
        t = (v.get("properties", {}) or {}).get("type", "?")
        vtypes[t] = vtypes.get(t, 0) + 1
    top_types = dict(sorted(vtypes.items(), key=lambda x: -x[1])[:8])
    log(f"  概念 type 分布 (top8): {top_types}")

    report: dict = {"dataset": DS, "graph_stats": stats, "cases": [],
                    "concept_type_dist": vtypes, "concept_count": len(concept_vertices)}

    # --- 图的强项演示: 已知顶点的邻居遍历 (RAG 做不到的拓扑召回) ---
    log(f"=== 图强项: 邻居遍历 (RAG 做不到的拓扑召回) ===")
    if concept_vertices:
        sample = concept_vertices[0]
        sname = (sample.get("properties", {}) or {}).get("name", "?")
        sid = sample.get("id")
        log(f"  取概念顶点「{sname}」(id={sid}) 做 1 跳邻居遍历:")
        try:
            nb = await lake.kg_get_neighbors(str(sid), depth=1, dataset_name=DS)
            nb_names = [(v.get("properties", {}) or {}).get("name", v.get("id"))
                        for v in (nb if isinstance(nb, list) else [])][:8]
            log(f"    ✓ {len(nb)} 个 1 跳邻居 | 样例: {nb_names}")
            report["graph_traversal_demo"] = {"source": sname, "source_id": str(sid),
                                               "neighbor_count": len(nb), "sample_neighbors": nb_names}
        except Exception as e:
            log(f"    ✗ neighbors 失败: {type(e).__name__}: {str(e)[:100]}")
            report["graph_traversal_demo"] = {"error": f"{type(e).__name__}: {str(e)[:120]}"}

    for keyword, question in CASES:
        log(f"\n{'='*70}")
        log(f"用例: 关键词「{keyword}」| 问题「{question[:40]}...」")
        case: dict = {"keyword": keyword, "question": question}

        # --- 图查询路径 ---
        log(f"  [图] 字面匹配 name 含「{keyword}」:")
        ts = time.time()
        matched = vertex_match(concept_vertices, keyword)
        graph_t = time.time() - ts
        case["graph"] = {
            "method": "REST vertex scan + keyword filter (+ neighbors)",
            "matched_count": len(matched),
            "matched": matched[:5],
            "vertex_scan_s": round(graph_t, 2),
        }
        for m in matched[:3]:
            log(f"        - [{m['label']}] {m['name']} (id={m['id']})")
        if not matched:
            log(f"        (无字面匹配 — REST 扫描上限 ~200 + 需精确 name 子串; 概念可能被拆分或超扫描范围)")

        # neighbors on first match
        if matched:
            ts = time.time()
            try:
                nb = await lake.kg_get_neighbors(matched[0]["id"], depth=1, dataset_name=DS)
                case["graph"]["neighbors_count"] = len(nb)
                case["graph"]["neighbors_s"] = round(time.time() - ts, 2)
                log(f"  [图] neighbors(1跳) of 「{matched[0]['name']}」: {len(nb)} 个 ({time.time()-ts:.1f}s)")
            except Exception as e:
                case["graph"]["neighbors_error"] = f"{type(e).__name__}: {str(e)[:120]}"
                log(f"  [图] neighbors 失败: {type(e).__name__}: {str(e)[:80]}")

        # --- RAG 路径: search_ka (语义召回) ---
        log(f"  [RAG] kg_search 语义召回「{keyword}」(top_k=5):")
        ts = time.time()
        try:
            sr = await lake.kg_search(DS, keyword, top_k=5)
            search_t = time.time() - ts
            case["rag_search"] = {
                "method": "FAISS semantic recall over KA node definitions",
                "node_count": sr["node_count"], "edge_count": sr["edge_count"],
                "elapsed_s": round(search_t, 1),
                "top_nodes": [{"type": n.get("type", ""), "name": n.get("name", ""),
                               "definition": str(n.get("definition", ""))[:60]}
                              for n in sr["nodes"][:5]],
            }
            log(f"        ✓ {search_t:.1f}s | {sr['node_count']} nodes / {sr['edge_count']} edges")
            for n in sr["nodes"][:3]:
                log(f"          - [{n.get('type','')}] {n.get('name','')} | {str(n.get('definition',''))[:50]}")
        except Exception as e:
            case["rag_search"] = {"error": f"{type(e).__name__}: {str(e)[:150]}"}
            log(f"        ✗ {case['rag_search']['error']}")

        # --- RAG 路径: chat_ka (生成式回答) ---
        log(f"  [RAG] kg_chat 生成回答 (top_k=5):")
        ts = time.time()
        try:
            cr = await lake.kg_chat(DS, question, top_k=5)
            chat_t = time.time() - ts
            case["rag_chat"] = {
                "method": "RAG: retrieve top_k nodes/edges → LLM generate",
                "answer_len": len(cr["answer"]), "retrieval_count": cr["retrieval_count"],
                "elapsed_s": round(chat_t, 1),
                "answer_excerpt": cr["answer"][:400],
            }
            log(f"        ✓ {chat_t:.1f}s | 答({len(cr['answer'])}字) + {cr['retrieval_count']} retrieved")
            log(f"          答: {cr['answer'][:200]}")
        except Exception as e:
            case["rag_chat"] = {"error": f"{type(e).__name__}: {str(e)[:150]}"}
            log(f"        ✗ {case['rag_chat']['error']}")

        report["cases"].append(case)

    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2))
    log(f"\n报告 → {REPORT}")
    lake.shutdown()

    # --- 汇总对比表 ---
    log("\n" + "=" * 70)
    log("对比汇总:")
    log(f"{'用例':<14} | {'图(字面匹配)':<16} | {'RAG search(语义)':<18} | {'RAG chat(生成)':<14}")
    for c in report["cases"]:
        g = c.get("graph", {})
        rs = c.get("rag_search", {})
        rc = c.get("rag_chat", {})
        g_str = f"{g.get('matched_count','?')}命中/{g.get('neighbors_count','-')}邻居"
        rs_str = f"{rs.get('node_count','✗')}节点 {rs.get('elapsed_s','?')}s"
        rc_str = f"{rc.get('answer_len','✗')}字 {rc.get('elapsed_s','?')}s" if "answer_len" in rc else "✗"
        log(f"{c['keyword']:<14} | {g_str:<16} | {rs_str:<18} | {rc_str:<14}")
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(asyncio.run(main()))
