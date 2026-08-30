"""W3.1 — annotation/recover:LS task/annotation 解析 + webhook + watermark。

契约(设计 §6.1):
* ``parse_ls_annotation(task)``:task["annotations"] 每项 → 五段结构
  (objects/events spans + relations 三元组 + rules_applied + scenario);
  relation 的 from_id/to_id 反查 region text 还原主宾;skip(was_cancelled)
  与无 result 的标注跳过;
* ``parse_webhook(payload)``:ANNOTATION_CREATED/UPDATED → 单条记录;
  其他 action/缺字段 → None;
* ``incremental_tasks(tasks, watermark)``:task_id 增量 + 新 watermark。
"""

from __future__ import annotations

from arrow_lake.annotation.recover import (
    Span,
    Triple,
    incremental_tasks,
    parse_ls_annotation,
    parse_webhook,
)

REGIONS = [
    {"id": "e0", "from_name": "objects", "to_name": "text", "type": "labels",
     "value": {"start": 0, "end": 3, "text": "调压站", "labels": ["硬件"]}},
    {"id": "e1", "from_name": "events", "to_name": "text", "type": "labels",
     "value": {"start": 4, "end": 9, "text": "燃气泄漏事故", "labels": ["事故"]}},
    {"from_name": "scenario", "to_name": "text", "type": "choices",
     "value": {"choices": ["应急"]}},
    {"from_name": "rules_applied", "to_name": "text", "type": "textarea",
     "value": {"text": ["R1", "R2"]}},
    {"id": "r0", "type": "relation", "from_id": "e0", "to_id": "e1",
     "direction": "right", "labels": ["导致"]},
]


def _task(annotations: list[dict], *, task_id: int = 5, row_id: str = "r2") -> dict:
    return {
        "id": task_id,
        "data": {"text": "…", "row_id": row_id, "strategy": "uncertainty"},
        "annotations": annotations,
    }


def _ann(result: list[dict], *, completed_by: int = 7, cancelled: bool = False) -> dict:
    return {
        "id": 11, "result": result, "completed_by": completed_by,
        "created_at": "2026-08-29T08:00:00Z",
        "was_cancelled": cancelled, "ground_truth": False,
    }


class TestParseTask:
    def test_five_sections_parsed(self):
        recs = parse_ls_annotation(_task([_ann(REGIONS)]))
        assert len(recs) == 1
        rec = recs[0]
        assert rec.row_id == "r2" and rec.annotator_id == "7"
        assert rec.objects == (Span("硬件", 0, 3, "调压站"),)
        assert rec.events == (Span("事故", 4, 9, "燃气泄漏事故"),)
        assert rec.relations == (Triple("调压站", "导致", "燃气泄漏事故"),)
        assert rec.rules_applied == ("R1", "R2")
        assert rec.scenario == "应急"

    def test_relation_to_unknown_region_dropped(self):
        bad = [*REGIONS[:1], {"id": "r9", "type": "relation",
                              "from_id": "e0", "to_id": "ghost", "labels": ["导致"]}]
        rec = parse_ls_annotation(_task([_ann(bad)]))[0]
        assert rec.relations == ()

    def test_cancelled_annotation_skipped(self):
        recs = parse_ls_annotation(_task([_ann(REGIONS, cancelled=True)]))
        assert recs == []

    def test_empty_result_skipped(self):
        recs = parse_ls_annotation(_task([_ann([])]))
        assert recs == []

    def test_multiple_annotators_multiple_records(self):
        recs = parse_ls_annotation(_task([
            _ann(REGIONS, completed_by=7),
            _ann(REGIONS, completed_by=8),
        ]))
        assert {r.annotator_id for r in recs} == {"7", "8"}

    def test_task_without_row_id_defaults_from_task_id(self):
        task = _task([_ann(REGIONS)])
        task["data"] = {"text": "…"}
        rec = parse_ls_annotation(task)[0]
        assert rec.row_id  # 有兜底标识,不崩


class TestWebhook:
    def test_annotation_created_parsed(self):
        payload = {
            "action": "ANNOTATION_CREATED",
            "annotation": _ann(REGIONS),
            "task": _task([]),
        }
        rec = parse_webhook(payload)
        assert rec is not None and rec.annotator_id == "7"

    def test_unrelated_action_none(self):
        assert parse_webhook({"action": "TASK_CREATED"}) is None
        assert parse_webhook({"action": "PROJECT_CREATED"}) is None

    def test_missing_fields_none(self):
        assert parse_webhook({"action": "ANNOTATION_CREATED"}) is None
        assert parse_webhook({}) is None


class TestWatermark:
    def _tasks(self, ids_annos):
        return [
            _task([_ann(REGIONS, completed_by=7)] if ann else [], task_id=tid)
            for tid, ann in ids_annos
        ]

    def test_incremental_filter_and_new_watermark(self):
        tasks = self._tasks([(1, True), (2, True), (5, True), (9, True)])
        batch, wm = incremental_tasks(tasks, watermark=2)
        assert [t["id"] for t in batch] == [5, 9]
        assert wm == 9

    def test_unannotated_task_does_not_advance_watermark(self):
        """W5 live 实证修:无标注 task 不推进——后到标注仍可回收。"""
        tasks = self._tasks([(1, True), (5, False), (9, False)])
        batch, wm = incremental_tasks(tasks, watermark=0)
        assert [t["id"] for t in batch] == [1]
        assert wm == 1  # 5/9 未标注,不推

    def test_late_annotation_still_recovered(self):
        """scheduler 先看(无标注)→ 标注者后标 → 下轮拉得到。"""
        tasks = self._tasks([(5, True), (9, False)])
        _, wm = incremental_tasks(tasks, watermark=0)
        assert wm == 5
        # (9 后来被标注)
        tasks2 = self._tasks([(5, True), (9, True)])
        batch, wm2 = incremental_tasks(tasks2, watermark=wm)
        assert [t["id"] for t in batch] == [9]
        assert wm2 == 9

    def test_no_new_tasks_keeps_watermark(self):
        tasks = self._tasks([(3, False)])
        batch, wm = incremental_tasks(tasks, watermark=3)
        assert batch == [] and wm == 3

    def test_empty_input(self):
        assert incremental_tasks([], watermark=0) == ([], 0)
