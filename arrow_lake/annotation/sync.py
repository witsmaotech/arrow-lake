"""F4.5/F4.6 — 回收同步核心 + 30s scheduler + 仲裁 task 生成(v1.11.3 W4.0)。

``recover_one`` 是手动端点(POST /annotation/recover)与后台 scheduler
共用的回收核心:增量拉取 → 五段解析 → 仲裁 → ADL 版本化写回 →
watermark 推进 → **分歧样本的仲裁 task 生成**(设计 §7.2)。

仲裁闭环(纯 append 语义,不改已有 ADL 行):

1. 分歧 row → 同一 LS project import 一个仲裁 task
   (``data.strategy="arbitration"``,专家看到原文重标);
2. 专家标注后在 LS 标记 ground truth;
3. 下一轮回收:专家标注(ground_truth=True)免检 → ``approved``
   (:func:`quality.adjudicate` 的免检分支)→ ADL 新行,仲裁完成。

幂等:该 row 已存在仲裁 task(fresh 里 ``strategy=="arbitration"``)→
跳过;text 缺失 → 跳过(专家没有可看的文本)。

scheduler 连续 ``max_failures`` 轮失败熔断停(LS 挂了不该拖着日志刷屏;
治理旁路绝不能影响数据面——GravitinoSyncScheduler 同款纪律)。
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from typing import Any

from arrow_lake.annotation.adl import build_adl_batch, write_adl
from arrow_lake.annotation.dispatch import LSClient
from arrow_lake.annotation.quality import adjudicate, project_kappa
from arrow_lake.annotation.recover import (
    RecoveredAnnotation,
    incremental_tasks,
    parse_ls_annotation,
)
from arrow_lake.config.annotation import AnnotationConfig

__all__ = [
    "AnnotationRecoverScheduler",
    "arbitration_tasks",
    "recover_one",
]

logger = logging.getLogger(__name__)


def _existing_adl_state(
    lake: Any, dataset: str
) -> tuple[set[str], dict[tuple[str, str], int]]:
    try:
        table = lake.read_dataset(f"{dataset}_adl")
    except Exception:  # 表不存在 = 首次回收
        return set(), {}
    ids: set[str] = set()
    versions: dict[tuple[str, str], int] = {}
    for row in table.select(
        ["adl_id", "source_row_id", "annotator_id", "adl_version"]
    ).to_pylist():
        ids.add(row["adl_id"])
        key = (row["source_row_id"], row["annotator_id"])
        versions[key] = max(versions.get(key, 0), int(row["adl_version"] or 0))
    return ids, versions


def arbitration_tasks(
    *,
    fresh: list[dict[str, Any]],
    recovered: list[RecoveredAnnotation],
    verdicts: dict[str, Any],
) -> list[dict[str, Any]]:
    """分歧 row → 仲裁 task 批(幂等:text 缺失或已有仲裁 task 跳过)。"""
    # row → 原文(优先非仲裁 task 的 data.text)
    text_of: dict[str, str] = {}
    has_arbitration: set[str] = set()
    for task in fresh:
        data = task.get("data") or {}
        row_id = str(data.get("row_id") or f"task-{task.get('id', 0)}")
        if str(data.get("strategy")) == "arbitration":
            has_arbitration.add(row_id)
        elif data.get("text"):
            text_of.setdefault(row_id, str(data["text"]))

    out: list[dict[str, Any]] = []
    for row_id, verdict in verdicts.items():
        if verdict.status != "arbitration" or row_id in has_arbitration:
            continue
        text = text_of.get(row_id)
        if not text:
            continue
        out.append({"data": {
            "text": text, "row_id": row_id, "strategy": "arbitration",
        }})
    return out


def recover_one(
    *,
    store: Any,
    lake: Any,
    config: AnnotationConfig,
    project_name: str,
    ls_client: LSClient | None = None,
    generate_arbitration: bool = True,
) -> dict:
    """一个项目的完整回收(scheduler 与手动端点共用);失败上抛。"""
    import uuid as _uuid

    from arrow_lake._lake_ingest import _DeadLetterStorageAdapter

    rec = store.get_project(project_name)
    if rec is None:
        raise LookupError(f"no annotation project {project_name!r}")
    ls_project_id = rec.get("ls_project_id")
    if not ls_project_id:
        raise LookupError(f"project {project_name!r} has no LS binding")

    client = ls_client or LSClient(config.ls_url, config.ls_api_token)
    # W5 live:export 全量(列表视图裁剪 annotations);大项目成本登记
    tasks = client.export_tasks(ls_project_id)
    watermark = int(rec.get("recover_watermark") or 0)
    fresh, new_watermark = incremental_tasks(tasks, watermark=watermark)

    recovered = [a for t in fresh for a in parse_ls_annotation(t)]
    by_task: dict[str, list[RecoveredAnnotation]] = {}
    for ann in recovered:
        by_task.setdefault(ann.row_id, []).append(ann)
    verdicts = adjudicate(by_task)

    existing_ids, group_versions = _existing_adl_state(lake, rec["dataset"])
    batch_id = f"rec-{_uuid.uuid4().hex[:8]}"
    table, written = build_adl_batch(
        dataset=rec["dataset"], recovered=recovered, adjudications=verdicts,
        batch_id=batch_id, existing_adl_ids=existing_ids,
        group_versions=group_versions,
    )
    if written:
        write_adl(_DeadLetterStorageAdapter(lake._get_storage()), rec["dataset"], table)
    store.set_watermark(project_name, new_watermark)

    arb_generated = 0
    if generate_arbitration:
        arb = arbitration_tasks(fresh=fresh, recovered=recovered, verdicts=verdicts)
        if arb:
            client.import_tasks(ls_project_id, arb)
            arb_generated = len(arb)

    counts = {"approved": 0, "arbitration": 0, "pending": 0}
    for verdict in verdicts.values():
        counts[verdict.status] = counts.get(verdict.status, 0) + 1
    return {
        "project": project_name,
        "tasks_seen": len(fresh),
        "annotations_recovered": len(recovered),
        "adl_rows_written": written,
        "review": counts,
        "kappa": project_kappa(by_task),
        "watermark": new_watermark,
        "batch_id": batch_id,
        "arbitration_tasks_generated": arb_generated,
    }


class AnnotationRecoverScheduler:
    """30s 后台轮询:active+bound 项目逐个 :func:`recover_one`(S9 主通道)。"""

    def __init__(
        self,
        store: Any,
        lake: Any,
        config: AnnotationConfig,
        *,
        interval: int = 30,
        recover: Callable[..., dict] = recover_one,
        max_failures: int = 5,
    ) -> None:
        self._store = store
        self._lake = lake
        self._config = config
        self._interval = max(interval, 5)
        self._recover = recover
        self._max_failures = max_failures
        self._consecutive_failures = 0
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run_loop, name="annotation-recover", daemon=True)
        self._thread.start()
        logger.info("annotation_recover_scheduler_started interval=%s", self._interval)

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=self._interval + 5)
            self._thread = None

    def _cycle(self) -> bool:
        """单轮:全部 eligible 项目各 recover 一次;全成→True(重置熔断计数)。"""
        projects = [
            p for p in self._store.list_projects()
            if p.get("status") == "active" and p.get("ls_project_id")
        ]
        ok = True
        for p in projects:
            try:
                summary = self._recover(
                    store=self._store, lake=self._lake, config=self._config,
                    project_name=p["name"])
                logger.debug("annotation_recover_cycle %s", summary)
            except Exception as exc:  # 单项目失败不拖垮整轮
                ok = False
                logger.warning(
                    "annotation_recover_failed project=%s error=%s", p["name"], exc)
        return ok

    def _tick(self) -> bool:
        """一轮 + 熔断计数;熔断触发 → 置 stop 并返回 False。"""
        try:
            ok = self._cycle()
        except Exception:  # cycle 自身异常(LS 构造等)
            ok = False
        if ok:
            self._consecutive_failures = 0
            return True
        self._consecutive_failures += 1
        if self._consecutive_failures >= self._max_failures:
            logger.error(
                "annotation_recover_circuit_open failures=%s "
                "(LS unreachable? fix and restart)", self._consecutive_failures)
            self._stop_event.set()
            return False
        return False

    def _run_loop(self) -> None:
        while not self._stop_event.is_set():
            self._tick()  # 熔断时 _tick 自行置 stop_event
            if self._stop_event.is_set():
                return
            self._stop_event.wait(self._interval)
