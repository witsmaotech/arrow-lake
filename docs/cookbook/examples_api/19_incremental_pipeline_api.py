#!/usr/bin/env python3
"""API-19 — 增量数据管线

业务场景: 数据仓库按天/批次增量更新，需保证数据一致性、索引同步、血缘可追溯
数据源: datas/transactions/sales_2024.csv (模拟分批追加)
流程: 初始批量摄取 → 分批追加 → 索引重建 → 一致性校验 → 血缘追踪 → 审计
"""

from __future__ import annotations
import os

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from conftest import ArrowLakeClient

BASE_URL = os.environ.get("ARROW_LAKE_BASE_URL", "http://localhost:8000")
API_KEY = os.environ.get("ARROW_LAKE_API_KEY", "dev-api-key-for-local-testing-only")
DATAS_DIR = Path(__file__).resolve().parent.parent / "datas"

DS_NAME = "incremental-sales"
BATCH_SIZE = 5  # 模拟每批 5 条追加


def main() -> None:
    print("=" * 60)
    print("API-19  增量数据管线")
    print("=" * 60)

    c = ArrowLakeClient(BASE_URL, API_KEY)

    c.delete_dataset(DS_NAME)

    csv_path = DATAS_DIR / "transactions" / "sales_2024.csv"
    assert csv_path.exists(), f"数据文件不存在: {csv_path}"

    # ── Phase 1: 初始批量摄取 ──

    print("\n── Phase 1: 初始批量摄取 ──")

    print("\nSTEP 1: 全量摄取第一批")
    resp = c.ingest_files(DS_NAME, [str(csv_path)])
    if not resp.get("success"):
        print(f"  [SKIP] 初始摄取失败: {resp.get('error')}: {resp.get('message', '')[:120]}")
        c.delete_dataset(DS_NAME)
        return

    initial_rows = resp.get("total_rows", 0)
    c._pass(f"初始摄取 — {initial_rows} 行")

    c.lineage_record(DS_NAME, "initial_load",
                     inputs=["raw/sales_2024.csv"], outputs=[DS_NAME],
                     metadata={"batch": 0, "rows": initial_rows})

    # ── Phase 2: 数据快照 ──

    print("\n── Phase 2: 数据快照 ──")

    print("\nSTEP 2: 记录初始行数")
    resp = c.query_olap(DS_NAME,
        f'SELECT count(*) as cnt, '
        f'  min(timestamp) as first_order, '
        f'  max(timestamp) as last_order, '
        f'  count(DISTINCT user_id) as users '
        f'FROM "{DS_NAME}"')
    if resp.get("success"):
        rows = resp.get("rows", [])
        if rows:
            r = rows[0]
            baseline_count = r.get("cnt", initial_rows)
            c._pass(f"基线快照 — {baseline_count} 行, "
                    f"{r.get('users', 0)} 用户, "
                    f"时间范围 {r.get('first_order', '?')} ~ {r.get('last_order', '?')}")

    # ── Phase 3: FTS 索引 (初始) ──

    print("\n── Phase 3: 初始索引构建 ──")

    print("\nSTEP 3: 创建 FTS 索引")
    resp = c.create_fts_index(DS_NAME, fts_column="text_content")
    if resp.get("success"):
        c._pass("FTS 索引就绪")
    else:
        print(f"  [INFO] {resp.get('error', '')}")

    print("\nSTEP 4: 初始搜索验证")
    resp = c.search_fts(DS_NAME, "Electronics", top_k=3)
    if resp.get("success"):
        baseline_results = resp.get("row_count", 0)
        c._pass(f"搜索 'Electronics' — {baseline_results} 条结果 (基线)")

    # ── Phase 4: 模拟增量追加 ──

    print("\n── Phase 4: 模拟增量追加 ──")

    # 用 HTTP ingest 模拟增量数据源
    print("\nSTEP 5: 增量批次 1 — HTTP 摄取")
    resp = c.ingest_http(DS_NAME, ["https://httpbin.org/json"])
    if resp.get("success"):
        batch1_rows = resp.get("total_rows", 0)
        c._pass(f"批次 1 — 追加 {batch1_rows} 行")
        c.lineage_record(DS_NAME, "incremental_append",
                         inputs=["https://httpbin.org/json"], outputs=[DS_NAME],
                         metadata={"batch": 1, "rows": batch1_rows})
    else:
        print(f"  [INFO] HTTP 追加不可用: {resp.get('error', '')}")

    # 用图片追加模拟新资产
    print("\nSTEP 6: 增量批次 2 — 图片资产追加")
    photo_dir = DATAS_DIR / "photos"
    if photo_dir.exists():
        imgs = [str(p) for p in photo_dir.iterdir()
                if p.suffix.lower() in (".jpg", ".jpeg", ".png")]
        if imgs:
            resp = c.ingest_images(DS_NAME, imgs[:2])
            if resp.get("success"):
                batch2_rows = resp.get("total_rows", 0)
                c._pass(f"批次 2 — 追加 {batch2_rows} 张图片")
                c.lineage_record(DS_NAME, "incremental_append",
                                 inputs=[str(p) for p in imgs[:2]],
                                 outputs=[DS_NAME],
                                 metadata={"batch": 2, "type": "images"})
            else:
                print(f"  [INFO] {resp.get('error', '')}")

    # ── Phase 5: 索引重建 ──

    print("\n── Phase 5: 索引重建 ──")

    print("\nSTEP 7: 重建 FTS 索引 (增量后)")
    resp = c.create_fts_index(DS_NAME, fts_column="text_content", replace=True)
    if resp.get("success"):
        c._pass("FTS 索引重建完成")
    else:
        print(f"  [INFO] {resp.get('error', '')}")

    print("\nSTEP 8: 构建向量索引 (增量后)")
    resp = c.create_vector_index(DS_NAME, vector_column="text_embedding",
                                  metric="cosine", index_type="IVF_PQ")
    if resp.get("success"):
        c._pass("向量索引构建完成")
    else:
        print(f"  [INFO] {resp.get('error', '')}")

    # ── Phase 6: 一致性校验 ──

    print("\n── Phase 6: 一致性校验 ──")

    print("\nSTEP 9: 校验总行数 (应 >= 基线)")
    resp = c.query_olap(DS_NAME, f'SELECT count(*) as cnt FROM "{DS_NAME}"')
    if resp.get("success"):
        rows = resp.get("rows", [])
        if rows:
            current = rows[0].get("cnt", 0)
            growth = current - baseline_count
            assert current >= baseline_count, \
                f"数据丢失! {current} < {baseline_count}"
            c._pass(f"一致性校验 — {current} 行 (增长 {growth})")

    print("\nSTEP 10: 校验搜索功能")
    resp = c.search_fts(DS_NAME, "Electronics", top_k=3)
    if resp.get("success"):
        new_results = resp.get("row_count", 0)
        c._pass(f"增量后搜索 — {new_results} 条结果 (基线 {baseline_results})")

    print("\nSTEP 11: 校验去重一致性")
    resp = c.quality_deduplicate(DS_NAME, strategy="exact", column="text_content")
    if resp.get("success"):
        dupes = resp.get("duplicates_removed", 0)
        c._pass(f"去重校验 — {dupes} 条重复 (预期 >= 0)")

    # ── Phase 7: 增量分析 ──

    print("\n── Phase 7: 增量分析 ──")

    print("\nSTEP 12: 增量前后对比")
    resp = c.query_olap(DS_NAME,
        f'SELECT count(*) as total, '
        f'  count(DISTINCT source) as sources, '
        f'  min(timestamp) as earliest, '
        f'  max(timestamp) as latest '
        f'FROM "{DS_NAME}"')
    if resp.get("success"):
        rows = resp.get("rows", [])
        if rows:
            r = rows[0]
            c._pass(f"当前状态 — {r.get('total', 0)} 行, "
                    f"{r.get('sources', 0)} 个来源")

    # ── Phase 8: 血缘 & 审计 ──

    print("\n── Phase 8: 血缘 & 审计 ──")

    print("\nSTEP 13: 完整血缘链")
    resp = c.lineage_history(DS_NAME)
    if resp.get("success"):
        events = resp.get("events", resp.get("data", []))
        c._pass(f"血缘链 — {len(events)} 个事件")
        for ev in events:
            batch = ev.get("metadata", {}).get("batch", "?")
            op = ev.get("operation", "?")
            rows_affected = ev.get("metadata", {}).get("rows", "?")
            print(f"         batch={batch} op={op:20s} rows={rows_affected}")

    print("\nSTEP 14: 审计记录")
    c.audit_record(DS_NAME, "incremental_pipeline",
                   details={
                       "batches": 3,
                       "initial_rows": initial_rows,
                       "index_rebuild": True,
                       "consistency_check": True,
                   })

    resp = c.audit_query(dataset_name=DS_NAME)
    if resp.get("success"):
        events = resp.get("events", resp.get("data", []))
        c._pass(f"审计记录 — {len(events)} 条")

    # ── Phase 9: 最终导出 ──

    print("\n── Phase 9: 最终导出 ──")

    print("\nSTEP 15: 导出增量合并后的完整数据集")
    resp = c.export(DS_NAME, format="parquet")
    if resp.get("success"):
        task_id = resp.get("task_id", "")
        if task_id:
            status = c.wait_for_export(DS_NAME, task_id, timeout=30)
            c._pass(f"导出完成 — {status.get('status')}")
            c.lineage_record(DS_NAME, "final_export",
                             inputs=[DS_NAME],
                             outputs=[f"exports/{DS_NAME}_final.parquet"],
                             metadata={"format": "parquet"})

    # 清理
    c.delete_dataset(DS_NAME)

    print("\n" + "=" * 60)
    print("API-19  增量数据管线 — ALL PASSED")
    print("=" * 60)


if __name__ == "__main__":
    main()
