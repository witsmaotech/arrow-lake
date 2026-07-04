#!/usr/bin/env python3
"""从 HugeGraph per-dataset 图拉子图样本 → graph.json（供 dashboard 力导向可视化）。

在容器网络内跑：
  docker run ... arrow-lake:1.8.6 python /examples_busi/export_graph.py

产出 results/graph.json: {nodes:[{id,label,name}], edges:[{source,target,label}]}。
节点按度数排序取前 80，保留相关边。
"""

from __future__ import annotations

import asyncio
import json
import os
from collections import defaultdict
from pathlib import Path

from arrow_lake import Lake

DATASET = "wuhu_lifeline"
RESULTS = Path(os.environ.get("BUSI_RESULTS_DIR", "/results"))
MAX_NODES = 80


def _flat(v):
    """HugeGraph valueMap 属性值常是单元素 list，展平。"""
    if isinstance(v, list):
        return v[0] if v else ""
    return v


def _name_from_id(vid: str) -> str:
    """id 形如 '3:芜湖市' / '2:wuhu_0209' → 取冒号后可读部分。"""
    if ":" in vid:
        return vid.split(":", 1)[1]
    return vid


async def main() -> None:
    lake = Lake(base_uri="/tmp/al-busi")
    RESULTS.mkdir(parents=True, exist_ok=True)

    print(f"拉取子图 (dataset={DATASET}, graph=kg_{DATASET}) ...")
    # 顶点（含 name 属性）
    verts = await lake.kg_query("g.V().limit(300).valueMap(true)") or []
    # 边（含 outV/inV 端点）
    edges_raw = await lake.kg_query("g.E().limit(400)") or []
    print(f"  原始: {len(verts)} 顶点, {len(edges_raw)} 边")

    # 顶点属性表 id -> {label, name}
    vmap: dict[str, dict] = {}
    for v in verts:
        if not isinstance(v, dict):
            continue
        vid = str(_flat(v.get("id", "")))
        label = str(_flat(v.get("label", "")) or "Entity")
        name = _flat(v.get("name")) or _flat(v.get("实体名")) or _flat(v.get("名称")) or ""
        vmap[vid] = {"label": label, "name": str(name) or _name_from_id(vid)}

    # 解析边
    edge_list: list[dict] = []
    for e in edges_raw:
        if not isinstance(e, dict):
            continue
        src = _flat(e.get("outV", ""))
        tgt = _flat(e.get("inV", ""))
        if not src or not tgt:
            continue
        edge_list.append({
            "source": str(src), "target": str(tgt),
            "label": str(_flat(e.get("label", "")) or "related"),
        })

    # 构造候选节点（端点）+ 补属性
    cand: dict[str, dict] = {}
    for e in edge_list:
        for vid in (e["source"], e["target"]):
            if vid not in cand:
                info = vmap.get(vid, {})
                cand[vid] = {
                    "id": vid,
                    "label": info.get("label") or "Entity",
                    "name": info.get("name") or _name_from_id(vid),
                }

    # 按度数排序，取前 MAX_NODES
    deg: dict[str, int] = defaultdict(int)
    for e in edge_list:
        deg[e["source"]] += 1
        deg[e["target"]] += 1
    top_ids = {v for v, _ in sorted(deg.items(), key=lambda x: -x[1])[:MAX_NODES]}
    nodes = [cand[i] for i in top_ids if i in cand]
    edges = [e for e in edge_list if e["source"] in top_ids and e["target"] in top_ids]

    # 按标签分组（用于上色）
    labels = sorted({n["label"] for n in nodes})

    out = {
        "nodes": nodes,
        "edges": edges,
        "labels": labels,
        "count": {"nodes": len(nodes), "edges": len(edges),
                  "total_vertices": len(vmap), "raw_edges": len(edge_list)},
    }
    dst = RESULTS / "graph.json"
    dst.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"  → {dst}: {len(nodes)} 节点, {len(edges)} 边, {len(labels)} 类型")
    lake.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
