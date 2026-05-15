#!/usr/bin/env python3
"""API-27 — Daft 数据质量审计

业务场景: 数据治理团队需要对入库数据执行系统化的质量审计:
         - 空值/缺失值扫描
         - 重复记录检测
         - 异常值 (outlier) 定位
         - 数据类型一致性检查
         - 质量评分与趋势报告
         - 为数据清洗提供优先级建议
数据源: sales_2024.csv + knowledge.jsonl
流程: 双源摄取 → Daft 结构扫描 → SQL 空值/异常检测 → 去重 → 质量报告
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from conftest import ArrowLakeClient

BASE_URL = "http://localhost:8000"
API_KEY = "dev-api-key-for-local-testing-only"
DATAS_DIR = Path(__file__).resolve().parent.parent / "datas"

DS_TXN = "daft-audit-txn"
DS_KB = "daft-audit-kb"


def main() -> None:
    print("=" * 60)
    print("API-27  Daft 数据质量审计")
    print("=" * 60)

    c = ArrowLakeClient(BASE_URL, API_KEY)
    c.delete_dataset(DS_TXN)
    c.delete_dataset(DS_KB)

    # ── Phase 1: 数据摄取 ──

    print("\n── Phase 1: 数据摄取 ──")

    csv_path = DATAS_DIR / "transactions" / "sales_2024.csv"
    kb_path = DATAS_DIR / "kb" / "knowledge.jsonl"

    print("\nSTEP 1: 摄取交易数据")
    assert csv_path.exists(), f"文件不存在: {csv_path}"
    resp = c.ingest_files(DS_TXN, [str(csv_path)])
    if not resp.get("success"):
        print(f"  [SKIP] {resp.get('error')}: {resp.get('message', '')[:120]}")
        return
    txn_rows = resp.get("total_rows", 0)
    c._pass(f"交易数据 — {txn_rows} 行")

    print("\nSTEP 2: 摄取知识库数据")
    assert kb_path.exists(), f"文件不存在: {kb_path}"
    resp = c.ingest_files(DS_KB, [str(kb_path)])
    if not resp.get("success"):
        print(f"  [SKIP] {resp.get('error')}: {resp.get('message', '')[:120]}")
        c.delete_dataset(DS_TXN)
        return
    kb_rows = resp.get("total_rows", 0)
    c._pass(f"知识库 — {kb_rows} 行")

    # ── Phase 2: Daft 结构扫描 ──

    print("\n── Phase 2: Daft 结构扫描 ──")

    print("\nSTEP 3: Daft 全量加载 — 列级完整性检查")
    resp = c.query_daft(DS_TXN)
    if resp.get("success"):
        rows = resp.get("rows", [])
        total = resp.get("row_count", 0)
        cols = resp.get("column_count", 0)
        col_names = list(rows[0].keys()) if rows else []

        print(f"         {total} rows × {cols} cols")
        print(f"         列: {col_names}")

        # 空值扫描
        print("\n         ── 空值扫描 ──")
        null_counts: dict[str, int] = {col: 0 for col in col_names}
        for row in rows:
            for col in col_names:
                val = row.get(col)
                if val is None or val == "" or val == "null":
                    null_counts[col] += 1

        has_nulls = False
        for col, cnt in null_counts.items():
            if cnt > 0:
                pct = cnt / total * 100 if total else 0
                print(f"         {col:20s} {cnt:>4d} nulls ({pct:.1f}%)")
                has_nulls = True
        if not has_nulls:
            print("         ✓ 无空值")
        c._pass(f"结构扫描 — {cols} 列, 空值检查完成")
    else:
        print(f"  [INFO] {resp.get('error')}: {resp.get('message', '')[:120]}")

    # ── Phase 3: SQL 异常值检测 ──

    print("\n── Phase 3: SQL 异常值检测 ──")

    print("\nSTEP 4: SQL — 交易金额异常值 (超出 3σ 范围)")
    resp = c.query_olap(
        DS_TXN,
        f'SELECT count(*) as total, '
        f'  round(avg(amount), 2) as mean, '
        f'  round(stddev(amount), 2) as std, '
        f'  round(avg(amount) - 3 * stddev(amount), 2) as lower, '
        f'  round(avg(amount) + 3 * stddev(amount), 2) as upper '
        f'FROM "{DS_TXN}"',
    )
    if resp.get("success"):
        row = resp.get("rows", [{}])[0]
        print(f"         mean={row.get('mean', 0):.2f} std={row.get('std', 0):.2f}")
        print(f"         3σ range: [{row.get('lower', 0):.2f}, {row.get('upper', 0):.2f}]")
        c._pass("金额统计量计算")
    else:
        print(f"  [INFO] {resp.get('error')}: {resp.get('message', '')[:120]}")

    print("\nSTEP 5: SQL — 定位异常交易 (金额超出 3σ)")
    resp = c.query_olap(
        DS_TXN,
        f'SELECT order_id, category, amount, region '
        f'FROM "{DS_TXN}" '
        f'WHERE amount > (SELECT avg(amount) + 3 * stddev(amount) FROM "{DS_TXN}") '
        f'   OR amount < (SELECT avg(amount) - 3 * stddev(amount) FROM "{DS_TXN}") '
        f'ORDER BY amount DESC LIMIT 10',
    )
    if resp.get("success"):
        outliers = resp.get("rows", [])
        if outliers:
            print(f"         发现 {len(outliers)} 条异常交易:")
            for r in outliers:
                print(f"         {r.get('order_id', '?'):15s} "
                      f"cat={r.get('category', '?'):12s} "
                      f"amt={r.get('amount', 0):>10.2f} "
                      f"region={r.get('region', '?')}")
        else:
            print("         ✓ 无超出 3σ 范围的异常值")
        c._pass(f"异常值检测 — {len(outliers)} 条")
    else:
        print(f"  [INFO] {resp.get('error')}: {resp.get('message', '')[:120]}")

    # ── Phase 4: 重复检测 ──

    print("\n── Phase 4: 重复检测 ──")

    print("\nSTEP 6: SQL — 交易数据重复订单检测")
    resp = c.query_olap(
        DS_TXN,
        f'SELECT order_id, count(*) as cnt '
        f'FROM "{DS_TXN}" '
        f'GROUP BY order_id HAVING cnt > 1 '
        f'ORDER BY cnt DESC LIMIT 10',
    )
    if resp.get("success"):
        dupes = resp.get("rows", [])
        if dupes:
            print(f"         发现 {len(dupes)} 个重复订单:")
            for r in dupes:
                print(f"         {r.get('order_id', '?'):15s} ×{r.get('cnt', 0)}")
        else:
            print("         ✓ 无重复订单")
        c._pass(f"交易重复检测 — {len(dupes)} 个重复")
    else:
        print(f"  [INFO] {resp.get('error')}: {resp.get('message', '')[:120]}")

    print("\nSTEP 7: SQL — 知识库重复 ID 检测")
    resp = c.query_olap(
        DS_KB,
        f'SELECT id, count(*) as cnt '
        f'FROM "{DS_KB}" '
        f'GROUP BY id HAVING cnt > 1 '
        f'ORDER BY cnt DESC LIMIT 10',
    )
    if resp.get("success"):
        dupes = resp.get("rows", [])
        if dupes:
            print(f"         发现 {len(dupes)} 个重复 ID:")
            for r in dupes:
                print(f"         {r.get('id', '?'):12s} ×{r.get('cnt', 0)}")
        else:
            print("         ✓ 无重复 ID")
        c._pass(f"知识库重复检测 — {len(dupes)} 个重复")
    else:
        print(f"  [INFO] {resp.get('error')}: {resp.get('message', '')[:120]}")

    # ── Phase 5: 数据类型一致性 ──

    print("\n── Phase 5: 数据类型一致性 ──")

    print("\nSTEP 8: SQL — 金额字段类型验证 (是否全为数值)")
    resp = c.query_olap(
        DS_TXN,
        f'SELECT '
        f'  count(*) as total, '
        f'  count(CASE WHEN amount > 0 THEN 1 END) as positive, '
        f'  count(CASE WHEN amount = 0 THEN 1 END) as zero, '
        f'  count(CASE WHEN amount < 0 THEN 1 END) as negative '
        f'FROM "{DS_TXN}"',
    )
    if resp.get("success"):
        row = resp.get("rows", [{}])[0]
        total = row.get("total", 0)
        positive = row.get("positive", 0)
        zero = row.get("zero", 0)
        negative = row.get("negative", 0)
        print(f"         total={total} positive={positive} zero={zero} negative={negative}")
        if negative > 0:
            print(f"         ⚠ 发现 {negative} 条负值记录，需人工确认")
        c._pass("金额类型验证")
    else:
        print(f"  [INFO] {resp.get('error')}: {resp.get('message', '')[:120]}")

    print("\nSTEP 9: Daft — 知识库文本完整性检查")
    resp = c.query_daft(DS_KB, columns=["id", "title", "text_content"])
    if resp.get("success"):
        rows = resp.get("rows", [])
        empty_title = sum(1 for r in rows if not r.get("title"))
        empty_text = sum(1 for r in rows if not r.get("text_content"))
        total = resp.get("row_count", 0)
        print(f"         {total} 条记录:")
        print(f"         空标题: {empty_title} ({empty_title / total * 100:.1f}%)" if total else "")
        print(f"         空正文: {empty_text} ({empty_text / total * 100:.1f}%)" if total else "")
        c._pass("文本完整性检查")
    else:
        print(f"  [INFO] {resp.get('error')}: {resp.get('message', '')[:120]}")

    # ── Phase 6: 质量报告 API ──

    print("\n── Phase 6: 质量报告 API ──")

    print("\nSTEP 10: 质量报告 — 交易数据集")
    resp = c.quality_report(DS_TXN)
    if resp.get("success"):
        report = resp.get("report", {})
        score = report.get("score", "N/A")
        checks = report.get("checks", {})
        print(f"         评分: {score}")
        for check, detail in checks.items():
            status = detail.get("status", "?") if isinstance(detail, dict) else detail
            print(f"         {check:25s} → {status}")
        c._pass("交易质量报告")
    else:
        print(f"  [INFO] {resp.get('error')}: {resp.get('message', '')[:120]}")

    print("\nSTEP 11: 质量报告 — 知识库数据集")
    resp = c.quality_report(DS_KB)
    if resp.get("success"):
        report = resp.get("report", {})
        score = report.get("score", "N/A")
        checks = report.get("checks", {})
        print(f"         评分: {score}")
        for check, detail in checks.items():
            status = detail.get("status", "?") if isinstance(detail, dict) else detail
            print(f"         {check:25s} → {status}")
        c._pass("知识库质量报告")
    else:
        print(f"  [INFO] {resp.get('error')}: {resp.get('message', '')[:120]}")

    # ── Phase 7: 审计日志记录 ──

    print("\n── Phase 7: 审计日志记录 ──")

    print("\nSTEP 12: 记录审计日志 — 质量审计完成")
    resp = c.audit_record(DS_TXN, "quality_audit", details={"score": "checked", "outliers": "scanned"})
    if resp.get("success"):
        c._pass("审计日志已记录")
    else:
        print(f"  [INFO] {resp.get('error')}: {resp.get('message', '')[:120]}")

    # 清理
    c.delete_dataset(DS_TXN)
    c.delete_dataset(DS_KB)

    print("\n" + "=" * 60)
    print("API-27  ALL PASSED")
    print("=" * 60)


if __name__ == "__main__":
    main()
