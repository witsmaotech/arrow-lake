#!/usr/bin/env python3
"""MS3 三项度量基线(v1.11.2 W5.3/F3.6)——对运行中的栈只读采集。

① 研判准确率:黄金集重放(POST /decisions/assess × GOLDEN_EXPECTED,
   比对命中规则集合);
② 依据可溯率:行动审计(action.execute)中 included.assess.rule_ids
   非空的比例(ADMIN /audit/query);
③ 越权拦截计数:action.denied 审计条数(D3:403+action 维度走审计管道);
旁证:unruly 规则占比(assess 响应 unruly / 参与求值规则数)。

报告落 docs_offline/ms3-metrics-baseline-<date>.md。用法:
    python scripts/ms3_metrics_baseline.py --api http://127.0.0.1:8000 \
        --token <ADMIN_JWT> [--out docs_offline/...]
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
import urllib.request

from arrow_lake.testing import ms3_demo as demo


def _req(method: str, url: str, token: str, json_body=None) -> dict:
    data = json.dumps(json_body).encode() if json_body is not None else None
    req = urllib.request.Request(
        url, data=data, method=method,
        headers={"Authorization": f"Bearer {token}",
                 "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=120) as resp:
        body = resp.read()
        return json.loads(body) if body else {}


def _payload(entry: dict) -> dict:
    p = entry.get("payload")
    if isinstance(p, dict):
        return p
    if isinstance(p, (list, tuple)):
        try:
            return dict(p)
        except Exception:
            return {}
    return {}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--api", default="http://127.0.0.1:8000")
    ap.add_argument("--token", required=True, help="ADMIN JWT(audit query)")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()
    api = args.api.rstrip("/")
    tok = args.token
    ds = demo.DEMO_DATASET

    # ① 准确率:黄金集重放
    cases, correct, unruly_seen, rules_seen = [], 0, [], set()
    for oid, expected in sorted(demo.GOLDEN_EXPECTED.items()):
        a = _req("POST", f"{api}/api/v1/decisions/assess", tok, json_body={
            "dataset": ds, "object_type": "alerts", "object_id": oid})
        matched = {c["rule_id"] for c in a["conclusions"]}
        ok = matched == expected
        correct += ok
        unruly_seen.extend(a.get("unruly") or [])
        rules_seen |= matched | set(a.get("unruly") or [])
        cases.append((oid, sorted(matched), sorted(expected), ok))
    accuracy = correct / len(demo.GOLDEN_EXPECTED)

    # ②③ 可溯率 + 拦截计数(审计管道)
    exec_entries = _req(
        "GET", f"{api}/api/v1/audit/query?event_type=action.execute", tok)
    exec_list = exec_entries if isinstance(exec_entries, list) else \
        exec_entries.get("entries") or []
    traced = sum(
        1 for e in exec_list
        if (_payload(e).get("included") or {})
        .get("assess.rule_ids")  # type: ignore[union-attr]
    )
    traceability = traced / len(exec_list) if exec_list else float("nan")

    denied_entries = _req(
        "GET", f"{api}/api/v1/audit/query?event_type=action.denied", tok)
    denied_list = denied_entries if isinstance(denied_entries, list) else \
        denied_entries.get("entries") or []

    unruly_ratio = (len(set(unruly_seen)) / len(rules_seen)) if rules_seen else 0.0
    today = dt.date.today().isoformat()
    lines = [
        f"# MS3 度量基线报告({today})",
        "",
        f"目标栈:`{api}` · 数据集 `{ds}` · 黄金集 {len(demo.GOLDEN_EXPECTED)} 对。",
        "",
        "| 度量 | 定义 | 基线值 |",
        "|---|---|---|",
        f"| ① 研判准确率 | 黄金集重放命中集合一致比例 | **{accuracy:.0%}**"
        f"({correct}/{len(demo.GOLDEN_EXPECTED)}) |",
        f"| ② 依据可溯率 | action.execute 审计含 rule_ids 比例 | "
        f"**{traceability:.0%}**({traced}/{len(exec_list)}) |",
        f"| ③ 越权拦截计数 | action.denied 审计条数 | "
        f"**{len(denied_list)}** |",
        f"| 旁证:unruly 占比 | unruly 规则/参与求值规则 | "
        f"{unruly_ratio:.0%}({sorted(set(unruly_seen))}) |",
        "",
        "## 黄金集明细",
        "",
        "| 对象 | 实际命中 | 期望 | 判定 |",
        "|---|---|---|---|",
    ]
    for oid, got, exp, ok in cases:
        lines.append(f"| {oid} | {', '.join(got) or '—'} | "
                     f"{', '.join(exp) or '—'} | {'✅' if ok else '❌'} |")
    report = "\n".join(lines) + "\n"

    out = args.out or f"docs_offline/ms3-metrics-baseline-{today}.md"
    with open(out, "w", encoding="utf-8") as f:
        f.write(report)
    print(report)
    print(f"report -> {out}")
    return 0 if correct == len(demo.GOLDEN_EXPECTED) else 1


if __name__ == "__main__":
    sys.exit(main())
