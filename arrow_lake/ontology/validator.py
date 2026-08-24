"""F1.1 — OntologyValidator:KG 快照 + SHACL shapes → 违规列表。

契约:
* SHACL 结果按 severity 分级 — ``sh:Warning`` → ``warn``(观察不拦),
  其余(Violation)→ ``reject``;
* ``type_pairs`` 跨实体约束由本模块纯 Python 承担(SHACL 表达笨重);
* **fail-closed**:pyshacl 自身故障 → 单条 reject(校验不可用不放行,
  与 quality gate 同纪律)。

红线:只读校验,不进查询热路径,不改抽取链路(调用点仅 kg_build 收尾
与 ontology API)。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pyshacl
from rdflib import BNode, Graph, Literal, RDF

from arrow_lake.ontology.shape_builder import AL, SHACL

LEVEL_REJECT = "reject"
LEVEL_WARN = "warn"


@dataclass(frozen=True)
class Violation:
    """一条校验违规(reject 拒 / warn 观察)。"""

    level: str  # "reject" | "warn"
    focus: str  # 违规实体/关系的标识(name 或 #index)
    path: str   # 字段
    value: str
    message: str


def _snapshot_to_graph(
    entities: list[dict[str, Any]],
    relations: list[dict[str, Any]],
) -> tuple[Graph, dict[BNode, str]]:
    """实体/关系行 → RDF 数据图;返回 (graph, BNode→focus 标识映射)。"""
    g = Graph()
    g.bind("al", AL)
    focus_map: dict[BNode, str] = {}

    def _add_row(row: dict[str, Any], cls: str) -> BNode:
        node = BNode()
        g.add((node, RDF.type, AL[cls]))
        focus_map[node] = str(row.get("name") or row.get("source", "?"))
        for key, value in row.items():
            if isinstance(value, (dict, list)):
                continue
            g.add((node, AL[str(key)], Literal(str(value))))
        return node

    for row in entities:
        _add_row(row, "Entity")
    for row in relations:
        _add_row(row, "Relation")
    return g, focus_map


def _parse_results(results_graph: Graph, focus_map: dict[BNode, str]) -> list[Violation]:
    violations: list[Violation] = []
    for result in results_graph.subjects(RDF.type, SHACL.ValidationResult):
        focus_node = results_graph.value(result, SHACL.focusNode)
        path = results_graph.value(result, SHACL.resultPath)
        value = results_graph.value(result, SHACL.value)
        message = results_graph.value(result, SHACL.resultMessage)
        # 结果节点用 sh:resultSeverity(sh:severity 是 shape 上的声明 — SHACL spec)
        severity = results_graph.value(result, SHACL.resultSeverity)

        level = LEVEL_WARN if severity is not None and "Warning" in str(severity) else LEVEL_REJECT
        violations.append(
            Violation(
                level=level,
                focus=focus_map.get(focus_node, str(focus_node)) if focus_node is not None else "?",
                path=str(path).split("#")[-1] if path is not None else "",
                value=str(value) if value is not None else "",
                message=str(message) if message is not None else "SHACL violation",
            )
        )
    return violations


def _pair_allowed(
    allowed_pairs: tuple[tuple[str, str, str], ...],
    src_t: str, rel_type: str, dst_t: str,
) -> bool:
    """三元组匹配,src/rel/dst 任一为 ``"*"`` 即通配(relation_validator 语义)。

    v1.11.0 F1.6:此前仅支持 ``(src, *, dst)`` 关系位通配,而运行时白名单
    (报价/部署于/交付于/达成/遵循)需要 src/dst 位通配 —— 升级为三元全通配。
    """
    return any(
        (s == "*" or s == src_t) and (r == "*" or r == rel_type) and (d == "*" or d == dst_t)
        for s, r, d in allowed_pairs
    )


def _check_type_pairs(
    entities: list[dict[str, Any]],
    relations: list[dict[str, Any]],
    allowed_pairs: tuple[tuple[str, str, str], ...],
) -> list[Violation]:
    """跨实体 (src_type, relation, dst_type) 配对检查(SHACL 之外的纯 Python)。"""
    if not allowed_pairs:
        return []
    entity_type = {
        str(e.get("name")): str(e.get("type", "")) for e in entities if e.get("name")
    }
    violations: list[Violation] = []
    for idx, rel in enumerate(relations):
        rel_type = str(rel.get("type", ""))
        src_t = entity_type.get(str(rel.get("source", "")))
        dst_t = entity_type.get(str(rel.get("target", "")))
        if src_t is None or dst_t is None:
            continue  # 端点缺失由 SHACL required 层抓,不重复报
        if not _pair_allowed(allowed_pairs, src_t, rel_type, dst_t):
            violations.append(
                Violation(
                    level=LEVEL_REJECT,
                    focus=str(rel.get("source", f"relation#{idx}")),
                    path="type",
                    value=f"{src_t} -[{rel_type}]-> {dst_t}",
                    message=f"type pair not allowed: {src_t} -[{rel_type}]-> {dst_t}",
                )
            )
    return violations


def _pairs_from_shapes(shapes: Graph) -> tuple[tuple[str, str, str], ...]:
    """从 shapes graph 读回 shape_builder 编码的 type-pair 元数据。"""
    pairs: list[tuple[str, str, str]] = []
    for lit in shapes.objects(AL.TypePairs, AL.pair):
        parts = str(lit).split("\x1f")
        if len(parts) == 3:
            pairs.append((parts[0], parts[1], parts[2]))
    return tuple(pairs)


def validate_snapshot(
    entities: list[dict[str, Any]],
    relations: list[dict[str, Any]],
    shapes: Graph,
    *,
    type_pairs: tuple[tuple[str, str, str], ...] | None = None,
) -> list[Violation]:
    """校验一份 KG 快照(实体/关系行)against SHACL shapes。

    Args:
        entities: 实体行(name/type/definition/...)。
        relations: 关系行(source/target/type/...)。
        shapes: shape_builder.build_shapes 的输出(type-pair 元数据随
            Turtle 持久化,从这里读回)。
        type_pairs: 显式覆盖跨实体配对约束(缺省 None = 从 shapes 读)。
    """
    try:
        data_graph, focus_map = _snapshot_to_graph(entities, relations)
        _, results_graph, _ = pyshacl.validate(
            data_graph, shacl_graph=shapes, inference="none",
        )
        violations = _parse_results(results_graph, focus_map)
    except Exception as exc:  # noqa: BLE001 — fail-closed: 校验不可用不放行
        return [
            Violation(
                level=LEVEL_REJECT,
                focus="<snapshot>",
                path="",
                value="",
                message=f"validator failure (fail-closed): {exc}",
            )
        ]
    effective_pairs = type_pairs if type_pairs is not None else _pairs_from_shapes(shapes)
    violations.extend(_check_type_pairs(entities, relations, effective_pairs))
    return violations
