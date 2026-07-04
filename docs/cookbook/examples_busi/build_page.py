#!/usr/bin/env python3
"""读 results/*.json + graph.json → 注入深色仪表盘模板 → 自包含 dashboard.html。

cytoscape.min.js 下载后内联（完全自包含、无 CDN/SRI 依赖、离线可看）。
数据内联（无 file:// CORS 问题，双击即开）。

运行（宿主 .venv，首次需代理下载 cytoscape）：
    HTTPS_PROXY=http://127.0.0.1:7887 .venv/bin/python3 docs/cookbook/examples_busi/build_page.py
"""

from __future__ import annotations

import json
import os
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
RESULTS = HERE / "results"
CY_URL = "https://cdnjs.cloudflare.com/ajax/libs/cytoscape/3.28.1/cytoscape.min.js"
CY_PATH = HERE / "assets" / "cytoscape.min.js"


def _load(name: str):
    try:
        return json.loads((RESULTS / name).read_text(encoding="utf-8"))
    except Exception:
        return {}


def _ensure_cytoscape() -> str:
    """下载（缓存）并返回 cytoscape.min.js 内容；失败返回空串（template 降级 CDN+SRI）。"""
    if CY_PATH.exists() and CY_PATH.stat().st_size > 10000:
        return CY_PATH.read_text(encoding="utf-8")
    try:
        CY_PATH.parent.mkdir(parents=True, exist_ok=True)
        proxy = os.environ.get("HTTPS_PROXY") or os.environ.get("HTTP_PROXY")
        if proxy:
            opener = urllib.request.build_opener(
                urllib.request.ProxyHandler({"https": proxy, "http": proxy})
            )
        else:
            opener = urllib.request.build_opener()
        data = opener.open(CY_URL, timeout=30).read()
        CY_PATH.write_bytes(data)
        print(f"  下载 cytoscape {len(data)} bytes → assets/")
        return data.decode("utf-8", errors="replace")
    except Exception as e:
        print(f"  [warn] cytoscape 下载失败，降级 CDN: {e}")
        return ""


def _display_name(raw: str) -> str:
    """id/name 形如 '3:芜湖市' / '2:wuhu_0209' → 取冒号后。"""
    if not raw:
        return "?"
    return raw.split(":", 1)[1] if ":" in raw else raw


def main() -> None:
    s1, s2, s3 = _load("01_ingest.json"), _load("02_embed_index.json"), _load("03_search.json")
    s4, s5 = _load("04_kg_build.json"), _load("05_kg_traversal.json")
    s6, s7 = _load("06_rag_qa.json"), _load("07_graphrag_qa.json")
    graph = _load("graph.json")

    st4 = s4.get("status", {}) if isinstance(s4.get("status"), dict) else {}
    st5 = s5.get("stats", {}) if isinstance(s5.get("stats"), dict) else {}
    rag_qs = s6.get("questions", []) if isinstance(s6, dict) else []
    gr_qs = s7.get("questions", []) if isinstance(s7, dict) else []

    # graph 节点 display_name
    for n in graph.get("nodes", []):
        n["display_name"] = _display_name(n.get("name") or n.get("id", ""))

    data = {
        "meta": {
            "dataset": "wuhu_lifeline", "version": "v1.8.6",
            "pdf": "芜湖市城市生命线安全工程一期建设方案",
            "pages": 552, "chunks": s1.get("rows", 552),
        },
        "metrics": [
            {"k": "摄入块数", "v": s1.get("rows", "?"), "u": "块"},
            {"k": "嵌入向量", "v": s2.get("embedded_rows", "?"), "u": "×1024"},
            {"k": "KG 实体", "v": st4.get("entity_count", "?"), "u": "个"},
            {"k": "KG 关系", "v": st4.get("relation_count", "?"), "u": "条"},
            {"k": "顶点", "v": st5.get("total_vertices", "?"), "u": ""},
            {"k": "边", "v": st5.get("total_edges", "?"), "u": ""},
            {"k": "KG 耗时", "v": round(s4.get("elapsed_sec", 0)), "u": "s"},
            {"k": "问答", "v": len(rag_qs) + len(gr_qs), "u": "题"},
        ],
        "pipeline": [
            {"n": "PDF 摄入", "d": "pypdf→jsonl", "ok": True},
            {"n": "向量嵌入", "d": "bge-m3", "ok": bool(s2.get("vector_index"))},
            {"n": "检索", "d": "FTS/VEC/HYB", "ok": True},
            {"n": "KG 构建", "d": f"{st4.get('processed_chunks','?')}/{st4.get('total_chunks','?')}",
             "ok": st4.get("status") == "COMPLETED"},
            {"n": "图谱遍历", "d": "g.V/kneighbor", "ok": True},
            {"n": "RAG", "d": "qwen2.5:14b", "ok": True},
            {"n": "GraphRAG", "d": "per-dataset", "ok": True},
        ],
        "graph": graph,
        "search": [
            {"kw": kw, "count": (v or {}).get("count"),
             "sample": ((v or {}).get("samples") or [""])[0]}
            for kw, v in (s3.get("text_search") or {}).items()
        ],
        "rag": [{"q": q.get("q"), "a": q.get("a"), "cite": q.get("citations")} for q in rag_qs],
        "graphrag": [{"q": q.get("q"), "a": q.get("a"), "cite": q.get("citations")} for q in gr_qs],
        "findings": [
            {"t": "镜像无 kreuzberg，PDF 摄入失败", "f": "prepare_pdf.py (pypdf→jsonl)"},
            {"t": "bge-m3 1024维，IVF_PQ num_sub_vectors 不整除", "f": "num_sub_vectors=32"},
            {"t": "HugeGraph 503 并发过载中断", "f": "BUILD_CONCURRENCY=3"},
            {"t": "邻居遍历 Vertex 不存在", "f": "g.V() 取真实顶点 id"},
            {"t": "qwen3.5:9b model not found", "f": "qwen2.5:14b"},
        ],
    }

    tmpl = (HERE / "dashboard_template.html").read_text(encoding="utf-8")
    out = tmpl.replace("{{DATA_JSON}}", json.dumps(data, ensure_ascii=False))

    # 内联 cytoscape（自包含、无外部脚本）；下载失败则移除 CDN tag，JS 走静态 fallback
    cy_js = _ensure_cytoscape()
    cdn_tag = '<script src="https://cdnjs.cloudflare.com/ajax/libs/cytoscape/3.28.1/cytoscape.min.js"></script>'
    out = out.replace(cdn_tag, f"<script>{cy_js}</script>" if cy_js else "")

    dst = HERE / "dashboard.html"
    dst.write_text(out, encoding="utf-8")
    print(f"  → {dst} ({dst.stat().st_size / 1024:.0f} KB)")
    print(f"  指标 {len(data['metrics'])} 项 | 流水线 {len(data['pipeline'])} 步 | "
          f"图谱 {len(graph.get('nodes',[]))} 节点 | RAG {len(rag_qs)} + GraphRAG {len(gr_qs)} 题")


if __name__ == "__main__":
    main()
