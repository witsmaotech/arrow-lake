"""MS3 vertical slice 演示资产(v1.11.2 W5.1,F3.5/D4)。

D4 决策的落地形态:**普通单表数据集** ``demo_ms3_alerts``(免容器寻址
复杂性,对象/研判/行动全管线兼容),契约声明 ``alerts`` 表节——物理
寻址回落 plain dataset(objectset 的 container probe 判空即走裸名)。

消费者:tests/unit/api/test_ms3_vertical_slice.py(DoD e2e)与
scripts/ms3_seed_demo.py(live 纯镜像种子)。演示数据非业务契约内容
(v1.11.0.1 W5.2 先例)。
"""

from __future__ import annotations

__all__ = [
    "DEMO_DATASET",
    "ALERTS_CSV",
    "ALERT_ROWS",
    "CONTRACT_YAML",
    "RULES",
    "ACTION_PUBLISH",
    "ACTION_ESCALATE",
    "ACTION_NOTIFY",
    "SCENARIO_YAML",
    "GOLDEN_EXPECTED",
]

DEMO_DATASET = "demo_ms3_alerts"

# ⚠️ 空值占位 `-`:裸空/引号空串都会被 pyarrow csv 推断为 null 类型,
# 摄入后路径在 null 列上悬挂(2026-08-28 实证);占位符保 string 类型,
# publish 时 update_lifecycle 覆写为 ISO 时间戳。
ALERTS_CSV = """alert_id,pressure,level,state,published_at
GAS.ALERT.D001,2000.0,high,pending,-
GAS.ALERT.D002,800.0,mid,pending,-
"""

# e2e 直建形态(与 CSV 同构;pressure double,其余 string)
ALERT_ROWS: list[dict] = [
    {"alert_id": "GAS.ALERT.D001", "pressure": 2000.0, "level": "high",
     "state": "pending", "published_at": None},
    {"alert_id": "GAS.ALERT.D002", "pressure": 800.0, "level": "mid",
     "state": "pending", "published_at": None},
]

CONTRACT_YAML = """
dataset: demo_ms3_alerts
tables:
  alerts:
    object_class: 告警事件
    lifecycle: {column: state, states: [pending, published, escalated, closed], initial: pending}
    identifier:
      column: alert_id
      pattern: "GAS.ALERT.{seq}"
    columns:
      - {name: pressure, label: 泄漏压力, unit: kPa}
      - {name: level}
      - {name: published_at}
"""

# 五分类混排:validation/derivation/risk_control/computation + transformation
# 的 unruly 一条(条件不可编译,S8 fail-open 到条)。
RULES: list[dict] = [
    {"rule_id": "DEMO.R.OPEN", "scope": DEMO_DATASET,
     "condition_expr": "target.state == 'pending'",
     "conclusion": "待研判处置", "source_ref": "demo-spec-B1",
     "rule_type": "validation"},
    {"rule_id": "DEMO.R.HIGH", "scope": DEMO_DATASET,
     "condition_expr": "target.pressure >= 1500",
     "conclusion": "高压泄漏告警", "source_ref": "demo-spec-A1",
     "rule_type": "risk_control"},
    {"rule_id": "DEMO.R.LEVEL", "scope": DEMO_DATASET,
     "condition_expr": "target.level in ['high', 'critical']",
     "conclusion": "高风险等级", "source_ref": "demo-spec-A2",
     "rule_type": "derivation"},
    {"rule_id": "DEMO.R.CRITICAL", "scope": "*",
     "condition_expr": "target.pressure >= 3000",
     "conclusion": "极高压需通报(全局)", "source_ref": "demo-spec-G",
     "rule_type": "computation"},
    {"rule_id": "DEMO.R.UNRULY", "scope": DEMO_DATASET,
     "condition_expr": "pressure >== 100",   # 不可编译 → unruly(S8)
     "conclusion": "坏规则(演示 unruly 隔离)", "source_ref": "demo-spec-N",
     "rule_type": "transformation"},
]

ACTION_PUBLISH = """
action_id: GAS.ALERT.PUBLISH
title: 发布燃气泄漏预警
target: {dataset: demo_ms3_alerts, object_class: 告警事件}
preconditions:
  - "assess.matched_rules >= 1"
  - "target.state == 'pending'"
effect:
  type: update_lifecycle
  to_state: published
  fields: {published_at: "{{ now() }}"}
idempotency_key: "{{ target.object_id }}"
compensation: {action: GAS.ALERT.WITHDRAW, policy: manual}
on_failure: {fallback: DEAD_LETTER, exception_class: technical}
audit: {reason_required: true, include: [assess.rule_ids]}
post_event:
  name: alert.published
  payload: [target.object_id, assess.rule_ids, actor]
"""

ACTION_ESCALATE = """
action_id: GAS.ALERT.ESCALATE
title: 升级人工处置
target: {dataset: demo_ms3_alerts, object_class: 告警事件}
preconditions:
  - "assess.matched_rules >= 1"
  - "target.state == 'pending'"
effect:
  type: update_lifecycle
  to_state: escalated
idempotency_key: "{{ target.object_id }}"
on_failure: {fallback: DEAD_LETTER, exception_class: business}
audit: {reason_required: true, include: [assess.rule_ids]}
"""

ACTION_NOTIFY = """
action_id: GAS.ALERT.NOTIFY
title: 通知值班
target: {dataset: demo_ms3_alerts, object_class: 告警事件}
preconditions: ["assess.matched_rules >= 1"]
effect:
  type: notify
  fields: {message: "告警 {{ target.object_id }} 已按场景处置"}
idempotency_key: "{{ target.object_id }}"
on_failure: {fallback: REJECT, exception_class: technical}
audit: {reason_required: false}
"""

SCENARIO_YAML = """
scenario_id: GAS.LEAK.RESPONSE
title: 燃气泄漏告警响应
process: 告警研判与预警发布
entries: ["target.state == 'pending'"]
steps:
  - {id: assess, type: assess, rules_scope: demo_ms3_alerts}
  - {id: publish, action: GAS.ALERT.PUBLISH, requires: [assess]}
  - {id: notify_ops, action: GAS.ALERT.NOTIFY, requires: [publish]}
  - {id: escalate_manual, action: GAS.ALERT.ESCALATE, requires: [assess], path: substitute}
gateways:
  - id: confidence_gate
    type: xor
    when: "assess.matched_rules >= 2"
    then: [publish]
    else: [escalate_manual]
  - id: parallel_notify
    type: and_split
    branches: [[publish], [notify_ops]]
timeout: PT30M
on_timeout: escalate_manual
"""

# 黄金集(W5.3 度量脚本复用):object_id → 期望命中 rule_id 集合
GOLDEN_EXPECTED: dict[str, set[str]] = {
    "GAS.ALERT.D001": {"DEMO.R.OPEN", "DEMO.R.HIGH", "DEMO.R.LEVEL"},
    "GAS.ALERT.D002": {"DEMO.R.OPEN"},
}
