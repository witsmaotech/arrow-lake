# 21 · 本体、契约与对象:定义层实操

> 版本基线:v1.11.0–v1.11.2(MS1 本体形式化 / MS2 语义层 / MS3 决策与行动),v1.11.4 校准。本章全部可复制实操,示例输出来自真实运行(demo_gas / wb_flow 试点)。Console 入口:「本体与语义」组——建模工作台 / 本体与规则 / 数据契约 / 对象浏览。

定义层回答三个问题:**数据应该长什么样**(契约)、**业务上什么算对**(规则)、**一行数据是哪个业务对象**(对象标识与对齐)。第 20 章的质量流水线消费的正是这一层:完整性维度读契约、研判读规则、黄金集按对象对齐。

```text
建模工作台(表单) ──→ 契约 YAML(结构约束,版本链)
       │                    │
       ├──→ 抽取模板骨架      ├──→ 本体规则(五分类,draft→active)
       └──→ LS 标注 config    └──→ KG 构建收尾的本体门禁(shadow/enforce)
                                ↓
对象浏览(Object Set 查询:标识/属性/生命周期/KG 邻居/规则引用)
                                ↓
研判台(assess 求值,可记录历史)→ 行动执行(受控写)
```

前提:ADMIN 个人 token(`X-API-Key`)。

---

## ① 契约——结构与版本链

契约是每表列规则的机器可校验声明(必填/枚举/值域/pattern),scope 必须等于数据集名。保存即进版本链,同内容 hash 跳过:

```bash
curl -X PUT "http://127.0.0.1:8000/api/v1/contracts/wb_flow" \
  -H "X-API-Key: $TOKEN" -H "Content-Type: application/json" -d '{
  "contract_yaml": "dataset: wb_flow\ntables:\n  valves:\n    object_class: 阀门\n    lifecycle: {column: status, states: [正常, 故障, 维修], initial: 正常}\n    identifier: {column: valve_id, pattern: \"WB.V.{n:[0-9]+}\"}\n    columns:\n      - {name: valve_id, required: true}\n      - {name: pressure, label: 阀前压力, unit: kPa, type: double, range: [0, 1000]}\nreferences: []\n"}'
# → {"scope":"wb_flow","version":1,"created":true,"diff":null}
```

```bash
curl -H "X-API-Key: $TOKEN" "http://127.0.0.1:8000/api/v1/contracts/wb_flow/versions"   # 版本链
curl -H "X-API-Key: $TOKEN" "http://127.0.0.1:8000/api/v1/contracts/wb_flow/diff"       # 结构化 diff(枚举/必填/类型增删)
```

要点:`lifecycle` 声明状态列(供行动中间件做状态迁移与前置校验);`identifier.pattern` 的 `{n:[0-9]+}` 命名组会在对象查询里解析成标识组件;`label/unit` 是语义层的属性口径。**Console 的建模工作台**用表单设计这一切,实时预览三生成物(契约 YAML / LS 标注 config / 抽取模板骨架),并带「仅校验」按钮(`POST /contracts/parse`,不落库)。

## ② 本体规则——登记、状态机与门禁

规则在 MS3 被研判引擎消费,`rule_type` 五分类(validation/computation/derivation/transformation/risk_control)对应不同治理语义:

```bash
curl -X POST -H "X-API-Key: $TOKEN" -H "Content-Type: application/json" \
  -d '{"rule_id":"WB.FLOW.R001","scope":"wb_flow","condition_expr":"pressure > 800",
       "conclusion":"超压告警","source_ref":"运维手册 v2 §3","rule_type":"risk_control"}' \
  http://127.0.0.1:8000/api/v1/ontology/rules
# → {"success":true,"data":{"id":16,"rule_id":"WB.FLOW.R001",...,"status":"draft"}}

# 状态机:draft → active(只有 active 参与研判;retired 软下线保留审计)
curl -X POST -H "X-API-Key: $TOKEN" \
  "http://127.0.0.1:8000/api/v1/ontology/rules/WB.FLOW.R001/transition?to_status=active"
```

`condition_expr` 用受控谓词 DSL(手写 tokenizer + frozen AST,无 eval;`target.pressure >= 3000` 形态)。另一路本体来自**抽取模板**:模板的 `ontology:` 段被形式化为 SHACL,KG 构建收尾挂**本体门禁**——shadow(默认,只计数)或 enforce(违规翻 FAILED),版本链自动落 Turtle + 结构化 diff:

```bash
curl -H "X-API-Key: $TOKEN" "http://127.0.0.1:8000/api/v1/kg/build/{task_id}/status"
# → {"ontology": {"mode": "shadow", "outcome": "pass", "violations": [], ...}}
```

## ③ 对象浏览——Object Set 查询

把"表行"升维成"业务对象":标识组件、label/unit 属性、生命周期状态、跨表 `_links`(基数/类型)、可选 KG 邻居与命中规则,全部走 /query/olap 同一条安全路径(行/列 ACL 同样生效):

```bash
curl -X POST -H "X-API-Key: $TOKEN" -H "Content-Type: application/json" \
  -d '{"dataset":"demo_gas","object_type":"segments","limit":1,"include_kg":true}' \
  http://127.0.0.1:8000/api/v1/objects/query
```

```json
{"objects": [{
  "object_id": "GAS.SEG.1",
  "identifier": {"matched": true, "components": {"n": "1"}},
  "attributes": [{"column":"pressure","label":"压力","value":100,"unit":"kPa"}],
  "lifecycle_state": "在运",
  "_kg": {"matched": true, "neighbors": [...]},
  "_rules": [{"rule_id": "...", "conclusion": "..."}]
}]}
```

**语义对齐**让跨源同口径成为可能:单位仿射换算(如 MPa→kPa、温度偏移)在查询视图里以 SQL 投影应用,同一物理量经两个源系统进来,读出来是同一对象、同一属性、同一单位。源系统 ID→对象 ID 的映射经 entity-map 显式维护(`POST /objects/entity-map`,幂等批量)。

## ④ 研判与行动——对象层的执行端

```bash
# 研判(对齐取数 → active 规则求值 → 结论+可行动作);record_history 是飞轮/RLHF 的数据源
curl -X POST -H "X-API-Key: $TOKEN" -H "Content-Type: application/json" \
  -d '{"dataset":"demo_gas","object_type":"segments","object_id":"GAS.SEG.1"}' \
  "http://127.0.0.1:8000/api/v1/decisions/assess?record_history=true"
# → {"matched_rules": 1, "confidence": 1.0, "conclusions": [...], "actionable": [...],
//   "history_recorded": true}
```

行动(action)与场景(scenario)是 YAML 目录(版本链同 hash 跳过):行动声明效果(update_lifecycle/字段写)、幂等键、补偿、审计要求;场景编排多步(步骤/网关/超时)。执行走八步中间件——权限→幂等→前置→**写前行数核验**(标识物理命中 ≠1 即拒,防跨分区重复标识越权写)→效果→审计→事件:

```bash
curl -X POST -H "X-API-Key: $TOKEN" -H "Content-Type: application/json" \
  -d '{"dataset":"demo_gas","object_type":"segments","object_id":"GAS.SEG.1",
       "reason":"工单 #123 处置完成","assess":{...}}' \
  http://127.0.0.1:8000/api/v1/actions/GAS.SEG.PUBLISH/execute
# → {"status":"executed","audit_id":"..."} 或 {"status":"already_in_effect"}(幂等命中)
```

场景当前是编排定义态(steps/gateways/timeout),单步执行走研判台——这是 Master Plan 的登记偏差,不是缺陷。

---

## 排障速查

| 症状 | 原因与解法 |
|---|---|
| 保存契约 422 `dataset field must match scope` | 契约 scope 必须等于数据集名,否则整份契约静默无约束 |
| 研判 422 `no contract`/types 空 | 对象类来自契约表节——先做第①步;无 identifier 列的数据集研判不适用(向导页会明示) |
| 规则改了但研判结果没变 | 规则状态机:只有 `active` 参与求值,新存的是 draft |
| action 执行 422 `physically matches N rows` | 写前行数核验拦截(标识列存在跨行重复)——先修数据或收紧 pattern |
| 对象查询单位不是我想要的 | 语义对齐配置(单位仿射)未登记;`GET /semantic/units` 查注册表 |
| 本体门禁一直 skip | 模板无 `ontology:` 段或 GATE_MODE=off;`/metrics` 看 `arrow_lake_ontology_check_total` |

下一章:[20 高质量数据集流水线](./20-hq-dataset-zh.md) · 架构深读:[ARCHITECTURE](../architecture-design/ARCHITECTURE.md) §v1.11.0–v1.11.2
