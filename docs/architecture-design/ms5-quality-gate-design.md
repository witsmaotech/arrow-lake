# MS5 五维质量门与发布层设计 — Quality Gate, Release & Corpus(F5.1-F5.8)

> **版本基线**:v1.11.4(规划中;实施载体,`arrow_lake/_version.py` 当前 1.11.3)· 日期:2026-08-30 · 状态:**已批准**(2026-08-30 用户评审:S1-S12 全项按推荐通过)
> 上游:Master Plan MS5 · DR6(热路径零改动)· DR15(五维门=建模侧 M7 **QoS Annotation** 形态——质量约束以标注挂载到契约/对象,非独立承载层)
> 地基:v1.11.3(MS4 标注闭环:ADL/kappa/仲裁/采样器全部就绪;MS5 前置 M-16/M-12/M-13 已在 v1.11.2 清偿)
> 实施载体版本:v1.11.4(估 ~3 周,Master Plan 最后一个里程碑)

---

## 0. 定位与红线

**是什么**:高质量数据集的**出口质检与发布线**——五维评估(相关性/准确性/完整性/多样性/时效性)→ 星级与准入门 → 漂移监控 → 版本锁定发布 → 训练语料四形态导出 → 数据飞轮回流。**闭合 Master Plan 数据飞轮**。

**不是什么**(红线沿承):
- ❌ **不是入口门**——ingest 侧 IngestionQualityGate(v1.10.7,schema/score/contract/死信)语义零改动;五维门是**发布门**(release gate),只读评估,失败拒发布不拒摄入;
- ❌ **热路径零改动**——评估器全是旁路纯函数(读数据集/ADL/血缘),query/ingest 零 diff(DR6);
- ❌ **QoS Annotation 形态**——五维权重/阈值/一票否决项作为**契约的 quality 节**(annotations 挂载),不建独立质量配置层(DR15);
- ❌ **不建外部发布 registry**——发布=AL 内 Lance 版本锁定 + system_db 注册表(Gravitino 评估结论沿用:不引入第二目录);
- ❌ **语料导出必过脱敏**——四形态全部经 masking(L2/L3)后出域,与 MS4 脱敏前置同源。

---

## 1. 架构总览

```
┌────────────────────────────────────────────────────────────────────┐
│                     Arrow Lake(已有,全只读消费)                   │
│  数据集(Lance 版本)  契约(quality 节)  ADL(MS4 标注)             │
│  KG 三元组  decisions(研判)  sys_lineage_events  sys_audit_trail  │
└──────────────┬─────────────────────────────────────────────────────┘
               ▼
┌────────────────────────────────────────────────────────────────────┐
│  F5.1 五维评估引擎(quality/dimensions/,旁路纯函数)               │
│   相关性(MS4 标注回路)· 准确性(ADL κ 聚合)· 完整性(契约+profiler)│
│   多样性(分布 gini)· 时效性(新鲜度 p95)                          │
│   → 加权评分(默认 .20/.35/.20/.15/.10,契约可覆盖)+星级+一票否决   │
│   → 报告物:sys_quality_reports(V020)                              │
├────────────────────────────────────────────────────────────────────┤
│  F5.3 漂移监控(quality/drift.py)                                  │
│   数值列直方图 + 类别列频率 → 与基线 KL 散度;基线=上次发布快照      │
├────────────────────────────────────────────────────────────────────┤
│  F5.4 发布层(release.py + sys_releases V021)                      │
│   Lance 版本锁定 + CHANGELOG + 准入门(85/95/98)拒绝劣化发布        │
│   F5.7 数据集规格书(datasheet YAML 发布物)                        │
├────────────────────────────────────────────────────────────────────┤
│  F5.6 语料四形态导出(corpus.py,脱敏后出域)                       │
│   ①SFT 指令对 ②预训练三元组 ③RLHF 偏好对 ④回归黄金集              │
│  F5.5 记录级血缘链:采集→门控→对齐→脱敏→标注→评估→发布 全链        │
│  F5.8 飞轮回流:研判失败→annotation 采样队列(decisions→sampler)    │
└────────────────────────────────────────────────────────────────────┘
```

---

## 2. 五维评估引擎(F5.1)

**输入**:数据集 + 契约 quality 节(权重/阈值覆盖)+ ADL + lineage。**输出**:维度分(0-100)+ 加权总分 + 星级 + 准入判定 + 一票否决明细,落 `sys_quality_reports`。

### 2.1 五维定义与数据源(业务手册第五章,生命线定制)

| 维度 | 权重 | 数据源 | 计算 | 门槛(默认) |
|---|---|---|---|---|
| **相关性** | 0.20 | **MS4 标注回路**(S2):relevance 采样→LS 人工判定(高/中/不相关) | (highly+0.5×somewhat)/total | ≥90 |
| **准确性** | 0.35 | **ADL 聚合 κ**(S3):项目全生命周期(修 W5"轮级"限制)+ LLM 抽查补充 | κ×100 | ≥81;关键集≥95 |
| **完整性** | 0.20 | 契约 required 列缺失率 + 引用完整性(profiler)+ 死信率 | 检查项通过率 | 必填缺失<1% |
| **多样性** | 0.15 | 类别列/category 频率分布 | 1−gini(覆盖度)×100 | gini<0.4 |
| **时效性** | 0.10 | updated_at/血缘时间戳;标注延迟(ADL annotated_at−行时间)p95 | p95 折算 | 领域可配 |

### 2.2 评分与准入

- **加权总分** = Σ(维度分×权重)×100;权重和恒 1(契约覆盖时归一化);
- **星级**:<60 ★ / 60-74 ★★ / 75-84 ★★★ / 85-94 ★★★★ / ≥95 ★★★★★;
- **准入门(发布层消费)**:85=可发布(铜)/ 95=推荐(银)/ 98=标杆(金);**拒绝劣化发布**——新版本总分低于已发布版本 → 拒(可显式 override 带 audit);
- **一票否决**(默认,契约可增删):κ<0.81(关键集<0.95)/ 相关性<0.90 / 必填列缺失>5% / 未脱敏数据集发布语料。

### 2.3 契约 quality 节(QoS Annotation 形态,S1)

```yaml
# 契约 YAML 顶层新增节(登记不校验——沿 MS2 label/lifecycle 先例,compiler 零变化)
quality:
  weights: {relevance: 0.20, accuracy: 0.35, completeness: 0.20, diversity: 0.15, timeliness: 0.10}
  thresholds: {accuracy: 81, relevance: 90}      # 维度门槛(分)
  veto: [accuracy_below_threshold, relevance_below_threshold]
  admission: {bronze: 85, silver: 95, gold: 98}
  timeliness: {max_p95_hours: 72}                 # 时效领域参数
  critical: false                                  # true→准确性门槛抬到 95
```

缺省=业务手册默认值(引擎内置常量),契约不写 quality 节即用默认——**零配置可用**。

---

## 3. 相关性抽样评估(F5.2)

**复用 MS4 标注闭环,零新基础设施**(S2):
1. `POST /quality/relevance/{ds}` → 随机抽 N(默认 500,cap)→ 自动建 relevance 标注项目(labeling config=三分类 Choices)→ dispatch(带预标注:LLM 初判,人工复核);
2. 人工在 LS 判定(高/中/不相关)→ recover → ADL 聚合出 relevance_score;
3. **反哺**:评估中判"不相关"的行 → 生成 `filter` 清单(整改建议),判"高相关+低分"→ annotation 采样队列优先(F5.8 同路)。
降级:LLM-only 评估(标注者缺位时,标注=建议非结论,报告标记 `assessed_by: llm`)。

## 4. 准确性:ADL κ 聚合(S3)

修 W5 已知限制(kappa 轮级):`project_kappa` 聚合版 = 从 **ADL 全量**(非当轮 fresh)按 (row,annotator) 取最新版本重算——`{ds}_adl` 本就是 SoT,聚合查询替代轮级快照。无标注数据集:LLM 抽查(复用 `/{name}/quality/llm_label`)+ 死信率折算,报告标记数据源。

## 5. 漂移监控(F5.3)

- **基线**:发布时自动快照(每列:数值列等宽直方图 32 桶 / 类别列 top-32 频率),存 `sys_drift_baselines`(JSON);手动可重置;
- **检测**:`POST /quality/drift/{ds}` 对当前数据算 KL 散度(数值/类别各自),阈值默认 0.1(契约 `quality.drift_kl` 可覆盖);超阈 → 报告 + metrics(`arrow_lake_quality_drift_kl{dataset,column}`);
- 周期:随评估跑(不建常驻线程——发布/手动/评估触发,scheduler 留迭代)。

## 6. 发布层(F5.4)+ 规格书(F5.7)

**发布 = Lance 版本锁定 + 注册**(`sys_releases` V021):

```python
POST /release/{ds}            # 校验:最新质量报告过准入门+无未决否决+漂移未超限
  → lance_version 锁定 + tag(ds@vX.Y.Z)+ CHANGELOG(人工输入)
  → datasheet YAML 生成(schema/scale/quality 摘要/labeling 统计/usage)
GET  /release/{ds}            # 发布历史
POST /release/{ds}/retire     # 下线(软状态)
```

- **语义化版本**:schema 破坏=MAJOR / 数据增量=MINOR / 质量修订=PATCH(人工指定,默认 MINOR);
- 规格书字段(业务模板):id/version/category/lifecycle_status/schema/scale/quality(五维+星级+κ)/labeling(标注覆盖/人数)/usage(语料形态与许可)——YAML 与 CHANGELOG 存 `sys_releases`,文件形态经 `/release/{ds}/datasheet` 导出。

## 7. 记录级血缘链(F5.5)

不建新管道:评估/发布/语料导出各环节补写 `sys_lineage_events`(`event_type: quality.assessed / release.published / corpus.exported`,payload 带 report_id/release_id/corpus 形态),与既有 ingest→标注链拼接成**采集→门控→对齐→脱敏→标注→评估→发布**全链。console lineage 页自然可见。

## 8. 语料四形态导出(F5.6,S7)

统一 `corpus.py` + `POST /release/{ds}/corpus?form=sft|pretrain|rlhf|golden`,**全部经 masking 后出域**(S4/L2/L3 同源;导出落 `/data/lake/exports/`):

| 形态 | 来源 | 结构 |
|---|---|---|
| **① SFT 指令对** | ADL(脱敏文本+L4 五段) | JSONL:`{system: 本体定义+active 规则, instruction: 文本, output: 五要素结构化 target}` |
| **② 预训练三元组** | KG(HugeGraph 快照) | JSONL:`{subject, predicate, object, +定义上下文}`(实体消歧后) |
| **③ RLHF 偏好对** | decisions(模型研判)× ADL(专家标注) | JSONL:`{prompt, chosen: 专家, rejected: 模型}`(分歧样本天然成对) |
| **④ 回归黄金集** | ADL approved + 查询对 | JSONL+pytest 入口:`tests/benchmark/golden/{ds}.jsonl`,`-m golden` 离线跑(**不进 CI 热路径**) |

## 9. 飞轮回流(F5.8)

`decisions` 研判失败/低置信案例 → `POST /quality/feedback/{ds}` 把对象行打回 annotation 采样队列(标 strategy=`feedback`,sampler 已支持外部序)——决策驱动闭环:模型在哪错,标注就补哪。审计全链。

---

## 10. 模块落点

```
arrow_lake/quality/
  dimensions.py        # F5.1 五维纯函数评估器(读 pyarrow/ADL/契约)
  drift.py             # F5.3 KL 漂移 + 基线
arrow_lake/release/
  __init__.py
  registry.py          # F5.4 发布注册/版本语义/CHANGELOG
  datasheet.py         # F5.7 规格书生成
  corpus.py            # F5.6 四形态导出(经 masking)
api/routers/
  quality_report.py    # POST /quality/assess/{ds} · GET 报告 · drift · relevance · feedback
  release.py           # /release/{ds} CRUD + datasheet + corpus
system_db/migrations/
  V020__quality_reports.sql   # 报告(维度分/总分/星级/否决/verdict)
  V021__releases.sql          # 发布注册(lance_version/tag/changelog/datasheet/状态)
  V022__drift_baselines.sql   # 漂移基线快照
console/
  quality-reports.html # F5.1 报告页(五维雷达/星级/否决/历史)
  releases.html        # 发布页(发布流/规格书/语料导出)
tests/benchmark/golden/      # 黄金集落点(④)
```

**DoD**(Master Plan):发布流程 E2E(评估→门→发布→规格书→语料导出→血缘全链)+ 四形态语料样例导出 + 黄金集挂入回归(离线 marker)+ 试点数据集(燃气域)走完全程。

---

## 11. 与上游衔接

- **MS1 本体/契约**:quality 节挂契约(S1);规则渲染进 SFT system prompt;
- **MS2 对象/对齐**:datasheet 的 schema/scale 摘要自契约与 catalog;
- **MS3 决策**:RLHF 偏好对与飞轮回流的判定源(decisions);
- **MS4 标注**:相关性评估载体(S2)、准确性 ADL κ(S3)、语料①③④ 的数据面、feedback 采样队列;
- **v1.10.7 入口门**:零触碰——入口门管"什么数据能进湖",五维门管"什么数据集能出门"。

---

## 12. 评审清单(S1-S12,待拍板)

- [x] **S1 契约 quality 节**:权重/阈值/否决/准入作为契约顶层 `quality:` 节(登记不校验,沿 MS2 label 先例;缺省=业务手册默认)——vs 独立 system_db 配置层(否决理由:DR15 QoS Annotation 形态)
- [x] **S2 相关性评估载体**:复用 MS4 标注回路(人工权威,LLM 预标)——vs 纯 LLM 评估(快但非"人工复核"语义;保留为降级档)
- [x] **S3 准确性口径**:ADL 全量聚合 κ((row,annotator) 取最新版本)+ 无标注集 LLM 抽查折算——修 W5"轮级 κ"限制在此一并做
- [x] **S4 多样性指标**:类别分布 1−gini——vs 覆盖率×均匀度复合(gini 单指标,手册口径)
- [x] **S5 时效性口径**:updated_at 新鲜度 + 标注延迟 p95 双指标折算(领域参数 `max_p95_hours` 默认 72h)
- [x] **S6 漂移存储**:基线=发布时自动快照于 sys_drift_baselines(数值 32 桶直方图+类别 top-32 频率,KL 阈值默认 0.1)
- [x] **S7 语料四形态范围**:①SFT(ADL)②预训练(KG)③RLHF(decisions×ADL)④黄金集(pytest -m golden 离线,不进 CI)——四形态一次交付 vs ①④先行(推荐一次交付,②③ 数据面已就绪成本低)
- [x] **S8 发布版本语义**:Lance 版本锁定+语义化 MAJOR.MINOR.PATCH(人工指定,默认 MINOR)+ 拒绝劣化发布(可 override 带 audit)
- [x] **S9 发布存储**:sys_releases(system_db)注册表 + datasheet YAML 为**生成物**(不手编)——不建文件目录/外部 registry
- [x] **S10 门禁模式**:五维门只做发布门,shadow/enforce 按 release 端点(enforce=拒发布;不做 ingest 挂钩)
- [x] **S11 console 页面**:两页(quality-reports 报告页 + releases 发布页,治理运维组)——vs 一页(信息密度过高,分页)
- [x] **S12 试点数据集**:demo_annotation_alerts(燃气域,MS4 已有 ADL/κ 基础,直接续)——需先补相关性标注回路一轮

---

*已批准(2026-08-30,S1-S12 全过);实施计划:`docs_offline/v1.11.4-version-plan.md`。*
