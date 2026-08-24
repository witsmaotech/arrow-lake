"""W1.4 — validator:KG 快照 + SHACL shapes → 违规列表(MS1 F1.1)。

分级契约:Violation(sh:Warning → warn,其余 → reject);validator 自身
故障 fail-closed(reject,不放行 — 与 quality gate 同纪律)。
"""

from __future__ import annotations

import pytest

from arrow_lake.ontology.shape_builder import build_shapes
from arrow_lake.ontology.template_adapter import OntologySpec
from arrow_lake.ontology.validator import Violation, validate_snapshot


def _shapes():
    spec = OntologySpec(
        template_name="t_demo",
        entity_type_enum=("主体", "项目"),
        relation_type_enum=("contains",),
        type_pairs=(),
        required_entity_fields=("name", "type", "definition"),
        required_relation_fields=("source", "target", "type"),
        entity_field_types={"name": "str", "type": "str", "definition": "str"},
        relation_field_types={"source": "str", "target": "str", "type": "str"},
        warn_fields=("definition",),
    )
    return build_shapes(spec)


class TestValidateSnapshot:
    def test_valid_snapshot_zero_violations(self) -> None:
        entities = [
            {"name": "承建方A", "type": "主体", "definition": "当事方"},
            {"name": "一期项目", "type": "项目", "definition": "建设项目"},
        ]
        relations = [{"source": "承建方A", "target": "一期项目", "type": "contains"}]
        violations = validate_snapshot(entities, relations, _shapes())
        assert violations == []

    def test_type_outside_enum_is_reject(self) -> None:
        entities = [{"name": "X", "type": "神秘类型", "definition": "d"}]
        violations = validate_snapshot(entities, [], _shapes())
        rejects = [v for v in violations if v.level == "reject"]
        assert rejects, "type 越枚举必须是 reject"
        assert any(v.path == "type" for v in rejects)

    def test_missing_definition_is_warn(self) -> None:
        entities = [{"name": "X", "type": "主体"}]  # definition 缺
        violations = validate_snapshot(entities, [], _shapes())
        warns = [v for v in violations if v.level == "warn" and v.path == "definition"]
        assert warns, "definition 缺失必须是 warn(观察不拦)"

    def test_missing_name_is_reject(self) -> None:
        entities = [{"type": "主体", "definition": "d"}]
        violations = validate_snapshot(entities, [], _shapes())
        assert any(v.level == "reject" and v.path == "name" for v in violations)

    def test_relation_type_outside_enum_is_reject(self) -> None:
        relations = [{"source": "A", "target": "B", "type": "hacked"}]
        violations = validate_snapshot([], relations, _shapes())
        assert any(v.level == "reject" and v.path == "type" for v in violations)

    def test_violation_carries_focus_for_debugging(self) -> None:
        entities = [{"name": "X", "type": "神秘类型", "definition": "d"}]
        violations = validate_snapshot(entities, [], _shapes())
        assert all(isinstance(v, Violation) for v in violations)
        assert any("X" in v.focus for v in violations), "focus 应指向违规实体便于排障"

    def test_validator_failure_is_fail_closed(self, monkeypatch) -> None:
        """pyshacl 抛异常 → 单条 reject(校验不可用不放行)。"""
        import arrow_lake.ontology.validator as vmod

        def boom(*a, **k):
            raise RuntimeError("pyshacl internal error")

        monkeypatch.setattr(vmod.pyshacl, "validate", boom)
        violations = validate_snapshot([{"name": "X", "type": "主体", "definition": "d"}], [], _shapes())
        assert len(violations) == 1
        assert violations[0].level == "reject"
        assert "validator" in violations[0].message.lower()

    def test_type_pair_constraint(self) -> None:
        """type_pairs(src,rel,dst)跨实体约束(W2 gate 用,W1 先在 validator 层落地)。"""
        from arrow_lake.ontology.shape_builder import build_shapes as bs

        spec = OntologySpec(
            template_name="t_demo",
            entity_type_enum=("主体", "项目", "金额"),
            relation_type_enum=("contains", "承建"),
            type_pairs=(("主体", "承建", "项目"),),
            required_entity_fields=("name", "type", "definition"),
            required_relation_fields=("source", "target", "type"),
            entity_field_types={"name": "str", "type": "str", "definition": "str"},
            relation_field_types={"source": "str", "target": "str", "type": "str"},
            warn_fields=("definition",),
        )
        entities = [
            {"name": "承建方A", "type": "主体", "definition": "d"},
            {"name": "一期项目", "type": "项目", "definition": "d"},
            {"name": "预算", "type": "金额", "definition": "d"},
        ]
        # 合法 pair:主体-承建->项目
        ok = validate_snapshot(entities, [{"source": "承建方A", "target": "一期项目", "type": "承建"}], bs(spec))
        assert not [v for v in ok if "pair" in v.message.lower()]
        # 非法 pair:金额-承建->项目(不在 pairs 集)
        bad = validate_snapshot(entities, [{"source": "预算", "target": "一期项目", "type": "承建"}], bs(spec))
        assert any("pair" in v.message.lower() for v in bad)
