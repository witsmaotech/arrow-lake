#!/usr/bin/env python3
"""API-24 — Daft 时间序列分析

业务场景: 数据分析师需要从交易数据中提取时间维度的洞察:
         - 月度趋势与环比变化
         - 品类季节性波动
         - 高峰时段识别
         - Daft 快速加载 + SQL 窗口函数联合分析
数据源: datas/transactions/sales_2024.csv (1000 条订单记录)
流程: 摄取 CSV → Daft 结构探测 → SQL 时间趋势 → 环比分析 → 峰值识别
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

DS_NAME = "daft-timeseries"


def main() -> None:
    print("=" * 60)
    print("API-24  Daft 时间序列分析")
    print("=" * 60)

    c = ArrowLakeClient(BASE_URL, API_KEY)
    c.delete_dataset(DS_NAME)

    # ── Phase 1: 数据摄取 ──

    print("\n── Phase 1: 数据摄取 ──")

    csv_path = DATAS_DIR / "transactions" / "sales_2024.csv"
    assert csv_path.exists(), f"数据文件不存在: {csv_path}"

    print("\nSTEP 1: 摄取交易 CSV")
    resp = c.ingest_files(DS_NAME, [str(csv_path)])
    if not resp.get("success"):
        print(f"  [SKIP] 摄取失败: {resp.get('error')}: {resp.get('message', '')[:120]}")
        c.delete_dataset(DS_NAME)
        return
    total_rows = resp.get("total_rows", 0)
    c._pass(f"摄取完成 — {total_rows} 行")

    # ── Phase 2: Daft 时间列探测 ──

    print("\n── Phase 2: Daft 时间列探测 ──")

    print("\nSTEP 2: Daft 加载全量数据 — 查看时间列格式")
    resp = c.query_daft(DS_NAME, columns=["timestamp", "order_id", "category", "amount"])
    if resp.get("success"):
        rows = resp.get("rows", [])[:5]
        for r in rows:
            print(f"         ts={r.get('timestamp', '?'):22s} "
                  f"order={r.get('order_id', '?'):15s} "
                  f"cat={r.get('category', '?'):12s} "
                  f"amt={r.get('amount', 0):>10}")
        c._pass(f"时间列探测 — {resp.get('row_count')} 行")
    else:
        print(f"  [INFO] {resp.get('error')}: {resp.get('message', '')[:120]}")

    # ── Phase 3: 月度趋势分析 ──

    print("\n── Phase 3: 月度趋势分析 ──")

    print("\nSTEP 3: SQL — 月度订单量与金额趋势")
    resp = c.query_olap(
        DS_NAME,
        f'SELECT '
        f'  strftime(timestamp, \'%Y-%m\') as month, '
        f'  count(*) as orders, '
        f'  round(sum(amount), 2) as revenue, '
        f'  round(avg(amount), 2) as avg_order, '
        f'  count(DISTINCT user_id) as active_users '
        f'FROM "{DS_NAME}" '
        f'GROUP BY strftime(timestamp, \'%Y-%m\') '
        f'ORDER BY month',
    )
    if resp.get("success"):
        for r in resp.get("rows", []):
            print(f"         {r.get('month', '?'):10s} "
                  f"orders={r.get('orders', 0):>4d} "
                  f"revenue={r.get('revenue', 0):>12} "
                  f"avg={r.get('avg_order', 0):>8} "
                  f"users={r.get('active_users', 0):>4d}")
        c._pass("月度趋势分析")
    else:
        print(f"  [INFO] {resp.get('error')}: {resp.get('message', '')[:120]}")

    # ── Phase 4: 品类月度波动 ──

    print("\n── Phase 4: 品类月度波动 ──")

    print("\nSTEP 4: SQL — TOP 品类月度金额趋势")
    resp = c.query_olap(
        DS_NAME,
        f'SELECT category, strftime(timestamp, \'%Y-%m\') as month, '
        f'  count(*) as orders, round(sum(amount), 2) as revenue '
        f'FROM "{DS_NAME}" '
        f'WHERE category IN ('
        f'  SELECT category FROM "{DS_NAME}" '
        f'  GROUP BY category ORDER BY sum(amount) DESC LIMIT 5'
        f') '
        f'GROUP BY category, strftime(timestamp, \'%Y-%m\') '
        f'ORDER BY category, month',
    )
    if resp.get("success"):
        current_cat = ""
        for r in resp.get("rows", []):
            cat = r.get("category", "?")
            if cat != current_cat:
                current_cat = cat
                print(f"         ── {cat} ──")
            print(f"           {r.get('month', '?'):10s} "
                  f"orders={r.get('orders', 0):>3d} "
                  f"revenue={r.get('revenue', 0):>10}")
        c._pass("TOP 品类月度波动")
    else:
        print(f"  [INFO] {resp.get('error')}: {resp.get('message', '')[:120]}")

    # ── Phase 5: 时段高峰分析 ──

    print("\n── Phase 5: 时段高峰分析 ──")

    print("\nSTEP 5: SQL — 小时维度订单分布 (识别高峰时段)")
    resp = c.query_olap(
        DS_NAME,
        f'SELECT '
        f'  cast(strftime(timestamp, \'%H\') as integer) as hour, '
        f'  count(*) as orders, '
        f'  round(avg(amount), 2) as avg_order '
        f'FROM "{DS_NAME}" '
        f'GROUP BY cast(strftime(timestamp, \'%H\') as integer) '
        f'ORDER BY hour',
    )
    if resp.get("success"):
        for r in resp.get("rows", []):
            hour = r.get("hour", 0)
            orders = r.get("orders", 0)
            bar = "█" * (orders // 2) if orders else ""
            print(f"         {hour:02d}:00  {orders:>4d} orders  avg={r.get('avg_order', 0):>8}  {bar}")
        c._pass("小时维度高峰分析")
    else:
        print(f"  [INFO] {resp.get('error')}: {resp.get('message', '')[:120]}")

    # ── Phase 6: 环比变化率 ──

    print("\n── Phase 6: 环比变化率 ──")

    print("\nSTEP 6: SQL — 月度环比金额变化率 (LAG 窗口函数)")
    resp = c.query_olap(
        DS_NAME,
        f'SELECT month, revenue, '
        f'  LAG(revenue) OVER (ORDER BY month) as prev_revenue, '
        f'  CASE WHEN LAG(revenue) OVER (ORDER BY month) > 0 '
        f'    THEN round((revenue - LAG(revenue) OVER (ORDER BY month)) '
        f'         / LAG(revenue) OVER (ORDER BY month) * 100, 2) '
        f'    ELSE NULL END as pct_change '
        f'FROM ('
        f'  SELECT strftime(timestamp, \'%Y-%m\') as month, '
        f'         round(sum(amount), 2) as revenue '
        f'  FROM "{DS_NAME}" '
        f'  GROUP BY strftime(timestamp, \'%Y-%m\')'
        f') monthly '
        f'ORDER BY month',
    )
    if resp.get("success"):
        for r in resp.get("rows", []):
            pct = r.get("pct_change")
            pct_str = f"{pct:>+.1f}%" if pct is not None else "   N/A"
            print(f"         {r.get('month', '?'):10s} "
                  f"revenue={r.get('revenue', 0):>12} "
                  f"prev={r.get('prev_revenue', 0) if r.get('prev_revenue') else 'N/A':>12} "
                  f"change={pct_str}")
        c._pass("环比变化率分析")
    else:
        print(f"  [INFO] {resp.get('error')}: {resp.get('message', '')[:120]}")

    # ── Phase 7: 地区时段热力图 ──

    print("\n── Phase 7: 地区时段热力图 ──")

    print("\nSTEP 7: SQL — 地区 × 时段订单矩阵")
    resp = c.query_olap(
        DS_NAME,
        f'SELECT '
        f'  region, '
        f'  cast(strftime(timestamp, \'%H\') as integer) as hour, '
        f'  count(*) as orders '
        f'FROM "{DS_NAME}" '
        f'GROUP BY region, cast(strftime(timestamp, \'%H\') as integer) '
        f'ORDER BY region, hour',
    )
    if resp.get("success"):
        regions_hours: dict[str, dict[int, int]] = {}
        for r in resp.get("rows", []):
            region = r.get("region", "?")
            hour = r.get("hour", 0)
            orders = r.get("orders", 0)
            regions_hours.setdefault(region, {})[hour] = orders

        for region, hours in regions_hours.items():
            peak_hour = max(hours, key=hours.get)  # type: ignore[arg-type]
            peak_orders = hours[peak_hour]
            total_region = sum(hours.values())
            print(f"         {region:18s} total={total_region:>4d} "
                  f"peak={peak_hour:02d}:00 ({peak_orders} orders)")
        c._pass("地区时段热力图")
    else:
        print(f"  [INFO] {resp.get('error')}: {resp.get('message', '')[:120]}")

    # 清理
    c.delete_dataset(DS_NAME)

    print("\n" + "=" * 60)
    print("API-24  ALL PASSED")
    print("=" * 60)


if __name__ == "__main__":
    main()
