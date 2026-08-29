"""W4.0 — annotation/sync:recover_one 共用核心 + 仲裁 task 生成 + scheduler。

契约(设计 §7.2 / S9):
* ``recover_one`` = 手动端点与 30s scheduler 共用的回收核心
  (增量→解析→仲裁→ADL→watermark→**仲裁 task 生成**);
* 仲裁 task:分歧 row → 同 project import 新 task
  (``data.strategy="arbitration"``,专家重标后在 LS 设 ground truth →
  下轮回收免检 approved,闭环);幂等:该 row 已有仲裁 task → 跳过;
  text 缺失 → 跳过;
* ``AnnotationRecoverScheduler``:30s 线程遍历 active+bound 项目;
  连续 5 轮失败熔断停(GravitinoSyncScheduler 同款)。
"""

from __future__ import annotations

from arrow_lake.annotation.sync import (
    AnnotationRecoverScheduler,
    arbitration_tasks,
    recover_one,
)
from arrow_lake.config.annotation import AnnotationConfig

CONFIG = AnnotationConfig(ls_url="http://ls", ls_api_token="tok")

REGIONS_A = [
    {"id": "e0", "from_name": "objects", "to_name": "text", "type": "labels",
     "value": {"start": 0, "end": 3, "text": "调压站", "labels": ["硬件"]}},
    {"from_name": "scenario", "to_name": "text", "type": "choices",
     "value": {"choices": ["应急"]}},
]
REGIONS_B = [  # scenario 不同 → 分歧
    {"id": "e0", "from_name": "objects", "to_name": "text", "type": "labels",
     "value": {"start": 0, "end": 3, "text": "调压站", "labels": ["硬件"]}},
    {"from_name": "scenario", "to_name": "text", "type": "choices",
     "value": {"choices": ["常规"]}},
]


def _task(task_id, annotations, *, strategy="uncertainty", row_id="r1", text="调压站异常"):
    return {"id": task_id, "data": {"text": text, "row_id": row_id, "strategy": strategy},
            "annotations": annotations}


def _ann(result, completed_by):
    return {"id": 1, "result": result, "completed_by": completed_by,
            "created_at": "t", "was_cancelled": False, "ground_truth": False}


def _parse(task):
    from arrow_lake.annotation.recover import parse_ls_annotation

    return parse_ls_annotation(task)


class TestArbitrationTasks:
    def test_discordant_row_yields_arbitration_task(self):
        from arrow_lake.annotation.quality import adjudicate

        task = _task(1, [_ann(REGIONS_A, 7), _ann(REGIONS_B, 8)])
        recovered = [a for t in [task] for a in _parse(t)]
        by_task = {a.row_id: [x for x in recovered if x.row_id == a.row_id] for a in recovered}
        verdicts = adjudicate(by_task)
        out = arbitration_tasks(fresh=[task], recovered=recovered, verdicts=verdicts)
        assert len(out) == 1
        assert out[0]["data"]["strategy"] == "arbitration"
        assert out[0]["data"]["row_id"] == "r1"
        assert out[0]["data"]["text"] == "调压站异常"

    def test_existing_arbitration_task_deduped(self):
        from arrow_lake.annotation.quality import adjudicate

        original = _task(1, [_ann(REGIONS_A, 7), _ann(REGIONS_B, 8)])
        existing_arb = _task(
            2, [], strategy="arbitration", row_id="r1")  # 上轮已生成
        fresh = [original, existing_arb]
        recovered = [a for t in fresh for a in _parse(t)]
        by_task = {}
        for a in recovered:
            by_task.setdefault(a.row_id, []).append(a)
        verdicts = adjudicate(by_task)
        out = arbitration_tasks(fresh=fresh, recovered=recovered, verdicts=verdicts)
        assert out == []  # 已有 → 不重复

    def test_approved_rows_no_task(self):
        from arrow_lake.annotation.quality import adjudicate

        task = _task(1, [_ann(REGIONS_A, 7), _ann(REGIONS_A, 8)])  # 全同
        recovered = _parse(task)
        verdicts = adjudicate({"r1": recovered})
        assert arbitration_tasks(fresh=[task], recovered=recovered, verdicts=verdicts) == []


class FakeStore:
    def __init__(self, projects: list[dict]) -> None:
        self._projects = {p["name"]: p for p in projects}

    def get_project(self, name):
        return self._projects.get(name)

    def list_projects(self):
        return list(self._projects.values())

    def set_watermark(self, name, wm):
        self._projects[name]["recover_watermark"] = wm
        return True

    def set_ls_project_id(self, name, i):
        self._projects[name]["ls_project_id"] = i


class FakeLSCl:
    """记录 import;tasks 可设。"""

    def __init__(self, tasks) -> None:
        self.tasks = tasks
        self.imported: list[tuple[int, list]] = []

    def list_tasks(self, project_id, **kw):
        return {"tasks": self.tasks, "total": len(self.tasks)}

    def import_tasks(self, project_id, tasks):
        self.imported.append((project_id, tasks))
        return {"task_ids": list(range(len(tasks)))}


class FakeLakeLite:
    def __init__(self) -> None:
        self.audits = []

    def read_dataset(self, name, **kw):
        raise RuntimeError("no adl")

    def audit_record(self, event, **kw):
        self.audits.append((event, kw.get("payload") or {}))

    class _S:
        def dataset_exists(self, name):
            return False

        def append_dataset(self, name, t):
            pass

        def create_dataset(self, name, t):
            pass

    def _get_storage(self):
        return FakeLakeLite._S()


def _project(**over):
    base = {"name": "p1", "dataset": "ds1", "labeling_config": "<View/>",
            "ls_project_id": 42, "status": "active", "recover_watermark": 0}
    base.update(over)
    return base


class TestRecoverOne:
    def test_full_chain_with_arbitration_generation(self):
        ls = FakeLSCl([_task(1, [_ann(REGIONS_A, 7), _ann(REGIONS_B, 8)])])
        store = FakeStore([_project()])
        lake = FakeLakeLite()
        summary = recover_one(
            store=store, lake=lake, config=CONFIG, project_name="p1", ls_client=ls)
        assert summary["review"]["arbitration"] == 1
        assert summary["arbitration_tasks_generated"] == 1
        assert ls.imported and ls.imported[0][0] == 42
        assert ls.imported[0][1][0]["data"]["strategy"] == "arbitration"
        assert store._projects["p1"]["recover_watermark"] == 1

    def test_concordant_no_arbitration_import(self):
        ls = FakeLSCl([_task(1, [_ann(REGIONS_A, 7), _ann(REGIONS_A, 8)])])
        store = FakeStore([_project()])
        summary = recover_one(
            store=store, lake=FakeLakeLite(), config=CONFIG,
            project_name="p1", ls_client=ls)
        assert summary["arbitration_tasks_generated"] == 0
        assert ls.imported == []


class TestScheduler:
    def test_cycle_recovers_active_bound_projects(self):
        store = FakeStore([
            _project(name="p1"),
            _project(name="p2", ls_project_id=None),   # 未绑定 → 跳过
            _project(name="p3", status="closed"),      # 关闭 → 跳过
        ])
        calls: list[str] = []

        def fake_recover(store, lake, config, project_name, ls_client=None):
            calls.append(project_name)
            return {"project": project_name}

        sched = AnnotationRecoverScheduler(
            store, FakeLakeLite(), CONFIG, interval=5, recover=fake_recover)
        sched._cycle()  # 单轮(不 start 线程)
        assert calls == ["p1"]

    def test_circuit_breaker_stops_after_failures(self):
        store = FakeStore([_project()])
        boom = RuntimeError("LS down")

        def failing(*a, **kw):
            raise boom

        sched = AnnotationRecoverScheduler(
            store, FakeLakeLite(), CONFIG, interval=5, recover=failing,
            max_failures=3)
        assert sched._tick() is False
        assert sched._tick() is False
        assert sched._tick() is False  # 第 3 次失败 → 熔断
        assert sched._consecutive_failures == 3
        assert sched._stop_event.is_set()  # 熔断置停
