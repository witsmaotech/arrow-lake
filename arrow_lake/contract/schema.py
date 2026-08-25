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
from typing import Any

import pyarrow as pa
import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

_TABLE_NAME_RE = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_-]*$")
# column names may be non-ASCII (压力/材质) but must stay SQL-quote safe
_UNSAFE_COLUMN_RE = re.compile(r'["\';\x00-\x1f]')


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
        out.append(f"(?P<{name}>{group_re})" if named else group_re)
        i = close + 1
    return "".join(out)


def pattern_to_match_regex(pattern: str) -> str:
    """Validation form (DuckDB regexp_full_match): literals + groups, no captures."""
    return _compile_pattern(pattern, named=False)


def pattern_to_extract_regex(pattern: str) -> str:
    """Extraction form (F2.1 identifier parsing): named capture groups."""
    return _compile_pattern(pattern, named=True)


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
        _compile_pattern(v, named=False)  # raises ValueError on bad syntax
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
    unit: str | None = None            # registration-only (F2.2); no row SQL
    range: tuple[float, float] | None = None   # closed interval
    enum: tuple[str, ...] | None = None
    type: str | None = None            # type assertion → warn level
    required: bool = False             # NOT NULL → reject level

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


class TableSection(BaseModel):
    model_config = ConfigDict(frozen=True)

    object_class: str | None = None
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
    )


class DatasetContract(BaseModel):
    """Parsed contract: one document, N table sections, M references."""

    model_config = ConfigDict(frozen=True)

    dataset: str
    tables: dict[str, TableSection]
    references: tuple[ReferenceRule, ...] = ()

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

def parse_contract(text: str) -> DatasetContract:
    """Parse contract YAML. Accepts the container form (``tables:``) and the
    legacy single-table form (top-level ``ontology:``), auto-wrapped into a
    default section named after the dataset."""
    raw = yaml.safe_load(text)
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
    return DatasetContract(dataset=dataset, tables=sections, references=refs)


# --------------------------------------------------------------------------- #
# Feature extraction + structured diff (version chain, mirrors ontology/versioning)
# --------------------------------------------------------------------------- #

def contract_features(c: DatasetContract) -> dict[str, Any]:
    tables: dict[str, Any] = {}
    for name, sec in c.tables.items():
        tables[name] = {
            "object_class": sec.object_class,
            "identifier": None if sec.identifier is None else
            [sec.identifier.column, sec.identifier.pattern],
            "columns": {r.name: {
                "unit": r.unit, "range": list(r.range) if r.range else None,
                "enum": list(r.enum) if r.enum else None,
                "type": r.type, "required": r.required,
            } for r in sec.columns},
        }
    return {
        "tables": tables,
        "references": sorted(
            [r.from_table, r.from_column,
             r.to_dataset or "", r.to_table or "", r.to_column]
            for r in c.references
        ),
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
    return {
        "tables_added": added,
        "tables_removed": removed,
        "tables": tables_diff,
        "references_added": sorted(list(r) for r in new_refs - old_refs),
        "references_removed": sorted(list(r) for r in old_refs - new_refs),
    }
