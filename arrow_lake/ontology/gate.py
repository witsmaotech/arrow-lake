"""F1.3/W2.1 — KG build 收尾的本体门禁运行器。

契约(实施计划 §4 W2.1 + 风险表):
* ``off`` 零开销 — 不读模板、不计指标、直接返回 None;
* 无模板/模板不可读 → ``skip``(计数,不算违规);
* 校验在默认线程池执行,``asyncio.wait_for`` 超时上限兜底;超时 =
  **fail-closed**(合成一条 reject,校验不可用不放行 — 与 quality gate
  同纪律);
* 违规分级:reject 拒 / warn 观察(validator 的 severity 契约);
* 指标 ``arrow_lake_ontology_check_total{dataset,result}``,
  result ∈ pass|warn|reject|skip。

红线:本模块是 validator 在 ontology 包外的唯一调用方;业务入口仅
kg_build 收尾(``_lake_kg``)与 ontology 管理 API。
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any

from arrow_lake.ontology.validator import Violation, validate_snapshot
from arrow_lake.ontology.versioning import load_template_artifact

OUTCOME_PASS = "pass"
OUTCOME_WARN = "warn"
OUTCOME_REJECT = "reject"
OUTCOME_SKIP = "skip"


@dataclass(frozen=True)
class OntologyGateResult:
    """一次门禁运行的结果(shadow 计数面 + enforce 决策面)。"""

    mode: str
    outcome: str                     # pass | warn | reject | skip
    rejects: int
    warns: int
    violations: list[dict[str, Any]] = field(default_factory=list)  # 已截断样本
    duration_seconds: float = 0.0

    def to_detail(self) -> dict[str, Any]:
        """任务明细里的可序列化摘要(kg_build_status 带违规摘要)。"""
        return {
            "mode": self.mode,
            "outcome": self.outcome,
            "rejects": self.rejects,
            "warns": self.warns,
            "violations": self.violations,
        }


def enforcement_error(result: OntologyGateResult, *, max_messages: int = 3) -> str | None:
    """enforce 拒绝时的 error 文案;无 reject 级违规返回 None。"""
    if not result.rejects:
        return None
    sample = "; ".join(
        f"{v['focus']}.{v['path']}={v['value'] or '?'} ({v['message']})"
        for v in result.violations[:max_messages]
    )
    msg = f"ontology gate rejected: {result.rejects} violation(s)"
    if sample:
        msg += f" — {sample}"
    return msg


async def run_ontology_gate(
    dataset_name: str,
    entities: list[dict[str, Any]],
    relations: list[dict[str, Any]],
    template_path: str | None,
    *,
    mode: str,
    timeout_seconds: float = 60.0,
    max_violations: int = 20,
) -> OntologyGateResult | None:
    """校验一份刚插入的 KG 快照 against 模板派生的 SHACL shapes。

    Returns:
        None — mode == "off"(零开销);
        OntologyGateResult — 其余模式(含 skip:模板缺失)。
    """
    if mode == "off":
        return None

    from arrow_lake.core.metrics import ontology_check_total

    start = time.monotonic()

    def _work() -> tuple[list[Violation], bool]:
        artifact = load_template_artifact(template_path)
        if artifact is None:
            return [], True  # skip
        violations = validate_snapshot(entities, relations, artifact.graph)
        return violations, False

    loop = asyncio.get_running_loop()
    try:
        violations, skipped = await asyncio.wait_for(
            loop.run_in_executor(None, _work), timeout=timeout_seconds,
        )
    except TimeoutError:
        violations = [
            Violation(
                level="reject",
                focus="<snapshot>",
                path="",
                value="",
                message=(
                    f"ontology validation timed out after {timeout_seconds:.0f}s "
                    "(fail-closed)"
                ),
            )
        ]
        skipped = False

    if skipped:
        ontology_check_total.labels(dataset=dataset_name, result=OUTCOME_SKIP).inc()
        return OntologyGateResult(
            mode=mode, outcome=OUTCOME_SKIP, rejects=0, warns=0,
            duration_seconds=round(time.monotonic() - start, 4),
        )

    rejects = sum(1 for v in violations if v.level == "reject")
    warns = len(violations) - rejects
    outcome = OUTCOME_REJECT if rejects else (OUTCOME_WARN if warns else OUTCOME_PASS)
    ontology_check_total.labels(dataset=dataset_name, result=outcome).inc()

    return OntologyGateResult(
        mode=mode,
        outcome=outcome,
        rejects=rejects,
        warns=warns,
        violations=[_violation_dict(v) for v in violations[:max_violations]],
        duration_seconds=round(time.monotonic() - start, 4),
    )


def _violation_dict(v: Violation) -> dict[str, str]:
    return {
        "level": v.level,
        "focus": v.focus,
        "path": v.path,
        "value": v.value,
        "message": v.message,
    }
