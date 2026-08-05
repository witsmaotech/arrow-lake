#!/usr/bin/env bash
# HugeGraph KG 边去重 / 多重性诊断 (v1.10.2 M2/P3 SOP).
#
# 背景: M2 实测确认 HugeGraph 边 id = (src, label, sort_values, dst),重插 upsert,
# 正常不会累积重复边。结构边 frequency=SINGLE 本就幂等;关系边 frequency=MULTIPLE +
# sort_keys=[relation_type] 保留同顶点对不同关系类型。故本脚本主要是**诊断工具**:
# 报告每条边 label 下同一 (outV, inV) 对的多重性,标记任何**真正重复**(签名完全相同 =
# label+端点+sort_key 值全等)——若出现则属异常(并发竞态/旧 bug 残留),可用 --apply
# 删多余(留一条)。
#
# ⚠️ 注意: 旧图(关系 label 仍是 SINGLE)的「同对不同关系类型塌缩成一条」是**数据丢失**,
# 本脚本无法恢复(需 drop+recreate 关系 label 后重建 KG)。本脚本只清真正重复边。
#
# 用法:
#   bash deploy/scripts/kg-dedup-edges.sh <dataset> [--apply]
#   # 报告(只读,默认): 列出每对的多重性 + 标记重复组
#   # --apply: 删除重复组中多余边(每组保留一条)
#  env 覆盖: HG_HOST / HG_PORT / HG_USER / HG_PASS
set -euo pipefail

DS="${1:?usage: $0 <dataset> [--apply]}"
APPLY=0
[[ "${2:-}" == "--apply" ]] && APPLY=1

HG_HOST="${HG_HOST:-127.0.0.1}"
HG_PORT="${HG_PORT:-8089}"
HG_USER="${HG_USER:-admin}"
HG_PASS="${HG_PASS:-pa}"
GRAPH="kg_${DS}"

if [[ "$APPLY" == "1" ]]; then
  echo "→ 边去重(apply): ${GRAPH}"
  echo "  ⚠️ --apply 开启:将删除重复边(留一条)"
else
  echo "→ 边多重性诊断(只读): ${GRAPH}"
  echo "  (加 --apply 才删重复边)"
fi

.venv/bin/python3 - "$GRAPH" "$HG_HOST" "$HG_PORT" "$HG_USER" "$HG_PASS" "$APPLY" <<'PYEOF'
import sys, json, urllib.parse
import httpx

graph, host, port, user, pw, apply = sys.argv[1:7]
apply = apply == "1"
base = f"http://{host}:{port}"
auth = (user, pw)

# 1. 拉全量边 (分页)
edges = []
offset = 0
with httpx.Client(auth=auth, timeout=30.0) as c:
    while True:
        r = c.get(f"{base}/graphs/{graph}/graph/edges",
                  params={"limit": "1000", "offset": str(offset)})
        r.raise_for_status()
        batch = r.json().get("edges", [])
        if not batch:
            break
        edges.extend(batch)
        if len(batch) < 1000:
            break
        offset += len(batch)

print(f"  总边数: {len(edges)}")

# 2. 按签名分组: (label, outV, inV, sort_key 值) 全等 = 真正重复
def sig(e):
    p = e.get("properties", {}) or {}
    sk = tuple(sorted((k, str(v)) for k, v in p.items()))
    return (e.get("label"), str(e.get("outV")), str(e.get("inV")), sk)

groups = {}
for e in edges:
    groups.setdefault(sig(e), []).append(e)

dups = {s: g for s, g in groups.items() if len(g) > 1}
if not dups:
    print("  ✅ 无真正重复边 (所有边签名唯一)")
else:
    print(f"  ⚠️ 发现 {len(dups)} 组重复:")
    with httpx.Client(auth=auth, timeout=30.0) as c:
        for s, g in dups.items():
            keep, rest = g[0], g[1:]
            print(f"    {s[0]} {s[1]}→{s[2]}: {len(g)} 条 (保留 {keep['id']}, 删 {len(rest)})")
            if apply:
                for e in rest:
                    eid = urllib.parse.quote(json.dumps(e["id"]), safe="")
                    code = c.delete(f"{base}/graphs/{graph}/graph/edges/{eid}").status_code
                    print(f"      delete {e['id']}: {code}")
    if not apply:
        print("  (加 --apply 执行删除)")

# 3. 多重性概览: 按 (label, outV, inV) 分组(不含 sort_key),看端点对多重性。
#    relation 边同对不同 relation_type 多条属正常;结构边>1 属异常。
pairs = {}
for e in edges:
    k = (e.get("label"), str(e.get("outV")), str(e.get("inV")))
    pairs.setdefault(k, []).append(e)
multi_pairs = {k: g for k, g in pairs.items() if len(g) > 1}
print(f"  端点对多重(label 内同 outV→inV >1): {len(multi_pairs)} 对")
print("    (关系边同对不同 relation_type 多条=正常;结构边/同 relation_type 多条=异常)")
PYEOF
