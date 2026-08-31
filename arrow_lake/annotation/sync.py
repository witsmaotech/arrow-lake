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

import contextlib
import json
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
    "RecoverInProgress",
    "arbitration_tasks",
    "project_status",
    "recover_one",
]

logger = logging.getLogger(__name__)


class RecoverInProgress(RuntimeError):
    """另一进程/worker 正在回收同一项目(H2 跨进程互斥)。"""


# --- H2:跨进程回收互斥(4 worker 各起 scheduler/手动端点并发) -----------
# Redis SETNX EX;Redis 不可用 → fail-open 返回 None(无锁,维持单机旧
# 语义;hermetic 测试无 Redis 也走通)。TTL 兜底防持锁进程死亡死锁。

_RECOVER_LOCK_TTL_SECONDS = 60


def _acquire_recover_lock(project_name: str):
    """返回 (release_fn) 持锁;False=被他方持有;None=无 Redis(fail-open)。"""
    try:
        from arrow_lake._lake_ingest import _embed_redis_client

        client = _embed_redis_client()
        if client is None:
            return None
        key = f"arrow-lake:recover:{project_name}"
        token = f"{threading.get_ident():x}-{id(project_name):x}"
        if not client.set(key, token, nx=True, ex=_RECOVER_LOCK_TTL_SECONDS):
            return False
        def _release() -> None:
            with contextlib.suppress(Exception):
                if client.get(key) == token:  # 只删自己的锁(防误删续期后他人锁)
                    client.delete(key)
        return _release
    except Exception:
        return None


def _release_quietly(release) -> None:
    if callable(release):
        with contextlib.suppress(Exception):
            release()


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


def project_status(
    *,
    store: Any,
    config: AnnotationConfig,
    project_name: str,
    ls_client: LSClient | None = None,
) -> dict:
    """项目当前看板(只读):全量 LS tasks 的裁决统计 + Fleiss κ。

    与 :func:`recover_one` 同源聚合(export→parse→adjudicate),但
    **不写 ADL、不生成仲裁、不动 watermark**——后台自动回收的结果
    因此在 UI 可见,无需触发一次手动回收副作用。未绑定 LS 的项目
    返回 ``bound: False`` 骨架而非报错(派发前看板可用)。
    """
    from arrow_lake.annotation.quality import adjudicate, project_kappa
    from arrow_lake.annotation.recover import parse_ls_annotation

    rec = store.get_project(project_name)
    if rec is None:
        raise LookupError(f"no annotation project {project_name!r}")
    ls_project_id = rec.get("ls_project_id")
    if not ls_project_id:
        return {"project": project_name, "bound": False, "tasks_total": 0,
                "annotated_rows": 0,
                "review": {"approved": 0, "arbitration": 0, "pending": 0},
                "kappa": None,
                "watermark": int(rec.get("recover_watermark") or 0)}

    client = ls_client or LSClient(config.ls_url, config.ls_api_token)
    tasks = client.export_tasks(ls_project_id)
    by_task: dict[str, list[Any]] = {}
    for t in tasks:
        for ann in parse_ls_annotation(t):
            by_task.setdefault(ann.row_id, []).append(ann)
    verdicts = adjudicate(
        by_task, min_annotators=config.adjudicate_min_annotators)
    counts = {"approved": 0, "arbitration": 0, "pending": 0}
    for verdict in verdicts.values():
        counts[verdict.status] = counts.get(verdict.status, 0) + 1
    return {
        "project": project_name, "bound": True,
        "tasks_total": len(tasks), "annotated_rows": len(by_task),
        "review": counts, "kappa": project_kappa(by_task),
        "watermark": int(rec.get("recover_watermark") or 0),
    }


def recover_one(
    *,
    store: Any,
    lake: Any,
    config: AnnotationConfig,
    project_name: str,
    ls_client: LSClient | None = None,
    generate_arbitration: bool = True,
) -> dict:
    """一个项目的完整回收(scheduler 与手动端点共用);失败上抛。

    H2:入口持 Redis 跨进程互斥(4 worker 各起 scheduler 并发回收同项目
    → 丢标注/重复行/仲裁重复导入);他方持锁 → :class:`RecoverInProgress`。
    C1:增量判据 = task id 前进 ∪ 已回收 task 标注数增长(第二标注者)。
    M4(质量):watermark 先推、仲裁 task 后导的顺序保留——仲裁 import
    失败不回滚 watermark 时,新判据下该批 task 的 counts 已推进,但
    分歧裁决下一轮仍会因 verdicts 稳定而跳过;仲裁补生成见 webhook 兜底。
    """
    import uuid as _uuid

    from arrow_lake._lake_ingest import _DeadLetterStorageAdapter

    rec = store.get_project(project_name)
    if rec is None:
        raise LookupError(f"no annotation project {project_name!r}")
    ls_project_id = rec.get("ls_project_id")
    if not ls_project_id:
        raise LookupError(f"project {project_name!r} has no LS binding")

    lock = _acquire_recover_lock(project_name)
    if lock is False:
        raise RecoverInProgress(
            f"another recover is running for {project_name!r} (lock held)")
    try:
        client = ls_client or LSClient(config.ls_url, config.ls_api_token)
        # W5 live:export 全量(列表视图裁剪 annotations);大项目成本登记
        tasks = client.export_tasks(ls_project_id)
        watermark = int(rec.get("recover_watermark") or 0)
        prev_counts: dict[str, int] = {}
        raw_counts = rec.get("recovered_counts")
        if isinstance(raw_counts, str) and raw_counts:
            with contextlib.suppress(ValueError, TypeError):
                prev_counts = {str(k): int(v)
                               for k, v in json.loads(raw_counts).items()}
        fresh, new_watermark, new_counts = incremental_tasks(
            tasks, watermark=watermark, recovered_counts=prev_counts,
        )

        recovered = [a for t in fresh for a in parse_ls_annotation(t)]
        by_task: dict[str, list[RecoveredAnnotation]] = {}
        for ann in recovered:
            by_task.setdefault(ann.row_id, []).append(ann)
        verdicts = adjudicate(
            by_task, min_annotators=config.adjudicate_min_annotators)

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
        store.set_recovered_counts(project_name, json.dumps(new_counts))

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
    finally:
        _release_quietly(lock)


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
        # M13(四维 review):LS project 计数签名——30s 周期先轻量 get_project
        # 比对,无变化跳过全量 export(万级 task 的 MB 级下载+解析)。
        # task_number 与 annotation_number **两者都在**才可比:标注数变化
        # (C1 第二标注者)必须穿透大门;字段缺失 → 不跳过(安全降级)。
        self._ls_counts: dict[str, tuple[int, int]] = {}

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

    def _project_signature(self, project: dict[str, Any]) -> tuple[int, int] | None:
        """LS 计数签名;不可得(字段缺/LS 异常)→ None = 不跳过。"""
        if not (self._config.ls_url and self._config.ls_api_token):
            return None
        try:
            client = LSClient(self._config.ls_url, self._config.ls_api_token)
            d = client.get_project(int(project["ls_project_id"])) or {}
            sig = (d.get("task_number"), d.get("annotation_number"))
            return sig if None not in sig else None  # type: ignore[return-value]
        except Exception:
            return None

    def _cycle(self) -> bool:
        """单轮:全部 eligible 项目各 recover 一次;全成→True(重置熔断计数)。"""
        projects = [
            p for p in self._store.list_projects()
            if p.get("status") == "active" and p.get("ls_project_id")
        ]
        ok = True
        for p in projects:
            sig = self._project_signature(p)
            if sig is not None and self._ls_counts.get(p["name"]) == sig:
                continue  # M13:计数无变化 → 跳过本轮全量 export
            try:
                summary = self._recover(
                    store=self._store, lake=self._lake, config=self._config,
                    project_name=p["name"])
                if sig is not None:
                    self._ls_counts[p["name"]] = sig
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
