"""Dataset contract model (DR13/DR14, v1.11.0.1 W2.2) — table-section form.

A contract is one YAML document per dataset (container). ``tables:`` holds
one section per table — the container form; a legacy single-table contract
(top-level ``ontology:`` block) auto-wraps into exactly one default section
named after the dataset. Row-level severities follow the MS1 discipline:
domain violations (enum/range/identifier pattern/reference) are ``reject``,
type assertions are ``warn`` (observational), ``unit`` is registration-only
(consumed by MS2 F2.2 unit alignment, never a row constraint).
"""

from __future__ import annotations

import re
from enum import Enum
from typing import Any, Literal

import pyarrow as pa
import yaml
from pydantic import BaseModel, ConfigDict, field_validator, model_validator

_TABLE_NAME_RE = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_-]*$")
# column names may be non-ASCII (压力/材质) but must stay SQL-quote safe
_UNSAFE_COLUMN_RE = re.compile(r'["\';\x00-\x1f]')
# P2-5 (review 2026-08-26 §三): ReDoS guard for contract identifier patterns
# (match form runs in DuckDB RE2 — linear — but the extract form is plain
# Python ``re`` in MS2 F2.1).
_MAX_GROUP_REGEX = 256


class Severity(str, Enum):
    REJECT = "reject"
    WARN = "warn"


# --------------------------------------------------------------------------- #
# Pattern syntax: "GAS.SEGMENT.{区域}.{序列}" / "V-{ver:[0-9]+}"
# --------------------------------------------------------------------------- #

def _compile_pattern(pattern: str, *, named: bool) -> str:
    """Translate a contract pattern into a regex.

    ``{name}`` → named capture group with default translation ``[^.]+``;
    ``{name:regex}`` → explicit group regex. Everything else is a literal
    (escaped). ``named=False`` emits group-free regex for DuckDB validation
    (RE2); ``named=True`` emits ``(?P<name>...)`` for identifier parsing
    (MS2 F2.1). Raises ValueError on syntax errors.
    """
    out: list[str] = []
    i = 0
    n = len(pattern)
    while i < n:
        ch = pattern[i]
        if ch != "{":
            out.append(re.escape(ch))
            i += 1
            continue
        close = pattern.find("}", i)
        if close == -1:
            raise ValueError(f"Unclosed '{{' in pattern at position {i}: {pattern!r}")
        body = pattern[i + 1:close]
        if not body:
            raise ValueError(f"Empty {{}} group in pattern: {pattern!r}")
        if "{" in body or "}" in body:
            raise ValueError(f"Nested braces in pattern group: {pattern!r}")
        name, sep, regex = body.partition(":")
        if not name:
            raise ValueError(f"Empty group name in pattern: {pattern!r}")
        group_re = regex if sep else r"[^.]+"
        # P2-5 (review 2026-08-26 §三): cap the group regex — the match form
        # runs in DuckDB RE2 (linear) but the extract form is plain Python
        # ``re`` in MS2 F2.1, so pathological patterns have a ReDoS surface.
        if len(group_re) > _MAX_GROUP_REGEX:
            raise ValueError(
                f"Pattern group regex exceeds {_MAX_GROUP_REGEX} chars "
                f"in pattern: {pattern!r}"
            )
        out.append(f"(?P<{name}>{group_re})" if named else group_re)
        i = close + 1
    return "".join(out)


def pattern_to_match_regex(pattern: str) -> str:
    """Validation form (DuckDB regexp_full_match): literals + groups, no captures."""
    rx = _compile_pattern(pattern, named=False)
    _assert_compilable(rx, pattern)
    return rx


def pattern_to_extract_regex(pattern: str) -> str:
    """Extraction form (F2.1 identifier parsing): named capture groups."""
    rx = _compile_pattern(pattern, named=True)
    _assert_compilable(rx, pattern)
    return rx


def _assert_compilable(rx: str, pattern: str) -> None:
    """P2-5 (review 2026-08-26 §三): the inner ``{name:regex}`` body must be
    a compilable regex — brace structure alone accepts garbage like an
    unclosed character class, which used to surface only at RUNTIME."""
    try:
        re.compile(rx)
    except re.error as exc:
        raise ValueError(f"Invalid inner regex in pattern {pattern!r}: {exc}") from exc


# --------------------------------------------------------------------------- #
# Contract models
# --------------------------------------------------------------------------- #

class IdentifierRule(BaseModel):
    model_config = ConfigDict(frozen=True)

    column: str
    pattern: str

    @field_validator("pattern")
    @classmethod
    def _pattern_syntax(cls, v: str) -> str:
        # P2-5 (review 2026-08-26 §三): try-compile BOTH translated forms —
        # brace structure alone accepts an invalid inner regex (e.g. an
        # unclosed character class), which used to blow up at RUNTIME (first
        # DuckDB evaluation / first F2.1 extract) instead of at save time.
        pattern_to_match_regex(v)
        pattern_to_extract_regex(v)
        return v

    @property
    def match_regex(self) -> str:
        return pattern_to_match_regex(self.pattern)

    @property
    def extract_regex(self) -> str:
        return pattern_to_extract_regex(self.pattern)


class ColumnRule(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str
    label: str | None = None           # 业务显示名 (DR15 D-1); registration-only
    unit: str | None = None            # registration-only (F2.2); no row SQL
    range: tuple[float, float] | None = None   # closed interval
    enum: tuple[str, ...] | None = None
    type: str | None = None            # type assertion → warn level
    required: bool = False             # NOT NULL → reject level

    @field_validator("label")
    @classmethod
    def _nonempty_label(cls, v: str | None) -> str | None:
        if v is not None and not v.strip():
            raise ValueError(f"label must be non-empty: {v!r}")
        return v

    @field_validator("name")
    @classmethod
    def _safe_name(cls, v: str) -> str:
        if not v or not v.strip() or _UNSAFE_COLUMN_RE.search(v):
            raise ValueError(f"Unsafe column name: {v!r}")
        return v

    @field_validator("range")
    @classmethod
    def _ordered_range(cls, v: tuple[float, float] | None):
        if v is not None and v[0] > v[1]:
            raise ValueError(f"range must satisfy lo <= hi, got {v}")
        return v

    @field_validator("enum")
    @classmethod
    def _nonempty_enum(cls, v: tuple[str, ...] | None):
        if v is not None and not v:
            raise ValueError("enum must list at least one value")
        return v

    @property
    def severity(self) -> Severity:
        """Domain constraints reject; type assertions only warn."""
        if self.type is not None and self.enum is None and self.range is None \
                and not self.required:
            return Severity.WARN
        return Severity.REJECT


class LifecycleDecl(BaseModel):
    """Entity state declaration (DR15 D-1) — registration-only, MS3 状态机铺路.

    ``column`` (optional) points at the state column; ``states`` enumerates
    the legal values; ``initial`` must be one of them. Transitions stay MS3.
    """

    model_config = ConfigDict(frozen=True)

    column: str | None = None
    states: tuple[str, ...]
    initial: str | None = None

    @field_validator("column")
    @classmethod
    def _nonempty_column(cls, v: str | None) -> str | None:
        if v is not None and not v.strip():
            raise ValueError(f"lifecycle column must be non-empty: {v!r}")
        return v

    @field_validator("states")
    @classmethod
    def _nonempty_unique(cls, v: tuple[str, ...]) -> tuple[str, ...]:
        if not v:
            raise ValueError("lifecycle states must list at least one state")
        if len(set(v)) != len(v):
            raise ValueError(f"lifecycle states must be unique: {list(v)}")
        return v

    @model_validator(mode="after")
    def _initial_in_states(self) -> LifecycleDecl:
        if self.initial is not None and self.initial not in self.states:
            raise ValueError(
                f"lifecycle initial {self.initial!r} not in states {list(self.states)}"
            )
        return self


class TableSection(BaseModel):
    model_config = ConfigDict(frozen=True)

    object_class: str | None = None
    lifecycle: LifecycleDecl | None = None   # DR15 D-1; registration-only
    identifier: IdentifierRule | None = None
    columns: tuple[ColumnRule, ...] = ()

    @field_validator("object_class")
    @classmethod
    def _nonempty(cls, v: str | None) -> str | None:
        if v is not None and not v.strip():
            raise ValueError("object_class must be non-empty")
        return v


class ReferenceRule(BaseModel):
    """Normalized cross-table reference (all three YAML forms land here)."""

    model_config = ConfigDict(frozen=True)

    from_table: str
    from_column: str
    to_table: str | None = None         # intra-container target table
    to_column: str
    to_dataset: str | None = None       # cross-container target dataset
    # DR15 D-1 (+S3): model-layer relationship semantics, registration-only —
    # never compiled, never enforcing cascades; consumed by F2.4 object browsing.
    cardinality: Literal["1:1", "1:N", "N:1", "M:N"] | None = None
    kind: Literal["association", "composition", "aggregation", "dependency"] | None = None

    @property
    def target_kind(self) -> str:
        return "cross" if self.to_dataset else "intra"


def _normalize_reference(
    raw: dict[str, Any], default_from_table: str,
) -> ReferenceRule:
    from_expr = raw.get("from")
    column = raw.get("column")
    if from_expr is None and column is None:
        raise ValueError(f"reference needs 'from' or legacy 'column': {raw!r}")
    if from_expr is not None:
        if "." not in from_expr:
            raise ValueError(f"'from' must be 'table.column', got {from_expr!r}")
        from_table, from_column = from_expr.split(".", 1)
    else:
        from_table, from_column = default_from_table, str(column)

    to = raw.get("to")
    to_dataset = raw.get("to_dataset")
    to_column = raw.get("to_column")
    if to is not None:
        if to_dataset is not None or to_column is not None:
            raise ValueError("reference cannot mix 'to' with to_dataset/to_column")
        if "." not in to:
            raise ValueError(f"'to' must be 'table.column', got {to!r}")
        to_table, to_column = to.split(".", 1)
    elif to_dataset is not None:
        if to_column is None:
            raise ValueError("cross-container reference needs to_column")
        to_table = None
    else:
        raise ValueError(f"reference needs 'to' or to_dataset/to_column: {raw!r}")

    return ReferenceRule(
        from_table=from_table, from_column=from_column,
        to_table=to_table, to_column=str(to_column), to_dataset=to_dataset,
        cardinality=raw.get("cardinality"), kind=raw.get("kind"),
    )


# --------------------------------------------------------------------------- #
# Quality node (MS5 W1.1 / DR15 S1) — QoS Annotation form, registration-only
# --------------------------------------------------------------------------- #

#: 五维名(relevance/accuracy/completeness/diversity/timeliness)。
QUALITY_DIMENSIONS = ("relevance", "accuracy", "completeness", "diversity", "timeliness")

#: 一票否决项注册表(契约 veto 只能从中增删;release-time 项在评估期跳过)。
KNOWN_QUALITY_VETOES = frozenset({
    "accuracy_below_threshold",
    "relevance_below_threshold",
    "required_missing_gt_5pct",
    "unmasked_corpus_publish",   # 发布语料时检查(assess 期不可触发)
})


class AdmissionDecl(BaseModel):
    """准入门分(铜/银/金)——加权总分落档即得发布准入层级。"""

    model_config = ConfigDict(frozen=True)

    bronze: float
    silver: float
    gold: float

    @model_validator(mode="after")
    def _ordered(self) -> AdmissionDecl:
        if not (0 <= self.bronze <= self.silver <= self.gold <= 100):
            raise ValueError(
                f"admission must satisfy 0 <= bronze <= silver <= gold <= 100, "
                f"got ({self.bronze}, {self.silver}, {self.gold})"
            )
        return self


class TimelinessDecl(BaseModel):
    """时效领域参数(S5):标注延迟 p95 上限(小时)。"""

    model_config = ConfigDict(frozen=True)

    max_p95_hours: float

    @field_validator("max_p95_hours")
    @classmethod
    def _positive(cls, v: float) -> float:
        if v <= 0:
            raise ValueError(f"max_p95_hours must be positive, got {v}")
        return v


class QualitySpec(BaseModel):
    """契约顶层 ``quality:`` 节(MS5 五维门配置)——**登记不校验**。

    QoS Annotation 形态(DR15/S1):权重/阈值/否决/准入只是挂载到契约的
    质量标注,compiler 不消费(入口门零变化),评估期由
    ``arrow_lake.quality.spec.resolve_quality_spec`` 与默认常量合成生效
    配置。所有字段可缺省——契约不写 quality 节即用业务手册默认值。

    语义注记:``weights`` 若写则**整体替换**五维向量(未列维度=0),随
    后按和归一;``thresholds`` 逐键覆盖默认;``critical=true`` 将准确
    性门槛抬到 95(取 max)。
    """

    model_config = ConfigDict(frozen=True)

    weights: dict[str, float] | None = None
    thresholds: dict[str, float] | None = None
    veto: tuple[str, ...] | None = None
    admission: AdmissionDecl | None = None
    timeliness: TimelinessDecl | None = None
    critical: bool = False
    drift_kl: float | None = None   # 漂移 KL 阈值覆盖(W2.2;缺省 0.1)

    @field_validator("drift_kl")
    @classmethod
    def _positive_drift_kl(cls, v: float | None) -> float | None:
        if v is not None and not 0 < v <= 10:
            raise ValueError(f"drift_kl must satisfy 0 < v <= 10, got {v}")
        return v

    @field_validator("weights")
    @classmethod
    def _known_positive(cls, v: dict[str, float] | None) -> dict[str, float] | None:
        if v is None:
            return v
        unknown = set(v) - set(QUALITY_DIMENSIONS)
        if unknown:
            raise ValueError(f"unknown quality dimensions in weights: {sorted(unknown)}")
        if any(w < 0 for w in v.values()):
            raise ValueError(f"quality weights must be non-negative, got {v}")
        if not any(w > 0 for w in v.values()):
            raise ValueError("quality weights must include at least one positive value")
        return v

    @field_validator("thresholds")
    @classmethod
    def _bounded(cls, v: dict[str, float] | None) -> dict[str, float] | None:
        if v is None:
            return v
        unknown = set(v) - set(QUALITY_DIMENSIONS)
        if unknown:
            raise ValueError(f"unknown quality dimensions in thresholds: {sorted(unknown)}")
        for name, t in v.items():
            if not 0 <= t <= 100:
                raise ValueError(f"quality threshold {name}={t} outside [0, 100]")
        return v

    @field_validator("veto")
    @classmethod
    def _known_vetoes(cls, v: tuple[str, ...] | None) -> tuple[str, ...] | None:
        if v is None:
            return v
        unknown = set(v) - KNOWN_QUALITY_VETOES
        if unknown:
            raise ValueError(f"unknown quality veto items: {sorted(unknown)}")
        if len(set(v)) != len(v):
            raise ValueError(f"quality veto items must be unique: {list(v)}")
        return v


class DatasetContract(BaseModel):
    """Parsed contract: one document, N table sections, M references."""

    model_config = ConfigDict(frozen=True)

    dataset: str
    tables: dict[str, TableSection]
    references: tuple[ReferenceRule, ...] = ()
    quality: QualitySpec | None = None   # MS5 W1.1; registration-only

    @field_validator("dataset")
    @classmethod
    def _dataset_name(cls, v: str) -> str:
        if not _TABLE_NAME_RE.match(v):
            raise ValueError(f"Invalid dataset name in contract: {v!r}")
        return v

    @field_validator("tables")
    @classmethod
    def _table_names(cls, v: dict[str, TableSection]) -> dict[str, TableSection]:
        for name in v:
            if not _TABLE_NAME_RE.match(name):
                raise ValueError(f"Invalid table name in contract: {name!r}")
        return v

    # -- schema reconciliation (W2.2: unknown column / type assertion) ----

    def check_against_schema(
        self, table: str, schema: pa.Schema,
    ) -> list[dict[str, str]]:
        """Warn-level notes reconciling a table section against a batch schema.

        Only observational notes are produced here (unknown columns, type
        assertion mismatches) — row-level enforcement is the compiler/gate's
        job (W3). Unknown table → no notes (not this contract's concern).
        """
        section = self.tables.get(table)
        if section is None:
            return []
        names = set(schema.names)
        notes: list[dict[str, str]] = []
        for rule in section.columns:
            if rule.name not in names:
                notes.append({
                    "level": Severity.WARN.value,
                    "kind": "unknown_column",
                    "column": rule.name,
                    "message": f"contract column '{rule.name}' absent from batch schema",
                })
                continue
            if rule.type == "date":
                field = schema.field(rule.name)
                if not pa.types.is_temporal(field.type):
                    notes.append({
                        "level": Severity.WARN.value,
                        "kind": "type_mismatch",
                        "column": rule.name,
                        "message": f"column '{rule.name}' asserted date but is {field.type}",
                    })
        return notes


# --------------------------------------------------------------------------- #
# Parsing (new container form + legacy singular auto-wrap)
# --------------------------------------------------------------------------- #

class _CappedLoader(yaml.SafeLoader):
    """Guarded loader for ADMIN-submitted contract YAML (P1-8, review
    2026-08-26).

    Empirically checked: PyYAML aliases are REFERENCE-shared (a
    billion-laughs payload of 182 bytes peaks at ~23 KB — no amplification),
    so the real DoS vectors here are DEEP NESTING (``[[[[...`` →
    RecursionError → 500) and sheer node count. Both get hard caps that
    raise a clean ``yaml.YAMLError`` (mapped to 422 by parse_contract)
    instead of crashing the worker.
    """

    MAX_NODES = 50_000
    MAX_DEPTH = 200

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._cap_nodes = 0
        self._cap_depth = 0

    def compose_node(self, parent: Any, index: Any) -> Any:
        self._cap_depth += 1
        try:
            if self._cap_depth > self.MAX_DEPTH:
                raise yaml.YAMLError(
                    f"contract YAML nesting exceeds {self.MAX_DEPTH} levels"
                )
            self._cap_nodes += 1
            if self._cap_nodes > self.MAX_NODES:
                raise yaml.YAMLError(
                    f"contract YAML exceeds {self.MAX_NODES} nodes"
                )
            return super().compose_node(parent, index)
        finally:
            self._cap_depth -= 1


def _contract_yaml_load(text: str) -> Any:
    """safe_load with node/depth caps; DoS-shaped input → ValueError (422).

    ``yaml.load(text, Loader=...)`` with a ``SafeLoader`` subclass keeps the
    SAFE constructor set (``!!python/object`` stays rejected, verified) —
    the custom loader only overrides node COMPOSITION for the caps."""
    try:
        return yaml.load(text, Loader=_CappedLoader)
    except yaml.YAMLError as exc:
        raise ValueError(f"contract YAML rejected: {exc}") from exc
    except RecursionError as exc:  # belt-and-braces below the depth cap
        raise ValueError("contract YAML rejected: nesting too deep") from exc


def parse_contract(text: str) -> DatasetContract:
    """Parse contract YAML. Accepts the container form (``tables:``) and the
    legacy single-table form (top-level ``ontology:``), auto-wrapped into a
    default section named after the dataset."""
    raw = _contract_yaml_load(text)
    if not isinstance(raw, dict):
        raise ValueError("contract must be a YAML mapping")
    dataset = raw.get("dataset") or raw.get("name")
    if not dataset:
        raise ValueError("contract requires a 'dataset' field")

    if "tables" in raw:
        sections = {
            name: TableSection(**body or {})
            for name, body in (raw["tables"] or {}).items()
        }
        default_table: str | None = None
    elif "ontology" in raw:
        ont = raw["ontology"] or {}
        refs_raw = ont.pop("references", []) or []
        sections = {dataset: TableSection(**ont)}
        default_table = dataset
    else:
        raise ValueError("contract needs 'tables:' or legacy 'ontology:' block")

    refs_raw = raw.get("references") or [] if "tables" in raw else refs_raw
    refs = tuple(
        _normalize_reference(r, default_table or dataset) for r in refs_raw
    )
    quality_raw = raw.get("quality")
    quality = (
        QualitySpec(**quality_raw) if isinstance(quality_raw, dict) else None
    )
    return DatasetContract(
        dataset=dataset, tables=sections, references=refs, quality=quality,
    )


# --------------------------------------------------------------------------- #
# Feature extraction + structured diff (version chain, mirrors ontology/versioning)
# --------------------------------------------------------------------------- #

def _quality_features(q: QualitySpec | None) -> dict[str, Any] | None:
    """Quality 节 → 可比较特征(未写字段省略;版本链 diff 用)。"""
    if q is None:
        return None
    feat: dict[str, Any] = {"critical": q.critical}
    if q.weights is not None:
        feat["weights"] = {k: q.weights[k] for k in sorted(q.weights)}
    if q.thresholds is not None:
        feat["thresholds"] = {k: q.thresholds[k] for k in sorted(q.thresholds)}
    if q.veto is not None:
        feat["veto"] = list(q.veto)
    if q.admission is not None:
        feat["admission"] = [q.admission.bronze, q.admission.silver, q.admission.gold]
    if q.timeliness is not None:
        feat["timeliness"] = q.timeliness.max_p95_hours
    return feat


def contract_features(c: DatasetContract) -> dict[str, Any]:
    tables: dict[str, Any] = {}
    for name, sec in c.tables.items():
        tables[name] = {
            "object_class": sec.object_class,
            "lifecycle": None if sec.lifecycle is None else
            [sec.lifecycle.column, list(sec.lifecycle.states), sec.lifecycle.initial],
            "identifier": None if sec.identifier is None else
            [sec.identifier.column, sec.identifier.pattern],
            "columns": {r.name: {
                "label": r.label,
                "unit": r.unit, "range": list(r.range) if r.range else None,
                "enum": list(r.enum) if r.enum else None,
                "type": r.type, "required": r.required,
            } for r in sec.columns},
        }
    return {
        "tables": tables,
        "references": sorted(
            [r.from_table, r.from_column,
             r.to_dataset or "", r.to_table or "", r.to_column,
             r.cardinality or "", r.kind or ""]
            for r in c.references
        ),
        "quality": _quality_features(c.quality),
    }


def diff_features(old: dict[str, Any], new: dict[str, Any]) -> dict[str, Any]:
    """Structured diff between two contract feature dicts (stored on the new
    version): tables added/removed; per-common-table identifier/object_class/
    column changes; references added/removed."""
    old_t, new_t = old["tables"], new["tables"]
    added = sorted(set(new_t) - set(old_t))
    removed = sorted(set(old_t) - set(new_t))
    tables_diff: dict[str, Any] = {}
    for name in sorted(set(old_t) & set(new_t)):
        o, n = old_t[name], new_t[name]
        entry: dict[str, Any] = {}
        if o["identifier"] != n["identifier"]:
            entry["identifier"] = {"was": o["identifier"], "now": n["identifier"]}
        if o["object_class"] != n["object_class"]:
            entry["object_class"] = {"was": o["object_class"], "now": n["object_class"]}
        if o["lifecycle"] != n["lifecycle"]:
            entry["lifecycle"] = {"was": o["lifecycle"], "now": n["lifecycle"]}
        cols: list[dict[str, Any]] = []
        oc, nc = o["columns"], n["columns"]
        for col in sorted(set(nc) - set(oc)):
            cols.append({"column": col, "change": "added"})
        for col in sorted(set(oc) - set(nc)):
            cols.append({"column": col, "change": "removed"})
        for col in sorted(set(oc) & set(nc)):
            if oc[col] != nc[col]:
                cols.append({"column": col, "change": "changed",
                             "was": oc[col], "now": nc[col]})
        if cols:
            entry["columns"] = cols
        if entry:
            tables_diff[name] = entry
    old_refs = {tuple(r) for r in old["references"]}
    new_refs = {tuple(r) for r in new["references"]}
    out: dict[str, Any] = {
        "tables_added": added,
        "tables_removed": removed,
        "tables": tables_diff,
        "references_added": sorted(list(r) for r in new_refs - old_refs),
        "references_removed": sorted(list(r) for r in old_refs - new_refs),
    }
    if old.get("quality") != new.get("quality"):
        out["quality"] = {"was": old.get("quality"), "now": new.get("quality")}
    return out
