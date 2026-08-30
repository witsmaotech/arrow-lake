"""F5.7 — 数据集规格书生成(v1.11.4 MS5 W3.3)。

**生成物,不手编**(S9):发布时从数据集+评估报告+ADL+契约即时合成,
YAML 存档进 ``sys_releases``,文件形态经 ``GET /release/{ds}/datasheet``
导出。字段(设计 §6 业务模板):id/version/category/lifecycle_status/
schema/scale/quality(五维+星级+κ)/labeling(标注覆盖/人数)/usage。
"""

from __future__ import annotations

from typing import Any

import pyarrow as pa
import yaml

__all__ = ["build_datasheet", "datasheet_yaml"]


def _labeling(adl: pa.Table | None, rows: int) -> dict[str, Any]:
    """标注统计:ADL 行数/去重行/标注人数/覆盖度(去重行 ÷ 表行数)。"""
    if adl is None or adl.num_rows == 0 or "source_row_id" not in adl.column_names:
        return {"adl_rows": 0, "annotated_rows": 0, "annotators": 0,
                "coverage": 0.0}
    recs = adl.select(["source_row_id", "annotator_id"]).to_pylist()
    annotated = {r["source_row_id"] for r in recs}
    annotators = {r["annotator_id"] for r in recs}
    coverage = round(len(annotated) / rows, 4) if rows else 0.0
    return {
        "adl_rows": adl.num_rows,
        "annotated_rows": len(annotated),
        "annotators": len(annotators),
        "coverage": coverage,
    }


def _schema_section(
    table: pa.Table, contract: Any,
) -> dict[str, Any]:
    out: dict[str, Any] = {
        "columns": {name: str(table.schema.field(name).type)
                    for name in table.column_names},
        "contract_present": contract is not None,
    }
    if contract is not None:
        required = sorted({
            rule.name
            for section in contract.tables.values()
            for rule in section.columns if rule.required
        })
        out["required_columns"] = required
        out["contract_tables"] = sorted(contract.tables)
    return out


def _quality_section(report: dict[str, Any]) -> dict[str, Any]:
    dims = report.get("dimensions") or {}
    accuracy = dims.get("accuracy") or {}
    return {
        "total_score": report.get("total_score"),
        "star": report.get("star"),
        "admission": report.get("admission"),
        "verdict": report.get("verdict"),
        "kappa": (accuracy.get("details") or {}).get("kappa"),
        "accuracy_source": accuracy.get("source"),
        "relevance_score": (dims.get("relevance") or {}).get("score"),
        "dimensions": {name: (d or {}).get("score")
                       for name, d in dims.items()},
        "vetoes": report.get("vetoes") or [],
        "degraded": report.get("degraded") or [],
        "weights": (report.get("spec") or {}).get("weights"),
    }


def build_datasheet(
    *,
    dataset: str,
    tag: str,
    lance_version: int,
    report: dict[str, Any],
    contract: Any,
    table: pa.Table,
    adl: pa.Table | None,
    changelog: str,
    released_by: str,
    released_at: str,
    category: str | None = None,
) -> dict[str, Any]:
    """发布物 → 规格书 dict(字段口径见模块 docstring)。"""
    return {
        "id": dataset,
        "version": tag,
        "category": category,
        "lifecycle_status": "active",
        "changelog": changelog,
        "lance_version": lance_version,
        "released_at": released_at,
        "released_by": released_by,
        "schema": _schema_section(table, contract),
        "scale": {"rows": table.num_rows, "columns": table.num_columns},
        "quality": _quality_section(report),
        "labeling": _labeling(adl, table.num_rows),
        "usage": {
            "license": "internal",
            "corpus_forms": ["sft", "pretrain", "rlhf", "golden"],
            "exports_dir": f"/data/lake/exports/{tag}/",
            "note": "corpus exports land in W4 (MS5 F5.6); all forms masked",
        },
    }


def datasheet_yaml(datasheet: dict[str, Any]) -> str:
    """规格书 dict → YAML 文本(存档 + 导出)。"""
    return yaml.safe_dump(
        datasheet, allow_unicode=True, sort_keys=False, default_flow_style=False)
