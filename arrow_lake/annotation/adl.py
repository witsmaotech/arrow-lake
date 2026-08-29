"""F4.5 — ADL(Lance 版本化写回,v1.11.3 MS4 W3.2)。

Annotation Data Layer:标注产物落 ``{ds}_adl``(与源数据集同容器独立表,
D2——生命周期与源绑定)。**append-only**(Lance 原生版本链,不覆盖):

* ``adl_id = sha1(dataset|row_id|annotator|signature)`` —— 标注内容幂等
  键:轮询与 webhook 双通道重放同一条 → 同 id → 去重跳过(S9 幂等);
  内容变化 → 新 id;
* ``adl_version``:同 ``(source_row_id, annotator_id)`` 组内递增(重标注
  = 新版本,S5);组内现存版本数由调用方读现有表提供(
  ``group_versions``)。

写入经 StorageWriter protocol(死信 writer 同款;真实现 =
``_LakeStorageWriter``,create-or-append)。
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any

import pyarrow as pa

from arrow_lake.annotation.quality import Adjudication, annotation_signature
from arrow_lake.annotation.recover import RecoveredAnnotation

__all__ = ["ADL_SCHEMA", "build_adl_batch", "write_adl"]

_SPAN_T = pa.struct([("label", pa.string()), ("start", pa.int32()), ("end", pa.int32())])
_TRIPLE_T = pa.struct([
    ("subject", pa.string()), ("predicate", pa.string()), ("object", pa.string()),
])

ADL_SCHEMA = pa.schema([
    ("adl_id", pa.string()),           # sha1(dataset|row|annotator|signature)
    ("source_dataset", pa.string()),
    ("source_row_id", pa.string()),    # 派发时 task.data.row_id
    # L4 五段
    ("objects", pa.list_(_SPAN_T)),
    ("events", pa.list_(_SPAN_T)),
    ("rules_applied", pa.list_(pa.string())),
    ("scenario", pa.string()),
    ("relations", pa.list_(_TRIPLE_T)),
    # 标注者元数据
    ("annotator_id", pa.string()),     # LS completed_by
    ("annotated_at", pa.string()),
    ("review_status", pa.string()),    # approved | arbitration | pending
    ("reviewer_id", pa.string()),      # 仲裁终审后填(本版空)
    # 版本化
    ("batch_id", pa.string()),         # 回收批次
    ("adl_version", pa.int32()),       # (row, annotator) 组内递增
])


def _adl_id(dataset: str, rec: RecoveredAnnotation, signature: str) -> str:
    raw = f"{dataset}|{rec.row_id}|{rec.annotator_id}|{signature}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


def build_adl_batch(
    *,
    dataset: str,
    recovered: Sequence[RecoveredAnnotation],
    adjudications: Mapping[str, Adjudication],
    batch_id: str,
    existing_adl_ids: set[str],
    group_versions: Mapping[tuple[str, str], int],
) -> tuple[pa.Table, int]:
    """回收标注 → ADL 行批(adl_id 去重 + 组内版本递增)。

    Args:
        existing_adl_ids: 现存 adl_id 集(重放幂等;由调用方读现有表)。
        group_versions: 现存 ``(row_id, annotator_id) → 最大 adl_version``。
        adjudications: ``row_id → Adjudication``(review_status 来源)。

    Returns:
        ``(table, written)``;written = 去重后实际新增行数。
    """
    rows: list[dict[str, Any]] = []
    seen_now: set[str] = set()
    local_versions: dict[tuple[str, str], int] = dict(group_versions)
    for rec in recovered:
        signature = annotation_signature(rec)
        adl_id = _adl_id(dataset, rec, signature)
        if adl_id in existing_adl_ids or adl_id in seen_now:
            continue  # 重放(轮询+webhook 双通道)/批内重复 → 幂等跳过
        seen_now.add(adl_id)
        group = (rec.row_id, rec.annotator_id)
        local_versions[group] = local_versions.get(group, 0) + 1
        adj = adjudications.get(rec.row_id, Adjudication("pending", None, ()))
        rows.append({
            "adl_id": adl_id,
            "source_dataset": dataset,
            "source_row_id": rec.row_id,
            "objects": [{"label": s.label, "start": s.start, "end": s.end} for s in rec.objects],
            "events": [{"label": s.label, "start": s.start, "end": s.end} for s in rec.events],
            "rules_applied": list(rec.rules_applied),
            "scenario": rec.scenario,
            "relations": [
                {"subject": t.subject, "predicate": t.predicate, "object": t.object}
                for t in rec.relations
            ],
            "annotator_id": rec.annotator_id,
            "annotated_at": rec.annotated_at or datetime.now(UTC).isoformat(),
            "review_status": adj.status,
            "reviewer_id": "",
            "batch_id": batch_id,
            "adl_version": local_versions[group],
        })
    table = pa.Table.from_pylist(rows, schema=ADL_SCHEMA)
    return table, len(rows)


def write_adl(writer: Any, dataset: str, table: pa.Table) -> int:
    """经 StorageWriter protocol 写 ``{ds}_adl``(create-or-append)。"""
    if table.num_rows == 0:
        return 0
    return int(writer.write(f"{dataset}_adl", table))
