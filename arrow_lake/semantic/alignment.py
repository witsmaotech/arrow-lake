"""语义对齐配置(v1.11.1 W3.2/W3.4,F2.2)——封闭 transform + 投影编译。

对齐配置是 per-dataset 的 YAML(独立存储 V015,S1 拍板:契约=本体约束,
对齐=变换操作,生命周期不同),负责"从实然到应然"——把源系统的单位/取值
归一到契约声明口径。**封闭种类(S4)**:仅 ``unit``(仿射,注册表换算或
显式 factor)与 ``value_map``(字典映射);不开放表达式,注入面为零。
``value_map`` miss 在 SQL 投影里透传原值(CASE ELSE);响应侧携带
``aligned`` 列级元数据(projection_sql 第二返回值)供消费方审计口径。
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# 与契约共用同一套加载防线(节点/深度帽)与命名语法——单一来源,不复制。
from arrow_lake.contract.schema import (
    _TABLE_NAME_RE,
    DatasetContract,
    _contract_yaml_load,
)
from arrow_lake.semantic.units import conversion


class UnitTransform(BaseModel):
    """仿射换算:``to_value = value * factor + offset``。

    ``factor`` 省略 → from/to 必须是注册表内同维度单位(保存期校验,422);
    显式 ``factor`` 覆盖注册表(注册表外单位唯一入口,offset 可选)。
    """

    model_config = ConfigDict(frozen=True, populate_by_name=True)

    frm: str = Field(alias="from", min_length=1, max_length=64)
    to: str = Field(min_length=1, max_length=64)
    factor: float | None = None
    offset: float = 0.0

    @model_validator(mode="after")
    def _resolvable(self) -> UnitTransform:
        if self.factor is None:
            try:
                conversion(self.frm, self.to)
            except ValueError as exc:
                raise ValueError(f"unit transform {self.frm!r} → {self.to!r}: {exc}") from exc
        return self

    def resolve(self) -> tuple[float, float]:
        if self.factor is not None:
            return (float(self.factor), float(self.offset))
        return conversion(self.frm, self.to)


class ColumnAlignment(BaseModel):
    model_config = ConfigDict(frozen=True)

    unit: UnitTransform | None = None
    value_map: dict[str, str] | None = None

    @field_validator("value_map")
    @classmethod
    def _nonempty_map(cls, v: dict[str, str] | None) -> dict[str, str] | None:
        if v is not None:
            if not v:
                raise ValueError("value_map must not be empty")
            for k, val in v.items():
                if not k or not val:
                    raise ValueError("value_map keys and values must be non-empty")
        return v

    @model_validator(mode="after")
    def _exactly_one(self) -> ColumnAlignment:
        if (self.unit is None) == (self.value_map is None):
            raise ValueError(
                "column alignment needs exactly one of 'unit' / 'value_map'"
            )
        return self


class SemanticAlignment(BaseModel):
    model_config = ConfigDict(frozen=True)

    dataset: str
    tables: dict[str, dict[str, ColumnAlignment]] = Field(default_factory=dict)

    @field_validator("dataset")
    @classmethod
    def _dataset_name(cls, v: str) -> str:
        if not _TABLE_NAME_RE.match(v):
            raise ValueError(f"Invalid dataset name in alignment: {v!r}")
        return v

    @field_validator("tables")
    @classmethod
    def _table_names(cls, v: dict[str, dict[str, ColumnAlignment]]) -> dict:
        for name in v:
            if not _TABLE_NAME_RE.match(name):
                raise ValueError(f"Invalid table name in alignment: {name!r}")
        return v


def parse_alignment(text: str) -> SemanticAlignment:
    """解析对齐配置 YAML(结构/单位可解析性在保存期即校验)。"""
    raw = _contract_yaml_load(text)
    if not isinstance(raw, dict):
        raise ValueError("alignment must be a YAML mapping")
    dataset = raw.get("dataset")
    if not dataset:
        raise ValueError("alignment requires a 'dataset' field")
    tables: dict[str, dict[str, ColumnAlignment]] = {}
    for tname, body in (raw.get("tables") or {}).items():
        columns = (body or {}).get("columns") or {}
        tables[tname] = {
            cname: ColumnAlignment(**(spec or {}))
            for cname, spec in columns.items()
        }
    return SemanticAlignment(dataset=dataset, tables=tables)


# --------------------------------------------------------------------------- #
# 契约软校验(warn 不拒)与 SQL 投影编译(W3.4)
# --------------------------------------------------------------------------- #


def check_against_contract(
    contract: DatasetContract, alignment: SemanticAlignment,
) -> list[dict[str, str]]:
    """对齐配置 vs 契约的观察级提示:未知表/未知列/单位口径不符(to ≠ 契约
    声明 unit)。契约 unit 是"应然",对齐负责到达它;不符 → warn 不拒。"""
    warns: list[dict[str, str]] = []
    for tname, cols in alignment.tables.items():
        section = contract.tables.get(tname)
        if section is None:
            warns.append({
                "kind": "unknown_table", "table": tname,
                "message": f"alignment table '{tname}' not in contract",
            })
            continue
        declared = {r.name: r for r in section.columns}
        for cname, ca in cols.items():
            rule = declared.get(cname)
            if rule is None:
                warns.append({
                    "kind": "unknown_column", "table": tname, "column": cname,
                    "message": f"alignment column '{cname}' not in contract table '{tname}'",
                })
                continue
            if ca.unit is not None and rule.unit is not None and ca.unit.to != rule.unit:
                warns.append({
                    "kind": "unit_mismatch", "table": tname, "column": cname,
                    "alignment_to": ca.unit.to, "contract_unit": rule.unit,
                    "message": (
                        f"alignment converts to {ca.unit.to!r} but contract "
                        f"declares unit {rule.unit!r}"
                    ),
                })
    return warns


def _sql_lit(v: str) -> str:
    return "'" + v.replace("'", "''") + "'"


def projection_sql(ca: ColumnAlignment, quoted: str) -> tuple[str, dict[str, Any]]:
    """一列的对齐投影:``(expr, meta)``。

    ``quoted`` 是调用方完成标识符引用的列名(引用方式不属于本层)。expr
    直接可嵌进 SELECT 列表;meta 供响应侧 ``aligned`` 元数据(口径审计)。
    """
    if ca.unit is not None:
        factor, offset = ca.unit.resolve()
        if factor == 1.0 and offset == 0.0:
            expr = quoted
        elif offset == 0.0:
            expr = f"({quoted} * {factor!r})"
        else:
            expr = f"({quoted} * {factor!r} + {offset!r})"
        return expr, {"kind": "unit", "from": ca.unit.frm, "to": ca.unit.to}
    whens = " ".join(
        f"WHEN {quoted} = {_sql_lit(k)} THEN {_sql_lit(v)}"
        for k, v in (ca.value_map or {}).items()
    )
    return (
        f"CASE {whens} ELSE {quoted} END",
        {"kind": "value_map", "map": dict(ca.value_map or {})},
    )
