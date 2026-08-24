#!/usr/bin/env python3
"""MS1 DoD① — 本体门禁基线报告(缺陷逃逸对比口径)。

对存量数据集的 KG 快照(KA dump)跑 validator 两次:
  (a) 仅必填/类型 — 形式化前的等价能力(``ontology:`` 段剥除后的降级 spec);
  (b) 全量 shapes — 含枚举 + type-pairs(F1.6 形式化后的完整约束)。

输出:违规数 / 类别分布 / **逃逸率**(口径 a 放行但口径 b 拒的违规)对比表
—— 即"门禁之前漏掉、门禁之后能抓"的量化度量,是切 enforce 的决策依据。

数据源(容器内跑):``/data/lake/ka/{ds}/ka/``
* ``map_reduce.json``(map_reduce 路径)— 跨 chunk 合并实体/关系;
* ``data.json``(dataset 路径)— hyperextract dump,nodes/edges 直读。

用法:
  docker exec arrow-lake-api-1 python3 /app/scripts/ontology_gate_baseline.py \
      --dataset czxm_lifeline
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parent.parent


def _default_templates_dir() -> Path:
    """模板目录探测:repo 布局(scripts/ 在仓库内)→ 容器挂载布局(/app)。"""
    candidates = [
        _REPO_ROOT / "arrow_lake/knowledge_graph/templates",
        Path("/app/arrow_lake/knowledge_graph/templates"),
    ]
    for c in candidates:
        if c.is_dir():
            return c
    return candidates[0]


# ---------------------------------------------------------------------------
# dump 加载
# ---------------------------------------------------------------------------


def load_ka_rows(ka_dir: Path) -> tuple[list[dict], list[dict], str | None]:
    """KA dump → (entities, relations, template_stem)。优先 map_reduce,回退 data.json。"""
    mr = ka_dir / "map_reduce.json"
    if mr.is_file():
        d = json.loads(mr.read_text(encoding="utf-8"))
        entities: dict[str, dict] = {}
        relations: list[dict] = []
        for cd in (d.get("chunks") or {}).values():
            for e in cd.get("entities") or []:
                name = str(e.get("name") or "")
                if not name:
                    continue
                row = entities.setdefault(name, {"name": name, "type": str(e.get("type") or "")})
                props = dict(e.get("properties") or [])
                definition = str(props.get("definition") or "")
                if definition and not row.get("definition"):
                    row["definition"] = definition  # 任一 chunk 有定义即采纳
            for r in cd.get("relations") or []:
                relations.append({
                    "source": str(r.get("source") or ""),
                    "target": str(r.get("target") or ""),
                    "type": str(r.get("type") or ""),
                    **({"description": str(dict(r.get("properties") or {}).get("description") or "")}
                       if dict(r.get("properties") or {}).get("description") else {}),
                })
        return list(entities.values()), relations, d.get("template")

    data = ka_dir / "data.json"
    if data.is_file():
        d = json.loads(data.read_text(encoding="utf-8"))
        entities = [
            {"name": str(n.get("name") or ""), "type": str(n.get("type") or ""),
             **({"definition": str(n["definition"])} if n.get("definition") else {})}
            for n in (d.get("nodes") or [])
        ]
        relations = [
            {"source": str(e.get("source") or ""), "target": str(e.get("target") or ""),
             "type": str(e.get("type") or ""),
             **({"description": str(e["description"])} if e.get("description") else {})}
            for e in (d.get("edges") or [])
        ]
        meta = ka_dir / "metadata.json"
        tpl = None
        if meta.is_file():
            try:
                tpl = json.loads(meta.read_text(encoding="utf-8")).get("template")
            except (ValueError, OSError):
                pass
        return entities, relations, tpl
    return [], [], None


# ---------------------------------------------------------------------------
# 两口径校验
# ---------------------------------------------------------------------------


def _categorize(v: Any) -> str:
    # warn 级先判(它从不计 reject;若后判会被 required 消息吞掉)
    if v.level == "warn":
        return "warn"
    if "not allowed" in v.message:
        return "type_pair"
    if "Less than" in v.message or "minCount" in v.message:
        return "required"
    if v.path == "type":
        return "enum"
    return "other"


def _viol_key(v: Any) -> tuple[str, str, str]:
    return (v.focus, v.path, v.value)


def run_baseline(dataset: str, ka_base: Path, templates_dir: Path) -> dict[str, Any]:
    """两口径跑一份存量快照,产出对比报告 dict。"""
    import yaml

    from arrow_lake.ontology.shape_builder import build_shapes
    from arrow_lake.ontology.template_adapter import adapt_template
    from arrow_lake.ontology.validator import validate_snapshot

    entities, relations, template_stem = load_ka_rows(ka_base / dataset / "ka")
    if not entities and not relations:
        return {"dataset": dataset, "error": f"no KA dump rows under {ka_base / dataset / 'ka'}"}

    template_path = None
    if template_stem:
        cand = templates_dir / f"{Path(str(template_stem)).stem}.yaml"
        if cand.is_file():
            template_path = cand

    if template_path is None:
        return {
            "dataset": dataset, "entities": len(entities), "relations": len(relations),
            "template": template_stem, "has_ontology_section": False,
            "note": "模板不可解析/不在项目模板目录 —— 两口径等价,无枚举可咬",
            "violations": {"a_required_only": {}, "b_full": {}},
            "rejects": {"a": 0, "b": 0},
            "escape": {"count": 0, "rate": 0.0, "samples": []},
        }

    tpl = yaml.safe_load(template_path.read_text(encoding="utf-8"))
    spec_full = adapt_template(tpl)
    spec_degraded = adapt_template({k: v for k, v in tpl.items() if k != "ontology"})

    res_a = validate_snapshot(entities, relations, build_shapes(spec_degraded))
    res_b = validate_snapshot(entities, relations, build_shapes(spec_full))

    cat_a = Counter(_categorize(v) for v in res_a)
    cat_b = Counter(_categorize(v) for v in res_b)
    keys_a = {_viol_key(v) for v in res_a if v.level == "reject"}
    keys_b = {_viol_key(v) for v in res_b if v.level == "reject"}
    escaped = [v for v in res_b if v.level == "reject" and _viol_key(v) not in keys_a]
    total_rows = len(entities) + len(relations)

    return {
        "dataset": dataset,
        "template": spec_full.template_name,
        "has_ontology_section": bool(spec_full.entity_type_enum or spec_full.type_pairs),
        "entities": len(entities),
        "relations": len(relations),
        "violations": {
            "a_required_only": dict(cat_a),
            "b_full": dict(cat_b),
        },
        "rejects": {"a": len(keys_a), "b": len(keys_b)},
        "escape": {
            # 逃逸率 = 口径 a 放行、口径 b 拒的 reject 违规占比(以行为分母)
            "count": len(escaped),
            "rate": round(len(escaped) / max(total_rows, 1), 4),
            "samples": [
                {"focus": v.focus, "path": v.path, "value": v.value, "message": v.message}
                for v in escaped[:10]
            ],
        },
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="本体门禁基线报告(两口径缺陷逃逸对比)")
    ap.add_argument("--dataset", required=True)
    ap.add_argument("--ka-base", default="/data/lake/ka")
    ap.add_argument("--templates-dir", default=str(_default_templates_dir()))
    ap.add_argument("--json", default=None, help="同时把报告写到此 JSON 文件")
    args = ap.parse_args(argv)

    report = run_baseline(args.dataset, Path(args.ka_base), Path(args.templates_dir))

    if "error" in report:
        print(f"ERROR: {report['error']}", file=sys.stderr)
        return 1

    print(f"\n=== 本体门禁基线 · {report['dataset']} ===")
    print(f"模板: {report['template']}  ontology 段: "
          f"{'有' if report['has_ontology_section'] else '无(两口径等价)'}")
    print(f"快照规模: entities={report['entities']} relations={report['relations']}")
    a = report["violations"]["a_required_only"]
    b = report["violations"]["b_full"]
    cats = sorted(set(a) | set(b))
    print(f"\n{'类别':<12}{'(a)仅必填/类型':>14}{'(b)全量shapes':>14}")
    for cat in cats:
        print(f"{cat:<12}{a.get(cat, 0):>14}{b.get(cat, 0):>14}")
    esc = report["escape"]
    print(f"\nreject: (a)={report['rejects']['a']}  (b)={report['rejects']['b']}")
    print(f"逃逸(a 放行 b 拒): {esc['count']} 条,行占比 {esc['rate']:.2%}")
    for s in esc["samples"][:5]:
        print(f"  - {s['focus']}.{s['path']} = {s['value'][:60]}")

    if args.json:
        Path(args.json).write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n报告已写 {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
