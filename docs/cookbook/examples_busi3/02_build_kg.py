#!/usr/bin/env python3
"""examples_busi3 / 02 — 构建 jd_ddd KG (hyper-extract → HugeGraph + KA dump)。

前置: jd_ddd 已 ingest 到 lake (66 chunk, 见 01_ingest_jd.py)。本脚本:
  1. clear kg_jd_ddd 旧数据 (保留 shell, 避免 GraphManager 脏态)
  2. lake.kg_build('jd_ddd') — fire-and-forget, 返回 task_id
  3. 轮询 kg_build_status 直到 COMPLETED/FAILED
  4. 校验: HG kg_jd_ddd 顶点/边数 + KA dump 落盘 (data/ka/jd_ddd/ka/)

env: source env.sh (百炼 qwen-turbo + 本地 ollama qwen3-embedding:4b)
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from pathlib import Path

DS = os.environ.get("BUSI3_DS", "jd_ddd")
RESULTS = Path(os.environ.get("BUSI3_DIR", ".")) / "data" / "results"
RESULTS.mkdir(parents=True, exist_ok=True)
REPORT = RESULTS / "02_build_kg.json"

t0 = time.time()


def log(m: str) -> None:
    print(f"[{time.time() - t0:>7.0f}s] {m}", flush=True)


async def main() -> int:
    from arrow_lake import Lake

    lake = Lake()

    # 1. clear 旧图数据 (保留 shell)
    try:
        await lake.kg_delete_graph(dataset_name=DS)
        log(f"cleared kg_{DS} 旧数据 (保留 shell)")
    except Exception as e:
        log(f"clear 跳过 (无旧数据或已清): {type(e).__name__}: {str(e)[:80]}")

    # 2. 触发 kg_build (fire-and-forget)
    task_id = await lake.kg_build(DS)
    log(f"kg_build started: task_id={task_id}")

    # 3. 轮询状态
    last = None
    while True:
        st = await lake.kg_build_status(task_id)
        if st is None:
            log("WARN: task 状态查询返回 None (跨 worker 不可见?), 等待重试")
            await asyncio.sleep(5)
            continue
        status = st["status"]
        if status != last:
            log(f"status={status} | {st['processed_chunks']}/{st['total_chunks']} chunk | "
                f"ent={st['entity_count']} rel={st['relation_count']}")
            last = status
        if status in ("COMPLETED", "FAILED", "completed", "failed"):
            break
        await asyncio.sleep(5)

    final = await lake.kg_build_status(task_id)
    if final is None or final["status"] not in ("COMPLETED", "completed"):
        log(f"✗ build 未完成: {final}")
        lake.shutdown()
        return 1

    # 4. 校验真实图数据 (不信 task entity_count)
    stats = await lake.kg_stats(dataset_name=DS)
    log(f"HG kg_{DS} stats: vertices={stats.get('total_vertices',0)} edges={stats.get('total_edges',0)}")

    ka_dir = Path(lake._config.hugegraph.he_ka_base_dir) / DS / "ka"
    files = sorted(p.name for p in ka_dir.iterdir()) if ka_dir.is_dir() else []
    log(f"KA dump {ka_dir}: {files}")

    report = {
        "dataset": DS,
        "task_id": task_id,
        "build_status": final["status"],
        "task_entity_count": final.get("entity_count", 0),
        "task_relation_count": final.get("relation_count", 0),
        "hg_vertices": stats.get("total_vertices", 0),
        "hg_edges": stats.get("total_edges", 0),
        "ka_dir": str(ka_dir),
        "ka_files": files,
        "elapsed_s": round(time.time() - t0, 1),
    }
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2))
    log(f"报告 → {REPORT}")
    lake.shutdown()

    ok = report["hg_vertices"] > 0 and ("data.json" in files)
    log("✓ build 成功" if ok else "✗ build 异常 (0 顶点或无 KA dump)")
    return 0 if ok else 2


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
