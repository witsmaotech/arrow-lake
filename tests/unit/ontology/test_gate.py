"""W2.1 — ontology gate at KG build finish (MS1 F1.3).

契约(实施计划 §4 W2.1):
* ``off`` 零开销(不读模板、不计指标、返回 None);
* 无模板/模板不可读 → ``skip``(计数);
* shadow 只计数不拦(enforce 语义在 _lake_kg._ontology_gate 测试,见
  test_gate_wiring.py);
* pyshacl 超时 → fail-closed reject(校验不可用不放行);
* 指标 ``arrow_lake_ontology_check_total{dataset,result}``。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from arrow_lake.core import metrics

# --- fixture data -----------------------------------------------------------

TEMPLATE_YAML = """
name: gate_test_template
ontology:
  entity_type_enum: [person, organization]
  relation_type_enum: [works_at]
  type_pairs:
    - [person, works_at, organization]
  required_entity_fields: [name, type, definition]
  warn_fields: [definition]
output:
  entities:
    fields:
      - {name: name, type: str, required: true}
      - {name: type, type: str, required: true}
      - {name: definition, type: str, required: true}
  relations:
    fields:
      - {name: source, type: str, required: true}
      - {name: target, type: str, required: true}
      - {name: type, type: str, required: true}
"""

ENTITIES_OK = [
    {"name": "Alice", "type": "person", "definition": "a person"},
    {"name": "Acme", "type": "organization", "definition": "a company"},
]
RELATIONS_OK = [{"source": "Alice", "type": "works_at", "target": "Acme"}]


@pytest.fixture
def template_path(tmp_path: Path) -> Path:
    p = tmp_path / "gate_test_template.yaml"
    p.write_text(TEMPLATE_YAML, encoding="utf-8")
    return p


def _metric_value(dataset: str, result: str) -> float:
    return metrics.ontology_check_total.labels(dataset=dataset, result=result)._value.get()


# --- off mode ---------------------------------------------------------------


@pytest.mark.asyncio()
async def test_off_mode_returns_none_without_metric(template_path: Path) -> None:
    from arrow_lake.ontology.gate import run_ontology_gate

    before = _metric_value("ds_off", "reject")
    result = await run_ontology_gate(
        "ds_off", ENTITIES_OK, RELATIONS_OK, str(template_path), mode="off",
    )
    assert result is None
    assert _metric_value("ds_off", "reject") == before


# --- skip path --------------------------------------------------------------


@pytest.mark.asyncio()
async def test_missing_template_counts_skip() -> None:
    from arrow_lake.ontology.gate import run_ontology_gate

    before = _metric_value("ds_skip", "skip")
    result = await run_ontology_gate(
        "ds_skip", ENTITIES_OK, RELATIONS_OK, None, mode="shadow",
    )
    assert result is not None
    assert result.outcome == "skip"
    assert result.rejects == 0
    assert _metric_value("ds_skip", "skip") == before + 1


@pytest.mark.asyncio()
async def test_unreadable_template_counts_skip(tmp_path: Path) -> None:
    from arrow_lake.ontology.gate import run_ontology_gate

    result = await run_ontology_gate(
        "ds_missing", [], [], str(tmp_path / "nope.yaml"), mode="shadow",
    )
    assert result.outcome == "skip"


# --- pass / warn / reject ---------------------------------------------------


@pytest.mark.asyncio()
async def test_valid_snapshot_passes(template_path: Path) -> None:
    from arrow_lake.ontology.gate import run_ontology_gate

    before = _metric_value("ds_ok", "pass")
    result = await run_ontology_gate(
        "ds_ok", ENTITIES_OK, RELATIONS_OK, str(template_path), mode="shadow",
    )
    assert result.outcome == "pass"
    assert result.rejects == 0
    assert result.warns == 0
    assert _metric_value("ds_ok", "pass") == before + 1


@pytest.mark.asyncio()
async def test_enum_violation_rejects(template_path: Path) -> None:
    from arrow_lake.ontology.gate import run_ontology_gate

    bad = [{"name": "R2", "type": "robot", "definition": "??"}]
    before = _metric_value("ds_bad", "reject")
    result = await run_ontology_gate(
        "ds_bad", bad, [], str(template_path), mode="shadow",
    )
    assert result.outcome == "reject"
    assert result.rejects >= 1
    first = result.violations[0]
    assert first["level"] == "reject"
    assert first["path"] == "type"
    assert _metric_value("ds_bad", "reject") == before + 1


@pytest.mark.asyncio()
async def test_empty_definition_warns_not_rejects(template_path: Path) -> None:
    """definition 缺(捕获层把空 definition 记为缺)→ Warning 级,观察不拦。"""
    from arrow_lake.ontology.gate import run_ontology_gate

    rows = [{"name": "Alice", "type": "person"}]  # definition 缺失
    before = _metric_value("ds_warn", "warn")
    result = await run_ontology_gate(
        "ds_warn", rows, [], str(template_path), mode="shadow",
    )
    assert result.outcome == "warn"
    assert result.warns >= 1
    assert result.rejects == 0
    assert _metric_value("ds_warn", "warn") == before + 1


# --- fail-closed timeout ----------------------------------------------------


@pytest.mark.asyncio()
async def test_validation_timeout_fails_closed(template_path: Path, monkeypatch) -> None:
    import time

    from arrow_lake.ontology import gate as gate_mod

    def _slow_validate(entities, relations, shapes, **kwargs):
        time.sleep(1.0)
        return []

    monkeypatch.setattr(gate_mod, "validate_snapshot", _slow_validate)
    before = _metric_value("ds_slow", "reject")
    result = await gate_mod.run_ontology_gate(
        "ds_slow", ENTITIES_OK, RELATIONS_OK, str(template_path),
        mode="shadow", timeout_seconds=0.05,
    )
    assert result.outcome == "reject"
    assert result.rejects == 1
    assert "fail-closed" in result.violations[0]["message"]
    assert _metric_value("ds_slow", "reject") == before + 1


# --- detail / error helpers -------------------------------------------------


@pytest.mark.asyncio()
async def test_to_detail_shape(template_path: Path) -> None:
    from arrow_lake.ontology.gate import run_ontology_gate

    result = await run_ontology_gate(
        "ds_detail", ENTITIES_OK, RELATIONS_OK, str(template_path), mode="shadow",
    )
    detail = result.to_detail()
    assert detail["mode"] == "shadow"
    assert detail["outcome"] == "pass"
    assert detail["rejects"] == 0
    assert detail["warns"] == 0
    assert isinstance(detail["violations"], list)


@pytest.mark.asyncio()
async def test_violations_capped_at_max(template_path: Path) -> None:
    from arrow_lake.ontology.gate import run_ontology_gate

    bad = [{"name": f"e{i}", "type": "robot", "definition": "x"} for i in range(10)]
    result = await run_ontology_gate(
        "ds_cap", bad, [], str(template_path), mode="shadow", max_violations=3,
    )
    assert len(result.violations) == 3
    # 明细截断但计数保留全量
    assert result.rejects == len(bad)


def test_enforcement_error_only_when_rejects() -> None:
    from arrow_lake.ontology.gate import OntologyGateResult, enforcement_error

    ok = OntologyGateResult(
        mode="enforce", outcome="pass", rejects=0, warns=0,
        violations=[], duration_seconds=0.1,
    )
    assert enforcement_error(ok) is None

    bad = OntologyGateResult(
        mode="enforce", outcome="reject", rejects=2, warns=0,
        violations=[
            {"level": "reject", "focus": "R2", "path": "type", "value": "robot",
             "message": "not in enum"},
            {"level": "reject", "focus": "R3", "path": "type", "value": "cyborg",
             "message": "not in enum"},
        ],
        duration_seconds=0.1,
    )
    err = enforcement_error(bad)
    assert err is not None
    assert "2" in err
    assert "not in enum" in err
