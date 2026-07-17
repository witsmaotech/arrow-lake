# 原型扩展规划 · 对齐 v1.8.7 → v1.9.0

> 目标：把 `docs/frontend-prototype/` 从 **v1.8.6 基线** 扩展到 **v1.9.0**，让 Web 界面地图覆盖控制面（Turso/libSQL）+ 治理/任务/用户态等新能力。
> 本文只做**分析与规划**，不含实现。设计系统不变（Mission Control · 深空墨底 + lake teal + 琥珀 · Plus Jakarta Sans + JetBrains Mono）。

## 1. 现状基线

- **版本**：README 自标 v1.8.6（2026-07-04）。`目录` 段已过期（列 11 页，实际 20 个 HTML）。
- **页面**（20）：index / login / dashboard / datasets / dataset-detail(9-Tab) / search / rag / kg / olap / embeddings / ingest / lineage / governance / audit / backup / tasks / system / admin / showcase / narrative。
- **设计系统**：`design-system.md` + `assets/tokens.css`（真值源）+ `app.css` + `layout.js`（骨架/图标/手绘 SVG 图表/Cmd+K/`</> View API` 抽屉）。密度 sidebar 248 / header 56 / row 36 / gap 8。
- **签名五件套**：五层深度遥测轨 / 状态灯语言 / 页顶遥测条 / 柔面面板 / `</> View API`。
- **验证闭环**：`.venv/bin/python3` + playwright chromium-1217 像素验证（起 `python3 -m http.server 5180`），不肉眼看截图。

## 2. 差距分析（原型未体现的新能力）

按"影响面 × 用户可感知度"排序。每项标**后端路由/Store** + **受影响页面**。

### 🔴 P0 — 全新用户面（v1.9.0 控制面，原型完全缺失）

| 新能力 | 后端 | 受影响 | 缺口 |
|---|---|---|---|
| **个人工作区**（saved_queries / dashboards / favorites / preferences / notifications） | `UserStateStore` + `/api/v1/me/*`（personal_token 鉴权） | **新页 `my-workspace.html`** + 顶栏头像入口 | 原型无任何"我的"面；这是 v1.9.0 最面向终端用户的新增 |
| **Personal Tokens**（`al_` 前缀 / sha256 / 撤销 / 过期） | `IdentityStore` + `/users/{id}/tokens` | `admin.html` 扩展 + `login.html` 发令牌入口 | 原型 admin.html "用户"段为占位（v1.8.6 "未实现"） |
| **RBAC 角色 / DatasetACL / SchemaACL** | `RbacStore` + `admin/acl/*` + `admin/deny` | `admin.html` 扩展（角色 CRUD + ACL 矩阵） | admin.html 现仅静态列表，无角色管理 UI |
| **admin /users CRUD** | `IdentityStore` + `admin/users` | `admin.html` | v1.8.6 标"未实现"，v1.9.0 已落地 |

### 🟠 P1 — 既有页深度扩展（v1.9.0 让数据"真有"）

| 新能力 | 后端 | 受影响 | 缺口 |
|---|---|---|---|
| **治理域落库**（schema_changelog / maintenance_runs / schedules / config_changelog） | `GovernanceStore` | `governance.html` 扩展 | 现页只覆盖 Gravitino catalog/tag/retention/masking，缺 schema 变更日志 + 维护运行记录 + 调度 + 配置变更 |
| **任务历史持久化**（完成/失败 → task_history，超 Redis 2h TTL 回退） | `TaskHistoryStore` + `tasks` | `tasks.html` 扩展 | 现页只看活任务，无历史 + 失败归档视图 |
| **摄入死信队列**（失败 ingest 入 DLQ，可重试/丢弃） | `IngestDLQStore` | **新段或新页 `ingest-dlq.html`**（或并入 tasks） | 原型无失败摄入处理面 |
| **血缘全链路写入**（12 ingest 变体 + create/append 真记血缘） | `LineageIndexStore` + `lineage/*` | `lineage.html` | 现页骨架在但后端此前零记录；v1.9.0 后血缘图/历史/影响分析有真数据，应展示异步队列 + Lance SoT 回填 |
| **RAG 会话跨重启持久** | `RagSessionStore`（注入 RAGPipeline） | `rag.html` | 现页会话为内存感；应体现"持久会话历史 + 跨重启续接" |
| **SystemDB 组件**（libSQL/sqld + migration V001-V004 + health probe） | `system_db/*` + compose `system-db` | `system.html` + `dashboard.html` 五层探活 | 现页无"控制面库"层；应加 system-db 健康 + 迁移版本 + 读 RLock 单写说明 |

### 🟡 P2 — v1.8.7/1.8.8/1.8.9 能力回填（既有页增强）

| 新能力 | 后端 | 受影响 | 缺口 |
|---|---|---|---|
| **RAG reranker**（OllamaReranker 默认 + 评分可见） | `rag/reranker` + `rag/query` | `rag.html` | 现页无 reranker 步骤/配置；应展示"retrieve→rerank→generate"三段 + rerank_score 列 |
| **KG 双 LLM**（he_extract_llm 抽取 / he_qa_llm 问答） | `hugegraph/he_extract_llm`·`he_qa_llm` | `kg.html` + dataset-detail KG Tab | 现页无 LLM 分阶段提示 |
| **增量 KA + 版本管理**（archive/list/rollback/prune） | `kg/build?incremental` + `kg/versions/*` | `kg.html` + dataset-detail KG Tab | 现页无增量开关 + 版本时间线/回滚 |
| **doc_type 模板暴露** | `kg/list-doc-types`·`list-templates`·`describe-template` | dataset-detail KG Tab | 应加模板选择器 + strict concept_graph 提示（定义覆盖 0%→100%） |
| **/ingest/documents 多格式 + auto-embed + FTS** | `datasets/{n}/ingest/documents`（全 kreuzberg 类型） | `ingest.html` + datasets.html | 现页摄入来源表单应补"文档（多格式）→自动嵌入→自动建 FTS 索引"链路标注 |
| **Docling 全栈**（多格式 + OCR） | `ingest` docling backend | `ingest.html` | 摄入页应体现 docling 可视化解析选项 |
| **Console SQL Worksheet** | `/query/olap` | `olap.html` | 确认是否已等价（README 提到 olap 复用 /query/olap） |

### ⚪ P3 — 打磨 / 既有遗留
- README `目录` 段过期（列 11 页，实际 20）→ 刷新。
- showcase.html 版本轴止于 v1.8.6 → 延长到 v1.8.9 / v1.9.0（加控制面层）。
- `03-fidelity-review.md` 需针对新增页重做保真度审计。

## 3. 扩展方案（站点地图变更）

**新增页（2）**：
- `my-workspace.html` — 个人工作区（收藏查询 / 我的仪表盘 / 收藏数据集 / 偏好 / 通知）。顶栏右侧头像下拉入口。
- `ingest-dlq.html`（或并入 `tasks.html` 的"死信"Tab） — 摄入失败队列 + 重试/丢弃。

**深度扩展页（7）**：`admin.html`（用户+令牌+角色+ACL）/ `governance.html`（+schema 变更+维护+调度+配置）/ `tasks.html`（+历史）/ `lineage.html`（真数据+异步队列说明）/ `rag.html`（+reranker+持久会话）/ `system.html`（+system-db）/ `kg.html`+`dataset-detail` KG Tab（+双 LLM+增量+版本+模板）。

**轻度回填（2）**：`ingest.html`（多格式+auto-embed+FTS+docling）/ `dashboard.html`（五层探活加"控制面 libSQL"层 + system-db 健康）。

**修订**：`README.md`（版本→v1.9.0、刷新目录与"页面×能力×路由"表、加 /me/* 与 admin/users 等路由）、`showcase.html`（版本轴延长 + 架构全景加控制面横切）。

### 建议路由映射（新/扩展）
| 页面 | 新增/扩展路由 |
|---|---|
| `my-workspace.html` | `me/saved-queries`·`me/dashboards`·`me/favorites`·`me/preferences`·`me/notifications` |
| `admin.html`（扩展） | `admin/users`·`admin/users/{id}/tokens`·`admin/roles`·`admin/acl`·`admin/acl/schema`·`admin/deny` |
| `governance.html`（扩展） | `metadata/*`（既有）+ `governance/schema-changelog`·`maintenance/runs`·`governance/schedules`·`governance/config-changelog` |
| `tasks.html`（扩展） | `tasks` + `tasks/history` |
| `ingest-dlq.html` | `ingest/dlq`（重试/丢弃） |
| `system.html`（扩展） | `health` + `system-db/migrations`·`system-db/health` |

## 4. 设计系统对齐（不变，只复用）

- **不引入新设计语言**：所有新页复用 `tokens.css` + `app.css` + `layout.js`（骨架/图标/图表/命令面板/`</> View API` 一致）。
- 新页遵循既有**密度**（sidebar 248 / header 56 / row 36 / gap 8）与**签名五件套**。
- `my-workspace.html` 作为首个"强个人化"页，可在顶栏遥测条右侧加**头像 + 下拉**（个人偏好/通知徽章/登出），下拉触控目标 ≥44px（`@media (pointer:coarse)`）。
- 用户态数据（saved query 名、仪表盘名）在原型阶段仍为**硬编码常量**，`</> View API` 暴露真实路由；生产化前须 DOMPurify 净化（README 安全说明已记）。

## 5. 实施阶段（建议）

按"用户可感知 × 实现成本"分批，每批完成后做像素验证闭环。

- **阶段 A（P0 新面）**：`my-workspace.html` + `admin.html` 扩展（用户/令牌/角色/ACL）。最高用户可感知度。
- **阶段 B（P1 治理/任务/血缘）**：`governance.html` 扩展 + `tasks.html` 历史 + `lineage.html` 真数据 + `ingest-dlq.html`。
- **阶段 C（P1 系统面 + P2 检索/KG）**：`system.html` 加控制面层 + `rag.html` reranker/持久会话 + `kg.html`/dataset-detail 双 LLM/增量/版本/模板。
- **阶段 D（收口）**：`ingest.html` 多格式回填 + `dashboard.html` 探活加层 + README/目录刷新 + showcase 版本轴延长 + `03-fidelity-review.md` 重审。

每页交付物：静态 HTML + 硬编码 mock + `</> View API`（cURL+Python）+ 手绘 SVG 图表（必要时）+ 像素验证截图。

## 6. 待确认（开工前）

1. `my-workspace.html` 放独立页 vs 顶栏抽屉？（建议独立页 + 顶栏头像入口，信息量大）。
2. `ingest-dlq` 独立页 vs 并入 `tasks.html` Tab？（建议并入 tasks，减少顶层导航膨胀——当前侧栏 5 组已接近上限）。
3. 侧栏导航是否新增"个人"组（My Workspace）？还是只走顶栏头像？（建议顶栏头像，保持侧栏 5 组稳定，遵循 `bottom-nav-limit`/nav-hierarchy）。
4. showcase 版本轴延长到 v1.9.0 时，架构全景"五层"是否升格为"六层（加控制面）"或把控制面并入⟂横切面？（建议并入横切面"治理"列，保持五层稳定）。

## 7. 验证闭环（不变）

- `.venv/bin/python3 -m http.server 5180 --directory docs/frontend-prototype`。
- playwright 驱动 `~/.cache/ms-playwright/chromium-1217/chrome-linux64/chrome --no-sandbox`，**像素级验证**（非肉眼看截图，CDN 缓存骗眼）。
- 校验：AA 对比 / 触控 ≥44px / `prefers-reduced-motion` 降级 / 1100px+860px 断点 / 状态形状+颜色双编码。
