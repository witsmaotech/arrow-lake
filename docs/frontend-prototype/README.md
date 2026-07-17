# Arrow Lake Console · 企业级前端原型

> 由 `/ui-ux-pro-max` 驱动设计系统 · 自包含可离线运行 · 双击任一 HTML 即开
> 产品：Arrow Lake 多模态数据湖仓 v1.9.0 · 2026-07-17

一套**企业级可用**的 Web 控制台原型，把湖仓的全部能力（摄入 / 检索 / OLAP / RAG / 知识图谱 / 治理）变成可点可看的界面。**零外部依赖**（除 Google Fonts），纯 HTML/CSS/原生 JS + 手绘 SVG 图表，离线可跑。

## 怎么看

直接双击任一 `.html`，或起一个本地服务：

```bash
cd docs/frontend-prototype
python3 -m http.server 5180
# 浏览器打开 http://localhost:5180/
```

**推荐浏览顺序**：`index.html`（落地页）→ `login.html`（登录）→ `dashboard.html`（总览·遥测）→ `datasets.html`（目录）→ `dataset-detail.html`（9-Tab 工作区·核心）→ `search.html`（5 模式检索）→ `rag.html`（对话+引用）→ `kg.html`（图谱浏览器）→ `olap.html`（SQL 分析）。

## 设计系统（真值源）

- **来源**：`/ui-ux-pro-max --design-system --density 8 --variance 4 --motion 5`
- **风格**：Real-Time Ops Landing + Soft UI Evolution（暗变体）· WCAG AA+ · Excellent 性能
- **字体**：Plus Jakarta Sans（UI）+ JetBrains Mono（数据/代码）— 刻意避开 Inter/Roboto
- **配色**：深空墨底 + lake teal `#14B8A6`（数据/链接/焦点）+ 琥珀 `#D97706`（CTA/进行中）+ 四态色（green/amber/red/info）
- **密度**：sidebar 248 / header 56 / row 36 / gap 8
- **签名**：① 五层深度遥测轨 ② 状态灯语言（形状+颜色双编码）③ 页顶遥测条 ④ 柔面面板 ⑤ `</> View API` 透明度
- **文件**：`design-system.md`（裁决）· `assets/tokens.css`（token 真值源）· `assets/app.css`（组件库）· `assets/layout.js`（骨架/图标/图表/命令面板/View API 抽屉）

## 页面 × 产品能力 × 后端路由

| 页面 | 体现的能力 | 后端路由（`/api/v1`） |
|---|---|---|
| `index.html` | 产品主张 · 五层架构 · 六能力柱 | — |
| `login.html` | JWT / API Key 认证 | `auth/token` · `auth/me` |
| `my-workspace.html` | 个人工作区（收藏查询/仪表盘/收藏/偏好/通知） | `me/{saved-queries,dashboards,favorites,preferences,notifications}` |
| `dashboard.html` | 总览 · 五层探活（+控制面） · 降级徽章 · 任务 · 指标 | `health` · `version` · `tasks` · `maintenance/status` |
| `datasets.html` | catalog · 11 种摄入来源（**按来源切换真实表单**） | `datasets` · `datasets/{n}/ingest/*` |
| `dataset-detail.html` | **10-Tab 工作区**（+索引 Tab：检索前置条件） | `datasets/{n}` · `search/*` · `kg/*` · `quality/*` · `lineage/*` · `admin/acl/{n}` |
| `embeddings.html` | 嵌入生成（text/image/clip）+ **索引管理** | `embed/*` · `index create/delete` |
| `search.html` | 5 模式检索 · **查询嵌入步骤** · nprobes/facets[]/weights | `datasets/{n}/search/*` |
| `rag.html` | RAG 对话 · retrieval_strategy · **GraphRAG 独立入口**(traversal_depth/graph_weight) | `rag/query` · `kg/query/graphrag` |
| `kg.html` | per-dataset 分图 · 8 traverser · GraphRAG | `kg/*` · `kg/traversers/*` |
| `olap.html` | SQL · **SQL-PGQ 图查询** · Daft · 物化视图 | `datasets/{n}/query/{olap\|graph\|daft}` |
| `lineage.html` | 跨数据集血缘 DAG · 历史 · 影响分析 | `lineage/{graph\|history\|impact\|stats}` |
| `audit.html` | HMAC 防篡改事件流 · verify 完整性校验 | `audit/{record\|verify\|query\|export}` |
| `governance.html` | Gravitino · catalog · tag→ACL · retention/masking | `/metadata/*` |
| `backup.html` | 备份列表（增量/全量）· 恢复向导 · CronJob | `backup/{create\|restore\|list}` |
| `admin.html` | 用户 · DatasetACL · SchemaACL · Deny 规则 | `admin/{users\|acl\|acl/schema\|deny}` |

## 交互特性

- **Cmd/Ctrl + K** 命令面板（任意控制台页）→ 跳页、跳数据集
- **侧栏折叠**（左上 ☰）→ 记忆到 localStorage
- **`</> API` 按钮** → 弹出该操作的等价 cURL + Python SDK（可复制）— 呼应产品三入口透明哲学
- **降级徽章** → hover 看 Ray/KG/Gremlin 回退路径
- **五层深度轨** → 长任务穿层可视化
- **图表全手绘 SVG**（无 Chart.js 依赖）：area / donut / sparkline / 力导向式 KG 图
- **响应式**：1100px / 860px 断点；`@media (pointer:coarse)` 触控目标 ≥44px
- **可达性**：`:focus-visible` 青环 · `prefers-reduced-motion` 全降级 · 对比 ≥ AA · 状态形状+颜色双编码

## 与产品的对应关系

本原型刻意把产品的**底层能力**映射到 UI 的每个角落，而非泛泛的 SaaS 模板：

- v1.8.6 的 **per-dataset 分图隔离** → KG 页 + 数据集工作区 KG Tab 明示 `kg_{dataset}`、drop-on-delete 零残留
- **优雅降级一等公民** → 顶栏常驻降级徽章、Dashboard 五层探活轨把"Gremlin→REST 降级"亮出来
- **三入口（SDK/REST/CLI）** → 每个写动作的 `</> View API`
- **零拷贝 / 谓词下推 / 流式** → OLAP 页结果区标注"流式 RecordBatchReader"、过滤"下推 Lance"

## 安全说明（生产化前必读）

本原型所有动态注入（`innerHTML`/`outerHTML`/`insertAdjacentHTML`）**仅消费硬编码常量**（NAV、图标、演示数据），**无不可信用户输入**。生产化时若接入真实数据，须：

- 引入 **DOMPurify** 对任何用户来源内容（数据集名、文档 title、KG 实体名、SQL 结果单元格）做净化后再注入
- 所有 Gremlin/SQL 走**参数化**（产品已具备：DuckDB prepared statement、Gremlin 参数化遍历）
- CSP 头：`script-src 'self'`（移除 Google Fonts 的 inline 或改为自托管）

## 已知原型边界

- 数据全部 mock；图表数据为内嵌生成，非真实接口
- 命令面板仅跳页（真实版应接数据集/历史 SQL 全文检索）
- KG 图为预设位置（真实版接 `kg/traversers/*` 后用力导向布局，如 vis-network/d3-force）
- 功能保真度审计见 `03-fidelity-review.md`（P0 逻辑漏洞 + P1 页面缺口 + P2 打磨 **均已修复**）

## 目录

```
docs/frontend-prototype/
├── README.md                  # 本文件
├── design-system.md           # 设计系统裁决
├── 03-fidelity-review.md      # 保真度审计
├── 04-v190-extension-plan.md  # v1.9.0 扩展规划（阶段 A→D）
├── index.html                 # 落地页（公开）
├── login.html                 # 登录
├── dashboard.html             # 总览（五层探活 + 控制面层）
├── my-workspace.html          # ★ 我的工作区（/me/* · v1.9.0）
├── datasets.html              # 数据集目录 + 摄入
├── dataset-detail.html        # ★ 9-Tab 数据集工作区
├── ingest.html                # 数据摄入（11 来源 · 多格式 + auto-embed/FTS）
├── embeddings.html            # 嵌入与索引
├── search.html                # 检索 Playground
├── rag.html                   # RAG 问答（reranker · 持久会话）
├── kg.html                    # 知识图谱（双 LLM · 增量 · 版本 · 模板）
├── olap.html                  # OLAP SQL 分析
├── lineage.html               # 数据血缘（v1.9.0 全链路写入）
├── audit.html                 # 审计追踪
├── governance.html            # 元数据治理 + 治理活动日志
├── backup.html                # 备份恢复
├── tasks.html                 # 异步任务（活跃 / 历史 / 死信）
├── system.html                # 系统健康（+ 控制面 SystemDB）
├── admin.html                 # 用户与 RBAC（Personal Tokens · 角色）
├── showcase.html              # 旗舰展示（架构全景 + 三王牌）
├── narrative.html             # 双线叙事（搁置）
└── assets/
    ├── tokens.css             # 设计 token（真值源）
    ├── app.css                # 组件库
    ├── layout.js              # 骨架 / 图标 / 图表 / 命令面板 / View API / 顶栏头像
    └── landing.css            # 落地页专用样式
```
