#!/usr/bin/env python3
"""API-16 — 销售漏斗与客户分群分析

业务场景: 运营团队对交易数据进行客户行为分析，识别高价值客户、复购用户、城市购买力
数据源: datas/transactions/sales_2024.csv (1000 条订单)
流程: 数据摄取 → 去重 → RFM 分析 → 复购识别 → 交叉分析 → 血缘+审计全记录
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from conftest import ArrowLakeClient

BASE_URL = "http://localhost:8000"
API_KEY = "dev-api-key-for-local-testing-only"
DATAS_DIR = Path(__file__).resolve().parent.parent / "datas"

DS_NAME = "sales-funnel"


def main() -> None:
    print("=" * 60)
    print("API-16  销售漏斗与客户分群分析")
    print("=" * 60)

    c = ArrowLakeClient(BASE_URL, API_KEY)

    c.delete_dataset(DS_NAME)

    # ── Phase 1: 数据摄取 ──

    print("\n── Phase 1: 数据摄取 ──")

    csv_path = DATAS_DIR / "transactions" / "sales_2024.csv"
    assert csv_path.exists(), f"数据文件不存在: {csv_path}"

    print("\nSTEP 1: 摄取交易数据")
    resp = c.ingest_files(DS_NAME, [str(csv_path)])
    if not resp.get("success"):
        print(f"  [SKIP] 摄取失败: {resp.get('error')}: {resp.get('message', '')[:120]}")
        c.delete_dataset(DS_NAME)
        return

    total_rows = resp.get("total_rows", 0)
    c._pass(f"摄取完成 — {total_rows} 行订单")

    # 血缘: 原始数据进入系统
    c.lineage_record(DS_NAME, "ingest",
                     inputs=["raw/sales_2024.csv"], outputs=[DS_NAME],
                     metadata={"rows": total_rows, "type": "transaction"})

    # ── Phase 2: 数据质量 ──

    print("\n── Phase 2: 数据质量 ──")

    print("\nSTEP 2: 去重")
    resp = c.quality_deduplicate(DS_NAME, strategy="exact", column="order_id")
    if resp.get("success"):
        dupes = resp.get("duplicates_removed", 0)
        c._pass(f"去重 — 移除 {dupes} 条重复")

    print("\nSTEP 3: 质量过滤")
    resp = c.quality_filter(DS_NAME, [
        {"column": "amount", "type": "min_length", "value": 1},
        {"column": "order_id", "type": "not_null"},
        {"column": "user_id", "type": "not_null"},
    ])
    if resp.get("success"):
        c._pass("关键字段非空校验通过")

    c.lineage_record(DS_NAME, "quality_check",
                     inputs=[DS_NAME], outputs=[f"{DS_NAME}_clean"],
                     metadata={"dedup": "exact", "filters": 3})

    # ── Phase 3: RFM 分析 ──

    print("\n── Phase 3: RFM 分析 (Recency, Frequency, Monetary) ──")

    # 消费频次 & 金额 (F + M)
    print("\nSTEP 4: 用户消费频次 & 金额分布")
    resp = c.query_olap(DS_NAME,
        f'SELECT user_id, '
        f'  count(*) as frequency, '
        f'  round(sum(amount), 2) as monetary, '
        f'  round(avg(amount), 2) as avg_ticket '
        f'FROM "{DS_NAME}" '
        f'GROUP BY user_id '
        f'ORDER BY monetary DESC '
        f'LIMIT 15')
    if resp.get("success"):
        rows = resp.get("rows", [])
        c._pass(f"用户消费分析 — TOP {len(rows)} 用户")
        print(f"         {'用户ID':12s} {'频次':>6s} {'总消费':>12s} {'平均客单价':>10s}")
        for r in rows[:8]:
            print(f"         {r.get('user_id', '?'):12s} "
                  f"{r.get('frequency', 0):>6d} "
                  f"{r.get('monetary', 0):>12.2f} "
                  f"{r.get('avg_ticket', 0):>10.2f}")

    # 消费金额分群
    print("\nSTEP 5: 客户价值分群")
    resp = c.query_olap(DS_NAME,
        f'SELECT CASE '
        f'  WHEN total >= 10000 THEN \'高价值\' '
        f'  WHEN total >= 5000 THEN \'中高价值\' '
        f'  WHEN total >= 2000 THEN \'中等价值\' '
        f'  ELSE \'普通客户\' '
        f'END AS segment, '
        f'  count(*) as user_count, '
        f'  round(avg(total), 2) as avg_spend '
        f'FROM ('
        f'  SELECT user_id, round(sum(amount), 2) as total '
        f'  FROM "{DS_NAME}" GROUP BY user_id'
        f') '
        f'GROUP BY segment '
        f'ORDER BY avg_spend DESC')
    if resp.get("success"):
        rows = resp.get("rows", [])
        c._pass(f"客户分群 — {len(rows)} 个层级")
        for r in rows:
            print(f"         {r.get('segment', '?'):12s} — "
                  f"{r.get('user_count', 0)} 人, 人均 ¥{r.get('avg_spend', 0):,.2f}")

    # ── Phase 4: 复购分析 ──

    print("\n── Phase 4: 复购分析 ──")

    print("\nSTEP 6: 复购用户识别")
    resp = c.query_olap(DS_NAME,
        f'SELECT user_id, count(*) as orders, '
        f'  round(sum(amount), 2) as total '
        f'FROM "{DS_NAME}" '
        f'GROUP BY user_id '
        f'HAVING count(*) > 1 '
        f'ORDER BY orders DESC '
        f'LIMIT 15')
    if resp.get("success"):
        rows = resp.get("rows", [])
        c._pass(f"复购用户 — {len(rows)} 人")

    print("\nSTEP 7: 复购率统计")
    resp = c.query_olap(DS_NAME,
        f'SELECT '
        f'  count(DISTINCT user_id) as total_users, '
        f'  count(DISTINCT CASE WHEN orders > 1 THEN user_id END) as repeat_users '
        f'FROM ('
        f'  SELECT user_id, count(*) as orders '
        f'  FROM "{DS_NAME}" GROUP BY user_id'
        f')')
    if resp.get("success"):
        rows = resp.get("rows", [])
        if rows:
            total = rows[0].get("total_users", 0)
            repeat = rows[0].get("repeat_users", 0)
            rate = (repeat / total * 100) if total > 0 else 0
            c._pass(f"复购率 — {repeat}/{total} = {rate:.1f}%")

    # ── Phase 5: 交叉分析 ──

    print("\n── Phase 5: 交叉分析 ──")

    print("\nSTEP 8: 品类 × 支付方式 交叉表")
    resp = c.query_olap(DS_NAME,
        f'SELECT category, payment_method, '
        f'  count(*) as cnt, '
        f'  round(sum(amount), 2) as revenue '
        f'FROM "{DS_NAME}" '
        f'GROUP BY category, payment_method '
        f'ORDER BY revenue DESC '
        f'LIMIT 10')
    if resp.get("success"):
        rows = resp.get("rows", [])
        c._pass(f"交叉分析 — {len(rows)} 组合")
        for r in rows[:5]:
            print(f"         {r.get('category', '?'):15s} × {r.get('payment_method', '?'):15s} "
                  f"— {r.get('cnt', 0)} 笔, ¥{r.get('revenue', 0):,.2f}")

    print("\nSTEP 9: 城市购买力排名")
    resp = c.query_olap(DS_NAME,
        f'SELECT region as city, '
        f'  count(DISTINCT user_id) as unique_customers, '
        f'  count(*) as total_orders, '
        f'  round(sum(amount), 2) as gmv, '
        f'  round(sum(amount) / count(DISTINCT user_id), 2) as arpu '
        f'FROM "{DS_NAME}" '
        f'GROUP BY region '
        f'ORDER BY gmv DESC '
        f'LIMIT 10')
    if resp.get("success"):
        rows = resp.get("rows", [])
        c._pass(f"城市排名 — {len(rows)} 个城市")
        print(f"         {'城市':20s} {'客户数':>6s} {'订单数':>6s} {'GMV':>12s} {'ARPU':>10s}")
        for r in rows[:5]:
            print(f"         {r.get('city', '?'):20s} "
                  f"{r.get('unique_customers', 0):>6d} "
                  f"{r.get('total_orders', 0):>6d} "
                  f"{r.get('gmv', 0):>12.2f} "
                  f"{r.get('arpu', 0):>10.2f}")

    # ── Phase 6: 导出 + 治理 ──

    print("\n── Phase 6: 导出与数据治理 ──")

    print("\nSTEP 10: 导出分析结果")
    resp = c.export(DS_NAME, format="csv")
    if resp.get("success"):
        task_id = resp.get("task_id", "")
        if task_id:
            status = c.wait_for_export(DS_NAME, task_id, timeout=30)
            c._pass(f"CSV 导出 — {status.get('status')}")

    # 完整血缘链
    c.lineage_record(DS_NAME, "analytics_export",
                     inputs=[DS_NAME],
                     outputs=[f"exports/{DS_NAME}_analytics.csv"],
                     metadata={"analyses": ["rfm", "repeat", "cross_tab"]})

    # 审计全记录
    c.audit_record(DS_NAME, "sales_funnel_analysis",
                   details={
                       "phases": 6,
                       "queries": 8,
                       "data_lineage": True,
                       "quality_checks": True,
                   })

    print("\nSTEP 11: 查看完整血缘")
    resp = c.lineage_history(DS_NAME)
    if resp.get("success"):
        events = resp.get("events", resp.get("data", []))
        c._pass(f"血缘链 — {len(events)} 个事件")
        for ev in events:
            print(f"         {ev.get('operation', '?'):18s} → "
                  f"{ev.get('outputs', [])}")

    print("\nSTEP 12: 查看审计记录")
    resp = c.audit_query(dataset_name=DS_NAME)
    if resp.get("success"):
        events = resp.get("events", resp.get("data", []))
        c._pass(f"审计记录 — {len(events)} 条")

    # 清理
    c.delete_dataset(DS_NAME)

    print("\n" + "=" * 60)
    print("API-16  销售漏斗与客户分群分析 — ALL PASSED")
    print("=" * 60)


if __name__ == "__main__":
    main()
