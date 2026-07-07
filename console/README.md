# Arrow Lake Console · SQL Worksheet

DuckDB SQL Web 界面,集成进 Arrow Lake 湖仓。**走产品已有的 OLAP 端点**,完全复用 RBAC / SELECT 安全校验 / 行级 ACL。

设计文档:`docs/architecture-design/duckdb-sql-worksheet.md`

## 为什么不用现成 DuckDB UI

- DuckDB 官方 `ui` extension:UI 不开源 + 本地单用户 + 绕过 RBAC
- duckdb-wasm:数据下沉浏览器,敏感数据不安全
- DbGate/CloudBeaver 直连型:绕过产品安全边界

本方案:UI 调产品 API(`/api/v1/datasets/{name}/query/olap`),不直连 DuckDB。

## 结构

```
console/
├── index.html            # 入口路由(已登录→olap,未登录→login)
├── login.html            # X-API-Key 换 JWT(auth_mode=BOTH)
├── olap.html             # ★ SQL Worksheet 主页
├── assets/
│   ├── tokens.css        # 设计真值源(复制自 prototype)
│   ├── app.css           # 组件库(复制自 prototype)
│   ├── console.css       # 增量样式(editor/table/toast/login)
│   └── console-layout.js # 精简 shell + icon + API 抽屉
└── src/                  # ES module(零构建)
    ├── auth.js           # token 存取 + login/logout
    ├── api.js            # fetch 封装 + 401 自动 refresh
    ├── ui/{toast,table}.js
    └── olap/{editor,results,worksheet}.js
```

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

1. 登录:输入产品 API Key(`X-API-Key` 换 JWT)
2. 选数据集(自动从 `/datasets` 拉取)
3. 输入 SELECT 语句 → `⌘/Ctrl+Enter` 运行
4. 结果:JSON 渲染(前 1 万行)+ CSV 导出全部;`max_rows` 最高 1,000,000

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
