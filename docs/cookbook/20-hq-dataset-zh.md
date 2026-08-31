# 20 · 高质量数据集流水线:从标注到发布

> 版本基线:v1.11.4(MS5 五维质量门+发布层,Master Plan 收官)。本章六步全部可复制实操,示例输出来自试点 `demo_annotation_alerts` 真实运行。Console 入口:「高质量数据集 → 生产向导」一页看全六步进度。

高质量数据集平台把"普通表格"变成"可信发布物":**契约**(结构约束)→ **标注**(人工真值)→ **评估**(五维打分)→ **发布**(版本锁定+规格书)→ **语料**(训练四形态,脱敏出域)→ **飞轮**(低置信回流补标,闭环)。

```text
①契约 ──→ ②标注 ──→ ③评估 ──→ ④发布 ──→ ⑤语料
             ↑                                │
             └──────── ⑥飞轮(低置信回流) ←────┘
```

前提:ADMIN 个人 token(header `X-API-Key`);Label Studio 已随默认栈部署(127.0.0.1:8085)。

---

## ① 契约——结构的机器可校验定义

契约声明对象类、标识 pattern、列规则(必填/值域/枚举)与质量权重。用建模工作台表单生成,或直接保存 YAML:

```bash
curl -X PUT "http://127.0.0.1:8000/api/v1/contracts/demo_annotation_alerts" \
  -H "X-API-Key: $TOKEN" -H "Content-Type: application/json" -d '{
  "contract_yaml": "dataset: demo_annotation_alerts\ntables:\n  alerts:\n    object_class: 燃气告警事件\n    columns:\n      - {name: text, label: 告警描述, required: true}\n      - {name: quality_score, label: 行质量分, range: [0, 1]}\nreferences: []\nquality:\n  critical: true\n  drift_kl: 0.1\n"}'
```

```json
{"scope":"demo_annotation_alerts","id":14,"version":1,"created":true,"diff":null}
```

要点:`dataset` 必须等于数据集名(否则静默无约束);`quality.critical` 是 **bool**(关键数据集标志,不是列清单);`drift_kl` 是漂移阈值(默认 0.1)。

## ② 标注——LS 人工真值 → Lance ADL

标注生产线是旁路(热路径零改动):Label Studio 作 transient 工作区,**ADL(Lance)是唯一真值源**。

```bash
# 建项目(标注界面从抽取模板生成)
curl -X POST http://127.0.0.1:8000/api/v1/annotation/projects \
  -H "X-API-Key: $TOKEN" -H "Content-Type: application/json" \
  -d '{"name":"alerts-l4","dataset":"demo_annotation_alerts","template_name":"project_concept_graph"}'

# 采样派发(脱敏→预标注→LS import 一键;四策略配比可选)
curl -X POST http://127.0.0.1:8000/api/v1/annotation/dispatch \
  -H "X-API-Key: $TOKEN" -H "Content-Type: application/json" \
  -d '{"project":"alerts-l4","total":20,"text_column":"text",
       "generalize_rules":[["1[3-9]\\d{9}","[手机号]"]]}'
```

标注员在 LS 完成标注(相关性三分类 + 实体/关系);30s 后台自动回收(或手动 `POST /annotation/recover`),看板随时可查:

```bash
curl -H "X-API-Key: $TOKEN" \
  http://127.0.0.1:8000/api/v1/annotation/projects/alerts-l4/status
```

```json
{"project":"alerts-l4","bound":true,"tasks_total":40,"annotated_rows":10,
 "review":{"approved":10,"arbitration":0,"pending":0},"kappa":0.638,"watermark":42}
```

第二标注者晚于回收窗口提交也没关系——watermark 是**复合判据**(task id 前进 ∪ 已回收任务标注数增长),`webhook` 事件还会即时触发补回收;双人试点可配 `adjudicate_min_annotators=1`。

## ③ 评估——五维打分与一票否决

```bash
curl -X POST -H "X-API-Key: $TOKEN" \
  http://127.0.0.1:8000/api/v1/quality/assess/demo_annotation_alerts
```

```json
{"total_score": 53.89, "star": 1, "admission": "none", "verdict": "veto",
 "dimensions": {"relevance": 75.0, "accuracy": 0.0, "completeness": 100.0,
                "diversity": 90.0, "timeliness": null},
 "vetoes": [{"kind": "accuracy_below_threshold", ...}, ...]}
```

五维=相关性 .20(标注回路)/准确性 .35(ADL 聚合 κ)/完整性 .20(契约+profiler)/多样性 .15(Gini)/时效性 .10(SLO);权重可在契约 `quality:` 节覆盖。relevance 不足时先跑「相关性回路」补人工标注再重评:

```bash
curl -X POST -H "X-API-Key: $TOKEN" \
  "http://127.0.0.1:8000/api/v1/quality/relevance/demo_annotation_alerts?n=200"
```

## ④ 发布——门禁、版本锁定与规格书

```bash
# 首次发布(门禁拒绝时返回结构化原因)
curl -X POST -H "X-API-Key: $TOKEN" -H "Content-Type: application/json" \
  -d '{"changelog":"契约 v1 后正式发布"}' \
  http://127.0.0.1:8000/api/v1/release/demo_annotation_alerts
# → 422 {"detail":{"blocked":["veto:accuracy_below_threshold",...],
//        "decision":"none","hint":"force=true + reason to override (audited)"}}

# force 发布(理由必填,审计留痕)
curl -X POST ... -d '{"changelog":"...","force":true,"reason":"试点:κ=-1 为留档分歧对"}' ...
```

```json
{"tag":"v1.4.0","lance_version":1,"status":"active","forced":true,
 "datasheet":{"schema":{"contract_present":true},"labeling":{"annotators":3,"coverage":1.0}, ...}}
```

发布=Lance 版本锁定+语义化 tag+自动生成的 datasheet YAML;门禁=准入+一票否决+**拒绝劣化**(基准=最新 active)+漂移超限。规格书:`GET /release/{ds}/datasheet`(JSON,`yaml` 字段)。

## ⑤ 语料——训练四形态,脱敏出域

```bash
curl -X POST -H "X-API-Key: $TOKEN" -H "Content-Type: application/json" \
  -d '{"generalize_rules":[["1[3-9]\\d{9}","[手机号]"]]}' \
  "http://127.0.0.1:8000/api/v1/release/demo_annotation_alerts/corpus?form=sft"
```

```json
{"records": 10, "path": "/data/lake/exports/v1.4.0/sft.jsonl",
 "masking": {"applied": true, "rules": 1}}
```

四形态:`sft`(ADL 五段×源表)/`pretrain`(KG 快照三元组,definition 也过脱敏)/`rlhf`(研判历史×ADL 按标识值配对)/`golden`(approved 人工行,可入 `tests/benchmark/golden/` 做回归)。**红线④:无脱敏配置一律 422**,`?allow_unmasked=true` 显式豁免并落 `corpus.unmasked` 审计。

## ⑥ 飞轮——低置信回流闭环

研判台对对象求值时勾选「记录研判」(`POST /decisions/assess?record_history=true`),历史即成为 RLHF 配对与自动回流的数据源:

```bash
curl -H "X-API-Key: $TOKEN" \
  http://127.0.0.1:8000/api/v1/decisions/history/demo_gas
# → {"total": 2, "low_confidence": 0, "recent":[{"object_id":"GAS.SEG.1", ...}]}

# 低置信对象自动入队补标(object_id 按标识值反查源行)
curl -X POST -H "X-API-Key: $TOKEN" -H "Content-Type: application/json" \
  -d '{"object_rows":[],"auto_low_confidence":true,"confidence_threshold":0.6}' \
  http://127.0.0.1:8000/api/v1/quality/feedback/demo_gas
```

补标完成后回到 ③ 重评——分数上涨、发布新版本,数据质量随使用持续提升。

---

## 排障速查

| 症状 | 原因与解法 |
|---|---|
| 契约保存 422 `QualitySpec.critical bool` | `critical` 是布尔标志,列约束写在 `columns` 的 `required/range/enum` |
| 评估 accuracy=0 + κ=-1 | 双标注真实分歧——仲裁 task 落地后重评;试点可 force(留审计) |
| corpus 422 `requires masking config` | 红线④设计:带 `generalize_rules` 或显式 `allow_unmasked=true` |
| feedback auto 恒 0 条 | 研判须开 `record_history=true` 且 confidence 低于阈值 |
| 发布 422 `no active release ... publish first` | corpus 导出要求已有 active 发布(第④步先行) |

下一章:[REST 实战配方](./19-rest-recipes-zh.md) · 架构深读:[ARCHITECTURE](../architecture-design/ARCHITECTURE.md) §v1.11.4
