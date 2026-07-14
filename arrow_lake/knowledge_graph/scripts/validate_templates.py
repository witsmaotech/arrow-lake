#!/usr/bin/env python3
"""hyper-extract 模板 validator — 4 层 checklist + auto-fix 建议 (#8).

扫描项目级领域模板 (arrow_lake/knowledge_graph/templates/*.yaml),
基于 hyper-extract he-template-validator 规范校验:
  L1 通用: 必需字段 / type 合法 / name CamelCase / tags 小写 / 字段数
  L2 Graph: entities+relations / identifiers 三件套 / 关系字段叫 type
  L3 Hypergraph: relation_members 类型(simple str/nested list) / 字段 type:list
  L4 Temporal/Spatial: time_field / location_field / rules_for_time/location
  + Schema vs Guideline 分离 (guideline 不重复字段定义)
  + 多语言纯度 (zh 纯中文, en 纯英文)
  + identifiers 一致性 (指向真实字段)
  + display 占位符引用真实字段

用法:
  python -m arrow_lake.knowledge_graph.scripts.validate_templates [templates_dir]
  python arrow_lake/knowledge_graph/scripts/validate_templates.py [dir]
"""
from __future__ import annotations

import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

VALID_AUTOTYPES = {
    "model", "list", "set", "graph", "hypergraph",
    "temporal_graph", "spatial_graph", "spatio_temporal_graph",
}
GRAPH_TYPES = {"graph", "hypergraph", "temporal_graph", "spatial_graph", "spatio_temporal_graph"}
VALID_FIELD_TYPES = {"str", "int", "float", "bool", "list", "list[str]", "datetime"}

# auto-fix: 非规范字段名 → 规范名
AUTO_FIX_FIELDS = {
    "relation_type": "type", "relation": "type",
    "entity_type": "type",
    "event_date": "time", "date": "time", "timestamp": "time",
    "location_name": "location", "place": "location", "geo": "location",
}

MAX_ENTITY_FIELDS = 5
MAX_RELATION_FIELDS = 5
MAX_LIST_FIELDS = 3

# 中文/英文纯度: zh 段禁止裸英文术语, en 段禁止中文字符
ENGLISH_WORD = re.compile(r"[A-Za-z]{2,}")
CJK_CHAR = re.compile(r"[一-鿿]")


@dataclass
class Issue:
    level: str          # ERROR / WARN / INFO
    layer: str          # L1通用 / L2Graph / L3Hypergraph / L4Time/Space / 命名 / 多语言 / Schema-Guideline / identifiers / display
    message: str
    autofix: str | None = None


@dataclass
class Report:
    path: str
    issues: list[Issue] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not any(i.level == "ERROR" for i in self.issues)

    def summary(self) -> str:
        err = sum(1 for i in self.issues if i.level == "ERROR")
        warn = sum(1 for i in self.issues if i.level == "WARN")
        info = sum(1 for i in self.issues if i.level == "INFO")
        tag = "✅ PASS" if self.ok else "❌ FAIL"
        return f"{tag}  {self.path}  (ERROR={err} WARN={warn} INFO={info})"


def _field_names(node: Any) -> list[str]:
    if not isinstance(node, dict):
        return []
    fields = node.get("fields")
    if not isinstance(fields, list):
        return []
    return [f.get("name", "") for f in fields if isinstance(f, dict) and f.get("name")]


def _biling(data: Any) -> tuple[str, str]:
    if not isinstance(data, dict):
        return "", ""
    return str(data.get("zh", "") or ""), str(data.get("en", "") or "")


def validate(data: dict, path: str) -> Report:
    r = Report(path=path)
    t = data.get("type", "")
    output = data.get("output") if isinstance(data.get("output"), dict) else {}
    guideline = data.get("guideline") if isinstance(data.get("guideline"), dict) else {}

    # --- L1 通用 ---
    for f in ("language", "name", "type", "tags", "description", "output", "guideline"):
        if f not in data:
            r.issues.append(Issue("ERROR", "L1通用", f"缺必需顶层字段: {f}"))
    if t and t not in VALID_AUTOTYPES:
        r.issues.append(Issue("ERROR", "L1通用", f"type 非法: '{t}' (合法: {sorted(VALID_AUTOTYPES)})"))
    # name CamelCase (graph 系模板原版用 snake_case 如 biography_graph, 允许; 但 project 模板建议 CamelCase)
    name = data.get("name", "")
    if name and "_" in name and name.islower():
        r.issues.append(Issue("INFO", "命名", f"name snake_case (建议 CamelCase): '{name}'", name.replace("_", " ").title().replace(" ", "")))
    # tags 小写
    for tag in data.get("tags", []) or []:
        if tag != tag.lower():
            r.issues.append(Issue("WARN", "命名", f"tag 非小写: '{tag}'", tag.lower()))

    # --- 字段数 / 字段类型 ---
    ents = output.get("entities") if isinstance(output.get("entities"), dict) else {}
    rels = output.get("relations") if isinstance(output.get("relations"), dict) else {}
    ent_fields = ents.get("fields", []) if isinstance(ents, dict) else []
    rel_fields = rels.get("fields", []) if isinstance(rels, dict) else []
    flat_fields = output.get("fields", []) if isinstance(output.get("fields"), list) else []

    if t in GRAPH_TYPES:
        if not ent_fields:
            r.issues.append(Issue("ERROR", "L2Graph", "output.entities.fields 缺失"))
        if not rel_fields:
            r.issues.append(Issue("ERROR", "L2Graph", "output.relations.fields 缺失"))
        if len(ent_fields) > MAX_ENTITY_FIELDS:
            r.issues.append(Issue("WARN", "字段数", f"实体字段 {len(ent_fields)} > {MAX_ENTITY_FIELDS}, 建议拆模板或砍可选"))
        if len(rel_fields) > MAX_RELATION_FIELDS:
            r.issues.append(Issue("WARN", "字段数", f"关系字段 {len(rel_fields)} > {MAX_RELATION_FIELDS}"))
    if t in ("model", "list", "set") and flat_fields:
        if len(flat_fields) > MAX_LIST_FIELDS:
            r.issues.append(Issue("WARN", "字段数", f"记录字段 {len(flat_fields)} > {MAX_LIST_FIELDS}"))

    # 字段类型合法 + auto-fix 字段名
    for f in (*ent_fields, *rel_fields, *flat_fields):
        if not isinstance(f, dict):
            continue
        fn = f.get("name", "")
        ft = f.get("type", "")
        if ft and ft not in VALID_FIELD_TYPES:
            r.issues.append(Issue("WARN", "字段类型", f"字段 '{fn}' type 非标准: '{ft}'"))
        if fn in AUTO_FIX_FIELDS:
            r.issues.append(Issue("WARN", "命名", f"字段名 '{fn}' 应改为 '{AUTO_FIX_FIELDS[fn]}'", AUTO_FIX_FIELDS[fn]))

    # --- L2 Graph identifiers ---
    if t in GRAPH_TYPES:
        ids = data.get("identifiers") if isinstance(data.get("identifiers"), dict) else {}
        for k in ("entity_id", "relation_id", "relation_members"):
            if k not in ids:
                r.issues.append(Issue("ERROR", "L2Graph", f"identifiers 缺 {k}"))
        ent_names = _field_names(ents)
        rel_names = _field_names(rels)
        if "entity_id" in ids and ids["entity_id"] not in ent_names:
            r.issues.append(Issue("ERROR", "identifiers", f"entity_id='{ids['entity_id']}' 不在 entities.fields {ent_names}"))
        # relation_members 一致性
        rm = ids.get("relation_members")
        if t == "graph" and isinstance(rm, dict):
            for role, fld in rm.items():
                if fld not in rel_names:
                    r.issues.append(Issue("ERROR", "identifiers", f"relation_members.{role}='{fld}' 不在 relations.fields"))
        elif t == "hypergraph":
            if isinstance(rm, str) and rm not in rel_names:
                r.issues.append(Issue("ERROR", "L3Hypergraph", f"relation_members(simple)='{rm}' 不在 relations.fields"))
            elif isinstance(rm, list):
                for fld in rm:
                    if fld not in rel_names:
                        r.issues.append(Issue("ERROR", "L3Hypergraph", f"relation_members(nested)='{fld}' 不在 relations.fields"))
                # nested 每个字段应 type:list
                for fld in rm:
                    for f in rel_fields:
                        if f.get("name") == fld and f.get("type") != "list":
                            r.issues.append(Issue("ERROR", "L3Hypergraph", f"nested relation_members 字段 '{fld}' 应 type:list (当前 {f.get('type')})"))

    # --- L4 Temporal / Spatial ---
    if t in ("temporal_graph", "spatio_temporal_graph"):
        ids = data.get("identifiers", {})
        if "time_field" not in ids:
            r.issues.append(Issue("ERROR", "L4Time/Space", "temporal 缺 identifiers.time_field"))
        if "rules_for_time" not in guideline:
            r.issues.append(Issue("WARN", "L4Time/Space", "temporal 缺 guideline.rules_for_time"))
    if t in ("spatial_graph", "spatio_temporal_graph"):
        ids = data.get("identifiers", {})
        if "location_field" not in ids:
            r.issues.append(Issue("ERROR", "L4Time/Space", "spatial 缺 identifiers.location_field"))
        if "rules_for_location" not in guideline:
            r.issues.append(Issue("WARN", "L4Time/Space", "spatial 缺 guideline.rules_for_location"))

    # --- Schema vs Guideline 分离 ---
    # guideline rules 不应重复 schema 字段定义 (如 "name 是实体名称" 属 schema description)
    guideline_text = " ".join(_flatten_rules(guideline)).lower()
    for f in (*ent_fields, *rel_fields):
        if isinstance(f, dict):
            fn = f.get("name", "")
            if fn and re.search(rf"\b{re.escape(fn)}\s+(是|字段|表示)", guideline_text):
                r.issues.append(Issue("INFO", "Schema-Guideline", f"guideline 可能重复 schema 字段 '{fn}' 定义 (Schema=WHAT, Guideline=HOW)"))

    # --- 多语言纯度 ---
    _check_biling(r, data.get("description"), "description")
    _check_biling(r, guideline.get("target"), "guideline.target")
    for f in (*ent_fields, *rel_fields, *flat_fields):
        if isinstance(f, dict):
            _check_biling(r, f.get("description"), f"字段 {f.get('name','')}.description")

    # --- display 占位符 ---
    display = data.get("display") if isinstance(data.get("display"), dict) else {}
    all_names = set(ent_names_full := _field_names(ents)) | set(_field_names(rels))
    for key, tmpl in display.items():
        if not isinstance(tmpl, str):
            continue
        for ph in re.findall(r"\{(\w+)\}", tmpl):
            if ph not in all_names and ph not in ("name",):
                r.issues.append(Issue("WARN", "display", f"display.{key} 占位符 {{{ph}}} 不在字段集"))

    return r


def _flatten_rules(guideline: dict) -> list[str]:
    out = []
    for v in guideline.values():
        if isinstance(v, dict):
            out.extend(str(x) for x in (v.get("zh", []) + v.get("en", [])) if x)
        elif isinstance(v, list):
            out.extend(str(x) for x in v if x)
        elif isinstance(v, str):
            out.append(v)
    return out


def _check_biling(r: Report, desc: Any, ctx: str) -> None:
    zh, en = _biling(desc)
    if zh and ENGLISH_WORD.search(zh):
        # 允许常见技术缩写 (DDD/API/SDK 等), 只标记连续英文词
        words = [w for w in ENGLISH_WORD.findall(zh) if w not in {"DDD", "API", "SDK", "PO", "VO", "DO", "DAO", "DTO", "SQL", "UI", "IO", "ID", "OK"}]
        if words:
            r.issues.append(Issue("INFO", "多语言", f"{ctx}.zh 含英文词 {words[:3]} (建议纯中文或加中文释义)"))
    if en and CJK_CHAR.search(en):
        r.issues.append(Issue("WARN", "多语言", f"{ctx}.en 含中文字符 (en 段应纯英文)"))


def main(argv: list[str] | None = None) -> int:
    argv = argv or sys.argv[1:]
    tdir = Path(argv[0]) if argv else (Path(__file__).resolve().parent.parent / "templates")
    if not tdir.is_dir():
        print(f"模板目录不存在: {tdir}")
        return 2
    yamls = sorted(tdir.glob("*.yaml"))
    if not yamls:
        print(f"无模板: {tdir}")
        return 0
    all_ok = True
    for yml in yamls:
        try:
            data = yaml.safe_load(yml.read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError) as e:
            print(f"❌ FAIL  {yml}  YAML 解析失败: {e}")
            all_ok = False
            continue
        if not isinstance(data, dict):
            print(f"❌ FAIL  {yml}  顶层非 dict")
            all_ok = False
            continue
        rep = validate(data, yml.name)
        print(rep.summary())
        for i in rep.issues:
            fix = f"  → auto-fix: {i.autofix}" if i.autofix else ""
            print(f"    [{i.level:5}] {i.layer:16} {i.message}{fix}")
        if not rep.ok:
            all_ok = False
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
