"""W3 — 基线脚本核心逻辑(两口径 + 逃逸)用合成 KA dump 钉住。"""

from __future__ import annotations

import json
from pathlib import Path

import yaml

_PCG = Path(__file__).resolve().parents[3] / \
    "arrow_lake/knowledge_graph/templates/project_concept_graph.yaml"


def _make_dump(ka_dir: Path) -> None:
    ka_dir.mkdir(parents=True, exist_ok=True)
    (ka_dir / "map_reduce.json").write_text(json.dumps({
        "template": "project_concept_graph",
        "template_hash": "x",
        "chunks": {
            "c1": {
                "hash": "h1",
                "entities": [
                    {"name": "合规主体A", "type": "主体",
                     "properties": [["definition", "采购方"]]},
                    {"name": "越界实体", "type": "机器人",
                     "properties": [["definition", "d"]]},
                    {"name": "缺定义", "type": "软件", "properties": []},
                ],
                "relations": [
                    {"source": "合规主体A", "target": "缺定义", "type": "提供",
                     "properties": [["description", "d"]]},
                    {"source": "缺定义", "target": "合规主体A", "type": "训练",
                     "properties": [["description", "d"]]},
                ],
            },
        },
    }, ensure_ascii=False), encoding="utf-8")


def test_baseline_escape_counts(tmp_path: Path) -> None:
    from scripts.ontology_gate_baseline import run_baseline

    ka_base = tmp_path / "ka"
    _make_dump(ka_base / "ds_test" / "ka")
    report = run_baseline("ds_test", ka_base, _PCG.parent)

    assert report["has_ontology_section"] is True
    assert report["entities"] == 3 and report["relations"] == 2
    # (a) 仅必填/类型(剥掉整个 ontology: 段,含 warn_fields —— 形式化前无
    # warn 降级):缺定义 1 条 reject
    assert report["rejects"]["a"] == 1
    # (b) 全量:越枚举(机器人)1 + 非法配对(软件—训练→主体)1;缺定义降为 warn
    assert report["rejects"]["b"] == 2
    a = report["violations"]["a_required_only"]
    b = report["violations"]["b_full"]
    assert a.get("required", 0) == 1
    assert b.get("enum", 0) >= 1
    assert b.get("type_pair", 0) >= 1
    assert b.get("warn", 0) >= 1  # 缺定义在 (b) 是 warn 不是 reject
    # 逃逸 = a 放行 b 拒 = 2(枚举 + 配对)
    assert report["escape"]["count"] == 2
    assert report["escape"]["rate"] > 0


def test_baseline_no_ontology_template(tmp_path: Path) -> None:
    """无 ontology 段的模板(entity_graph)→ 两口径等价,如实标注。"""
    from scripts.ontology_gate_baseline import load_ka_rows, run_baseline

    ka_base = tmp_path / "ka"
    _make_dump(ka_base / "ds_free" / "ka")
    # 造一个无段的假模板
    tpl = yaml.safe_load(_PCG.read_text(encoding="utf-8"))
    tpl["name"] = "free_graph"
    stripped = {k: v for k, v in tpl.items() if k != "ontology"}
    free = tmp_path / "templates" / "free_graph.yaml"
    free.parent.mkdir(exist_ok=True)
    free.write_text(yaml.safe_dump(stripped, allow_unicode=True), encoding="utf-8")
    (ka_base / "ds_free" / "ka" / "map_reduce.json").write_text(
        json.dumps({"template": "free_graph", "chunks": {}}), encoding="utf-8")
    # 重注 chunk(上面覆盖了 template 字段)
    _make_dump(ka_base / "ds_free" / "ka")
    d = json.loads((ka_base / "ds_free" / "ka" / "map_reduce.json").read_text())
    d["template"] = "free_graph"
    (ka_base / "ds_free" / "ka" / "map_reduce.json").write_text(
        json.dumps(d, ensure_ascii=False), encoding="utf-8")

    report = run_baseline("ds_free", ka_base, tmp_path / "templates")
    assert report["has_ontology_section"] is False
    assert report["escape"]["count"] == 0
    assert report["rejects"]["a"] == report["rejects"]["b"]
