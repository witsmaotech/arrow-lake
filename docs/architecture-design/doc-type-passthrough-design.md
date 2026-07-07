# doc_type 贯通设计：ingest 时自动判定文档类型 → hyper-extract 模板路由

> 状态：已评审通过，待实施
> 关联：`docs/v1.7.0-hyper-extract-kg-extraction-plan.md`（hyper-extract 引入方案）
> 触发：把 hyper-extract 设为默认 KG 抽取器后，`kg_build` 崩溃
> `AttributeError: 'OpenAICompatibleProvider' object has no attribute 'complete'`

## 1. 背景与问题

hyper-extract（he）在 v1.7.0 作为可选 KG 抽取后端集成，默认 `legacy`。改为默认 `he` 后，`kg_build`
立即崩溃。表面是 `DocTypeClassifier` 调了一个不存在的 `provider.complete()`，但**根因是 doc_type 没有贯通**：

- ingest 从不自动判定文档类型 —— `doc_type` 只能由调用方显式传入（`Lake.ingest_documents(doc_type=...)`
  或 REST `IngestDocumentsRequest.doc_type`），缺省 `None` 在 `_ingest_files.py:176` 被 `doc_type or ""`
  压成空串，整列 `''`。
- KG builder 见全空列（`builder.py:408` `any(d for d in doc_type_col)` 为 False）→ 回落到一次性 LLM 推断
  （`builder._infer_doc_type`），但 `DocTypeClassifier` 是坏的（`.complete()` 不存在）→ 崩溃。
- 于是 he 只能走默认模板，且分类器一调就炸。

### 现有「类型数组矩阵」（已就位，只是没接上）

| 层 | 位置 | 内容 |
|---|---|---|
| override | `config/rag.py` `he_doc_type_templates` | paper/report/manual/biography → general/* 模板 |
| gallery | `hyperextract/templates/presets/` | 6 类（finance/general/industry/legal/medicine/tcm）× ~30 模板 |
| 共享 taxonomy | `doc_type_router.py` `KNOWN_DOC_TYPES` | 10 canonical + 别名（论文→paper、报告→report…）|

矩阵与 hyper-extract 的衔接（共享 taxonomy）**本已存在**：classifier 标签 / 别名 / gallery 类别三方都靠
`KNOWN_DOC_TYPES` + `validate_taxonomy()` 对齐。缺的只是：(1) 修好分类器，(2) 在 ingest 时自动判定
填进 `doc_type` 列，让类型自然流到 he 路由。

### 三个断点（全链路实测）

1. **崩溃 bug** — `doc_type_router.py:385`：`provider.complete([LLMMessage(system=...), LLMMessage(user=...)])`
   同行两个 bug：`.complete()` 不存在（provider 只有 `.generate()`）；`LLMMessage(system=...)` 错（应为
   `role=, content=`）。`AttributeError`/`TypeError` 逃逸出 `classify()` 的窄 except → 崩调用方。
2. **空列** — `_ingest_files.py:176`：`doc_type or ""` 把缺省 `None` 压成 `''`；ingest 无自动判定。
3. **truthiness 误判** — `builder.py:408`：全 `''` 列被判为「无 doc_type」→ 强制（坏的）推断。

## 2. 目标

ingest 时由一个**共享 `DocTypeClassifier` 组件**按文件自动判定 doc_type（显式传入优先），写入数据集
`doc_type` 列；KG build 时直接按列路由到对的 hyper-extract 模板，不再依赖 build-time 推断。
**判定每个文件一次**（与现有「一文一类型，广播到所有 chunk」一致）。载体形态：**内部组件**（不暴露 REST）。

## 3. 设计

### 改动 1 — 修好 DocTypeClassifier（一切的前提）
`arrow_lake/knowledge_graph/doc_type_router.py` `from_llm_config` 闭包（~382-388）：

```python
# 修前（坏）
resp = await provider.complete([LLMMessage(system=system), LLMMessage(user=user)])
return resp.content
# 修后（镜像 extractor.py:177-187 的 generate 用法）
resp = await provider.generate([
    LLMMessage(role="system", content=system),
    LLMMessage(role="user",   content=user),
])
return resp.content   # 按 LLMResponse 实际字段取文本
```

并把 `classify()` 的 `except (TimeoutError, RuntimeError, OSError, ValueError)`（~414）**加 `AttributeError, TypeError`**，
任何 provider/解析异常都优雅降级为 `None`（兑现「best-effort, never raises」契约）。

### 改动 2 — report 改映射到 concept_graph
`arrow_lake/config/rag.py` `he_doc_type_templates`（~153）：
`"report": "general/doc_structure"` → `"general/concept_graph"`。
冒烟实测 doc_structure 在 granite4.1:8b + busi2 抽 0 实体，concept_graph 抽 8（高质量）。这是 report 类
文档（建设方案/白皮书）能产出 KG 的前提。doc_structure 模板质量留作后续。

### 改动 3 — ingest 时自动判定（核心）
把 `DocTypeClassifier` 从「KG 层专用」提升为**共享组件**，接进 ingest 流水线。

- **Ingestor 注入**：`arrow_lake/ingest/ingestor.py` `Ingestor.__init__` 加可选
  `doc_type_classifier: DocTypeClassifier | None = None`（默认 None → 无 LLM 时整特性静默跳过，列仍 `''`，
  由 KG-build-time 推断兜底）。`arrow_lake/_lake_ingest.py` `ingest_documents` 用 `self._config.llm` 构造
  分类器（镜像 `_lake_kg.py:108-112` `DocTypeClassifier.from_llm_config`，失败 log warning + None）传给 Ingestor。
- **per-file 判定钩子**：`arrow_lake/ingest/_ingest_files.py`，`chunker.chunk(...)` 之后、构建 `rows` dict
  （~170-178）之前，按文件判定：
  - 优先级 **显式 > 自动 > 空**：本次调用传了非空 `doc_type`（API/caller）→ 用之；否则 classifier 可用 →
    对聚合 `parsed.text`（复用 classifier 内置 ~1500 字符截断）调 `classify()` → 用 canonical label；否则 `""`。
  - 填 `"doc_type": [resolved] * len(chunks)`（替换现在的 `[doc_type or ""]`）。
- **sync/async 衔接**：`classify` 是 async，ingest 路径是 sync。加 `DocTypeClassifier.classify_sync(text)`
  适配（`asyncio.run` 或复用仓库现有 loop helper；注意 FastAPI 已有运行 loop 的情形，优先找既有 async→sync 工具，
  实施时先 grep `asyncio.run`/`run_until_complete` 对齐）。

### 改动 4 — 一致性（无需改 builder）
一旦 ingest 把真实 doc_type 写进列，`builder.py:408` 自然为 True → per-chunk 透传，**不再触发 build-time 推断**。
`builder._infer_doc_type`（改动 1 修好后）保留为**兜底**：仅对「ingest 无 LLM、列仍全空」的数据集生效。

## 4. 改动文件清单

| 文件 | 改动 |
|---|---|
| `arrow_lake/knowledge_graph/doc_type_router.py` | 修 `_complete`（`.complete`→`.generate`、`LLMMessage` kwargs）；`classify` except 加 `AttributeError, TypeError`；加 `classify_sync` |
| `arrow_lake/config/rag.py` | `he_doc_type_templates["report"]` → `general/concept_graph` |
| `arrow_lake/ingest/ingestor.py` | `Ingestor.__init__` 加可选 `doc_type_classifier` |
| `arrow_lake/_lake_ingest.py` | `ingest_documents` 构造 `DocTypeClassifier` 传给 `Ingestor`（失败→None） |
| `arrow_lake/ingest/_ingest_files.py` | per-file 判定钩子：显式 > auto > `""`，填 `doc_type` 列 |

## 5. 复用（不要新造）

- `DocTypeClassifier`（`doc_type_router.py:347-435`）—— 修好后即共享组件本体。
- `normalize_doc_type` / `KNOWN_DOC_TYPES` / `DOC_TYPE_ALIASES`（`doc_type_router.py:42-77, 321`）—— 矩阵衔接靠这套共享 taxonomy。
- `DocTypeRouter.resolve` / `TemplateGallery.match`（`doc_type_router.py:248-293, 185-220`）—— 不动。
- `builder._infer_doc_type`（`builder.py:427-440`）—— 改动 1 修好后作兜底。
- `DocTypeClassifier.from_llm_config` 构造模式（`_lake_kg.py:108-112`）—— ingest 侧镜像。

## 6. 验证

1. **单测**：`DocTypeClassifier.from_llm_config(cfg).classify(busi2 片段)` 返回 canonical label，不崩；
   扩展 `tests/` `test_doc_type_router` 覆盖 `.generate` 路径 + 异常降级。
2. **ingest 列**：`lake.ingest_documents('busi2_dt', [pdf])`（不传 doc_type）→ `read_dataset` 的 `doc_type`
   列非全空（每行同一 canonical label）；再传 `doc_type='paper'` → 列全 `paper`（显式覆盖）。
3. **KG 产出**：带 doc_type 列的数据集 `kg_build`（he + granite4.1:8b）→ 图顶点/边非零，sample 实体是正文
   实体；report→concept_graph 生效。
4. **回归**：`extractor_backend=legacy` ingest+kg_build 仍工作；无 LLM 配置时 ingest 不崩、列留空、KG-build-time
   兜底推断能出 doc_type。
5. **业务验收**：examples_busi2 全量 he 重建（granite4.1:8b + concurrency=6）产出可用规模图谱。

## 7. 风险与备注

- 改动 3 的 sync/async 是唯一需小心的实现点 —— 优先复用仓库现有 loop helper，避免 FastAPI 已有 loop 时嵌套。
- `general/doc_structure` 模板质量本次不修，仅绕过；后续若要「章节层级+交叉引用」型抽取再单独调模板/换模型。
- per-file 判定 = 每文件 1 次 LLM 调用，批量 ingest 多文件有一次/文件的额外延迟（KG 抽取本就重得多，可接受）；
  无 LLM 时自动跳过，零影响。
