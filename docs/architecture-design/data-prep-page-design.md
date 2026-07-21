# 文档型数据准备页(Data Preparation)· 设计 spec

- **日期**:2026-07-21
- **范围**:方案 B(全量)——前端新页 + 后端 MinHash 语义去重接线 + 2 个 LLM 端点(标注 / 结构化抽取)
- **来源原型**:`docs/frontend-prototype/cleaning.html`(数据准备 · Daft 管道原型,mock)
- **目标 console**:`console/`(原生 JS + ES module,连真实后端)
- **批准**:用户 2026-07-21 批准设计 §1–§6

---

## 1. 目标与动机

把原型里的「数据准备」页在真实 console 中落地,**专为文档型内容而建**,并在页内 + 导航 + 仪表盘三处突出。

**为什么是文档型**:原型是通用多模态(文本/图/音/视)。文档型语料的准备痛点是文本——近重复 chunk、长度/正则质量规则、文本规整、LLM 打标与结构化抽取。本页聚焦文本列操作,删除多模态清洗,把原型的「UDF 任意 Python」(RCE 风险)改造为安全的 DuckDB SQL 文本规整菜单。

**成功标准**(可验证):
1. 6 个操作在真实小文档数据集上,「预览(8 行真抽样)→ 确认 → 提交」闭环全部跑通。
2. LLM 标注 / 结构化抽取全量走异步任务,任务页可跟踪完成,新列落盘可查。
3. 语义去重(MinHash)在 facade 暴露并在页内可选。
4. 侧栏「数据准备」入口 + 仪表盘引导卡 + 页内文档 hero 三处突出到位。
5. 后端新增 80%+ 覆盖,TDD(先红后绿)。

---

## 2. 现状(代码级核实)

### 2.1 后端已有真实端点(`arrow_lake/api/routers/quality.py` · prefix `/api/v1/datasets`)
| 端点 | 作用 | 角色 |
|---|---|---|
| `GET /{name}/quality/profile` | 列级统计 + 质量分 + 直方图(`QualityProfiler`) | VIEWER |
| `POST /{name}/quality/filter` | 内置过滤器(`TextLengthFilter`/`ImageResolutionFilter`),mode all/any | EDITOR |
| `GET /{name}/quality/report` | 质量报告 | VIEWER |
| `POST /{name}/quality/deduplicate` | 去重:`DedupRequest{strategy, action, perceptual_threshold}` | EDITOR |
| `POST /{name}/quality/rules` | 声明式规则:length/range/regex/duplicate → reject/flag/remove | EDITOR |

`query/daft`(`POST /{name}/query/daft`)支持链式管道(sort/filter/groupby/sql/pivot/explode/sample/distinct/select/offset/limit),用于本页预览抽样。

### 2.2 关键能力已实现但**未接 facade**
- **MinHash+LSH 近重复去重**:`arrow_lake/quality/nemo_curator.py:434 NemoCuratorDeduplicator.deduplicate(table)`,`_dedup_minhash`( :491,用 `datasketch MinHash/MinHashLSH`)。**未被 `_lake_ingest.deduplicate` 调用**。
- `lake.deduplicate`(`_lake_ingest.py:719`)只接 `ContentDeduplicator`(dedup.py),strategy ∈ {exact, perceptual, both},**无 minhash/semantic,无 text_column**。

### 2.3 LLM 与写回基础设施(标注/抽取复用)
- **异步 LLM**:`arrow_lake/rag/provider.py` `generate(messages: list[LLMMessage]) -> LLMResponse`(OpenAI/Ollama 等 provider);`rag/pipeline.py:421 batch_query`(批处理模式)。
- **结构化批抽取**:`arrow_lake/knowledge_graph/he_extractor.py:824 extract_batch`(KG 用,可镜像)。
- **写回新列**:
  - `add_column(name, column_name, sql_expr)`(`ingest/_storage_advanced.py:131`)—— DuckDB SQL 表达式加列(文本规整用)。
  - `add_columns_table(name, columns: pa.Table)`(`_storage_advanced.py:168`)—— 原生 Lance `add_columns`,行对齐,免全量重写(LLM 标注/抽取落盘用)。
- **异步任务系统**(已核实,干净可复用):`arrow_lake/api/tasks.py` `TaskManager` + `async_tasks.py` 路由模式。`ingest_documents`(`datasets.py:530`)本身 `run_sync` 同步;前端调的 `/ingest/documents/async`(`async_tasks.py:205`)套任务系统:`task_id = TaskManager.create_task("ingest_documents", name)` → `TaskManager.run_background(task_id, sync_fn, *args, **kwargs)` → 返 `{task_id, operation}`;前端 `GET /tasks/{id}/status` 轮询 + `watchTask`。标注/抽取直接套同一模式。`run_background` 收**同步函数**,故 service 内 async LLM 调用需同步入口包裹。

### 2.4 console 约定
- 页面:`renderShell({active, crumb})` + `request(method, path, {body})`(`src/api.js`,双层 auth + 401 自动刷新)+ `toast` + `watchTask` + `isLoggedIn()` 守卫。参考 `console/ingest.html`。
- 组件库 `console/assets/app.css`:panel/panel-h/panel-b、kpi/kpi-row、telem、tbl、tag、tabs、field/input/select、bar/pct、lamp、btn-*、grid 助手。
- 模块拆分参考 `console/src/olap/{editor,results,worksheet}.js`(避单文件超 800 行)。
- 导航 `console/assets/console-layout.js`:分组「数据 / 智能 / 管理」。

---

## 3. 范围

**In-scope(方案 B 全量)**:
- 前端新页 `console/data-prep.html` + `src/data-prep/` 模块(6 操作)。
- 后端:MinHash 接线(dedup.py + _lake_ingest.py + models + router);新增 `quality/llm_enrich.py`(label + extract service);quality 路由增 2 端点。
- 导航「数据准备」入口 + 仪表盘引导卡 + 页内文档 hero。
- 后端 TDD 测试 3 组。

**Out-of-scope**:
- 多模态清洗(图/音/视)——文档型不相关,删除。
- UDF 任意 Python 执行——RCE 风险,改为 SQL 文本规整菜单。
- 重构现有 `/quality/*` 端点(只增不改语义)。

---

## 4. 前端架构

### 4.1 页面布局(`console/data-prep.html`)
```
renderShell({ active: "data-prep", crumb: '<a href="datasets.html">数据集</a> › <b>数据准备</b>' })
isLoggedIn() 守卫 → login.html

┌─ 文档型 hero(突出)─────────────────────────────────────┐
│ 📄 文档型数据准备                                         │
│ 摄入后 → 清洗 / 规则 / 去重 / 标注 / 抽取 → 再检索·RAG      │
│ [Daft 引擎灯][惰性执行][本地→Ray]   [</>等价代码][运行管道] │
├──────────────────────────────────────────────────────────┤
│ 数据源:[select ▾]  → 质量画像 KPI 行(诊断恒显)            │
│   总行 · 质量分 · 空值% · 文本列数 · 近重复%(GET /profile) │
├──────────────┬───────────────────────────────────────────┤
│ 操作步骤      │ [选中操作]配置面板                          │
│ ▸ 质量规则    │  ─ 列/规则/动作 …                           │
│ ▸ 文本规整    │ 预览(前 8 行 · query/daft 真抽样)          │
│ ▸ 去重        │  ─ 标记将受影响行/新列                       │
│ ▸ LLM 标注    │ 运行 → 结果 KPI(输入/影响/输出)+ 任务链接   │
│ ▸ 结构化抽取  │                                            │
└──────────────┴───────────────────────────────────────────┘
```

### 4.2 模块拆分(`console/src/data-prep/`)
- `profile.js` —— 数据源下拉 + 质量画像 KPI(选数据集后 `GET /quality/profile`)。
- `ops.js` —— 5 个变换操作的配置表单 + 预览(各自 renderOp(key),仿原型 `OPS` 结构,但配置项接真实字段)。
- `run.js` —— 预览提交(`query/daft`)、操作提交(各端点)、异步任务 `watchTask`、结果 KPI。
- `data-prep.html` —— 骨架 + 串联 + 页内 `<style>`(hero / op-btn 少量新样式)。

### 4.3 数据流
1. 进页 → `GET /datasets` 填数据源下拉。
2. 选数据集 → `GET /datasets/{name}` 取 schema → 自动识别**文本列**(供规则/规整/去重/标注/抽取选用)→ `GET /quality/profile` 填诊断 KPI。
3. 选操作 → `ops.js` 渲染配置 → 点「预览」→ `run.js` 调 `query/daft`(limit 8 + 变换)→ 渲染预览表。
4. 点「运行」→ 提交对应端点:
   - 规则/规整/去重:同步端点 → 结果 KPI。
   - 标注/抽取:`POST /quality/llm_label`|`/extract` → 返 `task_id` → `watchTask` → 完成后提示 + 刷新画像。

---

## 5. 操作目录(6 个,预览→提交)

| 操作 | 预览(8 行) | 提交端点 | 落盘 |
|---|---|---|---|
| 质量画像(诊断) | — | `GET /quality/profile` | 只读 KPI |
| 质量规则 | `query/daft` 标记将被 flag/remove 行 | `POST /quality/rules` | flag 列 / 删行 |
| 文本规整 | `query/daft` 显示新列(SQL 算) | 现有 schema-alter 端点(`operation="add_column"` → `lake.add_column(name,col,sql_expr)`,`datasets.py:~771-830`) | 新列 |
| 去重 | `query/daft` 标记重复行 | `POST /quality/deduplicate`(exact / **minhash**) | flag / 删行 |
| LLM 标注(新) | 真跑 8 行 LLM 看新列 | `POST /quality/llm_label` → 异步任务 | `add_columns_table` 新列 |
| 结构化抽取(新) | 真跑 8 行 structured output | `POST /quality/extract` → 异步任务 | 多列 `add_columns_table` |

### 5.1 文本规整(替代 UDF,安全)
固定菜单(非任意代码),前端组合 DuckDB SQL 表达式:
- `trim` / `lower` / `upper`
- `regexp_replace(col, 'https?://\\S+', '[URL]')`(去 URL)
- `regexp_replace(col, '\\s+', ' ')`(折叠空白)
- 去 PII 邮箱/手机号正则
提交 = `add_column(name, new_column, sql_expr)`。NFKC 等 DuckDB 无原生函数的,留 roadmap(标注说明)。

---

## 6. 后端新增(3 处)

### 6.1 语义去重接线(小)
- `arrow_lake/quality/dedup.py`:`ContentDeduplicator` 增 `strategy="minhash"` 分支 → 委派 `NemoCuratorDeduplicator`(传 `text_column`)。
- `arrow_lake/_lake_ingest.py:719 deduplicate`:加 `text_column: str | None = None` 参数;`minhash` 策略时透传。
- `arrow_lake/api/models/quality.py`:`DedupRequest` 加 `text_column: str | None`。
- `arrow_lake/api/routers/quality.py:deduplicate`:透传 `text_column`。
- 校验:`minhash` 必须传 `text_column` 且该列为文本列;行数可行性检查(仿 daft `check_feasibility`)。

### 6.2 LLM 标注端点(中)
- 新 `arrow_lake/quality/llm_enrich.py`:
  - `async def label_column(lake, name, column, new_column, prompt_template, *, model=None, max_rows=None, concurrency=8) -> LabelReport`
  - 读列 → 信号量限并发批 `rag/provider.generate(prompt_template.format(text=row))` → 收集 → `pa.table([new_column], [labels])` → `lake.add_columns_table(name, columns)`。
  - 返回 `{input_rows, labeled, failed, new_column, sample[]}`。
- `POST /api/v1/datasets/{name}/quality/llm_label`(EDITOR,异步任务包装):
  - `LlmLabelRequest{column, new_column, prompt_template, model?, max_rows?, concurrency?}`。
  - 提交任务 → 返 `task_id`;任务内跑 `label_column`。
- 预览:前端直接对 8 行抽样文本跑(小成本,前端可不走端点,或走带 `max_rows=8` 的同步预览端点——**实现计划定**:首选前端取 8 行文本后用现有 LLM 问答通道,避免新增预览端点)。

### 6.3 结构化抽取端点(中)
- 同模块 `async def extract_fields(lake, name, column, fields, *, model=None, max_rows=None, concurrency=8) -> ExtractReport`:
  - `fields: list[{name, type, description}]` → 组 JSON schema → 批 LLM structured output(镜像 `he_extractor.extract_batch` 模式)→ 解析 → 多列 `pa.table(field_names, cols)` → `add_columns_table`。
  - 返回 `{input_rows, extracted, failed, columns[], sample[]}`。
- `POST /api/v1/datasets/{name}/quality/extract`(EDITOR,异步任务):
  - `ExtractRequest{column, fields:[{name,type,description}], model?, max_rows?, concurrency?}`。

### 6.4 模型新增(`api/models/quality.py`)
```python
class LlmLabelRequest(BaseModel):
    column: str
    new_column: str
    prompt_template: str  # 含 {text} 占位
    model: str | None = None
    max_rows: int | None = None
    concurrency: int = 8

class ExtractField(BaseModel):
    name: str
    type: str  # string|number|integer|boolean
    description: str = ""

class ExtractRequest(BaseModel):
    column: str
    fields: list[ExtractField]  # 1..20
    model: str | None = None
    max_rows: int | None = None
    concurrency: int = 8
# DedupRequest 加: text_column: str | None = None
```

---

## 7. 安全 / 成本模型

- **LLM 全量异步**:标注/抽取全量提交走异步任务(仿 `ingest/documents/async`),返 `task_id` + `watchTask`。预览仅 8 行。
- **写操作确认**:规则(flag/remove)、规整(加列)、去重(remove)、标注/抽取(加列)提交前显式确认(改数据集)。
- **上限**:`max_rows` 默认上限(如 ≤ 5000,超出强制异步分批);LLM 并发 `concurrency` 默认 8,带速率退避。
- **可行性检查**:minhash/label/extract 前检查行数,超大拒绝并提示走分批。
- ** XSS / 注入**:前端所有动态文本走 `esc()`;SQL 表达式服务端 `_validate_sql_expr`;prompt 模板服务端拼装,不拼进 SQL。

---

## 8. 导航 + 仪表盘 + hero(「突出」)

- **侧栏**(`console-layout.js` NAV「数据」组):顺序 数据集 → **📄 数据准备** → 索引/嵌入。`id:"data-prep"`, `ic:"file"`(或新增文档图标)。
- **仪表盘引导卡**(`console/dashboard.html` `data-mr` 网格):文档型特色卡 —— 图标 + 标题「文档型数据准备」+ 副标「清洗 / 去重 / LLM 标注与抽取」+ CTA → `data-prep.html`。
- **页内 hero**:§4.1 文档焦点横幅(说明「为文档型而建」+ Daft/LLM 引擎灯 + 遥测条)。

---

## 9. 测试计划(CLAUDE.md 80% / TDD)

后端(先红后绿,`.venv/bin/python3 -m pytest -q --tb=line --no-header -x`):
- `tests/quality/test_dedup_minhash.py` —— minhash facade + `text_column` 必填校验 + 行数可行性。
- `tests/quality/test_llm_enrich.py` —— `label_column` / `extract_fields`(mock `rag/provider.generate`,验证批处理、失败重试、`add_columns_table` 落盘新列)。
- `tests/api/test_quality_prep_endpoints.py` —— `llm_label` / `extract` 路由(异步 task_id 返回 + EDITOR 权限 + 校验)。

前端:vanilla JS 无运行器 → 开发态(`python3 -m http.server 5189 --directory console` + 后端 8000)+ dev override 热重载,逐操作手验;必要时 `.venv` playwright 像素烟测(hero/引导卡)。

---

## 10. 构建顺序

1. 后端 MinHash 接线 + 测试。
2. 后端 `llm_enrich.py`(label + extract service)+ 测试(mock LLM)。
3. 后端 quality 路由增 2 端点 + models + 测试(异步任务接入)。
4. 前端 `data-prep.html` + `src/data-prep/{profile,ops,run}.js`(6 操作)。
5. 导航入口 + 仪表盘引导卡 + hero。
6. dev override 联调,逐操作 E2E 验证(真实小文档数据集)。
7. 文档:本 spec + 追加节到 `docs/v1.9.1-frontend-core-impl-plan.md` + 更新 memory。

---

## 11. 产物清单

**后端**
- 改:`arrow_lake/quality/dedup.py`、`arrow_lake/_lake_ingest.py`、`arrow_lake/api/routers/quality.py`、`arrow_lake/api/models/quality.py`
- 新:`arrow_lake/quality/llm_enrich.py`
- 测试:`tests/quality/test_dedup_minhash.py`、`tests/quality/test_llm_enrich.py`、`tests/api/test_quality_prep_endpoints.py`

**前端**
- 新:`console/data-prep.html`、`console/src/data-prep/profile.js`、`console/src/data-prep/ops.js`、`console/src/data-prep/run.js`
- 改:`console/assets/console-layout.js`(nav)、`console/dashboard.html`(引导卡)、`console/assets/app.css`(少量 hero/op-btn 样式)

**文档**
- 本 spec(`docs/superpowers/specs/2026-07-21-data-prep-page-design.md`)
- 追加:`docs/v1.9.1-frontend-core-impl-plan.md`(数据准备节)
- memory 更新

---

## 12. 待实现期确认(非设计级风险)

- ~~异步任务提交 API~~ **已核实**:`TaskManager.create_task(op, ds)` + `run_background(task_id, sync_fn, *args, **kwargs)`(`async_tasks.py` 模式)。
- **LLM provider 获取方式**:service 内如何拿到配置好的 `rag/provider`(从 lake config 还是依赖注入)——实现计划定。
- **async→sync 包裹**:`run_background` 收同步函数,service 内 `rag/provider.generate`(async)需 `asyncio.run` 或同步 client 包裹——实现计划定。
- **标注/抽取预览通道**:前端 8 行预览是否复用现有 LLM 问答通道(避免新增预览端点),首选。
- **minhash 默认参数**:`nemo_curator.py` 的 `ngram_size`/`num_hashes` 默认是否够用,或暴露给 UI。
- **datasketch 依赖**:确认已装(minhash 用,1.10.0)。

---

## 13. M1 实现状态(2026-07-21,已完成)

WS1–WS3 全部落地,TDD 17 新测试 + 既有 quality 20 测试零回归(37 passed)。

**实现确认/微调(相对 §6 设计)**:
- **WS1 MinHash**:`ContentDeduplicator` 加 `strategy="minhash"` + `text_column`(+ngram_size/num_hashes/threshold)。**直接用 datasketch CPU MinHash LSH**(未沿用 `nemo_curator.NeMoDeduplicator` 的 `HAS_NEMO and GPU` 门控——那个门控在无 GPU 时退化成 exact,不是真语义去重)。facade `_lake_ingest.deduplicate` + `DedupRequest` + router 全链路透传 `text_column`。测试 `tests/quality/test_dedup_minhash.py`(7)。
- **WS2 llm_enrich**:新 `arrow_lake/quality/llm_enrich.py`。`label_column` / `extract_fields`,信号量限并发、逐行失败不阻断、`add_columns_table` 落盘。provider 可注入(测试)或 `create_llm_provider(lake._config.llm)`。抽取值统一存 `pa.string()`(避免 schema 漂移)。返回 `EnrichReport.to_dict()`。测试 `tests/quality/test_llm_enrich.py`(6,mock provider + FakeLake)。
- **WS3 端点**:`POST /{name}/quality/llm_label` 与 `/extract`(EDITOR,202,`PrepTaskResponse`)。套 `TaskManager.create_task` + `run_background`(`run_background` 原生支持 async 函数 → service 直传,无需 sync 包裹)。**改进**:加 `_spawn` + 模块级 `_BG_TASKS` 强引用集,避免 fire-and-forget task 被 asyncio GC 静默取消(吸取 kg_build 教训;既有 `async_tasks.py` 同模式未做,此处做对)。测试 `tests/api/test_quality_prep_endpoints.py`(4,patch run_background 隔离真实 LLM)。
- **既有测试修正**:`tests/api/test_quality.py` 2 处 `deduplicate` 断言补 `text_column=None`(我的 router 改动所致,已清理)。

**待 M2/M3**:
- M2:前端 `data-prep.html` + `src/data-prep/{profile,ops,run}.js` + nav + dashboard 卡 + hero。
- M3:真实百炼 LLM 跑 label/extract E2E(端点已就绪,M1 用 mock 验证逻辑)+ 文档追加 v1.9.1 plan + memory。
- **预览通道待定**(§5):前端 8 行预览是复用现有 LLM 通道还是走端点 `max_rows=8`,M2 定。
