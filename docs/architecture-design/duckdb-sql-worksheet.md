# DuckDB SQL Worksheet · 设计文档

> **版本基线**:v1.10.0([`arrow_lake/_version.py`](../../arrow_lake/_version.py)、容器实测;功能 v1.8.7 落地,文档随主干校准)
> **文档日期**:2026-07-07
> **状态**:已实施(commit `3855b20` + 安全修复 `4039da3`/`a1b2c3d`)。
>
> **⚠️ 实施修订(2026-07-07)**:为消除 apache-arrow CDN 供应链依赖(安全审查 HIGH→MEDIUM 反复标记),**前端移除 stream 模式**,仅保留非流式 JSON(`format=json`,`max_rows` 上限 1,000,000 + CSV 导出)。本文 §3.4 / §4 / ADR-5 / ADR-6 中关于 SSE 流式与 apache-arrow 的描述为"后端能力 + 原设计记录",**前端 MVP 不使用**;后端 `_stream_table`(`query.py:36`)仍可供其他客户端调用。后续如需在前端启用 stream,须先用 Node+esbuild 把 apache-arrow 本地 vendor 化(importmap 指向 `./vendor/`),不得直接走 CDN。
> **语言约定**:中文正文、英文图注(技术图惯例 + 渲染稳定)
> **关联计划**:`~/.claude/plans/lively-orbiting-rossum.md`(执行级计划)、前端原型 [`docs/frontend-prototype/olap.html`](../frontend-prototype/olap.html)

---

## 目录

- [1. 背景与目标](#1-背景与目标)
- [2. 方案选型(为何不直接用现成 DuckDB UI)](#2-方案选型为何不直接用现成-duckdb-ui)
- [3. 现状接缝(已读源码验证)](#3-现状接缝已读源码验证)
- [4. 目标架构(`console/` 文件夹)](#4-目标架构console-文件夹)
- [5. 实现阶段](#5-实施阶段)
- [6. 复用资产清单](#6-复用资产清单)
- [7. 验证方案](#7-验证方案)
- [8. 决策记录(ADR)](#8-决策记录adr)
- [9. 风险与对策](#9-风险与对策)
- [10. 工作量与里程碑](#10-工作量与里程碑)

---

## 1. 背景与目标

### 1.1 需求

为 Arrow Lake 湖仓产品提供一个 **DuckDB SQL Web 界面**,让用户(数据工程师 / 分析师 / 业务人员)在浏览器里对湖仓里的 Lance 数据集跑 SQL,做探索性分析与数据验证。

### 1.2 现状

- **后端能力已完备**:`POST /api/v1/datasets/{name}/query/olap` 已是工业级 SQL 端点,基于 DuckDB + `lance_scan`,具备 RBAC、SELECT 安全校验、行级 ACL、SSE 流式、`max_rows` 防爆。
- **前端骨架已存在**:[`docs/frontend-prototype/olap.html`](../frontend-prototype/olap.html) 已有完整的查询页骨架(dataset 选择器、stream/batch_size/max_rows 控件、⌘⏎ 快捷键、结果表、API 透明抽屉),但 `runQ()` 是 **mock**:硬编码 14 行假数据、`contenteditable` 假编辑器、无任何 `fetch`。
- **设计系统已定型**:[`design-system.md`](../frontend-prototype/design-system.md) —— Soft UI 暗变体 + Plus Jakarta Sans + JetBrains Mono + lake teal `#14B8A6`。

### 1.3 目标

把现有 `olap.html` 原型在**独立的 `console/` 文件夹**里"接真",产出**可用的、走产品安全层的** SQL Worksheet:

- ✅ 真实调用 `/query/olap`,复用 RBAC / SELECT 校验 / 行级 ACL
- ✅ CodeMirror 6 编辑器(SQL 语法高亮 + 表/列名补全)
- ✅ 非流式 JSON 渲染 + 流式 SSE/Arrow IPC 增量渲染
- ✅ 零运行时构建(静态 HTML + 浏览器原生 ES module),离线可跑
- ✅ 同源部署(FastAPI mount `/console`),复用 8000 端口

### 1.4 非目标(Non-Goals)

- ❌ 不做完整 BI dashboard(图表/仪表盘/分享 → 留给后续 Superset 评估)
- ❌ 不替代原型 `docs/frontend-prototype/`(原型继续作为设计真值源)
- ❌ 不引入前端构建链(Webpack/Vite)—— 保持"双击即开"

---

## 2. 方案选型(为何不直接用现成 DuckDB UI)

调研了三类"现成 DuckDB Web UI",均**不适合集成进本产品**:

| 方案 | 类型 | 开源 | 问题 |
|------|------|------|------|
| **DuckDB 官方 `ui` extension** (MotherDuck, 2025-03) | A 直连 | ❌ 专有 license | UI 不开源(license 风险);为本地单用户设计(`localhost:4213`);**绕过产品 RBAC**;难以容器化多租户 |
| **duckdb-wasm shell** (`shell.duckdb.org`) | B 浏览器内 | ✅ Apache | 数据要下沉到浏览器,与"数据湖在 MinIO/Lance + RBAC"模型冲突;敏感数据不能放客户端 |
| **DbGate / CloudBeaver / Superset 直连型** | A 直连 | ✅ | 绕过产品安全边界(RBAC、行级 ACL、SELECT 校验);适合内部运维,不适合面向产品用户 |

**核心判断**:本产品是 **多用户 + RBAC + 数据湖** 模型。任何"直连 DuckDB"的 UI(ui extension / DbGate / CloudBeaver)都会**绕过产品的安全边界**。正确的集成方向是 **UI 调产品 API**,而非 UI 直连 DuckDB。

**选定方案:产品内 SQL Worksheet**(基于已有 `/query/olap` 端点的轻量前端)。

---

## 3. 现状接缝(已读源码验证)

### 3.1 API 契约

| 用途 | 端点 | 鉴权 | 关键字段 / 返回 |
|------|------|------|----------------|
| 登录换 token | `POST /api/v1/auth/token` | `X-API-Key` header(api_key 模式) | → `{access_token, refresh_token}`(JWT) |
| 刷新 token | `POST /api/v1/auth/refresh` | Bearer refresh | → 新 token pair |
| 登出 | `POST /api/v1/auth/logout` | Bearer | 撤销当前 token |
| dataset 列表 | `GET /api/v1/datasets?limit=&offset=` | `Role.VIEWER` | → `{datasets:[{name,version,num_rows}], total}` |
| dataset 详情 | `GET /api/v1/datasets/{name}` | `Role.VIEWER` | → `{name,version,num_rows}` |
| **OLAP SQL** | `POST /api/v1/datasets/{name}/query/olap` | `Role.EDITOR` | body `OlapQueryRequest` |
| 元数据 SQL(补全用) | `POST /api/v1/datasets/{name}/query/metadata` | `Role.EDITOR` | 同上,语义别名 |

### 3.2 `OlapQueryRequest`(`arrow_lake/api/models/query.py:13`)

```
sql        : str            # 1~16384 字符,SELECT-only 校验(_BLOCKED_SQL_PREFIXES)
max_rows   : int | None     # 1~1,000,000
format     : "json" | "arrow_ipc"   # 默认 json(前端首选)
stream     : bool           # 默认 false;true 走 SSE
batch_size : int            # 100~50000,默认 1000(stream 时每批行数)
```

### 3.3 `OlapQueryResponse`(`query.py:30`)

```
success      : bool
format       : str
row_count    : int
column_count : int
meta         : {sql, ...} | None
data         : str | None        # format=arrow_ipc 时
rows         : list[dict] | None # format=json 时(前端首选)
```

**前端策略**:
- 小/中结果(<10k 行)→ `stream=false, format=json` → 直接渲染 `rows`
- 大结果(≥10k 行)→ `stream=true` → SSE 增量渲染

### 3.4 SSE 流式格式(`arrow_lake/api/routers/query.py:36` `_stream_table`)

媒体类型 `text/event-stream`,header 带 `X-Row-Count`、`X-SQL`。事件序列:

```
data: {"type":"schema", "columns":[...], "row_count":N}

data: {"type":"batch",  "rows":M, "data":"<base64 Arrow IPC>"}     # × N

data: {"type":"done",   "total_rows":N}
```

> 注:每个 SSE 事件的 `data` 字段是 JSON 字符串,其中 `batch.data` 是 **base64 编码的 Arrow IPC RecordBatch**。前端需用 `apache-arrow` 解码。

### 3.5 鉴权现状

- `auth_mode` 默认 `API_KEY`(`arrow_lake/config/api.py:110`),可切 `JWT` / `BOTH`
- CORS 已配(`arrow_lake/api/app.py:336`):`allow_credentials=False`、`allow_headers` 含 `Authorization / Content-Type / X-API-Key`
  - → **必须用 `Authorization: Bearer`,不能用 cookie**(契合 JWT)
- `exempt_paths`(`config/api.py:173`):`["/health", "/metrics", "/docs", "/openapi.json", "/redoc"]`
  - → `/console/*` 需加入,否则 JWT middleware 会拦住未登录的 `login.html`

### 3.6 安全层(前端自动受益,零改动)

| 层 | 机制 | 位置 |
|----|------|------|
| RBAC | `require_role(Role.EDITOR)` | `query.py:128` |
| SQL 只读校验 | `validate_sql_safety(req.sql)`(拦截 DROP/DELETE/INSERT 等) | `query.py:132` |
| 行级 ACL | `checker.apply_table_filter(result.table, dataset, role)` | `query.py:134` |
| 防爆 | `max_rows` ≤ 1,000,000 | `query.py:15` |

### 3.7 可复用设计资产(从 `docs/frontend-prototype/` 复制)

| 文件 | 规模 | 用途 |
|------|------|------|
| `assets/tokens.css` | 90 行,43 CSS 变量 | 设计真值源(色/字/间距/影) |
| `assets/app.css` | 206 行 | 组件库:`.btn .panel .tbl .field .grid .tag .tabs` 等 |
| `assets/layout.js` | 209 行 | `renderShell` / `icon` / `openApi` 抽屉 / SVG 图表 |

---

## 4. 目标架构(`console/` 文件夹)

**技术路线**:静态 HTML + 浏览器原生 ES module(`<script type="module">`)+ 第三方库**预构建成单文件 vendor**(零运行时构建、离线、双击即开,与原型哲学一致)。

```
console/
├── README.md                      # 开发/部署说明
├── index.html                     # 入口:未登录→login.html,已登录→olap.html
├── login.html                     # X-API-Key 登录 → /auth/token → 存 JWT → 跳 olap
├── olap.html                      # ★ SQL Worksheet 主页(从原型 olap.html 接真)
├── assets/
│   ├── tokens.css                 # 复用(复制自 prototype)
│   ├── app.css                    # 复用
│   └── console.css                # 本应用增量样式(worksheet 布局/编辑器主题)
├── src/                           # ES module(零构建)
│   ├── main.js                    # 轻量 hash router + 页面引导
│   ├── api.js                     # fetch 封装:baseURL、Bearer 注入、401→refresh、错误归一
│   ├── auth.js                    # 登录 / token 存取(localStorage)、登出
│   ├── olap/
│   │   ├── worksheet.js           # 页面控制器:dataset 选择 / 运行 / 结果编排
│   │   ├── editor.js              # CodeMirror 6 + lang-sql 封装(高亮+补全+⌘⏎)
│   │   ├── results.js             # 非流式:rows→表 + 导出 CSV
│   │   └── stream.js              # 流式:fetch+ReadableStream 读 SSE + 解 Arrow IPC base64
│   └── ui/
│       ├── shell.js               # 侧边栏/顶栏(从 prototype layout.js 接真)
│       ├── table.js               # 通用结果表组件(排序/分页/虚拟滚动可选)
│       └── toast.js               # 错误/状态提示
├── vendor/                        # 预构建产物(提交进仓库,免 Node 运行时)
│   ├── codemirror.bundle.js       # CodeMirror 6 + @codemirror/lang-sql(IIFE)
│   └── arrow.bundle.js            # apache-arrow ES(Arrow IPC base64 → Table)
└── tools/
    └── build-vendor.mjs           # esbuild 脚本(仅升级 CM/arrow 版本时跑)
```

### 4.1 数据流

```
┌─────────────────────────────────────────────────────────────┐
│  Browser (console/olap.html)                                │
│                                                             │
│  CodeMirror editor ─┐                                       │
│                     ▼                                       │
│              worksheet.js                                   │
│                     │                                       │
│                     ▼                                       │
│               api.js (Bearer + 401 refresh)                 │
└─────────────────────┬───────────────────────────────────────┘
                      │  POST /api/v1/datasets/{name}/query/olap
                      ▼
┌─────────────────────────────────────────────────────────────┐
│  FastAPI (arrow_lake/api)                                   │
│                                                             │
│  require_role(EDITOR)  →  validate_sql_safety  →            │
│  lake.olap_query (DuckDB + lance_scan)  →                   │
│  checker.apply_table_filter (行级 ACL)  →                   │
│  arrow_table_to_response / _stream_table                    │
└─────────────────────────────────────────────────────────────┘
```

---

## 5. 实现阶段

### Phase 0 — 文档落盘 + 骨架(0.5 天)

**0a. 设计文档**(本文档,已完成)。

**0b. 建 `console/` 骨架**:
1. 建 `console/` 目录树(见 §4)
2. 复制 `tokens.css` / `app.css` → `console/assets/`
3. 写 `console/README.md`(开发:本地 `python3 -m http.server 5180` + API CORS;部署:FastAPI mount)

### Phase 1 — Vendor 准备(0.5 天)

1. 写 `tools/build-vendor.mjs`(esbuild):
   - `codemirror` + `@codemirror/lang-sql` + `@codemirror/view` → IIFE,挂 `window.CodeMirror`
   - `apache-arrow` → IIFE,挂 `window.Arrow`
2. 跑一次生成 `vendor/*.bundle.js` **并提交进仓库**(用户后续无需 Node)
3. HTML 用 `<script src="vendor/...">` 引入

### Phase 2 — 基础设施(1 天)

1. `src/auth.js`:`login(apiKey)` → `POST /auth/token`(带 `X-API-Key`)→ 存 `access_token` / `refresh_token` 到 `localStorage`;`getToken()` / `logout()`
2. `src/api.js`:`request(method, path, body)` 自动注入 `Authorization: Bearer`;**401 → 自动调 `/auth/refresh` 重试一次**;统一抛 `ApiError`
3. `login.html`:X-API-Key 输入 → 登录 → 跳 `olap.html`
4. `ui/shell.js`、`ui/toast.js`:从原型 `layout.js` 抽接真版(去 mock,保留 `renderShell` / `icon` / `openApi`)

### Phase 3 — SQL Worksheet 核心(1 天)★

1. `olap/editor.js`:CodeMirror 6 实例,SQL 模式,DuckDB 关键字,`⌘/Ctrl+Enter` 触发运行,主题对齐 lake teal 暗底
2. `olap/worksheet.js`:
   - 启动 `GET /datasets` 填选择器
   - 切 dataset → 跑 `SELECT * FROM <name> LIMIT 1` 拿列名 → 注入 CodeMirror **自动补全**(表/列名)
   - 运行 → `POST /datasets/{name}/query/olap`,body `{sql, max_rows, format:'json', stream}`
3. `olap/results.js`:非流式 → `rows` 渲染 `<table>` + meta(行数/耗时)+ 导出 CSV + 错误展示
4. `olap/stream.js`:**fetch + ReadableStream 手写 SSE reader**(EventSource 不支持 POST + 自定义 header);逐 `{type}` 解析:
   - `schema` → 建表头
   - `batch` → `window.Arrow` 解 base64 IPC → 增量追加行
   - `done` → 收尾 meta
5. `olap.html`:从原型接真,替换 mock `runQ()`

### Phase 4 — 增强(0.5 天/项,可选)

- 查询历史(`localStorage`,最近 20 条,可回填)
- 多 dataset tab、结果虚拟滚动(>1000 行)
- EXPLAIN 按钮(调 `/query/olap` 带 `EXPLAIN` 前缀,渲染计划)
- 图表按钮(对数值列画 sparkline / bar,纯 SVG,复用原型风格)

### Phase 5 — 部署集成(0.5 天)

1. `arrow_lake/api/app.py` ~494 行后(include_router 区结束后)新增:
   ```python
   from starlette.staticfiles import StaticFiles
   console_dir = Path(__file__).parent.parent / "console"
   if console_dir.is_dir():
       app.mount("/console", StaticFiles(directory=str(console_dir), html=True), name="console")
   ```
2. `config/api.py:173` `exempt_paths` 追加 `"/console"`(让 `login.html` 在 JWT 模式下也免拦)
3. 部署:重启容器 → `http://127.0.0.1:8000/console/`(同源,免 CORS)
4. 开发模式:本机 `cd console && python3 -m http.server 5180`,API `cors_origins` 加 `http://localhost:5180`

---

## 6. 复用资产清单

| 资产 | 路径 | 说明 |
|------|------|------|
| OLAP SQL 端点 | `arrow_lake/api/routers/query.py:124` `olap_query` | 零改动 |
| SQL 安全校验 | `validate_sql_safety` | 零改动,前端自动受益 |
| RBAC | `require_role` | 零改动 |
| 行级 ACL | `checker.apply_table_filter` | 零改动 |
| dataset 列表 | `datasets.py:544` `list_datasets` | 填选择器 |
| 鉴权 | `auth.py:92` `exchange_token`、`auth.py:122` refresh | 登录 |
| SSE 流式 | `query.py:36` `_stream_table` | 格式固定 |
| 设计 token | `docs/frontend-prototype/assets/tokens.css` | 复制 |
| 组件库 | `docs/frontend-prototype/assets/app.css` | 复制 |
| 布局/图标 | `docs/frontend-prototype/assets/layout.js` | 抽接真版 |

---

## 7. 验证方案

### 7.1 后端 API 可达性(无需改后端即可先验)

```bash
# 1. 登录拿 token(用产品的 X-API-Key)
TOKEN=$(curl -s -X POST http://127.0.0.1:8000/api/v1/auth/token \
  -H "X-API-Key: $API_KEY" | jq -r .access_token)

# 2. 列 dataset
curl -s http://127.0.0.1:8000/api/v1/datasets -H "Authorization: Bearer $TOKEN" | jq .

# 3. 跑 SQL(用现有 busi2 dataset,已有数据)
curl -s -X POST http://127.0.0.1:8000/api/v1/datasets/busi2/query/olap \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"sql":"SELECT doc_type, count(*) AS n FROM busi2 GROUP BY doc_type","format":"json"}' | jq .
```

### 7.2 前端开发模式

```bash
cd console && python3 -m http.server 5180
# 浏览器:http://localhost:5180/login.html → 登录 → olap.html → 选 busi2 → 跑 SQL
```

### 7.3 流式验证

勾选 `stream` → 跑大结果集 → 观察 `schema` / `batch` / `done` 三类事件增量渲染。

### 7.4 安全验证

- 非 SELECT 语句(如 `DROP TABLE`)→ 应被 `validate_sql_safety` 拒,前端展示 4xx 错误
- 无 token 访问 → 应 401

### 7.5 部署验证(Phase 5 后)

容器内 `curl http://127.0.0.1:8000/console/olap.html` 应返回 HTML。

---

## 8. 决策记录(ADR)

### ADR-1:不集成 DuckDB 官方 `ui` extension

- **决策**:不使用 DuckDB 官方 `ui` extension(MotherDuck 构建)
- **理由**:① UI 专有 license(开源风险);② 为本地单用户设计;③ 绕过产品 RBAC/ACL
- **替代**:自建走 API 的轻量 Worksheet(本方案)

### ADR-2:静态 HTML + 预构建 vendor,不引入前端构建链

- **决策**:浏览器原生 ES module + esbuild 预构建单文件 vendor(提交进仓库)
- **理由**:与现有原型"零依赖、离线、双击即开"哲学一致;避免引入 Webpack/Vite 构建链
- **代价**:升级 CodeMirror/apache-arrow 版本时需重跑 `build-vendor.mjs`(需 Node)

### ADR-3:同源部署(FastAPI mount `/console`)

- **决策**:`app.mount("/console", StaticFiles(...))`,复用 8000 端口
- **理由**:免 CORS、与 API 同源、不增端口;生产与开发体验一致
- **前提**:`exempt_paths` 加 `/console`(否则 JWT 模式拦住 login.html)

### ADR-4:编辑器选 CodeMirror 6 而非 Monaco

- **决策**:CodeMirror 6 + `@codemirror/lang-sql`
- **理由**:~200KB vs Monaco ~5MB;SQL 模式成熟;无 web worker 依赖(静态 HTML 友好)

### ADR-5:首选 JSON 非流式,大结果再走 SSE

- **决策**:`format=json, stream=false` 为默认;≥10k 行走 `stream=true`
- **理由**:JSON 渲染最简;SSE + Arrow IPC 解码复杂度高,仅大结果需要

---

## 9. 风险与对策

| 风险 | 影响 | 对策 |
|------|------|------|
| `auth_mode` 生产实为 `JWT`(非 `API_KEY`) | login 页 X-API-Key 无效 | login 页同时支持 X-API-Key 与用户名+密码;实施前确认 `auth_mode` |
| Node 未装在部署机 | 无法跑 `build-vendor.mjs` | vendor 产物提交进仓库,部署机无需 Node;只在升级时跑 |
| SSE 的 base64 Arrow IPC 解码复杂 | 流式渲染卡壳 | 复用 `apache-arrow` 官方库;先做 JSON 非流式(已可用),流式作为增强 |
| `/console` 被 JWT middleware 拦 | login.html 打不开 | `exempt_paths` 加 `/console`(Phase 5) |
| 大结果集内存爆炸 | 浏览器卡死 | 默认 `max_rows` 上限 + 虚拟滚动(Phase 4) |

---

## 10. 工作量与里程碑

| 阶段 | 内容 | 工时 |
|------|------|------|
| Phase 0 | 文档落盘 + 骨架 | 0.5 天 |
| Phase 1 | Vendor 准备 | 0.5 天 |
| Phase 2 | 基础设施(auth/api/login) | 1 天 |
| Phase 3 | SQL Worksheet 核心 | 1 天 |
| Phase 5 | 部署集成 | 0.5 天 |
| **合计(可用 MVP)** | | **≈ 3 天** |
| Phase 4 | 增强(历史/多 tab/EXPLAIN/图表) | +0.5 天/项 |

**里程碑**:Phase 3 完成 = 可用的、走 RBAC 的 SQL Worksheet(开发模式);Phase 5 完成 = 生产部署就绪。
