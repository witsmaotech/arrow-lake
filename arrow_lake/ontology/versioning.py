"""F1.4/W2.2 — ontology versioning:模板文件 → shapes 快照 + 版本 diff。

纯逻辑(rdflib 解析 + 集合差),不落库 — 落库在
``system_db.stores.ontology.OntologyVersionStore``(V010)。

* :func:`load_template_artifact` — 读模板文件 → (template_name, shapes
  Graph, Turtle, sha1);门禁与快照共用的单一加载路径;
* :func:`extract_features` / :func:`diff_features` — 从两版 Turtle 提取
  结构化特征(枚举/必填/type-pairs)并做增删 diff(ontology_versions
  .diff_json 的内容)。
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from pathlib import Path

from rdflib import Graph

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class TemplateArtifact:
    """一份模板的可持久化本体快照。"""

    template_name: str
    graph: Graph
    shapes_turtle: str
    source_hash: str  # 模板文件内容 sha1(同 hash 不重复快照)


def load_template_artifact(template_path: str | Path | None) -> TemplateArtifact | None:
    """模板文件 → TemplateArtifact;不可读/解析失败返回 None(调用方 skip)。"""
    if not template_path:
        return None
    import yaml

    from arrow_lake.ontology.shape_builder import build_shapes, to_turtle
    from arrow_lake.ontology.template_adapter import adapt_template

    try:
        raw = Path(template_path).read_bytes()
        template = yaml.safe_load(raw.decode("utf-8")) or {}
        if not isinstance(template, dict):
            return None
        spec = adapt_template(template)
        graph = build_shapes(spec)
        return TemplateArtifact(
            template_name=spec.template_name or Path(template_path).stem,
            graph=graph,
            shapes_turtle=to_turtle(graph),
            source_hash=hashlib.sha1(raw).hexdigest(),
        )
    except (OSError, ValueError, yaml.YAMLError) as exc:
        logger.warning("ontology artifact load failed (%s): %s", template_path, exc)
        return None


def extract_features(shapes_turtle: str) -> dict:
    """Turtle 快照 → 结构化特征(可比较、可 diff 的扁平视图)。

    与 shape_builder 的编码约定一一对应:NodeShape 的 PropertyShape 携带
    ``sh:path``/``sh:in``/``sh:minCount``;type-pairs 以 ``al:TypePairs
    al:pair`` 元数据三元组编码。
    """
    from rdflib.namespace import RDF

    from arrow_lake.ontology.shape_builder import AL, SHACL

    g = Graph().parse(data=shapes_turtle, format="turtle")

    def _features_for(shape_name: str) -> tuple[set[str], set[str]]:
        """(枚举值集合, 必填字段集合) — shape_name 上的 PropertyShape 集。"""
        enums: set[str] = set()
        required: set[str] = set()
        for shape in g.subjects(RDF.type, SHACL.NodeShape):
            if str(shape) != str(AL[shape_name]):
                continue
            for ps in g.objects(shape, SHACL.property):
                path = g.value(ps, SHACL.path)
                if path is None:
                    continue
                field = str(path).split("#")[-1]
                in_list = g.value(ps, SHACL["in"])
                if in_list is not None:
                    enums.update(str(v) for v in g.collection(in_list))
                min_count = g.value(ps, SHACL.minCount)
                if min_count is not None and int(min_count) >= 1:
                    required.add(field)
        return enums, required

    e_enums, e_required = _features_for("EntityShape")
    r_enums, r_required = _features_for("RelationShape")
    pairs = tuple(
        tuple(str(lit).split("\x1f"))
        for lit in g.objects(AL.TypePairs, AL.pair)
    )
    return {
        "entity_type_enum": sorted(e_enums),
        "relation_type_enum": sorted(r_enums),
        "required_entity_fields": sorted(e_required),
        "required_relation_fields": sorted(r_required),
        "type_pairs": sorted(pairs),
    }


_DIFF_SECTIONS = (
    "entity_type_enum", "relation_type_enum",
    "required_entity_fields", "required_relation_fields", "type_pairs",
)


def diff_features(old: dict, new: dict) -> dict:
    """两版特征 → {section: {added: [...], removed: [...]}}(保持 sorted 序)。"""
    diff: dict[str, dict[str, list]] = {}
    for section in _DIFF_SECTIONS:
        old_set = set(map(tuple, old.get(section) or [])) if section == "type_pairs" \
            else set(old.get(section) or [])
        new_set = set(map(tuple, new.get(section) or [])) if section == "type_pairs" \
            else set(new.get(section) or [])
        if section == "type_pairs":
            diff[section] = {
                "added": ["\x1f".join(p) for p in sorted(new_set - old_set)],
                "removed": ["\x1f".join(p) for p in sorted(old_set - new_set)],
            }
        else:
            diff[section] = {
                "added": sorted(new_set - old_set),
                "removed": sorted(old_set - new_set),
            }
    return diff
