"""W3.3 — release/datasheet.py 规格书生成(MS5 F5.7)。

字段(设计 §6 业务模板):id/version/category/lifecycle_status/schema/
scale/quality(五维+星级+κ)/labeling(标注覆盖/人数)/usage + changelog/
lance_version/released_at/released_by。YAML 为**生成物**(不手编,S9)。
"""

from __future__ import annotations

import pyarrow as pa
import yaml
from arrow_lake.release.datasheet import build_datasheet, datasheet_yaml

REPORT = {
    "id": 3, "total_score": 92.5, "star": 4, "admission": "silver",
    "verdict": "pass",
    "dimensions": {
        "relevance": {"score": 90.0, "source": "annotation", "details": {}},
        "accuracy": {"score": 85.0, "source": "adl",
                     "details": {"kappa": 0.85, "tasks": 4, "annotators": 2}},
        "completeness": {"score": 100.0, "source": "auto", "details": {}},
        "diversity": {"score": 90.0, "source": "auto", "details": {}},
        "timeliness": {"score": 100.0, "source": "auto", "details": {}},
    },
    "vetoes": [], "degraded": [],
    "spec": {"weights": {"accuracy": 0.35}, "critical": False},
}

TABLE = pa.table({
    "severity": pa.array(["high", "low", "high"]),
    "text": pa.array(["a", "b", "c"], pa.string()),
})


def _adl(rows: int = 2) -> pa.Table:
    return pa.table({
        "source_row_id": [f"r{i}" for i in range(rows)],
        "annotator_id": ["ann1", "ann2"][:rows] if rows <= 2 else ["ann1"] * rows,
        "adl_version": [1] * rows,
    })


def _build(**kw) -> dict:
    base = dict(
        dataset="alerts", tag="v1.2.0", lance_version=7, report=REPORT,
        contract=None, table=TABLE, adl=_adl(), changelog="修复两行",
        released_by="sysop", released_at="2026-08-30T12:00:00Z",
    )
    base.update(kw)
    return build_datasheet(**base)


def test_fields_complete() -> None:
    d = _build(category="project")
    assert d["id"] == "alerts" and d["version"] == "v1.2.0"
    assert d["category"] == "project" and d["lifecycle_status"] == "active"
    assert d["lance_version"] == 7 and d["changelog"] == "修复两行"
    assert d["released_by"] == "sysop"
    assert set(d["schema"]["columns"]) == {"severity", "text"}
    assert d["scale"] == {"rows": 3, "columns": 2}


def test_quality_summary_carries_kappa_and_dimensions() -> None:
    q = _build()["quality"]
    assert q["total_score"] == 92.5 and q["star"] == 4 and q["admission"] == "silver"
    assert q["kappa"] == 0.85
    assert q["dimensions"]["accuracy"] == 85.0
    assert q["vetoes"] == [] and q["degraded"] == []


def test_labeling_coverage_math() -> None:
    lab = _build()["labeling"]  # ADL 2 行标注 / 表 3 行
    assert lab["adl_rows"] == 2 and lab["annotators"] == 2
    assert lab["coverage"] == round(2 / 3, 4)
    # 空 ADL → 覆盖 0,不造假
    lab0 = _build(adl=None)["labeling"]
    assert lab0["coverage"] == 0.0 and lab0["adl_rows"] == 0


def test_contract_section_when_present() -> None:
    from arrow_lake.contract.schema import parse_contract
    contract = parse_contract(
        "dataset: alerts\ntables:\n  alerts:\n    columns:\n"
        "      - name: severity\n        required: true\n")
    d = _build(contract=contract)
    assert d["schema"]["contract_present"] is True
    assert d["schema"]["required_columns"] == ["severity"]
    d2 = _build()
    assert d2["schema"]["contract_present"] is False


def test_yaml_roundtrip() -> None:
    text = datasheet_yaml(_build())
    assert "id: alerts" in text and "version: v1.2.0" in text
    parsed = yaml.safe_load(text)
    assert parsed["quality"]["total_score"] == 92.5
    assert parsed["labeling"]["annotators"] == 2
