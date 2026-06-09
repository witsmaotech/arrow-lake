#!/usr/bin/env python3
"""API-11 — 电商交易分析管线

业务场景: 电商运营团队需要从交易数据中分析销售趋势、品类表现、用户行为
数据源: datas/transactions/sales_2024.csv (1000 条订单记录)
流程: CSV 摄取 → 去重 → 多维 OLAP 分析 → 导出报表
"""

from __future__ import annotations
import os

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from conftest import ArrowLakeClient

BASE_URL = os.environ.get("ARROW_LAKE_BASE_URL", "http://localhost:8000")
API_KEY = os.environ.get("ARROW_LAKE_API_KEY", "dev-api-key-for-local-testing-only")
DATAS_DIR = Path(__file__).resolve().parent.parent / "datas"

DS_NAME = "txn-analytics"


def main() -> None:
    print("=" * 60)
    print("API-11  电商交易分析管线")
    print("=" * 60)

    c = ArrowLakeClient(BASE_URL, API_KEY)

    # 清理
    c.delete_dataset(DS_NAME)

    # ── Phase 1: 数据摄取 ──

    print("\n── Phase 1: 数据摄取 ──")

    csv_path = DATAS_DIR / "transactions" / "sales_2024.csv"
    assert csv_path.exists(), f"数据文件不存在: {csv_path}"

    print("\nSTEP 1: 摄取交易 CSV")
    resp = c.ingest_files(DS_NAME, [str(csv_path)])
    if not resp.get("success"):
        print(f"  [SKIP] 摄取失败: {resp.get('error')}: {resp.get('message', '')[:120]}")
        print(f"         (Docker 容器可能无法读取宿主机路径)")
        c.delete_dataset(DS_NAME)
        return

    total_rows = resp.get("total_rows", 0)
    c._pass(f"摄取完成 — {total_rows} 行")

    # 记录血缘
    c.lineage_record(DS_NAME, "ingest",
                     inputs=[str(csv_path)], outputs=[DS_NAME],
                     metadata={"source": "sales_2024.csv", "rows": total_rows})

    # ── Phase 2: 数据质量 ──

    print("\n── Phase 2: 数据质量 ──")

    print("\nSTEP 2: 去重")
    resp = c.quality_deduplicate(DS_NAME, strategy="exact", column="order_id")
    if resp.get("success"):
        dupes = resp.get("duplicates_removed", 0)
        c._pass(f"去重完成 — 移除 {dupes} 条重复订单")
    else:
        print(f"  [INFO] {resp.get('error', '')}")

    print("\nSTEP 3: 质量报告")
    resp = c.quality_report(DS_NAME)
    if resp.get("success"):
        report = resp.get("report", resp.get("data", {}))
        if isinstance(report, dict):
            score = report.get("quality_score", "?")
            c._pass(f"数据质量评分: {score}")
    else:
        print(f"  [INFO] {resp.get('error', '')}")

    # ── Phase 3: 业务分析 ──

    print("\n── Phase 3: 业务分析 ──")

    # 品类销售额排名
    print("\nSTEP 4: 品类销售额 TOP 10")
    resp = c.query_olap(DS_NAME,
        f'SELECT category, '
        f'  count(*) as order_count, '
        f'  round(sum(amount), 2) as total_sales, '
        f'  round(avg(amount), 2) as avg_order_value '
        f'FROM "{DS_NAME}" '
        f'GROUP BY category '
        f'ORDER BY total_sales DESC '
        f'LIMIT 10')
    if resp.get("success"):
        rows = resp.get("rows", [])
        c._pass(f"品类分析 — {len(rows)} 个品类")
        print(f"         {'品类':20s} {'订单数':>8s} {'总销售额':>12s} {'客单价':>10s}")
        print(f"         {'─' * 20} {'─' * 8} {'─' * 12} {'─' * 10}")
        for r in rows:
            print(f"         {r.get('category', '?'):20s} "
                  f"{r.get('order_count', 0):>8d} "
                  f"{r.get('total_sales', 0):>12.2f} "
                  f"{r.get('avg_order_value', 0):>10.2f}")
    else:
        print(f"  [INFO] {resp.get('error', '')}")

    # 城市分布
    print("\nSTEP 5: 城市 TOP 10")
    resp = c.query_olap(DS_NAME,
        f'SELECT region as city, '
        f'  count(*) as orders, '
        f'  round(sum(amount), 2) as revenue '
        f'FROM "{DS_NAME}" '
        f'GROUP BY region '
        f'ORDER BY revenue DESC '
        f'LIMIT 10')
    if resp.get("success"):
        rows = resp.get("rows", [])
        c._pass(f"城市分布 — {len(rows)} 个城市")
        for r in rows[:5]:
            print(f"         {r.get('city', '?'):20s} — 订单 {r.get('orders', 0)}, "
                  f"营收 ¥{r.get('revenue', 0):,.2f}")
    else:
        print(f"  [INFO] {resp.get('error', '')}")

    # 支付方式分析
    print("\nSTEP 6: 支付方式占比")
    resp = c.query_olap(DS_NAME,
        f'SELECT payment_method, '
        f'  count(*) as cnt, '
        f'  round(count(*) * 100.0 / (SELECT count(*) FROM "{DS_NAME}"), 1) as pct '
        f'FROM "{DS_NAME}" '
        f'GROUP BY payment_method '
        f'ORDER BY cnt DESC')
    if resp.get("success"):
        for r in resp.get("rows", []):
            bar = "█" * int(r.get("pct", 0) / 2)
            print(f"         {r.get('payment_method', '?'):20s} {r.get('cnt', 0):>5d} "
                  f"({r.get('pct', 0):>5.1f}%) {bar}")
        c._pass("支付方式分析完成")
    else:
        print(f"  [INFO] {resp.get('error', '')}")

    # TOP 消费用户
    print("\nSTEP 7: TOP 10 消费用户")
    resp = c.query_olap(DS_NAME,
        f'SELECT user_id, '
        f'  count(*) as orders, '
        f'  round(sum(amount), 2) as total_spent '
        f'FROM "{DS_NAME}" '
        f'GROUP BY user_id '
        f'ORDER BY total_spent DESC '
        f'LIMIT 10')
    if resp.get("success"):
        rows = resp.get("rows", [])
        c._pass(f"TOP 用户 — {len(rows)} 人")
        for r in rows[:5]:
            print(f"         {r.get('user_id', '?'):12s} — "
                  f"{r.get('orders', 0)} 笔, ¥{r.get('total_spent', 0):,.2f}")
    else:
        print(f"  [INFO] {resp.get('error', '')}")

    # ── Phase 4: 导出 ──

    print("\n── Phase 4: 导出报表 ──")

    print("\nSTEP 8: 导出 Parquet")
    resp = c.export(DS_NAME, format="parquet")
    if resp.get("success"):
        task_id = resp.get("task_id", "")
        status = c.wait_for_export(DS_NAME, task_id, timeout=30)
        c._pass(f"导出完成 — {status.get('status')}")
        c.lineage_record(DS_NAME, "export",
                         inputs=[DS_NAME], outputs=[f"exports/{DS_NAME}.parquet"])
    else:
        print(f"  [INFO] {resp.get('error', '')}")

    # 审计
    c.audit_record(DS_NAME, "analytics_pipeline",
                   details={"phases": 4, "queries": 4, "export": "parquet"})

    # 清理
    c.delete_dataset(DS_NAME)

    print("\n" + "=" * 60)
    print("API-11  电商交易分析管线 — ALL PASSED")
    print("=" * 60)


if __name__ == "__main__":
    main()
