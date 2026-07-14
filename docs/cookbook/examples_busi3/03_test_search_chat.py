#!/usr/bin/env python3
"""examples_busi3 / 03 — 重测 search_ka/chat_ka (任务#1 验证)。

验证两条路径都通:
  A. extractor 直调: ext.search_ka / ext.chat_ka (hyper-extract 原生链路)
  B. facade: lake.kg_search / lake.kg_chat (任务#2 新增, async + 序列化)

3 个检索查询 + 3 个 RAG 问答应覆盖 DDD 核心概念。结果落 data/results/03_test.json。
env: source env.sh; 前置: 02_build_kg.py 已产出 KA dump。
"""
from __future__ import annotations

import asyncio
import json
import os
import time
from pathlib import Path

DS = os.environ.get("BUSI3_DS", "jd_ddd")
RESULTS = Path(os.environ.get("BUSI3_DIR", ".")) / "data" / "results"
RESULTS.mkdir(parents=True, exist_ok=True)
REPORT = RESULTS / "03_test.json"

SEARCH_QUERIES = [
    "聚合根是什么概念",          # 定义类 (语义召回 definition)
    "限界上下文 boundary",        # 概念类
    "领域服务 domain service",    # 英中混合
]
CHAT_QUESTIONS = [
    "DDD 的核心设计原则是什么?",
    "聚合根和领域服务有什么区别?",
    "通天塔平台为什么需要引入 DDD?",
]

t0 = time.time()


def log(m: str) -> None:
    print(f"[{time.time() - t0:>6.1f}s] {m}", flush=True)


def _pyd_to_dict(n):
    """hyper-extract 节点/边 → dict (pydantic.model_dump / __dict__ / 原样)."""
    if isinstance(n, dict):
        return n
    if hasattr(n, "model_dump"):
        try:
            return n.model_dump()
        except Exception:  # noqa: BLE001
            pass
    if hasattr(n, "__dict__"):
        return {k: v for k, v in vars(n).items() if not k.startswith("_")}
    return {"value": str(n)}


def _node_brief(n: dict) -> str:
    """单行节点摘要 (type | name | definition 前缀)."""
    name = n.get("name") or n.get("label") or n.get("id") or "?"
    typ = n.get("type") or ""
    definition = n.get("definition") or n.get("description") or ""
    return f"[{typ}] {name} | {str(definition)[:60]}"


async def main() -> int:
    from arrow_lake import Lake

    lake = Lake()
    ext = lake._get_kg_extractor()
    report: dict = {"dataset": DS, "extractor": type(ext).__name__ if ext else None,
                    "search": [], "chat": [], "facade_ok": None}

    # ===== A. extractor 直调: search_ka =====
    log(f"=== A. extractor.search_ka (直接调 hyper-extract) ===")
    for q in SEARCH_QUERIES:
        ts = time.time()
        try:
            nodes, edges = ext.search_ka(DS, q, top_k=5)
            dt = time.time() - ts
            nodes = [_pyd_to_dict(n) for n in (nodes or [])]
            edges = [_pyd_to_dict(e) for e in (edges or [])]
            entry = {"query": q, "ok": True, "elapsed_s": round(dt, 1),
                     "node_count": len(nodes), "edge_count": len(edges),
                     "top_nodes": [_node_brief(n) for n in nodes[:5]]}
            log(f"  ✓ {dt:.1f}s | {len(nodes)} nodes / {len(edges)} edges | {q}")
            for n in nodes[:3]:
                log(f"      - {_node_brief(n)}")
        except Exception as e:
            import traceback
            entry = {"query": q, "ok": False, "elapsed_s": round(time.time() - ts, 1),
                     "error": f"{type(e).__name__}: {str(e)[:200]}"}
            log(f"  ✗ {q}: {entry['error']}")
            traceback.print_exc()
        report["search"].append(entry)

    # ===== B. facade: lake.kg_search (async, 序列化) =====
    log(f"=== B. facade.lake.kg_search (任务#2) ===")
    try:
        fr = await lake.kg_search(DS, SEARCH_QUERIES[0], top_k=5)
        log(f"  ✓ facade kg_search: {fr['node_count']} nodes / {fr['edge_count']} edges (serialized dict)")
        report["facade_search_sample"] = {
            "node0_keys": list(fr["nodes"][0].keys()) if fr["nodes"] else [],
            "node_count": fr["node_count"],
        }
        facade_ok = True
    except Exception as e:
        log(f"  ✗ facade kg_search 失败: {type(e).__name__}: {str(e)[:150]}")
        report["facade_search_error"] = f"{type(e).__name__}: {str(e)[:200]}"
        facade_ok = False

    # ===== C. extractor 直调: chat_ka (RAG) =====
    log(f"=== C. extractor.chat_ka (RAG 问答) ===")
    for q in CHAT_QUESTIONS:
        ts = time.time()
        try:
            resp = ext.chat_ka(DS, q, top_k=5)
            dt = time.time() - ts
            ans = getattr(resp, "content", "") or ""
            retrieved = (getattr(resp, "additional_kwargs", {}) or {}).get("retrieved_items", [])
            entry = {"question": q, "ok": True, "elapsed_s": round(dt, 1),
                     "answer_len": len(ans), "retrieval_count": len(retrieved or []),
                     "answer_excerpt": ans[:300]}
            log(f"  ✓ {dt:.1f}s | 答({len(ans)}字) + {len(retrieved or [])} retrieved | {q}")
            log(f"      答: {ans[:160]}")
        except Exception as e:
            import traceback
            entry = {"question": q, "ok": False, "elapsed_s": round(time.time() - ts, 1),
                     "error": f"{type(e).__name__}: {str(e)[:200]}"}
            log(f"  ✗ {q}: {entry['error']}")
            traceback.print_exc()
        report["chat"].append(entry)

    # ===== D. facade: lake.kg_chat =====
    log(f"=== D. facade.lake.kg_chat (任务#2) ===")
    try:
        fr = await lake.kg_chat(DS, CHAT_QUESTIONS[0], top_k=5)
        log(f"  ✓ facade kg_chat: 答({len(fr['answer'])}字) + {fr['retrieval_count']} retrieved")
        report["facade_chat_sample"] = {"answer_len": len(fr["answer"]),
                                        "retrieval_count": fr["retrieval_count"]}
        facade_ok = facade_ok and True
    except Exception as e:
        log(f"  ✗ facade kg_chat 失败: {type(e).__name__}: {str(e)[:150]}")
        facade_ok = False
    report["facade_ok"] = bool(facade_ok)

    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2))
    log(f"报告 → {REPORT}")
    lake.shutdown()

    search_ok = all(e["ok"] for e in report["search"])
    chat_ok = all(e["ok"] for e in report["chat"])
    log("=" * 60)
    log(f"search: {'✓' if search_ok else '✗'} | chat: {'✓' if chat_ok else '✗'} | facade: {'✓' if report['facade_ok'] else '✗'}")
    return 0 if (search_ok and chat_ok and report["facade_ok"]) else 1


if __name__ == "__main__":
    import sys
    sys.exit(asyncio.run(main()))
