"""W2.1 接线审计 — 本体门禁的三个接缝(类比 quality gate 的 _make_ingestor 审计):

1. builder 侧:所有插入路径经 ``_insert_kg`` 捕获实体/关系行 + ``template_path``
   落在任务上(红线:不改抽取链路,只在插入汇合点做数据管道);
2. _lake_kg 侧:build 收尾(execute_build 后、task 终态同步段)调用门禁;
3. 红线钉死:``arrow_lake`` 内 ontology validator 的调用方仅 gate.py(经
   _lake_kg 与 ontology API 两处业务入口),不进查询热路径。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]


# ---------------------------------------------------------------------------
# 1. builder capture
# ---------------------------------------------------------------------------


def test_kg_build_task_has_capture_fields() -> None:
    from arrow_lake.knowledge_graph.builder import KGBuildTask

    task = KGBuildTask(
        task_id="t", status="RUNNING", dataset_name="ds", total_chunks=0,
        processed_chunks=0, entity_count=0, relation_count=0,
        started_at=None, completed_at=None, error=None,
    )
    assert task.template_path is None
    assert task.inserted_entities == []
    assert task.inserted_relations == []
    assert task.ontology_result is None


def test_insert_kg_records_rows_on_task() -> None:
    """_insert_kg(单汇合点)在 task 传入时记录实际插入的实体/关系行。"""
    import asyncio
    from unittest.mock import AsyncMock

    from arrow_lake.config import HugeGraphConfig
    from arrow_lake.knowledge_graph.builder import KGBuilder, KGBuildTask
    from arrow_lake.knowledge_graph.extractor import (
        ExtractedEntity,
        ExtractedRelation,
        ExtractionResult,
    )

    client = AsyncMock()
    client.ensure_schema = AsyncMock()
    client.add_vertices = AsyncMock(
        side_effect=lambda vertices, **kw: [f"hg-{i}" for i in range(len(vertices))]
    )
    client.add_edges = AsyncMock(side_effect=lambda edges, **kw: len(edges))
    builder = KGBuilder(
        client, AsyncMock(),
        HugeGraphConfig(
            enabled=True, host="localhost", port=8089,
            graph_name="g", build_batch_size=10,
        ),
    )
    task = KGBuildTask(
        task_id="t", status="RUNNING", dataset_name="ds", total_chunks=1,
        processed_chunks=0, entity_count=0, relation_count=0,
        started_at=None, completed_at=None, error=None,
    )
    result = ExtractionResult(
        entities=(
            ExtractedEntity(name="Alice", entity_type="person",
                            properties=(("definition", "who"),)),
        ),
        relations=(
            ExtractedRelation(source="Alice", target="Acme",
                              relation_type="works_at"),
        ),
        raw_text="",
    )
    asyncio.run(builder._insert_kg(result, "kg_ds", {"c1": "1:c1"}, task=task))
    assert task.inserted_entities == [
        {"name": "Alice", "type": "person", "definition": "who"},
    ]
    assert task.inserted_relations == [
        {"source": "Alice", "target": "Acme", "type": "works_at"},
    ]


def test_insert_kg_task_optional_backward_compat() -> None:
    """task=None(默认)保持旧行为 — 不崩、不记录。"""
    import asyncio
    from unittest.mock import AsyncMock

    from arrow_lake.config import HugeGraphConfig
    from arrow_lake.knowledge_graph.builder import KGBuilder
    from arrow_lake.knowledge_graph.extractor import (
        ExtractedEntity,
        ExtractionResult,
    )

    client = AsyncMock()
    client.add_vertices = AsyncMock(
        side_effect=lambda vertices, **kw: [f"hg-{i}" for i in range(len(vertices))]
    )
    client.add_edges = AsyncMock(side_effect=lambda edges, **kw: len(edges))
    builder = KGBuilder(
        client, AsyncMock(),
        HugeGraphConfig(
            enabled=True, host="localhost", port=8089,
            graph_name="g", build_batch_size=10,
        ),
    )
    result = ExtractionResult(
        entities=(ExtractedEntity(name="A", entity_type="person"),),
        relations=(), raw_text="",
    )
    asyncio.run(builder._insert_kg(result, "kg_ds", {"c1": "1:c1"}))


def test_all_insert_sites_pass_task() -> None:
    """_execute_build 的全部 3 条路径(dataset/map_reduce/per-chunk)的
    ``self._insert_kg(`` 调用必须传 ``task=task``(新路径不漏捕获)。"""
    src = (ROOT / "arrow_lake/knowledge_graph/builder.py").read_text()
    calls = re.findall(r"self\._insert_kg\(", src)
    assert len(calls) == 3, f"expect 3 insert sites, found {len(calls)}"
    # 每个调用点括号内都要带 task=task(粗粒度:计数一致即足够)
    wired = re.findall(r"task=task", src)
    assert len(wired) >= 3, "insert site(s) missing task=task capture"


def test_execute_build_records_template_path() -> None:
    """三条抽取分支都要把解析出的模板路径放到任务上(门禁快照的数据源)。"""
    src = (ROOT / "arrow_lake/knowledge_graph/builder.py").read_text()
    assignments = re.findall(r"task\.template_path\s*=", src)
    assert len(assignments) >= 3, (
        f"expect template_path assignment in all 3 branches, found {len(assignments)}"
    )


# ---------------------------------------------------------------------------
# 2. _lake_kg wiring
# ---------------------------------------------------------------------------


def test_lake_kg_calls_gate_after_build() -> None:
    src = (ROOT / "arrow_lake/_lake_kg.py").read_text()
    assert "_ontology_gate" in src, "kg_build 收尾未接本体门禁"
    # 门禁结果必须进任务明细(kg_build_status 带违规摘要的持久面)
    assert '"ontology"' in src or "'ontology'" in src
    # enforce 拒绝必须翻 FAILED(计划 TDD: enforce reject → build 失败带明细)
    assert "TaskStatus.FAILED" in src


def test_kg_build_status_surfaces_ontology_summary() -> None:
    """kg_build_status 两条路径(builder task / TM 回落)都带 ontology 摘要。"""
    src = (ROOT / "arrow_lake/_lake_kg.py").read_text()
    i = src.index("async def kg_build_status")
    body = src[i : i + 3500]
    assert body.count('"ontology"') >= 2, "两条状态路径均需暴露 ontology 摘要"


def test_status_response_model_has_ontology_field() -> None:
    from arrow_lake.api.models.knowledge_graph import KGBuildStatusResponse

    assert "ontology" in KGBuildStatusResponse.model_fields


def test_config_defaults_shadow() -> None:
    from arrow_lake.config import ArrowLakeConfig

    cfg = ArrowLakeConfig()
    assert cfg.ontology.gate_mode == "shadow"
    assert cfg.ontology.validation_timeout_seconds > 0


def test_metric_registered() -> None:
    from arrow_lake.core import metrics

    assert metrics.ontology_check_total is not None
    # prometheus_client stores the base name (serves with a _total suffix)
    assert metrics.ontology_check_total._name.endswith("ontology_check")


# ---------------------------------------------------------------------------
# 2b. enforcement semantics on the mixin (shadow vs enforce)
# ---------------------------------------------------------------------------

_TEMPLATE_YAML = """
name: wire_template
ontology:
  entity_type_enum: [person, organization]
  required_entity_fields: [name, type]
output:
  entities:
    fields:
      - {name: name, type: str, required: true}
      - {name: type, type: str, required: true}
"""


class _MiniLake:
    """最小 _LakeKGMixin 宿主:只挂 _config(门禁唯一依赖)。"""

    def __init__(self, gate_mode: str) -> None:
        from types import SimpleNamespace

        self._config = SimpleNamespace(
            ontology=SimpleNamespace(
                gate_mode=gate_mode,
                validation_timeout_seconds=5.0,
                max_violations_reported=20,
            ),
        )

    async def _ontology_gate(self, dataset: str, kg_task, tm_task) -> None:
        from arrow_lake._lake_kg import _LakeKGMixin

        return await _LakeKGMixin._ontology_gate(self, dataset, kg_task, tm_task)


def _make_kg_task(template_path: Path | None, entities: list) -> object:
    from arrow_lake.knowledge_graph.builder import KGBuildStatus, KGBuildTask

    task = KGBuildTask(
        task_id="t1", status=KGBuildStatus.COMPLETED, dataset_name="ds",
        total_chunks=1, processed_chunks=1, entity_count=len(entities),
        relation_count=0, started_at=None, completed_at=None, error=None,
    )
    task.template_path = str(template_path) if template_path else None
    task.inserted_entities = entities
    task.inserted_relations = []
    return task


def _make_tm_task() -> object:
    from arrow_lake.api.tasks import BackgroundTask, TaskStatus

    return BackgroundTask(
        task_id="tm1", operation="kg_build", dataset_name="ds",
        status=TaskStatus.COMPLETED, progress=1.0, created_at="2026-08-24T00:00:00Z",
    )


@pytest.mark.asyncio()
async def test_shadow_counts_but_does_not_block(tmp_path: Path) -> None:
    """shadow + reject 级违规:build 任务保持 COMPLETED,只挂摘要(计划 TDD:
    shadow 只计数不拦,断言 build 正常完成 + 指标 +1)。"""
    from arrow_lake.api.tasks import TaskStatus
    from arrow_lake.core import metrics
    from arrow_lake.knowledge_graph.builder import KGBuildStatus

    tp = tmp_path / "wire_template.yaml"
    tp.write_text(_TEMPLATE_YAML, encoding="utf-8")

    bad_entities = [{"name": "R2", "type": "robot"}]
    kg_task = _make_kg_task(tp, bad_entities)
    tm_task = _make_tm_task()

    before = metrics.ontology_check_total.labels(dataset="ds", result="reject")._value.get()
    lake = _MiniLake("shadow")
    await lake._ontology_gate("ds", kg_task, tm_task)

    assert kg_task.status == KGBuildStatus.COMPLETED  # 不拦
    assert tm_task.status == TaskStatus.COMPLETED
    assert metrics.ontology_check_total.labels(dataset="ds", result="reject")._value.get() == before + 1
    assert tm_task.detail["ontology"]["outcome"] == "reject"
    assert kg_task.ontology_result["rejects"] >= 1


@pytest.mark.asyncio()
async def test_enforce_reject_flips_tasks_to_failed(tmp_path: Path) -> None:
    """enforce + reject:两份任务都翻 FAILED,error 带违规明细(计划 TDD:
    enforce reject → build 失败带违规明细)。"""
    from arrow_lake.api.tasks import TaskStatus
    from arrow_lake.knowledge_graph.builder import KGBuildStatus

    tp = tmp_path / "wire_template.yaml"
    tp.write_text(_TEMPLATE_YAML, encoding="utf-8")

    kg_task = _make_kg_task(tp, [{"name": "R2", "type": "robot"}])
    tm_task = _make_tm_task()

    lake = _MiniLake("enforce")
    await lake._ontology_gate("ds", kg_task, tm_task)

    assert kg_task.status == KGBuildStatus.FAILED
    assert "ontology gate rejected" in kg_task.error
    assert tm_task.status == TaskStatus.FAILED
    assert "robot" in tm_task.error  # 违规明细带值
    assert tm_task.detail["ontology"]["rejects"] >= 1


@pytest.mark.asyncio()
async def test_enforce_pass_keeps_completed(tmp_path: Path) -> None:
    from arrow_lake.api.tasks import TaskStatus
    from arrow_lake.knowledge_graph.builder import KGBuildStatus

    tp = tmp_path / "wire_template.yaml"
    tp.write_text(_TEMPLATE_YAML, encoding="utf-8")

    kg_task = _make_kg_task(tp, [{"name": "Alice", "type": "person"}])
    tm_task = _make_tm_task()

    lake = _MiniLake("enforce")
    await lake._ontology_gate("ds", kg_task, tm_task)

    assert kg_task.status == KGBuildStatus.COMPLETED
    assert tm_task.status == TaskStatus.COMPLETED
    assert tm_task.detail["ontology"]["outcome"] == "pass"


@pytest.mark.asyncio()
async def test_off_mode_zero_overhead(tmp_path: Path) -> None:
    from arrow_lake.core import metrics

    tp = tmp_path / "wire_template.yaml"
    tp.write_text(_TEMPLATE_YAML, encoding="utf-8")

    kg_task = _make_kg_task(tp, [{"name": "R2", "type": "robot"}])
    tm_task = _make_tm_task()

    before = metrics.ontology_check_total.labels(dataset="ds", result="reject")._value.get()
    lake = _MiniLake("off")
    await lake._ontology_gate("ds", kg_task, tm_task)

    assert kg_task.ontology_result is None
    assert "ontology" not in tm_task.detail
    assert metrics.ontology_check_total.labels(dataset="ds", result="reject")._value.get() == before


# ---------------------------------------------------------------------------
# 3. red line: validator call sites
# ---------------------------------------------------------------------------


def test_validator_call_sites_are_bounded() -> None:
    """红线:validate_snapshot 在 ontology 包外只允许 gate.py 引用
    (业务入口 = kg_build 收尾 + ontology API,二者都经 gate)。"""
    offenders: list[str] = []
    for py in (ROOT / "arrow_lake").rglob("*.py"):
        rel = py.relative_to(ROOT).as_posix()
        if rel.startswith("arrow_lake/ontology/") or "__pycache__" in rel:
            continue
        text = py.read_text(encoding="utf-8")
        if "validate_snapshot" in text or "ontology.validator" in text:
            offenders.append(rel)
    assert offenders == [], f"validator leaked into hot paths: {offenders}"
