# Arrow Lake Console

Arrow Lake 数据湖 / 检索 / 知识图谱统一控制台(v1.9.2)。原生 JS + ES 模块,**零构建、零运行时依赖**。核心页接真实 `/api/v1`,双轨:数据智能轨(数据集→摄入→索引→检索→RAG/KG)+ 管理治理轨(用户/ACL/deny/任务)。

- 规划:`docs/v1.9.1-frontend-core-impl-plan.md`
- SQL Worksheet 设计:`docs/architecture-design/duckdb-sql-worksheet.md`

## 为什么不用现成 DuckDB UI

- DuckDB 官方 `ui` extension:UI 不开源 + 本地单用户 + 绕过 RBAC
- duckdb-wasm:数据下沉浏览器,敏感数据不安全
- DbGate/CloudBeaver 直连型:绕过产品安全边界

本方案:UI 调产品 API(`/api/v1/datasets/{name}/query/olap`),不直连 DuckDB。

## 结构

```
console/
├── index.html            # 概览 KPI + 快捷入口
├── login.html            # 鉴权入口(密码登录 / X-API-Key,auth_mode=BOTH)
├── datasets.html         # 数据集 catalog(★)
├── dataset-detail.html   # 数据集工作区(★)
├── ingest.html           # 摄入(documents 异步任务)(★)
├── embeddings.html       # 嵌入 / 索引管理(★)
├── search.html           # 检索(向量/FTS/混合/Facets/Ensemble)(★)
├── rag.html              # RAG 问答 + 引用 + 持久会话(★)
├── kg.html               # 知识图谱 GraphRAG(★)
├── olap.html             # SQL Worksheet(DuckDB OLAP)(★)
├── tasks.html            # 异步任务列表 / 状态流转(★)
├── admin.html            # 管理后台(用户 / dataset ACL / schema ACL / deny)(★)
├── assets/
│   ├── tokens.css / app.css / console.css   # 设计系统(复制自 prototype + 增量)
│   └── console-layout.js                    # shell + icon + NAV + API 抽屉
└── src/                  # ES module(零构建)
    ├── api.js / auth.js / task.js           # fetch 客户端 / 鉴权 / 任务轮询
    ├── kg-subgraph.js                       # KG 子图渲染
    ├── ui/{toast,modal,table}.js            # 反馈原语(toast / confirm / 表格)
    └── olap/{editor,results,worksheet}.js   # SQL Worksheet
```

★ = 真跑通(接真实 `/api/v1`)。audit/governance/backup/system/lineage/showcase/narrative 暂为 prototype 演示页,未接入。

## 页面状态(v1.9.2)

| 页 | 状态 | 主要端点 |
|---|---|---|
| login / index | ✅ 真跑通 | `/auth/login` `/auth/token` `/datasets` |
| datasets / dataset-detail | ✅ | `/datasets` `/datasets/{name}` |
| ingest | ✅ | `/datasets/{name}/ingest/documents/async` |
| embeddings | ✅ | `/datasets/{name}/index/*` `/embed/*` |
| search | ✅ | `/datasets/{name}/search/{mode}` `/embed/text` |
| rag | ✅ | `/rag/query` `/rag/templates` `/rag/sessions` |
| kg | ✅ | `/kg/build` `/kg/query/graphrag` `/kg/stats` |
| olap | ✅ | `/datasets/{name}/query/olap` |
| tasks | ✅ | `/tasks` `/tasks/history` |
| admin | ✅ | `/admin/users` `/admin/roles` `/admin/acl/*` `/admin/deny/*` `/admin/users/{id}/tokens`(批A+ 全功能) |
| my-workspace | ✅ | `/me/*`(personal token X-API-Key;5 区 收藏查询/通知/偏好/仪表盘/收藏) |
| showcase / narrative | 🎨 展示 | 架构全景·三张王牌 / 双线叙事(prototype 移植,d3+gsap mock,不走 renderShell) |

## 开发模式

```bash
cd console
python3 -m http.server 5189   # ⚠️ 5180 常被占用,用 5189
# 浏览器:http://localhost:5189/login.html
```

开发时前端 5189、API 8000(跨域),需 API 侧 `cors_origins` 加 `http://localhost:5189`。

## 生产部署(FastAPI 同源 mount)

`arrow_lake/api/app.py` 挂载 `app.mount("/console", StaticFiles(...))`。
访问:`http://<api-host>:8000/console/` → 同源、免 CORS、复用 8000 端口。
`config/api.py` `exempt_paths` 含 `/console`(让 login.html 免拦)。

## 用法

1. 登录:`login.html` 密码登录(libSQL/JWT),或 `X-API-Key` 换 JWT
2. 概览:`index.html` 看数据集 / KG / 索引 KPI + 快捷入口
3. 数据轨:datasets → 工作区 → 摄入(异步任务)→ 建索引 → 检索 → RAG / KG
4. 管理轨:`admin`(用户 / ACL / deny)、`tasks`(异步任务流转)
5. SQL:`olap` 选数据集,SELECT + `⌘/Ctrl+Enter` 运行(前 1 万行渲染 + CSV 导出;`max_rows` 上限 1,000,000)

## 安全

- 所有请求自动带 `Authorization: Bearer <jwt>`,401 自动 refresh
- SQL 走后端 `validate_sql_safety`(SELECT only)+ 行级 ACL,前端无法绕过
- `toast` 用 `textContent`;`dsSel` 用 `createElement`;单元格值经 `escapeHtml` 防注入

## 依赖

- **零运行时构建、零外部依赖**(静态 HTML + 浏览器原生 ES module,离线可用)
- 非流式 JSON:`max_rows` 上限 1,000,000;>1 万行仅渲染前 1 万 + CSV 导出全部
- 编辑器为 MVP textarea + 行号;CodeMirror 6 升级见设计文档 Phase 4

## 实施备注

- 原设计含 SSE stream 模式(用 apache-arrow 解码 base64 Arrow IPC)。为消除 CDN 供应链依赖,**已移除 stream**,仅保留非流式 JSON。
- 后端 `_stream_table`(`arrow_lake/api/routers/query.py:36`)仍可供其他客户端调用,前端 MVP 不使用。
