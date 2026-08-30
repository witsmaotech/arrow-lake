"""F5.1 — 五维评估引擎:completeness / diversity / timeliness 三维自动
评估器(v1.11.4 MS5 W1.2)。

旁路纯函数(DR6 热路径零改动):只读输入(pyarrow 表 / 契约 / 死信
计数 / ADL),产出 ``DimensionResult``(score 0-100;``None`` = 未评估
降级,评分层对 assessed 维度重归一)。relevance 由 W2 标注回路接入、
accuracy 见 ``compute_accuracy``(W1.3)。

口径(设计 §2.1,业务手册第五章):
* **completeness** = 检查项通过率。检查项:契约 ``required`` 列缺失率
  (≤1% 过;缺失率 = null 数/行数,列不在 schema = 1.0)+ 死信率
  (``_{ds}_dead_letter`` 行数 / (数据行 + 死信行),≤1% 过);
* **diversity** = (1 − gini) × 100,gini 取 Gini–Simpson 形态 Σpᵢ²
  (类别频率集中度;单类别=1 退化 0 分,均匀 k 类=1/k)。类别列 =
  string/dictionary 类型且去重基数 ≤ ``MAX_CATEGORY_CARDINALITY``
  (高基数 id 列天然排除);多类别列取**最差列封顶**(门槛 gini<0.4
  语义 = 全列过线);
* **timeliness** = 新鲜度 + 标注延迟 p95 双指标 SLO 折算:每指标
  ``h ≤ max`` 满分,超出按 ``100 × max / h`` 比例衰减(截 0);维度分
  = 可用指标均值(数据缺失 → 降级标记,不造假分)。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Any

import pyarrow as pa
import pyarrow.compute as pc

from arrow_lake.contract.schema import DatasetContract

__all__ = [
    "DEAD_LETTER_MAX",
    "MAX_CATEGORY_CARDINALITY",
    "RELEVANCE_LABELS",
    "REQUIRED_MISSING_MAX",
    "DimensionResult",
    "adl_signature",
    "annotation_delay_p95_hours",
    "compute_accuracy",
    "compute_completeness",
    "compute_diversity",
    "compute_relevance",
    "compute_timeliness",
    "detect_timestamp_column",
    "freshness_hours",
]

#: 契约 required 列缺失率上限(检查项过线,≤ 含)。
REQUIRED_MISSING_MAX = 0.01
#: 死信率上限(检查项过线,≤ 含)。
DEAD_LETTER_MAX = 0.01
#: 类别列基数帽:超过视为标识/自由文本列,不入多样性。
MAX_CATEGORY_CARDINALITY = 50

#: 时间列探测优先序(同名取先;其后按 _at/_time/_date 后缀,再后 schema 序)。
_TS_PRIORITY = (
    "updated_at", "created_at", "event_time", "event_at",
    "occurred_at", "timestamp", "ts", "time",
)


@dataclass(frozen=True)
class DimensionResult:
    """单维评估结果。``score=None`` 表示未评估(降级),评分层跳过。"""

    name: str
    score: float | None
    details: dict[str, Any] = field(default_factory=dict)
    source: str = "auto"   # auto | adl | llm | annotation(数据源标记)


# --------------------------------------------------------------------------- #
# completeness
# --------------------------------------------------------------------------- #

def _resolve_section(
    contract: DatasetContract | None, table_name: str | None,
) -> tuple[str | None, Any]:
    """契约表节解析:指名精确匹配 → 单节 → 数据集名同名节 → None。"""
    if contract is None:
        return None, None
    if table_name is not None:
        sec = contract.tables.get(table_name)
        return (table_name, sec) if sec is not None else (None, None)
    if len(contract.tables) == 1:
        name, sec = next(iter(contract.tables.items()))
        return name, sec
    sec = contract.tables.get(contract.dataset)
    return (contract.dataset, sec) if sec is not None else (None, None)


def compute_completeness(
    table: pa.Table,
    contract: DatasetContract | None,
    *,
    dead_letter_rows: int = 0,
    table_name: str | None = None,
) -> DimensionResult:
    """完整性:契约 required 缺失率 + 死信率 → 检查项通过率 ×100。"""
    checks: list[dict[str, Any]] = []
    section_name, section = _resolve_section(contract, table_name)
    if section is not None:
        for rule in section.columns:
            if not rule.required:
                continue
            if rule.name not in table.column_names:
                rate = 1.0
            elif table.num_rows == 0:
                rate = 1.0  # 空表 = required 数据整体缺席(门禁语义不放假分)
            else:
                rate = table.column(rule.name).null_count / table.num_rows
            checks.append({
                "kind": "required", "table": section_name, "column": rule.name,
                "missing_rate": round(rate, 6),
                "passed": rate <= REQUIRED_MISSING_MAX,
            })

    total = table.num_rows + max(0, dead_letter_rows)
    dead_rate = dead_letter_rows / total if total > 0 else 0.0
    checks.append({
        "kind": "dead_letter", "table": table_name,
        "dead_letter_rows": max(0, dead_letter_rows),
        "dataset_rows": table.num_rows,
        "rate": round(dead_rate, 6),
        "passed": dead_rate <= DEAD_LETTER_MAX,
    })

    passed = sum(1 for c in checks if c["passed"])
    details: dict[str, Any] = {
        "contract": contract is not None and section is not None,
        "section": section_name,
        "checks": checks,
    }
    if contract is not None and section is None:
        details["note"] = "contract section unresolved; required checks skipped"
    return DimensionResult(
        name="completeness",
        score=round(100.0 * passed / len(checks), 4),
        details=details,
    )


# --------------------------------------------------------------------------- #
# diversity
# --------------------------------------------------------------------------- #

def _column_frequencies(col: pa.ChunkedArray) -> list[tuple[Any, int]]:
    """列值频率(pc.value_counts;调用方已确保非空)。"""
    vc = pc.value_counts(col)
    return list(zip(
        vc.field("values").to_pylist(), vc.field("counts").to_pylist(),
        strict=True,
    ))


def compute_diversity(
    table: pa.Table, *, max_cardinality: int = MAX_CATEGORY_CARDINALITY,
) -> DimensionResult:
    """多样性:类别列 Gini–Simpson(Σpᵢ²),最差列封顶。"""
    columns: dict[str, dict[str, Any]] = {}
    for name in table.column_names:
        col = table.column(name)
        if not (pa.types.is_string(col.type) or pa.types.is_large_string(col.type)
                or pa.types.is_dictionary(col.type)):
            continue
        non_null = col.drop_null() if hasattr(col, "drop_null") else pc.drop_null(col)
        if len(non_null) == 0:
            continue  # 全空列无分布
        freqs = _column_frequencies(non_null)
        if len(freqs) > max_cardinality:
            continue  # 高基数(id/自由文本)非类别列
        total = sum(c for _, c in freqs)
        gini = sum((c / total) ** 2 for _, c in freqs)
        columns[name] = {
            "gini": round(gini, 6), "distinct": len(freqs), "total": total,
        }

    if not columns:
        return DimensionResult(
            name="diversity", score=None,
            details={"columns": {}, "note": "no categorical columns"},
        )
    worst = max(columns, key=lambda n: columns[n]["gini"])
    return DimensionResult(
        name="diversity",
        score=round((1.0 - columns[worst]["gini"]) * 100.0, 4),
        details={"columns": columns, "worst_column": worst},
    )


# --------------------------------------------------------------------------- #
# timeliness
# --------------------------------------------------------------------------- #

def _slo_score(hours: float | None, max_hours: float) -> float | None:
    """SLO 折算:≤max 满分;超出按 max/h 比例衰减;None=不计入。"""
    if hours is None:
        return None
    if hours <= max_hours:
        return 100.0
    return round(max(0.0, 100.0 * max_hours / hours), 4)


def compute_timeliness(
    *,
    freshness_hours: float | None,
    annotation_delay_p95_hours: float | None,
    max_p95_hours: float,
) -> DimensionResult:
    """时效性:新鲜度 + 标注延迟 p95 双指标 SLO 折算(指标均值)。"""
    components: dict[str, dict[str, Any]] = {}
    fresh_score = _slo_score(freshness_hours, max_p95_hours)
    if fresh_score is not None:
        components["freshness"] = {
            "hours": freshness_hours, "score": fresh_score,
        }
    delay_score = _slo_score(annotation_delay_p95_hours, max_p95_hours)
    if delay_score is not None:
        components["annotation_delay_p95"] = {
            "hours": annotation_delay_p95_hours, "score": delay_score,
        }
    if not components:
        return DimensionResult(
            name="timeliness", score=None,
            details={"components": {}, "note": "no temporal signal"},
        )
    score = sum(c["score"] for c in components.values()) / len(components)
    return DimensionResult(
        name="timeliness", score=round(score, 4), details={"components": components},
    )


# --------------------------------------------------------------------------- #
# accuracy(ADL 聚合 κ,W1.3)
# --------------------------------------------------------------------------- #

def adl_signature(row: dict[str, Any]) -> str:
    """ADL 行 → canonical signature。

    与 ``annotation.quality.annotation_signature`` 同构(span/events/
    relations/rules/scenario 排序拼接),唯一差异是 span 不带 text——
    ADL 不落 text,但**同一行**的 text = 行文本[start:end] 是偏移量的
    函数,故对同行标注的一致性比较,(label,start,end) 与
    (label,start,end,text) 判等完全等价。
    """
    objects = sorted(f"{s['label']}@{s['start']}-{s['end']}" for s in row["objects"])
    events = sorted(f"{s['label']}@{s['start']}-{s['end']}" for s in row["events"])
    relations = sorted(
        f"{t['subject']}>{t['predicate']}>{t['object']}" for t in row["relations"]
    )
    rules = sorted(row["rules_applied"])
    return "§".join((
        "|".join(objects), "|".join(events), "|".join(relations),
        "|".join(rules), row["scenario"],
    ))


def compute_accuracy(adl_table: pa.Table | None) -> DimensionResult:
    """准确性:ADL 全量聚合 Fleiss' κ((row,annotator) 取最新版本)。

    修 MS4 W5 轮级限制:输入是 ``{ds}_adl`` 全量(append-only SoT),
    非 dispatch 当轮 fresh 标注——项目全生命周期口径(S3)。κ×100 为
    维度分(负 κ=劣于随机,clamp 到 0;原值保留在 details.kappa)。

    降级(评分层跳过该维):ADL 空 → ``no annotations``;无双标注行 →
    ``no double-annotated rows``(单标注者/LLM 折算路径不在 W1)。
    ``llm:`` 前缀标注者(W2 LLM 直评/降级)不入 κ——κ 是人-人一致性。
    """
    if adl_table is None or adl_table.num_rows == 0:
        return DimensionResult(
            name="accuracy", score=None,
            details={"note": "no annotations"}, source="adl",
        )

    latest: dict[tuple[str, str], dict[str, Any]] = {}
    llm_rows = relevance_rows = 0
    for row in adl_table.to_pylist():
        if str(row["annotator_id"]).startswith("llm:"):
            llm_rows += 1
            continue
        if _is_relevance_row(row):  # Choices-only 行不是 L4 标注(W4 e2e 实证)
            relevance_rows += 1
            continue
        key = (row["source_row_id"], row["annotator_id"])
        cur = latest.get(key)
        if cur is None or row["adl_version"] >= cur["adl_version"]:
            latest[key] = row

    by_task: dict[str, list[str]] = {}
    for (row_id, _annotator), row in latest.items():
        by_task.setdefault(row_id, []).append(adl_signature(row))
    eligible = [sigs for sigs in by_task.values() if len(sigs) >= 2]
    excluded_single = len(by_task) - len(eligible)

    base = {
        "adl_rows": adl_table.num_rows,
        "excluded_single": excluded_single,
        "llm_rows_excluded": llm_rows,
        "relevance_rows_excluded": relevance_rows,
    }
    if not eligible:
        return DimensionResult(
            name="accuracy", score=None,
            details={**base, "note": "no double-annotated rows"},
            source="adl",
        )

    from arrow_lake.annotation.quality import fleiss_kappa

    n_raters = min(len(sigs) for sigs in eligible)
    kappa = fleiss_kappa([sigs[:n_raters] for sigs in eligible], n_raters=n_raters)
    if kappa is None:
        return DimensionResult(
            name="accuracy", score=None,
            details={**base, "note": "kappa undefined"},
            source="adl",
        )
    return DimensionResult(
        name="accuracy",
        score=round(max(0.0, min(100.0, kappa * 100.0)), 4),
        details={
            **base,
            "kappa": round(kappa, 6),
            "n_raters": n_raters,
            "tasks": len(eligible),
            "annotators": len({a for (_r, a) in latest}),
        },
        source="adl",
    )


# --------------------------------------------------------------------------- #
# relevance(ADL 聚合,W2.1 / F5.2)
# --------------------------------------------------------------------------- #

#: 三分类标签(D2):LS Choices 的 value 即回收后的 scenario 值。
RELEVANCE_LABELS: tuple[str, str, str] = ("高相关", "间接相关", "不相关")
_RELEVANCE_RANK = {"高相关": 2, "间接相关": 1, "不相关": 0}
_RELEVANCE_GRADE = {"高相关": 1.0, "间接相关": 0.5, "不相关": 0.0}


def _is_relevance_row(rec: dict[str, Any]) -> bool:
    """Choices-only 标注:scenario ∈ 三分类 且无任何结构(spans/关系/规则)。

    L4 结构化标注的 scenario 撞名不认——带 span 的行属于标注本体,
    不是相关性判定。
    """
    return (
        rec.get("scenario") in RELEVANCE_LABELS
        and not rec.get("objects")
        and not rec.get("events")
        and not rec.get("relations")
        and not rec.get("rules_applied")
    )


def compute_relevance(adl_table: pa.Table | None) -> DimensionResult:
    """相关性:ADL 聚合三分类 → 100×(high + 0.5×somewhat)/total(§2.1)。

    聚合:(row,annotator) 取最新 ``adl_version`` → per-row **majority**,
    人工票优先于 LLM 票(annotator_id ``llm:`` 前缀;行内有人工票则弃
    LLM 票),平票取低档(门禁保守)。``source`` 标数据形态:
    ``annotation``(全人)/ ``llm``(全 LLM,降级——建议非结论)/
    ``mixed``。
    """
    if adl_table is None or adl_table.num_rows == 0:
        return DimensionResult(
            name="relevance", score=None,
            details={"note": "no annotations"}, source="annotation",
        )

    latest: dict[tuple[str, str], dict[str, Any]] = {}
    for row in adl_table.to_pylist():
        if not _is_relevance_row(row):
            continue
        key = (row["source_row_id"], row["annotator_id"])
        cur = latest.get(key)
        if cur is None or row["adl_version"] >= cur["adl_version"]:
            latest[key] = row
    if not latest:
        return DimensionResult(
            name="relevance", score=None,
            details={"note": "no relevance annotations"}, source="annotation",
        )

    by_row: dict[str, list[dict[str, Any]]] = {}
    for (row_id, _annotator), row in latest.items():
        by_row.setdefault(row_id, []).append(row)

    counts = {label: 0 for label in RELEVANCE_LABELS}
    human_rows = llm_rows = 0
    for recs in by_row.values():
        humans = [r for r in recs if not str(r["annotator_id"]).startswith("llm:")]
        pool = humans if humans else recs
        if humans:
            human_rows += 1
        else:
            llm_rows += 1
        tally: dict[str, int] = {}
        for r in pool:
            tally[r["scenario"]] = tally.get(r["scenario"], 0) + 1
        best = max(tally.values())
        winners = [label for label, n in tally.items() if n == best]
        counts[min(winners, key=lambda l: _RELEVANCE_RANK[l])] += 1

    total = sum(counts.values())
    score = 100.0 * (
        counts["高相关"] + 0.5 * counts["间接相关"]
    ) / total
    source = (
        "annotation" if llm_rows == 0
        else ("llm" if human_rows == 0 else "mixed")
    )
    details: dict[str, Any] = {
        "counts": counts, "rows": total,
        "human_rows": human_rows, "llm_rows": llm_rows,
    }
    if source == "llm":
        details["assessed_by"] = "llm"
        details["note"] = "LLM-only assessment — suggestions, not human verdicts"
    return DimensionResult(
        name="relevance", score=round(score, 4), details=details, source=source,
    )


# --------------------------------------------------------------------------- #
# temporal helpers(orchestrator 用)
# --------------------------------------------------------------------------- #

def detect_timestamp_column(table: pa.Table) -> str | None:
    """时间列探测:temporal 类型;优先级 = 预置名 → _at/_time/_date 后缀
    → schema 序。无 temporal 列 → None。"""
    temporal = [n for n in table.column_names
                if pa.types.is_temporal(table.schema.field(n).type)]
    if not temporal:
        return None
    for name in _TS_PRIORITY:
        if name in temporal:
            return name
    for name in temporal:
        if name.endswith(("_at", "_time", "_date")):
            return name
    return temporal[0]


def _as_utc(value: datetime | date) -> datetime:
    """pyarrow 时间值 → aware datetime(秒级 timestamp 无 tz → 视作 UTC;
    date → 当日 00:00 UTC)。"""
    if isinstance(value, date) and not isinstance(value, datetime):
        return datetime(value.year, value.month, value.day, tzinfo=UTC)
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value


def freshness_hours(
    table: pa.Table, *, now: datetime, column: str | None = None,
) -> float | None:
    """新鲜度 = now − 表内最新时间戳(小时);无时间列/空表 → None。"""
    name = column or detect_timestamp_column(table)
    if name is None:
        return None
    latest = pc.max(table.column(name)).as_py()  # 空表/全空 → None
    if latest is None:
        return None
    hours = (now - _as_utc(latest)).total_seconds() / 3600.0
    return round(max(0.0, hours), 6)


def _parse_iso(value: Any) -> datetime | None:
    """ADL annotated_at(iso 串)→ aware datetime;不可解析 → None。"""
    if not isinstance(value, str) or not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return _as_utc(dt)


def annotation_delay_p95_hours(
    source: pa.Table, adl: pa.Table, *, text_column: str,
) -> float | None:
    """标注延迟 p95(小时):ADL annotated_at − 行时间,按 stable_row_id join。

    行时间取源表时间列(自动探测);行标识用 dispatch 的
    ``stable_row_id(text, index)``("h"+sha1(text)[:12])重建。任何一环
    缺失(无时间列/ADL 空/无可配对行)→ None(timeliness 降级到仅新鲜
    度,不造假分)。负延迟(时钟偏移)clamp 到 0;p95 = nearest-rank。
    """
    ts_col = detect_timestamp_column(source)
    if ts_col is None or adl.num_rows == 0:
        return None
    from arrow_lake.annotation.dispatch import stable_row_id

    texts = source.column(text_column).to_pylist() if text_column in source.column_names else []
    ts_values = source.column(ts_col).to_pylist()
    if len(texts) != len(ts_values):
        return None
    row_ts: dict[str, datetime] = {}
    for i, (text, ts) in enumerate(zip(texts, ts_values, strict=True)):
        if ts is None:
            continue
        row_ts[stable_row_id(text, i)] = _as_utc(ts)

    delays: list[float] = []
    for rec in adl.to_pylist():
        annotated = _parse_iso(rec.get("annotated_at"))
        created = row_ts.get(rec.get("source_row_id"))
        if annotated is None or created is None:
            continue
        hours = (annotated - created).total_seconds() / 3600.0
        delays.append(max(0.0, hours))
    if not delays:
        return None
    delays.sort()
    rank = max(0, math.ceil(0.95 * len(delays)) - 1)
    return round(delays[rank], 6)
