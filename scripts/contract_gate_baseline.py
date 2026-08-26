#!/usr/bin/env python3
"""W5.1 — 契约门禁基线报告(无契约 vs 有契约 两口径)。

对存量表格数据集(单表集或容器)离线评估契约编译行约束,产出切 enforce
的决策依据(reject 率,门槛 <10%,沿 MS1/本体门禁纪律):

  (a) 无契约 — 现状口径:契约约束为空,0 拦截;
  (b) 有契约 — 全量行约束(enum/range/pattern/not_null,OR 合并)。

报告含:逐约束违规计数与样本、warn 级 schema 提示(未知列/类型断言)、
引用完整性(**信息级** —— W3.1 门禁只接线行约束,引用未接线且跨时点
全量对账留 MS2,故不计入 reject 率)。

与摄入门禁的语义差:门禁只校验本次批次;本脚本离线扫**存量全量行**,
评估"若 enforce,既有数据里有多少行会被拒"——用于订正/重建决策。

⚠️ 内存警示(P1-10,review 2026-08-26):评估按**全量物化**读表 —— 大表
集(如 ontime 107M 行)必然 OOM。用 ``--max-rows N`` 闸截断扫描量,报告
会带 ``partial`` 标记(reject 率只代表前 N 行样本,不能直接作切 enforce
依据)。

⚠️ 跳过节(P1-10):契约节在目标数据集上无对应表(容器形契约打单表集,
或容器缺表)时标记 no_data/skipped —— **存在跳过节时 enforce_ready 恒为
false**(历史上这些节不计入决策,曾对未评估的契约亮绿灯)。

用法(容器,契约已入库):
  docker exec arrow-lake-api-1 python3 /app/scripts/contract_gate_baseline.py \
      --dataset gas_network --from-store [--max-rows 1000000]

用法(草案契约,宿主本地湖):
  python3 scripts/contract_gate_baseline.py --dataset ds --contract draft.yaml \
      --base-uri ./data
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

# enforce 切换门槛(计划 §5 W5.2:基线 reject 率 <10% 才切)
ENFORCE_THRESHOLD = 0.10


def _q(identifier: str) -> str:
    """Quote an identifier for DuckDB (embedded double quotes doubled)."""
    return '"' + identifier.replace('"', '""') + '"'


# ---------------------------------------------------------------------------
# 契约/存储装配(CLI 侧)
# ---------------------------------------------------------------------------


def _load_contract(dataset: str, contract_path: str | None, from_store: bool) -> str:
    if contract_path:
        return Path(contract_path).read_text(encoding="utf-8")

    from arrow_lake.config import ArrowLakeConfig

    cfg = ArrowLakeConfig()
    if not getattr(cfg.system_db, "enabled", False):
        raise SystemExit(
            "--from-store 需要 system_db 启用(容器内);宿主侧用 --contract <yaml>"
        )
    from arrow_lake.system_db import SystemDB
    from arrow_lake.system_db.stores import ContractStore

    db = SystemDB(
        cfg.system_db.url,
        auth_token=cfg.system_db.auth_token,
        connect_timeout_seconds=cfg.system_db.connect_timeout_seconds,
    )
    try:
        rec = ContractStore(db).get_version(dataset)
    finally:
        db.close()
    if rec is None:
        raise SystemExit(
            f"system_db 无 {dataset} 的契约;先 POST /api/v1/contracts 或用 --contract"
        )
    return rec["contract_yaml"]


def _make_storage(base_uri: str | None = None) -> Any:
    from arrow_lake.config import ArrowLakeConfig, StorageBackend, StorageConfig
    from arrow_lake.ingest.storage import LanceStorageManager

    if base_uri:  # host-side draft evaluation against a local lake
        return LanceStorageManager(
            StorageConfig(backend=StorageBackend.LOCAL, base_uri=base_uri))
    return LanceStorageManager(ArrowLakeConfig().storage)


# ---------------------------------------------------------------------------
# 核心评估
# ---------------------------------------------------------------------------


def _eval_section(
    con: Any, relation: str, section: str, tbl: Any, bundle: Any, contract: Any,
    sample_limit: int,
) -> dict[str, Any]:
    """Per-constraint counts + OR-combined reject over one table section."""
    rel = _q(relation)
    constraints = bundle.row_constraints(section)
    notes = contract.check_against_schema(section, tbl.schema)
    entries: list[dict[str, Any]] = []
    reject_exprs: list[str] = []
    for c in constraints:
        if c.column not in tbl.column_names:
            entries.append({
                "column": c.column, "kind": c.kind, "severity": c.severity.value,
                "violations": None, "rate": None, "samples": [],
                "error": "column absent from table",
            })
            continue
        expr = f"COALESCE(({c.sql}), FALSE)"  # 与 gate stage 4 同语义:NULL 放行
        n = con.execute(
            f"SELECT count(*) FROM {rel} WHERE {expr}"
        ).fetchone()[0]
        samples = [
            str(v[0]) for v in con.execute(
                f"SELECT {_q(c.column)} FROM {rel} WHERE {expr} LIMIT {sample_limit}"
            ).fetchall()
        ]
        entries.append({
            "column": c.column, "kind": c.kind, "severity": c.severity.value,
            "violations": n, "rate": round(n / max(tbl.num_rows, 1), 4),
            "samples": samples,
        })
        reject_exprs.append(expr)
    reject_rows = 0
    if reject_exprs and tbl.num_rows:
        reject_rows = con.execute(
            f"SELECT count(*) FROM {rel} WHERE {' OR '.join(reject_exprs)}"
        ).fetchone()[0]
    return {
        "rows": tbl.num_rows,
        "schema_notes": notes,
        "constraints": entries,
        "reject_rows": reject_rows,
        "reject_rate": round(reject_rows / max(tbl.num_rows, 1), 4),
    }


def _eval_references(
    con: Any, dataset: str, bundle: Any, loaded: dict[str, Any],
    mode: str, container_tables: list[str], storage: Any, sample_limit: int,
) -> list[dict[str, Any]]:
    """Informational referential-integrity check (NOT counted in reject rate)."""
    out: list[dict[str, Any]] = []
    registered: dict[str, bool] = {}

    def _register(relation: str, reader) -> bool:
        if relation not in registered:
            try:
                con.register(relation, reader())
                registered[relation] = True
            except Exception:  # noqa: BLE001 — target missing/unreadable → note
                registered[relation] = False
        return registered[relation]

    for ref in bundle.references:
        to_label = (
            f"{ref.to_dataset}.{ref.to_column}" if ref.to_dataset
            else f"{ref.to_table}.{ref.to_column}"
        )
        entry: dict[str, Any] = {
            "from": f"{ref.table}.{ref.from_column}",
            "to": to_label, "kind": "cross" if ref.to_dataset else "intra",
        }
        src = loaded.get(ref.table)
        if src is None:
            entry.update(note="source table not loaded in this run", violations=None)
            out.append(entry)
            continue
        if ref.from_column not in src.column_names:
            entry.update(note="fk column absent from source table", violations=None)
            out.append(entry)
            continue
        if ref.to_dataset:  # cross-container: target read bare (single-table set)
            relation = f"refd_{ref.to_dataset}"
            ok = _register(
                relation, lambda d=ref.to_dataset: storage.read_dataset(d))
            if not ok:
                entry.update(note="target dataset not readable", violations=None)
                out.append(entry)
                continue
        else:  # intra-container sibling table
            if mode != "container" or ref.to_table not in container_tables:
                entry.update(note="target table not available", violations=None)
                out.append(entry)
                continue
            relation = f"reft_{ref.to_table}"
            ok = _register(
                relation,
                lambda t=ref.to_table: storage.read_dataset(dataset, table=t))
            if not ok:
                entry.update(note="target table not readable", violations=None)
                out.append(entry)
                continue
        src_rel, tgt_rel = f"sec_{ref.table}", relation
        try:
            pred = ref.render(
                target_relation=_q(tgt_rel), source_alias="_src")
            n = con.execute(
                f"SELECT count(*) FROM {_q(src_rel)} AS _src WHERE {pred}"
            ).fetchone()[0]
            samples = [
                str(v[0]) for v in con.execute(
                    f"SELECT _src.{_q(ref.from_column)} FROM {_q(src_rel)} AS _src "
                    f"WHERE {pred} LIMIT {sample_limit}"
                ).fetchall()
            ]
            entry.update(violations=n, samples=samples)
        except Exception as exc:  # noqa: BLE001 — binder errors (target col missing…)
            entry.update(note=f"eval failed: {exc}", violations=None)
        out.append(entry)
    return out


def run_baseline(
    dataset: str, contract_yaml: str, storage: Any, *, sample_limit: int = 5,
    max_rows: int | None = None,
) -> dict[str, Any]:
    """Two-caliber offline evaluation of one contract against stored rows.

    ``max_rows`` (P1-10) caps each table's materialized scan — the report is
    then marked ``partial`` and its reject rate speaks only for the sampled
    prefix. Skipped sections (no matching table) block enforce_ready."""
    import duckdb

    from arrow_lake.contract.compiler import compile_contract
    from arrow_lake.contract.schema import parse_contract

    def _bounded(tbl: Any) -> Any:
        if max_rows is not None and tbl.num_rows > max_rows:
            return tbl.slice(0, max_rows)
        return tbl

    contract = parse_contract(contract_yaml)
    bundle = compile_contract(contract)
    container_tables = storage.list_container_tables(dataset)
    mode = "container" if container_tables else "single_table"

    con = duckdb.connect(":memory:")
    try:
        tables_report: dict[str, Any] = {}
        loaded: dict[str, Any] = {}

        if mode == "container":
            for name in contract.tables:
                if name not in container_tables:
                    tables_report[name] = {
                        "rows": None, "note": "no_data", "schema_notes": [],
                        "constraints": [], "reject_rows": 0, "reject_rate": 0.0,
                    }
                    continue
                tbl = _bounded(storage.read_dataset(dataset, table=name))
                relation = f"sec_{name}"
                con.register(relation, tbl)
                loaded[name] = tbl
                tables_report[name] = _eval_section(
                    con, relation, name, tbl, bundle, contract, sample_limit)
            uncontracted = sorted(set(container_tables) - set(contract.tables))
        else:
            try:
                tbl = _bounded(storage.read_dataset(dataset))
            except Exception as exc:  # noqa: BLE001 — missing dataset etc.
                return {"dataset": dataset, "error": f"cannot read dataset: {exc}"}
            # 门禁语义:单表集 section = 数据集名(legacy 自动包装的默认节)
            relation = f"sec_{dataset}"
            con.register(relation, tbl)
            loaded[dataset] = tbl
            tables_report[dataset] = _eval_section(
                con, relation, dataset, tbl, bundle, contract, sample_limit)
            for other in contract.tables:
                if other != dataset:
                    tables_report[other] = {
                        "rows": None, "note": "skipped_not_container",
                        "schema_notes": [], "constraints": [],
                        "reject_rows": 0, "reject_rate": 0.0,
                    }
            uncontracted = []

        refs_report = _eval_references(
            con, dataset, bundle, loaded, mode, container_tables,
            storage, sample_limit)
    finally:
        con.close()

    total_rows = sum(t["rows"] or 0 for t in tables_report.values())
    total_reject = sum(t["reject_rows"] for t in tables_report.values())
    rate = total_reject / max(total_rows, 1)
    # P1-10: sections with no matching table were silently excluded from the
    # decision — a container-shaped contract against a single-table set
    # produced enforce_ready=true for constraints that never ran. Skipped
    # sections now hard-block the green light.
    skipped = sum(
        1 for t in tables_report.values()
        if t.get("note") in ("no_data", "skipped_not_container")
    )
    partial = max_rows is not None
    return {
        "dataset": dataset,
        "mode": mode,
        "partial": partial,
        "max_rows": max_rows,
        "tables": tables_report,
        "uncontracted_tables": uncontracted,
        "references": refs_report,
        "totals": {"rows": total_rows, "reject_rows": total_reject},
        "calibers": {"a_no_contract": 0, "b_contract": total_reject},
        "decision": {
            "reject_rate": round(rate, 4),
            "threshold": ENFORCE_THRESHOLD,
            "skipped_sections": skipped,
            "enforce_ready": rate < ENFORCE_THRESHOLD and skipped == 0 and not partial,
            "note": (
                "引用完整性为信息级(门禁未接线引用约束),不计入 reject 率"
                + (f";{skipped} 个契约节无对应表未评估(enforce_ready 强制 false)"
                   if skipped else "")
                + (";样本截断口径(partial),不可直接作切 enforce 依据"
                   if partial else "")
            ),
        },
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _print_report(report: dict[str, Any]) -> None:
    mode_label = "容器" if report["mode"] == "container" else "单表"
    print(f"\n=== 契约门禁基线 · {report['dataset']}({mode_label})===")
    for name, t in report["tables"].items():
        if t.get("note"):
            print(f"\n表 {name}:{t['note']}")
            continue
        print(f"\n表 {name}({t['rows']} 行):")
        for n in t["schema_notes"]:
            print(f"  [warn] {n['kind']}: {n['message']}")
        for c in t["constraints"]:
            if c.get("error"):
                print(f"  {c['kind']}[{c['column']}]  ✗ {c['error']}")
                continue
            samples = ", ".join(c["samples"][:3])
            tail = f"  样本: {samples}" if samples else ""
            print(f"  {c['kind']}[{c['column']}]  违规 {c['violations']}"
                  f"({c['rate']:.2%}){tail}")
        print(f"  → reject(OR 合并){t['reject_rows']} 行({t['reject_rate']:.2%})")
    if report["uncontracted_tables"]:
        print(f"\n未覆盖表(存储有、契约无): {', '.join(report['uncontracted_tables'])}")
    if report["references"]:
        print("\n引用完整性(信息级,不计入 reject 率):")
        for r in report["references"]:
            if r.get("note"):
                print(f"  {r['from']} → {r['to']}: {r['note']}")
                continue
            samples = ", ".join(r.get("samples", [])[:3])
            tail = f"  样本: {samples}" if samples else ""
            print(f"  {r['from']} → {r['to']}: 缺失 {r['violations']}{tail}")
    d = report["decision"]
    print(f"\n两口径: (a)无契约 reject=0  (b)有契约 reject="
          f"{report['calibers']['b_contract']}/{report['totals']['rows']}")
    if report.get("partial"):
        print(f"⚠️ partial 样本口径(--max-rows={report.get('max_rows')})"
              f"——reject 率仅代表截断样本,不可直接作切 enforce 依据")
    if d.get("skipped_sections"):
        print(f"⚠️ {d['skipped_sections']} 个契约节无对应表未评估"
              f"——enforce_ready 强制 false(先对齐契约 scope 与数据集形态)")
    verdict = "✅ 可切 enforce" if d["enforce_ready"] else \
        "❌ 暂缓 enforce(先订正数据或放宽契约)"
    print(f"总 reject 率 {d['reject_rate']:.2%}(门槛 <{d['threshold']:.0%})"
          f" → {verdict}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="契约门禁基线报告(无契约 vs 有契约)")
    ap.add_argument("--dataset", required=True)
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--contract", help="契约 YAML 文件(草案)")
    src.add_argument("--from-store", action="store_true",
                     help="从 system_db 契约库取最新版(容器内)")
    ap.add_argument("--json", default=None, help="同时把报告写到此 JSON 文件")
    ap.add_argument("--base-uri", default=None,
                    help="本地湖根目录(宿主侧草案评估;缺省用容器/env 配置)")
    ap.add_argument("--sample-limit", type=int, default=5)
    ap.add_argument("--max-rows", type=int, default=None,
                    help="每表物化行数上限(大表防 OOM;设后报告为 partial 样本口径)")
    args = ap.parse_args(argv)

    contract_yaml = _load_contract(args.dataset, args.contract, args.from_store)
    report = run_baseline(
        args.dataset, contract_yaml, _make_storage(args.base_uri),
        sample_limit=args.sample_limit, max_rows=args.max_rows)

    if "error" in report:
        print(f"ERROR: {report['error']}", file=sys.stderr)
        return 1

    _print_report(report)
    if args.json:
        Path(args.json).write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n报告已写 {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
